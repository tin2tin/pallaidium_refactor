"""Background removal via BiRefNet-HR (ZhengPeng7/BiRefNet_HR)."""

from ...models.base import ModelPlugin, InputSpec, UISection, ParamSpec, ModelInputs
from ...utils.helpers import gfx_device, low_vram


class BiRefNetPlugin(ModelPlugin):
    MODEL_ID     = "ZhengPeng7/BiRefNet_HR"
    DISPLAY_NAME = "Image: Remove Background (BiRefNet)"
    MODEL_TYPE   = "image"
    DESCRIPTION  = "AI background removal via BiRefNet-HR"

    INPUTS       = InputSpec.IMAGE
    UI_SECTIONS  = []
    PARAMS       = ParamSpec()
    REQUIRED_PACKAGES          = ["torch", "torchvision", "transformers", "PIL"]
    supports_inpaint           = False
    supports_img2img           = False
    uses_standard_input_strip  = False

    def load(self, prefs, scene, **kw):
        import torch
        from transformers import AutoModelForImageSegmentation

        print("Loading BiRefNet-HR…")
        pipe = AutoModelForImageSegmentation.from_pretrained(
            self.MODEL_ID, trust_remote_code=True
        )
        if gfx_device == "mps":
            pipe.to("mps")
        elif low_vram():
            pass  # BiRefNet doesn't have cpu_offload; keep on CPU
        else:
            pipe.to(gfx_device)
        return {"pipe": pipe, "converter": None, "refiner": None, "preprocessor": None}

    def generate(self, pipe_obj, inputs: ModelInputs, scene, prefs):
        import torch
        from torchvision import transforms
        from PIL import Image

        pipe  = pipe_obj["pipe"]
        image = inputs.image
        if image is None:
            raise ValueError("BiRefNet requires an input image.")

        image_size = image.size
        transform_image = transforms.Compose([
            transforms.Resize((2048, 2048)),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
        ])
        input_tensor = transform_image(image.convert("RGB")).unsqueeze(0).to(gfx_device)

        with torch.no_grad():
            preds = pipe(input_tensor)[-1].sigmoid().cpu()
        pred = preds[0].squeeze()
        mask = transforms.ToPILImage()(pred).resize(image_size)

        result = image.copy()
        result.putalpha(mask)
        return result
