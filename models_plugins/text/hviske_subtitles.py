"""
Hviske Danish Subtitles — speaker-diarized ASR directly into VSE text strips.

Each detected speaker is assigned an individual, entirely-free VSE channel.
No speaker labels are written inside the subtitle text itself.

Requirements (install via add-on preferences → Install):
  accelerate
  transformers==4.57.6
  pyannote.audio
  soundfile
  torchaudio

Before using, accept the Pyannote Community-1 terms at:
  https://huggingface.co/pyannote/speaker-diarization-community-1
Then enter your HuggingFace token in the add-on preferences.

Workflow:
  1. Add a SOUND strip to the VSE and select it.
  2. Switch to the Text model panel and pick "Hviske: Danish Subtitles".
  3. Make sure your HF token is entered.
  4. Click Generate — subtitle strips appear, one channel per speaker.
"""

import gc
import os
import re
import time
import warnings

from ...models.base import ModelPlugin, InputSpec, UISection, ParamSpec, ModelInputs

_DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
_ASR_MODEL_ID      = "syvai/hviske-v5.3"
_BATCH_SIZE        = 8
_TARGET_SR         = 16_000


# ---------------------------------------------------------------------------
# Internal helpers (module-level so they don't rebuild on every call)
# ---------------------------------------------------------------------------

def _format_two_lines(text: str) -> str:
    """Split long text at the word nearest the midpoint."""
    if len(text) <= 45:
        return text
    mid    = len(text) // 2
    spaces = [i for i, c in enumerate(text) if c == " "]
    if not spaces:
        return text
    closest = min(spaces, key=lambda x: abs(x - mid))
    return text[:closest] + "\n" + text[closest + 1:]


