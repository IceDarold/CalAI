"""Speech-to-text providers. Converts voice messages to text."""

import logging
import tempfile
import os
from abc import ABC, abstractmethod
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

VOSK_MODEL_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "models" / "vosk-model-small-ru-0.22"


class BaseSTTProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, lang: str = "ru-RU") -> str | None:
        ...


class WhisperSTTProvider(BaseSTTProvider):
    """faster-whisper — local, offline, excellent Russian quality.

    Models: tiny (75MB), small (500MB), medium (1.5GB), large (3GB).
    Default: small — best quality/speed balance for Russian.
    """

    _model = None

    def _get_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            model_size = getattr(settings, 'whisper_model', 'small') or 'small'
            logger.info(f"Loading Whisper model: {model_size}...")
            self._model = WhisperModel(model_size, device="cpu", compute_type="int8")
            logger.info("Whisper model loaded.")
        return self._model

    async def transcribe(self, audio_bytes: bytes, lang: str = "ru-RU") -> str | None:
        try:
            model = self._get_model()

            # Write audio to temp file (Whisper reads files directly)
            with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp:
                tmp.write(audio_bytes)
                ogg_path = tmp.name

            try:
                segments, info = model.transcribe(
                    ogg_path, language="ru", beam_size=5,
                    vad_filter=True,  # skip silence
                )
                text = " ".join(s.text.strip() for s in segments if s.text.strip())
                return text if text else None
            finally:
                os.unlink(ogg_path)

        except Exception as e:
            logger.error(f"Whisper STT failed: {e}")
            return None


class VoskSTTProvider(BaseSTTProvider):
    """Local Vosk STT — lightweight, offline. Fallback if Whisper unavailable."""

    _model = None

    def _get_model(self):
        if self._model is None:
            import vosk
            if VOSK_MODEL_PATH.exists():
                self._model = vosk.Model(str(VOSK_MODEL_PATH))
        return self._model

    async def transcribe(self, audio_bytes: bytes, lang: str = "ru-RU") -> str | None:
        import vosk, json, subprocess
        try:
            model = self._get_model()
            if model is None:
                return None

            with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp:
                tmp.write(audio_bytes)
                ogg_path = tmp.name

            wav_path = ogg_path.replace('.ogg', '.wav')
            try:
                subprocess.run(
                    ['ffmpeg', '-y', '-i', ogg_path, '-ar', '16000', '-ac', '1',
                     '-f', 'wav', wav_path],
                    capture_output=True, timeout=10,
                )
                rec = vosk.KaldiRecognizer(model, 16000)
                with open(wav_path, 'rb') as wf:
                    while True:
                        data = wf.read(4000)
                        if not data:
                            break
                        rec.AcceptWaveform(data)
                text = json.loads(rec.FinalResult()).get('text', '').strip()
                return text if text else None
            finally:
                os.unlink(ogg_path)
                if os.path.exists(wav_path):
                    os.unlink(wav_path)
        except Exception as e:
            logger.error(f"Vosk STT failed: {e}")
            return None


def get_stt_provider() -> BaseSTTProvider | None:
    """Return the best available STT provider. Whisper > Vosk > None."""
    # Try Whisper first (best quality)
    try:
        import faster_whisper
        return WhisperSTTProvider()
    except ImportError:
        pass

    # Fall back to Vosk
    if VOSK_MODEL_PATH.exists():
        return VoskSTTProvider()

    return None
