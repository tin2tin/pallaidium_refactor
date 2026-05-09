"""Multi-image editing via Qwen-Image-Edit-2511."""

from ...models.base import ModelPlugin, InputSpec, UISection, ParamSpec, ModelInputs
from ...utils.helpers import gfx_device, low_vram, find_strip_by_name, get_strip_path, load_first_frame


class QwenImageEditPlugin(ModelPlugin):
    MODEL_ID     = "Qwen/Qwen-Image-Edit-2511"
    DISPLAY_NAME = "Image: Qwen Image Edit (multi-image)"
    MODEL_TYPE   = "image"
    DESCRIPTION  = "Multi-image instruction editing via Qwen-Image-Edit-2511"

    INPUTS       = InputSpec.PROMPT | InputSpec.NEG_PROMPT | InputSpec.MULTI_IMAGE
    UI_SECTIONS  = [
        UISection.PROMPT, UISection.NEG_PROMPT, UISection.MULTI_IMAGES,
        UISection.STEPS, UISection.SEED,
    ]
    PARAMS       = ParamSpec(steps=4, max_multi_images=3)
    REQUIRED_PACKAGES          = ["torch", "diffusers", "transformers"]
    supports_inpaint           = False
    supports_img2img           = False
    requires_input_strip       = True
    uses_standard_input_strip  = False

    def load(self, prefs, scene, **kw):
        import torch
        from transformers import BitsAndBytesConfig as TBnB, Qwen2_5_VLForConditionalGeneration
        from diffusers import BitsAndBytesConfig as DBnB, QwenImageEditPlusPipeline, QwenImageTransformer2DModel

        model_id = self.MODEL_ID
        dtype = torch.bfloat16
        print(f"Loading {model_id}…")

        q_transformer = DBnB(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                              bnb_4bit_compute_dtype=dtype,
                              llm_int8_skip_modules=["transformer_blocks.0.img_mod"])
        q_text = TBnB(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=dtype)

        transformer = QwenImageTransformer2DModel.from_pretrained(
            model_id, subfolder="transformer", quantization_config=q_transformer,
            torch_dtype=dtype,
        ).to("cpu")
        text_encoder = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            model_id, subfolder="text_encoder", quantization_config=q_text,
            torch_dtype=dtype,
        ).to("cpu")

        pipe = QwenImageEditPlusPipeline.from_pretrained(
            model_id, transformer=transformer, text_encoder=text_encoder, torch_dtype=dtype
        )
        pipe.load_lora_weights(
            "lightx2v/Qwen-Image-Edit-2511-Lightning",
            weight_name="Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors",
        )
        if gfx_device == "mps":
            pipe.to("mps")
        elif low_vram():
            pipe.enable_sequential_cpu_offload()
            pipe.vae.enable_tiling()
        else:
            pipe.enable_model_cpu_offload()
        return {"pipe": pipe, "converter": None, "refiner": None, "preprocessor": None}

    def draw_custom_ui(self, col, context) -> bool:
        scene = context.scene
        try:
            col.prop(scene, "input_strips", text="Input")
        except Exception:
            pass
        if scene.sequence_editor is None:
            return True
        for attr, action in [
            ("qwen_strip_1", "qwen_select1"),
            ("qwen_strip_2", "qwen_select2"),
            ("qwen_strip_3", "qwen_select3"),
        ]:
            row = col.row(align=True)
            row.prop_search(
                scene, attr, scene.sequence_editor, "strips",
                text="", icon="FILE_IMAGE",
            )
            row.operator("sequencer.strip_picker", text="", icon="EYEDROPPER").action = action
        return True

    def generate(self, pipe_obj, inputs: ModelInputs, scene, prefs):
        import torch

        pipe = pipe_obj["pipe"]
        seed = inputs.seed
        generator = (
            torch.Generator("cuda").manual_seed(seed)
            if torch.cuda.is_available() and seed != 0 else None
        )

        qwen_images = []
        if scene.input_strips == "input_strips" and inputs.image is not None:
            qwen_images.append(inputs.image)
        for attr in ["qwen_strip_1", "qwen_strip_2", "qwen_strip_3"]:
            strip_name = getattr(scene, attr, None)
            if strip_name:
                strip = find_strip_by_name(scene, strip_name)
                if strip:
                    qwen_images.append(load_first_frame(get_strip_path(strip)))

        if not qwen_images:
            raise ValueError("Qwen-Image-Edit requires at least one input image.")

        with torch.inference_mode():
            return pipe(
                image=qwen_images,
                prompt=inputs.prompt,
                generator=generator,
                true_cfg_scale=4.0,
                negative_prompt=inputs.neg_prompt + " ",
                num_inference_steps=inputs.steps,
                num_images_per_prompt=1,
            ).images[0]
