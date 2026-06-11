"""Speech-to-text providers. Converts voice messages to text."""

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

YANDEX_STT_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
VOSK_MODEL_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "models" / "vosk-model-small-ru-0.22"


class BaseSTTProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, lang: str = "ru-RU") -> str | None:
        ...


class VoskSTTProvider(BaseSTTProvider):
    """Local Vosk STT — offline, no API keys, ~45 MB Russian model."""

    def __init__(self):
        self._model = None

    def _get_model(self):
        if self._model is None:
            import vosk
            if not VOSK_MODEL_PATH.exists():
                logger.error(f"Vosk model not found at {VOSK_MODEL_PATH}")
                return None
            self._model = vosk.Model(str(VOSK_MODEL_PATH))
        return self._model

    async def transcribe(self, audio_bytes: bytes, lang: str = "ru-RU") -> str | None:
        import vosk
        try:
            model = self._get_model()
            if model is None:
                return None

            # Vosk works with 16kHz mono WAV, but Telegram sends OGG/OPUS
            # We need to convert OGG → WAV first
            import subprocess
            import tempfile
            import os

            # Write OGG to temp file
            with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp_ogg:
                tmp_ogg.write(audio_bytes)
                ogg_path = tmp_ogg.name

            wav_path = ogg_path.replace('.ogg', '.wav')
            try:
                # Convert OGG to WAV (16kHz, mono) using ffmpeg
                subprocess.run(
                    ['ffmpeg', '-y', '-i', ogg_path, '-ar', '16000', '-ac', '1',
                     '-f', 'wav', wav_path],
                    capture_output=True, timeout=10,
                )

                # Read WAV and feed to Vosk
                rec = vosk.KaldiRecognizer(model, 16000)
                with open(wav_path, 'rb') as wf:
                    while True:
                        data = wf.read(4000)
                        if not data:
                            break
                        rec.AcceptWaveform(data)

                result = json.loads(rec.FinalResult())
                text = result.get('text', '').strip()
                return text if text else None
            finally:
                os.unlink(ogg_path)
                if os.path.exists(wav_path):
                    os.unlink(wav_path)

        except Exception as e:
            logger.error(f"Vosk STT failed: {e}")
            return None


class YandexSTTProvider(BaseSTTProvider):
    """Yandex SpeechKit — cloud STT, needs API key with speechkit-stt.user role."""

    async def transcribe(self, audio_bytes: bytes, lang: str = "ru-RU") -> str | None:
        if not settings.yandex_api_key or not settings.yandex_folder_id:
            return None
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    YANDEX_STT_URL,
                    headers={"Authorization": f"Api-Key {settings.yandex_api_key}"},
                    params={"folderId": settings.yandex_folder_id, "lang": lang},
                    content=audio_bytes,
                )
                resp.raise_for_status()
                return resp.json().get("result", "")
        except Exception as e:
            logger.error(f"Yandex STT failed: {e}")
            return None


def get_stt_provider() -> BaseSTTProvider:
    # Prefer local Vosk if model downloaded
    if VOSK_MODEL_PATH.exists():
        return VoskSTTProvider()
    # Fall back to Yandex SpeechKit if configured
    if settings.yandex_api_key and settings.yandex_folder_id:
        return YandexSTTProvider()
    return None  # No STT available
