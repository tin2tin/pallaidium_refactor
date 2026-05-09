"""Text-to-image and img2img via FLUX.2 Klein (9B quantized, two MODEL_ID aliases)."""

from ...models.base import ModelPlugin, InputSpec, UISection, ParamSpec, ModelInputs
from ...utils.helpers import gfx_device, low_vram


class _Flux2KleinBase(ModelPlugin):
    MODEL_TYPE  = "image"
    INPUTS      = InputSpec.PROMPT | InputSpec.IMAGE | InputSpec.LORA
    UI_SECTIONS = [
        UISection.PROMPT, UISection.IMAGE_STRIP,
        UISection.RESOLUTION, UISection.STEPS, UISection.GUIDANCE, UISection.SEED,
        UISection.LORA,
    ]
    PARAMS      = ParamSpec(steps=4, guidance=1.0)
    REQUIRED_PACKAGES = ["torch", "diffusers", "transformers"]
    supports_inpaint  = False

    _BASE_PIPELINE = "black-forest-labs/FLUX.2-klein-9b-kv"
    _TRANSFORMER   = "OzzyGT/flux2_klein_9B_bnb_4bit_transformer"
    _TEXT_ENCODER  = "OzzyGT/flux2_klein_9B_bnb_4bit_text_encoder"

    def _build_klein_pipe(self):
        import torch
        from diffusers import Flux2KleinPipeline, Flux2Transformer2DModel
        from transformers import Qwen3ForCausalLM

        dtype = torch.bfloat16
        transformer = Flux2Transformer2DModel.from_pretrained(
            self._TRANSFORMER, torch_dtype=dtype, device_map="cpu"
        )
        text_encoder = Qwen3ForCausalLM.from_pretrained(
            self._TEXT_ENCODER, torch_dtype=dtype, device_map="cpu"
        )
        pipe = Flux2KleinPipeline.from_pretrained(
            self._BASE_PIPELINE,
            transformer=transformer, text_encoder=text_encoder, torch_dtype=dtype,
        )
        if gfx_device == "mps":
            pipe.to("mps")
        else:
            pipe.enable_model_cpu_offload()
        return pipe

    def load(self, prefs, scene, **kw):
        import torch

        mode = kw.get("mode", "txt2img")
        print(f"Loading {self.MODEL_ID} ({mode})…")

        if mode == "inpaint":
            from diffusers import DiffusionPipeline, FluxFillPipeline, FluxTransformer2DModel
            from transformers import T5EncoderModel

            orig = DiffusionPipeline.from_pretrained(
                self._BASE_PIPELINE, torch_dtype=torch.bfloat16
            )
            transformer = FluxTransformer2DModel.from_pretrained(
                "sayakpaul/FLUX.1-Fill-dev-nf4", subfolder="transformer",
                torch_dtype=torch.bfloat16,
            )
            text_enc_2 = T5EncoderModel.from_pretrained(
                "sayakpaul/FLUX.1-Fill-dev-nf4", subfolder="text_encoder_2",
                torch_dtype=torch.bfloat16,
            )
            pipe = FluxFillPipeline.from_pipe(
                orig, transformer=transformer, text_encoder_2=text_enc_2,
                torch_dtype=torch.bfloat16,
            )
            if gfx_device == "mps":
                pipe.to("mps")
            elif low_vram():
                pipe.enable_sequential_cpu_offload()
                pipe.vae.enable_tiling()
            else:
                pipe.enable_model_cpu_offload()
            return {"pipe": pipe, "converter": None, "refiner": None, "preprocessor": None}

        pipe = self._build_klein_pipe()
        return {"pipe": pipe, "converter": pipe, "refiner": None, "preprocessor": None}

    def generate(self, pipe_obj, inputs: ModelInputs, scene, prefs):
        import torch

        seed = inputs.seed
        generator = (
            torch.Generator("cuda").manual_seed(seed)
            if torch.cuda.is_available() and seed != 0 else None
        )
        common = dict(
            prompt=inputs.prompt,
            max_sequence_length=512,
            guidance_scale=1.0,
            num_inference_steps=4,
            height=inputs.height,
            width=inputs.width,
            generator=generator,
        )
        if inputs.mode == "inpaint" and inputs.image is not None and inputs.inpaint_mask is not None:
            return pipe_obj["pipe"](
                **common, image=inputs.image, mask_image=inputs.inpaint_mask,
            ).images[0]
        if inputs.mode == "img2img" and inputs.image is not None:
            return pipe_obj["converter"](**common, image=inputs.image).images[0]
        return pipe_obj["pipe"](**common).images[0]


class Flux2KleinBasePlugin(_Flux2KleinBase):
    MODEL_ID     = "Runware/BFL-FLUX.2-klein-base-4B"
    DISPLAY_NAME = "Image: FLUX.2 Klein 4B"
    DESCRIPTION  = "Fast text-to-image via FLUX.2 Klein 4B (quantized)"


class Flux2Klein9BPlugin(_Flux2KleinBase):
    MODEL_ID     = "black-forest-labs/FLUX.2-klein-9b-kv"
    DISPLAY_NAME = "Image: FLUX.2 Klein 9B"
    DESCRIPTION  = "Fast text-to-image via FLUX.2 Klein 9B (quantized)"
