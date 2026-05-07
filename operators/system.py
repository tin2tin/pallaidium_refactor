"""
System operators: install/uninstall dependencies, sound notification,
LoRA file refresh, IP Adapter file browsers.
"""

import bpy
import os
import glob
import subprocess
import importlib
import importlib.metadata
import importlib.util
import aud

from bpy_extras.io_utils import ExportHelper
from bpy.types import Operator
from bpy.props import StringProperty, BoolProperty

from ..utils.helpers import (
    ADDON_ID,
    python_exec,
    DependencyManager,
    BlenderInternalManager,
    SmartSkipManager,
    install_requirements_binary_only,
    install_requirements_allow_source,
    write_requirements_file,
)


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

        # Step 1: Base Requirements (Binary Only)
        if os.path.exists(local_req_file):
            with open(local_req_file, 'r') as f:
                raw_lines = f.read().splitlines()

            if not process_in_batches(raw_lines, "Base Binaries", install_requirements_binary_only):
                self.report({"ERROR"}, "Failed to install base binaries.")
                return {"CANCELLED"}

        # Step 2: Source Libs, Torch, Git
        phases = [
            ("Source_Libs", mgr.get_phase_1_5_source_libs()),
            ("Torch", mgr.get_phase_2_torch()),
            ("Git_Extensions", mgr.get_phase_3_git_and_extensions()),
        ]
        for phase_name, lines in phases:
            torch_installed = (
                any("torch" in x for x in lines)
                and not self.force_reinstall
                and importlib.util.find_spec("torch")
            )
            if "Torch" in phase_name and mgr.os_platform == "Windows" and not torch_installed:
                clean_lines = SmartSkipManager.filter_existing(lines)
                if clean_lines:
                    print("Ensuring clean Torch installation...")
                    subprocess.call([pybin, "-m", "pip", "uninstall", "-y", "torch", "torchvision", "torchaudio"])

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
            mgr.get_phase_1_5_source_libs()
            + mgr.get_phase_2_torch()
            + mgr.get_phase_3_git_and_extensions()
        )
        for line in script_phases:
            name = SmartSkipManager.extract_package_name(line)
            if name: all_targets.add(name)

        safe_uninstall_list = [
            pkg for pkg in all_targets
            if not BlenderInternalManager.is_protected(pkg)
        ]

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
        for filename in os.listdir(directory):
            if filename.endswith(".safetensors"):
                file_item = lora_files.add()
                file_item.name = filename.replace(".safetensors", "")
                file_item.enabled = False
                file_item.weight_value = 1.0
        return {"FINISHED"}


class IPAdapterFaceFileBrowserOperator(Operator):
    bl_idname = "ip_adapter_face.file_browser"
    bl_label = "Open IP Adapter Face File Browser"

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    import_as_folder: bpy.props.BoolProperty(name="Import as Folder", default=False)

    def execute(self, context):
        valid_image_extensions = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".gif", ".hdr"}
        scene = context.scene

        if self.filepath:
            if self.import_as_folder:
                files_to_import = bpy.context.scene.ip_adapter_face_files_to_import
                files_to_import.clear()
                print("Importing folder:", self.filepath)
                for file_path in glob.glob(os.path.join(self.filepath, "*")):
                    if os.path.isfile(file_path):
                        if os.path.splitext(file_path)[1].lower() in valid_image_extensions:
                            print("Found image file in folder:", os.path.basename(file_path))
                            new_file = files_to_import.add()
                            new_file.path = os.path.abspath(self.filepath)
                scene.ip_adapter_face_folder = os.path.abspath(os.path.dirname(self.filepath))
                self.report({"INFO"}, f"{len(files_to_import)} image files found in folder.")
            else:
                print("Importing file:", self.filepath)
                if os.path.splitext(self.filepath)[1].lower() in valid_image_extensions:
                    print("Adding image file:", os.path.basename(self.filepath))
                    files_to_import = bpy.context.scene.ip_adapter_face_files_to_import
                    new_file = files_to_import.add()
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
        valid_image_extensions = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".gif", ".hdr"}
        scene = context.scene

        if self.filepath:
            if self.import_as_folder:
                files_to_import = bpy.context.scene.ip_adapter_style_files_to_import
                files_to_import.clear()
                self.filepath = os.path.dirname(self.filepath)
                print("Importing folder:", self.filepath)
                for file_path in glob.glob(os.path.join(self.filepath, "*")):
                    if os.path.isfile(file_path):
                        if os.path.splitext(file_path)[1].lower() in valid_image_extensions:
                            print("Found image file in folder:", os.path.basename(file_path))
                            new_file = files_to_import.add()
                            new_file.name = os.path.basename(file_path)
                            new_file.path = os.path.abspath(file_path)
                scene.ip_adapter_style_folder = os.path.abspath(self.filepath)
                self.report({"INFO"}, f"{len(files_to_import)} image files found in folder.")
            else:
                print("Importing file:", self.filepath)
                if os.path.splitext(self.filepath)[1].lower() in valid_image_extensions:
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
