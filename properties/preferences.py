import bpy
from bpy_extras.io_utils import ExportHelper
import ctypes
import random
import site
import platform
import json
import subprocess
import sys
import os
import aud
import re
import glob
import string
from os.path import dirname, realpath, isdir, join, basename
import shutil
from datetime import date
import pathlib
import gc
import time
from bpy_extras.io_utils import ImportHelper
from bpy.types import Operator, Panel, AddonPreferences, UIList, PropertyGroup
from bpy.props import (
    StringProperty,
    BoolProperty,
    EnumProperty,
    IntProperty,
    FloatProperty,
)
import sys
import base64
from io import BytesIO
import asyncio
import inspect
from fractions import Fraction
import importlib
import importlib.metadata
import warnings
import logging
import bpy
import os
import re
from datetime import date

from ..utils.helpers import *

class GeneratorAddonPreferences(AddonPreferences):
    bl_idname = __package__.rsplit(".", 1)[0]
    soundselect: EnumProperty(
        name="Sound",
        items={
            ("ding", "Ding", "A simple bell sound"),
            ("coin", "Coin", "A Mario-like coin sound"),
            ("user", "User", "Load a custom sound file"),
        },
        default="ding",
    )
    default_folder = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sounds", "*.wav")

    usersound: StringProperty(
        name="User",
        description="Load a custom sound from your computer",
        subtype="FILE_PATH",
        default=default_folder,
        maxlen=1024,
    )

    playsound: BoolProperty(
        name="Audio Notification",
        default=True,
    )

    movie_model_card: bpy.props.EnumProperty(
        name="Video Model",
            items=[
            ("Hailuo/MiniMax/txt2vid", "API MiniMax (txt2vid)", "Purchased API access needed!"),
            ("Hailuo/MiniMax/img2vid", "API MiniMax (img2vid)", "Purchased API access needed!"),
            (
                "Hailuo/MiniMax/subject2vid",
                "API MiniMax (subject2vid)",
                "Purchased API access needed!",
            ),
            #("THUDM/CogVideoX-2b", "CogVideoX-2b (720x480x48)", "THUDM/CogVideoX-2b"),
            #("THUDM/CogVideoX-5b", "CogVideoX-5b (720x480x48)", "THUDM/CogVideoX-5b"),
#            (
#                "hunyuanvideo-community/HunyuanVideo",
#                "Hunyuan Video 960x544x(frames/4+1)",
#                "hunyuanvideo-community/HunyuanVideo",
#            ),
            (
                "lllyasviel/FramePackI2V_HY",
                "FramePack 960x544x(frames/4+1)",
                "lllyasviel/FramePackI2V_HY",
            ),
#            (
#                "Lightricks/LTX-2",
#                "LTX-2",
#                "Lightricks/LTX-2",
#            ), 
#            (
#                "rootonchair/LTX-2-19b-distilled",
#                "LTX-2 19b Distilled",
#                "rootonchair/LTX-2-19b-distilled",
#            ), 
            (
                "LTX-2 Multi-Input File",
                "LTX-2 Multi-Input (Txt, Aud & Img in Meta Strips)",
                "LTX-2 Multi-Input File",
            ),           
            (
                "Lightricks/LTX-Video",
                "LTX 0.9.7 (1280x720x257(frames/8+1))",
                "Lightricks/LTX-Video",
            ),
            (
                "Skywork/SkyReels-V1-Hunyuan-T2V",
                "SkyReels-V1-Hunyuan (960x544x97)",
                "Skywork/SkyReels-V1-Hunyuan-T2V",
            ),
            (
                "Wan-AI/Wan2.2-T2V-A14B-Diffusers",
                "Wan2.2-T2V (832x480x81)",
                "Wan-AI/Wan2.2-T2V-A14B-Diffusers",
            ),
            (
                "Wan-AI/Wan2.2-I2V-A14B-Diffusers",
                "Wan2.2-I2V-14B (832x480x81)",
                "Wan-AI/Wan2.2-I2V-A14B-Diffusers",
            ),
            (
                "stabilityai/stable-diffusion-xl-base-1.0",
                "Frame by Frame SDXL Turbo (1024x1024)",
                "Stable Diffusion XL 1.0",
            ),
        ],
        default="Lightricks/LTX-Video",
        update=input_strips_updated,
    )
    image_model_card: bpy.props.EnumProperty(
        name="Image Model",
        items=[
            ("Qwen/Qwen-Image-2512", "Qwen-Image-2512-Turbo", "Qwen/Qwen-Image-2512"),
            (
                "Qwen/Qwen-Image-Edit-2511",
                "Qwen Multi-image Edit 2511",
                "Text and multiple images as input.",
            ),
            # ("diffusers/FLUX.2-dev-bnb-4bit", "Flux2", "diffusers/FLUX.2-dev-bnb-4bit"),
            ("Runware/BFL-FLUX.2-klein-base-4B", "FLUX.2 klein 4B", "Runware/BFL-FLUX.2-klein-base-4B"),
            ("black-forest-labs/FLUX.2-klein-9b-kv", "FLUX.2 klein 9B", "black-forest-labs/FLUX.2-klein-9b-kv"),
            ("ChuckMcSneed/FLUX.1-dev", "Flux Dev", "ChuckMcSneed/FLUX.1-dev"),
            ("Tongyi-MAI/Z-Image", "Z-Image", "Tongyi-MAI/Z-Image"),
            ("Tongyi-MAI/Z-Image-Turbo", "Z-Image Turbo", "Tongyi-MAI/Z-Image-Turbo"),
            (
                "lzyvegetable/FLUX.1-schnell",
                "Flux Schnell",
                "lzyvegetable/FLUX.1-schnell",
            ),
            ("yuvraj108c/FLUX.1-Kontext-dev", "Flux Kontext", "yuvraj108c/FLUX.1-Kontext-dev"),

            ("kontext-community/relighting-kontext-dev-lora-v3", "Flux Kontext Relight", "kontext-community/relighting-kontext-dev-lora-v3"),

            # ("fuliucansheng/FLUX.1-Canny-dev-diffusers-lora", "Flux Canny", "fuliucansheng/FLUX.1-Canny-dev-diffusers-lora"),

            ("romanfratric234/FLUX.1-Depth-dev-lora", "Flux Depth", "romanfratric234/FLUX.1-Depth-dev-lora"),

            # ("Runware/FLUX.1-Redux-dev", "Flux Redux", "Runware/FLUX.1-Redux-dev"),

            ("lodestones/Chroma", "Chroma", "Chroma is a 8.9B parameter model based on FLUX.1-schnell"),
            (
                "stabilityai/stable-diffusion-xl-base-1.0",
                "SDXL 1.0 (1024x1024)",
                "stabilityai/stable-diffusion-xl-base-1.0",
            ),
#            (
#                "adamo1139/stable-diffusion-3.5-large-ungated",
#                "SDXL 3.5 Large",
#                "adamo1139/stable-diffusion-3.5-large-ungated",
#            ),
            (
                "adamo1139/stable-diffusion-3.5-medium-ungated",
                "SDXL 3.5 Medium",
                "adamo1139/stable-diffusion-3.5-medium-ungated",
            ),
            (
                "Alpha-VLLM/Lumina-Image-2.0",
                "Lumina Image 2.0",
                "Alpha-VLLM/Lumina-Image-2.0",
            ),
            (
                "diffusers/controlnet-canny-sdxl-1.0-small",
                "SDXL Canny (1024 x 1024)",
                "diffusers/controlnet-canny-sdxl-1.0-small",
            ),
            (
                "xinsir/controlnet-openpose-sdxl-1.0",
                "SDXL OpenPose (1024 x 1024)",
                "xinsir/controlnet-openpose-sdxl-1.0",
            ),
            (
                "xinsir/controlnet-scribble-sdxl-1.0",
                "SDXL Scribble (1024x1024)",
                "xinsir/controlnet-scribble-sdxl-1.0",
            ),
            (
                "Shitao/OmniGen-v1-diffusers",
                "OmniGen",
                "Text and image input.",
            ),
            (
                "ZhengPeng7/BiRefNet_HR",
                "BiRefNet Remove Background",
                "ZhengPeng7/BiRefNet_HR",
            ),
        ],
        default="stabilityai/stable-diffusion-xl-base-1.0",
        update=output_strips_updated,
    )
