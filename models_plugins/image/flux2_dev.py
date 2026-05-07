"""Text-to-image with multi-image support via FLUX.2-dev (4-bit quantized, HF token required)."""

from ...models.base import ModelPlugin, InputSpec, UISection, ParamSpec, ModelInputs
from ...utils.helpers import gfx_device, low_vram, find_strip_by_name, get_strip_path, load_first_frame


class Flux2DevPlugin(ModelPlugin):
    MODEL_ID     = "diffusers/FLUX.2-dev-bnb-4bit"
    DISPLAY_NAME = "Image: FLUX.2 Dev (4-bit, multi-image)"
    MODEL_TYPE   = "image"
    DESCRIPTION  = "Text-to-image with multi-image support via FLUX.2-dev (HF token required)"

    INPUTS       = InputSpec.PROMPT | InputSpec.MULTI_IMAGE | InputSpec.HF_TOKEN
    UI_SECTIONS  = [
        UISection.PROMPT, UISection.MULTI_IMAGES,
        UISection.RESOLUTION, UISection.STEPS, UISection.GUIDANCE, UISection.SEED,
    ]
    PARAMS       = ParamSpec(steps=8, guidance=3.5)
    REQUIRED_PACKAGES = ["torch", "diffusers", "transformers"]

    def load(self, prefs, scene, **kw):
        import torch
        from transformers import Mistral3ForConditionalGeneration
        from diffusers import Flux2Pipeline, Flux2Transformer2DModel
        from huggingface_hub import login

        print(f"Loading {self.MODEL_ID}…")
        try:
            login(token=prefs.hugginface_token, add_to_git_credential=True)
        except Exception as e:
            raise RuntimeError(f"HuggingFace login failed: {e}")

        dtype = torch.bfloat16
        transformer = Flux2Transformer2DModel.from_pretrained(
            self.MODEL_ID, subfolder="transformer", torch_dtype=dtype, device_map="cpu"
        )
        text_encoder = Mistral3ForConditionalGeneration.from_pretrained(
            self.MODEL_ID, subfolder="text_encoder", dtype=dtype, device_map="cpu"
        )
        pipe = Flux2Pipeline.from_pretrained(
            self.MODEL_ID, transformer=transformer, text_encoder=text_encoder, torch_dtype=dtype
        )
        pipe.load_lora_weights(
            "fal/FLUX.2-dev-Turbo", weight_name="flux.2-turbo-lora.safetensors"
        )
        if gfx_device == "mps":
            pipe.to("mps")
        else:
            pipe.enable_model_cpu_offload()
            pipe.vae.enable_tiling()
        return {"pipe": pipe, "converter": None, "refiner": None, "preprocessor": None}

    def generate(self, pipe_obj, inputs: ModelInputs, scene, prefs):
        import torch

        pipe = pipe_obj["pipe"]
        seed = inputs.seed
        generator = (
            torch.Generator("cuda").manual_seed(seed)
            if torch.cuda.is_available() and seed != 0 else None
        )

        flux_images = []
        if inputs.image is not None:
            flux_images.append(inputs.image)
        for i in range(1, 10):
            strip_name = getattr(scene, f"flux_strip_{i}", None)
            if strip_name:
                strip = find_strip_by_name(scene, strip_name)
                if strip:
                    flux_images.append(load_first_frame(get_strip_path(strip)))

        return pipe(
            image=flux_images if flux_images else None,
            prompt=inputs.prompt,
            generator=generator,
            max_sequence_length=512,
            num_inference_steps=inputs.steps,
            guidance_scale=inputs.guidance,
            height=inputs.height,
            width=inputs.width,
        ).images[0]
