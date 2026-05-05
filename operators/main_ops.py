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
from ..ui.panels import *

_pallaidium_movie_model_cache = {
    "pipe": None,
    "refiner": None,
    "last_model_card": None,
}

_pallaidium_model_cache = {
    "pipe": None,
    "converter": None,
    "refiner": None,
    "last_model_card": None,
}

_pallaidium_audio_model_cache = {
    "pipe": None,
    "vocoder": None,
    "model": None,
    "feature_extractor": None,
    "last_model_card": None,
}

_pallaidium_text_model_cache = {
    "model": None,
    "processor": None,
    "tokenizer": None,
    "last_model_card": None,
}

class GENERATOR_OT_export_requirements(Operator, ExportHelper):
    bl_idname = "sequencer.export_requirements"
    bl_label = "Export requirements.txt"
    bl_options = {'REGISTER'}
    filename_ext = ".txt"
    filter_glob: bpy.props.StringProperty(default="requirements.txt", options={'HIDDEN'}, maxlen=255)

    def execute(self, context):
        dists = importlib.metadata.distributions()
        lines = []
        for dist in dists:
            try: lines.append(f"{dist.metadata['Name']}=={dist.version}")
            except: pass
        lines.sort()
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
        except Exception: return {'CANCELLED'}
        return {'FINISHED'}

class GENERATOR_OT_install(Operator):
    bl_idname = "sequencer.install_generator"
    bl_label = "Install Dependencies"
    bl_options = {"REGISTER", "UNDO"}
    force_reinstall: bpy.props.BoolProperty(name="Force Reinstall", default=False)

    def execute(self, context):
        try:
            from . import console_utils 
            console_utils.show_system_console(True)
            console_utils.set_system_console_topmost(True)
        except ImportError: pass

        pybin = python_exec()
        addon_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        local_req_file = os.path.join(addon_dir, "requirements.txt")
        mgr = DependencyManager()

        # BATCH SIZE for all phases
        BATCH_SIZE = 5
        
        try: subprocess.check_call([pybin, "-m", "pip", "install", "--upgrade", "pip"])
        except: pass

        def process_in_batches(lines, phase_name, install_func):
            safe_lines = BlenderInternalManager.filter_list(lines)
            if not self.force_reinstall:
                final_lines = SmartSkipManager.filter_existing(safe_lines)
            else:
                final_lines = safe_lines

            total_items = len(final_lines)
            if total_items == 0:
                print(f"--- {phase_name}: All up to date. ---")
                return True

            print(f"--- {phase_name}: Installing {total_items} packages in batches of {BATCH_SIZE} ---")
            
            for i in range(0, total_items, BATCH_SIZE):
                batch_lines = final_lines[i : i + BATCH_SIZE]
                current_batch_num = (i // BATCH_SIZE) + 1
                total_batches = (total_items + BATCH_SIZE - 1) // BATCH_SIZE
                
                # Log batch content
                batch_names = [SmartSkipManager.extract_package_name(x) or x for x in batch_lines]
                print(f"   [Batch {current_batch_num}/{total_batches}] Installing: {', '.join(batch_names)}")
                
                temp_req = os.path.join(addon_dir, f"temp_{phase_name.replace(' ', '_')}_batch_{current_batch_num}.txt")
                write_requirements_file(temp_req, batch_lines)
                
                success = install_func(temp_req)
                if os.path.exists(temp_req): os.remove(temp_req)
                if not success:
                    print(f"!!! Error installing Batch {current_batch_num} !!!")
                    return False
            return True

        # -----------------------------------------------------------
        # Step 1: Base Requirements (Binary Only)
        # -----------------------------------------------------------
        if os.path.exists(local_req_file):
            with open(local_req_file, 'r') as f:
                raw_lines = f.read().splitlines()
            
            if not process_in_batches(raw_lines, "Base Binaries", install_requirements_binary_only):
                 self.report({"ERROR"}, "Failed to install base binaries.")
                 return {"CANCELLED"}

        # -----------------------------------------------------------
        # Step 2: Source Libs, Torch, Git (Allow Source Build)
        # -----------------------------------------------------------
        phases = [
            ("Source_Libs", mgr.get_phase_1_5_source_libs()),
            ("Torch", mgr.get_phase_2_torch()),
            ("Git_Extensions", mgr.get_phase_3_git_and_extensions()),
        ]
        for phase_name, lines in phases:
            torch_installed = any("torch" in x for x in lines) and not self.force_reinstall and importlib.util.find_spec("torch")
            if "Torch" in phase_name and mgr.os_platform == "Windows" and not torch_installed:
                 clean_lines = SmartSkipManager.filter_existing(lines)
                 if clean_lines:
                     print("Ensuring clean Torch installation...")
                     subprocess.call([pybin, "-m", "pip", "uninstall", "-y", "torch", "torchvision", "torchaudio"])#, "xformers"

            if not process_in_batches(lines, phase_name, install_requirements_allow_source):
                self.report({"ERROR"}, f"Failed to install: {phase_name}")
                return {"CANCELLED"}

        self.report({"INFO"}, "Installation check finished.")
        return {"FINISHED"}

class GENERATOR_OT_uninstall(Operator):
    bl_idname = "sequencer.uninstall_generator"
    bl_label = "Uninstall Dependencies"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        pybin = python_exec()
        mgr = DependencyManager()
        addon_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        local_req_file = os.path.join(addon_dir, "requirements.txt")
        
        all_targets = set()
        
        if os.path.exists(local_req_file):
            with open(local_req_file, 'r') as f:
                lines = f.read().splitlines()
                for line in lines:
                    name = SmartSkipManager.extract_package_name(line)
                    if name: all_targets.add(name)

        script_phases = (
            mgr.get_phase_1_5_source_libs() + 
            mgr.get_phase_2_torch() + 
            mgr.get_phase_3_git_and_extensions()
        )
        for line in script_phases:
            name = SmartSkipManager.extract_package_name(line)
            if name: all_targets.add(name)

        safe_uninstall_list = []
        for pkg in all_targets:
            if not BlenderInternalManager.is_protected(pkg):
                safe_uninstall_list.append(pkg)

        uninstall_file = os.path.join(addon_dir, "temp_uninstall_list.txt")
        write_requirements_file(uninstall_file, safe_uninstall_list)

        print(f"Uninstalling {len(safe_uninstall_list)} packages...")
        try:
            subprocess.call([pybin, "-m", "pip", "uninstall", "-y", "-r", uninstall_file])
        except Exception as e:
            print(f"Error: {e}")

        if os.path.exists(uninstall_file): os.remove(uninstall_file)
        self.report({"INFO"}, "Uninstallation finished. Please, restart Blender.")
        return {"FINISHED"}

class GENERATOR_OT_sound_notification(Operator):
    """Test your notification settings"""

    bl_idname = "renderreminder.pallaidium_play_notification"
    bl_label = "Test Notification"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        preferences = context.preferences
        addon_prefs = preferences.addons[ADDON_ID].preferences
        if addon_prefs.playsound:
            device = aud.Device()

            def coinSound():
                sound = aud.Sound("")
                handle = device.play(
                    sound.triangle(1000)
                    .highpass(20)
                    .lowpass(2000)
                    .ADSR(0, 0.5, 1, 0)
                    .fadeout(0.1, 0.1)
                    .limit(0, 1)
                )
                handle = device.play(
                    sound.triangle(1500)
                    .highpass(20)
                    .lowpass(2000)
                    .ADSR(0, 0.5, 1, 0)
                    .fadeout(0.2, 0.2)
                    .delay(0.1)
                    .limit(0, 1)
                )

            def ding():
                sound = aud.Sound("")
                handle = device.play(
                    sound.triangle(3000)
                    .highpass(20)
                    .lowpass(1000)
                    .ADSR(0, 0.5, 1, 0)
                    .fadeout(0, 1)
                    .limit(0, 1)
                )

            if addon_prefs.soundselect == "ding":
                ding()
            if addon_prefs.soundselect == "coin":
                coinSound()
            if addon_prefs.soundselect == "user":
                file = str(addon_prefs.usersound)
                if os.path.isfile(file):
                    sound = aud.Sound(file)
                    handle = device.play(sound)
        return {"FINISHED"}

class LORA_OT_RefreshFiles(Operator):
    bl_idname = "lora.refresh_files"
    bl_label = "Refresh Files"

    def execute(self, context):
        scene = context.scene
        directory = bpy.path.abspath(scene.lora_folder)
        lora_files = scene.lora_files
        lora_files.clear()
        if not directory:
            self.report({"ERROR"}, "No folder selected")
            return {"CANCELLED"}
        #        lora_files = scene.lora_files
        #        lora_files.clear()
        for filename in os.listdir(directory):
            if filename.endswith(".safetensors"):
                file_item = lora_files.add()
                file_item.name = filename.replace(".safetensors", "")
                file_item.enabled = False
                file_item.weight_value = 1.0
        return {"FINISHED"}

class SEQUENCER_OT_generate_movie(Operator):
    """Generate Video"""

    bl_idname = "sequencer.generate_movie"
    bl_label = "Prompt"
    bl_description = "Convert text to video"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        global _pallaidium_movie_model_cache
        import random
        import os
        
        scene = context.scene

        #        if not scene.generate_movie_prompt:
        #            self.report({"INFO"}, "Text prompt in the Generative AI tab is empty!")
        #            return {"CANCELLED"}
        try:
            import torch
            from diffusers.utils import export_to_video
            from PIL import Image

            Image.MAX_IMAGE_PIXELS = None
            import numpy as np
        except ModuleNotFoundError as e:
            print("Dependencies needs to be installed in the add-on preferences. "+str(e.name))
            self.report(
                {"INFO"},
                "In the add-on preferences, install dependencies.",
            )
            return {"CANCELLED"}

        show_system_console(True)
        set_system_console_topmost(True)
        seq_editor = scene.sequence_editor
        input = scene.input_strips

        if not seq_editor:
            scene.sequence_editor_create()

        # clear_cuda_cache() # Moved to conditional logic

        current_frame = scene.frame_current
        prompt = style_prompt(scene.generate_movie_prompt)[0]
        negative_prompt = (
            scene.generate_movie_negative_prompt
            + ", "
            + style_prompt(scene.generate_movie_prompt)[1]
            + ", nsfw, nude, nudity"
        )
        movie_x = scene.generate_movie_x
        movie_y = scene.generate_movie_y
        x = scene.generate_movie_x = closest_divisible_32(movie_x)
        y = scene.generate_movie_y = closest_divisible_32(movie_y)
        old_duration = duration = scene.generate_movie_frames
        movie_num_inference_steps = scene.movie_num_inference_steps
        movie_num_guidance = scene.movie_num_guidance
        input = scene.input_strips
        preferences = context.preferences
        addon_prefs = preferences.addons[ADDON_ID].preferences
        local_files_only = addon_prefs.local_files_only
        movie_model_card = addon_prefs.movie_model_card
        image_model_card = addon_prefs.image_model_card
        
        # --- CACHE RETRIEVAL ---
        pipe = _pallaidium_movie_model_cache["pipe"]
        refiner = _pallaidium_movie_model_cache["refiner"]
        
        should_load = context.scene.get("ai_load_state", True)
        should_unload = context.scene.get("ai_unload_state", True)

        # Force load if cache is empty or model changed
        if pipe is None and not should_load:
             if movie_model_card not in ["Hailuo/MiniMax/txt2vid", "Hailuo/MiniMax/img2vid", "Hailuo/MiniMax/subject2vid"]:
                print("Model cache missing. Forcing load.")
                should_load = True
        
        if _pallaidium_movie_model_cache["last_model_card"] != movie_model_card:
            print("Model card changed. Forcing load.")
            should_load = True

        def ensure_skyreel(prompt: str) -> str:
            if not prompt.startswith("FPS-24,"):
                return "FPS-24, " + prompt
            return prompt

        # LOADING MODELS
        if should_load:
            print("Model:  " + movie_model_card)
            pipe = None
            refiner = None
            clear_cuda_cache()

            # Models for refine imported image or movie
            if (
                (scene.movie_path or scene.image_path or scene.sound_path)
                and input == "input_strips"
                and movie_model_card != "THUDM/CogVideoX-5b"
                and movie_model_card != "THUDM/CogVideoX-2b"
                and movie_model_card != "Lightricks/LTX-Video"
                and movie_model_card != "rootonchair/LTX-2-19b-distilled"
                and movie_model_card != "LTX-2 Multi-Input File"
                and movie_model_card != "Lightricks/LTX-2"
                and movie_model_card != "hunyuanvideo-community/HunyuanVideo"
                and movie_model_card != "lllyasviel/FramePackI2V_HY"
                and movie_model_card != "Hailuo/MiniMax/txt2vid"
                and movie_model_card != "Hailuo/MiniMax/img2vid"
                and movie_model_card != "Hailuo/MiniMax/subject2vid"
                and movie_model_card != "Skywork/SkyReels-V1-Hunyuan-T2V"
                and movie_model_card != "Wan-AI/Wan2.2-I2V-A14B-Diffusers"
                and movie_model_card != "Wan-AI/Wan2.1-VACE-1.3B-diffusers"
            ) or movie_model_card == "stabilityai/stable-diffusion-xl-base-1.0":
                # Frame by Frame
                if (
                    movie_model_card == "stabilityai/stable-diffusion-xl-base-1.0"
                ):  # frame2frame
                    from diffusers import StableDiffusionXLImg2ImgPipeline, AutoencoderKL
                    from torchvision import transforms

                    enabled_items = None

                    lora_files = scene.lora_files
                    enabled_names = []
                    enabled_weights = []
                    # Check if there are any enabled items before loading
                    enabled_items = [item for item in lora_files if item.enabled]
                    vae = AutoencoderKL.from_pretrained(
                        "madebyollin/sdxl-vae-fp16-fix", torch_dtype=torch.float16
                    )
                    pipe = StableDiffusionXLImg2ImgPipeline.from_pretrained(
                        movie_model_card,
                        torch_dtype=torch.float16,
                        variant="fp16",
                        vae=vae,
                    )
                    pipe.watermark = NoWatermark()

                    scene = context.scene

                    if enabled_items:
                        for item in enabled_items:
                            enabled_names.append(
                                (clean_filename(item.name)).replace(".", "")
                            )
                            enabled_weights.append(item.weight_value)
                            pipe.load_lora_weights(
                                bpy.path.abspath(scene.lora_folder),
                                weight_name=item.name + ".safetensors",
                                adapter_name=((clean_filename(item.name)).replace(".", "")),
                            )
                        pipe.set_adapters(enabled_names, adapter_weights=enabled_weights)
                        print("Load LoRAs: " + " ".join(enabled_names))

                    if gfx_device == "mps":
                        pipe.to("mps")
                    elif low_vram():
                        pipe.enable_sequential_cpu_offload()
                        # pipe.vae.enable_slicing()
                        pipe.vae.enable_tiling()
                    else:
                        pipe.enable_model_cpu_offload()

                    refiner = pipe

            # Models for movie generation
            elif (
                movie_model_card != "Hailuo/MiniMax/txt2vid"
                and movie_model_card != "Hailuo/MiniMax/img2vid"
                and movie_model_card != "Hailuo/MiniMax/subject2vid"
            ):
                # CogVideoX
                if (
                    movie_model_card == "THUDM/CogVideoX-5b"
                    or movie_model_card == "THUDM/CogVideoX-2b"
                ):
                    # vid2vid
                    if scene.movie_path and input == "input_strips":
                        from diffusers.utils import load_video
                        from diffusers import (
                            CogVideoXDPMScheduler,
                            CogVideoXVideoToVideoPipeline,
                        )

                        pipe = CogVideoXVideoToVideoPipeline.from_pretrained(
                            movie_model_card, torch_dtype=torch.bfloat16
                        )
                        pipe.scheduler = CogVideoXDPMScheduler.from_config(
                            pipe.scheduler.config
                        )

                    # img2vid
                    elif scene.image_path and input == "input_strips":
                        print("Load: Image to video (CogVideoX)")
                        from diffusers import CogVideoXImageToVideoPipeline
                        from diffusers.utils import load_image

                        pipe = CogVideoXImageToVideoPipeline.from_pretrained(
                            "THUDM/CogVideoX-5b-I2V", torch_dtype=torch.bfloat16
                        )

                    # txt2vid
                    else:
                        print("Load: text to video (CogVideoX)")
                        from diffusers import CogVideoXPipeline

                        pipe = CogVideoXPipeline.from_pretrained(
                            movie_model_card,
                            torch_dtype=torch.float16,
                        )

                    if gfx_device == "mps":
                        pipe.to("mps")
                    elif low_vram():
                        pipe.enable_sequential_cpu_offload()
                        # pipe.vae.enable_slicing()
                        pipe.vae.enable_tiling()
                    else:
                        pipe.enable_model_cpu_offload()

                    scene.generate_movie_x = 720
                    scene.generate_movie_y = 480

                # LTX
                elif movie_model_card == "Lightricks/LTX-Video":
                    from transformers import T5EncoderModel, T5Tokenizer
                    from diffusers import AutoencoderKLLTXVideo
                    from diffusers import LTXPipeline, LTXVideoTransformer3DModel#, GGUFQuantizationConfig
                    from diffusers import LTXConditionPipeline, LTXLatentUpsamplePipeline, BitsAndBytesConfig, LTXVideoTransformer3DModel
                    print("LTX Video: Load Model")

                    import torch
                    from diffusers import LTXConditionPipeline, LTXLatentUpsamplePipeline, BitsAndBytesConfig, LTXVideoTransformer3DModel
                    from diffusers.pipelines.ltx.pipeline_ltx_condition import LTXVideoCondition
                    from diffusers.utils import export_to_video, load_video, load_image

                    nf4_config = BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_compute_dtype=torch.bfloat16,
                    )

                    transformer = LTXVideoTransformer3DModel.from_pretrained(
                        "a-r-r-o-w/LTX-Video-0.9.7-diffusers",
                        quantization_config=nf4_config,
                        torch_dtype=torch.bfloat16,
                        subfolder="transformer",
                    )

                    pipe = LTXConditionPipeline.from_pretrained("a-r-r-o-w/LTX-Video-0.9.7-diffusers", transformer=transformer, torch_dtype=torch.bfloat16)

                    if gfx_device == "mps":
                        pipe.to("mps")
                    elif low_vram():
                        pipe.vae.enable_tiling()
                        pipe.enable_model_cpu_offload()
                    else:
                        pipe.vae.enable_tiling()
                        pipe.enable_model_cpu_offload()

                # LTX-2
                elif movie_model_card == "rootonchair/LTX-2-19b-distilled":
                    print("LTX-2 Video: Load Model")

                    import torch, os
                    from diffusers.pipelines.ltx2 import LTX2ImageToVideoPipeline, LTX2LatentUpsamplePipeline
                    from diffusers.pipelines.ltx2.latent_upsampler import LTX2LatentUpsamplerModel
                    from diffusers.pipelines.ltx2.utils import DISTILLED_SIGMA_VALUES, STAGE_2_DISTILLED_SIGMA_VALUES
                    from diffusers.pipelines.ltx2.export_utils import encode_video
                    from diffusers import BitsAndBytesConfig
                    from diffusers.utils import export_to_video, load_video, load_image

                    from diffusers.utils import load_image

                    pipe = LTX2ImageToVideoPipeline.from_pretrained(movie_model_card, torch_dtype=torch.bfloat16)
                    
                    if gfx_device == "mps":
                        pipe.to("mps")
                    elif low_vram():
                        pipe.vae.enable_tiling()
                        pipe.enable_sequential_cpu_offload(device=gfx_device)
                    else:
                        pipe.vae.enable_tiling()
                        pipe.enable_sequential_cpu_offload(device=gfx_device)
                    
#                    latent_upsampler = LTX2LatentUpsamplerModel.from_pretrained(
#                        movie_model_card,
#                        subfolder="latent_upsampler",
#                        torch_dtype=torch.bfloat16,
#                    )

                elif movie_model_card == "Lightricks/LTX-2":
                    pass
                elif movie_model_card == "LTX-2 Multi-Input File":
                    pass
                
                # HunyuanVideo
                elif movie_model_card == "hunyuanvideo-community/HunyuanVideo":
                    # vid2vid
                    if scene.movie_path and input == "input_strips":
                        print("HunyuanVideo doesn't support vid2vid! Using img2vid instead...")

                    # img2vid
                    if (scene.image_path or scene.movie_path) and input == "input_strips":
                        print("HunyuanVideo: Load Image to Video Model")
                        from diffusers import HunyuanVideoImageToVideoPipeline
                        model_id = "hunyuanvideo-community/HunyuanVideo-I2V"
                        if low_vram():
                            transformer_path = f"https://huggingface.co/city96/HunyuanVideo-I2V-gguf/blob/main/hunyuan-video-i2v-720p-Q4_K_S.gguf"
                        else:
                            transformer_path = f"https://huggingface.co/city96/HunyuanVideo-I2V-gguf/blob/main/hunyuan-video-i2v-720p-Q4_K_S.gguf"
                            #transformer_path = f"https://huggingface.co/city96/HunyuanVideo-I2V-gguf/blob/main/hunyuan-video-i2v-720p-Q5_K_S.gguf"
                    # prompt to video
                    else:
                        print("HunyuanVideo: Load Prompt to Video Model")
                        model_id = "hunyuanvideo-community/HunyuanVideo"
                        from diffusers import HunyuanVideoPipeline
                        if low_vram():
                            transformer_path = f"https://huggingface.co/city96/HunyuanVideo-gguf/blob/main/hunyuan-video-t2v-720p-Q3_K_S.gguf"
                        else:
                            transformer_path = f"https://huggingface.co/city96/HunyuanVideo-gguf/blob/main/hunyuan-video-t2v-720p-Q4_K_S.gguf"

                    enabled_items = None
                    lora_files = scene.lora_files
                    enabled_names = []
                    enabled_weights = []
                    # Check if there are any enabled items before loading
                    enabled_items = [item for item in lora_files if item.enabled]

                    from diffusers.models import HunyuanVideoTransformer3DModel
                    from diffusers.utils import export_to_video
                    from diffusers import BitsAndBytesConfig
                    from transformers import LlamaModel, CLIPTextModel
                    from diffusers import GGUFQuantizationConfig

                    quantization_config = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16)
                    transformer = HunyuanVideoTransformer3DModel.from_single_file(
                        transformer_path,
                        quantization_config=GGUFQuantizationConfig(compute_dtype=torch.bfloat16),
                        torch_dtype=torch.bfloat16,
                    )

                    if (scene.image_path or scene.movie_path) and input == "input_strips":
                        pipe = HunyuanVideoImageToVideoPipeline.from_pretrained(
                            model_id,
                            #text_encoder=text_encoder,
                            #text_encoder_2=text_encoder_2,
                            transformer=transformer,
                            torch_dtype=torch.float16,
                        )
                    else:
                        text_encoder = LlamaModel.from_pretrained(
                            model_id,
                            #"hunyuanvideo-community/HunyuanVideo",
                            subfolder="text_encoder",
                            quantization_config=quantization_config,
                            torch_dtype=torch.float16
                        )
                        text_encoder_2 = CLIPTextModel.from_pretrained(
                            model_id,
                            #"hunyuanvideo-community/HunyuanVideo",
                            subfolder="text_encoder_2",
                            quantization_config=quantization_config,
                            torch_dtype=torch.float16
                        )
                        pipe = HunyuanVideoPipeline.from_pretrained(
                            model_id,
                            text_encoder=text_encoder,
                            text_encoder_2=text_encoder_2,
                            transformer=transformer,
                            torch_dtype=torch.float16,
                        )

    #                    from diffusers import HunyuanVideoPipeline, HunyuanVideoTransformer3DModel
    #                    from diffusers import GGUFQuantizationConfig
    #                    from diffusers.utils import export_to_video

    #                    if low_vram():
    #                        transformer_path = f"https://huggingface.co/city96/HunyuanVideo-gguf/blob/main/hunyuan-video-t2v-720p-Q3_K_S.gguf"
    #                    else:
    #                        transformer_path = f"https://huggingface.co/city96/HunyuanVideo-gguf/blob/main/hunyuan-video-t2v-720p-Q4_K_S.gguf"

    #                    transformer = HunyuanVideoTransformer3DModel.from_single_file(
    #                        transformer_path,
    #                        quantization_config=GGUFQuantizationConfig(compute_dtype=torch.bfloat16),
    #                        torch_dtype=torch.bfloat16,
    #                    )

    #                    pipe = HunyuanVideoPipeline.from_pretrained(
    #                        movie_model_card,
    #                        transformer=transformer,
    #                        torch_dtype=torch.float16
    #                    )

                    if enabled_items:
                        for item in enabled_items:
                            enabled_names.append(
                                (clean_filename(item.name)).replace(".", "")
                            )
                            enabled_weights.append(item.weight_value)
                            pipe.load_lora_weights(
                                bpy.path.abspath(scene.lora_folder),
                                weight_name=item.name + ".safetensors",
                                adapter_name=((clean_filename(item.name)).replace(".", "")),
                            )
                        pipe.set_adapters(enabled_names, adapter_weights=enabled_weights)
                        print("Load LoRAs: " + " ".join(enabled_names))

                    if gfx_device == "mps":
                        pipe.to("mps")
                    elif low_vram():
                        pipe.vae.enable_tiling()
                        pipe.enable_model_cpu_offload()

                    else:
                        #pipe.vae.enable_tiling()
                        pipe.enable_model_cpu_offload()

                # FramePack
                elif movie_model_card == "lllyasviel/FramePackI2V_HY":
                    from diffusers import BitsAndBytesConfig, HunyuanVideoFramepackPipeline, HunyuanVideoFramepackTransformer3DModel
                    from diffusers.utils import export_to_video, load_image
                    from transformers import SiglipImageProcessor, SiglipVisionModel

                    # vid2vid
                    if scene.movie_path and input == "input_strips":
                        print("FramePack doesn't support vid2vid! Using img2vid instead...")

                    # img2vid
                    if (scene.image_path or scene.movie_path) and input == "input_strips":
                        print("FramePack: Load Image to Video Model")

                        nf4_config = BitsAndBytesConfig(
                            load_in_4bit=True,
                            bnb_4bit_quant_type="nf4",
                            bnb_4bit_compute_dtype=torch.bfloat16,
                        )

                        transformer = HunyuanVideoFramepackTransformer3DModel.from_pretrained(
                            "lllyasviel/FramePack_F1_I2V_HY_20250503",
                            #"lllyasviel/FramePackI2V_HY",
                            #"newgenai79/SkyReels-V1-Hunyuan-I2V-int4",
                            #subfolder="transformer",
                            quantization_config=nf4_config,
                            torch_dtype=torch.bfloat16,
                        )
                        feature_extractor = SiglipImageProcessor.from_pretrained(
                            "lllyasviel/flux_redux_bfl", subfolder="feature_extractor"
                        )
                        image_encoder = SiglipVisionModel.from_pretrained(
                            "lllyasviel/flux_redux_bfl", subfolder="image_encoder", torch_dtype=torch.float16
                        )

                        pipe = HunyuanVideoFramepackPipeline.from_pretrained(
                            "hunyuanvideo-community/HunyuanVideo",
                            transformer=transformer,
                            feature_extractor=feature_extractor,
                            image_encoder=image_encoder,
                            torch_dtype=torch.float16,
                        )


                    # prompt to video
                    else:
                        print("FramePack: Prompt to Video is not supported!")
                        return {"CANCELLED"}
    #                    model_id = "hunyuanvideo-community/HunyuanVideo"
    ##                    from diffusers import BitsAndBytesConfig, HunyuanVideoFramepackPipeline, HunyuanVideoFramepackTransformer3DModel
    ##                    from diffusers.utils import export_to_video, load_image
    #                    from transformers import SiglipImageProcessor, SiglipVisionModel

    #                    nf4_config = BitsAndBytesConfig(
    #                        load_in_4bit=True,
    #                        bnb_4bit_quant_type="nf4",
    #                        bnb_4bit_compute_dtype=torch.bfloat16,
    #                    )

    #                    transformer = HunyuanVideoFramepackTransformer3DModel.from_pretrained(
    #                        "lllyasviel/FramePackI2V_HY",
    #                        quantization_config=nf4_config,
    #                        torch_dtype=torch.bfloat16,
    #                    )
    #                    feature_extractor = SiglipImageProcessor.from_pretrained(
    #                        "lllyasviel/flux_redux_bfl", subfolder="feature_extractor"
    #                    )
    #                    image_encoder = SiglipVisionModel.from_pretrained(
    #                        "lllyasviel/flux_redux_bfl", subfolder="image_encoder", torch_dtype=torch.float16
    #                    )
    #                    pipe = HunyuanVideoFramepackPipeline.from_pretrained(
    #                        "hunyuanvideo-community/HunyuanVideo",
    #                        transformer=transformer,
    #                        feature_extractor=feature_extractor,
    #                        image_encoder=image_encoder,
    #                        torch_dtype=torch.float16,
    #                    )

                    if gfx_device == "mps":
                        pipe.to("mps")
                    else:
                        pipe.vae.enable_tiling()
                        pipe.enable_model_cpu_offload()

                #Skyreel
                elif movie_model_card == "Skywork/SkyReels-V1-Hunyuan-T2V":

                    prompt = ensure_skyreel(prompt)
                    print("Corrected Prompt: "+prompt)

                    # vid2vid
                    if scene.movie_path and input == "input_strips":
                        print("SkyReels-V1-Hunyuan doesn't support vid2vid! Doing img2vid instead.")
                        #return {"CANCELLED"}

                    # img2vid
                    if (scene.image_path or scene.movie_path) and input == "input_strips":
                        print("Load: Image to video (SkyReels-V1-Hunyuan-I2V)")
                        #import torch._dynamo.config
                        from diffusers import HunyuanSkyreelsImageToVideoPipeline, HunyuanVideoTransformer3DModel
                        from diffusers.utils import load_image, export_to_video
    #                    from diffusers.hooks import apply_group_offloading

                        #torch._dynamo.config.inline_inbuilt_nn_modules = True

                        model_id = "hunyuanvideo-community/HunyuanVideo"
                        transformer_model_id = "newgenai79/SkyReels-V1-Hunyuan-I2V-int4"

                        transformer = HunyuanVideoTransformer3DModel.from_pretrained(
                            transformer_model_id, torch_dtype=torch.bfloat16, subfolder="transformer",
                        )

    #                    apply_group_offloading(
    #                        transformer,
    #                        onload_device=torch.device("cuda"),
    #                        offload_device=torch.device("cpu"),
    #                        offload_type="block_level",
    #                        num_blocks_per_group=2,
    #                        use_stream=True,
    #                    )

                        pipe = HunyuanSkyreelsImageToVideoPipeline.from_pretrained(
                            model_id, transformer=transformer, torch_dtype=torch.float16
                        )

                    # txt2vid
                    else:
                        print("Load: text to video (SkyReels-V1-Hunyuan-T2V)")

                        #import torch._dynamo.config
                        from diffusers import HunyuanVideoPipeline, HunyuanVideoTransformer3DModel
                        from diffusers.utils import export_to_video

                        #torch._dynamo.config.inline_inbuilt_nn_modules = True

                        model_id = "newgenai79/HunyuanVideo-int4"
                        transformer_model_id = "newgenai79/SkyReels-V1-Hunyuan-T2V-int4"
                        transformer = HunyuanVideoTransformer3DModel.from_pretrained(
                            transformer_model_id,
                            subfolder="transformer",
                            torch_dtype=torch.bfloat16
                        )
                        transformer.enable_layerwise_casting(storage_dtype=torch.float8_e4m3fn, compute_dtype=torch.bfloat16)
                        pipe = HunyuanVideoPipeline.from_pretrained(model_id, transformer=transformer, torch_dtype=torch.float16)

                    if gfx_device == "mps":
                        pipe.to("mps")
                    elif low_vram():
                        # pipe.vae.enable_slicing()
                        pipe.vae.enable_tiling()
                        pipe.enable_model_cpu_offload()
                    else:
                        pipe.enable_model_cpu_offload()
                        #pipe.enable_sequential_cpu_offload()
                        #pipe.enable_xformers_memory_efficient_attention()
                        #pipe.to("cuda")

                elif movie_model_card == "Wan-AI/Wan2.2-T2V-A14B-Diffusers":
                    if (scene.movie_path or scene.image_path) and input == "input_strips":
                        print("Wan2.1-T2V doesn't support img/vid2vid!")
                        return {"CANCELLED"}

                    # Import all necessary classes
                    import gc
                    from diffusers import WanPipeline, WanTransformer3DModel, FlowMatchEulerDiscreteScheduler
                    from diffusers.utils import export_to_video
                    from transformers import BitsAndBytesConfig
                    
                    print("--- Initializing ---")
                    import gc
                    gc.collect()
                    torch.cuda.empty_cache()

                    MODEL_ID = "Wan-AI/Wan2.2-T2V-A14B-Diffusers"

                    # 4-Bit Configuration (Mandatory for 24GB VRAM)
                    nf4_config = BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_compute_dtype=torch.bfloat16
                    )

                    # LOAD MODELS (4-BIT QUANTIZED)
                    print("--- Loading Models ---")

                    # Load Transformer 1 (High Noise)
                    transformer_high = WanTransformer3DModel.from_pretrained(
                        MODEL_ID,
                        subfolder="transformer",
                        quantization_config=nf4_config,
                        torch_dtype=torch.bfloat16,
                        low_cpu_mem_usage=True
                    )

                    # Load Transformer 2 (Low Noise)
                    transformer_low = WanTransformer3DModel.from_pretrained(
                        MODEL_ID,
                        subfolder="transformer_2",
                        quantization_config=nf4_config,
                        torch_dtype=torch.bfloat16,
                        low_cpu_mem_usage=True
                    )

                    # Create Pipeline (WanPipeline for Text-to-Video)
                    pipe = WanPipeline.from_pretrained(
                        MODEL_ID,
                        transformer=transformer_high,
                        transformer_2=transformer_low,
                        torch_dtype=torch.bfloat16,
                        low_cpu_mem_usage=True
                    )

                    # MEMORY & SCHEDULER SETUP
                    print("--- Configuring Optimization ---")

                    if gfx_device == "mps":
                        # Note: bitsandbytes quantization typically requires a CUDA-enabled GPU.
                        # This line will likely fail on MPS. You may need to add logic
                        # to skip quantization if gfx_device is "mps".
                        pipe.to("mps")
                    else:
                        # 1. CPU Offload (Saves VRAM)
                        pipe.enable_model_cpu_offload()

                        # We enable slicing (frame-by-frame decode) and DISABLE tiling.
                        pipe.vae.enable_slicing()
                        pipe.vae.disable_tiling()

                        # 3. Set Scheduler to Euler + Shift 5.0 (Required for Lightx2v LoRA)
                        pipe.scheduler = FlowMatchEulerDiscreteScheduler.from_config(
                            pipe.scheduler.config,
                            shift=5.0,
                            use_dynamic_shifting=False
                        )

                    # LOAD T2V LORA (FROM SCREENSHOT)
                    print("--- Loading T2V Turbo LoRA ---")
                    try:
                        # Filename from your screenshot: lightx2v_T2V_14B_...
                        LORA_FILENAME = "Lightx2v/lightx2v_T2V_14B_cfg_step_distill_v2_lora_rank128_bf16.safetensors"
                        
                        # Load into High Noise Transformer
                        pipe.load_lora_weights(
                            "Kijai/WanVideo_comfy",
                            weight_name=LORA_FILENAME,
                            adapter_name="lightx2v"
                        )
                        # Load into Low Noise Transformer
                        pipe.load_lora_weights(
                            "Kijai/WanVideo_comfy",
                            weight_name=LORA_FILENAME,
                            adapter_name="lightx2v_2",
                            load_into_transformer_2=True
                        )
                        
                        pipe.set_adapters(["lightx2v", "lightx2v_2"], adapter_weights=[1.0, 1.0])
                        print("T2V LoRA loaded successfully.")
                        
                    except Exception as e:
                        print(f"LoRA Load Failed: {e}")
                        print("STOPPING: This script is optimized for the LoRA. Running without it might produce bad results at low steps.")
                                        
                    lora_files = scene.lora_files
                    enabled_names = []
                    enabled_weights = []
                    # Check if there are any enabled items before loading
                    enabled_items = [item for item in lora_files if item.enabled]

                    if enabled_items:
                        for item in enabled_items:
                            enabled_names.append(
                                (clean_filename(item.name)).replace(".", "")
                            )
                            enabled_weights.append(item.weight_value)
                            pipe.load_lora_weights(
                                bpy.path.abspath(scene.lora_folder),
                                weight_name=item.name + ".safetensors",
                                adapter_name=((clean_filename(item.name)).replace(".", "")),
                            )
                        pipe.set_adapters(enabled_names, adapter_weights=enabled_weights)
                        print("Load LoRAs: " + " ".join(enabled_names))