def _break_into_subtitle_chunks(start: float, end: float, text: str,
                                 speaker: str, max_chars: int = 80) -> list:
    """Slice one long utterance into broadcast-standard subtitle chunks."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []

    parts   = re.split(r"(?<=[.!?,]) +", text)
    chunks: list = []
    current = ""

    for part in parts:
        fits = len(current) + len(part) + (1 if current else 0) <= max_chars
        if fits:
            current = (current + " " + part) if current else part
        else:
            if current:
                chunks.append(current)
            if len(part) > max_chars:
                words = part.split()
                temp  = ""
                for w in words:
                    if len(temp) + len(w) + (1 if temp else 0) <= max_chars:
                        temp = (temp + " " + w) if temp else w
                    else:
                        chunks.append(temp)
                        temp = w
                current = temp
            else:
                current = part
    if current:
        chunks.append(current)

    total_chars = sum(len(c) for c in chunks)
    total_dur   = end - start
    subs        = []
    cur_start   = start

    for c in chunks:
        if not total_chars:
            break
        frac     = len(c) / total_chars
        orig_dur = total_dur * frac
        # Cap display time to a comfortable reading speed (~8 chars/s)
        disp_dur = min(orig_dur, max(2.0, len(c) * 0.12))
        subs.append({
            "start":   cur_start,
            "end":     cur_start + disp_dur,
            "speaker": speaker,
            "text":    c.strip(),
        })
        cur_start += orig_dur

    return subs


def _find_free_channels(seq_editor, start_frame: int, end_frame: int, count: int) -> list:
    """Return `count` channel numbers that are entirely free in [start_frame, end_frame)."""
    all_strips = list(seq_editor.strips_all)
    free: list = []
    ch = 1
    while len(free) < count:
        for seq in all_strips:
            if (
                seq.channel == ch
                and seq.frame_final_start < end_frame
                and (seq.frame_final_start + seq.frame_final_duration) > start_frame
            ):
                break  # channel occupied — try next
        else:
            free.append(ch)
        ch += 1
    return free


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

class HviskeDanishSubtitlesPlugin(ModelPlugin):
    MODEL_ID     = _ASR_MODEL_ID
    DISPLAY_NAME = "Hviske: Danish Subtitles"
    MODEL_TYPE   = "text"
    DESCRIPTION  = (
        "Speaker-diarized Danish ASR → VSE text strips. "
        "Select a SOUND strip, set your HuggingFace token, then Generate. "
        "Each speaker gets its own channel."
    )

    INPUTS      = InputSpec.HF_TOKEN
    UI_SECTIONS = []
    PARAMS      = ParamSpec()

    REQUIRED_PACKAGES = [
        "torch", "transformers", "accelerate",
        "pyannote.audio", "soundfile", "torchaudio",
    ]

    requires_input_strip = True   # user must select a strip

    # load() is lightweight — heavy models are managed inside generate()
    # to free VRAM between the diarization and ASR steps.
    def load(self, prefs, scene, **kw) -> dict:
        return {"model": None, "processor": None, "tokenizer": None}

    def generate(self, pipe, inputs: ModelInputs, scene, prefs):
        import bpy
        import numpy as np
        import soundfile as sf
        import torch
        import torchaudio.functional as _taf
        from pyannote.audio import Pipeline as PyannotePipeline
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

        warnings.filterwarnings("ignore", message=".*degrees of freedom is <= 0.*")
        torch.backends.cuda.matmul.allow_tf32 = True

        hf_token = prefs.hugginface_token
        local    = prefs.local_files_only

        # ── 0. locate the source SOUND strip ───────────────────────────────
        seq_editor = scene.sequence_editor
        if not seq_editor:
            print("Hviske: No sequence editor found.")
            return None

        _pallaidium_dir = "Pallaidium_Media"

        def _is_source_sound(strip):
            """True if this strip is a non-Pallaidium SOUND with a real file."""
            if strip.type != "SOUND":
                return False
            if not getattr(strip, "sound", None):
                return False
            p = bpy.path.abspath(strip.sound.filepath)
            return _pallaidium_dir not in p and os.path.isfile(p)

        # 1. active strip if it's a source SOUND
        candidate = seq_editor.active_strip
        if candidate and not _is_source_sound(candidate):
            candidate = None

        # 2. any selected source SOUND strip
        if candidate is None:
            for seq in seq_editor.strips_all:
                if seq.select and _is_source_sound(seq):
                    candidate = seq
                    break

        # 3. longest source SOUND strip in the entire timeline
        if candidate is None:
            source_strips = [s for s in seq_editor.strips_all if _is_source_sound(s)]
            if source_strips:
                candidate = max(source_strips, key=lambda s: s.frame_final_duration)

        if candidate is None:
            print(
                "Hviske: No source SOUND strip found. "
                "Add your audio file to the VSE as a SOUND strip."
            )
            return None

        active            = candidate
        audio_path        = bpy.path.abspath(active.sound.filepath)
        strip_start_frame = active.frame_final_start

        render       = scene.render
        fps          = render.fps / render.fps_base
        # seconds of audio trimmed from the start of the file
        audio_offset = getattr(active, "frame_offset_start", 0) / fps

        if not os.path.isfile(audio_path):
            print(f"Hviske: Audio file not found: {audio_path}")
            return None

        # ── 1. audio preprocessing ─────────────────────────────────────────
        print("── Hviske Step 1: Audio Preprocessing ──")
        print(f"Hviske: strip={active.name!r}  path={audio_path!r}")
        audio, sr = sf.read(audio_path)
        audio = np.asarray(audio, dtype=np.float32)

        # stereo (or any multi-channel) → mono, matching the original script
        if audio.ndim > 1:
            audio = np.mean(audio, axis=1)

        duration_s = len(audio) / sr
        print(f"Hviske: {duration_s:.1f}s  {sr} Hz  {'stereo→mono' if audio.ndim > 1 else 'mono'}")

        if duration_s < 5.0:
            print(
                f"Hviske: Audio is only {duration_s:.1f}s — pyannote needs at least "
                f"~5 s of speech. Select a longer SOUND strip."
            )
            return None

        # resample to 16 kHz for both pyannote and Hviske ASR
        if sr != _TARGET_SR:
            print(f"Hviske: Resampling {sr} Hz → {_TARGET_SR} Hz …")
            audio = _taf.resample(
                torch.from_numpy(audio), orig_freq=int(sr), new_freq=_TARGET_SR
            ).numpy()
            sr = _TARGET_SR

        # ── 2. speaker diarization ─────────────────────────────────────────
        print("── Hviske Step 2: Speaker Diarization ──")
        try:
            dia_pipe = PyannotePipeline.from_pretrained(
                _DIARIZATION_MODEL, token=hf_token
            )
            dia_pipe.to(torch.device("cuda"))
        except Exception as exc:
            print(
                f"Hviske: Could not load Pyannote. Verify your HF token and that you "
                f"have accepted the terms at https://hf.co/{_DIARIZATION_MODEL}\n{exc}"
            )
            return None

        # Call pipeline with the file path, exactly as the original script does
        print("Hviske: Analysing speakers …")
        diarization = dia_pipe(audio_path)

        # speaker_diarization is a pyannote.core.Annotation
        ann = diarization.speaker_diarization
        segments: list = []
        for turn, _, speaker in ann.itertracks(yield_label=True):
            segments.append({"start": turn.start, "end": turn.end, "speaker": speaker})

        # merge same-speaker segments whose gap is < 1.5 s
        merged: list = []
        for seg in segments:
            if (
                merged
                and seg["speaker"] == merged[-1]["speaker"]
                and (seg["start"] - merged[-1]["end"]) < 1.5
            ):
                merged[-1]["end"] = max(merged[-1]["end"], seg["end"])
            else:
                merged.append(dict(seg))

        valid = [s for s in merged if (s["end"] - s["start"]) >= 0.5]
        valid.sort(key=lambda x: x["start"])
        print(f"Hviske: {len(valid)} consolidated speaker segments.")

        del dia_pipe
        gc.collect()
        torch.cuda.empty_cache()

        if not valid:
            print("Hviske: No speaker segments found.")
            return None

        # ── 3. ASR transcription ───────────────────────────────────────────
        print("── Hviske Step 3: ASR Transcription ──")
        processor = AutoProcessor.from_pretrained(
            _ASR_MODEL_ID, trust_remote_code=True, local_files_only=local
        )
        asr_model = AutoModelForSpeechSeq2Seq.from_pretrained(
            _ASR_MODEL_ID,
            trust_remote_code=True,
            torch_dtype=torch.float16,
            low_cpu_mem_usage=True,
            local_files_only=local,
        ).to("cuda").eval()

        print(f"Hviske: Transcribing {len(valid)} segments (batch size {_BATCH_SIZE}) …")
        t0 = time.time()

        with torch.no_grad():
            for i in range(0, len(valid), _BATCH_SIZE):
                batch = valid[i : i + _BATCH_SIZE]
                audio_arrays = [
                    audio[
                        max(0, int(s["start"] * sr)) : min(len(audio), int(s["end"] * sr))
                    ]
                    for s in batch
                ]
                outputs = asr_model.transcribe(
                    processor=processor,
                    language="da",
                    audio_arrays=audio_arrays,
                    sample_rates=[sr] * len(audio_arrays),
                )
                for seg, txt in zip(batch, outputs):
                    seg["text"] = txt.strip()

        print(f"Hviske: Transcription completed in {time.time() - t0:.1f}s")

        del asr_model, processor
        gc.collect()
        torch.cuda.empty_cache()

        # ── 4. chunk utterances into subtitle-sized pieces ─────────────────
        print("── Hviske Step 4: Chunking into Subtitles ──")
        all_subs: list = []
        for seg in valid:
            txt = seg.get("text", "").strip()
            if not txt:
                continue
            all_subs.extend(
                _break_into_subtitle_chunks(seg["start"], seg["end"], txt, seg["speaker"])
            )
        all_subs.sort(key=lambda x: x["start"])

        if not all_subs:
            print("Hviske: No subtitles generated (all segments were empty).")
            return None

        # ── 5. insert text strips — one VSE channel per speaker ────────────
        print("── Hviske Step 5: Inserting VSE Text Strips ──")
        unique_speakers = sorted(set(s["speaker"] for s in all_subs))
        num_speakers    = len(unique_speakers)

        last_end_frame = strip_start_frame + int(all_subs[-1]["end"] * fps) + 2
        channels       = _find_free_channels(seq_editor, strip_start_frame, last_end_frame, num_speakers)
        spk_to_ch      = {spk: channels[idx] for idx, spk in enumerate(unique_speakers)}

        print(f"Hviske: {num_speakers} speaker(s) → channels {channels}")

        created = 0
        for sub in all_subs:
            # Offset subtitle times by any audio trim at the strip's start
            sub_start_f = strip_start_frame + int((sub["start"] - audio_offset) * fps)
            sub_end_f   = strip_start_frame + int((sub["end"]   - audio_offset) * fps)
            length_f    = max(1, sub_end_f - sub_start_f)
            channel     = spk_to_ch[sub["speaker"]]
            disp_text   = _format_two_lines(sub["text"])

            new_strip = seq_editor.strips.new_effect(
                name=disp_text[:63],
                type="TEXT",
                frame_start=sub_start_f,
                length=length_f,
                channel=channel,
            )
            # Blender 4.x / 5.x compatibility for setting the right handle
            if hasattr(new_strip, "right_handle"):
                new_strip.right_handle = sub_end_f
            else:
                try:
                    new_strip.frame_final_end = sub_end_f
                except Exception:
                    pass

            new_strip.text        = disp_text
            new_strip.wrap_width  = 0.68
            new_strip.font_size   = 16
            new_strip.location[0] = 0.5
            new_strip.location[1] = 0.2
            new_strip.anchor_x    = "CENTER"
            new_strip.anchor_y    = "TOP"
            new_strip.alignment_x = "LEFT"
            new_strip.use_shadow  = True
            new_strip.use_box     = True
            new_strip.box_color   = (0, 0, 0, 0.7)
            created += 1

        print(
            f"Hviske: Done — {created} subtitle strips inserted "
            f"across {num_speakers} channel(s)."
        )

        # Return None so the text operator does not create an extra strip
        # (the operator guard is `if text:`, so None → no strip)
        return None
