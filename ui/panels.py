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
        col = layout.column(align=False)
        col.use_property_split = True
        col.use_property_decorate = False
        col = col.box()
        col = col.column()

        if scene.sequence_editor is None:
            scene.sequence_editor_create()

        # OmniGen
        if image_model_card == "Shitao/OmniGen-v1-diffusers" and type == "image":
            col.prop(context.scene, "omnigen_prompt_1", text="", icon="ADD")
            row = col.row(align=True)
            row.prop_search(
                scene,
                "omnigen_strip_1",
                scene.sequence_editor,
                "strips",
                text="",
                icon="FILE_IMAGE",
            )
            row.operator("sequencer.strip_picker", text="", icon="EYEDROPPER").action = "omni_select1"

            col.prop(context.scene, "omnigen_prompt_2", text="", icon="ADD")
            row = col.row(align=True)
            row.prop_search(
                scene,
                "omnigen_strip_2",
                scene.sequence_editor,
                "strips",
                text="",
                icon="FILE_IMAGE",
            )
            row.operator("sequencer.strip_picker", text="", icon="EYEDROPPER").action = "omni_select2"

            col.prop(context.scene, "omnigen_prompt_3", text="", icon="ADD")
            row = col.row(align=True)
            row.prop_search(
                scene,
                "omnigen_strip_3",
                scene.sequence_editor,
                "strips",
                text="",
                icon="FILE_IMAGE",
            )
            row.operator("sequencer.strip_picker", text="", icon="EYEDROPPER").action = "omni_select3"
        else:
            try:
                col.prop(context.scene, "input_strips", text="Input")
            except:
                pass

        # Qwen multi-image
        if image_model_card == "Qwen/Qwen-Image-Edit-2511" and type == "image":
            row = col.row(align=True)
            row.prop_search(
                scene,
                "qwen_strip_1",
                scene.sequence_editor,
                "strips",
                text="",
                icon="FILE_IMAGE",
            )
            row.operator("sequencer.strip_picker", text="", icon="EYEDROPPER").action = "qwen_select1"

            row = col.row(align=True)
            row.prop_search(
                scene,
                "qwen_strip_2",
                scene.sequence_editor,
                "strips",
                text="",
                icon="FILE_IMAGE",
            )
            row.operator("sequencer.strip_picker", text="", icon="EYEDROPPER").action = "qwen_select2"

            row = col.row(align=True)
            row.prop_search(
                scene,
                "qwen_strip_3",
                scene.sequence_editor,
                "strips",
                text="",
                icon="FILE_IMAGE",
            )
            row.operator("sequencer.strip_picker", text="", icon="EYEDROPPER").action = "qwen_select3"

        # Flux multi-image
        if image_model_card == "diffusers/FLUX.2-dev-bnb-4bit" and type == "image": # Keep "Qwen/Qwen-Image-Edit-2511" if it's the model card being checked
            # Loop through the currently visible strips
            for i in range(1, scene.flux_visible_strips + 1):
                row = col.row(align=True)
                row.prop_search(
                    scene,
                    f"flux_strip_{i}",
                    scene.sequence_editor,
                    "strips",
                    text="",
                    icon="FILE_IMAGE",
                )


                op = row.operator("sequencer.strip_picker", text="", icon="EYEDROPPER")
                op.action = f"flux_select{i}"

                # If this is the last visible strip and not all 9 are displayed, add the '+' button
                if i == scene.flux_visible_strips and scene.flux_visible_strips < 9:
                    # Only show the Hide/Clear button if there's more than one visible strip
                    if scene.flux_visible_strips > 1:
                        clear_op = row.operator("object.flux_hide_strip", text="", icon="REMOVE")
                        clear_op.strip_index = i # Pass the index of the strip to clear/hide
                    row.operator("object.flux_add_strip", text="", icon="ADD")

        if image_model_card == "kontext-community/relighting-kontext-dev-lora-v3" and type == "image":
            box = layout.box()
            box = box.column(align=True)
            box.use_property_split = True
            box.use_property_decorate = False
            box.prop(context.scene, "illumination_style", text="Relight Style")
            box.prop(context.scene, "light_direction", text="Direction")


        if type != "text":
            if type != "audio":
                if type == "movie" and "Hailuo/MiniMax/" in movie_model_card:
                    if movie_model_card == "Hailuo/MiniMax/subject2vid":
                        row = col.row(align=True)
                        row.prop_search(
                            scene,
                            "minimax_subject",
                            scene.sequence_editor,
                            "strips",
                            text="Subject",
                            icon="USER",
                        )
                        row.operator("sequencer.strip_picker", text="", icon="EYEDROPPER").action = "minimax_select"

                elif type == "movie" and movie_model_card == "lllyasviel/FramePackI2V_HY":
                    if input == "input_strips":
                        row = col.row(align=True)
                        row.prop_search(
                            scene,
                            "out_frame",
                            scene.sequence_editor,
                            "strips",
                            text="End Frame",
                            icon="RENDER_RESULT",
                        )
                        row.operator("sequencer.strip_picker", text="", icon="EYEDROPPER").action = "out_frame_select"

                elif (type == "movie") or (
                    type == "image"
                    and image_model_card != "xinsir/controlnet-openpose-sdxl-1.0"
                    and image_model_card != "xinsir/controlnet-scribble-sdxl-1.0"
                    and image_model_card != "ZhengPeng7/BiRefNet_HR"
                    and image_model_card != "Shitao/OmniGen-v1-diffusers"
                    and image_model_card != "Qwen/Qwen-Image-Edit-2511"
                    and image_model_card != "Runware/FLUX.1-Redux-dev"
                    and image_model_card != "fuliucansheng/FLUX.1-Canny-dev-diffusers-lora"
                    and image_model_card != "romanfratric234/FLUX.1-Depth-dev-lora"
                    #and image_model_card != "yuvraj108c/FLUX.1-Kontext-dev"
                    and image_model_card != "kontext-community/relighting-kontext-dev-lora-v3"
                ):
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
                        and image_model_card
                        != "diffusers/controlnet-canny-sdxl-1.0-small"
                        and image_model_card != "Runware/BFL-FLUX.2-klein-base-4B"
                        and image_model_card != "black-forest-labs/FLUX.2-klein-9b-kv"
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

            if (
                image_model_card == "xinsir/controlnet-openpose-sdxl-1.0"
                and type == "image"
            ):
                col = col.column(heading="Read as", align=True)
                col.prop(context.scene, "openpose_use_bones", text="OpenPose Rig Image")
            if (
                image_model_card == "xinsir/controlnet-scribble-sdxl-1.0"
                and type == "image"
            ):
                col = col.column(heading="Read as", align=True)
                col.prop(context.scene, "use_scribble_image", text="Scribble Image")

            # IPAdapter.
            if (
                image_model_card == "stabilityai/stable-diffusion-xl-base-1.0"
                # or image_model_card == "xinsir/controlnet-openpose-sdxl-1.0"
                # or image_model_card == "diffusers/controlnet-canny-sdxl-1.0-small"
                # or image_model_card == "xinsir/controlnet-scribble-sdxl-1.0"
            ) and type == "image":
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
            if not (type == "image" and image_model_card == "ZhengPeng7/BiRefNet_HR"):
                col = layout.column(align=True)
                col = col.box()
                col = col.column(align=True)
                col.use_property_split = True
                col.use_property_decorate = False
            if (
                (type == "image" and image_model_card == "ZhengPeng7/BiRefNet_HR")
                or (
                    image_model_card == "Shitao/OmniGen-v1-diffusers"
                    and type == "image"
                )
                or (type == "image" and image_model_card == "Runware/FLUX.1-Redux-dev")
            ):
                pass
            else:
                col.use_property_split = False
                col.use_property_decorate = False
                col.prop(context.scene, "generate_movie_prompt", text="", icon="ADD")
                if (
                    (
                        type == "audio"
                        and audio_model_card == "tintwotin/Foundation-1-Diffusers"
                    )
                    or (
                        type == "image"
                        and image_model_card == "lzyvegetable/FLUX.1-schnell"
                    )
                    or (
                        type == "image"
                        and image_model_card == "ChuckMcSneed/FLUX.1-dev"
                    )
                    or (
                        type == "image"
                        and image_model_card == "Runware/BFL-FLUX.2-klein-base-4B"
                    )
                    or (
                        type == "image"
                        and image_model_card == "black-forest-labs/FLUX.2-klein-9b-kv"
                    )
                    or (
                        type == "image"
                        and image_model_card == "yuvraj108c/FLUX.1-Kontext-dev"
                    )
                    or (type == "image" and image_model_card == "kontext-community/relighting-kontext-dev-lora-v3")
                    or (
                        type == "image"
                        and image_model_card
                        == "fuliucansheng/FLUX.1-Canny-dev-diffusers-lora"
                    )
                    or (
                        type == "image"
                        and image_model_card
                        == "romanfratric234/FLUX.1-Depth-dev-lora"
                    )
                    or (
                        type == "image"
                        and image_model_card == "Runware/FLUX.1-Redux-dev"
                    )
                    or (
                        type == "audio"
                        and (audio_model_card == "WhisperSpeech" or audio_model_card == "SWivid/F5-TTS" or audio_model_card == "Chatterbox" or audio_model_card == "ChatterboxTurbo" or audio_model_card == "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
                    )
                    or (type == "movie" and "Hailuo/MiniMax/" in movie_model_card)
                ):
                    pass
                elif type == "audio" and (
                    audio_model_card == "parler-tts/parler-tts-large-v1"
                    or audio_model_card == "parler-tts/parler-tts-mini-v1"
                ):
                    layout = col.column()
                    col = layout.column(align=True)
                    col.use_property_split = True
                    col.use_property_decorate = False
                    col.prop(
                        context.scene,
                        "parler_direction_prompt",
                        text="Instruction",
                    )
                else:
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
                if type != "audio" and not (
                    type == "image" and image_model_card == "ZhengPeng7/BiRefNet_HR"
                ):
                    col.prop(context.scene, "generatorai_styles", text="Style")
            if type == "movie" and "Hailuo/MiniMax/" in movie_model_card:
                pass
            else:
                layout = col.column()
                if (
                    type == "movie"
                    or type == "image"
                    and not (
                        type == "image" and image_model_card == "ZhengPeng7/BiRefNet_HR"
                    )
                ):
                    col = layout.column(align=True)
                    col.prop(context.scene, "generate_movie_x", text="X")
                    col.prop(context.scene, "generate_movie_y", text="Y")
                col = layout.column(align=True)
                if (
                    type == "movie"
                    or type == "image"
                    and not (
                        type == "image" and image_model_card == "ZhengPeng7/BiRefNet_HR"
                    )
                ):
                    col.prop(context.scene, "generate_movie_frames", text="Frames")
                if (
                    type == "audio"
                    and audio_model_card != "WhisperSpeech"
                    and audio_model_card != "SWivid/F5-TTS"
                    and audio_model_card != "Chatterbox"
                    and audio_model_card != "ChatterboxTurbo"
                    and audio_model_card != "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
                    and audio_model_card != "parler-tts/parler-tts-large-v1"
                    and audio_model_card != "parler-tts/parler-tts-mini-v1"
                ):
                    col.prop(context.scene, "audio_length_in_f", text="Frames")
                if type == "audio" and (audio_model_card == "WhisperSpeech" or audio_model_card == "SWivid/F5-TTS" or audio_model_card == "Chatterbox" or audio_model_card == "ChatterboxTurbo" or audio_model_card == "Qwen/Qwen3-TTS-12Hz-1.7B-Base"):
                    row = col.row(align=True)
                    row.prop(context.scene, "audio_path", text="Speaker Ref.")
                    row.operator(
                        "sequencer.open_audio_filebrowser", text="", icon="FILEBROWSER"
                    )
                    if audio_model_card == "Qwen/Qwen3-TTS-12Hz-1.7B-Base":
                        row = col.row(align=True)
                        row.prop(context.scene, "audio_text", text="Text Ref.")
                    if audio_model_card == "Chatterbox" or audio_model_card == "ChatterboxTurbo":
                        col.prop(context.scene, "chat_exaggeration")
                        col.prop(context.scene, "chat_pace")
                        col.prop(context.scene, "chat_temperature")
                    else:
                        if audio_model_card == "WhisperSpeech":
                            col.prop(context.scene, "audio_speed", text="Speed")
                        else:
                            col.prop(context.scene, "audio_speed_tts", text="Speed")

                if type == "audio" and (audio_model_card == "WhisperSpeech" or audio_model_card == "Chatterbox" or audio_model_card == "Chatterbox" or audio_model_card == "Qwen/Qwen3-TTS-12Hz-1.7B-Base"):
                    pass

                elif type == "audio" and (
                    addon_prefs.audio_model_card
                    == "tintwotin/Foundation-1-Diffusers"
                ):
                    col.prop(
                        context.scene, "movie_num_inference_steps", text="Quality Steps"
                    )
                else:
                    if (
                        (
                            type == "audio"
                            and (
                                audio_model_card == "parler-tts/parler-tts-mini-v1"
                                or audio_model_card == "parler-tts/parler-tts-large-v1"
                            )
                            or (
                                type == "image"
                                and image_model_card == "ZhengPeng7/BiRefNet_HR"
                            )
                            or (
                                type == "movie"
                                and (movie_model_card == "Wan-AI/Wan2.2-I2V-A14B-Diffusers")
                            )
                            or (
                                type == "movie"
                                and (movie_model_card == "Wan-AI/Wan2.2-T2V-A14B-Diffusers")
                            )
                            or (
                                type == "movie"
                                and (movie_model_card == "Lightricks/LTX-2")
                            )
                            or (
                                type == "movie"
                                and (movie_model_card == "LTX-2 Multi-Input File")
                            )                            
                        )
                    ):
                        pass
                    else:
                        col.prop(
                            context.scene,
                            "movie_num_inference_steps",
                            text="Quality Steps",
                        )

                    if (
                        (
                            type == "image"
                            and image_model_card == "lzyvegetable/FLUX.1-schnell"
                        )
                        or (
                            type == "image"
                            and image_model_card == "ZhengPeng7/BiRefNet_HR"
                        )
                        or (
                            type == "audio"
                            and (
                                audio_model_card == "parler-tts/parler-tts-mini-v1"
                                or audio_model_card == "parler-tts/parler-tts-large-v1"
                                or audio_model_card == "SWivid/F5-TTS"
                                or audio_model_card == "Chatterbox"
                                or audio_model_card == "ChatterboxTurbo"
                                or audio_model_card == "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
                            )
                        )
                        or (
                            scene.use_lcm
                        )
                        or (
                            type == "movie"
                            and (movie_model_card == "Wan-AI/Wan2.2-I2V-A14B-Diffusers")
                        )
                        or (
                            type == "movie"
                            and (movie_model_card == "Wan-AI/Wan2.2-T2V-A14B-Diffusers")
                        ) 
                        or (
                            type == "movie"
                            and (movie_model_card == "Lightricks/LTX-2")
                        ) 
                        or (
                            type == "movie"
                            and (movie_model_card == "LTX-2 Multi-Input File")
                        )                        
                    ):
                        pass
                    else:
                        if (
                            image_model_card == "Shitao/OmniGen-v1-diffusers"
                            and type == "image"
                        ) or (
                            image_model_card == "Qwen/Qwen-Image-Edit-2511"
                            and type == "image"
                        ):
                            col.prop(
                                context.scene, "img_guidance_scale", text="Image Power"
                            )
                        col.prop(context.scene, "movie_num_guidance", text="Word Power")

                if not (
                    type == "image" and image_model_card == "ZhengPeng7/BiRefNet_HR"
                ):
                    col = col.column(align=True)
                    row = col.row(align=True)
                    sub_row = row.row(align=True)
                    row.prop(
                        context.scene, "movie_use_random", text="", icon="QUESTION"
                    )
                    sub_row.prop(context.scene, "movie_num_seed", text="Seed")
                    sub_row.active = not context.scene.movie_use_random

                if type == "image" and not (
                    type == "image" and image_model_card == "ZhengPeng7/BiRefNet_HR"
                ):
                    col = col.column(heading="Enhance", align=True)
                    row = col.row()
                    row.prop(context.scene, "refine_sd", text="Quality")
                    sub_col = col.row()
                    sub_col.active = context.scene.refine_sd

                    if (
                        (
                            type == "image"
                            and image_model_card
                            == "stabilityai/stable-diffusion-xl-base-1.0"
                        )
                        or (
                            type == "image"
                            and image_model_card
                            == "xinsir/controlnet-openpose-sdxl-1.0"
                        )
                        or (
                            type == "image"
                            and image_model_card
                            == "xinsir/controlnet-scribble-sdxl-1.0"
                        )
                        or (
                            type == "image"
                            and image_model_card
                            == "diffusers/controlnet-canny-sdxl-1.0-small"
                        )
                        or (
                            type == "image"
                            and image_model_card == "segmind/Segmind-Vega"
                        )
                    ):
                        row.prop(context.scene, "use_lcm", text="Speed")

                    # ADetailer
                    if image_model_card == "stabilityai/stable-diffusion-xl-base-1.0":
                        col = col.column(heading="Details", align=True)

                    row = col.row()
                    if image_model_card == "stabilityai/stable-diffusion-xl-base-1.0":
                        row.prop(context.scene, "adetailer", text="Faces")

                    # AuraSR
                    # col = col.column(heading="Upscale", align=True)
                    row.prop(context.scene, "aurasr", text="Upscale 4x")
                    # row = col.row()
                if (type == "movie") and (
                    movie_model_card == "stabilityai/stable-diffusion-xl-base-1.0"
                ):
                    col = layout.column(heading="Upscale", align=True)
                    col.prop(context.scene, "aurasr", text="4x")
                if type == "audio" and audio_model_card == "SWivid/F5-TTS":
                    col = layout.column(heading="Remove", align=True)
                    col.prop(context.scene, "remove_silence", text="Silence")

            # LoRA.
            if (
                (
                    image_model_card == "stabilityai/stable-diffusion-xl-base-1.0"
                    or image_model_card == "xinsir/controlnet-openpose-sdxl-1.0"
                    or image_model_card == "diffusers/controlnet-canny-sdxl-1.0-small"
                    or image_model_card == "xinsir/controlnet-scribble-sdxl-1.0"
                    or image_model_card == "lzyvegetable/FLUX.1-schnell"
                    or image_model_card == "yuvraj108c/FLUX.1-Kontext-dev"
                    or image_model_card == "lodestones/Chroma"
                    or image_model_card == "Tongyi-MAI/Z-Image"
                    or image_model_card == "Tongyi-MAI/Z-Image-Turbo"
                    or image_model_card == "Qwen/Qwen-Image-2512"
                    or image_model_card == "Qwen/Qwen-Image-Edit-2511"
                    or image_model_card == "ChuckMcSneed/FLUX.1-dev"
                    or image_model_card == "Runware/BFL-FLUX.2-klein-base-4B"
                    or image_model_card == "black-forest-labs/FLUX.2-klein-9b-kv"
                    or image_model_card == "diffusers/FLUX.2-dev-bnb-4bit"
                    or image_model_card == "fuliucansheng/FLUX.1-Canny-dev-diffusers-lora"
                    or image_model_card == "romanfratric234/FLUX.1-Depth-dev-lora"
                    or image_model_card == "Runware/FLUX.1-Redux-dev"
                    or image_model_card == "diffusers/FLUX.2-dev-bnb-4bit"
                )
                and type == "image"
            ) or ((
                type == "movie")
                and (movie_model_card == "stabilityai/stable-diffusion-xl-base-1.0"
                or (movie_model_card == "hunyuanvideo-community/HunyuanVideo")
                or (movie_model_card == "Wan-AI/Wan2.2-I2V-A14B-Diffusers")
                or (movie_model_card == "Wan-AI/Wan2.2-T2V-A14B-Diffusers")
                or (movie_model_card == "Wan-AI/Wan2.1-VACE-1.3B-diffusers")
                or (movie_model_card == "Lightricks/LTX-2")
                or (movie_model_card == "LTX-2 Multi-Input File")
            )):
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
            if (
                addon_prefs.image_model_card
                == "adamo1139/stable-diffusion-3.5-large-ungated"
                or addon_prefs.image_model_card
                == "diffusers/FLUX.2-dev-bnb-4bit"
            ):
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
                if movie_model_card == "stabilityai/stable-diffusion-xl-base-1.0":
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