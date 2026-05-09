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
from ..properties.scene_props import *
from ..properties.preferences import *

class SEQUENCER_PT_pallaidium_panel(Panel):  # UI
    """Generate Media using AI"""

    bl_idname = "SEQUENCER_PT_sequencer_generate_movie_panel"
    bl_label = "Pallaidium"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Generative AI"

    @classmethod
    def poll(cls, context):
        return context.area.type == "SEQUENCE_EDITOR"

    def draw(self, context):
        preferences = context.preferences
        addon_prefs = preferences.addons[ADDON_ID].preferences
        audio_model_card = addon_prefs.audio_model_card
        movie_model_card = addon_prefs.movie_model_card
        image_model_card = addon_prefs.image_model_card
        text_model_card = addon_prefs.text_model_card
        scene = context.scene
        type = scene.generatorai_typeselect
        input = scene.input_strips
        layout = self.layout

        # --- Plugin-driven rendering ---
        from ..models import get_plugin as _reg_get_plugin
        from ..models.base import UISection
        _card = {"movie": movie_model_card, "image": image_model_card,
                 "audio": audio_model_card, "text": text_model_card}.get(type, "")
        plugin = _reg_get_plugin(_card)
        def _has(sec): return plugin is None or sec in (plugin.UI_SECTIONS or [])
        col = layout.column(align=False)
        col.use_property_split = True
        col.use_property_decorate = False
        col = col.box()
        col = col.column()

        if scene.sequence_editor is None:
            scene.sequence_editor_create()

        drew_custom = (plugin.draw_custom_ui(col, context) is True) if (plugin and type == "image") else False
        if not drew_custom:
            try:
                col.prop(context.scene, "input_strips", text="Input")
            except:
                pass


        if type != "text":
            if type != "audio":
                if type == "movie" and plugin is not None and not plugin.uses_standard_input_strip:
                    plugin.draw_custom_ui(col, context)

                elif (type == "movie") or (type == "image" and (plugin is None or plugin.uses_standard_input_strip)):
                    if input == "input_strips" and (not scene.inpaint_selected_strip or image_model_card == "yuvraj108c/FLUX.1-Kontext-dev"):
                        col = col.column(heading="Use", align=True)
                        col.prop(addon_prefs, "use_strip_data", text=" Name & Seed")
                        if type == "movie" and os_platform != "Darwin" and (
                            movie_model_card == "lzyvegetable/FLUX.1-schnell"
                            or movie_model_card == "ChuckMcSneed/FLUX.1-dev"
                        ):
                            pass
                        else:
                            col.prop(context.scene, "image_power", text="Strip Power")

                    if (
                        bpy.context.scene.sequence_editor is not None
                        and (plugin is None or plugin.supports_inpaint)
                    ):
                        if input == "input_strips" and type == "image":
                            row = col.row(align=True)
                            row.prop_search(
                                scene,
                                "inpaint_selected_strip",
                                scene.sequence_editor,
                                "strips",
                                text="Inpaint Mask",
                                icon="SEQ_STRIP_DUPLICATE",
                            )
                            row.operator("sequencer.strip_picker", text="", icon="EYEDROPPER").action = "inpaint_select"

            if image_model_card == "yuvraj108c/FLUX.1-Kontext-dev" and type == "image":
                row = col.row(align=True)
                row.prop_search(
                    scene,
                    "kontext_strip_1",
                    scene.sequence_editor,
                    "strips",
                    text="Reference Image",
                    icon="FILE_IMAGE",
                )
                row.operator("sequencer.strip_picker", text="", icon="EYEDROPPER").action = "kontext_select1"

            if _has(UISection.POSE_TOGGLE):
                col = col.column(heading="Read as", align=True)
                col.prop(context.scene, "openpose_use_bones", text="OpenPose Rig Image")
            if _has(UISection.SCRIBBLE_TOGGLE):
                col = col.column(heading="Read as", align=True)
                col.prop(context.scene, "use_scribble_image", text="Scribble Image")

            # IPAdapter.
            if _has(UISection.IP_ADAPTER) and type == "image":
                row = col.row(align=True)
                row.prop(scene, "ip_adapter_face_folder", text="Adapter Face")
                row.operator(
                    "ip_adapter_face.file_browser", text="", icon="FILE_FOLDER"
                )

                row = col.row(align=True)
                row.prop(scene, "ip_adapter_style_folder", text="Adapter Style")
                row.operator(
                    "ip_adapter_style.file_browser", text="", icon="FILE_FOLDER"
                )

            # Prompts
            if plugin is None or plugin.UI_SECTIONS:
                col = layout.column(align=True)
                col = col.box()
                col = col.column(align=True)
                col.use_property_split = True
                col.use_property_decorate = False
            if _has(UISection.PROMPT):
                col.use_property_split = False
                col.use_property_decorate = False
                col.prop(context.scene, "generate_movie_prompt", text="", icon="ADD")
                if _has(UISection.NEG_PROMPT):
                    col.prop(
                        context.scene,
                        "generate_movie_negative_prompt",
                        text="",
                        icon="REMOVE",
                    )
                layout = col.column()
                col = layout.column(align=True)
                col.use_property_split = True
                col.use_property_decorate = False
                if type != "audio":
                    col.prop(context.scene, "generatorai_styles", text="Style")
            layout = col.column()
            if _has(UISection.RESOLUTION):
                col = layout.column(align=True)
                col.prop(context.scene, "generate_movie_x", text="X")
                col.prop(context.scene, "generate_movie_y", text="Y")
            col = layout.column(align=True)
            if _has(UISection.FRAMES):
                col.prop(context.scene, "generate_movie_frames", text="Frames")
            if _has(UISection.AUDIO_DURATION):
                col.prop(context.scene, "audio_length_in_f", text="Frames")
            if type == "audio" and _has(UISection.AUDIO_REF):
                row = col.row(align=True)
                row.prop(context.scene, "audio_path", text="Speaker Ref.")
                row.operator(
                    "sequencer.open_audio_filebrowser", text="", icon="FILEBROWSER"
                )
            if type == "audio" and _has(UISection.TEXT_REF):
                row = col.row(align=True)
                row.prop(context.scene, "audio_text", text="Text Ref.")
            if type == "audio" and _has(UISection.CHAT_PARAMS):
                col.prop(context.scene, "chat_exaggeration")
                col.prop(context.scene, "chat_pace")
                col.prop(context.scene, "chat_temperature")

            if _has(UISection.STEPS) and not scene.use_lcm:
                col.prop(
                    context.scene,
                    "movie_num_inference_steps",
                    text="Quality Steps",
                )

            if _has(UISection.GUIDANCE) and not scene.use_lcm:
                if image_model_card == "Shitao/OmniGen-v1-diffusers" and type == "image":
                    col.prop(
                        context.scene, "img_guidance_scale", text="Image Power"
                    )
                col.prop(context.scene, "movie_num_guidance", text="Word Power")

            if _has(UISection.ILLUMINATION):
                col.prop(context.scene, "illumination_style", text="Relight Style")
                col.prop(context.scene, "light_direction", text="Direction")
            if type == "audio" and _has(UISection.MUSIC_PARAMS):
                col.prop(context.scene, "music_bpm", text="BPM")
                col.prop(context.scene, "music_key_scale", text="Key")
                col.prop(context.scene, "music_time_signature", text="Time Sig.")
                col.prop(context.scene, "music_lyrics", text="Lyrics")

            if _has(UISection.SEED):
                col = col.column(align=True)
                row = col.row(align=True)
                sub_row = row.row(align=True)
                row.prop(
                    context.scene, "movie_use_random", text="", icon="QUESTION"
                )
                sub_row.prop(context.scene, "movie_num_seed", text="Seed")
                sub_row.active = not context.scene.movie_use_random

            if type == "image" and (plugin is None or plugin.UI_SECTIONS):
                col = col.column(heading="Enhance", align=True)
                row = col.row()
                row.prop(context.scene, "refine_sd", text="Quality")
                sub_col = col.row()
                sub_col.active = context.scene.refine_sd

                if _has(UISection.ENHANCE):
                    row.prop(context.scene, "use_lcm", text="Speed")

                if image_model_card == "stabilityai/stable-diffusion-xl-base-1.0":
                    col = col.column(heading="Details", align=True)

                row = col.row()
                if image_model_card == "stabilityai/stable-diffusion-xl-base-1.0":
                    row.prop(context.scene, "adetailer", text="Faces")

                row.prop(context.scene, "aurasr", text="Upscale 4x")

            if type == "movie" and movie_model_card == "stable-diffusion-xl/frame2frame":
                col = layout.column(heading="Upscale", align=True)
                col.prop(context.scene, "aurasr", text="4x")

            # LoRA.
            if _has(UISection.LORA):
                layout = self.layout
                layout.use_property_split = True
                layout.use_property_decorate = False
                col = layout.column(align=True)
                col = col.box()
                col = col.column(align=True)
                col.use_property_split = False
                col.use_property_decorate = False

                # Folder selection and refresh button
                row = col.row(align=True)
                row.prop(scene, "lora_folder", text="LoRA")
                row.operator("lora.refresh_files", text="", icon="FILE_REFRESH")

                # Custom UIList
                lora_files = scene.lora_files
                list_len = len(lora_files)
                if list_len > 0:
                    col.template_list(
                        "LORABROWSER_UL_files",
                        "The_List",
                        scene,
                        "lora_files",
                        scene,
                        "lora_files_index",
                        rows=2,
                    )

        elif text_model_card == "ZuluVision/MoviiGen1.1_Prompt_Rewriter":
                col = layout.column(align=True)
                col = col.box()
                col = col.column(align=True)
                col.use_property_split = False
                col.use_property_decorate = False
                col.prop(context.scene, "generate_movie_prompt", text="", icon="ADD")

        # Output.
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.box()
        col = col.column(align=True)
        try:
            col.prop(context.scene, "generatorai_typeselect", text="Output")
        except:
            pass

        if type == "image":
            col.prop(addon_prefs, "image_model_card", text=" ")
            from ..models.base import InputSpec as _InputSpec
            if plugin is not None and _InputSpec.HF_TOKEN in plugin.INPUTS:
                row = col.row(align=True)
                row.prop(addon_prefs, "hugginface_token")
                row.operator(
                    "wm.url_open", text="", icon="URL"
                ).url = "https://huggingface.co/settings/tokens"

        if type == "movie":
            col.prop(addon_prefs, "movie_model_card", text=" ")
        if type == "audio":
            col.prop(addon_prefs, "audio_model_card", text=" ")
        if type == "text":
            col.prop(addon_prefs, "text_model_card", text=" ")
        if type != "text" and not (
            type == "movie" and "Hailuo/MiniMax/" in movie_model_card
        ):
            col = col.column()
            col.prop(context.scene, "movie_num_batch", text="Batch Count")

        # Generate.
        col = layout.column()
        col = col.box()
        if input == "input_strips":
            ed = scene.sequence_editor
            row = col.row(align=True)
            row.scale_y = 1.2
            row.operator("sequencer.text_to_generator", text="Generate from Strips")
        else:
            row = col.row(align=True)
            row.scale_y = 1.2
            if type == "movie":
                # Frame by Frame
                if movie_model_card == "stable-diffusion-xl/frame2frame":
                    row.operator(
                        "sequencer.text_to_generator", text="Generate from Strips"
                    )
                else:
                    row.operator("sequencer.generate_movie", text="Generate")
            if type == "image":
                row.operator("sequencer.generate_image", text="Generate")
            if type == "audio":
                row.operator("sequencer.generate_audio", text="Generate")
            if type == "text":
                row.operator("sequencer.generate_text", text="Generate")