#    if low_vram(): # broken by transformers
#        parler = (
#            "parler-tts/parler-tts-mini-v1",
#            "Speech: Parler TTS Mini",
#            "parler-tts/parler-tts-mini-v1",
#        )
#    else:
#        parler = (
#            "parler-tts/parler-tts-large-v1",
#            "Speech: Parler TTS Large",
#            "parler-tts/parler-tts-large-v1",
#        )

    if os_platform != "Linux":
        items = [
            ("Chatterbox", "Speech: Chatterbox", "Zero shot TTS & voice conversion"),
            ("ChatterboxTurbo", "Speech: ChatterboxTurbo", "Zero shot TTS & voice conversion"),
            ("Qwen/Qwen3-TTS-12Hz-1.7B-Base", "Speech: Qwen3-TTS Clone", "Qwen/Qwen3-TTS-12Hz-1.7B-Base"),
            # ("SWivid/F5-TTS", "Speech: F5-TTS", "Zero shot TTS"), Broken dependencies
#            ("WhisperSpeech", "Speech: WhisperSpeech", "Zero shot TTS"),
            ("MMAudio", "Audio: Video to Audio", "Add sync audio to video"),
            (
                "tintwotin/Foundation-1-Diffusers",
                "Music Loop: Fountain 1",
                "Text to Music",
            ),
            #parler,
        ]
    else:
        items = [
            # ("SWivid/F5-TTS", "Speech: F5-TTS", "SWivid/F5-TTS"), Broken dependencies
            ("Chatterbox", "Chatterbox", "Zero shot txt2speech & voice cloning"),
            ("ChatterboxTurbo", "Speech: ChatterboxTurbo", "Zero shot TTS & voice conversion"),
            ("Qwen/Qwen3-TTS-12Hz-1.7B-Base", "Speech: Qwen3-TTS Clone", "Qwen/Qwen3-TTS-12Hz-1.7B-Base"),
            ("MMAudio", "Audio: Video to Audio", "Add sync audio to video"),
            (
                "tintwotin/Foundation-1-Diffusers",
                "Stable Audio Open",
                "Text to Music",
            ),
            #parler,
        ]

    audio_model_card: bpy.props.EnumProperty(
        name="Audio Model",
        items=items,
        default="tintwotin/Foundation-1-Diffusers",
        update=output_strips_updated,
    )
    hugginface_token: bpy.props.StringProperty(
        name="Hugginface Token",
        default="hugginface_token",
        subtype="PASSWORD",
    )
    text_model_card: EnumProperty(
        name="Text Model",
        items=[
            (
                "Salesforce/blip-image-captioning-large",
                "Image Captioning: Blip",
                "Image Captioning",
            ),
            (
                "florence-community/Florence-2-large",
                "Image Captioning: Florence-2",
                "Image Captioning",
            ),
#            ( #torchao error
#                "ZuluVision/MoviiGen1.1_Prompt_Rewriter",
#                "Prompt Enhancer: MoviiGen",
#                "MoviiGen Prompt Rewriter",
#            ),
        ],
        default="Salesforce/blip-image-captioning-large",
        update=output_strips_updated,
    )
    generator_ai: StringProperty(
        name="Filepath",
        description="Path to the folder where the generated files are stored",
        subtype="DIR_PATH",
        default=join(bpy.utils.user_resource("DATAFILES"), "Pallaidium_Media"),
    )
    use_strip_data: BoolProperty(
        name="Use Input Strip Data",
        default=True,
    )
    local_files_only: BoolProperty(
        name="Use Local Files Only",
        default=False,
    )

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        row = box.row()
        row.operator("sequencer.install_generator")
        row.operator("sequencer.uninstall_generator")
        row.operator("sequencer.export_requirements")
        try:
            box.prop(self, "movie_model_card")
            box.prop(self, "image_model_card")
        except:
            pass
        if (
            self.image_model_card == "adamo1139/stable-diffusion-3.5-large-ungated"
            or (self.image_model_card == "ChuckMcSneed/FLUX.1-dev" and os_platform == "Darwin")
            or (self.image_model_card == "lzyvegetable/FLUX.1-schnell" and os_platform == "Darwin")
            or (self.image_model_card == "diffusers/FLUX.2-dev-bnb-4bit")
        ):
            row = box.row(align=True)
            row.prop(self, "hugginface_token")
            row.operator(
                "wm.url_open", text="", icon="URL"
            ).url = "https://huggingface.co/settings/tokens"
        try:
            box.prop(self, "audio_model_card")
        except:
            pass
        box.prop(self, "generator_ai")
        row = box.row(align=True)
        row.label(text="Notification:")
        row.prop(self, "playsound", text="")
        sub_row = row.row()
        sub_row.prop(self, "soundselect", text="")
        if self.soundselect == "user":
            sub_row.prop(self, "usersound", text="")
        sub_row.operator(
            "renderreminder.pallaidium_play_notification", text="", icon="PLAY"
        )
        sub_row.active = self.playsound

        row_row = box.row(align=True)
        row_row.label(text="Use Input Strip Data:")
        row_row.prop(self, "use_strip_data", text="")
        row_row.label(text="")
        row_row.label(text="")
        row_row.label(text="")

        row_row = box.row(align=True)
        row_row.label(text="Use Local Files Only:")
        row_row.prop(self, "local_files_only", text="")
        row_row.label(text="")
        row_row.label(text="")
        row_row.label(text="")