"""Voice cloning TTS via Qwen3-TTS (Qwen/Qwen3-TTS-12Hz-1.7B-Base)."""

from ...models.base import ModelPlugin, InputSpec, UISection, ParamSpec, ModelInputs
from ...utils.helpers import solve_path, clean_filename


class Qwen3TTSPlugin(ModelPlugin):
    MODEL_ID     = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
    DISPLAY_NAME = "TTS: Qwen3 (voice clone)"
    MODEL_TYPE   = "audio"
    DESCRIPTION  = "Voice cloning TTS — requires speaker audio + reference text"

    # Both audio reference AND reference transcription are required
    INPUTS       = InputSpec.PROMPT | InputSpec.AUDIO_REF_REQ | InputSpec.TEXT_REF
    UI_SECTIONS  = [
        UISection.PROMPT,
        UISection.AUDIO_REF,
        UISection.TEXT_REF,
        UISection.SEED,
    ]
    PARAMS       = ParamSpec(audio_ref_required=True)
    REQUIRED_PACKAGES = ["torch", "soundfile", "faster_qwen3_tts"]

    def load(self, prefs, scene, **kw):
        import torch
        from faster_qwen3_tts import FasterQwen3TTS

        device = (
            "cuda" if torch.cuda.is_available()
            else "mps" if torch.backends.mps.is_available()
            else "cpu"
        )
        print(f"Loading Qwen3-TTS on {device}…")
        model = FasterQwen3TTS.from_pretrained(
            self.MODEL_ID,
            dtype=torch.bfloat16,
        )
        return {"pipe": None, "model": model, "vocoder": None, "feature_extractor": None}

    def generate(self, pipe_obj, inputs: ModelInputs, scene, prefs) -> str:
        import torch
        import soundfile as sf
        import random

        model = pipe_obj["model"]
        device = (
            "cuda" if torch.cuda.is_available()
            else "mps" if torch.backends.mps.is_available()
            else "cpu"
        )
        seed = inputs.seed
        if device == "cuda":
            torch.cuda.manual_seed_all(seed)
        random.seed(seed)

        if not inputs.audio_ref:
            raise ValueError("Qwen3-TTS requires a speaker reference audio file.")
        if not inputs.text_ref:
            raise ValueError("Qwen3-TTS requires a reference transcription text file.")

        output_path = solve_path(clean_filename(str(seed) + "_" + inputs.prompt) + ".wav")

        print(f"Qwen3-TTS generating…  ref_audio={inputs.audio_ref}")
        wavs, sr = model.generate_voice_clone(
            text=inputs.prompt,
            language="English",
            ref_audio=inputs.audio_ref,
            ref_text=inputs.text_ref,
        )
        if not wavs:
            raise RuntimeError("Qwen3-TTS: generation returned no audio.")

        sf.write(output_path, wavs[0], sr)
        print("Qwen3-TTS audio saved:", output_path)
        return output_path
