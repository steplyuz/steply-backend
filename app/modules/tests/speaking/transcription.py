"""Ovozni matnga aylantirish (STT).

Provayder STT_PROVIDER orqali tanlanadi:
- "openai": OpenAI-mos API (whisper-1), STT_BASE_URL / STT_API_KEY;
- "aws": Amazon Transcribe. Audio S3 ga yuklanadi, transkripsiya ishga tushadi,
  natija so'z vaqtlari bilan olinadi va S3 dagi vaqtinchalik fayllar o'chiriladi.

Ikkala provayder ham bir xil natija beradi: {"text", "words": [{"w","s","e"}], "duration"}.
"""
import asyncio
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 24 * 1024 * 1024  # OpenAI-mos STT limiti ~25 MB
AWS_JOB_TIMEOUT_SECONDS = 300
AWS_MEDIA_FORMATS = {"m4a": "m4a", "mp3": "mp3", "mp4": "mp4", "wav": "wav", "ogg": "ogg", "webm": "webm"}

_client = AsyncOpenAI(
    base_url=(settings.STT_BASE_URL or settings.OPENAI_BASE_URL).strip() or None,
    api_key=settings.STT_API_KEY or settings.OPENAI_API_KEY,
    timeout=120.0,
    max_retries=2,
)


async def transcribe(path: str) -> dict:
    if os.path.getsize(path) > MAX_AUDIO_BYTES:
        raise ValueError("Audio fayl STT limitidan katta")
    if settings.STT_PROVIDER == "local":
        return await asyncio.to_thread(_transcribe_local, path)
    if settings.STT_PROVIDER == "aws":
        return await asyncio.to_thread(_transcribe_aws, path)
    return await _transcribe_openai(path)


async def _transcribe_openai(path: str) -> dict:
    with open(path, "rb") as f:
        resp = await _client.audio.transcriptions.create(
            model=settings.STT_MODEL,
            file=f,
            language="en",
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )
    words = [{"w": w.word, "s": w.start, "e": w.end} for w in (getattr(resp, "words", None) or [])]
    return {"text": (resp.text or "").strip(), "words": words, "duration": getattr(resp, "duration", None)}


_whisper_model = None
_whisper_lock = threading.Lock()


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel

        _whisper_model = WhisperModel(
            settings.WHISPER_MODEL_SIZE,
            device=settings.WHISPER_DEVICE,
            compute_type=settings.WHISPER_COMPUTE_TYPE,
        )
    return _whisper_model


def _transcribe_local(path: str) -> dict:
    """Lokal Whisper (faster-whisper): API kaliti va tashqi xizmat kerak emas.

    Model bitta bo'lib, qulf bilan ishlatiladi (bir vaqtda bir nechta so'rov xavfsiz bo'lsin).
    """
    with _whisper_lock:
        model = _get_whisper_model()
        segments, info = model.transcribe(path, language="en", word_timestamps=True, vad_filter=True)
        parts: list[str] = []
        words: list[dict] = []
        for seg in segments:
            parts.append(seg.text)
            for w in seg.words or []:
                token = (w.word or "").strip()
                if token:
                    words.append({"w": token, "s": float(w.start), "e": float(w.end)})
    text = "".join(parts).strip()
    duration = float(info.duration) if info is not None and getattr(info, "duration", None) is not None else None
    return {"text": text, "words": words, "duration": duration}


def aws_result_to_transcript(doc: dict) -> dict:
    """Amazon Transcribe JSON natijasini umumiy formatga keltiradi (faqat so'zlar, punktuatsiyasiz vaqt)."""
    results = doc.get("results") or {}
    transcripts = results.get("transcripts") or []
    text = (transcripts[0].get("transcript") if transcripts else "") or ""
    words = []
    for item in results.get("items") or []:
        if item.get("type") != "pronunciation":
            continue
        alternatives = item.get("alternatives") or []
        if not alternatives:
            continue
        try:
            start = float(item["start_time"])
            end = float(item["end_time"])
        except (KeyError, TypeError, ValueError):
            continue
        words.append({"w": alternatives[0].get("content", ""), "s": start, "e": end})
    duration = words[-1]["e"] if words else None
    return {"text": text.strip(), "words": words, "duration": duration}


