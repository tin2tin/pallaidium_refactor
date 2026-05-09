"""Fast text-to-image via ERNIE-Image-Turbo (baidu/ERNIE-Image-Turbo)."""

from ...models.base import ModelPlugin, InputSpec, UISection, ParamSpec, ModelInputs
from ...utils.helpers import gfx_device, low_vram


class ErnieTurboPlugin(ModelPlugin):
    MODEL_ID     = "baidu/ERNIE-Image-Turbo"
    DISPLAY_NAME = "Image: ERNIE-Image Turbo"
    MODEL_TYPE   = "image"
    DESCRIPTION  = "Fast text-to-image via ERNIE-Image-Turbo"

    INPUTS       = InputSpec.PROMPT | InputSpec.NEG_PROMPT
    UI_SECTIONS  =[
        UISection.PROMPT, UISection.NEG_PROMPT,
        UISection.RESOLUTION, UISection.STEPS, UISection.GUIDANCE, UISection.SEED,
    ]
    # Turbo model is optimized for 8 steps and a 1.0 guidance scale
    PARAMS       = ParamSpec(steps=8, guidance=1.0)
    REQUIRED_PACKAGES =["torch", "diffusers"]

    def load(self, prefs, scene, **kw):
        import torch
        from diffusers import ErnieImagePipeline

        print(f"Loading {self.MODEL_ID}…")
        pipe = ErnieImagePipeline.from_pretrained(self.MODEL_ID, torch_dtype=torch.bfloat16)
        
        if gfx_device == "mps":
            pipe.to("mps")
        elif low_vram():
            pipe.enable_model_cpu_offload()
        else:
            pipe.enable_sequential_cpu_offload()
            
        return {"pipe": pipe, "converter": None, "refiner": None, "preprocessor": None}

    def generate(self, pipe_obj, inputs: ModelInputs, scene, prefs):
        import torch

        pipe = pipe_obj["pipe"]
        seed = inputs.seed
        generator = (
            torch.Generator("cuda").manual_seed(seed)
            if torch.cuda.is_available() and seed != 0
            else (torch.Generator(device=gfx_device).manual_seed(seed) if seed != 0 else None)
        )
        
        return pipe(
            prompt=inputs.prompt,
            negative_prompt=inputs.neg_prompt,
            num_inference_steps=inputs.steps,
            guidance_scale=inputs.guidance,
            height=inputs.height,
            width=inputs.width,
            generator=generator,
            use_pe=True,
        ).images[0]