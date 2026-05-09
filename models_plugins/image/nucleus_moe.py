"""Text-to-image via NucleusMoE-Image (NucleusAI/NucleusMoE-Image)."""

from ...models.base import ModelPlugin, InputSpec, UISection, ParamSpec, ModelInputs
from ...utils.helpers import gfx_device, low_vram


class NucleusMoEPlugin(ModelPlugin):
    MODEL_ID     = "NucleusAI/NucleusMoE-Image"
    DISPLAY_NAME = "Image: NucleusMoE"
    MODEL_TYPE   = "image"
    DESCRIPTION  = "High-quality text-to-image via NucleusMoE-Image (Sparse MoE)"

    INPUTS       = InputSpec.PROMPT | InputSpec.NEG_PROMPT
    UI_SECTIONS  =[
        UISection.PROMPT, UISection.NEG_PROMPT,
        UISection.RESOLUTION, UISection.STEPS, UISection.GUIDANCE, UISection.SEED,
    ]
    # NucleusMoE benchmarks typically use 50 steps and 8.0 guidance scale
    PARAMS       = ParamSpec(steps=50, guidance=8.0)
    REQUIRED_PACKAGES =["torch", "diffusers"]

    def load(self, prefs, scene, **kw):
        import torch
        from diffusers import NucleusMoEImagePipeline

        print(f"Loading {self.MODEL_ID}…")
        pipe = NucleusMoEImagePipeline.from_pretrained(self.MODEL_ID, torch_dtype=torch.bfloat16)
        
        if gfx_device == "mps":
            pipe.to("mps")
        elif low_vram():
            pipe.enable_model_cpu_offload()
        else:
            pipe.to(gfx_device)
            
        # Optional: Enable text KV caching natively added for NucleusMoE in diffusers 
        # to speed up inference caching across steps.
        try:
            from diffusers import TextKVCacheConfig
            pipe.transformer.enable_cache(TextKVCacheConfig())
            print("NucleusMoE TextKVCacheConfig enabled.")
        except (ImportError, AttributeError):
            pass
            
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
        ).images[0]