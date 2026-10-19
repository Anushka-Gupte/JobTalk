"""Speech-to-text with faster-whisper (open source, runs on CPU)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jobtalk.config import Settings


def _patch_pyav() -> None:
    """Patch PyAV av.open to ignore removed metadata_errors parameter in PyAV >= 14."""
    try:
        import av

        orig_open = av.open
        if not getattr(orig_open, "_jobtalk_patched", False):

            def safe_open(*args: Any, **kwargs: Any) -> Any:
                kwargs.pop("metadata_errors", None)
                return orig_open(*args, **kwargs)

            safe_open._jobtalk_patched = True  # type: ignore[attr-defined]
            av.open = safe_open
    except ImportError:
        pass


_patch_pyav()


def transcribe(
    audio_path: str | Path,
    settings: Settings | None = None,
    initial_prompt: str | None = None,
) -> str:
    _patch_pyav()
    if settings is None:
        settings = Settings()
    prompt = initial_prompt if initial_prompt is not None else settings.whisper_initial_prompt
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Audio support missing. Run: pip install 'jobtalk[audio]'") from exc
    model = WhisperModel(settings.whisper_model, compute_type="int8")
    kwargs = {"vad_filter": True, "initial_prompt": prompt}
    if settings.whisper_language and not settings.whisper_model.endswith(".en"):
        kwargs["language"] = settings.whisper_language
    elif settings.whisper_model.endswith(".en"):
        kwargs["language"] = "en"
    segments, _ = model.transcribe(str(audio_path), **kwargs)
    return " ".join(seg.text.strip() for seg in segments)

