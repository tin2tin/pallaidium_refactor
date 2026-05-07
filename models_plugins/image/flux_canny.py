"""FLUX Canny ControlNet (fuliucansheng/FLUX.1-Canny-dev-diffusers-lora)."""

from ...models.base import ModelPlugin, InputSpec, UISection, ParamSpec, ModelInputs
from ...utils.helpers import gfx_device, low_vram


class FluxCannyPlugin(ModelPlugin):
    MODEL_ID     = "fuliucansheng/FLUX.1-Canny-dev-diffusers-lora"
    DISPLAY_NAME = "Image: FLUX Canny ControlNet"
    MODEL_TYPE   = "image"
    DESCRIPTION  = "Edge-guided generation via FLUX.1 Canny ControlNet"

    INPUTS       = InputSpec.PROMPT | InputSpec.IMAGE
    UI_SECTIONS  = [
        UISection.PROMPT, UISection.IMAGE_STRIP,
        UISection.RESOLUTION, UISection.STEPS, UISection.GUIDANCE, UISection.SEED,
    ]
    PARAMS       = ParamSpec(steps=28, guidance=3.5)
    REQUIRED_PACKAGES = ["torch", "diffusers", "controlnet_aux"]

    def load(self, prefs, scene, **kw):
        import torch
        from diffusers import BitsAndBytesConfig, FluxTransformer2DModel, FluxControlPipeline
        from controlnet_aux import CannyDetector

        print("Loading FLUX Canny ControlNet…")
        pipecard = "fuliucansheng/FLUX.1-Canny-dev-diffusers"
        nf4_config = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
        model_nf4 = FluxTransformer2DModel.from_pretrained(
            pipecard, subfolder="transformer",
            quantization_config=nf4_config, torch_dtype=torch.bfloat16,
        )
        pipe = FluxControlPipeline.from_pretrained(
            pipecard, transformer=model_nf4, torch_dtype=torch.bfloat16,
            local_files_only=prefs.local_files_only,
        )
        if gfx_device == "mps":
            pipe.to("mps")
        elif low_vram():
            pipe.enable_model_cpu_offload()
            pipe.vae.enable_slicing()
            pipe.vae.enable_tiling()
        else:
            pipe.enable_model_cpu_offload()
            pipe.vae.enable_slicing()
            pipe.vae.enable_tiling()
        processor = CannyDetector()
        return {"pipe": pipe, "converter": None, "refiner": None, "preprocessor": processor}

    def generate(self, pipe_obj, inputs: ModelInputs, scene, prefs):
        import torch

        pipe      = pipe_obj["pipe"]
        processor = pipe_obj["preprocessor"]
        image     = inputs.image
        if image is None:
            raise ValueError("FLUX Canny requires an input image.")

        seed = inputs.seed
        generator = (
            torch.Generator("cuda").manual_seed(seed)
            if torch.cuda.is_available() and seed != 0 else None
        )
        control_image = processor(
            image,
            low_threshold=50, high_threshold=200,
            detect_resolution=inputs.width,
            image_resolution=inputs.width,
        )
        return pipe(
            prompt=inputs.prompt,
            control_image=control_image,
            num_inference_steps=inputs.steps,
            guidance_scale=inputs.guidance,
            height=inputs.height,
            width=inputs.width,
            generator=generator,
        ).images[0]
