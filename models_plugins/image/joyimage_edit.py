"""Instruction-guided image editing via JoyAI-Image-Edit (spatial editing support)."""

from ...models.base import ModelPlugin, InputSpec, UISection, ParamSpec, ModelInputs
from ...utils.helpers import gfx_device, low_vram


_SPATIAL_MODES = [
    ("general",  "General Edit",      "Instruction-guided image editing"),
    ("move",     "Object Move",       "Move object into a red-box region"),
    ("rotate",   "Object Rotation",   "Rotate object to a canonical view"),
    ("camera",   "Camera Control",    "Shift camera viewpoint, keep 3D scene"),
]

_ROTATE_VIEWS = [
    "front", "right", "left", "rear",
    "front right", "front left", "rear right", "rear left",
]

_CAMERA_ZOOM = ["unchanged", "in", "out"]


def _register_scene_props():
    """Register bpy scene properties needed by this plugin."""
    try:
        import bpy

        if not hasattr(bpy.types.Scene, "joyimage_spatial_mode"):
            bpy.types.Scene.joyimage_spatial_mode = bpy.props.EnumProperty(
                name="Spatial Mode",
                items=_SPATIAL_MODES,
                default="general",
            )
        if not hasattr(bpy.types.Scene, "joyimage_object"):
            bpy.types.Scene.joyimage_object = bpy.props.StringProperty(
                name="Object",
                description="Name of the object to move/rotate",
                default="object",
            )
        if not hasattr(bpy.types.Scene, "joyimage_rotate_view"):
            bpy.types.Scene.joyimage_rotate_view = bpy.props.EnumProperty(
                name="View",
                items=[(v, v.title(), "") for v in _ROTATE_VIEWS],
                default="front",
            )
        if not hasattr(bpy.types.Scene, "joyimage_yaw"):
            bpy.types.Scene.joyimage_yaw = bpy.props.FloatProperty(
                name="Yaw (°)", default=0.0, min=-180.0, max=180.0, step=10,
            )
        if not hasattr(bpy.types.Scene, "joyimage_pitch"):
            bpy.types.Scene.joyimage_pitch = bpy.props.FloatProperty(
                name="Pitch (°)", default=0.0, min=-90.0, max=90.0, step=10,
            )
        if not hasattr(bpy.types.Scene, "joyimage_zoom"):
            bpy.types.Scene.joyimage_zoom = bpy.props.EnumProperty(
                name="Zoom",
                items=[(z, z.title(), "") for z in _CAMERA_ZOOM],
                default="unchanged",
            )
    except Exception:
        pass


class JoyImageEditPlugin(ModelPlugin):
    MODEL_ID     = "jdopensource/JoyAI-Image-Edit-Diffusers"
    DISPLAY_NAME = "Image: JoyAI Image Edit (spatial)"
    MODEL_TYPE   = "image"
    DESCRIPTION  = (
        "Instruction-guided image editing with object move, object rotation, "
        "and camera control via JoyAI-Image-Edit"
    )

    INPUTS      = InputSpec.PROMPT | InputSpec.IMAGE
    UI_SECTIONS = [
        UISection.PROMPT,
        UISection.IMAGE_STRIP,
        UISection.STEPS, UISection.GUIDANCE, UISection.SEED,
    ]
    PARAMS = ParamSpec(steps=40, guidance=4.0)

    # diffusers must be installed from git until >0.38.0 is released:
    #   pip install git+https://github.com/huggingface/diffusers.git
    REQUIRED_PACKAGES         = ["torch", "diffusers", "transformers"]
    supports_inpaint          = False
    supports_img2img          = True
    requires_input_strip      = True
    uses_standard_input_strip = False   # prevents strip_power row from appearing
    show_enhance              = False   # hides Quality / Speed / Upscale 4x row

    def load(self, prefs, scene, **kw):
        import torch
        from diffusers import JoyImageEditPipeline

        print(f"Loading {self.MODEL_ID}…")
        pipe = JoyImageEditPipeline.from_pretrained(self.MODEL_ID)
        pipe.to(torch.bfloat16)
        if gfx_device == "mps":
            pipe.to("mps")
        elif low_vram():
            pipe.enable_sequential_cpu_offload()
        else:
            pipe.enable_model_cpu_offload()

        _register_scene_props()
        return {"pipe": pipe, "converter": None, "refiner": None, "preprocessor": None}

    def _build_prompt(self, scene, user_prompt: str) -> str:
        mode = getattr(scene, "joyimage_spatial_mode", "general")
        obj  = getattr(scene, "joyimage_object", "object").strip() or "object"

        if mode == "move":
            return f"Move the {obj} into the red box and finally remove the red box."
        if mode == "rotate":
            view = getattr(scene, "joyimage_rotate_view", "front")
            return f"Rotate the {obj} to show the {view} side view."
        if mode == "camera":
            yaw  = getattr(scene, "joyimage_yaw", 0)
            pitch = getattr(scene, "joyimage_pitch", 0)
            zoom = getattr(scene, "joyimage_zoom", "unchanged")
            return (
                "Move the camera.\n"
                f"- Camera rotation: Yaw {yaw:.1f}°, Pitch {pitch:.1f}°.\n"
                f"- Camera zoom: {zoom}.\n"
                "- Keep the 3D scene static; only change the viewpoint."
            )
        return user_prompt  # general mode — use the user's own prompt

    def draw_custom_ui(self, col, context) -> bool:
        scene = context.scene

        # Always require strips — show the enum locked so the user can see why
        if scene.input_strips != "input_strips":
            scene.input_strips = "input_strips"
        row = col.row()
        row.enabled = False
        row.prop(scene, "input_strips", text="Input")

        col.prop(scene, "joyimage_spatial_mode", text="Mode")

        mode = getattr(scene, "joyimage_spatial_mode", "general")

        if mode == "move":
            col.label(text='Mark target region with a red box in the input image.')
            col.prop(scene, "joyimage_object", text="Object")
        elif mode == "rotate":
            col.prop(scene, "joyimage_object", text="Object")
            col.prop(scene, "joyimage_rotate_view", text="View")
        elif mode == "camera":
            row = col.row(align=True)
            row.prop(scene, "joyimage_yaw",   text="Yaw°")
            row.prop(scene, "joyimage_pitch",  text="Pitch°")
            col.prop(scene, "joyimage_zoom",   text="Zoom")

        return True

    def generate(self, pipe_obj, inputs: ModelInputs, scene, prefs):
        import torch

        pipe = pipe_obj["pipe"]
        seed = inputs.seed
        device = "cuda" if torch.cuda.is_available() else gfx_device
        generator = (
            torch.Generator(device).manual_seed(seed) if seed != 0 else None
        )

        if inputs.image is None:
            raise ValueError("JoyAI-Image-Edit requires an input image.")

        prompt = self._build_prompt(scene, inputs.prompt)

        with torch.inference_mode():
            return pipe(
                image=inputs.image,
                prompt=prompt,
                num_inference_steps=inputs.steps,
                guidance_scale=inputs.guidance,
                generator=generator,
            ).images[0]
