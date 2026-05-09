"""Two-stage distilled text/image/audio-to-video via LTX-2.3 (OzzyGT/LTX-2.3-Distilled, SDNQ 4-bit)."""

import os
import gc
import ctypes
import torch

os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

from ...models.base import ModelPlugin, InputSpec, UISection, ParamSpec, ModelInputs
from ...utils.helpers import gfx_device, solve_path, clean_filename, load_first_frame


def vae_temporal_decode_streaming(vae, latents_cpu, *, decode_device, temb=None):
    """Streaming temporal decode — faster than spatial tiling on ≥16 GB cards."""
    tile_latent_min    = vae.tile_sample_min_num_frames // vae.temporal_compression_ratio
    n_latent_frames    = latents_cpu.shape[2]
    n_sample_frames    = (n_latent_frames - 1) * vae.temporal_compression_ratio + 1
    latent_stride      = vae.tile_sample_stride_num_frames // vae.temporal_compression_ratio
    sample_stride      = vae.tile_sample_stride_num_frames   # sample-space stride (8× latent_stride)
    blend_n            = vae.tile_sample_min_num_frames - vae.tile_sample_stride_num_frames

    result_tiles, prev_tile =[], None
    for i in range(0, n_latent_frames, latent_stride):
        tile_cpu = latents_cpu[:, :, i : i + tile_latent_min + 1, :, :]
        tile = tile_cpu.to(device=decode_device, dtype=vae.dtype, non_blocking=True)
        saved = vae.use_framewise_decoding
        vae.use_framewise_decoding = False
        decoded = vae.decode(tile, temb=temb, return_dict=False)[0]
        vae.use_framewise_decoding = saved
        row = decoded.cpu()
        if i > 0:
            row = row[:, :, :-1, :, :]
        if prev_tile is None:
            result_tiles.append(row[:, :, : sample_stride + 1, :, :])
        else:
            stitched = vae.blend_t(prev_tile, row, blend_n)
            result_tiles.append(stitched[:, :, :sample_stride, :, :])
        prev_tile = row
        del tile, decoded
    return torch.cat(result_tiles, dim=2)[:, :, :n_sample_frames]


