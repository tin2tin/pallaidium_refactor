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


# ---------------------------------------------------------------------------
# Dynamic EnumProperty item callbacks — delegates to the plugin registry
# ---------------------------------------------------------------------------

def _video_enum_items(self, context):
    from ..models import get_enum_items
    return get_enum_items("video")


def _image_enum_items(self, context):
    from ..models import get_enum_items
    return get_enum_items("image")


def _audio_enum_items(self, context):
    from ..models import get_enum_items
    return get_enum_items("audio")


def _text_enum_items(self, context):
    from ..models import get_enum_items
    return get_enum_items("text")


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
        items=_video_enum_items,
        update=input_strips_updated,
    )
    image_model_card: bpy.props.EnumProperty(
        name="Image Model",
        items=_image_enum_items,
        update=output_strips_updated,
    )
    audio_model_card: bpy.props.EnumProperty(
        name="Audio Model",
        items=_audio_enum_items,
        update=output_strips_updated,
    )
    hugginface_token: bpy.props.StringProperty(
        name="Hugginface Token",
        default="hugginface_token",
        subtype="PASSWORD",
    )
    text_model_card: EnumProperty(
        name="Text Model",
        items=_text_enum_items,
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
        from ..models import get_plugin as _gp
        from ..models.base import InputSpec as _IS
        _img_plugin = _gp(self.image_model_card)
        if (
            (_img_plugin is not None and _IS.HF_TOKEN in _img_plugin.INPUTS)
            or (self.image_model_card == "ChuckMcSneed/FLUX.1-dev" and os_platform == "Darwin")
            or (self.image_model_card == "lzyvegetable/FLUX.1-schnell" and os_platform == "Darwin")
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