"""Chat va lokal STT (faster-whisper) ni tekshiradi. API kalit faqat chat uchun kerak."""
import asyncio
import os
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openai import AsyncOpenAI  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.modules.tests.speaking import transcription  # noqa: E402


def write_silence(path: str) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)


async def check_chat() -> None:
    client = AsyncOpenAI(base_url=settings.OPENAI_BASE_URL.strip() or None,
                         api_key=settings.OPENAI_API_KEY, timeout=60.0)
    model = settings.SPEAKING_AI_MODEL or settings.OPENAI_MODEL
    try:
        resp = await client.chat.completions.create(
            model=model, max_tokens=200,
            messages=[{"role": "user", "content": "Reply with the word OK."}],
        )
        text = (resp.choices[0].message.content or "(bo'sh javob)").strip()
        print(f"[OK  ] Chat ({model}): {text[:80]}")
    except Exception as exc:  # noqa: BLE001
        print(f"[XATO] Chat ({model}): {str(exc)[:300]}")


def check_stt() -> None:
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        write_silence(path)
        res = transcription._transcribe_local(path)
        print(f"[OK  ] Lokal STT (model={settings.WHISPER_MODEL_SIZE}): matn={res['text']!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"[XATO] Lokal STT: {str(exc)[:300]}")
    finally:
        os.unlink(path)


if __name__ == "__main__":
    print(f"STT_PROVIDER={settings.STT_PROVIDER}")
    asyncio.run(check_chat())
    check_stt()