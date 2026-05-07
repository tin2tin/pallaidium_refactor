"""Text-to-video, img2vid, and vid2vid via CogVideoX-5b / CogVideoX-2b."""

import os
import shutil
from ...models.base import ModelPlugin, InputSpec, UISection, ParamSpec, ModelInputs
from ...utils.helpers import gfx_device, low_vram, solve_path, clean_filename


class _CogVideoXBase(ModelPlugin):
    MODEL_TYPE   = "video"
    DESCRIPTION  = "CogVideoX text-to-video, img2vid, and vid2vid"

    INPUTS       = InputSpec.PROMPT | InputSpec.NEG_PROMPT | InputSpec.IMAGE
    UI_SECTIONS  = [
        UISection.PROMPT, UISection.NEG_PROMPT, UISection.VIDEO_STRIP,
        UISection.FRAMES, UISection.STEPS, UISection.GUIDANCE, UISection.SEED,
    ]
    PARAMS       = ParamSpec(width=720, height=480, frames=49, steps=50, guidance=6.0)
    REQUIRED_PACKAGES = ["torch", "diffusers"]

    def load(self, prefs, scene, **kw):
        import torch

        mode = kw.get("mode", "txt2vid")
        print(f"Loading {self.MODEL_ID} ({mode})…")

        if mode == "vid2vid":
            from diffusers import CogVideoXDPMScheduler, CogVideoXVideoToVideoPipeline
            pipe = CogVideoXVideoToVideoPipeline.from_pretrained(
                self.MODEL_ID, torch_dtype=torch.bfloat16
            )
            pipe.scheduler = CogVideoXDPMScheduler.from_config(pipe.scheduler.config)
        elif mode == "img2vid":
            from diffusers import CogVideoXImageToVideoPipeline
            pipe = CogVideoXImageToVideoPipeline.from_pretrained(
                "THUDM/CogVideoX-5b-I2V", torch_dtype=torch.bfloat16
            )
        else:
            from diffusers import CogVideoXPipeline
            pipe = CogVideoXPipeline.from_pretrained(self.MODEL_ID, torch_dtype=torch.float16)

        if gfx_device == "mps":
            pipe.to("mps")
        elif low_vram():
            pipe.enable_sequential_cpu_offload()
            pipe.vae.enable_tiling()
        else:
            pipe.enable_model_cpu_offload()

        return {"pipe": pipe, "refiner": None, "last_model_card": self.MODEL_ID}

    def generate(self, pipe_obj, inputs: ModelInputs, scene, prefs):
        import torch
        from diffusers.utils import export_to_video

        pipe = pipe_obj["pipe"]
        seed = inputs.seed
        generator = (
            torch.Generator("cuda").manual_seed(seed)
            if torch.cuda.is_available() and seed != 0 else None
        )

        common = dict(
            prompt=inputs.prompt,
            negative_prompt=inputs.neg_prompt,
            num_inference_steps=inputs.steps,
            guidance_scale=inputs.guidance,
            num_frames=inputs.frames,
            generator=generator,
            height=480,
            width=720,
        )

        if inputs.mode == "vid2vid" and inputs.video_path:
            from diffusers.utils import load_video
            video = load_video(inputs.video_path)[:49]
            video_frames = pipe(
                video=video,
                strength=1.0 - inputs.strength,
                **common,
            ).frames[0]
        elif inputs.mode == "img2vid" and inputs.image is not None:
            video_frames = pipe(
                image=inputs.image,
                num_videos_per_prompt=1,
                use_dynamic_cfg=True,
                **common,
            ).frames[0]
        else:
            video_frames = pipe(
                num_videos_per_prompt=1,
                **common,
            ).frames[0]

        import bpy
        render = bpy.context.scene.render
        fps = round(render.fps / render.fps_base, 3)
        src_path = export_to_video(video_frames, fps=fps)
        dst_path = solve_path(clean_filename(str(seed) + "_" + inputs.prompt) + ".mp4")
        shutil.move(src_path, dst_path)
        return dst_path


class CogVideoX5bPlugin(_CogVideoXBase):
    MODEL_ID     = "THUDM/CogVideoX-5b"
    DISPLAY_NAME = "Video: CogVideoX-5b"


class CogVideoX2bPlugin(_CogVideoXBase):
    MODEL_ID     = "THUDM/CogVideoX-2b"
    DISPLAY_NAME = "Video: CogVideoX-2b"