class LTX2_3MultiPlugin(ModelPlugin):
    MODEL_ID     = "LTX-2.3 Multi-Input File"
    DISPLAY_NAME = "Video: LTX-2.3 (Multimodal)"
    MODEL_TYPE   = "video"
    DESCRIPTION  = "Two-stage distilled LTX-2.3 (SDNQ 4-bit) — text/image/audio-to-video with audio output"

    INPUTS       = InputSpec.PROMPT | InputSpec.NEG_PROMPT | InputSpec.IMAGE | InputSpec.LORA | InputSpec.AUDIO_REF
    UI_SECTIONS  =[
        UISection.PROMPT, UISection.NEG_PROMPT, UISection.VIDEO_STRIP,
        UISection.RESOLUTION, UISection.FRAMES, UISection.SEED, UISection.LORA,
    ]
    PARAMS            = ParamSpec(width=768, height=512, frames=121, steps=8, guidance=1.0)
    REQUIRED_PACKAGES =["torch", "torchaudio", "soundfile", "av", "diffusers", "transformers", "sdnq"]
    supports_inpaint  = False

    def load(self, prefs, scene, **kw):
        return {"pipe": None, "refiner": None, "last_model_card": self.MODEL_ID}

    def generate(self, pipe_obj, inputs: ModelInputs, scene, prefs) -> str:
        from diffusers import LTX2VideoTransformer3DModel
        from diffusers.pipelines.ltx2.export_utils import encode_video
        from diffusers.pipelines.ltx2.latent_upsampler import LTX2LatentUpsamplerModel
        from diffusers.pipelines.ltx2.utils import DISTILLED_SIGMA_VALUES, STAGE_2_DISTILLED_SIGMA_VALUES
        from diffusers.schedulers import FlowMatchEulerDiscreteScheduler
        from transformers import Gemma3ForConditionalGeneration

        try:
            from .pipeline_ltx2_multimodal import LTX2MultiModalPipeline, LTX2AudioCondition, LTX2ImageCondition, load_audio
        except ImportError:
            from pipeline_ltx2_multimodal import LTX2MultiModalPipeline, LTX2AudioCondition, LTX2ImageCondition, load_audio

        MODEL_PATH     = "OzzyGT/LTX-2.3-Distilled"
        SDNQ_PATH      = "OzzyGT/LTX-2.3-Distilled-sdnq-dynamic-int4"
        UPSAMPLER_PATH = "OzzyGT/LTX-2.3-upsampler-x2"

        torch_dtype    = torch.bfloat16
        onload_device  = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        offload_device = torch.device("cpu")
        fps            = 24.0

        seed = inputs.seed or torch.randint(0, 2**32, (1,)).item()
        generator = torch.Generator(device="cpu").manual_seed(seed)

        def _flush():
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
            try:
                ctypes.CDLL("libc.so.6").malloc_trim(0)
            except Exception:
                pass

        # ── Stage Resolution Match Fix ──────────────────────────────────────
        stage1_w = max(32, round((inputs.width / 2) / 32) * 32)
        stage1_h = max(32, round((inputs.height / 2) / 32) * 32)
        w = stage1_w * 2
        h = stage1_h * 2

        # ── Resolve Image & Audio Inputs (Optional safely) ──────────────────
        image_input = inputs.image
        if image_input is None and getattr(inputs, "video_path", None):
            image_input = load_first_frame(inputs.video_path)

        sound_path = getattr(inputs, "audio_ref", None)

        # ── Frame Count Calculation ─────────────────────────────────────────
        if sound_path:
            try:
                import soundfile as sf
                info = sf.info(sound_path)
                dur_s = info.frames / info.samplerate
                print(f"LTX-2.3: Audio condition detected ({dur_s:.2f}s). Overriding frame count.")
            except Exception:
                dur_s = inputs.frames / fps
            raw = dur_s * fps
            num_frames = int(((raw + 7) // 8) * 8) + 1
            num_frames = max(9, num_frames)
        else:
            target = inputs.frames
            num_frames = max(9, ((target - 1) // 8) * 8 + 1)
            dur_s = num_frames / fps
            
        print("Number of frames: " + str(num_frames))
        _flush()

        # Parse Image Conditions (Only if image is present)
        image_conditions = None
        if image_input is not None:
            if isinstance(image_input, str):
                from diffusers.utils import load_image
                image_input = load_image(image_input).convert("RGB")
            elif hasattr(image_input, "convert"):
                image_input = image_input.convert("RGB")
            image_conditions =[LTX2ImageCondition(image=image_input, frame=0, strength=1.0)]

        # ── Step 0: Text encoding ───────────────────────────────────────────
        print("LTX-2.3: Text encoding")
        text_encoder = Gemma3ForConditionalGeneration.from_pretrained(
            SDNQ_PATH, subfolder="text_encoder", torch_dtype=torch_dtype,
        )
        embeds_pipe = LTX2MultiModalPipeline.from_pretrained(
            MODEL_PATH,
            text_encoder=text_encoder,
            transformer=None, vae=None, audio_vae=None, vocoder=None,
            scheduler=None,
            torch_dtype=torch_dtype,
        )
        embeds_pipe.to(onload_device)
        with torch.inference_mode():
            prompt_embeds, prompt_attention_mask, _, _ = embeds_pipe.encode_prompt(
                prompt=inputs.prompt,
                negative_prompt=inputs.neg_prompt,
                do_classifier_free_guidance=False,
            )
        prompt_embeds          = prompt_embeds.detach().to(offload_device, copy=True)
        prompt_attention_mask  = prompt_attention_mask.detach().to(offload_device, copy=True)
        del embeds_pipe, text_encoder
        _flush()

        # ── Stage 1: Base generation ────────────────────────────────────────
        print(f"LTX-2.3: Stage 1 ({stage1_w}×{stage1_h})")
        transformer = LTX2VideoTransformer3DModel.from_pretrained(
            SDNQ_PATH, subfolder="transformer", torch_dtype=torch_dtype, device_map="cpu",
        )
        pipe = LTX2MultiModalPipeline.from_pretrained(
            MODEL_PATH,
            transformer=transformer,
            text_encoder=None, tokenizer=None,
            torch_dtype=torch_dtype,
        )
        pipe.scheduler = FlowMatchEulerDiscreteScheduler.from_config(
            pipe.scheduler.config, use_dynamic_shifting=False, shift_terminal=None,
        )

        # Parse Audio Conditions (Only if audio is present)
        audio_conditions = None
        if sound_path and hasattr(pipe, "audio_vae") and pipe.audio_vae:
            target_sr = pipe.audio_vae.config.sample_rate
            try:
                waveform = load_audio(sound_path, target_sample_rate=target_sr, seconds=dur_s)
                audio_conditions =[LTX2AudioCondition(audio=waveform, strength=1.0)]
                print(f"LTX-2.3: Audio successfully loaded into pipeline condition.")
            except Exception as e:
                print(f"LTX-2.3 Warning: Failed to load audio condition: {e}")

        # Rely EXCLUSIVELY on leaf_level offloading. It correctly catches the internal
        # vae.encode() calls and moves modules to the GPU just-in-time.
        pipe.enable_group_offload(
            onload_device=onload_device, 
            offload_type="leaf_level",
            use_stream=True, 
            low_cpu_mem_usage=True,
        )

        stage1_kw = dict(
            prompt_embeds=prompt_embeds.to(onload_device, dtype=torch_dtype),
            prompt_attention_mask=prompt_attention_mask.to(onload_device),
            width=stage1_w, height=stage1_h,
            num_frames=num_frames, frame_rate=fps,
            num_inference_steps=8, sigmas=DISTILLED_SIGMA_VALUES,
            guidance_scale=1.0, generator=generator,
            output_type="latent", return_dict=False,
            use_cross_timestep=True, # Critical for Audio+Image cross-attention mapping!
        )
        
        # Inject optional modalities
        if image_conditions is not None:
            stage1_kw["image_conditions"] = image_conditions
        if audio_conditions is not None:
            stage1_kw["audio_conditions"] = audio_conditions

        with torch.inference_mode(), torch.autocast("cuda", dtype=torch_dtype):
            outputs = pipe(**stage1_kw)

        if isinstance(outputs, (tuple, list)):
            video_latent = outputs[0].detach().to(offload_device, copy=True)
            audio_latent = outputs[1].detach().to(offload_device, copy=True) if len(outputs) > 1 and outputs[1] is not None else None
        else:
            video_latent = outputs.detach().to(offload_device, copy=True)
            audio_latent = None
        
        if audio_latent is not None:
            print("LTX-2.3: Stage 1 successfully generated audio latent.")
            
        del pipe, transformer
        _flush()

        # ── Latent upsampling (2×) ──────────────────────────────────────────
        print("LTX-2.3: Latent upsampling ×2")
        upsampler = LTX2LatentUpsamplerModel.from_pretrained(
            UPSAMPLER_PATH, torch_dtype=torch_dtype,
        ).to(onload_device)
        with torch.inference_mode():
            up_latent = upsampler(video_latent.to(onload_device, dtype=torch_dtype))
        up_latent = up_latent.detach().to(offload_device, copy=True)
        del upsampler, video_latent
        _flush()

        # ── Stage 2: Refinement ─────────────────────────────────────────────
        print(f"LTX-2.3: Stage 2 refinement ({w}×{h})")
        transformer2 = LTX2VideoTransformer3DModel.from_pretrained(
            SDNQ_PATH, subfolder="transformer", torch_dtype=torch_dtype, device_map="cpu",
        )
        refine_pipe = LTX2MultiModalPipeline.from_pretrained(
            MODEL_PATH,
            transformer=transformer2,
            text_encoder=None, tokenizer=None,
            torch_dtype=torch_dtype,
        )
        refine_pipe.scheduler = FlowMatchEulerDiscreteScheduler.from_config(
            refine_pipe.scheduler.config, use_dynamic_shifting=False, shift_terminal=None,
        )

        refine_pipe.enable_group_offload(
            onload_device=onload_device, 
            offload_type="leaf_level",
            use_stream=True, 
            low_cpu_mem_usage=True,
        )

        refine_kw = dict(
            prompt_embeds=prompt_embeds.to(onload_device, dtype=torch_dtype),
            prompt_attention_mask=prompt_attention_mask.to(onload_device),
            latents=up_latent.to(onload_device, dtype=torch_dtype),
            width=w, height=h, num_frames=num_frames,
            num_inference_steps=3,
            noise_scale=STAGE_2_DISTILLED_SIGMA_VALUES[0],
            sigmas=STAGE_2_DISTILLED_SIGMA_VALUES,
            guidance_scale=1.0, generator=generator,
            output_type="latent", return_dict=False,
            use_cross_timestep=True, # Critical for Audio+Image cross-attention mapping!
        )
        
        # Re-inject optional modalities correctly for Stage 2
        if image_conditions is not None:
            refine_kw["image_conditions"] = image_conditions
            
        if audio_conditions is not None:
            # Pass CONDITIONS to Stage 2 (not audio_latents) so the mask remains 1.0 
            # and perfectly preserves the audio waveform during refinement!
            refine_kw["audio_conditions"] = audio_conditions
        elif audio_latent is not None:
            # Fall back to refining generated audio (e.g. Pure Text-to-Video generation)
            refine_kw["audio_latents"] = audio_latent.to(onload_device, dtype=torch_dtype)

        with torch.inference_mode(), torch.autocast("cuda", dtype=torch_dtype):
            outputs2 = refine_pipe(**refine_kw)

        if isinstance(outputs2, (tuple, list)):
            final_v = outputs2[0].detach().to(offload_device, copy=True)
            final_a = outputs2[1].detach().to(offload_device, copy=True) if len(outputs2) > 1 and outputs2[1] is not None else audio_latent
        else:
            final_v = outputs2.detach().to(offload_device, copy=True)
            final_a = audio_latent
            
        del refine_pipe, transformer2, up_latent, prompt_embeds, prompt_attention_mask
        _flush()

        # ── Decode ──────────────────────────────────────────────────────────
        print("LTX-2.3: Decode")
        decode_pipe = LTX2MultiModalPipeline.from_pretrained(
            MODEL_PATH,
            transformer=None, text_encoder=None, tokenizer=None, scheduler=None,
            torch_dtype=torch_dtype,
        )
        vae = decode_pipe.vae.to(onload_device)
        with torch.inference_mode():
            video = vae_temporal_decode_streaming(vae, final_v.to("cpu"), decode_device=onload_device)
            video = decode_pipe.video_processor.postprocess_video(video, output_type="np")

        audio_out      = None
        audio_sr       = 24000
        if final_a is not None and hasattr(decode_pipe, "audio_vae") and decode_pipe.audio_vae:
            print("LTX-2.3: Decoding final audio latent.")
            audio_vae = decode_pipe.audio_vae.to(onload_device)
            vocoder   = decode_pipe.vocoder.to(onload_device)
            audio_sr  = getattr(vocoder.config, "output_sampling_rate", 24000)
            with torch.inference_mode():
                mel       = audio_vae.decode(final_a.to(onload_device, dtype=audio_vae.dtype), return_dict=False)[0]
                audio_out = vocoder(mel).cpu()
            del audio_vae, vocoder
        else:
            print("LTX-2.3: No final audio latent found to decode.")

        del decode_pipe, vae, final_v, final_a
        _flush()

        # ── Save ────────────────────────────────────────────────────────────
        dst_path = solve_path(clean_filename(str(seed) + "_" + inputs.prompt[:40]) + ".mp4")
        if audio_out is not None:
            print("LTX-2.3: Encoding final video with audio.")
            encode_video(
                torch.from_numpy((video[0] * 255).round().astype("uint8")),
                fps=fps, audio=audio_out[0].float().cpu(),
                audio_sample_rate=audio_sr, output_path=dst_path,
            )
        else:
            print("LTX-2.3: Encoding final video without audio.")
            encode_video(
                torch.from_numpy((video[0] * 255).round().astype("uint8")),
                fps=fps, output_path=dst_path,
            )
        print(f"LTX-2.3 saved: {dst_path}")
        return dst_path