def _aws_session_kwargs() -> dict:
    kwargs = {"region_name": settings.AWS_REGION}
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
        kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
    return kwargs


def _transcribe_aws(path: str) -> dict:
    """Sinxron (asyncio.to_thread ichida ishlaydi)."""
    import boto3

    bucket = settings.AWS_TRANSCRIBE_BUCKET
    if not bucket:
        raise RuntimeError("AWS_TRANSCRIBE_BUCKET sozlanmagan")
    suffix = Path(path).suffix.lower().lstrip(".")
    media_format = AWS_MEDIA_FORMATS.get(suffix)
    if media_format is None:
        raise ValueError(f"AWS Transcribe bu audio formatni qo'llamaydi: {suffix}")

    s3 = boto3.client("s3", **_aws_session_kwargs())
    transcribe = boto3.client("transcribe", **_aws_session_kwargs())
    token = uuid.uuid4().hex
    media_key = f"stt/{token}.{suffix}"
    job_name = f"stt-{token}"
    out_key = f"stt-out/{job_name}.json"

    s3.upload_file(path, bucket, media_key)
    try:
        transcribe.start_transcription_job(
            TranscriptionJobName=job_name,
            LanguageCode=settings.AWS_TRANSCRIBE_LANGUAGE,
            MediaFormat=media_format,
            Media={"MediaFileUri": f"s3://{bucket}/{media_key}"},
            OutputBucketName=bucket,
            OutputKey=out_key,
        )
        deadline = time.monotonic() + AWS_JOB_TIMEOUT_SECONDS
        while True:
            info = transcribe.get_transcription_job(TranscriptionJobName=job_name)["TranscriptionJob"]
            status = info["TranscriptionJobStatus"]
            if status == "COMPLETED":
                break
            if status == "FAILED":
                reason = info.get("FailureReason") or "noma'lum"
                raise RuntimeError("Transcribe xato: " + reason)
            if time.monotonic() > deadline:
                raise TimeoutError("Transkripsiya vaqti tugadi")
            time.sleep(2)
        body = s3.get_object(Bucket=bucket, Key=out_key)["Body"].read()
        return aws_result_to_transcript(json.loads(body))
    finally:
        # Vaqtinchalik fayllar va job tozalanadi (xato bo'lsa ham).
        for fn, kwargs in (
            (s3.delete_object, {"Bucket": bucket, "Key": media_key}),
            (s3.delete_object, {"Bucket": bucket, "Key": out_key}),
            (transcribe.delete_transcription_job, {"TranscriptionJobName": job_name}),
        ):
            try:
                fn(**kwargs)
            except Exception:  # noqa: BLE001
                logger.warning("AWS vaqtinchalik resurs o'chmadi: %s", kwargs)


LONG_PAUSE_SECONDS = 2.0


def speech_metrics(words: list[dict], duration: float | None = None) -> dict:
    """Audio vaqt belgilaridan nutq metrikalari (STT so'z vaqtlari asosida).

    Pauza = ikki so'z orasidagi bo'shliq. "Uzoq pauza" >= LONG_PAUSE_SECONDS.
    Talaffuz bu yerda o'lchanmaydi: u faqat audio ovozidan baholanadi.
    """
    if not words:
        return {"words": 0, "speech_seconds": 0.0, "wpm": 0.0, "pauses_2s": 0,
                "longest_pause_s": 0.0, "audio_seconds": round(duration or 0.0, 1)}
    speech = max(0.0, float(words[-1]["e"]) - float(words[0]["s"]))
    gaps = [max(0.0, float(b["s"]) - float(a["e"])) for a, b in zip(words, words[1:])]
    wpm = (len(words) / speech * 60) if speech > 0 else 0.0
    return {
        "words": len(words),
        "speech_seconds": round(speech, 1),
        "wpm": round(wpm, 1),
        "pauses_2s": sum(1 for g in gaps if g >= LONG_PAUSE_SECONDS),
        "longest_pause_s": round(max(gaps), 2) if gaps else 0.0,
        "audio_seconds": round(duration or 0.0, 1),
    }
