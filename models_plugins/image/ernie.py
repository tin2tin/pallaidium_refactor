"""Text-to-image via ERNIE-Image (baidu/ERNIE-Image)."""

from ...models.base import ModelPlugin, InputSpec, UISection, ParamSpec, ModelInputs
from ...utils.helpers import gfx_device, low_vram


class ErniePlugin(ModelPlugin):
    MODEL_ID     = "baidu/ERNIE-Image"
    DISPLAY_NAME = "Image: ERNIE-Image"
    MODEL_TYPE   = "image"
    DESCRIPTION  = "High-quality text-to-image via ERNIE-Image"

    INPUTS       = InputSpec.PROMPT | InputSpec.NEG_PROMPT
    UI_SECTIONS  =[
        UISection.PROMPT, UISection.NEG_PROMPT,
        UISection.RESOLUTION, UISection.STEPS, UISection.GUIDANCE, UISection.SEED,
    ]
    # ERNIE base uses 50 steps and 4.0 guidance scale
    PARAMS       = ParamSpec(steps=50, guidance=4.0)
    REQUIRED_PACKAGES = ["torch", "diffusers"]

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