#                    if gfx_device == "mps":
#                        # Note: bitsandbytes quantization typically requires a CUDA-enabled GPU.
#                        # This line will likely fail on MPS. You may need to add logic
#                        # to skip quantization if gfx_device is "mps".
#                        pipe.to("mps")
#                    elif low_vram():
#                        pipe.enable_model_cpu_offload()
#                    else:
#                        pipe.to("cuda")
#                        #pipe.enable_model_cpu_offload()


                    lora_files = scene.lora_files
                    enabled_names = []
                    enabled_weights = []
                    # Check if there are any enabled items before loading
                    enabled_items = [item for item in lora_files if item.enabled]

                    if enabled_items:
                        for item in enabled_items:
                            enabled_names.append(
                                (clean_filename(item.name)).replace(".", "")
                            )
                            enabled_weights.append(item.weight_value)
                            pipe.load_lora_weights(
                                bpy.path.abspath(scene.lora_folder),
                                weight_name=item.name + ".safetensors",
                                adapter_name=((clean_filename(item.name)).replace(".", "")),
                            )
                        pipe.set_adapters(enabled_names, adapter_weights=enabled_weights)
                        print("Load LoRAs: " + " ".join(enabled_names))

                    if gfx_device == "mps":
                        # Note: bitsandbytes quantization typically requires a CUDA-enabled GPU.
                        # This line will likely fail on MPS. You may need to add logic
                        # to skip quantization if gfx_device is "mps".
                        pipe.to("mps")
                    elif low_vram():
                        pipe.enable_model_cpu_offload()
                    else:
                        pipe.enable_model_cpu_offload()

                elif movie_model_card == "Wan-AI/Wan2.2-I2V-A14B-Diffusers":
                    if (not scene.movie_path and not scene.image_path) and not input == "input_strips":
                        print("Wan2.1-I2V doesn't support txt2vid!")
                        self.report({'ERROR'}, "Wan2.1-I2V requires an input image or video.")
                        return {"CANCELLED"}

                    print(f"Load: {movie_model_card} with maximum memory optimization.")

                    import torch
                    from diffusers import WanImageToVideoPipeline, WanTransformer3DModel, FlowMatchEulerDiscreteScheduler
                    from diffusers.utils import export_to_video, load_image
                    from transformers import BitsAndBytesConfig
                    import gc  
      
                    MODEL_ID = "Wan-AI/Wan2.2-I2V-A14B-Diffusers"

                    # 4-Bit Configuration (Crucial for 24GB VRAM)
                    nf4_config = BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_compute_dtype=torch.bfloat16
                    )

                    print("--- Loading Models (This takes a moment) ---")

                    # Load Transformer 1 (High Noise)
                    transformer_high = WanTransformer3DModel.from_pretrained(
                        MODEL_ID,
                        subfolder="transformer",
                        quantization_config=nf4_config,
                        torch_dtype=torch.bfloat16,
                        low_cpu_mem_usage=True
                    )

                    # Load Transformer 2 (Low Noise)
                    transformer_low = WanTransformer3DModel.from_pretrained(
                        MODEL_ID,
                        subfolder="transformer_2",
                        quantization_config=nf4_config,
                        torch_dtype=torch.bfloat16,
                        low_cpu_mem_usage=True
                    )

                    # Create Pipeline
                    pipe = WanImageToVideoPipeline.from_pretrained(
                        MODEL_ID,
                        transformer=transformer_high,
                        transformer_2=transformer_low,
                        torch_dtype=torch.bfloat16,
                        low_cpu_mem_usage=True
                    )

                    # Enable CPU Offload (Saves VRAM)
                    if gfx_device == "mps":
                        # Note: bitsandbytes quantization typically requires a CUDA-enabled GPU.
                        # This line will likely fail on MPS. You may need to add logic
                        # to skip quantization if gfx_device is "mps".
                        pipe.to("mps")
                    elif low_vram():
                        pipe.enable_model_cpu_offload()
                    else:
                        pipe.enable_model_cpu_offload()

                    pipe.scheduler = FlowMatchEulerDiscreteScheduler.from_config(
                        pipe.scheduler.config,
                        shift=5.0
                    )

                    # LOAD LORA (4-Step Turbo)
                    print("--- Loading LoRAs ---")
                    try:
                        # Load LoRA for High Noise Transformer
                        pipe.load_lora_weights(
                            "Kijai/WanVideo_comfy",
                            weight_name="Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank128_bf16.safetensors",
                            adapter_name="lightx2v"
                        )
                        # Load LoRA for Low Noise Transformer
                        pipe.load_lora_weights(
                            "Kijai/WanVideo_comfy",
                            weight_name="Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank128_bf16.safetensors",
                            adapter_name="lightx2v_2",
                            load_into_transformer_2=True
                        )
                        pipe.set_adapters(["lightx2v", "lightx2v_2"], adapter_weights=[1.0, 1.0])
                        print("LoRAs loaded successfully.")
                    except Exception as e:
                        print(f"LoRA Load Failed: {e}")
                        print("Continuing without LoRA (Standard Speed)...")

                    lora_files = scene.lora_files
                    enabled_names = []
                    enabled_weights = []
                    enabled_items = [item for item in lora_files if item.enabled]

                    if enabled_items:
                        for item in enabled_items:
                            enabled_names.append(
                                (clean_filename(item.name)).replace(".", "")
                            )
                            enabled_weights.append(item.weight_value)
                            pipe.load_lora_weights(
                                bpy.path.abspath(scene.lora_folder),
                                weight_name=item.name + ".safetensors",
                                adapter_name=((clean_filename(item.name)).replace(".", "")),
                            )
                        pipe.set_adapters(enabled_names, adapter_weights=enabled_weights)
                        print("Load LoRAs: " + " ".join(enabled_names))

                # Wan Vace - Refactored and Optimized
                elif movie_model_card == "Wan-AI/Wan2.1-VACE-1.3B-diffusers":
                    print("Model: " + movie_model_card)

                    # The loading logic is the same for both t2v and i2v, so we remove the redundant if/else.
                    # We just print the mode for user feedback.
                    if not ((scene.movie_path or scene.image_path) and input == "input_strips"):
                        print("Mode: Text-to-Video")
                    else:
                        print("Mode: Image-to-Video")

                    import torch
                    from diffusers import AutoencoderKLWan, WanVACEPipeline
                    from diffusers.quantizers import PipelineQuantizationConfig
                    from diffusers.schedulers import UniPCMultistepScheduler
                    from diffusers.utils import export_to_video, load_image

                    # 1. Define the quantization configuration for the main transformer model.
                    pipeline_quant_config = PipelineQuantizationConfig(
                        quant_backend="bitsandbytes_4bit",
                        quant_kwargs={
                            "load_in_4bit": True,
                            "bnb_4bit_quant_type": "nf4",
                            "bnb_4bit_compute_dtype": torch.bfloat16
                        },
                        components_to_quantize=["transformer"],
                    )

                    # 2. Load the VAE separately in full float32 precision to ensure maximum quality.
                    # This is an important step for VACE models.
                    print("Loading VAE in float32 for maximum quality...")
                    vae = AutoencoderKLWan.from_pretrained(movie_model_card, subfolder="vae", torch_dtype=torch.float32)

                    # 3. Load the main pipeline, passing both the quantization config and the pre-loaded VAE.
                    # This applies 4-bit quantization to the transformer while using our high-quality VAE.
                    print("Loading main pipeline with 4-bit quantization...")
                    pipe = WanVACEPipeline.from_pretrained(
                        movie_model_card,
                        vae=vae, # Use the high-precision VAE we just loaded
                        quantization_config=pipeline_quant_config
                    )
                    print("Pipeline loaded successfully.")

                    # 4. Set up the scheduler as before. This is done after the pipeline is loaded.
                    flow_shift = 5.0  # 5.0 for 720P, 3.0 for 480P
                    pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=flow_shift)
                    print(f"Scheduler set to UniPCMultistep with flow_shift={flow_shift}")

                    # 5. Apply memory management for inference. This is still a good safety measure.
                    if gfx_device == "mps":
                        # Quantization is not supported on MPS, so this path assumes a non-quantized model.
                        print("Moving model to MPS.")
                        pipe.to("mps")
                    # For CUDA devices, offloading is the final step for memory-safe inference.
                    elif low_vram():
                        print("Low VRAM mode: Enabling model CPU offload.")
                        pipe.enable_model_cpu_offload()
                    else:
                        print("Defaulting to model CPU offload for stability.")
                        #pipe.enable_sequential_cpu_offload()
                        pipe.enable_model_cpu_offload()

    #            # Wan Vace
    #            elif movie_model_card == "Wan-AI/Wan2.1-VACE-1.3B-diffusers":
    #                print("Model: "+movie_model_card)
    #                # t2i
    #                if not ((scene.movie_path or scene.image_path) and input == "input_strips"):
    #                    from diffusers import AutoencoderKLWan, WanVACEPipeline
    #                    from diffusers.schedulers.scheduling_unipc_multistep import UniPCMultistepScheduler
    #                    from diffusers.utils import export_to_video

    #                    vae = AutoencoderKLWan.from_pretrained(movie_model_card, subfolder="vae", torch_dtype=torch.float32)
    #                    pipe = WanVACEPipeline.from_pretrained(movie_model_card, vae=vae, torch_dtype=torch.bfloat16)
    #                    flow_shift = 5.0  # 5.0 for 720P, 3.0 for 480P
    #                    pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=flow_shift)
    #
    #                #i2v
    #                else:
    #                    import PIL.Image
    #                    from diffusers import AutoencoderKLWan, WanVACEPipeline
    #                    from diffusers.schedulers.scheduling_unipc_multistep import UniPCMultistepScheduler
    #                    from diffusers.utils import export_to_video, load_image

    #                    vae = AutoencoderKLWan.from_pretrained(movie_model_card, subfolder="vae", torch_dtype=torch.float32)
    #                    pipe = WanVACEPipeline.from_pretrained(movie_model_card, vae=vae, torch_dtype=torch.bfloat16)
    #                    flow_shift = 5.0  # 5.0 for 720P, 3.0 for 480P
    #                    pipe.scheduler = UniPCMultistepScheduler.from_config(pipe.scheduler.config, flow_shift=flow_shift)

    #                if gfx_device == "mps":
    #                    pipe.to("mps")
    #                elif low_vram():
    #                    # pipe.enable_slicing()
    #                    pipe.enable_model_cpu_offload()
    #                else:
    #                    #pipe.enable_sequential_cpu_offload()
    #                    #pipe.vae.enable_tiling()
    #                    pipe.enable_model_cpu_offload()

                else:
                    from diffusers import TextToVideoSDPipeline
                    import torch

                    pipe = TextToVideoSDPipeline.from_pretrained(
                        movie_model_card,
                        torch_dtype=torch.float16,
                        use_safetensors=False,
                        local_files_only=local_files_only,
                    )
                    from diffusers import DPMSolverMultistepScheduler

                    pipe.scheduler = DPMSolverMultistepScheduler.from_config(
                        pipe.scheduler.config
                    )
                    if gfx_device == "mps":
                        pipe.to("mps")
                    elif low_vram():
                        pipe.enable_model_cpu_offload()
                        # pipe.vae.enable_slicing()
                    else:
                        pipe.to(gfx_device)
            
            # --- CACHE UPDATE ---
            _pallaidium_movie_model_cache["pipe"] = pipe
            _pallaidium_movie_model_cache["refiner"] = refiner
            _pallaidium_movie_model_cache["last_model_card"] = movie_model_card

        # GENERATING - Main Loop Video
        for i in range(scene.movie_num_batch):
            if duration == -1 and input == "input_strips":
                strip = scene.sequence_editor.active_strip
                if strip:
                    duration = scene.generate_movie_frames = (
                        strip.frame_final_duration + 1
                    )
                    print(str(strip.frame_final_duration))

            start_time = timer()

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            if i > 0:
                empty_channel = scene.sequence_editor.active_strip.channel
                start_frame = (
                    scene.sequence_editor.active_strip.frame_final_start
                    + scene.sequence_editor.active_strip.frame_final_duration
                )
                scene.frame_current = (
                    scene.sequence_editor.active_strip.frame_final_start
                )
            else:
                empty_channel = find_first_empty_channel(
                    scene.frame_current,
                    (scene.movie_num_batch * abs(duration)) + scene.frame_current,
                )
                start_frame = scene.frame_current

            # Get seed
            seed = context.scene.movie_num_seed
            seed = (
                seed
                if not context.scene.movie_use_random
                else random.randint(-2147483647, 2147483647)
            )
            print("Seed: " + str(seed))
            context.scene.movie_num_seed = seed

            # Use cuda if possible
            if torch.cuda.is_available():
                generator = (
                    torch.Generator("cuda").manual_seed(seed) if seed != 0 else None
                )
            else:
                if seed != 0:
                    generator = torch.Generator(device=gfx_device)
                    generator.manual_seed(seed)
                else:
                    generator = None

            # Process batch input for images
            if (scene.movie_path or scene.image_path) and input == "input_strips":
                video_path = scene.movie_path

                # frame2frame
                if movie_model_card == "stabilityai/stable-diffusion-xl-base-1.0":
                    input_video_path = video_path
                    output_video_path = solve_path("temp_images")
                    if scene.movie_path:
                        print("Process: Frame by frame (SD XL) - from Movie strip")
                        frames = process_video(input_video_path, output_video_path)
                    elif scene.image_path:
                        print("Process: Frame by frame (SD XL) - from Image strip")
                        frames = process_image(
                            scene.image_path, int(scene.generate_movie_frames)
                        )

                    from torchvision import transforms

                    pil_to_tensor = transforms.ToTensor()

                    video_frames = []

                    for frame_idx, frame in enumerate(frames):
                        try:
                            if frame is None:
                                print(f"Frame {frame_idx} is None. Skipping.")
                                continue

                            if not isinstance(frame, Image.Image):
                                print(
                                    f"Frame {frame_idx} is not a valid PIL image. Type: {type(frame)}. Skipping."
                                )
                                continue

                            width, height = frame.size
                            print(
                                f"Processing frame {frame_idx + 1}/{len(frames)}, size: {width}x{height}"
                            )

                            if width == 0 or height == 0:
                                print(
                                    f"Frame {frame_idx} has invalid dimensions {width}x{height}. Skipping."
                                )
                                continue

                            new_width = closest_divisible_8(width)
                            new_height = closest_divisible_8(height)

                            if (new_width, new_height) != (width, height):
                                print(
                                    f"Resizing frame {frame_idx} to {new_width}x{new_height}"
                                )
                                frame = frame.resize(
                                    (new_width, new_height), Image.Resampling.LANCZOS
                                )

                            frame = transforms.functional.invert(frame)

                            frame_tensor = pil_to_tensor(frame)
                            frame_tensor = frame_tensor.float()

                            print(
                                f"Frame {frame_idx} - Tensor shape: {frame_tensor.shape}, Total elements: {frame_tensor.numel()}"
                            )
                            print(
                                f"Frame {frame_idx} - Tensor data type: {frame_tensor.dtype}"
                            )

                            if frame_tensor.numel() == 0:
                                print(
                                    f"Frame {frame_idx}: Tensor has zero elements. Skipping."
                                )
                                continue

                            if frame_tensor.ndim == 3:
                                frame_tensor = frame_tensor.unsqueeze(0)
                                print(
                                    f"After adding batch dimension - Tensor shape: {frame_tensor.shape}"
                                )

                            print(
                                f"Before processing - Tensor shape: {frame_tensor.shape}, Elements: {frame_tensor.numel()}"
                            )

                            try:
                                print(f"Frame {frame_idx}: Running Frame by Frame...")
                                image = refiner(
                                    prompt,
                                    image=frame_tensor,
                                    strength=1.00 - scene.image_power,
                                    num_inference_steps=movie_num_inference_steps,
                                    guidance_scale=2.8,  # movie_num_guidance,
                                    generator=generator,
                                ).images[0]

                                if image is None or not isinstance(image, Image.Image):
                                    print(
                                        f"Frame {frame_idx}: Output is INVALID. Skipping."
                                    )
                                    continue

                                print(f"Frame {frame_idx}: Is a valid image.")

                            except Exception as e:
                                print(f"Frame {frame_idx}: ERROR in refiner - {e}")
                                continue

                        except Exception as e:
                            print(f"Frame {frame_idx}: General error - {e}")
                            continue

                        video_frames.append(image)

                    video_frames = np.array(video_frames)

                # CogVideoX img/vid2vid
                elif (
                    movie_model_card == "THUDM/CogVideoX-5b"
                    or movie_model_card == "THUDM/CogVideoX-2b"
                ):
                    if scene.movie_path:
                        print("Process: Video to video (CogVideoX)")
                        if not os.path.isfile(scene.movie_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        # video = load_video_as_np_array(video_path)
                        video = load_video(video_path)[:49]
                        video_frames = pipe(
                            video=video,
                            prompt=prompt,
                            strength=1.00 - scene.image_power,
                            negative_prompt=negative_prompt,
                            num_inference_steps=movie_num_inference_steps,
                            guidance_scale=movie_num_guidance,
                            height=480,
                            width=720,
                            # num_frames=abs(duration),
                            generator=generator,
                        ).frames[0]

                    elif scene.image_path:
                        print("Process: Image to video (CogVideoX)")
                        strip = scene.sequence_editor.active_strip
                        img_path = os.path.join(bpy.path.abspath(strip.directory), strip.elements[0].filename)
                        if not os.path.isfile(img_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_image(img_path)
                        image = image.resize(
                            (closest_divisible_32(int(x)), closest_divisible_32(int(y)))
                        )
                        video_frames = pipe(
                            image=image,
                            prompt=prompt,
                            # strength=1.00 - scene.image_power,
                            # negative_prompt=negative_prompt,
                            num_inference_steps=movie_num_inference_steps,
                            guidance_scale=movie_num_guidance,
                            height=480,
                            width=720,
                            # num_frames=abs(duration),
                            generator=generator,
                            use_dynamic_cfg=True,
                        ).frames[0]

                # LTX
                elif movie_model_card == "Lightricks/LTX-Video":
                    if scene.movie_path:
                        print("Process: Image from Video to Video")
                        if not os.path.isfile(bpy.path.abspath(scene.movie_path)):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_video(bpy.path.abspath(scene.movie_path))
                        #image = load_first_frame(bpy.path.abspath(scene.movie_path))
                    if scene.image_path:
                        strip = scene.sequence_editor.active_strip
                        print("Process: Image to video (LTX)")
                        img_path = os.path.join(bpy.path.abspath(strip.directory), strip.elements[0].filename)
                        print("Path: "+img_path)
                        if not os.path.isfile(img_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_image(img_path)
                    #                    image = image.resize(
                    #                        (closest_divisible_32(int(x)), closest_divisible_32(int(y)))
                    #                    )
                    video_frames = pipe(
                        image=image,
                        prompt=prompt,
                        # strength=1.00 - scene.image_power,
                        negative_prompt=negative_prompt,
                        num_inference_steps=movie_num_inference_steps,
                        guidance_scale=movie_num_guidance,
                        height=y,
                        width=x,
                        num_frames=abs(duration),
                        generator=generator,
                        max_sequence_length=512,
                        decode_timestep=0.05,
                        image_cond_noise_scale=0.025,
                    ).frames[0]

                # LTX-2
                elif movie_model_card == "rootonchair/LTX-2-19b-distilled":
                    if scene.movie_path:
                        print("Process: Video Image to Video")
                        if not os.path.isfile(scene.movie_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_first_frame(bpy.path.abspath(scene.movie_path))
                    if scene.image_path:
                        print("Process: Image to video")
                        strip = scene.sequence_editor.active_strip
                        img_path = os.path.join(bpy.path.abspath(strip.directory), strip.elements[0].filename)
                        if not os.path.isfile(img_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_image(img_path)
                        
                    render = bpy.context.scene.render
                    fps = round((render.fps / render.fps_base), 3) 
 
                    x = width_clean = (x // 32) * 32
                    y = height_clean = (y // 32) * 32
                    
                    # 2. Ensure frame count follows (n * 8) + 1 rule (Temporal Requirement)
                    # LTX-2 VAE compresses time by 8x. Arbitrary frame counts cause tensor mismatch.
                    target_frames = abs(duration)
                    duration = valid_num_frames = ((target_frames - 1) // 8) * 8 + 1
                    
                    # Ensure a minimum valid length (9 frames is the smallest block: 1*8 + 1)
                    if valid_num_frames < 9:
                        duration = valid_num_frames = 9
                        
                    #print(f"LTX-2 Adjustment: Resizing {x}x{y} -> {width_clean}x{height_clean}")
#                    print(f"LTX-2 Adjustment: Frames {target_frames} -> {valid_num_frames}")
#                    print(f"Preprocessing input image to fit {width_clean}x{height_clean} without distortion.")
#                    image = resize_and_pad_image(image, width_clean, height_clean) 
                    
                    print("Stage 1: Image → video latents")
                                                          
                    video_latent, audio_latent = pipe(
                        image=image,
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        width=x,
                        height=y,
                        num_frames=abs(duration),
                        frame_rate=fps,
                        max_sequence_length=512,
                        num_inference_steps=8,
                        sigmas=DISTILLED_SIGMA_VALUES,
                        guidance_scale=1.0,
                        generator=generator,
                        output_type="latent",
                        return_dict=False,
                    )
                    print("Stage 1.5: Latent upsampling")
                    #clear_cuda_cache()
                    latent_upsampler = LTX2LatentUpsamplerModel.from_pretrained(
                        movie_model_card,
                        subfolder="latent_upsampler",
                        torch_dtype=torch.bfloat16,
                    )
                    upsample_pipe = LTX2LatentUpsamplePipeline(vae=pipe.vae, latent_upsampler=latent_upsampler)
                    upsample_pipe.enable_model_cpu_offload(device=gfx_device)
                    upscaled_video_latent = upsample_pipe(
                        latents=video_latent,
                        output_type="latent",
                        return_dict=False,
                    )[0]
                    print("Stage 2: Decode + final upscale")
                    video, audio = pipe(
                        image=image,
                        latents=upscaled_video_latent,
                        audio_latents=audio_latent,
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        width=x * 2,
                        height=y * 2,
                        num_inference_steps=3,
                        num_frames=abs(duration),
                        noise_scale=STAGE_2_DISTILLED_SIGMA_VALUES[0],
                        sigmas=STAGE_2_DISTILLED_SIGMA_VALUES,
                        generator=generator,
                        guidance_scale=1.0,
                        output_type="np",
                        return_dict=False,
                    )
                    video = (video * 255).round().astype("uint8")
                    video = torch.from_numpy(video)
                    #clear_cuda_cache()
                    dst_path = solve_path(clean_filename(str(seed) + "_" + prompt) + ".mp4")

                    encode_video(
                        video[0],
                        fps=fps,
                        audio=audio[0].float().cpu(),
                        audio_sample_rate=pipe.vocoder.config.output_sampling_rate,
                        output_path=dst_path,
                    )                                      

                # LTX-2
                elif movie_model_card == "Lightricks/LTX-2":
                    import gc
                    import os
                    import time
                    import torch
                    import cv2
                    import numpy as np
                    from diffusers import LTX2ImageToVideoPipeline, LTX2LatentUpsamplePipeline, LTX2VideoTransformer3DModel
                    from diffusers.pipelines.ltx2.export_utils import encode_video
                    from diffusers.pipelines.ltx2.latent_upsampler import LTX2LatentUpsamplerModel
                    from diffusers.pipelines.ltx2.utils import DISTILLED_SIGMA_VALUES, STAGE_2_DISTILLED_SIGMA_VALUES
                    from diffusers.utils import load_image
                    from transformers import Gemma3ForConditionalGeneration
                    if scene.movie_path:
                        print("Process: Video Image to Video ")
                        if not os.path.isfile(scene.movie_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_first_frame(bpy.path.abspath(scene.movie_path))
                    if scene.image_path:
                        print("Process: Image to video")
                        strip = scene.sequence_editor.active_strip
                        img_path = os.path.join(bpy.path.abspath(strip.directory), strip.elements[0].filename)
                        if not os.path.isfile(img_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_image(img_path)
                        
                    render = bpy.context.scene.render
                    fps = round((render.fps / render.fps_base), 3) 
 
                    x = width_clean = (x // 32) * 32
                    y = height_clean = (y // 32) * 32
                    
                    # 2. Ensure frame count follows (n * 8) + 1 rule (Temporal Requirement)
                    # LTX-2 VAE compresses time by 8x. Arbitrary frame counts cause tensor mismatch.
                    target_frames = abs(duration)
                    duration = valid_num_frames = ((target_frames - 1) // 8) * 8 + 1
                    
                    # Ensure a minimum valid length (9 frames is the smallest block: 1*8 + 1)
                    if valid_num_frames < 9:
                        duration = valid_num_frames = 9
                        

                    # Start global timer
                    total_start_time = time.time()

                    torch_dtype = torch.bfloat16
                    device = "cuda"
                    model_path = "Lightricks/LTX-2"
                    #width = 928-64
                    #height = 512-32
                    #num_frames = 145
                    seed = int.from_bytes(os.urandom(8), "big")
                    generator = torch.Generator("cpu").manual_seed(seed)

                    print("Loading base models...")
                    load_start = time.time()

#                    image = load_image(
#                        r"C:\Users\peter\Downloads\Nordic Siblings - Mid Shot 3.png"
#                    )

                    text_encoder = Gemma3ForConditionalGeneration.from_pretrained(
                        "OzzyGT/LTX-2-bnb-4bit-text-encoder",
                        dtype=torch_dtype,
                        device_map="cpu",
                    )

                    transformer = LTX2VideoTransformer3DModel.from_pretrained(
                        "OzzyGT/LTX-2-bnb-4bit-transformer-distilled",
                        torch_dtype=torch_dtype,
                        device_map="cpu",
                    )

                    pipe = LTX2ImageToVideoPipeline.from_pretrained(
                        model_path, transformer=transformer, text_encoder=text_encoder, torch_dtype=torch_dtype
                    )
                    pipe.vae.enable_tiling(
                        tile_sample_min_height=256,
                        tile_sample_min_width=256,
                        tile_sample_min_num_frames=16,
                        tile_sample_stride_height=192,
                        tile_sample_stride_width=192,
                        tile_sample_stride_num_frames=8,
                    )
                    pipe.vae.use_framewise_encoding = True
                    pipe.vae.use_framewise_decoding = True
                    pipe.enable_model_cpu_offload()

                    torch.cuda.synchronize()
                    print(f"Base models loaded in: {time.time() - load_start:.2f} seconds")

                    #prompt = "A warm sunny backyard. The camera starts in a tight cinematic close-up of a woman and a man in their 30s, facing each other with serious expressions. The woman, emotional and dramatic, says softly, “That’s it... Dad’s lost it. And we’ve lost Dad.” The man looks at her and exhales, saying softly: “Stop being so dramatic, Jess.” A beat. Then he glances aside, then mutters defensively, “He’s just having fun.” The camera slowly pans right, revealing the grandfather in the garden wearing enormous butterfly wings, waving his arms in the air like he’s trying to take off. He shouts, “Wheeeew!” as he flaps his wings with full commitment. The woman covers her face, on the verge of tears. The tone is deadpan, absurd, and quietly tragic."
                    #negative_prompt = "worst quality, inconsistent motion, blurry, jittery, distorted"

                    frame_rate = 24.0

                    print("Starting base generation (Stage 1)...")
                    base_gen_start = time.time()

                    video_latent, audio_latent = pipe(
                        prompt=prompt,
                        image=image,
                        width=x,
                        height=y,
                        num_frames=duration,
                        frame_rate=frame_rate,
                        num_inference_steps=8,
                        sigmas=DISTILLED_SIGMA_VALUES,
                        guidance_scale=1.0,
                        generator=generator,
                        output_type="latent",
                        return_dict=False,
                    )

                    torch.cuda.synchronize()
                    print(f"Base generation finished in: {time.time() - base_gen_start:.2f} seconds")

                    print("Loading Latent Upsampler...")
                    upsampler_load_start = time.time()

                    latent_upsampler = LTX2LatentUpsamplerModel.from_pretrained(
                        "rootonchair/LTX-2-19b-distilled",
                        subfolder="latent_upsampler",
                        torch_dtype=torch_dtype,
                    )
                    upsample_pipe = LTX2LatentUpsamplePipeline(vae=pipe.vae, latent_upsampler=latent_upsampler)
                    upsample_pipe.enable_model_cpu_offload(device=device)

                    torch.cuda.synchronize()
                    print(f"Latent Upsampler loaded in: {time.time() - upsampler_load_start:.2f} seconds")

                    print("Starting Latent Upscaling...")
                    upscale_start = time.time()

                    upscaled_video_latent = upsample_pipe(
                        latents=video_latent,
                        output_type="latent",
                        return_dict=False,
                    )[0]

                    torch.cuda.synchronize()
                    print(f"Latent Upscaling finished in: {time.time() - upscale_start:.2f} seconds")

                    print("Cleaning up memory...")
                    cleanup_start = time.time()

                    latent_upsampler.to("cpu")
                    del video_latent
                    del upsample_pipe
                    del latent_upsampler
                    gc.collect()
                    torch.cuda.empty_cache()

                    torch.cuda.synchronize()
                    print(f"Memory cleanup finished in: {time.time() - cleanup_start:.2f} seconds")

                    print("Starting Stage 2 Generation (High-Res Decoding)...")
                    stage2_start = time.time()

                    video, audio = pipe(
                        image=image,
                        latents=upscaled_video_latent,
                        audio_latents=audio_latent,
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        width = x * 2,
                        height = y * 2,
                        num_frames=duration,
                        num_inference_steps=3,
                        noise_scale=STAGE_2_DISTILLED_SIGMA_VALUES[0],
                        sigmas=STAGE_2_DISTILLED_SIGMA_VALUES,
                        generator=generator,
                        guidance_scale=1.0,
                        output_type="np",
                        return_dict=False,
                    )

                    torch.cuda.synchronize()
                    print(f"Stage 2 Generation finished in: {time.time() - stage2_start:.2f} seconds")

                    # --- IN-MEMORY SHARPENING STEP ---
                    print("Applying sharpening to frames in memory...")
                    sharpen_proc_start = time.time()

                    # Convert from float [0, 1] to uint8 [0, 255]
                    video_np = (video[0] * 255).round().astype("uint8")

                    # Define the sharpening kernel
                    # Adjust '5' to '4.8' for slightly softer or '5.5' for stronger sharpening
                    kernel = np.array([[0, -1, 0], 
                                       [-1, 4.9, -1], 
                                       [0, -1, 0]])

                    sharpened_frames = []
                    for frame in video_np:
                        # Apply filter to each frame in the sequence
                        s_frame = cv2.filter2D(frame, -1, kernel)
                        sharpened_frames.append(s_frame)

                    # Stack back into a single numpy array and convert to torch tensor for encode_video
                    video_sharpened = np.stack(sharpened_frames)
                    video_final = torch.from_numpy(video_sharpened)

                    print(f"In-memory sharpening finished in: {time.time() - sharpen_proc_start:.2f} seconds")
                    # ---------------------------------

                    # --- PURE RESTORATION PIPELINE (NO GRADING) ---
                    print("Applying clean restoration...")
                    post_proc_start = time.time()

                    video_np = (video[0] * 255).round().astype("uint8")
                    processed_frames = []

                    for frame in video_np:

                        # -------------------------------------------------
                        # 1 Recover Highlights (Soft Compression)
                        # -------------------------------------------------
                        frame_f = frame.astype(np.float32) / 255.0

                        # Compress only upper range
                        highlight_mask = frame_f > 0.85
                        frame_f[highlight_mask] = 0.85 + (frame_f[highlight_mask] - 0.85) * 0.5

                        restored = np.clip(frame_f * 255, 0, 255).astype(np.uint8)

                        # -------------------------------------------------
                        # 2 Mild Edge-Preserving Denoise
                        # -------------------------------------------------
                        denoised = cv2.bilateralFilter(
                            restored,
                            d=5,
                            sigmaColor=30,
                            sigmaSpace=30
                        )

                        # -------------------------------------------------
                        # 3 Advanced Face-Safe Detail Restoration
                        # -------------------------------------------------

                        # Convert to float
                        frame_f = denoised.astype(np.float32) / 255.0

                        # --- Band 1: Micro detail (small radius) ---
                        blur_small = cv2.GaussianBlur(frame_f, (0, 0), 0.6)
                        micro = frame_f - blur_small
                        micro_boost = frame_f + micro * 0.8

                        # --- Band 2: Structure detail (larger radius) ---
                        blur_large = cv2.GaussianBlur(frame_f, (0, 0), 2.0)
                        structure = frame_f - blur_large
                        structure_boost = micro_boost + structure * 0.4

                        # Clip safely
                        restored = np.clip(structure_boost, 0, 1)

                        final_frame = (restored * 255).astype(np.uint8)


                        processed_frames.append(final_frame)

                    video_processed = np.stack(processed_frames)
                    video_final = torch.from_numpy(video_processed)

                    print(f"Restoration finished in: {time.time() - post_proc_start:.2f} seconds")
                    # ----------------------------------------------------

                    print("Processing and exporting video...")
                    export_start = time.time()

#                    output_filename = f"ltx2_upscale_{width*2}x{height*2}x{num_frames}_sharpened_d.mp4"
#                    output_path = os.path.join(r"C:\Users\peter\Downloads", output_filename)
                    dst_path = solve_path(clean_filename(str(seed) + "_" + prompt) + ".mp4")
                    
                    encode_video(
                        video_final,
                        fps=frame_rate,
                        audio=audio[0].float().cpu(),
                        audio_sample_rate=pipe.vocoder.config.output_sampling_rate,
                        output_path=dst_path,
                    )

                    print(f"Export finished in: {time.time() - export_start:.2f} seconds")
                    print(f"Total script execution time: {time.time() - total_start_time:.2f} seconds")
#                    print("Saved sharpened video to: " + output_path)                        
                
                #LTX2-Multifile
                elif movie_model_card == "LTX-2 Multi-Input File":

                    import gc
                    import os
                    import time
                    #import torch
                    import wave
                    from PIL import Image
                    from diffusers.utils import load_image
                    from diffusers.utils import load_video
                    from diffusers import (
                        AutoencoderKLLTX2Video,
                        LTX2LatentUpsamplePipeline,
                        LTX2Pipeline,
                        LTX2VideoTransformer3DModel,
                    )
                    from diffusers.pipelines.ltx2.export_utils import encode_video
                    from diffusers.pipelines.ltx2.latent_upsampler import LTX2LatentUpsamplerModel
                    from diffusers.pipelines.ltx2.utils import (
                        DISTILLED_SIGMA_VALUES,
                        STAGE_2_DISTILLED_SIGMA_VALUES,
                    )
                    from diffusers.schedulers import FlowMatchEulerDiscreteScheduler

                    from sdnq.common import use_torch_compile as triton_is_available
                    from sdnq.loader import apply_sdnq_options_to_model
                    from transformers import Gemma3ForConditionalGeneration

                    # ==========================================================
                    # CLEANUP
                    # ==========================================================

                    def cleanup():
                        gc.collect()
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                    cleanup()

                    # ==========================================================
                    # SETTINGS
                    # ==========================================================

                    MODEL_PATH = "Lightricks/LTX-2"

                    #MODEL_PATH = "OzzyGT/tiny_LTX2"
                    #MODEL_PATH = "OzzyGT/LTX2_distilled_SDNQ_4bit_dynamic"
                    #MODEL_PATH = "Disty0/LTX-2-SDNQ-8bit-dynamic"
                    DEFAULT_NUM_FRAMES = 121
                    num_frames = DEFAULT_NUM_FRAMES

                    torch_dtype = torch.bfloat16
                    onload_device = torch.device("cuda")
                    offload_device = torch.device("cpu")

                    total_start_time = time.time()

                    # ==========================================================
                    # INPUT DETECTION
                    # ==========================================================

                    image = None
                    sound_path = None

#                    render = bpy.context.scene.render
#                    fps = round((render.fps / render.fps_base), 3) 
                    fps=24.0
                    
                    if scene.movie_path:
                        print("Process: Video Image to Video")
                        if not os.path.isfile(scene.movie_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_first_frame(bpy.path.abspath(scene.movie_path))
                    if scene.image_path:
                        print("Process: Image to video ")
                        strip = scene.sequence_editor.active_strip
                        img_path = os.path.join(bpy.path.abspath(strip.directory), strip.elements[0].filename)
                        if not os.path.isfile(img_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_image(img_path)

                    if scene.sound_path:
                        print("Process: Sound to Video")
                        if not os.path.isfile(bpy.path.abspath(scene.sound_path)):
                            print("No file found.")
                            return {"CANCELLED"}
                        sound_path = bpy.path.abspath(scene.sound_path)
                        
                    # MISSING: make a trimmed temp file
                    def get_wav_duration(path):
                        import soundfile as sf
                        try:
                            info = sf.info(path)
                            return info.frames / info.samplerate
                        except Exception as e:
                            print("Duration detection failed:", e)
                            return DEFAULT_NUM_FRAMES / fps

                    if sound_path:
                        duration = get_wav_duration(sound_path)

                        raw_frames = duration * fps
                        multiple_of_8 = int((raw_frames + 7) // 8) * 8
                        num_frames = multiple_of_8 + 1

                        print(f"Audio duration: {duration:.2f}s")
                    else:
                        # 2. Ensure frame count follows (n * 8) + 1 rule (Temporal Requirement)
                        # LTX-2 VAE compresses time by 8x. Arbitrary frame counts cause tensor mismatch.
                        target_frames = abs(duration)
                        num_frames = valid_num_frames = ((target_frames - 1) // 8) * 8 + 1
                        
                        # Ensure a minimum valid length (9 frames is the smallest block: 1*8 + 1)
                        if valid_num_frames < 9:
                            num_frames = valid_num_frames = 9

                    print(f"Image enabled: {image is not None}")
                    print(f"Audio enabled: {sound_path is not None}")
                    print(f"Frames: {num_frames}")

                    if image is None:
                        image = Image.new("RGB", (x, y), (0, 0, 0))
                        
                    cleanup()

                    # ==========================================================
                    # TEXT ENCODING
                    # ==========================================================

                    text_encoder = Gemma3ForConditionalGeneration.from_pretrained(
                        "OzzyGT/LTX-2-bnb-8bit-text-encoder",
                        dtype=torch_dtype,
                    )

                    embeds_pipe = LTX2Pipeline.from_pretrained(
                        MODEL_PATH,
                        text_encoder=text_encoder,
                        transformer=None,
                        vae=None,
                        audio_vae=None,
                        vocoder=None,
                        scheduler=None,
                        connectors=None,
                        torch_dtype=torch_dtype,
                    )

                    embeds_pipe.enable_sequential_cpu_offload()
                    #embeds_pipe.enable_model_cpu_offload()

                    with torch.inference_mode():
                        prompt_embeds, prompt_attention_mask, _, _ = embeds_pipe.encode_prompt(
                            prompt, negative_prompt, do_classifier_free_guidance=False
                        )

                    prompt_embeds = prompt_embeds.detach().to(offload_device, copy=True)
                    prompt_attention_mask = prompt_attention_mask.detach().to(offload_device, copy=True)

                    del embeds_pipe, text_encoder
                    cleanup()

                    # ==========================================================
                    # STAGE 1
                    # ==========================================================

                    transformer = LTX2VideoTransformer3DModel.from_pretrained(
                        "OzzyGT/LTX_2_SDNQ_4bit_dynamic_distilled_transformer",
                        torch_dtype=torch_dtype,
                        device_map="cpu",
                    )

                    if triton_is_available and torch.cuda.is_available():
                        transformer = apply_sdnq_options_to_model(transformer, use_quantized_matmul=True)

                    pipe = LTX2Pipeline.from_pretrained(
                        "rootonchair/LTX-2-19b-distilled",
                        custom_pipeline="multimodalart/ltx2-audio-to-video",
                        transformer=transformer,
                        torch_dtype=torch_dtype,
                        trust_remote_code=True,
                    )

                    lora_files = scene.lora_files
                    enabled_names = []
                    enabled_weights = []
                    enabled_items = [item for item in lora_files if item.enabled]

                    if enabled_items:
                        for item in enabled_items:
                            enabled_names.append(
                                (clean_filename(item.name)).replace(".", "")
                            )
                            enabled_weights.append(item.weight_value)
                            pipe.load_lora_weights(
                                bpy.path.abspath(scene.lora_folder),
                                weight_name=item.name + ".safetensors",
                                adapter_name=((clean_filename(item.name)).replace(".", "")),
                            )
                        pipe.set_adapters(enabled_names, adapter_weights=enabled_weights)
                        print("Load LoRAs: " + " ".join(enabled_names))

                    pipe.enable_group_offload(
                        onload_device=onload_device,
                        offload_device=offload_device,
                        offload_type="leaf_level",
                        low_cpu_mem_usage=True,
                    )

                    with torch.inference_mode():

                        kwargs = dict(
                            prompt_embeds=prompt_embeds.to(onload_device),
                            prompt_attention_mask=prompt_attention_mask.to(onload_device),
                            width=x,
                            height=y,
                            num_frames=num_frames,
                            frame_rate=fps,
                            num_inference_steps=8,
                            sigmas=DISTILLED_SIGMA_VALUES,
                            guidance_scale=1.0,
                            generator=generator,
                            output_type="latent",
                            return_dict=False,
                        )

                        if sound_path:
                            kwargs["audio"] = sound_path

                        if image is not None:
                            kwargs["image"] = image

                        outputs = pipe(**kwargs)

                    if isinstance(outputs, tuple):
                        video_latent = outputs[0]
                        audio_latent = outputs[1] if len(outputs) > 1 else None
                    else:
                        video_latent = outputs
                        audio_latent = None

                    video_latent = video_latent.detach().to(offload_device, copy=True)
                    if audio_latent is not None:
                        audio_latent = audio_latent.detach().to(offload_device, copy=True)

                    del pipe, transformer
                    cleanup()


                    # ==========================================================
                    # LATENT UPSCALE
                    # ==========================================================

                    latent_upsampler = LTX2LatentUpsamplerModel.from_pretrained(
                        "rootonchair/LTX-2-19b-distilled",
                        subfolder="latent_upsampler",
                        torch_dtype=torch_dtype,
                    ).to(onload_device)

                    vae = AutoencoderKLLTX2Video.from_pretrained(
                        MODEL_PATH,
                        subfolder="vae",
                        torch_dtype=torch_dtype,
                    ).to(onload_device)

                    upscale_pipe = LTX2LatentUpsamplePipeline(vae=vae, latent_upsampler=latent_upsampler)
                    upscale_pipe.enable_model_cpu_offload(device=onload_device)

                    with torch.inference_mode():
                        up_latent = upscale_pipe(
                            latents=video_latent,
                            output_type="latent",
                            return_dict=False,
                        )[0]

                    up_latent = up_latent.detach().to(offload_device, copy=True)

                    del upscale_pipe, latent_upsampler, vae, video_latent
                    cleanup()


                    # ==========================================================
                    # STAGE 2
                    # ==========================================================

                    transformer = LTX2VideoTransformer3DModel.from_pretrained(
                        "OzzyGT/LTX_2_SDNQ_4bit_dynamic_distilled_transformer",
                        torch_dtype=torch_dtype,
                        device_map="cpu",
                    )

                    if triton_is_available and torch.cuda.is_available():
                        transformer = apply_sdnq_options_to_model(transformer, use_quantized_matmul=True)

                    refine_pipe = LTX2Pipeline.from_pretrained(
                        MODEL_PATH,
                        transformer=transformer,
                        text_encoder=None,
                        torch_dtype=torch_dtype,
                    )

                    refine_pipe.scheduler = FlowMatchEulerDiscreteScheduler.from_config(
                        refine_pipe.scheduler.config,
                        use_dynamic_shifting=False,
                        shift_terminal=None,
                    )

                    adapter_name=((clean_filename("ltx-2-19b-ic-lora-detailer.safetensors")).replace(".", ""))
                    refine_pipe.load_lora_weights(
                        bpy.path.abspath("Lightricks/LTX-2-19b-IC-LoRA-Detailer"),
                        weight_name="ltx-2-19b-ic-lora-detailer.safetensors",
                        adapter_name=adapter_name,
                    )
                    refine_pipe.set_adapters(adapter_name)
                    print("Load LoRA: " + " ".join(adapter_name))

                    refine_pipe.enable_group_offload(
                        onload_device=onload_device,
                        offload_device=offload_device,
                        offload_type="leaf_level",
                        low_cpu_mem_usage=True,
                    )

                    refine_kwargs = dict(
                        latents=up_latent.to(onload_device),
                        prompt_embeds=prompt_embeds.to(onload_device),
                        prompt_attention_mask=prompt_attention_mask.to(onload_device),
                        width=x * 2,
                        height=y * 2,
                        num_frames=num_frames,
                        num_inference_steps=3,
                        sigmas=STAGE_2_DISTILLED_SIGMA_VALUES,
                        noise_scale=STAGE_2_DISTILLED_SIGMA_VALUES[0],
                        guidance_scale=1.0,
                        generator=generator,
                        output_type="latent",
                        return_dict=False,
                    )

                    if audio_latent is not None:
                        refine_kwargs["audio_latents"] = audio_latent.to(onload_device)

                    with torch.inference_mode():
                        outputs = refine_pipe(**refine_kwargs)

                    if isinstance(outputs, tuple):
                        final_v_latent = outputs[0]
                        final_a_latent = outputs[1] if len(outputs) > 1 else None
                    else:
                        final_v_latent = outputs
                        final_a_latent = None

                    del refine_pipe, transformer, up_latent, audio_latent
                    cleanup()


                    # ==========================================================
                    # DECODE (TILING RESTORED)
                    # ==========================================================

                    decode_pipe = LTX2Pipeline.from_pretrained(
                        MODEL_PATH,
                        text_encoder=None,
                        transformer=None,
                        scheduler=None,
                        connectors=None,
                        torch_dtype=torch_dtype,
                    )

                    decode_pipe.to(onload_device)

                    decode_pipe.vae.enable_tiling(
                        tile_sample_min_height=256,
                        tile_sample_min_width=256,
                        tile_sample_min_num_frames=16,
                        tile_sample_stride_height=192,
                        tile_sample_stride_width=192,
                        tile_sample_stride_num_frames=8,
                    )

                    decode_pipe.vae.use_framewise_encoding = True
                    decode_pipe.vae.use_framewise_decoding = True
                    decode_pipe.enable_model_cpu_offload()

                    with torch.inference_mode():

                        video = decode_pipe.vae.decode(
                            final_v_latent.to(onload_device, dtype=decode_pipe.vae.dtype),
                            None,
                            return_dict=False,
                        )[0]

                        video = decode_pipe.video_processor.postprocess_video(video, output_type="np")

                        audio_out = None
                        if final_a_latent is not None:
                            mel = decode_pipe.audio_vae.decode(
                                final_a_latent.to(onload_device, dtype=decode_pipe.audio_vae.dtype),
                                return_dict=False,
                            )[0]
                            audio_out = decode_pipe.vocoder(mel)

                    del decode_pipe
                    cleanup()


                    # ==========================================================
                    # SAVE
                    # ==========================================================

                    video_tensor = torch.from_numpy((video * 255).round().astype("uint8"))
                    #output_path = os.path.join(OUTPUT_FOLDER, f"ltx2_final_{seed}.mp4")
                    dst_path = solve_path(clean_filename(str(seed) + "_" + prompt) + ".mp4")

                    if audio_out is not None:
                        encode_video(
                            video_tensor[0],
                            fps=fps,
                            audio=audio_out[0].float().cpu(),
                            audio_sample_rate=24000,
                            output_path=dst_path,
                        )
                    else:
                        encode_video(
                            video_tensor[0],
                            fps=fps,
                            output_path=dst_path,
                        )

                    print(f"\nSaved to: {dst_path}")
                    print(f"Total time: {time.time() - total_start_time:.2f}s")                

                #Skyreel
                elif movie_model_card == "Skywork/SkyReels-V1-Hunyuan-T2V":
                    from diffusers.utils import load_image, export_to_video
                    if scene.movie_path:
                        print("Process: Video Image to Video (SkyReels-V1-Hunyuan-T2V)")
                        if not os.path.isfile(scene.movie_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_first_frame(bpy.path.abspath(scene.movie_path))
                    if scene.image_path:
                        print("Process: Image to video (SkyReels-V1-Hunyuan-T2V)")
                        strip = scene.sequence_editor.active_strip
                        img_path = os.path.join(bpy.path.abspath(strip.directory), strip.elements[0].filename)
                        print("Path: "+img_path)
                        if not os.path.isfile(img_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_image(img_path)
                    #                    image = image.resize(
                    #                        (closest_divisible_32(int(x)), closest_divisible_32(int(y)))
                    #                    )
                    video_frames = pipe(
                        image=image,
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        num_inference_steps=movie_num_inference_steps,
                        guidance_scale=movie_num_guidance,
                        height=y,
                        width=x,
                        num_frames=abs(duration),
                        generator=generator,
                        max_sequence_length=512,
                    ).frames[0]

                elif movie_model_card == "hunyuanvideo-community/HunyuanVideo":

                    from diffusers.utils import load_image, export_to_video
                    import os
                    if scene.movie_path:
                        print("Process: Video Image to Video (Hunyuan-I2V)")
                        if not os.path.isfile(scene.movie_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_first_frame(bpy.path.abspath(scene.movie_path))
                    if scene.image_path:
                        print("Process: Image to video (Hunyuan-I2V)")
                        strip = scene.sequence_editor.active_strip
                        img_path = os.path.join(bpy.path.abspath(strip.directory), strip.elements[0].filename)
                        if not os.path.isfile(img_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_image(img_path)
                    #                    image = image.resize(
                    #                        (closest_divisible_32(int(x)), closest_divisible_32(int(y)))
                    #                    )
                    video_frames = pipe(
                        image=image,
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        num_inference_steps=movie_num_inference_steps,
                        guidance_scale=movie_num_guidance,
                        height=y,
                        width=x,
                        num_frames=abs(duration),
                        generator=generator,
                        max_sequence_length=512,
                    ).frames[0]

                elif movie_model_card == "lllyasviel/FramePackI2V_HY":
                    from diffusers.utils import load_image, export_to_video
                    if scene.movie_path:
                        print("Process: Video Image to Video (FramePack)")
                        if not os.path.isfile(scene.movie_path):
                            print("No file found.")
                            return {"CANCELLED"}

                        #from diffusers.utils import load_video
                        #image=load_video(bpy.path.abspath(scene.movie_path))

                        image = load_first_frame(bpy.path.abspath(scene.movie_path))
                    if scene.image_path:
                        print("Process: Image to video (FramePack)")
                        strip = scene.sequence_editor.active_strip
                        img_path = os.path.join(bpy.path.abspath(strip.directory), strip.elements[0].filename)
                        if not os.path.isfile(img_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_image(img_path)
#                        image = image.resize(
#                            (closest_divisible_32(int(x)), closest_divisible_32(int(y)))
#                        )

                    if scene.out_frame:
                        subject_strip = find_strip_by_name(scene, scene.out_frame)
                        print("image_strip from find_strip_by_name:", subject_strip)

                        if subject_strip.type == "IMAGE":
                            print("image_strip type is IMAGE")
                            image_path_chk = bpy.path.abspath(
                                os.path.join(
                                    subject_strip.directory,
                                    subject_strip.elements[0].filename,
                                )
                            )
                            if not os.path.isfile(bpy.path.abspath(image_path_chk)):
                                print("No End Frame file found.")
                                return {"CANCELLED"}
                            else:
                                print("Load image path: "+bpy.path.abspath(image_path_chk))
                                last_image = load_image(bpy.path.abspath(image_path_chk))
                                last_image = last_image.resize(image.size)
                                print("Last Frame loaded.")
                        else:
                            print("image_strip type is not IMAGE:", image_strip.type)
                            return {"CANCELLED"}
                    else:
                        last_image = None

                    video_frames = pipe(
                        image=image,
                        last_image=last_image,
                        prompt=prompt,
                        #negative_prompt=negative_prompt,
                        num_inference_steps=movie_num_inference_steps,
                        guidance_scale=movie_num_guidance,
                        height=y,
                        width=x,
                        num_frames=abs(duration),
                        generator=generator,
                        sampling_type="vanilla",
                        #max_sequence_length=512,
                    ).frames[0]

                elif movie_model_card == "Wan-AI/Wan2.2-I2V-A14B-Diffusers":
                    from diffusers.utils import load_image, export_to_video
                    import os
                    import numpy as np
                    if scene.movie_path:
                        print("Process: Video Image to Video (Wan-AI/Wan2.2-I2V-A14B-Diffusers)")
                        if not os.path.isfile(scene.movie_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_first_frame(bpy.path.abspath(scene.movie_path))
                    if scene.image_path:
                        print("Process: Image to video (Wan-AI/Wan2.2-I2V-A14B-Diffusers)")
                        strip = scene.sequence_editor.active_strip
                        img_path = os.path.join(bpy.path.abspath(strip.directory), strip.elements[0].filename)
                        if not os.path.isfile(img_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_image(img_path)
                        
                    def resize_for_wan(image, max_dim=832):
                        w, h = image.size
                        scale = max_dim / max(w, h)
                        new_w = int(w * scale)
                        new_h = int(h * scale)
                        # Round to nearest 16
                        new_w = (new_w // 16) * 16
                        new_h = (new_h // 16) * 16
                        return image.resize((new_w, new_h), Image.LANCZOS)

                    image = resize_for_wan(image)                        
                    import gc
                    gc.collect()
                    torch.cuda.empty_cache()
                    video_frames = pipe(
                        image=image,
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        num_frames=abs(duration),
                        num_inference_steps=8,
                        guidance_scale=1.0,
                        guidance_scale_2=1.0,
                        height=y,
                        width=x,
                        generator=generator,
                        max_sequence_length=512,
                    ).frames[0]
                elif movie_model_card == "Wan-AI/Wan2.1-VACE-1.3B-diffusers" and input == "input_strips":
                    from diffusers.utils import load_image, export_to_video
                    import numpy as np
                    import PIL
                    if scene.movie_path:
                        print("Process: Video Image to Video (Wan2.1-I2V-14B-480P-Diffusers)")
                        if not os.path.isfile(scene.movie_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_first_frame(bpy.path.abspath(scene.movie_path))
                    if scene.image_path:
                        print("Process: Image to video (Wan2.1-I2V-14B-480P-Diffusers)")
                        strip = scene.sequence_editor.active_strip
                        img_path = os.path.join(bpy.path.abspath(strip.directory), strip.elements[0].filename)
                        if not os.path.isfile(img_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_image(img_path)

                        img = image.resize((x, y))
                        frames = [img]
                        # Ideally, this should be 127.5 to match original code, but they perform computation on numpy arrays
                        # whereas we are passing PIL images. If you choose to pass numpy arrays, you can set it to 127.5 to
                        # match the original code.
                        frames.extend([PIL.Image.new("RGB", (x, y), (128, 128, 128))] * (abs(duration) - 1))
                        mask_black = PIL.Image.new("L", (x, y), 0)
                        mask_white = PIL.Image.new("L", (x, y), 255)
                        mask = [mask_black, *[mask_white] * (abs(duration) - 1)]

                    video_frames = pipe(
                        video=frames,
                        mask=mask,
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        num_inference_steps=movie_num_inference_steps,
                        guidance_scale=movie_num_guidance,
                        height=y,
                        width=x,
                        #num_frames=abs(duration),
                        generator=generator,
                        max_sequence_length=512,
                    ).frames[0]
                elif (
                    movie_model_card != "Hailuo/MiniMax/txt2vid"
                    and movie_model_card != "Hailuo/MiniMax/img2vid"
                    and movie_model_card != "Hailuo/MiniMax/subject2vid"
                    #and movie_model_card != "Wan-AI/Wan2.2-T2V-A14B-Diffusers"
                ): #something is broken here?
                    from diffusers.utils import load_image, export_to_video
                    if scene.movie_path:
                        print("Process: Video to video")
                        if not os.path.isfile(scene.movie_path):
                            print("No file found.")
                            return {"CANCELLED"}
                    elif scene.image_path:
                        print("Process: Image to video")
                        strip = scene.sequence_editor.active_strip
                        img_path = os.path.join(bpy.path.abspath(strip.directory), strip.elements[0].filename)
                        if not os.path.isfile(img_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_image(img_path)

                    video = load_video_as_np_array(video_path)
                    video = process_image(
                        scene.image_path, int(scene.generate_movie_frames)
                    )
                    video = np.array(video)

                    # Upscale video
                    if scene.video_to_video:
                        video = [
                            Image.fromarray(frame).resize(
                                (
                                    closest_divisible_32(int(x * 2)),
                                    closest_divisible_32(int(y * 2)),
                                )
                            )
                            for frame in video
                        ]
                    else:
                        video = [
                            Image.fromarray(frame).resize(
                                (
                                    closest_divisible_32(int(x)),
                                    closest_divisible_32(int(y)),
                                )
                            )
                            for frame in video
                        ]
                    video_frames = upscale(
                        prompt,
                        video=video,
                        strength=1.00 - scene.image_power,
                        negative_prompt=negative_prompt,
                        num_inference_steps=movie_num_inference_steps,
                        guidance_scale=movie_num_guidance,
                        generator=generator,
                    ).frames[0]

                elif movie_model_card == "Wan-AI/Wan2.2-T2V-A14B-Diffusers":
                    if (scene.movie_path or scene.image_path) and input == "input_strips":
                        print("Wan2.1-T2V doesn't support img/vid2vid!")
                        return {"CANCELLED"}


            # Prompt input for movies
            elif (
                movie_model_card != "Hailuo/MiniMax/txt2vid"
                and movie_model_card != "Hailuo/MiniMax/img2vid"
                and movie_model_card != "Hailuo/MiniMax/subject2vid"
            ):
                print("Generate: Video from text")

                if (
                    movie_model_card == "THUDM/CogVideoX-5b"
                    or movie_model_card == "THUDM/CogVideoX-2b"
                ):
                    video_frames = pipe(
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        num_inference_steps=movie_num_inference_steps,
                        guidance_scale=movie_num_guidance,
                        num_videos_per_prompt=1,
                        height=480,
                        width=720,
                        #
                        num_frames=abs(duration),
                        generator=generator,
                    ).frames[0]
                    scene.generate_movie_x = 720
                    scene.generate_movie_y = 480

                # HunyuanVideo
                elif movie_model_card == "hunyuanvideo-community/HunyuanVideo":
                    video_frames = pipe(
                        prompt=prompt,
                        # negative_prompt=negative_prompt,
                        num_inference_steps=movie_num_inference_steps,
                        guidance_scale=movie_num_guidance,
                        num_videos_per_prompt=1,
                        height=y,
                        width=x,
                        num_frames=abs(duration),
                        generator=generator,
                    ).frames[0]
                # FramePack
                elif movie_model_card == "lllyasviel/FramePackI2V_HY":
                    video_frames = pipe(
                        prompt=prompt,
                        # negative_prompt=negative_prompt,
                        num_inference_steps=movie_num_inference_steps,
                        guidance_scale=movie_num_guidance,
                        num_videos_per_prompt=1,
                        height=y,
                        width=x,
                        num_frames=abs(duration),
                        generator=generator,
                    ).frames[0]
                # Skyreel
                elif movie_model_card == "Skywork/SkyReels-V1-Hunyuan-T2V":
                    video_frames = pipe(
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        num_inference_steps=movie_num_inference_steps,
                        guidance_scale=movie_num_guidance,
                        height=y,
                        width=x,
                        num_frames=abs(duration),
                        generator=generator,
                        max_sequence_length=512,
                        #true_cfg_scale=6.0,
                        # use_dynamic_cfg=True,
                    ).frames[0]
                # Wan t2i
                elif movie_model_card == "Wan-AI/Wan2.2-T2V-A14B-Diffusers":
                    video_frames = pipe(
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        num_inference_steps=8,
                        guidance_scale=1.0,
                        height=y,
                        width=x,
                        num_frames=abs(duration),
                        generator=generator,
                        max_sequence_length=256,
                    ).frames[0]
                    
                elif movie_model_card == "Lightricks/LTX-2":
                    import gc
                    import os
                    import time
                    import torch
                    import cv2
                    import numpy as np
                    from diffusers import LTX2Pipeline, LTX2LatentUpsamplePipeline, LTX2VideoTransformer3DModel
                    from diffusers.pipelines.ltx2.export_utils import encode_video
                    from diffusers.pipelines.ltx2.latent_upsampler import LTX2LatentUpsamplerModel
                    from diffusers.pipelines.ltx2.utils import DISTILLED_SIGMA_VALUES, STAGE_2_DISTILLED_SIGMA_VALUES
                    from diffusers.utils import load_image
                    from transformers import Gemma3ForConditionalGeneration
                        
                    render = bpy.context.scene.render
                    fps = round((render.fps / render.fps_base), 3) 
 
                    x = width_clean = (x // 32) * 32
                    y = height_clean = (y // 32) * 32
                    
                    # 2. Ensure frame count follows (n * 8) + 1 rule (Temporal Requirement)
                    # LTX-2 VAE compresses time by 8x. Arbitrary frame counts cause tensor mismatch.
                    target_frames = abs(duration)
                    duration = valid_num_frames = ((target_frames - 1) // 8) * 8 + 1
                    
                    # Ensure a minimum valid length (9 frames is the smallest block: 1*8 + 1)
                    if valid_num_frames < 9:
                        duration = valid_num_frames = 9
                        

                    # Start global timer
                    total_start_time = time.time()

                    torch_dtype = torch.bfloat16
                    device = "cuda"
                    model_path = "Lightricks/LTX-2"

                    seed = int.from_bytes(os.urandom(8), "big")
                    generator = torch.Generator("cpu").manual_seed(seed)

                    print("Loading base models...")
                    load_start = time.time()

                    text_encoder = Gemma3ForConditionalGeneration.from_pretrained(
                        "OzzyGT/LTX-2-bnb-4bit-text-encoder",
                        dtype=torch_dtype,
                        device_map="cpu",
                    )

                    transformer = LTX2VideoTransformer3DModel.from_pretrained(
                        "OzzyGT/LTX-2-bnb-4bit-transformer-distilled",
                        torch_dtype=torch_dtype,
                        device_map="cpu",
                    )

                    pipe = LTX2Pipeline.from_pretrained(
                        model_path, transformer=transformer, text_encoder=text_encoder, torch_dtype=torch_dtype
                    )
                    
                    lora_files = scene.lora_files
                    enabled_names = []
                    enabled_weights = []
                    enabled_items = [item for item in lora_files if item.enabled]

                    if enabled_items:
                        for item in enabled_items:
                            enabled_names.append(
                                (clean_filename(item.name)).replace(".", "")
                            )
                            enabled_weights.append(item.weight_value)
                            pipe.load_lora_weights(
                                bpy.path.abspath(scene.lora_folder),
                                weight_name=item.name + ".safetensors",
                                adapter_name=((clean_filename(item.name)).replace(".", "")),
                            )
                        pipe.set_adapters(enabled_names, adapter_weights=enabled_weights)
                        print("Load LoRAs: " + " ".join(enabled_names))                    
                    
                    pipe.vae.enable_tiling(
                        tile_sample_min_height=256,
                        tile_sample_min_width=256,
                        tile_sample_min_num_frames=16,
                        tile_sample_stride_height=192,
                        tile_sample_stride_width=192,
                        tile_sample_stride_num_frames=8,
                    )
                    pipe.vae.use_framewise_encoding = True
                    pipe.vae.use_framewise_decoding = True
                    pipe.enable_model_cpu_offload()

                    torch.cuda.synchronize()
                    print(f"Base models loaded in: {time.time() - load_start:.2f} seconds")

                    frame_rate = 24.0

                    print("Starting base generation (Stage 1)...")
                    base_gen_start = time.time()

                    video_latent, audio_latent = pipe(
                        prompt=prompt,
                        width=x,
                        height=y,
                        num_frames=duration,
                        frame_rate=frame_rate,
                        num_inference_steps=8,
                        sigmas=DISTILLED_SIGMA_VALUES,
                        guidance_scale=1.0,
                        generator=generator,
                        output_type="latent",
                        return_dict=False,
                    )

                    torch.cuda.synchronize()
                    print(f"Base generation finished in: {time.time() - base_gen_start:.2f} seconds")

                    print("Loading Latent Upsampler...")
                    upsampler_load_start = time.time()

                    latent_upsampler = LTX2LatentUpsamplerModel.from_pretrained(
                        "rootonchair/LTX-2-19b-distilled",
                        subfolder="latent_upsampler",
                        torch_dtype=torch_dtype,
                    )
                    upsample_pipe = LTX2LatentUpsamplePipeline(vae=pipe.vae, latent_upsampler=latent_upsampler)
                    upsample_pipe.enable_model_cpu_offload(device=device)

                    torch.cuda.synchronize()
                    print(f"Latent Upsampler loaded in: {time.time() - upsampler_load_start:.2f} seconds")

                    print("Starting Latent Upscaling...")
                    upscale_start = time.time()

                    upscaled_video_latent = upsample_pipe(
                        latents=video_latent,
                        output_type="latent",
                        return_dict=False,
                    )[0]

                    torch.cuda.synchronize()
                    print(f"Latent Upscaling finished in: {time.time() - upscale_start:.2f} seconds")

                    print("Cleaning up memory...")
                    cleanup_start = time.time()

                    latent_upsampler.to("cpu")
                    del video_latent
                    del upsample_pipe
                    del latent_upsampler
                    gc.collect()
                    torch.cuda.empty_cache()

                    torch.cuda.synchronize()
                    print(f"Memory cleanup finished in: {time.time() - cleanup_start:.2f} seconds")

                    print("Starting Stage 2 Generation (High-Res Decoding)...")
                    stage2_start = time.time()

                    video, audio = pipe(
                        latents=upscaled_video_latent,
                        audio_latents=audio_latent,
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        width = x * 2,
                        height = y * 2,
                        num_frames=duration,
                        num_inference_steps=3,
                        noise_scale=STAGE_2_DISTILLED_SIGMA_VALUES[0],
                        sigmas=STAGE_2_DISTILLED_SIGMA_VALUES,
                        generator=generator,
                        guidance_scale=1.0,
                        output_type="np",
                        return_dict=False,
                    )

                    torch.cuda.synchronize()
                    print(f"Stage 2 Generation finished in: {time.time() - stage2_start:.2f} seconds")
                    
                    # --- PURE RESTORATION PIPELINE (NO GRADING) ---
                    print("Applying clean restoration...")
                    post_proc_start = time.time()

                    video_np = (video[0] * 255).round().astype("uint8")
                    processed_frames = []

                    for frame in video_np:

                        # -------------------------------------------------
                        # 1 Recover Highlights (Soft Compression)
                        # -------------------------------------------------
                        frame_f = frame.astype(np.float32) / 255.0

                        # Compress only upper range
                        highlight_mask = frame_f > 0.85
                        frame_f[highlight_mask] = 0.85 + (frame_f[highlight_mask] - 0.85) * 0.5

                        restored = np.clip(frame_f * 255, 0, 255).astype(np.uint8)

                        # -------------------------------------------------
                        # 2 Mild Edge-Preserving Denoise
                        # -------------------------------------------------
                        denoised = cv2.bilateralFilter(
                            restored,
                            d=5,
                            sigmaColor=30,
                            sigmaSpace=30
                        )

                        # -------------------------------------------------
                        # 3 Advanced Face-Safe Detail Restoration
                        # -------------------------------------------------

                        # Convert to float
                        frame_f = denoised.astype(np.float32) / 255.0

                        # --- Band 1: Micro detail (small radius) ---
                        blur_small = cv2.GaussianBlur(frame_f, (0, 0), 0.6)
                        micro = frame_f - blur_small
                        micro_boost = frame_f + micro * 0.8

                        # --- Band 2: Structure detail (larger radius) ---
                        blur_large = cv2.GaussianBlur(frame_f, (0, 0), 2.0)
                        structure = frame_f - blur_large
                        structure_boost = micro_boost + structure * 0.4

                        # Clip safely
                        restored = np.clip(structure_boost, 0, 1)

                        final_frame = (restored * 255).astype(np.uint8)


                        processed_frames.append(final_frame)

                    video_processed = np.stack(processed_frames)
                    video_final = torch.from_numpy(video_processed)

                    print(f"Restoration finished in: {time.time() - post_proc_start:.2f} seconds")

                    print("Processing and exporting video...")
                    export_start = time.time()

                    dst_path = solve_path(clean_filename(str(seed) + "_" + prompt) + ".mp4")
                    
                    encode_video(
                        video_final,
                        fps=frame_rate,
                        audio=audio[0].float().cpu(),
                        audio_sample_rate=pipe.vocoder.config.output_sampling_rate,
                        output_path=dst_path,
                    )

                    print(f"Export finished in: {time.time() - export_start:.2f} seconds")
                    print(f"Total script execution time: {time.time() - total_start_time:.2f} seconds")

                #LTX2-Multifile
                elif movie_model_card == "LTX-2 Multi-Input File":

                    import gc
                    import os
                    import time
                    #import torch
                    import wave
                    from PIL import Image
                    from diffusers.utils import load_image
                    from diffusers.utils import load_video
                    from diffusers import (
                        AutoencoderKLLTX2Video,
                        LTX2LatentUpsamplePipeline,
                        LTX2Pipeline,
                        LTX2VideoTransformer3DModel,
                    )
                    from diffusers.pipelines.ltx2.export_utils import encode_video
                    from diffusers.pipelines.ltx2.latent_upsampler import LTX2LatentUpsamplerModel
                    from diffusers.pipelines.ltx2.utils import (
                        DISTILLED_SIGMA_VALUES,
                        STAGE_2_DISTILLED_SIGMA_VALUES,
                    )
                    from diffusers.schedulers import FlowMatchEulerDiscreteScheduler

                    from sdnq.common import use_torch_compile as triton_is_available
                    from sdnq.loader import apply_sdnq_options_to_model
                    from transformers import Gemma3ForConditionalGeneration

                    # ==========================================================
                    # CLEANUP
                    # ==========================================================

                    def cleanup():
                        gc.collect()
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                    cleanup()

                    # ==========================================================
                    # SETTINGS
                    # ==========================================================

                    MODEL_PATH = "Lightricks/LTX-2"
                    #MODEL_PATH = "OzzyGT/tiny_LTX2"
                    #MODEL_PATH = "OzzyGT/LTX2_distilled_SDNQ_4bit_dynamic"

                    DEFAULT_NUM_FRAMES = 121
                    num_frames = DEFAULT_NUM_FRAMES

                    torch_dtype = torch.bfloat16
                    onload_device = torch.device("cuda")
                    offload_device = torch.device("cpu")

                    total_start_time = time.time()

                    # ==========================================================
                    # INPUT DETECTION
                    # ==========================================================

                    image = None
                    sound_path = None

#                    render = bpy.context.scene.render
#                    fps = round((render.fps / render.fps_base), 3) 
                    fps=24.0
                    
                    if scene.movie_path:
                        print("Process: Video Image to Video")
                        if not os.path.isfile(scene.movie_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_first_frame(bpy.path.abspath(scene.movie_path)).convert("RGB")
                    if scene.image_path:
                        print("Process: Image to video")
                        strip = scene.sequence_editor.active_strip
                        img_path = os.path.join(bpy.path.abspath(strip.directory), strip.elements[0].filename)
                        if not os.path.isfile(img_path):
                            print("No file found.")
                            return {"CANCELLED"}
                        image = load_image(img_path).convert("RGB")

                    if scene.sound_path:
                        print("Process: Sound to Video")
                        if not os.path.isfile(bpy.path.abspath(scene.sound_path)):
                            print("No file found.")
                            return {"CANCELLED"}
                        sound_path = bpy.path.abspath(scene.sound_path)
                        
                    # MISSING: make a trimmed temp file
                    def get_wav_duration(path):
                        import soundfile as sf
                        try:
                            info = sf.info(path)
                            return info.frames / info.samplerate
                        except Exception as e:
                            print("Duration detection failed:", e)
                            return DEFAULT_NUM_FRAMES / fps

                    if sound_path:
                        duration = get_wav_duration(sound_path)

                        raw_frames = duration * fps
                        multiple_of_8 = int((raw_frames + 7) // 8) * 8
                        num_frames = multiple_of_8 + 1

                        print(f"Audio duration: {duration:.2f}s")
                    else:
                        # 2. Ensure frame count follows (n * 8) + 1 rule (Temporal Requirement)
                        # LTX-2 VAE compresses time by 8x. Arbitrary frame counts cause tensor mismatch.
                        target_frames = abs(duration)
                        num_frames = valid_num_frames = ((target_frames - 1) // 8) * 8 + 1
                        
                        # Ensure a minimum valid length (9 frames is the smallest block: 1*8 + 1)
                        if valid_num_frames < 9:
                            num_frames = valid_num_frames = 9

                    print(f"Image enabled: {image is not None}")
                    print(f"Audio enabled: {sound_path is not None}")
                    print(f"Frames: {num_frames}")

                    if image is None:
                        image = Image.new("RGB", (x, y), (0, 0, 0))
                        
                    cleanup()

                    # ==========================================================
                    # TEXT ENCODING
                    # ==========================================================

                    text_encoder = Gemma3ForConditionalGeneration.from_pretrained(
                        "OzzyGT/LTX-2-bnb-8bit-text-encoder",
                        dtype=torch_dtype,
                    )

                    embeds_pipe = LTX2Pipeline.from_pretrained(
                        MODEL_PATH,
                        text_encoder=text_encoder,
                        transformer=None,
                        vae=None,
                        audio_vae=None,
                        vocoder=None,
                        scheduler=None,
                        connectors=None,
                        torch_dtype=torch_dtype,
                    )

                    embeds_pipe.enable_sequential_cpu_offload()
                    #embeds_pipe.enable_model_cpu_offload()

                    with torch.inference_mode():
                        prompt_embeds, prompt_attention_mask, _, _ = embeds_pipe.encode_prompt(
                            prompt, negative_prompt, do_classifier_free_guidance=False
                        )

                    prompt_embeds = prompt_embeds.detach().to(offload_device, copy=True)
                    prompt_attention_mask = prompt_attention_mask.detach().to(offload_device, copy=True)

                    del embeds_pipe, text_encoder
                    cleanup()

                    # ==========================================================
                    # STAGE 1
                    # ==========================================================

                    transformer = LTX2VideoTransformer3DModel.from_pretrained(
                        "OzzyGT/LTX_2_SDNQ_4bit_dynamic_distilled_transformer",
                        torch_dtype=torch_dtype,
                        device_map="cpu",
                    )

                    if triton_is_available and torch.cuda.is_available():
                        transformer = apply_sdnq_options_to_model(transformer, use_quantized_matmul=True)

                    pipe = LTX2Pipeline.from_pretrained(
                        "rootonchair/LTX-2-19b-distilled",
                        custom_pipeline="multimodalart/ltx2-audio-to-video",
                        transformer=transformer,
                        torch_dtype=torch_dtype,
                        trust_remote_code=True,
                    )

                    lora_files = scene.lora_files
                    enabled_names = []
                    enabled_weights = []
                    enabled_items = [item for item in lora_files if item.enabled]
                    
                    if enabled_items:
                        for item in enabled_items:
                            enabled_names.append(
                                (clean_filename(item.name)).replace(".", "")
                            )
                            enabled_weights.append(item.weight_value)
                            pipe.load_lora_weights(
                                bpy.path.abspath(scene.lora_folder),
                                weight_name=item.name + ".safetensors",
                                adapter_name=((clean_filename(item.name)).replace(".", "")),
                            )
                        pipe.set_adapters(enabled_names, adapter_weights=enabled_weights)
                        print("Load LoRAs: " + " ".join(enabled_names))

                    pipe.enable_group_offload(
                        onload_device=onload_device,
                        offload_device=offload_device,
                        offload_type="leaf_level",
                        low_cpu_mem_usage=True,
                    )

                    with torch.inference_mode():

                        kwargs = dict(
                            prompt_embeds=prompt_embeds.to(onload_device),
                            prompt_attention_mask=prompt_attention_mask.to(onload_device),
                            width=x,
                            height=y,
                            num_frames=num_frames,
                            frame_rate=fps,
                            num_inference_steps=8,
                            sigmas=DISTILLED_SIGMA_VALUES,
                            guidance_scale=1.0,
                            generator=generator,
                            output_type="latent",
                            return_dict=False,
                        )

                        if sound_path:
                            kwargs["audio"] = sound_path

                        if image is not None:
                            kwargs["image"] = image

                        outputs = pipe(**kwargs)

                    if isinstance(outputs, tuple):
                        video_latent = outputs[0]
                        audio_latent = outputs[1] if len(outputs) > 1 else None
                    else:
                        video_latent = outputs
                        audio_latent = None

                    video_latent = video_latent.detach().to(offload_device, copy=True)
                    if audio_latent is not None:
                        audio_latent = audio_latent.detach().to(offload_device, copy=True)

                    del pipe, transformer
                    cleanup()


                    # ==========================================================
                    # LATENT UPSCALE
                    # ==========================================================

                    latent_upsampler = LTX2LatentUpsamplerModel.from_pretrained(
                        "rootonchair/LTX-2-19b-distilled",
                        subfolder="latent_upsampler",
                        torch_dtype=torch_dtype,
                    ).to(onload_device)

                    vae = AutoencoderKLLTX2Video.from_pretrained(
                        MODEL_PATH,
                        subfolder="vae",
                        torch_dtype=torch_dtype,
                    ).to(onload_device)

                    upscale_pipe = LTX2LatentUpsamplePipeline(vae=vae, latent_upsampler=latent_upsampler)
                    upscale_pipe.enable_model_cpu_offload(device=onload_device)

                    with torch.inference_mode():
                        up_latent = upscale_pipe(
                            latents=video_latent,
                            output_type="latent",
                            return_dict=False,
                        )[0]

                    up_latent = up_latent.detach().to(offload_device, copy=True)

                    del upscale_pipe, latent_upsampler, vae, video_latent
                    cleanup()


                    # ==========================================================
                    # STAGE 2
                    # ==========================================================

                    transformer = LTX2VideoTransformer3DModel.from_pretrained(
                        "OzzyGT/LTX_2_SDNQ_4bit_dynamic_distilled_transformer",
                        torch_dtype=torch_dtype,
                        device_map="cpu",
                    )

                    if triton_is_available and torch.cuda.is_available():
                        transformer = apply_sdnq_options_to_model(transformer, use_quantized_matmul=True)

                    refine_pipe = LTX2Pipeline.from_pretrained(
                        MODEL_PATH,
                        transformer=transformer,
                        text_encoder=None,
                        torch_dtype=torch_dtype,
                    )

                    refine_pipe.scheduler = FlowMatchEulerDiscreteScheduler.from_config(
                        refine_pipe.scheduler.config,
                        use_dynamic_shifting=False,
                        shift_terminal=None,
                    )

                    adapter_name=((clean_filename("ltx-2-19b-ic-lora-detailer.safetensors")).replace(".", ""))
                    refine_pipe.load_lora_weights(
                        bpy.path.abspath("Lightricks/LTX-2-19b-IC-LoRA-Detailer"),
                        weight_name="ltx-2-19b-ic-lora-detailer.safetensors",
                        adapter_name=adapter_name,
                    )
                    refine_pipe.set_adapters(adapter_name)
                    print("Load LoRA: " + " ".join(adapter_name))

                    refine_pipe.enable_group_offload(
                        onload_device=onload_device,
                        offload_device=offload_device,
                        offload_type="leaf_level",
                        low_cpu_mem_usage=True,
                    )

                    refine_kwargs = dict(
                        latents=up_latent.to(onload_device),
                        prompt_embeds=prompt_embeds.to(onload_device),
                        prompt_attention_mask=prompt_attention_mask.to(onload_device),
                        width=x * 2,
                        height=y * 2,
                        num_frames=num_frames,
                        num_inference_steps=3,
                        sigmas=STAGE_2_DISTILLED_SIGMA_VALUES,
                        noise_scale=STAGE_2_DISTILLED_SIGMA_VALUES[0],
                        guidance_scale=1.0,
                        generator=generator,
                        output_type="latent",
                        return_dict=False,
                    )

                    if audio_latent is not None:
                        refine_kwargs["audio_latents"] = audio_latent.to(onload_device)

                    with torch.inference_mode():
                        outputs = refine_pipe(**refine_kwargs)

                    if isinstance(outputs, tuple):
                        final_v_latent = outputs[0]
                        final_a_latent = outputs[1] if len(outputs) > 1 else None
                    else:
                        final_v_latent = outputs
                        final_a_latent = None

                    del refine_pipe, transformer, up_latent, audio_latent
                    cleanup()


                    # ==========================================================
                    # DECODE
                    # ==========================================================

                    decode_pipe = LTX2Pipeline.from_pretrained(
                        MODEL_PATH,
                        text_encoder=None,
                        transformer=None,
                        scheduler=None,
                        connectors=None,
                        torch_dtype=torch_dtype,
                    )

                    decode_pipe.to(onload_device)

                    decode_pipe.vae.enable_tiling(
                        tile_sample_min_height=256,
                        tile_sample_min_width=256,
                        tile_sample_min_num_frames=16,
                        tile_sample_stride_height=192,
                        tile_sample_stride_width=192,
                        tile_sample_stride_num_frames=8,
                    )

                    decode_pipe.vae.use_framewise_encoding = True
                    decode_pipe.vae.use_framewise_decoding = True
                    decode_pipe.enable_model_cpu_offload()

                    with torch.inference_mode():

                        video = decode_pipe.vae.decode(
                            final_v_latent.to(onload_device, dtype=decode_pipe.vae.dtype),
                            None,
                            return_dict=False,
                        )[0]

                        video = decode_pipe.video_processor.postprocess_video(video, output_type="np")

                        audio_out = None
                        if final_a_latent is not None:
                            mel = decode_pipe.audio_vae.decode(
                                final_a_latent.to(onload_device, dtype=decode_pipe.audio_vae.dtype),
                                return_dict=False,
                            )[0]
                            audio_out = decode_pipe.vocoder(mel)

                    del decode_pipe
                    cleanup()


                    # ==========================================================
                    # SAVE
                    # ==========================================================

                    video_tensor = torch.from_numpy((video * 255).round().astype("uint8"))
                    #output_path = os.path.join(OUTPUT_FOLDER, f"ltx2_final_{seed}.mp4")
                    dst_path = solve_path(clean_filename(str(seed) + "_" + prompt) + ".mp4")

                    if audio_out is not None:
                        encode_video(
                            video_tensor[0],
                            fps=fps,
                            audio=audio_out[0].float().cpu(),
                            audio_sample_rate=24000,
                            output_path=dst_path,
                        )
                    else:
                        encode_video(
                            video_tensor[0],
                            fps=fps,
                            output_path=dst_path,
                        )

                    print(f"\nSaved to: {dst_path}")
                    print(f"Total time: {time.time() - total_start_time:.2f}s")
                    
                else:
                    video_frames = pipe(
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        num_inference_steps=movie_num_inference_steps,
                        guidance_scale=movie_num_guidance,
                        height=y,
                        width=x,
                        num_frames=abs(duration),
                        generator=generator,
                        max_sequence_length=256,
                    ).frames[0]
                movie_model_card = addon_prefs.movie_model_card

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

                # Upscale video.
                if scene.video_to_video:
                    print("Upscale: Video")
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                    video = [
                        Image.fromarray(frame).resize(
                            (closest_divisible_32(x * 2), closest_divisible_32(y * 2))
                        )
                        for frame in video_frames
                    ]
                    video_frames = upscale(
                        prompt,
                        video=video,
                        strength=1.00 - scene.image_power,
                        negative_prompt=negative_prompt,
                        num_inference_steps=movie_num_inference_steps,
                        guidance_scale=movie_num_guidance,
                        generator=generator,
                    ).frames[0]

            # MiniMax
            if (
                movie_model_card == "Hailuo/MiniMax/txt2vid"
                or movie_model_card == "Hailuo/MiniMax/img2vid"
                or movie_model_card == "Hailuo/MiniMax/subject2vid"
            ):
                current_dir = os.path.dirname(__file__)
                init_file_path = os.path.join(current_dir, "MiniMax_API.txt")
                api_key = str(read_file(init_file_path))
                if api_key == "":
                    print("API key is missing!")
                    return {"CANCELLED"}

                image_path = None

                if movie_model_card == "Hailuo/MiniMax/img2vid":
                    if scene.image_path and minimax_validate_image(
                        bpy.path.abspath(scene.image_path)
                    ):
                        image_path = bpy.path.abspath(scene.image_path)
                        print("Image Path: " + image_path)
                    else:
                        print("Image path not found: " + bpy.path.abspath(scene.image_path))
                        return {"CANCELLED"}

                elif movie_model_card == "Hailuo/MiniMax/subject2vid":
                    print("Entered movie_model_card == 'Hailuo/MiniMax/subject2vid'")
                    print("scene.minimax_subject:", scene.minimax_subject)

                    if scene.minimax_subject:
                        subject_strip = find_strip_by_name(scene, scene.minimax_subject)
                        print("image_strip from find_strip_by_name:", subject_strip)

                        if subject_strip.type == "IMAGE":
                            print("image_strip type is IMAGE")
                            image_path_chk = bpy.path.abspath(
                                os.path.join(
                                    subject_strip.directory,
                                    subject_strip.elements[0].filename,
                                )
                            )
                            # subject_strip = bpy.path.abspath(get_render_strip(self, context, subject_strip))
                            print("image_strip after get_render_strip:", image_path_chk)

                            # image_path_chk = bpy.path.abspath(get_strip_path(image_strip))
                            # print("image_path_chk (validated path):", image_path_chk)

                            if minimax_validate_image(image_path_chk):
                                print("Image path is valid")
                                image_path = image_path_chk
                                # print("Image Path:", image_path)
                            else:
                                print("Image path failed validation:", image_path_chk)
                                return {"CANCELLED"}
                        else:
                            print("image_strip type is not IMAGE:", image_strip.type)
                            return {"CANCELLED"}
                    else:
                        print("Subject is empty!")
                        return {"CANCELLED"}

                if not image_path and not movie_model_card == "Hailuo/MiniMax/txt2vid":
                    print("Loading strip failed!")
                    return {"CANCELLED"}

                task_id = invoke_video_generation(
                    prompt[:2000], api_key, image_path, movie_model_card
                )
                src_path = solve_path(clean_filename(prompt[:20]) + ".mp4")
                print("Task ID: "+str(task_id))
                print("Generating: " + src_path)
                print(
                    "-----------------Video generation task submitted to MiniMax-----------------"
                )
                while True:
                    #progress_bar(10)

                    file_id, status = query_video_generation(task_id, api_key)
                    if file_id != "":
                        print("Image Path: " + src_path)
                        dst_path = fetch_video_result(file_id, api_key, src_path)
                        if os.path.exists(dst_path):
                            print("---------------Successful---------------")
                            break
                        else:
                            print("---------------Failed---------------")
                            return {"CANCELLED"}
                    elif status == "Fail" or status == "Unknown":
                        print("---------------Failed---------------")
                        return {"CANCELLED"}

                print("Result: " + dst_path)
                
            elif movie_model_card == "rootonchair/LTX-2-19b-distilled" or movie_model_card == "Lightricks/LTX-2" or movie_model_card == "LTX-2 Multi-Input File": 
                pass
            else:
                # Move to folder.
                render = bpy.context.scene.render
                fps = round((render.fps / render.fps_base), 3)
                if (movie_model_card == "Wan-AI/Wan2.2-I2V-A14B-Diffusers" or movie_model_card == "Wan-AI/Wan2.2-T2V-A14B-Diffusers"):
                    fps = 16  
                src_path = export_to_video(video_frames, fps=fps)
                dst_path = solve_path(clean_filename(str(seed) + "_" + prompt) + ".mp4")
                shutil.move(src_path, dst_path)

            # Add strip.
            if not os.path.isfile(dst_path):
                print("No resulting file found.")
                return {"CANCELLED"}
            
            if movie_model_card == "Lightricks/LTX-2" or movie_model_card == "rootonchair/LTX-2-19b-distilled" or movie_model_card == "LTX-2 Multi-Input File":
                sound = True
                empty_channel=empty_channel-1
            else:
                sound = False
            
            for window in bpy.context.window_manager.windows:
                screen = window.screen
                for area in screen.areas:
                    if area.type == "SEQUENCE_EDITOR":
                        from bpy import context

                        with context.temp_override(window=window, area=area):
                            if movie_model_card == "rootonchair/LTX-2-19b-distilled" or movie_model_card == "Lightricks/LTX-2" or movie_model_card == "LTX-2 Multi-Input File": 
                                filepath = dst_path
                                if os.path.isfile(filepath):
                                    strip = scene.sequence_editor.strips.new_sound(
                                        name=prompt,
                                        filepath=filepath,
                                        channel=empty_channel,
                                        frame_start=start_frame,
                                    )
                                    scene.sequence_editor.active_strip = strip
#                                    if i > 0:
#                                        scene.frame_current = (
#                                            scene.sequence_editor.active_strip.frame_final_start
#                                        )
                                    empty_channel = empty_channel+1 
                                else:
                                    print("No resulting audio-file found!")
                            
                            scene.sequence_editor.strips.new_movie(
                                name = str(seed) + "_" + prompt,
                                filepath=dst_path,
                                frame_start=start_frame,
                                channel=empty_channel,
                                fit_method="FIT",
                            )
                            strip = scene.sequence_editor.active_strip
                            scene.sequence_editor.active_strip = strip
                            
                            #strip.use_framerate=False
                            #strip.use_proxy = True
                            #scene.sequence_editor.build_proxy()
                            if i > 0:
                                scene.frame_current = (
                                    scene.sequence_editor.active_strip.frame_final_start
                                )
                            sound_strip = scene.sequence_editor.strips.new_sound(
                                name=str(seed) + "_" + prompt,
                                filepath=dst_path,
                                channel=empty_channel-1,
                                frame_start=start_frame,
                            )
                            # Redraw UI to display the new strip. Remove this if Blender crashes: https://docs.blender.org/api/current/info_gotcha.html#can-i-redraw-during-script-execution
                            #bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
                            break
            print_elapsed_time(start_time)
        if old_duration == -1 and input == "input_strips":
            scene.generate_movie_frames = -1

        if should_unload:
            print("Unloading video models from memory...")
            pipe = None
            refiner = None
            converter = None
            
            _pallaidium_movie_model_cache["pipe"] = None
            _pallaidium_movie_model_cache["refiner"] = None

            clear_cuda_cache()

        bpy.types.Scene.movie_path = ""
#        if input != "input_strips":
#            bpy.ops.renderreminder.pallaidium_play_notification()
        scene.frame_current = current_frame
        return {"FINISHED"}

class SequencerOpenAudioFile(Operator, ImportHelper):
    bl_idname = "sequencer.open_audio_filebrowser"
    bl_label = "Open Audio File Browser"
    filter_glob: StringProperty(
        default="*.wav;",
        options={"HIDDEN"},
    )

    def execute(self, context):
        scene = context.scene
        # Check if the file exists

        if self.filepath and os.path.exists(self.filepath):
            valid_extensions = {".wav"}
            filename, extension = os.path.splitext(self.filepath)
            if extension.lower() in valid_extensions:
                print("Selected audio file:", self.filepath)
                scene.audio_path = bpy.path.abspath(self.filepath)
            else:
                print("Info: Only wav is allowed.")
        else:
            self.report({"ERROR"}, "Selected file does not exist.")
            return {"CANCELLED"}
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

class SEQUENCER_OT_generate_audio(Operator):
    """Generate Audio"""

    bl_idname = "sequencer.generate_audio"
    bl_label = "Prompt"
    bl_description = "Convert text to audio"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        global _pallaidium_audio_model_cache
        import os
        
        scene = context.scene
        if not scene.sequence_editor:
            scene.sequence_editor_create()
        preferences = context.preferences
        addon_prefs = preferences.addons[ADDON_ID].preferences
        prompt = scene.generate_movie_prompt
        negative_prompt = scene.generate_movie_negative_prompt
        movie_num_inference_steps = scene.movie_num_inference_steps
        movie_num_guidance = scene.movie_num_guidance
        strip = scene.sequence_editor.active_strip
        active_strip = scene.sequence_editor.active_strip
        input = scene.input_strips
        
        # --- CACHE RETRIEVAL ---
        pipe = _pallaidium_audio_model_cache["pipe"]
        vocoder = _pallaidium_audio_model_cache["vocoder"]
        model = _pallaidium_audio_model_cache["model"]
        feature_extractor = _pallaidium_audio_model_cache["feature_extractor"]
        
        should_load = context.scene.get("ai_load_state", True)
        should_unload = context.scene.get("ai_unload_state", True)
        
        # Force load if cache is empty or model changed
        if pipe is None and model is None and not should_load:
            # Basic check, if main objects are missing, force load
            print("Audio model cache missing. Forcing load.")
            should_load = True
            
        if _pallaidium_audio_model_cache["last_model_card"] != addon_prefs.audio_model_card:
            print("Audio model card changed. Forcing load.")
            should_load = True

        strips = context.selected_strips
        if strip in strips:
            duration = scene.audio_length_in_f = (
                strip.frame_final_duration + 1
            )
            audio_length_in_s = duration = duration / (
                scene.render.fps / scene.render.fps_base
            )
        else:
            duration = scene.audio_length_in_f
            audio_length_in_s = duration = duration / (
                scene.render.fps / scene.render.fps_base
            )

        import torch
        import torchaudio
        import scipy
        import random
        import os
        from scipy.io.wavfile import write as write_wav

        # --- DEPENDENCY CHECKS ---
        if addon_prefs.audio_model_card == "tintwotin/Foundation-1-Diffusers":
            try:
                import scipy
                import torch
                from diffusers import StableAudioPipeline
            except ModuleNotFoundError as e:
                print("Dependencies needs to be installed in the add-on preferences: "+str(e.name))
                self.report(
                    {"INFO"},
                    "Dependencies needs to be installed in the add-on preferences.",
                )
                return {"CANCELLED"}

        if addon_prefs.audio_model_card == "WhisperSpeech":
            import numpy as np
            try:
                from whisperspeech.pipeline import Pipeline
                from resemble_enhance.enhancer.inference import denoise, enhance
            except ModuleNotFoundError as e:
                missing_module_name = e.name
                error_message = (
                    f"Module '{missing_module_name}' not found. "
                    "This dependency needs to be installed. "
                    "Please check the add-on preferences to install missing dependencies."
                )
                print(error_message)
                if hasattr(self, 'report'):
                    self.report({"ERROR"}, error_message)
                return {"CANCELLED"}

        if addon_prefs.audio_model_card == "SWivid/F5-TTS":
            try:
                import torcheval
                import numpy as np
                import soundfile as sf
                import torch
                import torchaudio
                from cached_path import cached_path
                from f5_tts.infer.utils_infer import (
                    infer_process,
                    load_model,
                    load_vocoder,
                    preprocess_ref_audio_text,
                    remove_silence_for_generated_wav,
                )
                from f5_tts.model import DiT, UNetT
                import tempfile
            except ImportError as e:
                print("\n--------------------------------------------------")
                print(f"WARNING: TTS dependencies not found or failed to import: {e}")
                print("Please install required libraries in Blender's Python environment.")
                print("--------------------------------------------------\n")
                return {"CANCELLED"}

        if (
            addon_prefs.audio_model_card == "Chatterbox"
        ):
            import numpy as np
            try:
                import torchaudio as ta
                from chatterbox.tts import ChatterboxTTS
                from chatterbox.vc import ChatterboxVC
                import spacy
            except ModuleNotFoundError as e:
                missing_module_name = e.name
                error_message = (
                    f"Module '{missing_module_name}' not found. "
                    "This dependency needs to be installed. "
                    "Please check the add-on preferences to install missing dependencies."
                )
                print(error_message)
                if hasattr(self, 'report'):
                    self.report({"ERROR"}, error_message)
                return {"CANCELLED"}

        if (
            addon_prefs.audio_model_card == "ChatterboxTurbo"
        ):
            import numpy as np
            try:
                import torchaudio as ta
                import torch
                from chatterbox.tts_turbo import ChatterboxTurboTTS
            except ModuleNotFoundError as e:
                missing_module_name = e.name
                error_message = (
                    f"Module '{missing_module_name}' not found. "
                    "This dependency needs to be installed. "
                    "Please check the add-on preferences to install missing dependencies."
                )
                print(error_message)
                if hasattr(self, 'report'):
                    self.report({"ERROR"}, error_message)
                return {"CANCELLED"}

        if (
            addon_prefs.audio_model_card == "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
        ):
            import numpy as np
            try:
                import torch
                import soundfile as sf
                #from qwen_tts import Qwen3TTSModel
                from faster_qwen3_tts import FasterQwen3TTS
            except ModuleNotFoundError as e:
                missing_module_name = e.name
                error_message = (
                    f"Module '{missing_module_name}' not found. "
                    "This dependency needs to be installed. "
                    "Please check the add-on preferences to install missing dependencies."
                )
                print(error_message)
                if hasattr(self, 'report'):
                    self.report({"ERROR"}, error_message)
                return {"CANCELLED"}

        if (
            addon_prefs.audio_model_card == "parler-tts/parler-tts-large-v1"
            or addon_prefs.audio_model_card == "parler-tts/parler-tts-mini-v1"
        ):
            import numpy as np
            try:
                from parler_tts import ParlerTTSForConditionalGeneration
                from transformers import AutoTokenizer
            except ModuleNotFoundError as e:
                missing_module_name = e.name
                error_message = (
                    f"Module '{missing_module_name}' not found. "
                    "This dependency needs to be installed. "
                    "Please check the add-on preferences to install missing dependencies."
                )
                print(error_message)
                if hasattr(self, 'report'):
                    self.report({"ERROR"}, error_message)
                return {"CANCELLED"}

        if addon_prefs.audio_model_card == "MMAudio":
            try:
                from datetime import datetime
                from pathlib import Path
                import librosa
                import gradio as gr
                import torch
                import torchaudio
                import os
                import numpy as np
                import mmaudio
                from mmaudio.eval_utils import (ModelConfig, all_model_cfg, generate, load_video, load_image, make_video, VideoInfo,
                                                setup_eval_logging)
                from mmaudio.model.flow_matching import FlowMatching
                from mmaudio.model.networks import MMAudio, get_my_mmaudio
                from mmaudio.model.sequence_config import SequenceConfig
                from mmaudio.model.utils.features_utils import FeaturesUtils
                import tempfile
                torch.backends.cuda.matmul.allow_tf32 = True
                torch.backends.cudnn.allow_tf32 = True
            except ModuleNotFoundError as e:
                missing_module_name = e.name
                error_message = (
                    f"Module '{missing_module_name}' not found. "
                    "This dependency needs to be installed. "
                    "Please check the add-on preferences to install missing dependencies."
                )
                print(error_message)
                if hasattr(self, 'report'):
                    self.report({"ERROR"}, error_message)
                return {"CANCELLED"}

        show_system_console(True)
        set_system_console_topmost(True)

        if should_load:
            # clear the VRAM
            clear_cuda_cache()
            
            # Reset local variables
            pipe = None
            vocoder = None
            model = None
            feature_extractor = None

            # Load models Audio
            print("Model:  " + addon_prefs.audio_model_card)

            if addon_prefs.audio_model_card == "tintwotin/Foundation-1-Diffusers":
                #repo_id = "ylacombe/stable-audio-1.0"
                repo_id = "tintwotin/Foundation-1-Diffusers"
                pipe = StableAudioPipeline.from_pretrained(
                    repo_id, torch_dtype=torch.float16
                )
                if gfx_device == "mps":
                    pipe.to("mps")
                elif low_vram():
                    pipe.enable_model_cpu_offload()
                else:
                    pipe.to(gfx_device)

            # WhisperSpeech
            elif addon_prefs.audio_model_card == "WhisperSpeech":
                from whisperspeech.pipeline import Pipeline
                pipe = Pipeline(s2a_ref="collabora/whisperspeech:s2a-q4-small-en+pl.model")

            #F5-TTS
            elif addon_prefs.audio_model_card == "SWivid/F5-TTS":
                DEFAULT_F5TTS_CFG = [
                    "hf://SWivid/F5-TTS/F5TTS_v1_Base/model_1250000.safetensors",
                    "hf://SWivid/F5-TTS/F5TTS_v1_Base/vocab.txt",
                    json.dumps(dict(dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, conv_layers=4)),
                ]
                print("Loading vocoder...")
                try:
                    vocoder = load_vocoder()
                    print("Vocoder loaded successfully.")
                except Exception as e:
                    raise RuntimeError(f"Vocoder failed to load: {e}") from e

                ckpt_path_str = str(cached_path(DEFAULT_F5TTS_CFG[0]))
                F5TTS_model_cfg_dict = json.loads(DEFAULT_F5TTS_CFG[2])
                pipe = load_model(DiT, F5TTS_model_cfg_dict, ckpt_path_str).to(gfx_device)
                print("F5-TTS model loaded.")
                if pipe is None:
                     raise RuntimeError(f"Failed to load or get model F5-TTS'.")

            # Chatterbox
            elif addon_prefs.audio_model_card == "Chatterbox":
                if torch.cuda.is_available():
                    device = "cuda"
                elif torch.backends.mps.is_available():
                    device = "mps"
                else:
                    device = "cpu"
                print(f"Using device: {device}")
                # Pre-load TTS model if possible, though Chatterbox logic below handles VC vs TTS dynamically
                # We instantiate TTS here to cache it for text strips
                try:
                    model = ChatterboxTTS.from_pretrained(device=device)
                except:
                    pass

            # ChatterboxTurbo
            elif addon_prefs.audio_model_card == "ChatterboxTurbo":
                if torch.cuda.is_available():
                    device = "cuda"
                elif torch.backends.mps.is_available():
                    device = "mps"
                else:
                    device = "cpu"
                print(f"Using device: {device}")
                # Pre-load TTS model if possible, though Chatterbox logic below handles VC vs TTS dynamically
                # We instantiate TTS here to cache it for text strips
                try:
                    model = ChatterboxTurboTTS.from_pretrained(device=device)
                except:
                    pass
            
            #Qwen3-TTS
            elif addon_prefs.audio_model_card == "Qwen/Qwen3-TTS-12Hz-1.7B-Base":
                if torch.cuda.is_available():
                    device = "cuda"
                elif torch.backends.mps.is_available():
                    device = "mps"
                else:
                    device = "cpu"
                print(f"Using device: {device}")
                
                model = FasterQwen3TTS.from_pretrained(
                    "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
                    #device_map=device,
                    dtype=torch.bfloat16,
                    #attn_implementation="flash_attention_2",
                )                

            # Parler
            elif (
                addon_prefs.audio_model_card == "parler-tts/parler-tts-large-v1"
                or addon_prefs.audio_model_card == "parler-tts/parler-tts-mini-v1"
            ):
                from parler_tts import ParlerTTSForConditionalGeneration
                from transformers import AutoTokenizer
                pipe = ParlerTTSForConditionalGeneration.from_pretrained("parler-tts/parler-tts-mini-v1").to(gfx_device)
                tokenizer = AutoTokenizer.from_pretrained("parler-tts/parler-tts-mini-v1")
                # Store tokenizer in pipe to keep it simple for cache
                pipe.tokenizer = tokenizer 

            #MMAudio
            elif addon_prefs.audio_model_card == "MMAudio":
                torch.backends.cuda.matmul.allow_tf32 = True
                torch.backends.cudnn.allow_tf32 = True
                device = gfx_device
                dtype = torch.bfloat16
                model_config: ModelConfig = all_model_cfg['large_44k_v2']
                model_config.download_if_needed()
                scheduler_config = model_config.seq_cfg
                model: MMAudio = get_my_mmaudio(model_config.model_name).to(device, dtype).eval()
                model.load_weights(torch.load(model_config.model_path, map_location=device, weights_only=True))
                print(f'Loaded weights from {model_config.model_path}')
                feature_extractor = FeaturesUtils(
                    tod_vae_ckpt=model_config.vae_path,
                    synchformer_ckpt=model_config.synchformer_ckpt,
                    enable_conditions=True,
                    mode=model_config.mode,
                    bigvgan_vocoder_ckpt=model_config.bigvgan_16k_path,
                    need_vae_encoder=False
                ).to(device, dtype)

            # Deadend
            else:
                print("Audio model not found.")
                self.report({"INFO"}, "Audio model not found.")
                return {"CANCELLED"}
            
            # --- UPDATE CACHE ---
            _pallaidium_audio_model_cache["pipe"] = pipe
            _pallaidium_audio_model_cache["vocoder"] = vocoder
            _pallaidium_audio_model_cache["model"] = model
            _pallaidium_audio_model_cache["feature_extractor"] = feature_extractor
            _pallaidium_audio_model_cache["last_model_card"] = addon_prefs.audio_model_card

        old_duration = duration = scene.audio_length_in_f

        # Main loop Audio
        for i in range(scene.movie_num_batch):
            start_time = timer()
            strip = scene.sequence_editor.active_strip
            if strip and input == "input_strips" and duration == -1:
                duration = scene.audio_length_in_f = (
                    strip.frame_final_duration + 1
                )
                audio_length_in_s = duration = duration / (
                    scene.render.fps / scene.render.fps_base
                )
            else:
                duration = scene.audio_length_in_f
                audio_length_in_s = duration / (
                    scene.render.fps / scene.render.fps_base
                )

            # Find free space for the strip in the timeline.
            if i > 0:
                empty_channel = scene.sequence_editor.active_strip.channel
                start_frame = (
                    scene.sequence_editor.active_strip.frame_final_start
                    + scene.sequence_editor.active_strip.frame_final_duration
                )
                scene.frame_current = (
                    scene.sequence_editor.active_strip.frame_final_start
                )
            else:
                if input != "input_strips":
                    empty_channel = find_first_empty_channel(
                        scene.frame_current,
                        (scene.movie_num_batch * (len(prompt) * 4))
                        + scene.frame_current,
                    )
                else:
                    empty_channel = find_first_empty_channel(
                        active_strip.frame_final_start,
                        (duration
                        + scene.frame_current)
                    )
                start_frame = scene.frame_current

            # Get seed
            seed = context.scene.movie_num_seed
            seed = (
                seed
                if not context.scene.movie_use_random
                else random.randint(-2147483647, 2147483647)
            )
            print("Seed: " + str(seed))
            context.scene.movie_num_seed = seed

            # Use cuda if possible
            if torch.cuda.is_available():
                generator = (
                    torch.Generator("cuda").manual_seed(seed) if seed != 0 else None
                )
            else:
                if seed != 0:
                    generator = torch.Generator(device=gfx_device)
                    generator.manual_seed(seed)
                else:
                    generator = None

            # Stable Open Audio
            if addon_prefs.audio_model_card == "tintwotin/Foundation-1-Diffusers":
                import random
                print("Generate: Stable Open Audio")
                seed = context.scene.movie_num_seed
                seed = (
                    seed
                    if not context.scene.movie_use_random
                    else random.randint(0, 999999)
                )
                print("Seed: " + str(seed))
                context.scene.movie_num_seed = seed
                filename = solve_path(clean_filename(str(seed) + "_" + prompt) + ".wav")
                audio = pipe(
                    prompt,
                    negative_prompt=negative_prompt,
                    num_inference_steps=movie_num_inference_steps,
                    audio_end_in_s=audio_length_in_s,
                    num_waveforms_per_prompt=1,
                    generator=generator,
                ).audios
                output = audio[0].T.float().cpu().numpy()
                write_wav(filename, pipe.vae.sampling_rate, output)

            # WhisperSpeech
            elif addon_prefs.audio_model_card == "WhisperSpeech":
                prompt = context.scene.generate_movie_prompt
                prompt = prompt.replace("\n", " ").strip()
                filename = solve_path(clean_filename(prompt) + ".wav")
                if scene.audio_path:
                    speaker = bpy.path.abspath(scene.audio_path)
                else:
                    speaker = None
                pipe.generate_to_file(
                    filename,
                    prompt,
                    speaker=speaker,
                    lang="en",
                    cps=int(scene.audio_speed),
                )

            #F5-TTS
            elif addon_prefs.audio_model_card == "SWivid/F5-TTS":
                if scene.audio_path:
                    speaker = bpy.path.abspath(scene.audio_path)
                else:
                    speaker = None
                    print("No speaker file found. Cancelled...")
                    return {"CANCELLED"}
                print("Speaker: "+speaker)

                ref_audio_processed = None
                ref_text_used = ""
                # Ensure vocoder is available (loaded from cache or reloaded)
                if vocoder is None:
                     print("Loading vocoder...")
                     try:
                         vocoder = load_vocoder()
                         _pallaidium_audio_model_cache["vocoder"] = vocoder
                         print("Vocoder loaded successfully.")
                     except Exception as e:
                         raise RuntimeError(f"Vocoder failed to load: {e}") from e

                # ckpt_path_str = str(cached_path(DEFAULT_F5TTS_CFG[0]))
                # F5TTS_model_cfg_dict = json.loads(DEFAULT_F5TTS_CFG[2])
                # pipe = load_model(DiT, F5TTS_model_cfg_dict, ckpt_path_str)
                # print("F5-TTS model loaded.")
                
                prompt = context.scene.generate_movie_prompt
                prompt = prompt.replace("\n", " ").strip()
                filename = solve_path(clean_filename(prompt) + ".wav")
                ref_audio_processed, ref_text_used = preprocess_ref_audio_text(
                    speaker,
                    ref_text_used,
                    show_info=print,
                )
                seed = context.scene.movie_num_seed
                seed = (
                    seed
                    if not context.scene.movie_use_random
                    else random.randint(0, 2147483647)
                )
                torch.manual_seed(seed)
                print("Seed: " + str(seed))
                context.scene.movie_num_seed = seed
                final_wave = None
                final_sample_rate = None
                final_wave, final_sample_rate, _ = infer_process(
                    ref_audio_processed,
                    ref_text_used,
                    prompt,
                    pipe,
                    vocoder,
                    cross_fade_duration=0.15,
                    nfe_step=movie_num_inference_steps,
                    speed=(scene.audio_speed_tts),
                    show_info=print,
                )
                filename = solve_path(clean_filename(str(seed) + "_" + prompt) + ".wav")
                if scene.remove_silence and final_wave is not None and len(final_wave) > 0:
                    print("Attempting to remove silence...")
                    tmp_wav_path = None
                    try:
                        tmp_fd, tmp_wav_path = tempfile.mkstemp(suffix=".wav")
                        os.close(tmp_fd)
                        sf.write(tmp_wav_path, final_wave, final_sample_rate)
                        remove_silence_for_generated_wav(tmp_wav_path)
                        loaded_audio, loaded_sr = torchaudio.load(tmp_wav_path)
                        final_wave = loaded_audio.squeeze().cpu().numpy()
                        final_sample_rate = loaded_sr
                        print("Silence removal successful.")
                    except Exception as e:
                        print(f"Error during silence removal: {e}")
                    finally:
                         if tmp_wav_path and os.path.exists(tmp_wav_path):
                             try:
                                 os.remove(tmp_wav_path)
                             except OSError as e:
                                 print(f"Warning: Could not remove temporary file {tmp_wav_path}: {e}")
                if final_wave is not None and len(final_wave) > 0:
                    try:
                        output_audio_path = filename = solve_path(clean_filename(str(seed) + "_" + prompt) + ".wav")
                        sf.write(output_audio_path, final_wave.astype(np.float32), final_sample_rate)
                    except Exception as e:
                        exception = e
                        print(f"Error saving output audio to {output_audio_path}: {e}")
                else:
                     exception = RuntimeError("Synthesis failed, no audio data generated.")
                     print("Synthesis failed, no audio data to save.")

            # Chatterbox
            elif (
                addon_prefs.audio_model_card == "Chatterbox"
            ):
                output_audio_path = filename = solve_path(clean_filename(str(seed) + "_" + prompt) + ".wav")
                strip = scene.sequence_editor.active_strip
                if scene.audio_path:
                    speaker = speaker = bpy.path.abspath(scene.audio_path)
                else:
                    speaker = None
                seed = context.scene.movie_num_seed
                seed = (
                    seed
                    if not context.scene.movie_use_random
                    else random.randint(0, 2147483647)
                )
                torch.manual_seed(seed)
                if torch.cuda.is_available():
                    device = "cuda"
                elif torch.backends.mps.is_available():
                    device = "mps"
                else:
                    device = "cpu"
                    
                if device == "cuda":
                    torch.cuda.manual_seed(seed)
                    torch.cuda.manual_seed_all(seed)
                random.seed(seed)
                np.random.seed(seed)
                pace = scene.chat_pace
                exaggeration = scene.chat_exaggeration
                temperature = scene.chat_temperature

                if input and input == "input_strips" and strip.type == "SOUND": # Voice clone
                    AUDIO_PROMPT_PATH = os.path.join(bpy.path.abspath(strip.sound.filepath))
                    print("Voice cloning: "+strip.sound.name)
                    # For VC we might need to load a different model, but we try to reuse cached if compatible or load fresh
                    vc_model = ChatterboxVC.from_pretrained(device)
                    wav = vc_model.generate(audio=AUDIO_PROMPT_PATH)#,target_voice_path=speaker)
                    ta.save(output_audio_path, wav, vc_model.sr)
                else: # Text-to-Speech
                    try:
                        print(f"Starting Text-to-Speech for prompt: '{prompt}'")
                        # Use cached model if available
                        if model is None:
                            model = ChatterboxTTS.from_pretrained(device=device)
                            _pallaidium_audio_model_cache["model"] = model
                            
                        chunks = split_text_for_tts(prompt)
                        all_wav_chunks = []
                        for i, chunk_text in enumerate(chunks):
                            if not chunk_text.strip():
                                continue
                            print(f"Synthesizing chunk {i+1}/{len(chunks)}: '{chunk_text}...'")
                            try:
                                wav_chunk_tensor = model.generate(
                                    chunk_text,
                                    audio_prompt_path=speaker,
                                    exaggeration=exaggeration,
                                    cfg_weight=pace,
                                    temperature=temperature
                                )
                                all_wav_chunks.append(wav_chunk_tensor.flatten())
                            except Exception as e:
                                print(f"Error synthesizing chunk {i+1}: {e}")
                        if all_wav_chunks:
                            final_wav = torch.cat(all_wav_chunks, dim=0)
                            ta.save(output_audio_path, final_wav.unsqueeze(0), model.sr)
                            print(f"Successfully saved combined audio to {output_audio_path}")
                        else:
                            print("No audio was generated. The prompt might have been empty or resulted in errors.")
                    except Exception as e:
                        print(f"An unexpected error occurred in the TTS process: {e}")

            # ChatterboxTurbo
            elif (
                addon_prefs.audio_model_card == "ChatterboxTurbo"
            ):
                output_audio_path = filename = solve_path(clean_filename(str(seed) + "_" + prompt) + ".wav")
                strip = scene.sequence_editor.active_strip
                if scene.audio_path:
                    speaker = speaker = bpy.path.abspath(scene.audio_path)
                else:
                    speaker = None
                seed = context.scene.movie_num_seed
                seed = (
                    seed
                    if not context.scene.movie_use_random
                    else random.randint(0, 2147483647)
                )
                torch.manual_seed(seed)
                if torch.cuda.is_available():
                    device = "cuda"
                elif torch.backends.mps.is_available():
                    device = "mps"
                else:
                    device = "cpu"
                    
                if device == "cuda":
                    torch.cuda.manual_seed(seed)
                    torch.cuda.manual_seed_all(seed)
                random.seed(seed)
                np.random.seed(seed)
                pace = scene.chat_pace
                exaggeration = scene.chat_exaggeration
                temperature = scene.chat_temperature

                if input and input == "input_strips" and strip.type == "SOUND": # Voice clone
                    AUDIO_PROMPT_PATH = os.path.join(bpy.path.abspath(strip.sound.filepath))
                    print("Voice cloning: "+strip.sound.name)
                    # For VC we might need to load a different model, but we try to reuse cached if compatible or load fresh
                    vc_model = ChatterboxTurboTTS.from_pretrained(device)
                    wav = vc_model.generate(audio_prompt_path=AUDIO_PROMPT_PATH)
                    ta.save(output_audio_path, wav, vc_model.sr)
                else: # Text-to-Speech
                    try:
                        print(f"Starting Text-to-Speech for prompt: '{prompt}'")
                        # Use cached model if available
                        if model is None:
                            model = ChatterboxTurboTTS.from_pretrained(device=device)
                            _pallaidium_audio_model_cache["model"] = model
                            
                        chunks = split_text_for_tts(prompt)
                        all_wav_chunks = []
                        for i, chunk_text in enumerate(chunks):
                            if not chunk_text.strip():
                                continue
                            print(f"Synthesizing chunk {i+1}/{len(chunks)}: '{chunk_text}...'")
                            try:
                                wav_chunk_tensor = model.generate(
                                    chunk_text,
                                    audio_prompt_path=speaker,
                                    exaggeration=exaggeration,
                                    cfg_weight=pace,
                                    temperature=temperature
                                )
                                all_wav_chunks.append(wav_chunk_tensor.flatten())
                            except Exception as e:
                                print(f"Error synthesizing chunk {i+1}: {e}")
                        if all_wav_chunks:
                            final_wav = torch.cat(all_wav_chunks, dim=0)
                            ta.save(output_audio_path, final_wav.unsqueeze(0), model.sr)
                            print(f"Successfully saved combined audio to {output_audio_path}")
                        else:
                            print("No audio was generated. The prompt might have been empty or resulted in errors.")
                    except Exception as e:
                        print(f"An unexpected error occurred in the TTS process: {e}")

            #Qwen3-TTS
            elif addon_prefs.audio_model_card == "Qwen/Qwen3-TTS-12Hz-1.7B-Base":
                output_audio_path = filename = solve_path(clean_filename(str(seed) + "_" + prompt) + ".wav")
                strip = scene.sequence_editor.active_strip
                seed = context.scene.movie_num_seed
                seed = (
                    seed
                    if not context.scene.movie_use_random
                    else random.randint(0, 2147483647)
                )
                if torch.cuda.is_available():
                    device = "cuda"
                elif torch.backends.mps.is_available():
                    device = "mps"
                else:
                    device = "cpu"            
                if device == "cuda":
                    torch.cuda.manual_seed(seed)
                    torch.cuda.manual_seed_all(seed)
                random.seed(seed)
                np.random.seed(seed)
 
                print(f"Starting Text-to-Speech for prompt: '{prompt}'")
                try:
                    # Use cached model if available
                    if model is None:
                        #model = Qwen3TTSModel.from_pretrained(
                        model = FasterQwen3TTS.from_pretrained(
                            "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
                            #device_map=device,
                            dtype=torch.bfloat16,
                            #attn_implementation="flash_attention_2",
                        )
                except Exception as e:
                    print(f"An unexpected error occurred in the TTS process: {e}")

                if scene.audio_path:
                    ref_audio = bpy.path.abspath(scene.audio_path)
                else:
                    ref_audio = None
                    print("Reference speaker file not found.")
                    self.report({"INFO"}, "Reference speaker file not found.")
                    return {"CANCELLED"}

                if scene.audio_text:
                    ref_text = bpy.path.abspath(scene.audio_text)
                else:
                    ref_text = None
                    print("Reference text file not found.")
                    self.report({"INFO"}, "Reference text file not found.")
                    return {"CANCELLED"}        

                wavs = None
                
                wavs, sr = model.generate_voice_clone(
                    text=prompt,
                    language="English",

                    ref_audio=ref_audio,
                    ref_text=ref_text,
                )
                if not wavs:
                    print("Audio generation failed")
                out=sf.write(output_audio_path, wavs[0], sr)
                print("Audio saved: "+str(out))     

            # Parler
            elif (
                addon_prefs.audio_model_card == "parler-tts/parler-tts-large-v1"
                or addon_prefs.audio_model_card == "parler-tts/parler-tts-mini-v1"
            ):
                prompt = prompt
                seed = context.scene.movie_num_seed
                seed = (
                    seed
                    if not context.scene.movie_use_random
                    else random.randint(0, 999999)
                )
                print("Seed: " + str(seed))
                context.scene.movie_num_seed = seed
                description = context.scene.parler_direction_prompt
                
                # Use tokenizer stored in pipe if available from cache
                tokenizer = getattr(pipe, 'tokenizer', None)
                if tokenizer is None:
                    from transformers import AutoTokenizer
                    tokenizer = AutoTokenizer.from_pretrained("parler-tts/parler-tts-mini-v1")
                
                input_ids = tokenizer(description, return_tensors="pt").input_ids.to(
                    gfx_device
                )
                prompt_input_ids = tokenizer(prompt, return_tensors="pt").input_ids.to(
                    gfx_device
                )
                generation = pipe.generate(
                    input_ids=input_ids, prompt_input_ids=prompt_input_ids
                )
                audio_arr = generation.cpu().numpy().squeeze()
                filename = solve_path(str(seed) + "_" + prompt + ".wav")
                write_wav(filename, pipe.config.sampling_rate, audio_arr)

            #MMAudio
            if addon_prefs.audio_model_card == "MMAudio":
                model_config: ModelConfig = all_model_cfg['large_44k_v2']
                scheduler_config = model_config.seq_cfg # Need re-access to seq_cfg
                
                scheduler = FlowMatching(min_sigma=0, inference_mode='euler', num_steps=movie_num_inference_steps)
                seed = context.scene.movie_num_seed
                seed = (
                    seed
                    if not context.scene.movie_use_random
                    else random.randint(0, 999999)
                )
                print("Seed: " + str(seed))
                context.scene.movie_num_seed = seed

                if torch.cuda.is_available():
                    generator = (
                        torch.Generator("cuda").manual_seed(seed) if seed != 0 else None
                    )
                else:
                    if seed != 0:
                        generator = torch.Generator(device=gfx_device)
                        generator.manual_seed(seed)
                    else:
                        generator = None
                generated_audio = None
                if scene.movie_path:
                    print("Process: Video to audio")
                    if not os.path.isfile(scene.movie_path):
                        print("No file found.")
                        return {"CANCELLED"}
                    video_path = scene.movie_path
                    video_data = load_video(video_path, audio_length_in_s)
                    video_frames = video_data.clip_frames.unsqueeze(0)
                    sync_frames = video_data.sync_frames.unsqueeze(0)
                    duration = video_data.duration_sec
                    scheduler_config.duration = video_data.duration_sec
                    model.update_seq_lengths(scheduler_config.latent_seq_len, scheduler_config.clip_seq_len, scheduler_config.sync_seq_len)
                    with torch.no_grad():
                        generated_audio = generate(
                            video_frames, sync_frames, [prompt],
                            negative_text=[negative_prompt],
                            feature_utils=feature_extractor,
                            net=model, fm=scheduler, rng=generator,
                            cfg_strength=movie_num_guidance,
                        )
                elif scene.image_path:
                    print("Process: Image to audio")
                    strip = scene.sequence_editor.active_strip
                    img_path = os.path.join(bpy.path.abspath(strip.directory), strip.elements[0].filename)
                    if not os.path.isfile(img_path):
                        print("No file found.")
                        return {"CANCELLED"}
                    image = load_image(img_path)
                    video_path = img_path
                    image_data = load_image(scene.image_path)
                    clip_frames = image_data.clip_frames
                    sync_frames = image_data.sync_frames
                    clip_frames = clip_frames.unsqueeze(0)
                    sync_frames = sync_frames.unsqueeze(0)
                    blender_fps_num = bpy.context.scene.render.fps
                    blender_fps_den = bpy.context.scene.render.fps_base
                    if blender_fps_den == 0:
                        effective_fps_float = 0.0
                    else:
                        effective_fps_float = blender_fps_num / blender_fps_den
                    if effective_fps_float == 0.0:
                        fps_as_fraction = Fraction(24, 1)
                    else:
                        fps_as_fraction = Fraction(effective_fps_float).limit_denominator(1001)
                    video_data = VideoInfo.from_image_info(image_data, audio_length_in_s, fps=fps_as_fraction)
                    scheduler_config.duration = audio_length_in_s
                    model.update_seq_lengths(scheduler_config.latent_seq_len, scheduler_config.clip_seq_len, scheduler_config.sync_seq_len)
                    with torch.no_grad():
                        generated_audio = generate(clip_frames,
                                          sync_frames, [prompt],
                                          negative_text=[negative_prompt],
                                          feature_utils=feature_extractor,
                                          net=model, fm=scheduler, rng=generator,
                                          cfg_strength=movie_num_guidance,
                                          image_input=True)
                elif strip.type != "MOVIE" and strip.type != "IMAGE":
                    if scene.audio_length_in_f == -1:
                        scene.audio_length_in_f = 25
                    clip_frames = sync_frames = None
                    scheduler_config.duration = audio_length_in_s
                    model.update_seq_lengths(scheduler_config.latent_seq_len, scheduler_config.clip_seq_len, scheduler_config.sync_seq_len)
                    with torch.no_grad():
                        generation = generate(clip_frames,
                                          sync_frames, [prompt],
                                          negative_text=[negative_prompt],
                                          feature_utils=feature_extractor,
                                          net=model, fm=scheduler, rng=generator,
                                          cfg_strength=movie_num_guidance,
                                          image_input=True)
                    audio_output = generation.float().cpu()[0]
                    target_sr = int((context.preferences.system.audio_sample_rate).split('_')[1])
                    filename = solve_path(str(seed) + "_" + prompt + ".wav")
                    torchaudio.save(filename, audio_output, target_sr)

                if generated_audio != None:
                    audio_output = generated_audio.float().cpu()[0]
                    target_sr = int((context.preferences.system.audio_sample_rate).split('_')[1])
                    filename = video_output_path = solve_path(str(seed) + "_" + prompt + ".mp4")
                    make_video(video_data, video_output_path, audio_output, sampling_rate=target_sr)
                    print(f'Saved video to {video_output_path}')
                else:
                    # In case of failure inside MMAudio generation
                    if pipe:
                        pipe = None

                    # clear the VRAM
                    clear_cuda_cache()

#                    if input != "input_strips":
#                        bpy.ops.renderreminder.pallaidium_play_notification()
                    return {"CANCELLED"}                   

            # Add Audio Strip
            filepath = filename
            if os.path.isfile(filepath):
                strip = scene.sequence_editor.strips.new_sound(
                    name=prompt,
                    filepath=filepath,
                    channel=empty_channel,
                    frame_start=start_frame,
                )
                scene.sequence_editor.active_strip = strip
                if i > 0:
                    scene.frame_current = (
                        scene.sequence_editor.active_strip.frame_final_start
                    )
                bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
            else:
                print("No resulting file found!")
            print_elapsed_time(start_time)
            if old_duration == -1 and input == "input_strips":
                scene.audio_length_in_f = scene.generate_movie_frames = -1
        
        # --- UNLOAD LOGIC ---
        if should_unload:
            print("Unloading audio models from memory...")
            pipe = None
            vocoder = None
            model = None
            feature_extractor = None
            
            _pallaidium_audio_model_cache["pipe"] = None
            _pallaidium_audio_model_cache["vocoder"] = None
            _pallaidium_audio_model_cache["model"] = None
            _pallaidium_audio_model_cache["feature_extractor"] = None
            
            # clear the VRAM
            clear_cuda_cache()

#        if input != "input_strips":
#            bpy.ops.renderreminder.pallaidium_play_notification()
        return {"FINISHED"}

class IPAdapterFaceFileBrowserOperator(Operator):
    bl_idname = "ip_adapter_face.file_browser"
    bl_label = "Open IP Adapter Face File Browser"

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    import_as_folder: bpy.props.BoolProperty(name="Import as Folder", default=False)

    def execute(self, context):
        valid_image_extensions = {
            ".png",
            ".jpg",
            ".jpeg",
            ".bmp",
            ".tiff",
            ".tif",
            ".gif",
            ".hdr",
        }
        scene = context.scene

        if self.filepath:
            if self.import_as_folder:
                files_to_import = bpy.context.scene.ip_adapter_face_files_to_import
                files_to_import.clear()
                # self.filepath = os.path.dirname(self.filepath)

                print("Importing folder:", self.filepath)
                for file_path in glob.glob(os.path.join(self.filepath, "*")):
                    if os.path.isfile(file_path):
                        file_ext = os.path.splitext(file_path)[1].lower()
                        if file_ext in valid_image_extensions:
                            print(
                                "Found image file in folder:",
                                os.path.basename(file_path),
                            )
                            new_file = files_to_import.add()
                            # new_file.name = os.path.basename(self.filepath)

                            new_file.path = os.path.abspath(self.filepath)
                scene.ip_adapter_face_folder = os.path.abspath(
                    os.path.dirname(self.filepath)
                )
                self.report(
                    {"INFO"}, f"{len(files_to_import)} image files found in folder."
                )
            else:
                print("Importing file:", self.filepath)
                valid_file_ext = os.path.splitext(self.filepath)[1].lower()
                if valid_file_ext in valid_image_extensions:
                    print("Adding image file:", os.path.basename(self.filepath))
                    files_to_import = bpy.context.scene.ip_adapter_face_files_to_import
                    new_file = files_to_import.add()
                    # new_file.name = os.path.basename(self.filepath)

                    new_file.name = os.path.abspath(self.filepath)
                    self.report({"INFO"}, "Image file added.")
                    scene.ip_adapter_face_folder = os.path.abspath(self.filepath)
                else:
                    self.report({"ERROR"}, "Selected file is not a valid image.")
        else:
            self.report({"ERROR"}, "No file selected.")
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

class IPAdapterStyleFileBrowserOperator(Operator):
    bl_idname = "ip_adapter_style.file_browser"
    bl_label = "Open IP Adapter Style File Browser"

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    import_as_folder: bpy.props.BoolProperty(name="Import as Folder", default=False)

    def execute(self, context):
        valid_image_extensions = {
            ".png",
            ".jpg",
            ".jpeg",
            ".bmp",
            ".tiff",
            ".tif",
            ".gif",
            ".hdr",
        }
        scene = context.scene

        if self.filepath:
            if self.import_as_folder:
                files_to_import = bpy.context.scene.ip_adapter_style_files_to_import
                files_to_import.clear()  # Clear the list first
                self.filepath = os.path.dirname(self.filepath)
                print("Importing folder:", self.filepath)
                for file_path in glob.glob(os.path.join(self.filepath, "*")):
                    if os.path.isfile(file_path):
                        file_ext = os.path.splitext(file_path)[1].lower()
                        if file_ext in valid_image_extensions:
                            print(
                                "Found image file in folder:",
                                os.path.basename(file_path),
                            )
                            new_file = files_to_import.add()
                            new_file.name = os.path.basename(file_path)
                            new_file.path = os.path.abspath(file_path)
                scene.ip_adapter_style_folder = os.path.abspath(self.filepath)
                self.report(
                    {"INFO"}, f"{len(files_to_import)} image files found in folder."
                )
            else:
                print("Importing file:", self.filepath)
                valid_file_ext = os.path.splitext(self.filepath)[1].lower()
                if valid_file_ext in valid_image_extensions:
                    print("Adding image file:", os.path.basename(self.filepath))
                    files_to_import = bpy.context.scene.ip_adapter_style_files_to_import
                    new_file = files_to_import.add()
                    new_file.name = os.path.basename(self.filepath)
                    new_file.path = os.path.abspath(self.filepath)
                    self.report({"INFO"}, "Image file added.")
                    scene.ip_adapter_style_folder = os.path.abspath(self.filepath)
                else:
                    self.report({"ERROR"}, "Selected file is not a valid image.")
        else:
            self.report({"ERROR"}, "No file selected.")
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

class SEQUENCER_OT_generate_image(Operator):
    """Generate Image"""

    bl_idname = "sequencer.generate_image"
    bl_label = "Prompt"
    bl_description = "Convert text to image"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        global _pallaidium_model_cache
        import os
        
        scene = context.scene
        seq_editor = scene.sequence_editor
        preferences = context.preferences
        addon_prefs = preferences.addons[ADDON_ID].preferences
        use_strip_data = addon_prefs.use_strip_data
        local_files_only = addon_prefs.local_files_only
        image_model_card = addon_prefs.image_model_card
        image_power = scene.image_power
        strips = context.selected_strips
        type = scene.generatorai_typeselect

        inference_parameters = None
        
        # --- RETRIEVE FROM CACHE OR INITIALIZE ---
        pipe = _pallaidium_model_cache["pipe"]
        refiner = _pallaidium_model_cache["refiner"]
        converter = _pallaidium_model_cache["converter"]
        
        guidance = scene.movie_num_guidance
        enabled_items = None

        lora_files = scene.lora_files
        enabled_names = []
        enabled_weights = []
        # Check if there are any enabled items before loading
        enabled_items = [item for item in lora_files if item.enabled]

        show_system_console(True)
        set_system_console_topmost(True)
        
        # Determine Loading State
        should_load = context.scene.get("ai_load_state", True)
        should_unload = context.scene.get("ai_unload_state", True)        

        # Safety check: If cache is empty but we were told not to load, we must load.
        if pipe is None and converter is None and not should_load:
            print("Model cache missing. Forcing load.")
            should_load = True
        
        # Safety check: If model card changed manually between batch steps (unlikely but safer)
        if _pallaidium_model_cache["last_model_card"] != image_model_card and _pallaidium_model_cache["last_model_card"] is not None:
             print("Model card changed. Forcing load.")
             should_load = True

        if not seq_editor:
            scene.sequence_editor_create()

        try:
#            if os_platform != "Darwin":
            from diffusers import DiffusionPipeline, DPMSolverMultistepScheduler
            from diffusers.utils import pt_to_pil
            import torch
            from diffusers.utils import load_image
            import requests
            import numpy as np
            import PIL
            import cv2
            from PIL import Image

        # from compel import Compel

        except ModuleNotFoundError as e:
            print("Dependencies needs to be installed in the add-on preferences: "+str(e.name))
            self.report(
                {"INFO"},
                "Dependencies needs to be installed in the add-on preferences.",
            )
            return {"CANCELLED"}


        current_frame = scene.frame_current
        type = scene.generatorai_typeselect
        input = scene.input_strips
        prompt = style_prompt(scene.generate_movie_prompt)[0]
        negative_prompt = (
            scene.generate_movie_negative_prompt
            + ", "
            + style_prompt(scene.generate_movie_prompt)[1]
            + ", nsfw, nude, nudity,"
        )
        image_x = scene.generate_movie_x
        image_y = scene.generate_movie_y
        x = scene.generate_movie_x = closest_divisible_32(image_x)
        y = scene.generate_movie_y = closest_divisible_32(image_y)
        duration = scene.generate_movie_frames
        image_num_inference_steps = scene.movie_num_inference_steps
        image_num_guidance = scene.movie_num_guidance
        active_strip = context.scene.sequence_editor.active_strip
        do_inpaint = (
            input == "input_strips"
            and find_strip_by_name(scene, scene.inpaint_selected_strip)
            and type == "image"
            and not image_model_card == "diffusers/controlnet-canny-sdxl-1.0-small"
            and not image_model_card == "xinsir/controlnet-openpose-sdxl-1.0"
            and not image_model_card == "xinsir/controlnet-scribble-sdxl-1.0"
            and not image_model_card == "adamo1139/stable-diffusion-3.5-large-ungated"
            and not image_model_card == "adamo1139/stable-diffusion-3.5-medium-ungated"
            and not image_model_card == "ZhengPeng7/BiRefNet_HR"
            and not image_model_card == "Shitao/OmniGen-v1-diffusers"
            and not image_model_card == "Qwen/Qwen-Image-Edit-2511"
            and not image_model_card == "diffusers/FLUX.2-dev-bnb-4bit"
            and not image_model_card == "Runware/BFL-FLUX.2-klein-base-4B"
            and not image_model_card == "black-forest-labs/FLUX.2-klein-9b-kv" 
#            and (not scene.ip_adapter_face_folder and image_model_card == "stabilityai/stable-diffusion-xl-base-1.0")
#            and (not scene.ip_adapter_style_folder and image_model_card == "stabilityai/stable-diffusion-xl-base-1.0")
        )
        do_convert = (
            (scene.image_path or scene.movie_path)
            and not image_model_card == "diffusers/controlnet-canny-sdxl-1.0-small"
            and not image_model_card == "xinsir/controlnet-openpose-sdxl-1.0"
            and not image_model_card == "xinsir/controlnet-scribble-sdxl-1.0"
            and not image_model_card == "ZhengPeng7/BiRefNet_HR"
            and not image_model_card == "Shitao/OmniGen-v1-diffusers"
            and not image_model_card == "Qwen/Qwen-Image-Edit-2511"
            and not image_model_card == "diffusers/FLUX.2-dev-bnb-4bit"
#            and (not scene.ip_adapter_face_folder and image_model_card == "stabilityai/stable-diffusion-xl-base-1.0")
#            and (not scene.ip_adapter_style_folder and image_model_card == "stabilityai/stable-diffusion-xl-base-1.0")
            and not do_inpaint
        )
        do_refine = scene.refine_sd and not do_convert
        if (
            do_inpaint
            or do_convert
            or image_model_card == "diffusers/controlnet-canny-sdxl-1.0-small"
            or image_model_card == "xinsir/controlnet-openpose-sdxl-1.0"
            or image_model_card == "xinsir/controlnet-scribble-sdxl-1.0"
            and not scene.ip_adapter_face_folder
            and not scene.ip_adapter_style_folder
            and not image_model_card == "Shitao/OmniGen-v1-diffusers"
            and not image_model_card == "Qwen/Qwen-Image-Edit-2511"
            and not image_model_card == "diffusers/FLUX.2-dev-bnb-4bit"
        ):
            if not strips:
                self.report({"INFO"}, "Select strip(s) for processing.")
                return {"CANCELLED"}
            for strip in strips:
                if strip.type in {"MOVIE", "IMAGE", "TEXT", "SCENE"}:
                    break
            else:
                self.report(
                    {"INFO"},
                    "None of the selected strips are movie, image, text or scene types.",
                )
                return {"CANCELLED"}

        print("do_inpaint: "+str(do_inpaint))
        print("do_convert: "+str(do_convert))
        print("do_refine: "+str(do_refine))


        if should_load == True:
            # Clear old models from memory before loading new ones
            pipe = None
            converter = None
            refiner = None
            clear_cuda_cache()

            # LOADING MODELS
            # models for inpaint
            if do_inpaint:
                from diffusers import AutoPipelineForInpainting
                from diffusers.utils import load_image

                # clear the VRAM
                clear_cuda_cache()

                if image_model_card == "yuvraj108c/FLUX.1-Kontext-dev":
                    print("Load Inpaint: " + image_model_card)
                    import torch
                    from diffusers import BitsAndBytesConfig, FluxTransformer2DModel
                    from diffusers import FluxKontextInpaintPipeline
                    from diffusers.utils import load_image

                    nf4_config = BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_compute_dtype=torch.bfloat16,
                    )
                    model_nf4 = FluxTransformer2DModel.from_pretrained(
                        image_model_card,
                        subfolder="transformer",
                        quantization_config=nf4_config,
                        torch_dtype=torch.bfloat16,
                    )

                    pipe = FluxKontextInpaintPipeline.from_pretrained(
                        image_model_card,
                        transformer=model_nf4,
                        torch_dtype=torch.bfloat16,
                        local_files_only=local_files_only,
                    )

                    if gfx_device == "mps":
                        pipe.to("mps")
                    elif low_vram():
                        # torch.cuda.set_per_process_memory_fraction(0.99)
                        pipe.enable_model_cpu_offload()
                    else:
                        pipe.to(gfx_device)


                elif image_model_card == "stabilityai/stable-diffusion-xl-base-1.0":
                    print("Load Inpaint: " + image_model_card)
                    pipe = AutoPipelineForInpainting.from_pretrained(
                        "diffusers/stable-diffusion-xl-1.0-inpainting-0.1",
                        torch_dtype=torch.float16,
                        variant="fp16",
                        local_files_only=local_files_only,
                    )

                    # Set scheduler
                    if scene.use_lcm:
                        from diffusers import LCMScheduler

                        pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
                        if enabled_items:
                            enabled_names.append("lcm-lora-sdxl")
                            enabled_weights.append(1.0)
                            pipe.load_lora_weights(
                                "latent-consistency/lcm-lora-sdxl",
                                weight_name="pytorch_lora_weights.safetensors",
                                adapter_name=("lcm-lora-sdxl"),
                            )
                        else:
                            pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
                            pipe.load_lora_weights("latent-consistency/lcm-lora-sdxl")
                    else:
                        from diffusers import DPMSolverMultistepScheduler

                        pipe.scheduler = DPMSolverMultistepScheduler.from_config(
                            pipe.scheduler.config
                        )

                    pipe.watermark = NoWatermark()
                    if gfx_device == "mps":
                        pipe.to("mps")
                    elif low_vram():
                        # torch.cuda.set_per_process_memory_fraction(0.99)
                        pipe.enable_model_cpu_offload()
                    else:
                        pipe.to(gfx_device)

                elif (
                    image_model_card == "lzyvegetable/FLUX.1-schnell"
                    or image_model_card == "ChuckMcSneed/FLUX.1-dev"
                    or image_model_card == "Runware/BFL-FLUX.2-klein-base-4B"
                    or image_model_card == "black-forest-labs/FLUX.2-klein-9b-kv"
                    #or image_model_card == "diffusers/FLUX.2-dev-bnb-4bit"
                ):
                    print("Load Inpaint: " + image_model_card)
                    from diffusers import (
                        DiffusionPipeline,
                        FluxFillPipeline,
                        FluxTransformer2DModel,
                    )
                    from transformers import T5EncoderModel

                    orig_pipeline = DiffusionPipeline.from_pretrained(
                        image_model_card, torch_dtype=torch.bfloat16
                    )

                    transformer = FluxTransformer2DModel.from_pretrained(
                        "sayakpaul/FLUX.1-Fill-dev-nf4",
                        subfolder="transformer",
                        torch_dtype=torch.bfloat16,
                    )
                    text_encoder_2 = T5EncoderModel.from_pretrained(
                        "sayakpaul/FLUX.1-Fill-dev-nf4",
                        subfolder="text_encoder_2",
                        torch_dtype=torch.bfloat16,
                    )
                    pipe = FluxFillPipeline.from_pipe(
                        orig_pipeline,
                        transformer=transformer,
                        text_encoder_2=text_encoder_2,
                        torch_dtype=torch.bfloat16,
                    )

                    if gfx_device == "mps":
                        pipe.to("mps")
                    elif low_vram():
                        pipe.enable_sequential_cpu_offload()
                        pipe.vae.enable_tiling()
                    else:
                        pipe.enable_model_cpu_offload()

            # Conversion img2img/vid2img.
            elif do_convert:  # and not scene.aurasr:
                print("Load: img2img/vid2img Model")
                print("Conversion Model:  " + image_model_card)
                if image_model_card == "stabilityai/stable-diffusion-xl-base-1.0":
                    from diffusers import StableDiffusionXLImg2ImgPipeline, AutoencoderKL

                    vae = AutoencoderKL.from_pretrained(
                        "madebyollin/sdxl-vae-fp16-fix",
                        torch_dtype=torch.float16,
                        local_files_only=local_files_only,
                    )
                    converter = StableDiffusionXLImg2ImgPipeline.from_pretrained(
                        "thingthatis/stable-diffusion-xl-refiner-1.0",
                        # text_encoder_2=pipe.text_encoder_2,
                        vae=vae,
                        torch_dtype=torch.float16,
                        variant="fp16",
                        local_files_only=local_files_only,
                    )
                    if gfx_device == "mps":
                        converter.to("mps")
                    elif low_vram():
                        converter.enable_model_cpu_offload()
                    else:
                        converter.to(gfx_device)
                else:
                    from diffusers import AutoPipelineForImage2Image
                    if (
                        os_platform == "Darwin"
                    ):  # or image_model_card == "adamo1139/stable-diffusion-3.5-large-ungated":
                        from huggingface_hub.commands.user import login

                        result = login(
                            token=addon_prefs.hugginface_token, add_to_git_credential=True
                        )
                        print(str(result))

                    # FLUX MacOS
                    if image_model_card == "ChuckMcSneed/FLUX.1-dev" and os_platform == "Darwin":
                        from mflux import Flux1, Config
                        converter = Flux1.from_name(
                           model_name="dev",  # "schnell" or "dev"
                           quantize=4,            # 4 or 8
                        )
                    elif image_model_card == "lzyvegetable/FLUX.1-schnell" and os_platform == "Darwin":
                        from mflux import Flux1, Config
                        converter = Flux1.from_name(
                           model_name="schnell",  # "schnell" or "dev"
                           quantize=4,            # 4 or 8
                        )
                    # Win
                    elif (
                        image_model_card == "lzyvegetable/FLUX.1-schnell"
                        or image_model_card == "ChuckMcSneed/FLUX.1-dev"
                        or image_model_card == "yuvraj108c/FLUX.1-Kontext-dev"
                        or image_model_card == "kontext-community/relighting-kontext-dev-lora-v3"
                    ):
                        relight = False
                        from diffusers import BitsAndBytesConfig, FluxTransformer2DModel

                        if image_model_card == "yuvraj108c/FLUX.1-Kontext-dev" or image_model_card == "kontext-community/relighting-kontext-dev-lora-v3":
                            from diffusers import FluxKontextPipeline

                        if image_model_card == "kontext-community/relighting-kontext-dev-lora-v3":
                            image_model_card = "yuvraj108c/FLUX.1-Kontext-dev"
                            relight = True

                        nf4_config = BitsAndBytesConfig(
                            load_in_4bit=True,
                            bnb_4bit_quant_type="nf4",
                            bnb_4bit_compute_dtype=torch.bfloat16,
                        )
                        model_nf4 = FluxTransformer2DModel.from_pretrained(
                            image_model_card,
                            subfolder="transformer",
                            quantization_config=nf4_config,
                            torch_dtype=torch.bfloat16,
                        )

                        if image_model_card == "yuvraj108c/FLUX.1-Kontext-dev":
                            converter = FluxKontextPipeline.from_pretrained(
                                image_model_card,
                                transformer=model_nf4,
                                torch_dtype=torch.bfloat16,
                                local_files_only=local_files_only,
                            )
                        else:
                            converter = AutoPipelineForImage2Image.from_pretrained(
                                image_model_card,
                                transformer=model_nf4,
                                torch_dtype=torch.bfloat16,
                                local_files_only=local_files_only,
                            )

                        if relight == True:
                            print("AI Relight: Loading and applying Relighting LoRA...")
                            converter.load_lora_weights(
                                "kontext-community/relighting-kontext-dev-lora-v3",
                                weight_name="relighting-kontext-dev-lora-v3.safetensors",
                                adapter_name="lora"
                            )
                            converter.set_adapters(["lora"], adapter_weights=[0.75])
                            image_model_card = "kontext-community/relighting-kontext-dev-lora-v3"

                        if gfx_device == "mps":
                            converter.to("mps")
                        elif low_vram():
                            converter.enable_sequential_cpu_offload()
                            #converter.enable_model_cpu_offload()
                            converter.vae.enable_slicing()
                            converter.vae.enable_tiling()
                        else:
                            converter.enable_model_cpu_offload()

                    elif (
                        image_model_card == "Runware/BFL-FLUX.2-klein-base-4B"
                        or image_model_card == "Runware/BFL-FLUX.2-klein-base-4B"
                    ):
                        from diffusers import Flux2KleinPipeline, Flux2Transformer2DModel
                        from transformers import Qwen3ForCausalLM


                        device = "cuda"
                        dtype = torch.bfloat16

                        transformer = Flux2Transformer2DModel.from_pretrained(
                            "OzzyGT/flux2_klein_9B_bnb_4bit_transformer", torch_dtype=dtype, device_map="cpu"
                        )

                        text_encoder = Qwen3ForCausalLM.from_pretrained(
                            "OzzyGT/flux2_klein_9B_bnb_4bit_text_encoder", torch_dtype=dtype, device_map="cpu"
                        )

                        converter = Flux2KleinPipeline.from_pretrained(
                            "black-forest-labs/FLUX.2-klein-9b-kv", transformer=transformer, text_encoder=text_encoder, torch_dtype=dtype
                        )
                        
                        if gfx_device == "mps":
                            converter.to("mps")
                        else:
                            converter.enable_model_cpu_offload()

                    # FLUX ControlNets
                    elif (
                        image_model_card == "fuliucansheng/FLUX.1-Canny-dev-diffusers-lora"
                    ) or (image_model_card == "romanfratric234/FLUX.1-Depth-dev-lora"):
                        from diffusers import FluxControlPipeline
                        from diffusers.utils import load_image
                        if image_model_card == "fuliucansheng/FLUX.1-Canny-dev-diffusers-lora":
                            pipecard = "fuliucansheng/FLUX.1-Canny-dev-diffusers"
                        else:
                            pipecard = "ChuckMcSneed/FLUX.1-dev"

                        from diffusers import BitsAndBytesConfig, FluxTransformer2DModel

                        nf4_config = BitsAndBytesConfig(
                            load_in_4bit=True,
                            bnb_4bit_quant_type="nf4",
                            bnb_4bit_compute_dtype=torch.bfloat16,
                        )
                        model_nf4 = FluxTransformer2DModel.from_pretrained(
                            pipecard,
                            subfolder="transformer",
                            quantization_config=nf4_config,
                            torch_dtype=torch.bfloat16,
                        )
                        converter = FluxControlPipeline.from_pretrained(
                            pipecard,
                            transformer=model_nf4,
                            torch_dtype=torch.bfloat16,
                            local_files_only=local_files_only,
                        )

                        if gfx_device == "mps":
                            converter.to("mps")
                        elif low_vram():
                            #pipe.enable_sequential_cpu_offload()
                            converter.enable_model_cpu_offload()
                            converter.vae.enable_slicing()
                            converter.vae.enable_tiling()
                        else:
                            #pipe.enable_sequential_cpu_offload()
                            converter.enable_model_cpu_offload()
                            converter.vae.enable_slicing()
                            converter.vae.enable_tiling()

                        if pipecard == "ChuckMcSneed/FLUX.1-dev":
                            converter.load_lora_weights(image_model_card)

                        if image_model_card == "fuliucansheng/FLUX.1-Canny-dev-diffusers-lora":
                            from controlnet_aux import CannyDetector
                            processor = CannyDetector()
                        else:
                            from image_gen_aux import DepthPreprocessor
                            processor = DepthPreprocessor.from_pretrained(
                                "LiheYoung/depth-anything-large-hf"
                            )

                    # redux
                    elif image_model_card == "Runware/FLUX.1-Redux-dev":
                        from transformers import SiglipImageProcessor, SiglipVisionModel
                        from diffusers import FluxPriorReduxPipeline, FluxPipeline
                        from diffusers.utils import load_image

                        converter = FluxPipeline.from_pretrained(
                            "ChuckMcSneed/FLUX.1-dev" ,
                            text_encoder=None,
                            text_encoder_2=None,
                            torch_dtype=torch.bfloat16,
                            #transformer=model_nf4,
                        )
                        pipe_prior_redux = FluxPriorReduxPipeline.from_pretrained("Runware/FLUX.1-Redux-dev", torch_dtype=torch.bfloat16).to("cuda")

                        if gfx_device == "mps":
                            converter.to("mps")
                        elif low_vram():
                            converter.enable_model_cpu_offload()
                            converter.vae.enable_slicing()
                            converter.vae.enable_tiling()
                        else:
                            converter.enable_sequential_cpu_offload()
                            #converter.enable_model_cpu_offload() # too slow
                            converter.vae.enable_slicing()
                            converter.vae.enable_tiling()

                    elif image_model_card == "Qwen/Qwen-Image-2512":
                        print("Load: Qwen-Image - img2img")

                        from diffusers.utils import load_image
                        from transformers import BitsAndBytesConfig as TransformersBitsAndBytesConfig
                        from transformers import Qwen2_5_VLForConditionalGeneration
                        from diffusers import BitsAndBytesConfig as DiffusersBitsAndBytesConfig
                        from diffusers import QwenImageImg2ImgPipeline, QwenImageTransformer2DModel

                        model_id = "Qwen/Qwen-Image-2512"
                        torch_dtype = torch.bfloat16
                        device = gfx_device

                        quantization_config_transformer = DiffusersBitsAndBytesConfig(
                            load_in_4bit=True,
                            bnb_4bit_quant_type="nf4",
                            bnb_4bit_compute_dtype=torch.bfloat16,
                            llm_int8_skip_modules=["transformer_blocks.0.img_mod"],
                        )

                        quantization_config_text_encoder = TransformersBitsAndBytesConfig(
                            load_in_4bit=True,
                            bnb_4bit_quant_type="nf4",
                            bnb_4bit_compute_dtype=torch.bfloat16,
                        )

                        transformer = QwenImageTransformer2DModel.from_pretrained(
                            model_id,
                            subfolder="transformer",
                            quantization_config=quantization_config_transformer,
                            torch_dtype=torch_dtype,
                        )
                        transformer = transformer.to("cpu")

                        text_encoder = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                            model_id,
                            subfolder="text_encoder",
                            quantization_config=quantization_config_text_encoder,
                            torch_dtype=torch_dtype,
                        )
                        text_encoder = text_encoder.to("cpu")

                        converter = QwenImageImg2ImgPipeline.from_pretrained(
                            model_id,
                            transformer=transformer,
                            text_encoder=text_encoder,
                            torch_dtype=torch_dtype
                        )
                        converter.load_lora_weights("Wuli-Art/Qwen-Image-2512-Turbo-LoRA", weight_name="Wuli-Qwen-Image-2512-Turbo-LoRA-4steps-V1.0-bf16.safetensors")
                        #converter.fuse_lora()

                        if gfx_device == "mps":
                            converter.to("mps")
                        elif low_vram():
                            converter.enable_model_cpu_offload()
                            converter.vae.enable_slicing()
                            converter.vae.enable_tiling()
                        else:
                            converter.enable_model_cpu_offload()
                            
                    # zimage turbo img2img
                    elif image_model_card == "Tongyi-MAI/Z-Image-Turbo":
                        from diffusers import ZImageImg2ImgPipeline, BitsAndBytesConfig, ZImageTransformer2DModel
                        from diffusers.utils import load_image
                        #from transformers import BitsAndBytesConfig  # Import needed for quantization

#                        # 1. Define the 8-bit configuration
#                        bnb_config = BitsAndBytesConfig(
#                            load_in_8bit=True,
#                            llm_int8_threshold=6.0
#                        )

                        # 2. Load the custom transformer with the quantization config
                        transformer = ZImageTransformer2DModel.from_pretrained(
                            "linoyts/beyond-reality-z-image-diffusers",
                            #quantization_config=bnb_config,  # Apply 8-bit quantization here
                            torch_dtype=torch.bfloat16,
                        )

                        # 3. Load the pipeline
                        converter = ZImageImg2ImgPipeline.from_pretrained(
                            "Tongyi-MAI/Z-Image-Turbo",
                            transformer=transformer,
                            torch_dtype=torch.bfloat16,
                        )
                        if gfx_device == "mps":
                            converter.to("mps")
                        elif low_vram():
                            #converter.enable_model_cpu_offload()
                            converter.enable_sequential_cpu_offload()
                            #converter.vae.enable_tiling()
                        else:
                            # pipe.enable_sequential_cpu_offload()
                            # pipe.vae.enable_tiling()
                            converter.enable_model_cpu_offload()            
                            #converter.to("cuda")  

                    # zimage img2img
                    elif image_model_card == "Tongyi-MAI/Z-Image":
                        from diffusers import ZImageImg2ImgPipeline
                        from diffusers.utils import load_image
                        converter = ZImageImg2ImgPipeline.from_pretrained(
                            "Tongyi-MAI/Z-Image",
                            torch_dtype=torch.bfloat16,
                            low_cpu_mem_usage=False,
                        )
                        if gfx_device == "mps":
                            converter.to("mps")
                        elif low_vram():
                            converter.enable_model_cpu_offload()
                            #pipe.enable_sequential_cpu_offload()
                            converter.vae.enable_tiling()
                        else:
                            # pipe.enable_sequential_cpu_offload()
                            # pipe.vae.enable_tiling()
                            #pipe.enable_model_cpu_offload()            
                            converter.to("cuda") 

                    else:
                        try:
                            converter = AutoPipelineForImage2Image.from_pretrained(
                                image_model_card,
                                torch_dtype=torch.float16,
                                variant="fp16",
                                local_files_only=local_files_only,
                            )
                        except:
                            try:
                                converter = AutoPipelineForImage2Image.from_pretrained(
                                    image_model_card,
                                    torch_dtype=torch.float16,
                                    local_files_only=local_files_only,
                                )
                            except:
                                print(
                                    "The "
                                    + image_model_card
                                    + " model does not work for a image to image pipeline!"
                                )
                                return {"CANCELLED"}
                        if gfx_device == "mps":
                            converter.to("mps")
                        elif low_vram():
                            converter.enable_model_cpu_offload()
                        else:
                            converter.to(gfx_device)

                if enabled_items:
                    if scene.use_lcm:
                        from diffusers import LCMScheduler

                        pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
                        if enabled_items:
                            enabled_names.append("lcm-lora-sdxl")
                            enabled_weights.append(1.0)
                            converter.load_lora_weights(
                                "latent-consistency/lcm-lora-sdxl",
                                weight_name="pytorch_lora_weights.safetensors",
                                adapter_name=("lcm-lora-sdxl"),
                            )
                        else:
                            converter.load_lora_weights("latent-consistency/lcm-lora-sdxl")

                        converter.watermark = NoWatermark()

                        if gfx_device == "mps":
                            converter.to("mps")
                        elif low_vram():
                            converter.enable_model_cpu_offload()
                        else:
                            converter.to(gfx_device)

            # Canny & Illusion
            elif image_model_card == "diffusers/controlnet-canny-sdxl-1.0-small":
                if image_model_card == "diffusers/controlnet-canny-sdxl-1.0-small":
                    print("Load: Canny")
                else:
                    print("Load: Illusion")
                from diffusers import (
                    ControlNetModel,
                    StableDiffusionXLControlNetPipeline,
                    AutoencoderKL,
                )

                controlnet = ControlNetModel.from_pretrained(
                    "diffusers/controlnet-canny-sdxl-1.0-small",
                    torch_dtype=torch.float16,
                    variant="fp16",
                    local_files_only=local_files_only,
                )
                vae = AutoencoderKL.from_pretrained(
                    "madebyollin/sdxl-vae-fp16-fix", torch_dtype=torch.float16
                )
                pipe = StableDiffusionXLControlNetPipeline.from_pretrained(
                    "stabilityai/stable-diffusion-xl-base-1.0",
                    controlnet=controlnet,
                    vae=vae,
                    torch_dtype=torch.float16,
                    variant="fp16",
                )

                pipe.watermark = NoWatermark()

                if scene.use_lcm:
                    from diffusers import LCMScheduler

                    pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
                    if enabled_items:
                        enabled_names.append("lcm-lora-sdxl")
                        enabled_weights.append(1.0)
                        pipe.load_lora_weights(
                            "latent-consistency/lcm-lora-sdxl",
                            weight_name="pytorch_lora_weights.safetensors",
                            adapter_name=("lcm-lora-sdxl"),
                        )
                    else:
                        pipe.load_lora_weights("latent-consistency/lcm-lora-sdxl")

                if gfx_device == "mps":
                    pipe.to("mps")
                elif low_vram():
                    pipe.enable_model_cpu_offload()
                else:
                    pipe.to(gfx_device)

            # OpenPose
            elif image_model_card == "xinsir/controlnet-openpose-sdxl-1.0":
                print("Load: OpenPose Model")

                from diffusers import (
                    ControlNetModel,
                    StableDiffusionXLControlNetPipeline,
                    AutoencoderKL,
                )
                from diffusers import DDIMScheduler, EulerAncestralDiscreteScheduler
                from controlnet_aux import OpenposeDetector
                from PIL import Image
                import torch
                import numpy as np

                # import cv2

                controlnet_conditioning_scale = 1.0

                eulera_scheduler = EulerAncestralDiscreteScheduler.from_pretrained(
                    "stabilityai/stable-diffusion-xl-base-1.0", subfolder="scheduler"
                )

                controlnet = ControlNetModel.from_pretrained(
                    "xinsir/controlnet-openpose-sdxl-1.0", torch_dtype=torch.float16
                )

                vae = AutoencoderKL.from_pretrained(
                    "madebyollin/sdxl-vae-fp16-fix", torch_dtype=torch.float16
                )

                pipe = StableDiffusionXLControlNetPipeline.from_pretrained(
                    "stabilityai/stable-diffusion-xl-base-1.0",
                    controlnet=controlnet,
                    vae=vae,
                    # safety_checker=None,
                    torch_dtype=torch.float16,
                    scheduler=eulera_scheduler,
                )

                processor = OpenposeDetector.from_pretrained("lllyasviel/ControlNet")

                if scene.use_lcm:
                    from diffusers import LCMScheduler

                    pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
                    pipe.load_lora_weights("latent-consistency/lcm-lora-sdxl")
                    scene.movie_num_guidance = 0

                if gfx_device == "mps":
                    pipe.to("mps")
                elif low_vram():
                    pipe.enable_model_cpu_offload()
                else:
                    pipe.to(gfx_device)

            # Scribble
            elif image_model_card == "xinsir/controlnet-scribble-sdxl-1.0":
                # https://huggingface.co/xinsir/controlnet-scribble-sdxl-1.0 #use this instead

                print("Load: Scribble Model")
                from controlnet_aux import PidiNetDetector, HEDdetector
                from diffusers import (
                    ControlNetModel,
                    StableDiffusionXLControlNetPipeline,
                    EulerAncestralDiscreteScheduler,
                    AutoencoderKL,
                )

                processor = HEDdetector.from_pretrained("lllyasviel/Annotators")
                checkpoint = "xinsir/controlnet-scribble-sdxl-1.0"
                controlnet = ControlNetModel.from_pretrained(
                    checkpoint,
                    torch_dtype=torch.float16,
                    local_files_only=local_files_only,
                )

                vae = AutoencoderKL.from_pretrained(
                    "madebyollin/sdxl-vae-fp16-fix", torch_dtype=torch.float16
                )

                pipe = StableDiffusionXLControlNetPipeline.from_pretrained(
                    "stabilityai/stable-diffusion-xl-base-1.0",
                    controlnet=controlnet,
                    torch_dtype=torch.float16,
                    vae=vae,
                    local_files_only=local_files_only,
                )
                if scene.use_lcm:
                    from diffusers import LCMScheduler

                    pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
                    pipe.load_lora_weights("latent-consistency/lcm-lora-sdxl")
                    #pipe.fuse_lora()
                    scene.movie_num_guidance = 0
                else:
                    eulera_scheduler = EulerAncestralDiscreteScheduler.from_pretrained(
                        "stabilityai/stable-diffusion-xl-base-1.0", subfolder="scheduler"
                    )

                if gfx_device == "mps":
                    pipe.to("mps")
                elif low_vram():
                    pipe.enable_model_cpu_offload()
                else:
                    pipe.to(gfx_device)

            # Remove Background
            elif image_model_card == "ZhengPeng7/BiRefNet_HR":
                print("Load: Remove Background")

                from transformers import AutoModelForImageSegmentation
                from torchvision import transforms
                from PIL import Image, ImageFilter
                import torch

                pipe = AutoModelForImageSegmentation.from_pretrained(
                    "ZhengPeng7/BiRefNet_HR", trust_remote_code=True
                )

                if gfx_device == "mps":
                    pipe.to("mps")
                elif low_vram():
                    pipe.enable_model_cpu_offload()
                else:
                    pipe.to(gfx_device)

            # SD3 Stable Diffusion 3
            elif (
                image_model_card == "adamo1139/stable-diffusion-3.5-medium-ungated"
            ):
                print("Load: Stable Diffusion 3.5 Medium Model")
                from diffusers import BitsAndBytesConfig, SD3Transformer2DModel
                from diffusers import StableDiffusion3Pipeline
                import torch

                nf4_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.bfloat16,
                )
                model_nf4 = SD3Transformer2DModel.from_pretrained(
                    image_model_card,
                    subfolder="transformer",
                    quantization_config=nf4_config,
                    torch_dtype=torch.bfloat16,
                )

                pipe = StableDiffusion3Pipeline.from_pretrained(
                    image_model_card, transformer=model_nf4, torch_dtype=torch.bfloat16
                )
                pipe.enable_model_cpu_offload()

            # SD3.5 Stable Diffusion 3.5
            elif image_model_card == "adamo1139/stable-diffusion-3.5-large-ungated":
                print("Load: Stable Diffusion 3.5 large Model")
                from huggingface_hub.commands.user import login

                result = login(
                    token=addon_prefs.hugginface_token, add_to_git_credential=True
                )
                print(str(result))

                import torch

                if not do_inpaint and not enabled_items and not do_convert:
                    from diffusers import BitsAndBytesConfig, SD3Transformer2DModel
                    from diffusers import StableDiffusion3Pipeline

                    nf4_config = BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_compute_dtype=torch.bfloat16,
                    )
                    model_nf4 = SD3Transformer2DModel.from_pretrained(
                        image_model_card,
                        subfolder="transformer",
                        quantization_config=nf4_config,
                        torch_dtype=torch.bfloat16,
                    )

                    pipe = StableDiffusion3Pipeline.from_pretrained(
                        image_model_card,
                        transformer=model_nf4,
                        torch_dtype=torch.bfloat16,
                    )
                    # pipe.enable_model_cpu_offload()
                else:
                    from diffusers import StableDiffusion3Pipeline

                    pipe = StableDiffusion3Pipeline.from_pretrained(
                        image_model_card,
                        torch_dtype=torch.float16,
                    )
                if gfx_device == "mps":
                    pipe.to("mps")
                else:
                    pipe.enable_model_cpu_offload()

            # FLUX MACOS
            elif image_model_card == "ChuckMcSneed/FLUX.1-dev" and os_platform == "Darwin":
                from huggingface_hub.commands.user import login

                result = login(
                    token=addon_prefs.hugginface_token, add_to_git_credential=True
                )
                print(str(result))
                from mflux import Flux1, Config
                pipe = Flux1.from_name(
                   model_name="dev",  # "schnell" or "dev"
                   quantize=4,            # 4 or 8
                )
            elif image_model_card == "lzyvegetable/FLUX.1-schnell" and os_platform == "Darwin":
                from huggingface_hub.commands.user import login

                result = login(
                    token=addon_prefs.hugginface_token, add_to_git_credential=True
                )
                print(str(result))
                from mflux import Flux1, Config
                pipe = Flux1.from_name(
                   model_name="schnell",  # "schnell" or "dev"
                   quantize=4,            # 4 or 8
                )

            # Flux
            elif (
                image_model_card == "lzyvegetable/FLUX.1-schnell"
                or image_model_card == "ChuckMcSneed/FLUX.1-dev"
            ):
                print("Load: Flux Model")
                clear_cuda_cache()
                import torch
                #from diffusers import FluxPipeline
                sys.path.append(os.path.dirname(__file__))
                #from pipelines.pipeline_flux_de_distill import FluxPipeline

                if not do_inpaint and not enabled_items and not do_convert:
                    sys.path.append(os.path.dirname(__file__))
                    from diffusers import BitsAndBytesConfig, FluxTransformer2DModel, FluxPipeline
                    #print("De-destilled")

                    nf4_config = BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_compute_dtype=torch.bfloat16,
                    )
                    model_nf4 = FluxTransformer2DModel.from_pretrained(
                        image_model_card,
                        subfolder="transformer",
                        quantization_config=nf4_config,
                        torch_dtype=torch.bfloat16,
                    )
    #                model_nf4 = FluxTransformer2DModel.from_pretrained(
    #                    "InstantX/flux-dev-de-distill-diffusers",
    #                    quantization_config=nf4_config,
    #                    torch_dtype=torch.bfloat16
    #                )

                    pipe = FluxPipeline.from_pretrained(
                        image_model_card, transformer=model_nf4, torch_dtype=torch.bfloat16
                    )

                    if gfx_device == "mps":
                        pipe.to("mps")
                    elif low_vram():
                        #pipe.enable_sequential_cpu_offload()
                        pipe.enable_model_cpu_offload()
                        pipe.vae.enable_slicing()
                        pipe.vae.enable_tiling()
                    else:
                        pipe.enable_model_cpu_offload()
                else:  # LoRA + img2img
                    from diffusers import BitsAndBytesConfig, FluxTransformer2DModel, FluxPipeline

                    nf4_config = BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_compute_dtype=torch.bfloat16,
                    )
                    model_nf4 = FluxTransformer2DModel.from_pretrained(
                        image_model_card,
                        subfolder="transformer",
                        quantization_config=nf4_config,
                        torch_dtype=torch.bfloat16,
                    )

                    pipe = FluxPipeline.from_pretrained(
                        image_model_card, transformer=model_nf4, torch_dtype=torch.bfloat16
                    )

                    # pipe = FluxPipeline.from_pretrained(image_model_card, torch_dtype=torch.bfloat16)

                    if gfx_device == "mps":
                        pipe.to("mps")
                    elif low_vram():
                        pipe.enable_model_cpu_offload()
                        pipe.vae.enable_slicing()
                        pipe.vae.enable_tiling()
                    else:
                        pipe.enable_model_cpu_offload()

            # FLUX Kontext
            elif image_model_card == "yuvraj108c/FLUX.1-Kontext-dev":
                from diffusers import BitsAndBytesConfig, FluxTransformer2DModel
                from diffusers import FluxKontextPipeline

                nf4_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.bfloat16,
                )
                model_nf4 = FluxTransformer2DModel.from_pretrained(
                    image_model_card,
                    subfolder="transformer",
                    quantization_config=nf4_config,
                    torch_dtype=torch.bfloat16,
                )

                converter = FluxKontextPipeline.from_pretrained(
                    image_model_card,
                    transformer=model_nf4,
                    torch_dtype=torch.bfloat16,
                    local_files_only=local_files_only,
                )

                if gfx_device == "mps":
                    converter.to("mps")
                elif low_vram():
                    converter.enable_sequential_cpu_offload()
                    #converter.enable_model_cpu_offload()
                    converter.vae.enable_slicing()
                    converter.vae.enable_tiling()
                else:
                    converter.enable_model_cpu_offload()
                    
            #FLUX Klein
            elif (
                image_model_card == "Runware/BFL-FLUX.2-klein-base-4B"
                or image_model_card == "black-forest-labs/FLUX.2-klein-9b-kv"
            ):
                from diffusers import Flux2KleinPipeline, Flux2Transformer2DModel
                from transformers import Qwen3ForCausalLM


                device = "cuda"
                dtype = torch.bfloat16

                transformer = Flux2Transformer2DModel.from_pretrained(
                    "OzzyGT/flux2_klein_9B_bnb_4bit_transformer", torch_dtype=dtype, device_map="cpu"
                )

                text_encoder = Qwen3ForCausalLM.from_pretrained(
                    "OzzyGT/flux2_klein_9B_bnb_4bit_text_encoder", torch_dtype=dtype, device_map="cpu"
                )

                pipe = Flux2KleinPipeline.from_pretrained(
                    "black-forest-labs/FLUX.2-klein-9b-kv", transformer=transformer, text_encoder=text_encoder, torch_dtype=dtype
                )
                
                if gfx_device == "mps":
                    pipe.to("mps")
                else:
                    pipe.enable_model_cpu_offload()

            # Qwen-Image
            elif image_model_card == "Qwen/Qwen-Image-2512":
                    clear_cuda_cache()

                    if not do_inpaint and not do_convert:

                        import torch
                        from diffusers import QwenImagePipeline, QwenImageTransformer2DModel
                        from transformers import Qwen2_5_VLForConditionalGeneration


                        torch_dtype = torch.bfloat16

                        transformer = QwenImageTransformer2DModel.from_pretrained(
                            "OzzyGT/Qwen-Image-2512-bnb-4bit-transformer", torch_dtype=torch_dtype, device_map="cpu"
                        )
                        text_encoder = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                            "OzzyGT/Qwen-Image-2512-bnb-4bit-text-encoder", torch_dtype=torch_dtype, device_map="cpu"
                        )

                        pipe = QwenImagePipeline.from_pretrained(
                            "Qwen/Qwen-Image-2512", transformer=transformer, text_encoder=text_encoder, torch_dtype=torch_dtype
                        )
                    else:
                        print("Load: Qwen-Image - img2img")

                        from diffusers.utils import load_image
                        from transformers import BitsAndBytesConfig as TransformersBitsAndBytesConfig
                        from transformers import Qwen2_5_VLForConditionalGeneration
                        from diffusers import BitsAndBytesConfig as DiffusersBitsAndBytesConfig
                        from diffusers import QwenImageImg2ImgPipeline, QwenImageTransformer2DModel

                        model_id = "Qwen/Qwen-Image-2512"
                        torch_dtype = torch.bfloat16
                        device = gfx_device

                        quantization_config_transformer = DiffusersBitsAndBytesConfig(
                            load_in_4bit=True,
                            bnb_4bit_quant_type="nf4",
                            bnb_4bit_compute_dtype=torch.bfloat16,
                            llm_int8_skip_modules=["transformer_blocks.0.img_mod"],
                        )

                        quantization_config_text_encoder = TransformersBitsAndBytesConfig(
                            load_in_4bit=True,
                            bnb_4bit_quant_type="nf4",
                            bnb_4bit_compute_dtype=torch.bfloat16,
                        )

                        transformer = QwenImageTransformer2DModel.from_pretrained(
                            model_id,
                            subfolder="transformer",
                            quantization_config=quantization_config_transformer,
                            torch_dtype=torch_dtype,
                        )
                        transformer = transformer.to("cpu")

                        text_encoder = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                            model_id,
                            subfolder="text_encoder",
                            quantization_config=quantization_config_text_encoder,
                            torch_dtype=torch_dtype,
                        )
                        text_encoder = text_encoder.to("cpu")

                        pipe = QwenImageImg2ImgPipeline.from_pretrained(
                            model_id,
                            transformer=transformer,
                            text_encoder=text_encoder,
                            torch_dtype=torch_dtype
                        )

                    pipe.load_lora_weights("Wuli-Art/Qwen-Image-2512-Turbo-LoRA", weight_name="Wuli-Qwen-Image-2512-Turbo-LoRA-4steps-V1.0-bf16.safetensors")

                    if gfx_device == "mps":
                        pipe.to("mps")
                    elif low_vram():
                        pipe.enable_model_cpu_offload()
                        pipe.vae.enable_slicing()
                        pipe.vae.enable_tiling()
                    else:
                        pipe.enable_model_cpu_offload()

            # Chroma
            elif image_model_card == "lodestones/Chroma":

                if not do_inpaint and not enabled_items and not do_convert:
                    import torch
                    from diffusers import BitsAndBytesConfig, ChromaTransformer2DModel, ChromaPipeline
                    from diffusers.quantizers import PipelineQuantizationConfig

                    if gfx_device == "mps" or low_vram():
                        print("Quant: 4-bit")

                        dtype = torch.bfloat16

                        repo_id = "imnotednamode/Chroma-v36-dc-diffusers"

                        pipeline_quant_config = PipelineQuantizationConfig(
                            quant_backend="bitsandbytes_4bit",
                            quant_kwargs={
                                "load_in_4bit": True,
                                "bnb_4bit_quant_type": "nf4",
                                "bnb_4bit_compute_dtype": dtype,
                                "llm_int8_skip_modules": ["distilled_guidance_layer"],
                            },
                            components_to_quantize=["transformer", "text_encoder"],
                        )

                        pipe = ChromaPipeline.from_pretrained(
                            "imnotednamode/Chroma-v36-dc-diffusers",
                            quantization_config=pipeline_quant_config,
                            torch_dtype=dtype,
                        )

                        if gfx_device == "mps":
                            pipe.to("mps")
                        elif low_vram():
                            pipe.enable_model_cpu_offload()
                            pipe.vae.enable_slicing()
                            pipe.vae.enable_tiling()
                    else:
                        print("Quant: 8-bit")
                        dtype = torch.bfloat16
                        pipe = ChromaPipeline.from_pretrained(
                            "imnotednamode/Chroma-v36-dc-diffusers",
                            quantization_config=PipelineQuantizationConfig(
                                quant_backend="bitsandbytes_8bit",
                                quant_kwargs={"load_in_8bit": True},
                                components_to_quantize=["transformer", "text_encoder_2"]
                            ),
                            torch_dtype=dtype,
                        )
                        pipe.to("cuda")
                else:
                    print("Inpaint, LoRA and img2img is not supported for Chroma!")

            elif image_model_card == "Tongyi-MAI/Z-Image-Turbo":
                from diffusers import ZImagePipeline, ZImageTransformer2DModel
                from transformers import BitsAndBytesConfig  # Import needed for quantization

                # 1. Define the 4-bit configuration
#                bnb_config = BitsAndBytesConfig(
#                    load_in_8bit=True,
#                    bnb_8bit_quant_type="nf8",
#                    bnb_8bit_compute_dtype=torch.float16,  # Standardize math to float16 to match bitsandbytes preference
#                )

                # 2. Load the custom transformer with the quantization config
                transformer = ZImageTransformer2DModel.from_pretrained(
                    "linoyts/beyond-reality-z-image-diffusers",
                    #quantization_config=bnb_config,  # Apply 4-bit quantization here
                    torch_dtype=torch.bfloat16,
                )

                # 3. Load the pipeline
                pipe = ZImagePipeline.from_pretrained(
                    "Tongyi-MAI/Z-Image-Turbo",
                    transformer=transformer,
                    torch_dtype=torch.bfloat16,
                )
                #pipe.vae.to(dtype=torch.float32)
                if gfx_device == "mps":
                    pipe.to("mps")
                elif low_vram():
                    #pipe.enable_model_cpu_offload()
                    pipe.enable_sequential_cpu_offload()
                    #pipe.vae.enable_tiling()
                else:
                    #pipe.enable_sequential_cpu_offload()
                    # pipe.vae.enable_tiling()
                    pipe.enable_model_cpu_offload()           
                    #pipe.to("cuda")   
                       
            elif image_model_card == "Tongyi-MAI/Z-Image":
                from diffusers import ZImagePipeline
                pipe = ZImagePipeline.from_pretrained(
                    "Tongyi-MAI/Z-Image",
                    torch_dtype=torch.bfloat16,
                    low_cpu_mem_usage=False,
                )
                if gfx_device == "mps":
                    pipe.to("mps")
                elif low_vram():
                    pipe.enable_model_cpu_offload()
                    #pipe.enable_sequential_cpu_offload()
                    pipe.vae.enable_tiling()
                else:
                    # pipe.enable_sequential_cpu_offload()
                    # pipe.vae.enable_tiling()
                    pipe.enable_model_cpu_offload()            
                                              
            elif image_model_card == "Alpha-VLLM/Lumina-Image-2.0":
                from diffusers import Lumina2Pipeline

                pipe = Lumina2Pipeline.from_pretrained(
                    "Alpha-VLLM/Lumina-Image-2.0", torch_dtype=torch.bfloat16
                )

                if gfx_device == "mps":
                    pipe.to("mps")
                elif low_vram():
                    pipe.enable_model_cpu_offload()
                    #pipe.enable_sequential_cpu_offload()
                    pipe.vae.enable_tiling()
                else:
                    # pipe.enable_sequential_cpu_offload()
                    # pipe.vae.enable_tiling()
                    pipe.enable_model_cpu_offload()

            # OmniGen
            elif image_model_card == "Shitao/OmniGen-v1-diffusers":
                from diffusers import OmniGenPipeline

                pipe = OmniGenPipeline.from_pretrained(
                    "Shitao/OmniGen-v1-diffusers", torch_dtype=torch.bfloat16
                )

                if gfx_device == "mps":
                    pipe.to("mps")
                elif low_vram():
                    pipe.enable_sequential_cpu_offload()
                    pipe.vae.enable_tiling()
                else:
                    # pipe.enable_sequential_cpu_offload()
                    # pipe.vae.enable_tiling()
                    pipe.enable_model_cpu_offload()

            # Qwen Multi-image
            elif image_model_card == "Qwen/Qwen-Image-Edit-2511":
                clear_cuda_cache()

                print("Load: Qwen-Image-Edit-2511")
                      
                
                # Import necessary classes for quantization and model components
                from transformers import BitsAndBytesConfig as TransformersBitsAndBytesConfig
                from transformers import Qwen2_5_VLForConditionalGeneration
                from diffusers import BitsAndBytesConfig as DiffusersBitsAndBytesConfig
                from diffusers import QwenImageEditPlusPipeline, QwenImageTransformer2DModel

                # Define model ID, data type, and device
                model_id = "Qwen/Qwen-Image-Edit-2511"
                torch_dtype = torch.bfloat16
                device = gfx_device

                # Configure 4-bit quantization for the transformer model
                quantization_config_diffusers = DiffusersBitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.bfloat16,
                    llm_int8_skip_modules=["transformer_blocks.0.img_mod"],
                )

                # Load the transformer model with quantization and move to CPU initially
                transformer = QwenImageTransformer2DModel.from_pretrained(
                    #"linoyts/Qwen-Image-Edit-Rapid-AIO",
                    model_id,
                    subfolder="transformer",
                    quantization_config=quantization_config_diffusers,
                    torch_dtype=torch_dtype,
                )
                transformer = transformer.to("cpu")

                # Configure 4-bit quantization for the text encoder
                quantization_config_transformers = TransformersBitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.bfloat16,
                )

                # Load the text encoder with quantization and move to CPU initially
                text_encoder = Qwen2_5_VLForConditionalGeneration.from_pretrained(
                    model_id,
                    subfolder="text_encoder",
                    quantization_config=quantization_config_transformers,
                    torch_dtype=torch_dtype,
                )
                text_encoder = text_encoder.to("cpu")

                # Assemble the pipeline from the pre-loaded, quantized components
                pipe = QwenImageEditPlusPipeline.from_pretrained(
                    model_id,
                    transformer=transformer,
                    text_encoder=text_encoder,
                    torch_dtype=torch_dtype
                )
                pipe.load_lora_weights("lightx2v/Qwen-Image-Edit-2511-Lightning", weight_name="Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors")
                #pipe.fuse_lora()

                print("Pipeline loaded")

                # Move the complete pipeline to the GPU for inference
                # pipeline.to(device)

                if gfx_device == "mps":
                    pipe.to("mps")
                elif low_vram():
                    pipe.enable_sequential_cpu_offload()
                    pipe.vae.enable_tiling()
                else:
                    pipe.enable_model_cpu_offload()
                    #pipe.enable_sequential_cpu_offload()
                    # pipe.vae.enable_tiling()
                    #pipe.to(gfx_device)

            # FLUX2                
            elif image_model_card == "diffusers/FLUX.2-dev-bnb-4bit":
                from transformers import Mistral3ForConditionalGeneration

                from diffusers import Flux2Pipeline, Flux2Transformer2DModel
                from huggingface_hub import login, get_token

                HF_TOKEN = addon_prefs.hugginface_token#login(

                try:
                    login(token=HF_TOKEN, add_to_git_credential=True)
                    print("Successfully logged in to Hugging Face.")
                except Exception as e:
                    print(f"Failed to log in: {e}")
                    return("CANCELLED")
                    
                repo_id = "diffusers/FLUX.2-dev-bnb-4bit"
                device = gfx_device#"cuda:0"
                torch_dtype = torch.bfloat16

                transformer = Flux2Transformer2DModel.from_pretrained(
                  repo_id, subfolder="transformer", torch_dtype=torch_dtype, device_map="cpu"
                )
                text_encoder = Mistral3ForConditionalGeneration.from_pretrained(
                  repo_id, subfolder="text_encoder", dtype=torch_dtype, device_map="cpu"
                )

                pipe = Flux2Pipeline.from_pretrained(
                  repo_id, transformer=transformer, text_encoder=text_encoder, torch_dtype=torch_dtype
                )
                
                pipe.load_lora_weights(
                    "fal/FLUX.2-dev-Turbo", 
                    weight_name="flux.2-turbo-lora.safetensors"
                )
                                
                if gfx_device == "mps":
                    pipe.to("mps")
                else:
                    pipe.enable_model_cpu_offload()        
                    pipe.vae.enable_tiling()

            # Stable diffusion etc.
            else:
                print("Load: " + image_model_card + " Model")

                if image_model_card == "stabilityai/stable-diffusion-xl-base-1.0":
                    if not (scene.ip_adapter_face_folder or scene.ip_adapter_style_folder):
                        from diffusers import AutoencoderKL

                        vae = AutoencoderKL.from_pretrained(
                            "madebyollin/sdxl-vae-fp16-fix",
                            torch_dtype=torch.float16,
                            local_files_only=local_files_only,
                        )
                        pipe = DiffusionPipeline.from_pretrained(
                            image_model_card,
                            vae=vae,
                            torch_dtype=torch.float16,
                            variant="fp16",
                            local_files_only=local_files_only,
                        )

                    # IPAdapter
                    else:
                        print("Loading: IP Adapter")
                        import torch
                        from diffusers import DDIMScheduler
                        from diffusers.utils import load_image

                        from transformers import CLIPVisionModelWithProjection

                        image_encoder = CLIPVisionModelWithProjection.from_pretrained(
                            "h94/IP-Adapter",
                            subfolder="models/image_encoder",
                            torch_dtype=torch.float16,
                            local_files_only=local_files_only,
                        )
                        if find_strip_by_name(scene, scene.inpaint_selected_strip):
                            from diffusers import AutoPipelineForInpainting

                            pipe = AutoPipelineForInpainting.from_pretrained(
                                "diffusers/stable-diffusion-xl-1.0-inpainting-0.1",
                                torch_dtype=torch.float16,
                                image_encoder=image_encoder,
                                local_files_only=local_files_only,
                            )
                        elif scene.input_strips == "input_strips" and (
                            scene.image_path or scene.movie_path
                        ):
                            from diffusers import AutoPipelineForImage2Image

                            pipe = AutoPipelineForImage2Image.from_pretrained(
                                "stabilityai/stable-diffusion-xl-base-1.0",
                                torch_dtype=torch.float16,
                                image_encoder=image_encoder,
                                local_files_only=local_files_only,
                            )
                        else:
                            from diffusers import AutoPipelineForText2Image

                            pipe = AutoPipelineForText2Image.from_pretrained(
                                "stabilityai/stable-diffusion-xl-base-1.0",
                                torch_dtype=torch.float16,
                                image_encoder=image_encoder,
                                local_files_only=local_files_only,
                            )
                        if scene.ip_adapter_face_folder and scene.ip_adapter_style_folder:
                            pipe.load_ip_adapter(
                                "h94/IP-Adapter",
                                subfolder="sdxl_models",
                                weight_name=[
                                    "ip-adapter-plus_sdxl_vit-h.safetensors",
                                    "ip-adapter-plus-face_sdxl_vit-h.safetensors",
                                ],
                                local_files_only=local_files_only,
                            )
                            pipe.set_ip_adapter_scale([0.7, 0.5])
                        elif scene.ip_adapter_face_folder:
                            pipe.load_ip_adapter(
                                "h94/IP-Adapter",
                                subfolder="sdxl_models",
                                weight_name=["ip-adapter-plus-face_sdxl_vit-h.safetensors"],
                                local_files_only=local_files_only,
                            )
                            pipe.set_ip_adapter_scale([0.8])
                        elif scene.ip_adapter_style_folder:
                            pipe.load_ip_adapter(
                                "h94/IP-Adapter",
                                subfolder="sdxl_models",
                                weight_name=["ip-adapter-plus_sdxl_vit-h.safetensors"],
                                local_files_only=local_files_only,
                            )
                            pipe.set_ip_adapter_scale([1.0])
                            pipe.scheduler = DDIMScheduler.from_config(
                                pipe.scheduler.config
                            )
                            
                    if gfx_device == "mps":
                        pipe.to("mps")
                    elif low_vram():
                        pipe.enable_model_cpu_offload()
                    else:
                        pipe.to(gfx_device)

                #                    scale = {
                #                        "down": {"block_2": [0.0, 1.0]},
                #                        "up": {"block_0": [0.0, 1.0, 0.0]},
                #                    }
                #                    pipe.set_ip_adapter_scale(scale)#[scale, scale])
                else:
                    print("Load: Auto Pipeline")
                    try:
                        from diffusers import AutoPipelineForText2Image

                        pipe = AutoPipelineForText2Image.from_pretrained(
                            image_model_card,
                            torch_dtype=torch.float16,
                            variant="fp16",
                            local_files_only=local_files_only,
                        )
                    except:
                        from diffusers import AutoPipelineForText2Image

                        pipe = AutoPipelineForText2Image.from_pretrained(
                            image_model_card,
                            torch_dtype=torch.float16,
                            local_files_only=local_files_only,
                        )

                # LCM
                if scene.use_lcm:
                    print("Use LCM: True")
                    from diffusers import LCMScheduler

                    if image_model_card == "stabilityai/stable-diffusion-xl-base-1.0":
                        if enabled_items:
                            enabled_names.append("lcm-lora-sdxl")
                            enabled_weights.append(1.0)
                            pipe.load_lora_weights(
                                "latent-consistency/lcm-lora-sdxl",
                                weight_name="pytorch_lora_weights.safetensors",
                                adapter_name=("lcm-lora-sdxl"),
                            )
                        else:
                            pipe.load_lora_weights("latent-consistency/lcm-lora-sdxl")
                        pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
                        scene.movie_num_guidance = 0
                    elif image_model_card == "segmind/Segmind-Vega":
                        scene.movie_num_guidance = 0
                        pipe.load_lora_weights("segmind/Segmind-VegaRT")
                        #pipe.fuse_lora()

            # LoRA
            if (
                (
                    image_model_card == "stabilityai/stable-diffusion-xl-base-1.0"
                    and ((not scene.image_path and not scene.movie_path) or do_inpaint)
                )
                or image_model_card == "xinsir/controlnet-openpose-sdxl-1.0"
                or image_model_card == "diffusers/controlnet-canny-sdxl-1.0-small"
                or image_model_card == "xinsir/controlnet-scribble-sdxl-1.0"
                or image_model_card == "lzyvegetable/FLUX.1-schnell"
                or image_model_card == "ChuckMcSneed/FLUX.1-dev"
                or image_model_card == "Qwen/Qwen-Image-Edit-2511"
                or image_model_card == "Qwen/Qwen-Image-2512"
                or image_model_card == "Tongyi-MAI/Z-Image"
                or image_model_card == "Tongyi-MAI/Z-Image-Turbo"
                or image_model_card == "Runware/BFL-FLUX.2-klein-base-4B"
                or image_model_card == "black-forest-labs/FLUX.2-klein-9b-kv"
    #            or image_model_card == "Runware/FLUX.1-Redux-dev"
    #            or image_model_card == "fuliucansheng/FLUX.1-Canny-dev-diffusers-lora"
    #            or image_model_card == "romanfratric234/FLUX.1-Depth-dev-lora"
            ):
                scene = context.scene
                if do_convert:
                    pipe = converter
                if enabled_items:
                    for item in enabled_items:
                        enabled_names.append((clean_filename(item.name)).replace(".", ""))
                        enabled_weights.append(item.weight_value)
                        pipe.load_lora_weights(
                            bpy.path.abspath(scene.lora_folder),
                            weight_name=item.name + ".safetensors",
                            adapter_name=((clean_filename(item.name)).replace(".", "")),
                        )
                    pipe.set_adapters(enabled_names, adapter_weights=enabled_weights)
                    print("Load LoRAs: " + " ".join(enabled_names))

            # Refiner model - load if chosen.
            if do_refine:
                print(
                    "Load Refine Model:  " + "thingthatis/stable-diffusion-xl-refiner-1.0"
                )
                from diffusers import StableDiffusionXLImg2ImgPipeline, AutoencoderKL

                vae = AutoencoderKL.from_pretrained(
                    "madebyollin/sdxl-vae-fp16-fix",
                    torch_dtype=torch.float16,
                    local_files_only=local_files_only,
                )
                refiner = StableDiffusionXLImg2ImgPipeline.from_pretrained(
                    "thingthatis/stable-diffusion-xl-refiner-1.0",
                    vae=vae,
                    torch_dtype=torch.float16,
                    variant="fp16",
                    local_files_only=local_files_only,
                )
                refiner.watermark = NoWatermark()
                if gfx_device == "mps":
                    pipe.to("mps")
                elif low_vram():
                    refiner.enable_model_cpu_offload()
                    # refiner.enable_vae_tiling()
                    # refiner.vae.enable_slicing()
                else:
                    refiner.to(gfx_device)
            
            # --- UPDATE GLOBAL CACHE ---
            _pallaidium_model_cache["pipe"] = pipe
            _pallaidium_model_cache["converter"] = converter
            _pallaidium_model_cache["refiner"] = refiner
            _pallaidium_model_cache["last_model_card"] = image_model_card

        # --------------------- Main Generate Loop Image -------------------------
        from PIL import Image
        import random

        for i in range(scene.movie_num_batch):
            start_time = timer()

            # Find free space for the strip in the timeline.

            if i > 0:
                empty_channel = scene.sequence_editor.active_strip.channel
                start_frame = (
                    scene.sequence_editor.active_strip.frame_final_start
                    + scene.sequence_editor.active_strip.frame_final_duration
                )
                scene.frame_current = (
                    scene.sequence_editor.active_strip.frame_final_start
                )
            else:
                empty_channel = find_first_empty_channel(
                    scene.frame_current,
                    (scene.movie_num_batch * duration) + scene.frame_current,
                )
                start_frame = scene.frame_current
            # Generate seed.

            seed = context.scene.movie_num_seed
            seed = (
                seed
                if not context.scene.movie_use_random
                else random.randint(-2147483647, 2147483647)
            )
            print("Seed: " + str(seed))
            context.scene.movie_num_seed = seed

            # Use cuda if possible.

            if torch.cuda.is_available():
                generator = (
                    torch.Generator("cuda").manual_seed(seed) if seed != 0 else None
                )
            else:
                if seed != 0:
                    generator = torch.Generator(device=gfx_device)
                    generator.manual_seed(seed)
                else:
                    generator = None

            # SDXL Canny & Illusion
            if image_model_card == "diffusers/controlnet-canny-sdxl-1.0-small":
                init_image = None
                if scene.image_path:
                    init_image = load_first_frame(scene.image_path)
                if scene.movie_path:
                    init_image = load_first_frame(scene.movie_path)
                if not init_image:
                    print("Loading strip failed!")
                    return {"CANCELLED"}
                image = scale_image_within_dimensions(np.array(init_image), x, None)

                if image_model_card == "diffusers/controlnet-canny-sdxl-1.0-small":
                    print("Process: Canny")
                    image = np.array(init_image)
                    low_threshold = 100
                    high_threshold = 200
                    image = cv2.Canny(image, low_threshold, high_threshold)
                    image = image[:, :, None]
                    canny_image = np.concatenate([image, image, image], axis=2)
                    canny_image = Image.fromarray(canny_image)
                    # canny_image = np.array(canny_image)

                    image = pipe(
                        prompt=prompt,
                        # negative_prompt=negative_prompt,
                        num_inference_steps=image_num_inference_steps,  # Should be around 50
                        controlnet_conditioning_scale=1.00 - scene.image_power,
                        image=canny_image,
                        #                    guidance_scale=clamp_value(
                        #                        image_num_guidance, 3, 5
                        #                    ),  # Should be between 3 and 5.
                        #                    # guess_mode=True, #NOTE: Maybe the individual methods should be selectable instead?
                        #                    height=y,
                        #                    width=x,
                        #                    generator=generator,
                    ).images[0]
                else:
                    print("Process: Illusion")
                    illusion_image = init_image

                    image = pipe(
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        num_inference_steps=image_num_inference_steps,  # Should be around 50
                        control_image=illusion_image,
                        controlnet_conditioning_scale=1.00 - scene.image_power,
                        generator=generator,
                        control_guidance_start=0,
                        control_guidance_end=1,
                        # output_type="latent"
                        #                    guidance_scale=clamp_value(
                        #                        image_num_guidance, 3, 5
                        #                    ),  # Should be between 3 and 5.
                        #                    # guess_mode=True, #NOTE: Maybe the individual methods should be selectable instead?
                        #                    height=y,
                        #                    width=x,
                    ).images[0]

            # OpenPose
            elif image_model_card == "xinsir/controlnet-openpose-sdxl-1.0":
                image = None
                if scene.image_path:
                    image = load_first_frame(scene.image_path)
                if scene.movie_path:
                    image = load_first_frame(scene.movie_path)
                if not image:
                    print("Loading strip failed!")
                    clear_cuda_cache()
                    return {"CANCELLED"}
                image = image.resize((x, y))
                # image = scale_image_within_dimensions(np.array(init_image),x,None)

                # Make OpenPose bones from normal image

                if not scene.openpose_use_bones:
                    image = np.array(image)

                    image = processor(image, hand_and_face=True)
                    # Save pose image
                    filename = clean_filename(
                        str(seed) + "_" + context.scene.generate_movie_prompt
                    )
                    out_path = solve_path("Pose_" + filename + ".png")
                    print("Saving OpenPoseBone image: " + out_path)
                    image.save(out_path)
                # OpenPose from prompt
                # if not (scene.ip_adapter_face_folder or scene.ip_adapter_style_folder):

                print("Process: OpenPose")
                image = pipe(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    image=image,
                    controlnet_conditioning_scale=controlnet_conditioning_scale,
                    num_inference_steps=image_num_inference_steps,
                    # guidance_scale=image_num_guidance,
                    generator=generator,
                ).images[0]

            # Scribble
            elif image_model_card == "xinsir/controlnet-scribble-sdxl-1.0":
                print("Process: Scribble")
                init_image = None

                if scene.image_path:
                    init_image = load_first_frame(scene.image_path)
                if scene.movie_path:
                    init_image = load_first_frame(scene.movie_path)
                if not init_image:
                    print("Loading strip failed!")
                    clear_cuda_cache()
                    return {"CANCELLED"}
                image = scale_image_within_dimensions(np.array(init_image), x, None)

                if not scene.use_scribble_image:
                    image = np.array(image)
                    image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                    image = cv2.bitwise_not(image)
                    image = cv2.GaussianBlur(image, (0, 0), 3)

                    # higher threshold, thiner line
                    random_val = int(round(random.uniform(0.01, 0.10), 2) * 255)
                    image[image > random_val] = 255
                    image[image < 255] = 0
                    image = Image.fromarray(image)
                    image = processor(image, scribble=True)
                else:
                    image = np.array(image)
                    image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                    image = cv2.bitwise_not(image)
                    image = processor(image, scribble=True)

                image = pipe(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    image=image,
                    num_inference_steps=image_num_inference_steps,
                    guidance_scale=image_num_guidance,
                    controlnet_conditioning_scale=1.0,
                    height=y,
                    width=x,
                    generator=generator,
                ).images[0]

            # FLUX ControlNets
            elif (image_model_card == "fuliucansheng/FLUX.1-Canny-dev-diffusers-lora") or (
                image_model_card == "romanfratric234/FLUX.1-Depth-dev-lora"
            ):
                print("Process: Flux ControlNets")
                init_image = None

                if scene.image_path:
                    init_image = load_first_frame(scene.image_path)
                if scene.movie_path:
                    init_image = load_first_frame(scene.movie_path)
                if not init_image:
                    print("Loading strip failed!")
                    clear_cuda_cache()
                    return {"CANCELLED"}
                image = init_image
                #image = scale_image_within_dimensions(np.array(init_image), x, None)

                if image_model_card == "fuliucansheng/FLUX.1-Canny-dev-diffusers-lora":
                    image = processor(
                        image,
                        low_threshold=50,
                        high_threshold=200,
                        detect_resolution=x,
                        image_resolution=x,
                    )
                else:
                    #from image_gen_aux import DepthPreprocessor
                    #processor = DepthPreprocessor.from_pretrained("LiheYoung/depth-anything-large-hf")
                    image = processor(image)[0].convert("RGB")
                    #image = get_depth_map(image)

                image = converter(
                    prompt=prompt,
                    control_image=image,
                    num_inference_steps=image_num_inference_steps,
                    guidance_scale=image_num_guidance,
                    # controlnet_conditioning_scale=1.0,
                    height=y,
                    width=x,
                    generator=generator,
                ).images[0]

            elif image_model_card == "Runware/FLUX.1-Redux-dev":
                init_image = None

                if scene.image_path:
                    init_image = load_first_frame(scene.image_path)
                if scene.movie_path:
                    init_image = load_first_frame(scene.movie_path)
                if not init_image:
                    print("Loading strip failed!")
                    clear_cuda_cache()
                    return {"CANCELLED"}
                image = init_image
                pipe_prior_output = pipe_prior_redux(image)
                image = converter(
                    num_inference_steps=image_num_inference_steps,
                    guidance_scale=image_num_guidance,
                    **pipe_prior_output,
                    height=y,
                    width=x,
                    generator=generator,
                ).images[0]

            # Remove Background
            elif image_model_card == "ZhengPeng7/BiRefNet_HR":
                init_image = None

                if scene.image_path:
                    init_image = load_first_frame(scene.image_path)
                if scene.movie_path:
                    init_image = load_first_frame(scene.movie_path)
                if not init_image:
                    print("Loading strip failed!")
                    clear_cuda_cache()
                    return {"CANCELLED"}
                image = scale_image_within_dimensions(np.array(init_image), x, None)

                transform_image = transforms.Compose(
                    [
                        transforms.Resize((2048, 2048)),
                        transforms.ToTensor(),
                        transforms.Normalize(
                            [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]
                        ),
                    ]
                )

                # Load and transform the image
                image = Image.fromarray(image).convert("RGB")
                image_size = image.size
                input_image = transform_image(image).unsqueeze(0).to("cuda")

                # Generate the background mask
                with torch.no_grad():
                    preds = pipe(input_image)[-1].sigmoid().cpu()
                pred = preds[0].squeeze()
                mask = transforms.ToPILImage()(pred)
                mask = mask.resize(image_size)

                #                # Refine the mask: Apply thresholding and feathering for smoother removal
                #                mask = mask.convert("L")

                #                threshold_value = 200
                #                mask = mask.point(lambda p: 255 if p > threshold_value else 0)

                #                feather_radius = 1
                #                mask = mask.filter(ImageFilter.GaussianBlur(feather_radius))

                # Apply the refined mask to the image to remove the background
                image.putalpha(mask)

            elif image_model_card == "Alpha-VLLM/Lumina-Image-2.0":
                inference_parameters = {
                    "prompt": prompt,
                    "negative_prompt": negative_prompt,
                    "num_inference_steps": image_num_inference_steps,
                    "guidance_scale": image_num_guidance,
                    "height": y,
                    "width": x,
                    "cfg_trunc_ratio": 0.25,
                    "cfg_normalization": True,
                    "generator": generator,
                }
                image = pipe(
                    **inference_parameters,
                ).images[0]

            # OmniGen
            elif image_model_card == "Shitao/OmniGen-v1-diffusers":
                omnigen_images = []

                prompt = scene.omnigen_prompt_1
                if find_strip_by_name(scene, scene.omnigen_strip_1):
                    omnigen_images.append(
                        load_first_frame(
                            get_strip_path(
                                find_strip_by_name(scene, scene.omnigen_strip_1)
                            )
                        )
                    )
                    prompt = prompt + " <img><|image_1|></img> "

                prompt = prompt + scene.omnigen_prompt_2
                if find_strip_by_name(scene, scene.omnigen_strip_2):
                    omnigen_images.append(
                        load_first_frame(
                            get_strip_path(
                                find_strip_by_name(scene, scene.omnigen_strip_2)
                            )
                        )
                    )
                    prompt = prompt + " <img><|image_2|></img> "

                prompt = prompt + scene.omnigen_prompt_3
                if find_strip_by_name(scene, scene.omnigen_strip_3):
                    omnigen_images.append(
                        load_first_frame(
                            get_strip_path(
                                find_strip_by_name(scene, scene.omnigen_strip_3)
                            )
                        )
                    )
                    prompt = prompt + " <img><|image_3|></img> "
                print(prompt)

                if not omnigen_images:
                    omnigen_images = None
                    img_size = False
                else:
                    img_size = True
                inference_parameters = {
                    "prompt": prompt,
                    "input_images": omnigen_images,
                    "img_guidance_scale": scene.img_guidance_scale,
                    "use_input_image_size_as_output": img_size,
                    "num_inference_steps": image_num_inference_steps,
                    "guidance_scale": image_num_guidance,
                    "height": y,
                    "width": x,
                    "generator": generator,
                }
                image = pipe(
                    **inference_parameters,
                ).images[0]

            #Qwen Multi-image
            elif image_model_card == "Qwen/Qwen-Image-Edit-2511":

                qwen_images = []
                init_image = None

                if scene.input_strips == "input_strips":
                    if scene.image_path:
                        init_image = load_first_frame(scene.image_path)
                    if scene.movie_path:
                        init_image = load_first_frame(scene.movie_path)
                    if init_image:
                        qwen_images.append(init_image)

                if find_strip_by_name(scene, scene.qwen_strip_1):
                    qwen_images.append(
                        load_first_frame(
                            get_strip_path(
                                find_strip_by_name(scene, scene.qwen_strip_1)
                            )
                        )
                    )

                if find_strip_by_name(scene, scene.qwen_strip_2):
                    qwen_images.append(
                        load_first_frame(
                            get_strip_path(
                                find_strip_by_name(scene, scene.qwen_strip_2)
                            )
                        )
                    )

                if init_image != None and find_strip_by_name(scene, scene.qwen_strip_3):
                    qwen_images.append(
                        load_first_frame(
                            get_strip_path(
                                find_strip_by_name(scene, scene.qwen_strip_3)
                            )
                        )
                    )

                if not qwen_images:
                    qwen_images = None
                    print("No input images found. Cancelled!")
                    clear_cuda_cache()
                    return {"CANCELLED"}

                inference_parameters = {
                    "image": qwen_images,
                    "prompt": prompt,
                    "generator": generator,
                    "true_cfg_scale": 4.0,
                    "negative_prompt": negative_prompt+" ",
                    "num_inference_steps": image_num_inference_steps,
                    #"guidance_scale": 1.0,
                    "num_images_per_prompt": 1,
#                    "height": y,
#                    "width": x,
                }

                with torch.inference_mode():
                    image = pipe(
                        **inference_parameters,
                    ).images[0]
#                    output = pipeline(**inputs)
#                    output_image = output.images[0]
#                    output_image.save("output_image_edit_plus.png")
#                    print("Image saved at", os.path.abspath("output_image_edit_plus.png"))

            # FLUX2 image input               
            elif image_model_card == "diffusers/FLUX.2-dev-bnb-4bit":
                
                flux_images = []
                init_image = None

                # Handle initial image from image_path or movie_path
                if scene.input_strips == "input_strips":
                    if scene.image_path:
                        init_image = load_first_frame(scene.image_path)
                    elif scene.movie_path: # Use elif to prioritize image_path if both exist
                        init_image = load_first_frame(scene.movie_path)
                    if init_image:
                        flux_images.append(init_image)

                # Iterate through flux_strip_1 to flux_strip_9
                for i in range(1, 10):
                    strip_attr = f"flux_strip_{i}"
                    if hasattr(scene, strip_attr) and getattr(scene, strip_attr):
                        strip_name = getattr(scene, strip_attr)
                        found_strip = find_strip_by_name(scene, strip_name)
                        if found_strip:
                            flux_images.append(load_first_frame(get_strip_path(found_strip)))

                if not flux_images:
                    flux_images = None
#                    print("No input images found. Cancelled!")
#                    clear_cuda_cache()
#                    return {"CANCELLED"}

                image = pipe(
                    image=flux_images,
                    prompt=prompt,
                    generator=generator,#generator=torch.Generator(device=device).manual_seed(42),
                    max_sequence_length=512,
                    num_inference_steps=image_num_inference_steps,
                    guidance_scale=image_num_guidance,
                    height=y,
                    width=x,
                ).images[0]


            # Inpaint
            elif do_inpaint:
                mask_image = None
                init_image = None
                image_reference = None
                mask_strip = find_strip_by_name(scene, scene.inpaint_selected_strip)

                if not mask_strip:
                    print("Selected mask not found!")
                    clear_cuda_cache()
                    return {"CANCELLED"}
                if (
                    mask_strip.type == "MASK"
                    or mask_strip.type == "COLOR"
                    or mask_strip.type == "SCENE"
                    or mask_strip.type == "META"
                ):
                    mask_strip = get_render_strip(self, context, mask_strip)
                mask_path = get_strip_path(mask_strip)
                mask_image = load_first_frame(mask_path)

                if not mask_image:
                    print("Loading mask failed!")
                    return
                mask_image = mask_image.resize((x, y))
                mask_image = pipe.mask_processor.blur(mask_image, blur_factor=33)

                if scene.image_path:
                    init_image = load_first_frame(scene.image_path)
                if scene.movie_path:
                    init_image = load_first_frame(scene.movie_path)
                if not init_image:
                    print("Loading init image failed!")
                    clear_cuda_cache()
                    return {"CANCELLED"}
                else:
                    init_image = init_image.resize((x, y))

                if scene.kontext_strip_1:
                    if find_strip_by_name(scene, scene.kontext_strip_1):
                        input_image = load_first_frame(
                            get_strip_path(
                                find_strip_by_name(scene, scene.kontext_strip_1)
                            )
                        )
                    image_reference = input_image

                print(f"Init image loaded:      {init_image is not None}")
                print(f"Mask image loaded:      {mask_image is not None}")
                print(f"Reference image loaded: {image_reference is not None}")

                if (
                    image_model_card == "lzyvegetable/FLUX.1-schnell"
                    or image_model_card == "ChuckMcSneed/FLUX.1-dev"
                    or image_model_card == "Runware/BFL-FLUX.2-klein-base-4B"
                    or image_model_card == "black-forest-labs/FLUX.2-klein-9b-kv"
                ):
                    print("Process Inpaint: " + image_model_card)
                    inference_parameters = {
                        "prompt": prompt,
                        # "prompt_2": None, # Uncomment if your pipe supports/requires it
                        "max_sequence_length": 512,
                        "image": init_image,
                        "mask_image": mask_image,
                        "num_inference_steps": image_num_inference_steps, # Ensure this has a value
                        "guidance_scale": image_num_guidance,            # Ensure this has a value
                        "height": y,
                        "width": x,
                        "generator": generator,
                        # "padding_mask_crop": 42, # Uncomment if needed
                        # "strength": 0.5,       # Uncomment if needed
                    }

                    if image_model_card == "lzyvegetable/FLUX.1-schnell":
                        # Override specific parameters for FLUX
                        inference_parameters["guidance_scale"] = 0
                        inference_parameters["num_inference_steps"] = 4

                    image = pipe(
                        **inference_parameters
                    ).images[0]

                # Kontext Inpaint
                elif (
                    image_model_card == "yuvraj108c/FLUX.1-Kontext-dev"
                ):

                    print("Process Inpaint: " + image_model_card)
                    inference_parameters = {
                        "prompt": prompt,
                        # "prompt_2": None, # Uncomment if your pipe supports/requires it
                        "max_sequence_length": 512,
                        "image": init_image,
                        "mask_image": mask_image,
                        "image_reference": image_reference,
                        "num_inference_steps": image_num_inference_steps, # Ensure this has a value
                        "guidance_scale": image_num_guidance,            # Ensure this has a value
                        "height": y,
                        "width": x,
                        "generator": generator,
                        "strength": 1.00 - scene.image_power,
                        # "padding_mask_crop": 42, # Uncomment if needed
                        # "strength": 0.5,       # Uncomment if needed
                    }

                    if image_model_card == "lzyvegetable/FLUX.1-schnell":
                        # Override specific parameters for FLUX
                        inference_parameters["guidance_scale"] = 0
                        inference_parameters["num_inference_steps"] = 4

                    image = pipe(
                        **inference_parameters
                    ).images[0]

                elif image_model_card == "stabilityai/stable-diffusion-xl-base-1.0":
                    print("Process Inpaint: " + image_model_card)
                    inference_parameters = {
                        "prompt": prompt,
                        "negative_prompt": negative_prompt,
                        "image": init_image,
                        "mask_image": mask_image,
                        "num_inference_steps": image_num_inference_steps,
                        "guidance_scale": image_num_guidance,
                        "height": y,
                        "width": x,
                        "generator": generator,
                        "padding_mask_crop": 42,
                        "strength": 0.99,
                    }
                    image = pipe(
                        **inference_parameters,
                    ).images[0]

                #                # Limit inpaint to maske area:
                #                # Convert mask to grayscale NumPy array
                #                mask_image_arr = np.array(mask_image.convert("L"))

                #                # Add a channel dimension to the end of the grayscale mask
                #                mask_image_arr = mask_image_arr[:, :, None]
                #                mask_image_arr = mask_image_arr.astype(np.float32) / 255.0
                #                mask_image_arr[mask_image_arr < 0.5] = 0
                #                mask_image_arr[mask_image_arr >= 0.5] = 1

                #                # Take the masked pixels from the repainted image and the unmasked pixels from the initial image
                #                unmasked_unchanged_image_arr = (
                #                    1 - mask_image_arr
                #                ) * init_image + mask_image_arr * image
                #                image = PIL.Image.fromarray(
                #                    unmasked_unchanged_image_arr.astype("uint8")
                #                )

                delete_strip(mask_strip)

            # Img2img
            elif do_convert:  # and not scene.aurasr:
                if enabled_items:
                    self.report(
                        {"INFO"},
                        "LoRAs are ignored for image to image processing.",
                    )
                img_path = None
                if scene.movie_path:
                    print("Process: Image to Image")
                    init_image = load_first_frame(scene.movie_path)
                    init_image = init_image.resize((x, y))
                elif scene.image_path:
                    print("Process: Image to Image")
                    init_image = load_first_frame(scene.image_path)
                    init_image = init_image.resize((x, y))
                    img_path=scene.image_path
                # init_image = load_image(scene.image_path).convert("RGB")
                print("X: " + str(x), "Y: " + str(y))

                # MacOS
                if (image_model_card == "ChuckMcSneed/FLUX.1-dev" and os_platform == "Darwin") or (image_model_card == "lzyvegetable/FLUX.1-schnell" and os_platform == "Darwin"):
                    if not img_path:
                        print("Please, input an image!")
                        clear_cuda_cache()
                        return {"CANCELLED"}
                    image = converter.generate_image(
                       seed=abs(int(seed)),
                       prompt=prompt,
                       config=Config(
                          num_inference_steps=image_num_inference_steps,  # "schnell" works well with 2-4 steps, "dev" works well with 20-25 steps
                          height=y,
                          width=x,
                          image_path=os.path.abspath(img_path),
                          image_strength=1.00-scene.image_power,
                       )
                    )

                elif (
                    image_model_card == "lzyvegetable/FLUX.1-schnell"
                ):
                    image = converter(
                        prompt=prompt,
                        prompt_2=None,
                        max_sequence_length=512,
                        image=init_image,
                        strength=1.00 - scene.image_power,
                        # negative_prompt=negative_prompt,
                        num_inference_steps=image_num_inference_steps,
                        guidance_scale=0.0,
                        height=y,
                        width=x,
                        generator=generator,
                    ).images[0]


                elif (
                    image_model_card == "ChuckMcSneed/FLUX.1-dev"
                ):
                    image = converter(
                        prompt=prompt,
                        #prompt_2=None,
                        max_sequence_length=512,
                        image=init_image,
                        strength=1.00 - scene.image_power,
                        # negative_prompt=negative_prompt,
                        num_inference_steps=image_num_inference_steps,
                        guidance_scale=image_num_guidance,
                        height=y,
                        width=x,
                        generator=generator,
                    ).images[0]
                elif (
                    image_model_card == "Runware/BFL-FLUX.2-klein-base-4B" or image_model_card == "black-forest-labs/FLUX.2-klein-9b-kv"
                ):
                    image = converter(
                        prompt=prompt,
                        #prompt_2=None,
                        max_sequence_length=512,
                        image=init_image,
                        #strength=1.00 - scene.image_power,
                        # negative_prompt=negative_prompt,
                        guidance_scale=1.0,
                        num_inference_steps=4,
                        height=y,
                        width=x,
                        generator=generator,
                    ).images[0]
                elif (
                    image_model_card == "Qwen/Qwen-Image-2512"
                ):
                    image = converter(
                        prompt=prompt,
                        #prompt_2=None,
                        negative_prompt=negative_prompt,
                        max_sequence_length=512,
                        image=init_image,
                        strength=1.00 - scene.image_power,
                        # negative_prompt=negative_prompt,
                        num_inference_steps=image_num_inference_steps,
                        guidance_scale=image_num_guidance,
                        height=y,
                        width=x,
                        generator=generator,
                    ).images[0]
                elif (
                    image_model_card == "yuvraj108c/FLUX.1-Kontext-dev"
                ):

                    kontext_images = []
                    if scene.kontext_strip_1:
                        if find_strip_by_name(scene, scene.kontext_strip_1):
                            input_image = load_first_frame(
                                get_strip_path(
                                    find_strip_by_name(scene, scene.kontext_strip_1)
                                )
                            )
                        init_image = input_image

                    if not kontext_images:
                        kontext_images = None
                        img_size = False
                    else:
                        img_size = True

                    image = converter(
                        prompt=prompt,
                        #prompt_2=None,
                        max_sequence_length=512,
                        #input_images=kontext_images,
                        #image=kontext_images,
                        image=init_image,
                        #strength=1.00 - scene.image_power,
                        # negative_prompt=negative_prompt,
                        num_inference_steps=image_num_inference_steps,
                        guidance_scale=image_num_guidance,
                        height=y,
                        width=x,
                        generator=generator,
                    ).images[0]

                elif (
                    image_model_card == "kontext-community/relighting-kontext-dev-lora-v3"
                ):

                    prompt_description = ""
                    style_and_direction_parts = []

                    if prompt:
                        prompt_description = prompt
                        style_and_direction_parts.append("with custom lighting")
                    else:
                        prompt_description = ILLUMINATION_OPTIONS.get(context.scene.illumination_style, "")
                        style_and_direction_parts.append(f"with {context.scene.illumination_style} lighting")

                    if context.scene.light_direction != "auto":
                        style_and_direction_parts.append(f"coming from the {context.scene.light_direction}")

                    style_description = " ".join(style_and_direction_parts)
                    final_prompt = (
                        f"Relight the image {style_description}. "
                        f"{prompt_description} "
                        "Maintain the identity of the foreground subjects."
                    )

                    print(f"AI Relight: Running inference with prompt: {final_prompt}")
                    image = converter(
                        image=init_image, prompt=final_prompt, num_inference_steps=image_num_inference_steps, guidance_scale=image_num_guidance,
                        width=x, height=y, generator=generator
                    ).images[0]
                    
                elif image_model_card == "Tongyi-MAI/Z-Image":
                    image = converter(
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        max_sequence_length=512,
                        image=init_image,
                        strength=1.00 - scene.image_power,
                        num_inference_steps=image_num_inference_steps,
                        guidance_scale=image_num_guidance,
                        height=y,
                        width=x,
                        generator=generator,
                    ).images[0] 
                elif image_model_card == "Tongyi-MAI/Z-Image-Turbo":
                    image = converter(
                        prompt=prompt,
                        #prompt_2=None,
                        negative_prompt=negative_prompt,
                        max_sequence_length=512,
                        image=init_image,
                        strength=1.00 - scene.image_power,
                        num_inference_steps=9,
                        guidance_scale=0.0,
                        # negative_prompt=negative_prompt,
#                        num_inference_steps=image_num_inference_steps,
#                        guidance_scale=image_num_guidance,
                        height=y,
                        width=x,
                        generator=generator,
                    ).images[0]                  

                # Not Turbo
                else:
                    image = converter(
                        prompt=prompt,
                        image=init_image,
                        strength=1.00 - scene.image_power,
                        negative_prompt=negative_prompt,
                        num_inference_steps=image_num_inference_steps,
                        guidance_scale=image_num_guidance,
                        # height=y,
                        # width=x,
                        generator=generator,
                    ).images[0]

            # MacOS
            elif (image_model_card == "ChuckMcSneed/FLUX.1-dev" and os_platform == "Darwin") or (image_model_card == "lzyvegetable/FLUX.1-schnell" and os_platform == "Darwin"):
                image = pipe.generate_image(
                   seed=abs(int(seed)),
                   prompt=prompt,
                   config=Config(
                      num_inference_steps=image_num_inference_steps,  # "schnell" works well with 2-4 steps, "dev" works well with 20-25 steps
                      height=y,
                      width=x,
                   )
                )

            # Flux Schnell
            elif (
                image_model_card == "lzyvegetable/FLUX.1-schnell"
            ):  # and not scene.aurasr:
                inference_parameters = {
                    "prompt": prompt,
                    "prompt_2": None,
                    "max_sequence_length": 512,
                    "num_inference_steps": image_num_inference_steps,
                    "guidance_scale": image_num_guidance,
                    "height": y,
                    "width": x,
                    "generator": generator,
                }
                image = pipe(
                    **inference_parameters,
                ).images[0]

            # Flux Dev
            elif (
                image_model_card == "ChuckMcSneed/FLUX.1-dev"
            ):
                inference_parameters = {
                    "prompt": prompt,
                    "prompt_2": None,
                    "negative_prompt": negative_prompt,
                    "max_sequence_length": 512,
                    #"image": init_image,
                    #"mask_image": mask_image,
                    "num_inference_steps": image_num_inference_steps,
                    "guidance_scale": image_num_guidance,
                    "height": y,
                    "width": x,
                    "generator": generator,
                }

                image = pipe(
                    **inference_parameters,
                ).images[0]
            elif (
                image_model_card == "yuvraj108c/FLUX.1-Kontext-dev"
            ):

                kontext_images = []
                init_image = None
                if scene.kontext_strip_1:
                    if find_strip_by_name(scene, scene.kontext_strip_1):
                        input_image = load_first_frame(
                            get_strip_path(
                                find_strip_by_name(scene, scene.kontext_strip_1)
                            )
                        )
                    init_image = input_image
                image = converter(
                    prompt=prompt,
                    #prompt_2=None,
                    max_sequence_length=512,
                    image=init_image,
                    #strength=1.00 - scene.image_power,
                    # negative_prompt=negative_prompt,
                    num_inference_steps=image_num_inference_steps,
                    guidance_scale=image_num_guidance,
                    height=y,
                    width=x,
                    generator=generator,
                ).images[0]

            #FLUX 2
            elif (image_model_card == "diffusers/FLUX.2-dev-bnb-4bit"):
                image = pipe(
                    prompt=prompt,
                    generator=generator,#generator=torch.Generator(device=device).manual_seed(42),
                    max_sequence_length=512,
                    num_inference_steps=image_num_inference_steps,
                    guidance_scale=image_num_guidance,
                    height=y,
                    width=x,
                ).images[0]
                
            #FLUX klein
            elif (image_model_card == "Runware/BFL-FLUX.2-klein-base-4B" or image_model_card == "black-forest-labs/FLUX.2-klein-9b-kv"):
                image = pipe(
                    prompt=prompt,
                    generator=generator,#generator=torch.Generator(device=device).manual_seed(42),
                    max_sequence_length=512,
                    guidance_scale=1.0,
                    num_inference_steps=4,
                    height=y,
                    width=x,
                ).images[0]

            elif (image_model_card == "Tongyi-MAI/Z-Image"):                
                image = pipe(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    generator=generator,#generator=torch.Generator(device=device).manual_seed(42),
                    max_sequence_length=512,
                    num_inference_steps=image_num_inference_steps,
                    guidance_scale=image_num_guidance,
                    height=y,
                    width=x,
                ).images[0]  
                
            elif (image_model_card == "Tongyi-MAI/Z-Image-Turbo"):                
                image = pipe(
                    prompt=prompt,
                    generator=generator,#generator=torch.Generator(device=device).manual_seed(42),
                    max_sequence_length=512,
                    num_inference_steps=image_num_inference_steps,  # 9 This actually results in 8 DiT forwards
                    guidance_scale=0.0,     # Guidance should be 0 for the Turbo models
                    height=y,
                    width=x,
                ).images[0]                

            # Chroma
            elif (image_model_card == "lodestones/Chroma"):
                inference_parameters = {
                    "prompt": prompt,
                    "negative_prompt": negative_prompt,
                    "max_sequence_length": 512,
                    #"image": init_image,
                    #"mask_image": mask_image,
                    "num_inference_steps": image_num_inference_steps,
                    "guidance_scale": image_num_guidance,
                    "height": y,
                    "width": x,
                    "generator": generator,
                }

                image = pipe(
                    **inference_parameters,
                ).images[0]

            # Generate Stable Diffusion etc.
            elif (
                image_model_card == "adamo1139/stable-diffusion-3.5-large-ungated"
                or image_model_card == "adamo1139/stable-diffusion-3.5-medium-ungated"
            ):
                print("Generate: Stable Diffusion Image ")
                inference_parameters = {
                    "prompt": "",
                    "prompt_3": prompt,
                    "negative_prompt": negative_prompt,
                    "num_inference_steps": image_num_inference_steps,
                    "guidance_scale": image_num_guidance,
                    "height": y,
                    "width": x,
                    "max_sequence_length": 512,
                    "generator": generator,
                }
                image = pipe(
                    **inference_parameters,
                ).images[0]
            else:
                print("Generate: Image")
                from diffusers.utils import load_image

                # IPAdapter
                if (
                    scene.ip_adapter_face_folder or scene.ip_adapter_style_folder
                ) and image_model_card == "stabilityai/stable-diffusion-xl-base-1.0":
                    mask_image = None
                    init_image = None
                    ip_adapter_image = None

                    if scene.ip_adapter_face_folder and scene.ip_adapter_style_folder:
                        face_images = load_images_from_folder(
                            (scene.ip_adapter_face_folder).replace("\\", "/")
                        )
                        style_images = load_images_from_folder(
                            (scene.ip_adapter_style_folder).replace("\\", "/")
                        )
                        ip_adapter_image = [style_images, face_images]
                    elif scene.ip_adapter_face_folder:
                        face_images = load_images_from_folder(
                            (scene.ip_adapter_face_folder).replace("\\", "/")
                        )
                        ip_adapter_image = [face_images]
                    elif scene.ip_adapter_style_folder:
                        style_images = load_images_from_folder(
                            (scene.ip_adapter_style_folder).replace("\\", "/")
                        )
                        ip_adapter_image = [style_images]

                    # Inpaint
                    if scene.inpaint_selected_strip:
                        print("Process: Inpaint")
                        mask_strip = find_strip_by_name(
                            scene, scene.inpaint_selected_strip
                        )

                        if not mask_strip:
                            print("Selected mask not found!")
                            clear_cuda_cache()
                            return {"CANCELLED"}
                        if (
                            mask_strip.type == "MASK"
                            or mask_strip.type == "COLOR"
                            or mask_strip.type == "SCENE"
                            or mask_strip.type == "META"
                        ):
                            mask_strip = get_render_strip(self, context, mask_strip)
                        mask_path = get_strip_path(mask_strip)
                        mask_image = load_first_frame(mask_path)

                        if not mask_image:
                            print("Loading mask failed!")
                            return
                        mask_image = mask_image.resize((x, y))
                        mask_image = pipe.mask_processor.blur(
                            mask_image, blur_factor=33
                        )

                        if scene.image_path:
                            init_image = load_first_frame(scene.image_path)
                        if scene.movie_path:
                            init_image = load_first_frame(scene.movie_path)
                        if not init_image:
                            print("Loading strip failed!")
                            clear_cuda_cache()
                            return {"CANCELLED"}
                        image = pipe(
                            prompt,
                            negative_prompt=negative_prompt,
                            image=init_image,
                            mask_image=mask_image,
                            ip_adapter_image=ip_adapter_image,
                            num_inference_steps=image_num_inference_steps,
                            guidance_scale=image_num_guidance,
                            height=y,
                            width=x,
                            generator=generator,
                            # cross_attention_kwargs={"scale": 1.0},
                            # padding_mask_crop=42,
                            # strength=0.99,
                        ).images[0]

                    # Input strip + ip adapter
                    elif scene.input_strips == "input_strips" and (
                        scene.image_path or scene.movie_path
                    ):
                        if scene.image_path:
                            init_image = load_first_frame(scene.image_path)
                        if scene.movie_path:
                            init_image = load_first_frame(scene.movie_path)
                        if not init_image:
                            print("Loading strip failed!")
                            clear_cuda_cache()
                            return {"CANCELLED"}
                        image = pipe(
                            prompt,
                            image=init_image,
                            negative_prompt=negative_prompt,
                            ip_adapter_image=ip_adapter_image,
                            num_inference_steps=image_num_inference_steps,
                            guidance_scale=image_num_guidance,
                            height=y,
                            width=x,
                            # strength=max(1.00 - scene.image_power, 0.1),
                            generator=generator,
                        ).images[0]

                    # No inpaint, but IP Adapter
                    else:
                        image = pipe(
                            prompt,
                            negative_prompt=negative_prompt,
                            ip_adapter_image=ip_adapter_image,
                            num_inference_steps=image_num_inference_steps,
                            guidance_scale=image_num_guidance,
                            height=y,
                            width=x,
                            generator=generator,
                        ).images[0]

                # Qwen
                elif image_model_card == "Qwen/Qwen-Image-2512":
                    # LoRA.
                    if enabled_items:
                        image = pipe(
                            # prompt_embeds=prompt, # for compel - long prompts
                            prompt,
                            negative_prompt=negative_prompt,
                            num_inference_steps=image_num_inference_steps,
                            #guidance_scale=0.0,
                            height=y,
                            width=x,
                            true_cfg_scale=4.0,
                            generator=generator,
                        ).images[0]

                    # No LoRA.
                    else:
                        image = pipe(
                            prompt,
                            negative_prompt=negative_prompt,
                            num_inference_steps=image_num_inference_steps,
                            true_cfg_scale=4.0,
                            height=y,
                            width=x,
                            generator=generator,
                            max_sequence_length=512,
                        ).images[0]

                # Not Turbo
                else:  # if not scene.aurasr:
                    # LoRA.
                    if enabled_items:
                        image = pipe(
                            prompt,
                            negative_prompt=negative_prompt,
                            num_inference_steps=image_num_inference_steps,
                            guidance_scale=image_num_guidance,
                            height=y,
                            width=x,
                            cross_attention_kwargs={"scale": 1.0},
                            generator=generator,
                            max_sequence_length=512,
                        ).images[0]
                    # No LoRA.
                    else:
                        image = pipe(
                            prompt,
                            negative_prompt=negative_prompt,
                            num_inference_steps=image_num_inference_steps,
                            guidance_scale=image_num_guidance,
                            height=y,
                            width=x,
                            generator=generator,
                            max_sequence_length=512,
                        ).images[0]

            # Add refiner
            if do_refine:
                print("Refine: Image")
                image = refiner(
                    prompt=prompt,
                    image=image,
                    strength=max(1.00 - scene.image_power, 0.1),
                    negative_prompt=negative_prompt,
                    num_inference_steps=image_num_inference_steps,
                    guidance_scale=max(image_num_guidance, 1.1),
                    generator=generator,
                ).images[0]

            # ADetailer
            if scene.adetailer:
                from asdff.base import AdPipelineBase
                from huggingface_hub import hf_hub_download
                from diffusers import StableDiffusionXLPipeline, AutoencoderKL

                vae = AutoencoderKL.from_pretrained(
                    "madebyollin/sdxl-vae-fp16-fix",
                    torch_dtype=torch.float16,
                    local_files_only=local_files_only,
                )
                pipe = StableDiffusionXLPipeline.from_pretrained(
                    "stabilityai/stable-diffusion-xl-base-1.0",
                    vae=vae,
                    variant="fp16",
                    torch_dtype=torch.float16,
                )
                if gfx_device == "mps":
                    pipe.to("mps")
                elif low_vram():
                    pipe.enable_model_cpu_offload()
                else:
                    pipe.to(gfx_device)

                face_prompt = (
                    prompt + ", face, (8k, RAW photo, best quality, masterpiece:1.2)"
                )
                face_n_prompt = "nsfw, blurry, disfigured"
                face_mask_pad = 32
                mask_blur = 4
                mask_dilation = 4
                strength = 0.4
                ddim_steps = 20
                ad_images = image

                ad_components = pipe.components
                ad_pipe = AdPipelineBase(**ad_components)

                model_path = hf_hub_download(
                    "Bingsu/adetailer",
                    "face_yolov8n.pt",
                    local_dir="asdff/yolo_models",
                    local_dir_use_symlinks=False,
                )
                common = {
                    "prompt": face_prompt,
                    "n_prompt": face_n_prompt,
                    "num_inference_steps": int(image_num_inference_steps),
                    "target_size": (x, y),
                }
                inpaint_only = {"strength": strength}
                result = ad_pipe(
                    common=common,
                    inpaint_only=inpaint_only,
                    images=ad_images,
                    mask_dilation=mask_dilation,
                    mask_blur=mask_blur,
                    mask_padding=face_mask_pad,
                    model_path=model_path,
                )
                try:
                    image = result.images[0]
                except:
                    print("No images detected. ADetailer disabled.")

            # AuraSR
            if scene.aurasr:
                if do_convert:
                    if scene.movie_path:
                        print("Process: Movie Frame to Image")
                        init_image = load_first_frame(scene.movie_path)
                        init_image = init_image.resize((x, y))
                    elif scene.image_path:
                        print("Process: Image to Image")
                        init_image = load_first_frame(scene.image_path)
                        init_image = init_image.resize((x, y))
                    image = init_image

                if image:
                    from aura_sr import AuraSR

                    aura_sr = AuraSR.from_pretrained("fal/AuraSR-v2")
                    image = aura_sr.upscale_4x_overlapped(image)

            # Move to folder
            filename = clean_filename(
                str(seed) + "_" + context.scene.generate_movie_prompt
            )
            out_path = solve_path(filename + ".png")
            image.save(out_path)
            bpy.types.Scene.genai_out_path = out_path

            if input == "input_strips":
                old_strip = active_strip

            # Add strip
            if os.path.isfile(out_path):
                strip = scene.sequence_editor.strips.new_image(
                    name=str(seed) + "_" + context.scene.generate_movie_prompt,
                    frame_start=start_frame,
                    filepath=out_path,
                    channel=empty_channel,
                    fit_method="FIT",
                )
                if scene.generate_movie_frames == -1 and input == "input_strips":
                    strip.frame_final_duration = old_strip.frame_final_duration
                else:
                    strip.frame_final_duration = abs(scene.generate_movie_frames)

                if inference_parameters != None:
                    set_ai_metadata_from_dict(
                        strip=strip,
                        params_dict=inference_parameters
                    )

                scene.sequence_editor.active_strip = strip
                if i > 0:
                    scene.frame_current = (
                        scene.sequence_editor.active_strip.frame_final_start
                    )
                strip.use_proxy = True
                # bpy.ops.sequencer.rebuild_proxy()
            else:
                print("No resulting file found.")
            import gc
            gc.collect()

            for window in bpy.context.window_manager.windows:
                screen = window.screen
                for area in screen.areas:
                    if area.type == "SEQUENCE_EDITOR":
                        from bpy import context

                        with context.temp_override(window=window, area=area):
                            if i > 0:
                                scene.frame_current = (
                                    scene.sequence_editor.active_strip.frame_final_start
                                )
                            # Redraw UI to display the new strip. Remove this if Blender crashes: https://docs.blender.org/api/current/info_gotcha.html#can-i-redraw-during-script-execution
                            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
                            break
            print_elapsed_time(start_time)

        if should_unload:
            print("Unloading models from memory...")
            try:
                if pipe:
                    pipe = None
                if refiner:
                    compel = None
                if converter:
                    converter = None
            except:
                pass
            
            # --- CLEAR GLOBAL CACHE ---
            _pallaidium_model_cache["pipe"] = None
            _pallaidium_model_cache["converter"] = None
            _pallaidium_model_cache["refiner"] = None

            # clear the VRAM
            clear_cuda_cache()

        scene.movie_num_guidance = guidance
#        if input != "input_strips":
#            bpy.ops.renderreminder.pallaidium_play_notification()
        scene.frame_current = current_frame

        return {"FINISHED"}

class SEQUENCER_OT_generate_text(Operator):
    """Generate Text"""

    bl_idname = "sequencer.generate_text"
    bl_label = "Prompt"
    bl_description = "Generate texts from strips"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        global _pallaidium_text_model_cache
        import os
        
        scene = context.scene
        input = scene.input_strips
        seq_editor = scene.sequence_editor
        preferences = context.preferences
        addon_prefs = preferences.addons[ADDON_ID].preferences
        local_files_only = addon_prefs.local_files_only
        guidance = scene.movie_num_guidance
        current_frame = scene.frame_current
        #prompt = style_prompt(scene.generate_movie_prompt)[0]
        prompt = scene.generate_movie_prompt
        x = scene.generate_movie_x = closest_divisible_32(scene.generate_movie_x)
        y = scene.generate_movie_y = closest_divisible_32(scene.generate_movie_y)
        active_strip = context.scene.sequence_editor.active_strip
        
        # Handle duration safety if no active strip
        if active_strip:
            old_duration = duration = active_strip.frame_final_duration
        else:
            old_duration = duration = 100
            
        render = bpy.context.scene.render
        fps = render.fps / render.fps_base
        show_system_console(True)
        set_system_console_topmost(True)
        
        # --- CACHE RETRIEVAL ---
        model = _pallaidium_text_model_cache["model"]
        processor = _pallaidium_text_model_cache["processor"]
        tokenizer = _pallaidium_text_model_cache["tokenizer"]
        
        should_load = context.scene.get("ai_load_state", True)
        should_unload = context.scene.get("ai_unload_state", True)
        
        # Force load if cache is empty or model changed
        if model is None and not should_load:
            print("Text model cache missing. Forcing load.")
            should_load = True
            
        if _pallaidium_text_model_cache["last_model_card"] != addon_prefs.text_model_card:
            print("Text model card changed. Forcing load.")
            should_load = True

        if not seq_editor:
            scene.sequence_editor_create()

        # --- DEPENDENCY CHECKS ---
        if addon_prefs.text_model_card == "Salesforce/blip-image-captioning-large":
            try:
                import torch
                from PIL import Image
                from transformers import BlipProcessor, BlipForConditionalGeneration
            except ModuleNotFoundError as e:
                print("Dependencies needs to be installed in the add-on preferences. "+str(e.name))

                self.report(
                    {"INFO"},
                    "Dependencies need to be installed in the add-on preferences.",
                )
                return {"CANCELLED"}

        elif (
            addon_prefs.text_model_card == "ZuluVision/MoviiGen1.1_Prompt_Rewriter"
        ):
            try:
                import torch
                from transformers import TorchAoConfig, AutoModelForCausalLM, AutoTokenizer
            except ModuleNotFoundError as e:
                print("Dependencies needs to be installed in the add-on preferences. "+str(e.name))

                self.report(
                    {"INFO"},
                    "Dependencies need to be installed in the add-on preferences.",
                )
                return {"CANCELLED"}
        elif (
            addon_prefs.text_model_card == "florence-community/Florence-2-large"
        ):
            try:
                from transformers import AutoModelForSeq2SeqLM, AutoProcessor, AutoConfig
            except ModuleNotFoundError as e:
                print("Dependencies needs to be installed in the add-on preferences. "+str(e.name))

                self.report(
                    {"INFO"},
                    "Dependencies need to be installed in the add-on preferences.",
                )
                return {"CANCELLED"}

        # --- LOAD MODEL LOGIC ---
        if should_load:
            # clear the VRAM
            clear_cuda_cache()
            
            # Reset locals
            model = None
            processor = None
            tokenizer = None
            
            print(f"Loading Text Model: {addon_prefs.text_model_card}")
            
            if addon_prefs.text_model_card == "Salesforce/blip-image-captioning-large":
                import torch
                from transformers import BlipProcessor, BlipForConditionalGeneration
                
                processor = BlipProcessor.from_pretrained(
                    "Salesforce/blip-image-captioning-large",
                    local_files_only=local_files_only,
                )

                model = BlipForConditionalGeneration.from_pretrained(
                    "Salesforce/blip-image-captioning-large",
                    torch_dtype=torch.float16,
                    local_files_only=local_files_only,
                ).to(gfx_device)
            
            elif addon_prefs.text_model_card == "florence-community/Florence-2-large":
                from transformers import AutoProcessor, Florence2ForConditionalGeneration
                import torch

                # Move model to GPU
                model = Florence2ForConditionalGeneration.from_pretrained(
                    "florence-community/Florence-2-large",
                    device_map="auto",  # automatically puts model on GPU
                )

                processor = AutoProcessor.from_pretrained("florence-community/Florence-2-large")
                
            elif addon_prefs.text_model_card == "ZuluVision/MoviiGen1.1_Prompt_Rewriter":
                import torch
                from transformers import TorchAoConfig, AutoModelForCausalLM, AutoTokenizer
                
                print("Loading MoviiGen...")
                quantization_config = TorchAoConfig("int4_weight_only", group_size=128)
                model_name = "ZuluVision/MoviiGen1.1_Prompt_Rewriter"

                model = AutoModelForCausalLM.from_pretrained(
                    model_name,
                    torch_dtype=torch.bfloat16,
                    #torch_dtype="auto",
                    device_map="auto",
                    quantization_config=quantization_config,
                )
                tokenizer = AutoTokenizer.from_pretrained(model_name)

            # Update Cache
            _pallaidium_text_model_cache["model"] = model
            _pallaidium_text_model_cache["processor"] = processor
            _pallaidium_text_model_cache["tokenizer"] = tokenizer
            _pallaidium_text_model_cache["last_model_card"] = addon_prefs.text_model_card

        # --- IMAGE LOADING (Shared for Image Captioning models) ---
        init_image = None
        if not addon_prefs.text_model_card == "ZuluVision/MoviiGen1.1_Prompt_Rewriter":
            if scene.movie_path:
                init_image = load_first_frame(bpy.path.abspath(scene.movie_path))
            elif scene.image_path:
                init_image = load_first_frame(bpy.path.abspath(scene.image_path))
            
            if init_image:
                init_image = init_image.resize((x, y))
            else:
                print("No input image loaded succesfully. Cancelling.")
                return {"CANCELLED"}

        # --- INFERENCE LOGIC ---
        text = ""
        
        if addon_prefs.text_model_card == "Salesforce/blip-image-captioning-large":
            import torch
            
            inputs = processor(init_image, "", return_tensors="pt").to(
                gfx_device, torch.float16
            )

            out = model.generate(**inputs, max_new_tokens=256)
            text = processor.decode(out[0], skip_special_tokens=True)
            text = clean_string(text)
            print("Generated text: " + text)

        elif (
            addon_prefs.text_model_card == "florence-community/Florence-2-large"
        ):
            
            # Ensure image is RGB
            if init_image.mode != "RGB":
                init_image = init_image.convert("RGB")

            caption_prompt = "<MORE_DETAILED_CAPTION>"

            # Prepare inputs and move all tensors to the same device as the model
            inputs = processor(text=caption_prompt, images=init_image, return_tensors="pt")
            inputs = {k: v.to(model.device) for k, v in inputs.items()}

            # Generate
            generated_ids = model.generate(
                **inputs,
                max_new_tokens=1024,
                num_beams=3,
                repetition_penalty=1.10,
            )

            # Decode
            generated_text = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
            parsed_answer = processor.post_process_generation(
                generated_text,
                task=caption_prompt,
                image_size=(init_image.width, init_image.height),
            )
            text = parsed_answer[caption_prompt]
            print("Generated text:", text)            
            
            # Process inputs
            inputs = processor(text=caption_prompt, images=init_image, return_tensors="pt")

            # Move all tensors to the GPU
            inputs = {k: v.to(gfx_device) for k, v in inputs.items()}

            # Generate text
            generated_ids = model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=1024,
                num_beams=3,
                repetition_penalty=1.10,
            )

            # Decode output
            generated_text = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
            parsed_answer = processor.post_process_generation(
                generated_text,
                task=caption_prompt,
                image_size=(init_image.width, init_image.height),
            )
            text = parsed_answer[caption_prompt]
            print("Generated text:", text)

            

        elif addon_prefs.text_model_card == "ZuluVision/MoviiGen1.1_Prompt_Rewriter":
            import torch
            
            if input == "input_strips" and active_strip and active_strip.type != "TEXT":
                print("Unsupported strip type for Rewriter: "+active_strip.name)
                # If loading happened but validation failed, we should proceed to unload if requested
                pass 
            else:
                print("Enhancing prompt.")
                # model and tokenizer already loaded
                
                messages = [
                    #{"role": "system", "content": "Be creative and expand the input into a single line of comma-separated cinematic keywords, strictly ordered as: camera, camera motion, subject, distinct subject details, distinct situation, distinct location details, setting, lighting, atmosphere, style."},
                    #{"role": "system", "content": "You are an advanced AI model tasked with You must respond in the language used by the user."},
                    #{"role": "system", "content": "You enhance the input prompt to a 400 characters image prompt, in precise cinematic language, in comma separated nouns and adjectives. First camera angle and framing, then be creative and expand on all the input elements, don't change the order, by specifying subjects, their situation, one by one, then the settings, lighting, color, atmosphere, mood, style, motion, and camera movement. Do not repeat words or elements. Example: a cinematic wide-shot of a young woman, red hair, army clothes, dark forest, dramatic lightning. "},
                    {"role": "system", "content": "As a cinematic prompt engineer, be creative, rewrite the following into a comma-separated list of visual details, starting with camera angle, camera motion and progressing through subject, setting, lighting, atmosphere, style."},
                    {"role": "user", "content": prompt}
                ]
                input_formatted = tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True
                )
                model_inputs = tokenizer([input_formatted], return_tensors="pt").to(model.device)

                generated_ids = model.generate(
                    **model_inputs,
                    max_new_tokens=512
                )
                generated_ids = [
                    output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
                ]

                text = tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
                text = remove_duplicate_phrases(text)
                print("Generated text: " + str(text))

        # --- STRIP CREATION ---
        if input == "input_strips" and active_strip:
            # Use 'left_handle' (formerly frame_final_start) for the start position
            # Use 'duration' (formerly frame_final_duration) for the length
            start_frame = int(getattr(active_strip, 'left_handle', getattr(active_strip, 'frame_final_start', 1)))
            duration = int(getattr(active_strip, 'duration', getattr(active_strip, 'frame_final_duration', 100)))
            
            # The strip length must be the duration of the source strip
            strip_length = duration
        else:
            start_frame = int(scene.frame_current)
            strip_length = 100

        empty_channel = find_first_empty_channel(start_frame, start_frame + strip_length)

        # Add strip
        if text:
            # We use 'length' instead of 'frame_end' for the constructor
            strip = scene.sequence_editor.strips.new_effect(
                name=str(text),
                type="TEXT",
                frame_start=start_frame,
                length=strip_length, # This is the duration in frames
                channel=empty_channel,
            )
            
            # If you are in 5.1, right_handle is the absolute end frame
            if hasattr(strip, 'right_handle'):
                strip.right_handle = start_frame + strip_length
            else:
                strip.frame_final_end = start_frame + strip_length
                
            strip.text = text
            strip.wrap_width = 0.68
            strip.font_size = 16
            strip.location[0] = 0.5
            strip.location[1] = 0.2
            strip.anchor_x = "CENTER"
            strip.anchor_y = "TOP"
            strip.alignment_x = "LEFT"
            strip.use_shadow = True
            strip.use_box = True
            strip.box_color = (0, 0, 0, 0.7)
            scene.sequence_editor.active_strip = strip
        
        # UI Redraw
        for window in bpy.context.window_manager.windows:
            screen = window.screen
            for area in screen.areas:
                if area.type == "SEQUENCE_EDITOR":
                    from bpy import context

                    with context.temp_override(window=window, area=area):
                        if active_strip:
                            if (
                                active_strip.frame_final_start
                                <= scene.frame_current
                                <= (
                                    active_strip.frame_final_start
                                    + active_strip.frame_final_duration
                                )
                            ):
                                pass
                            else:
                                scene.frame_current = (
                                    active_strip.frame_final_start
                                )
                        # Redraw UI to display the new strip.
                        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
                        break
        scene.movie_num_guidance = guidance
        scene.frame_current = current_frame

        # --- UNLOAD LOGIC ---
        if should_unload:
            print("Unloading text models from memory...")
            model = None
            processor = None
            tokenizer = None
            
            _pallaidium_text_model_cache["model"] = None
            _pallaidium_text_model_cache["processor"] = None
            _pallaidium_text_model_cache["tokenizer"] = None
            
            # clear the VRAM
            clear_cuda_cache()

        return {"FINISHED"}

class SEQUENCER_OT_strip_to_generatorAI(Operator):
    """Convert selected text strips to Generative AI with Smart Memory Management"""

    bl_idname = "sequencer.text_to_generator"
    bl_label = "Pallaidium"
    bl_options = {"INTERNAL"}
    bl_description = "Adds selected strips as inputs to the Generative AI process"

    @classmethod
    def poll(cls, context):
        return context.sequencer_scene and context.scene.sequence_editor

    def execute(self, context):
        import os
        # --- Initialization ---
        bpy.types.Scene.movie_path = ""
        bpy.types.Scene.image_path = ""
        bpy.types.Scene.sound_path = ""
        preferences = context.preferences
        
        try:
            addon_prefs = preferences.addons[ADDON_ID].preferences
            play_sound = addon_prefs.playsound
            addon_prefs.playsound = False
            use_strip_data = addon_prefs.use_strip_data
        except:
            play_sound = False 
            use_strip_data = False

        scene = context.scene
        sequencer = bpy.ops.sequencer
        strips = context.selected_strips
        active_strip = context.scene.sequence_editor.active_strip
        
        if not strips == context.selected_strips:
            active_strip.select = True
            
        # STORE BASE PROMPTS HERE - These are our "Clean" copies
        base_prompt = scene.generate_movie_prompt
        base_negative_prompt = scene.generate_movie_negative_prompt
        current_prompt_text = base_prompt
        current_negative_text = base_negative_prompt
        current_frame = scene.frame_current
        target_type = scene.generatorai_typeselect 
        seed = scene.movie_num_seed
        use_random = scene.movie_use_random
        temp_strip = None
        temp_strips = []
        current_temp_strip = None
        run_generation = False
        
        # --- Input Validation ---
        if not strips:
            self.report({"INFO"}, "Select strip(s) for processing.")
            return {"CANCELLED"}
        else:
            print("\nStrip input processing started...")
        
        valid_types = {"MOVIE", "IMAGE", "TEXT", "SCENE", "META", "SOUND"}
        for strip in strips:
            if strip.type in valid_types:
                break
        else:
            self.report({"INFO"}, "None of the selected strips are valid types.")
            return {"CANCELLED"}

        if target_type == "text":
            for strip in strips:
                if strip.type in {"MOVIE", "IMAGE", "TEXT", "SCENE", "META", "SOUND"}:
                    break
            else:
                self.report({"INFO"}, "None of the selected strips are possible to process to text.")
                return {"CANCELLED"}

        # --- Hardware Info (Optional) ---
        try:
            if gfx_device == "cuda":
                print(f"CUDA version: {torch.version.cuda}")
        except:
            pass

        # --- Main Processing Loop ---
        total_strips = len(strips)
        
        for count, strip in enumerate(strips):
            # 1. Selection Logic
            for dsel_strip in bpy.context.scene.sequence_editor.strips:
                dsel_strip.select = False
            strip.select = True
            context.scene.sequence_editor.active_strip = strip

            # 2. Smart Memory Management Logic
            is_first_strip = (count == 0)
            is_last_strip = (count == total_strips - 1)
            current_strip_type = strip.type
            
            prev_strip_type = strips[count-1].type if count > 0 else None
            type_has_changed = (prev_strip_type is not None and current_strip_type != prev_strip_type)

            next_strip_type = strips[count+1].type if count < total_strips - 1 else None
            next_type_is_different = (next_strip_type is not None and next_strip_type != current_strip_type)

            should_load_model = is_first_strip or type_has_changed
            should_unload_model = is_last_strip or next_type_is_different

            context.sequencer_scene["ai_load_state"] = should_load_model
            context.sequencer_scene["ai_unload_state"] = should_unload_model
            
            print(f"Processing {count+1}/{total_strips} [{strip.type}]. Load: {should_load_model}, Unload: {should_unload_model}")


            # 3A. Intermediate META Strip Handling
            if (target_type == "movie" and addon_prefs.movie_model_card == "LTX-2 Multi-Input File") or (target_type == "image"): # and addon_prefs.image_model_card == "Tongyi-MAI/Z-Image-Turbo" 
                if strip.type == "META":
                    meta_strip = strip
                    strips_array = strip.strips
                else:
                    meta_strip = None
                    strips_array = [strip]
                
                current_temp_strip = None
                for child_strip in strips_array: 
                    for dsel_strip in bpy.context.scene.sequence_editor.strips:
                        dsel_strip.select = False
                    child_strip.select = True
                    context.scene.sequence_editor.active_strip = child_strip 
                                       
                    if child_strip.type == "TEXT":
                        pass
                    else:
                        # Unified call: Generate the strip regardless of type
                        current_temp_strip = get_render_strip(self, context, child_strip, meta_strip=meta_strip)
                        print("Adding: "+str(current_temp_strip))
                        
                        # If successful, add to our cleanup list
                        if current_temp_strip:
                            temp_strips.append(current_temp_strip)

                    # 4. Processing Variables Setup
                    # We calculate specific prompts into these variables, then apply them
                    run_generation = False

                    # --- TEXT STRIP ---
                    if child_strip.type == "TEXT":
                        #if child_strip.text:
                        if meta_strip and meta_strip.type == 'META':
                            for child in meta_strip.strips:
                                if child.type == 'TEXT':
                                    print("Found text:", child.text)
                                    current_prompt_text = child.text + ", " + base_prompt
                            # Combine Strip Text + Base Prompt
                            run_generation = True
                        else:
                            current_prompt_text = child_strip.text + ", " + base_prompt
                            run_generation = True

                    # --- IMAGE / MOVIE STRIP ---
                    if current_temp_strip and (current_temp_strip.type == "IMAGE" or current_temp_strip.type == "MOVIE" or current_temp_strip.type == "SOUND"):
                        # Set path
                        if current_temp_strip.type == "IMAGE":
                            strip_dirname = os.path.dirname(current_temp_strip.directory)
                            file_path = bpy.path.abspath(os.path.join(strip_dirname, current_temp_strip.elements[0].filename))
                            bpy.types.Scene.movie_path = file_path
                        else:
                            if current_temp_strip.type == "MOVIE":
                                file_path = bpy.path.abspath(current_temp_strip.filepath)
                                bpy.types.Scene.movie_path = file_path
                            elif current_temp_strip.type == "SOUND" and target_type == "movie":
                                file_path = bpy.path.abspath(current_temp_strip.sound.filepath)
                                bpy.types.Scene.sound_path = file_path
                        current_temp_strip = None
                            
                        run_generation = True
                            
                    print(bpy.types.Scene.movie_path)
                    print(bpy.types.Scene.image_path)
                    print(bpy.types.Scene.sound_path)
#                    
#                if current_prompt_text == "":
#                    current_prompt_text == base_prompt 
                                   
                print(f"Prompt: {current_prompt_text}")
                    
            # 3B. Intermediate Strip Handling
            elif strip.type in {"SCENE", "MOVIE", "META", "SOUND", "TEXT", "IMAGE"}: 
                if (target_type == "image" or target_type == "text") and strip.type not in {"TEXT", "IMAGE"}:
                    trim_frame = find_overlapping_frame(strip, current_frame)
                    if trim_frame and len(strips) == 1:
                        bpy.ops.sequencer.duplicate_move(
                            SEQUENCER_OT_duplicate={},
                            TRANSFORM_OT_seq_slide={"value": (0, 1), "use_restore_handle_selection": False, "snap": False}
                        )
                        intermediate_strip = bpy.context.selected_strips[0]
                        intermediate_strip.frame_start = strip.frame_start
                        intermediate_strip.frame_offset_start = int(trim_frame)
                        intermediate_strip.frame_final_duration = 1
                        temp_strip = strip = get_render_strip(self, context, intermediate_strip)
                        if intermediate_strip: delete_strip(intermediate_strip)

                    elif target_type == "text":
                        bpy.ops.sequencer.copy()
                        bpy.ops.sequencer.paste(keep_offset=True)
                        intermediate_strip = bpy.context.selected_strips[0]
                        intermediate_strip.frame_start = strip.frame_start
                        intermediate_strip.frame_final_duration = strip.frame_final_duration
                        temp_strip = strip = get_render_strip(self, context, intermediate_strip)
                        if intermediate_strip: delete_strip(intermediate_strip)
                    else:
                        temp_strip = strip = get_render_strip(self, context, strip)
                elif strip.type not in {"TEXT", "IMAGE"}:
                    temp_strip = strip = get_render_strip(self, context, strip)

                # 4. Processing Variables Setup
                # We calculate specific prompts into these variables, then apply them
                run_generation = False
                current_prompt_text = base_prompt
                current_negative_text = base_negative_prompt

                # --- TEXT STRIP ---
                if strip.type == "TEXT":
                    if strip.text:
                        # Combine Strip Text + Base Prompt
                        current_prompt_text = strip.text + ", " + base_prompt
                        run_generation = True

                # --- SOUND STRIP ---
                if strip.type == "SOUND":
                    if strip.sound:
                        # Sound usually uses just the base prompt, or you can add logic here
                        current_prompt_text = base_prompt 
                        run_generation = True

                # --- IMAGE / MOVIE STRIP ---
                if strip.type == "IMAGE" or strip.type == "MOVIE" or strip.type == "SOUND":
                    # Set path
                    if strip.type == "IMAGE":
                        strip_dirname = os.path.dirname(strip.directory)
                        file_path = bpy.path.abspath(os.path.join(strip_dirname, strip.elements[0].filename))
                        bpy.types.Scene.image_path = file_path
                    else:
                        if strip.type == "MOVIE":
                            file_path = bpy.path.abspath(strip.filepath)
                            bpy.types.Scene.movie_path = file_path
                        elif strip.type == "SOUND":
                            file_path = bpy.path.abspath(strip.sound.filepath)
                            bpy.types.Scene.sound_path = file_path
                    run_generation = True

                    if strip.name:
                        strip_prompt = os.path.splitext(strip.name)[0]
                        seed_nr = extract_numbers(str(strip_prompt))

                        if seed_nr and use_strip_data:
                            file_seed = int(seed_nr)
                            strip_prompt = strip_prompt.replace(str(file_seed) + "_", "")
                            context.scene.movie_use_random = False
                            context.scene.movie_num_seed = file_seed

                        # Style Prompts using BASE prompt
                        if use_strip_data:
                            styled = style_prompt(strip_prompt + ", " + base_prompt)
                        else:
                            styled = style_prompt(base_prompt)
                        
                        current_prompt_text = styled[0]
                        current_negative_text = styled[1]
                    
                    if current_prompt_text == "":
                        current_prompt_text = base_prompt
                        
#                    if target_type != "text":
#                        print(f"Prompt: {current_prompt_text}")
                    print(f"Prompt: {current_prompt_text}")

            # 5. EXECUTE GENERATION
            if run_generation:
                # Apply the calculated prompt to the scene property
                if current_prompt_text == None: current_prompt_text = ""
                if current_negative_text == None: current_negative_text = ""
                scene.generate_movie_prompt = current_prompt_text
                scene.generate_movie_negative_prompt = current_negative_text
                scene.frame_current = strip.frame_final_start
                context.scene.sequence_editor.active_strip = strip
                
                # Apply Seed/Random settings
                if use_strip_data and strip.type in {"IMAGE", "MOVIE", "SOUND"}:
                     # Seed was already set in the block above
                     pass
                else:
                     context.scene.movie_use_random = use_random
                     context.scene.movie_num_seed = seed

                # Call the actual operator
                if target_type == "movie": sequencer.generate_movie()
                elif target_type == "audio": sequencer.generate_audio()
                elif target_type == "image": sequencer.generate_image()
                elif target_type == "text": sequencer.generate_text()

                # --- IMMEDIATE RESTORE ---
                # Restore the clean base prompt immediately after the call
                scene.generate_movie_prompt = base_prompt
                scene.generate_movie_negative_prompt = base_negative_prompt
                context.scene.movie_use_random = use_random
                context.scene.movie_num_seed = seed
                
                # Clean up paths
                bpy.types.Scene.image_path = ""
                bpy.types.Scene.movie_path = ""
                bpy.types.Scene.sound_path = ""
                # --- Single temp strip cleanup ---
                if temp_strip is not None:
                    if temp_strip.type == 'MOVIE':
                        delete_linked_audio(context, temp_strip)

                    delete_strip(temp_strip)
                    temp_strip = None


                # --- Batch Cleanup: Delete all temporary strips collected ---
                for s in temp_strips:
                    if s:
                        try:
                            seq_editor = context.scene.sequence_editor
                            if seq_editor and s.name in seq_editor.strips_all:

                                if s.type == 'MOVIE':
                                    delete_linked_audio(context, s)

                                delete_strip(s)

                        except Exception as e:
                            print(f"Warning: Could not delete temp strip {s.name}: {e}")
                
                # Clear the list for the next iteration
                temp_strips.clear()
                    
        # --- Final Cleanup ---
        scene.frame_current = current_frame
        # Final safety restore
        scene.generate_movie_prompt = base_prompt
        scene.generate_movie_negative_prompt = base_negative_prompt
        context.scene.movie_use_random = use_random
        context.scene.movie_num_seed = seed
        context.scene.sequence_editor.active_strip = active_strip

#        try:
#            addon_prefs.playsound = play_sound
#            bpy.ops.renderreminder.pallaidium_play_notification()
#        except:
#            pass

        print("Processing finished.")

        return {"FINISHED"}

class SEQUENCER_OT_ai_strip_picker(Operator):
    """Pick a strip"""
    bl_idname = "sequencer.strip_picker"
    bl_label = "Pick Strip"
    bl_description = "Pick a strip in the VSE"
    bl_options = {"REGISTER", "UNDO"}
    action: StringProperty(
        name="Action",
        description="Action to perform on the picked strip",
        default="select"
    )

    def modal(self, context, event):
        if event.type == "LEFTMOUSE" and event.value == "PRESS":
            print("Picking...")
            area = context.area
            region = context.region
            mouse_region_coord = (event.mouse_region_x, event.mouse_region_y)
            if area.type != "SEQUENCE_EDITOR" or not region:
                    self.report({"WARNING"}, "Invalid region or area for VSE")
                    context.window.cursor_modal_restore()
                    return {"CANCELLED"}

            v2d = region.view2d
            mouse_x_view, mouse_y_view = v2d.region_to_view(*mouse_region_coord)

            for strip in context.scene.sequence_editor.strips_all:
                # Check if the strip has a transform property before accessing it
                if hasattr(strip, 'transform'):
                    scale_y = strip.transform.scale_y
                else:
                    # If not, assume a default scale of 1.0 (occupies one channel)
                    scale_y = 1.0

                # Calculate the vertical bounds of the strip in view space
                strip_y_min_view = strip.channel - 0.5 * scale_y
                strip_y_max_view = strip.channel + 0.5 * scale_y

                if (
                    strip.frame_start <= mouse_x_view < strip.frame_final_end and
                    strip_y_min_view <= mouse_y_view < strip_y_max_view
                ):
                    self.perform_action(context, strip)
                    context.window.cursor_modal_restore()
                    return {"FINISHED"}


#                # Calculate the vertical bounds of the strip in view space
#                # Assuming each channel has a nominal height of 1.0 in view space
#                strip_y_min_view = strip.channel - 0.5 * strip.transform.scale_y  # Consider the scaled height
#                strip_y_max_view = strip.channel + 0.5 * strip.transform.scale_y

#                if (
#                    strip.frame_start <= mouse_x_view < strip.frame_final_end and
#                    (strip.type == "IMAGE" or strip.type =="MOVIE")#and
#                    #strip_y_min_view <= mouse_y_view < strip_y_max_view
#                ):
#                    self.perform_action(context, strip)
#                    context.window.cursor_modal_restore()
#                    return {"FINISHED"}

            # If no strip picked, don't exit — allow continuous clicking
            return {"RUNNING_MODAL"}

        elif event.type in {"RIGHTMOUSE", "ESC"}:
            context.window.cursor_modal_restore()
            return {"CANCELLED"}

        return {"RUNNING_MODAL"}

    def perform_action(self, context, strip):
        """Handle different actions on the picked strip"""
        scene = context.scene
        if self.action == "omni_select1":
            self.report({"INFO"}, f"Picked: {strip.name}")
            if find_strip_by_name(scene, strip.name):
                scene.omnigen_strip_1 = strip.name
        elif self.action == "omni_select2":
            print(f"Picked Strip Name: {strip.name}")
            self.report({"INFO"}, f"Picked '{strip.name}'")
            if find_strip_by_name(scene, strip.name):
                context.scene.omnigen_strip_2 = strip.name
        elif self.action == "omni_select3":
            print(f"Picked Strip Name: {strip.name}")
            self.report({"INFO"}, f"Picked '{strip.name}'")
            if find_strip_by_name(scene, strip.name):
                context.scene.omnigen_strip_3 = strip.name

        if self.action == "qwen_select1":
            self.report({"INFO"}, f"Picked: {strip.name}")
            if find_strip_by_name(scene, strip.name):
                scene.qwen_strip_1 = strip.name
        elif self.action == "qwen_select2":
            print(f"Picked Strip Name: {strip.name}")
            self.report({"INFO"}, f"Picked '{strip.name}'")
            if find_strip_by_name(scene, strip.name):
                context.scene.qwen_strip_2 = strip.name
        elif self.action == "qwen_select3":
            print(f"Picked Strip Name: {strip.name}")
            self.report({"INFO"}, f"Picked '{strip.name}'")
            if find_strip_by_name(scene, strip.name):
                context.scene.qwen_strip_3 = strip.name

        elif self.action == "minimax_select":
            print(f"Picked Strip Name: {strip.name}")
            self.report({"INFO"}, f"Picked '{strip.name}'")
            if find_strip_by_name(scene, strip.name):
                context.scene.minimax_subject = strip.name

        elif self.action == "inpaint_select":
            print(f"Picked Strip Name: {strip.name}")
            self.report({"INFO"}, f"Picked '{strip.name}'")
            if find_strip_by_name(scene, strip.name):
                context.scene.inpaint_selected_strip = strip.name

        elif self.action == "out_frame_select":
            print(f"Picked Strip Name: {strip.name}")
            self.report({"INFO"}, f"Picked '{strip.name}'")
            if find_strip_by_name(scene, strip.name):
                context.scene.out_frame = strip.name
                
        for i in range(1, 10): # Loop for flux_select1 to flux_select9
            if self.action == f"flux_select{i}":
                self.report({"INFO"}, f"Picked: {strip.name}")
                if find_strip_by_name(scene, strip.name):
                    setattr(scene, f"flux_strip_{i}", strip.name)
                break # Exit the loop once the action is found and handled

        if self.action == "kontext_select1":
            self.report({"INFO"}, f"Picked: {strip.name}")
            if find_strip_by_name(scene, strip.name):
                scene.kontext_strip_1 = strip.name
#        else:
#            self.report({"WARNING"}, f"Unknown action: {self.action}")

    def invoke(self, context, event):
        if context.area.type == 'SEQUENCE_EDITOR':
            context.window_manager.modal_handler_add(self)
            context.window.cursor_modal_set("EYEDROPPER")
            return {"RUNNING_MODAL"}
        else:
            self.report({'WARNING'}, "This operator only works in the Video Sequence Editor")
            return {"CANCELLED"}