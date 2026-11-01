"""Speaking AI sozlamalarini tekshirish: OpenAI chat va transkripsiya (STT) ishlayaptimi.

Ishga tushirish (loyiha ildizida, venv faollashgan holda):
    python scripts/check_openai.py

Chiqishda har bir chaqiruv uchun ishlatilgan base_url, model va natija (yoki aniq xato) ko'rsatiladi.
"""
import asyncio
import io
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openai import AsyncOpenAI  # noqa: E402

from app.core.config import settings  # noqa: E402


def _silence_wav(seconds: float = 1.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buf.getvalue()


def _report(name: str, base_url: str | None, model: str, ok: bool, detail: str) -> None:
    mark = "OK  " if ok else "XATO"
    print(f"[{mark}] {name}\n        base_url: {base_url or 'https://api.openai.com/v1 (standart)'}\n        model:    {model}\n        natija:   {detail}\n")


async def check_chat(client: AsyncOpenAI, base_url, model: str) -> None:
    try:
        resp = await client.chat.completions.create(
            model=model, temperature=0, max_tokens=5,
            messages=[{"role": "user", "content": "Say OK"}],
        )
        _report("Chat (baholash)", base_url, model, True, (resp.choices[0].message.content or "").strip())
    except Exception as exc:  # noqa: BLE001
        _report("Chat (baholash)", base_url, model, False, f"{getattr(exc, 'status_code', '')} {getattr(exc, 'message', exc)}")


async def check_stt(base_url, model: str) -> None:
    client = AsyncOpenAI(base_url=base_url or None, api_key=settings.STT_API_KEY or settings.OPENAI_API_KEY, timeout=60.0)
    try:
        resp = await client.audio.transcriptions.create(
            model=model, file=("silence.wav", _silence_wav(), "audio/wav"), response_format="verbose_json",
        )
        _report("Transkripsiya (STT)", base_url, model, True, f"matn: {(getattr(resp, 'text', '') or '')!r}")
    except Exception as exc:  # noqa: BLE001
        _report("Transkripsiya (STT)", base_url, model, False, f"{getattr(exc, 'status_code', '')} {getattr(exc, 'message', exc)}")


def check_aws_transcribe() -> None:
    import boto3

    kwargs = {"region_name": settings.AWS_REGION}
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    bucket = settings.AWS_TRANSCRIBE_BUCKET
    try:
        boto3.client("transcribe", **kwargs).list_transcription_jobs(MaxResults=1)
        _report("AWS Transcribe (kirish)", None, settings.AWS_REGION, True, "kredensial ishlaydi")
    except Exception as exc:  # noqa: BLE001
        _report("AWS Transcribe (kirish)", None, settings.AWS_REGION, False, str(exc)[:300])
    if not bucket:
        _report("AWS S3 bucket", None, "-", False, "AWS_TRANSCRIBE_BUCKET bo'sh")
        return
    try:
        boto3.client("s3", **kwargs).head_bucket(Bucket=bucket)
        _report("AWS S3 bucket", None, bucket, True, "bucket mavjud va ruxsat bor")
    except Exception as exc:  # noqa: BLE001
        _report("AWS S3 bucket", None, bucket, False, str(exc)[:300])


async def main() -> None:
    if settings.STT_PROVIDER == "aws":
        check_aws_transcribe()
        await check_chat(AsyncOpenAI(base_url=settings.OPENAI_BASE_URL.strip() or None,
                                     api_key=settings.OPENAI_API_KEY, timeout=60.0),
                         settings.OPENAI_BASE_URL.strip() or None,
                         settings.SPEAKING_AI_MODEL or settings.OPENAI_MODEL)
        return
    if not settings.OPENAI_API_KEY:
        print("OPENAI_API_KEY bo'sh. .env ni tekshiring.")
        return
    chat_base = settings.OPENAI_BASE_URL.strip() or None
    stt_base = (settings.STT_BASE_URL or settings.OPENAI_BASE_URL).strip() or None
    chat_model = settings.SPEAKING_AI_MODEL or settings.OPENAI_MODEL
    client = AsyncOpenAI(base_url=chat_base, api_key=settings.OPENAI_API_KEY, timeout=60.0)
    await check_chat(client, chat_base, chat_model)
    await check_stt(stt_base, settings.STT_MODEL)
    print("Maslahat: base_url oxiri /v1 bo'lishi kerak (masalan https://api.openai.com/v1). 404 ko'pincha noto'g'ri manzil yoki model nomidan keladi.")


if __name__ == "__main__":
    asyncio.run(main())
