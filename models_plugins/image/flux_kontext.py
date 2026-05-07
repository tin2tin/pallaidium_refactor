"""Instruction-based image editing via FLUX.1-Kontext-dev (img2img and inpaint)."""

from ...models.base import ModelPlugin, InputSpec, UISection, ParamSpec, ModelInputs
from ...utils.helpers import gfx_device, low_vram, find_strip_by_name, get_strip_path, load_first_frame


class FluxKontextPlugin(ModelPlugin):
    MODEL_ID     = "yuvraj108c/FLUX.1-Kontext-dev"
    DISPLAY_NAME = "Image: FLUX Kontext (image editing)"
    MODEL_TYPE   = "image"
    DESCRIPTION  = "Instruction-based image editing via FLUX.1-Kontext-dev"

    INPUTS       = InputSpec.PROMPT | InputSpec.IMAGE
    UI_SECTIONS  = [
        UISection.PROMPT, UISection.IMAGE_STRIP,
        UISection.RESOLUTION, UISection.STEPS, UISection.GUIDANCE,
        UISection.IMAGE_STRENGTH, UISection.SEED,
    ]
    PARAMS       = ParamSpec(steps=28, guidance=3.5)
    REQUIRED_PACKAGES = ["torch", "diffusers"]

    def _build_nf4_transformer(self):
        import torch
        from diffusers import BitsAndBytesConfig, FluxTransformer2DModel

        nf4 = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
        return FluxTransformer2DModel.from_pretrained(
            self.MODEL_ID, subfolder="transformer",
            quantization_config=nf4, torch_dtype=torch.bfloat16,
        )

    def load(self, prefs, scene, **kw):
        import torch

        mode = kw.get("mode", "img2img")
        print(f"Loading {self.MODEL_ID} ({mode})…")

        if mode == "inpaint":
            from diffusers import FluxKontextInpaintPipeline

            transformer = self._build_nf4_transformer()
            pipe = FluxKontextInpaintPipeline.from_pretrained(
                self.MODEL_ID, transformer=transformer, torch_dtype=torch.bfloat16,
            )
            if gfx_device == "mps":
                pipe.to("mps")
            elif low_vram():
                pipe.enable_model_cpu_offload()
            else:
                pipe.to(gfx_device)
            return {"pipe": pipe, "converter": None, "refiner": None, "preprocessor": None}

        from diffusers import FluxKontextPipeline

        transformer = self._build_nf4_transformer()
        converter = FluxKontextPipeline.from_pretrained(
            self.MODEL_ID, transformer=transformer, torch_dtype=torch.bfloat16,
        )
        if gfx_device == "mps":
            converter.to("mps")
        elif low_vram():
            converter.enable_sequential_cpu_offload()
            converter.vae.enable_slicing()
            converter.vae.enable_tiling()
        else:
            converter.enable_model_cpu_offload()
        return {"pipe": converter, "converter": converter, "refiner": None, "preprocessor": None}

    def generate(self, pipe_obj, inputs: ModelInputs, scene, prefs):
        import torch

        seed = inputs.seed
        generator = (
            torch.Generator("cuda").manual_seed(seed)
            if torch.cuda.is_available() and seed != 0 else None
        )

        init_image = inputs.image
        if init_image is None:
            strip_name = getattr(scene, "kontext_strip_1", None)
            if strip_name:
                strip = find_strip_by_name(scene, strip_name)
                if strip:
                    init_image = load_first_frame(get_strip_path(strip))

        if inputs.mode == "inpaint" and init_image is not None and inputs.inpaint_mask is not None:
            ref_image = None
            strip_name = getattr(scene, "kontext_strip_1", None)
            if strip_name:
                strip = find_strip_by_name(scene, strip_name)
                if strip:
                    ref_image = load_first_frame(get_strip_path(strip))
            return pipe_obj["pipe"](
                prompt=inputs.prompt,
                max_sequence_length=512,
                image=init_image,
                mask_image=inputs.inpaint_mask,
                image_reference=ref_image,
                num_inference_steps=inputs.steps,
                guidance_scale=inputs.guidance,
                height=inputs.height,
                width=inputs.width,
                strength=1.0 - inputs.strength,
                generator=generator,
            ).images[0]

        return pipe_obj["converter"](
            prompt=inputs.prompt,
            max_sequence_length=512,
            image=init_image,
            num_inference_steps=inputs.steps,
            guidance_scale=inputs.guidance,
            height=inputs.height,
            width=inputs.width,
            generator=generator,
        ).images[0]
