"""Amazon Transcribe STT: natijani o'girish, provayder tanlash va butun oqim (boto3 soxta klientlari bilan)."""
import asyncio
import io
import json
import os
import tempfile

# DATABASE_URL app importidan OLDIN o'rnatilishi shart (boshqa testlar bilan bazani aralashtirmaslik uchun)
_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/aws_stt.db"

import pytest

from app.core.config import settings
from app.modules.tests.speaking import transcription

SAMPLE_TRANSCRIBE_JSON = {
    "results": {
        "transcripts": [{"transcript": "I like tea ."}],
        "items": [
            {"type": "pronunciation", "start_time": "0.1", "end_time": "0.4",
             "alternatives": [{"content": "I", "confidence": "0.9"}]},
            {"type": "pronunciation", "start_time": "0.5", "end_time": "0.9",
             "alternatives": [{"content": "like", "confidence": "0.9"}]},
            {"type": "punctuation", "alternatives": [{"content": "."}]},
            {"type": "pronunciation", "start_time": "2.0", "end_time": "2.3",
             "alternatives": [{"content": "tea", "confidence": "0.8"}]},
        ],
    }
}


def test_aws_result_is_converted_to_common_format():
    out = transcription.aws_result_to_transcript(SAMPLE_TRANSCRIBE_JSON)
    assert out["text"] == "I like tea ."
    assert out["words"] == [
        {"w": "I", "s": 0.1, "e": 0.4},
        {"w": "like", "s": 0.5, "e": 0.9},
        {"w": "tea", "s": 2.0, "e": 2.3},
    ]
    assert out["duration"] == 2.3


def test_aws_result_handles_empty_transcript():
    out = transcription.aws_result_to_transcript({"results": {"transcripts": [], "items": []}})
    assert out == {"text": "", "words": [], "duration": None}


def test_transcribe_dispatches_to_aws_when_selected(monkeypatch):
    called = {}

    def fake_aws(path):
        called["path"] = path
        return {"text": "ok", "words": [], "duration": None}

    tmp = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
    tmp.write(b"0" * 10)
    tmp.close()
    monkeypatch.setattr(settings, "STT_PROVIDER", "aws")
    monkeypatch.setattr(transcription, "_transcribe_aws", fake_aws)
    try:
        res = asyncio.run(transcription.transcribe(tmp.name))
    finally:
        os.unlink(tmp.name)
    assert res["text"] == "ok" and called["path"] == tmp.name


class _FakeS3:
    def __init__(self, store):
        self.store = store
        self.deleted = []

    def upload_file(self, path, bucket, key):
        self.store[(bucket, key)] = open(path, "rb").read()

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.store[(Bucket, Key)])}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)


class _FakeTranscribe:
    def __init__(self, store, status="COMPLETED", reason=None):
        self.store = store
        self.status = status
        self.reason = reason
        self.started = None
        self.deleted = []

    def start_transcription_job(self, **kwargs):
        self.started = kwargs
        if self.status == "COMPLETED":
            self.store[(kwargs["OutputBucketName"], kwargs["OutputKey"])] = json.dumps(SAMPLE_TRANSCRIBE_JSON).encode()

    def get_transcription_job(self, TranscriptionJobName):
        info = {"TranscriptionJobName": TranscriptionJobName, "TranscriptionJobStatus": self.status}
        if self.reason:
            info["FailureReason"] = self.reason
        return {"TranscriptionJob": info}

    def delete_transcription_job(self, TranscriptionJobName):
        self.deleted.append(TranscriptionJobName)


def _patch_boto(monkeypatch, status="COMPLETED", reason=None):
    import boto3

    store = {}
    s3 = _FakeS3(store)
    tc = _FakeTranscribe(store, status=status, reason=reason)

    def fake_client(service, **kwargs):
        return s3 if service == "s3" else tc

    monkeypatch.setattr(boto3, "client", fake_client)
    monkeypatch.setattr(settings, "AWS_TRANSCRIBE_BUCKET", "steply-stt")
    monkeypatch.setattr(settings, "AWS_TRANSCRIBE_LANGUAGE", "en-US")
    return s3, tc


def _audio_file(suffix=".webm"):
    f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    f.write(b"\x1a\x45\xdf\xa3" + b"0" * 50)
    f.close()
    return f.name


def test_aws_flow_uploads_runs_job_returns_words_and_cleans_up(monkeypatch):
    s3, tc = _patch_boto(monkeypatch)
    path = _audio_file()
    try:
        res = transcription._transcribe_aws(path)
    finally:
        os.unlink(path)
    assert res["words"][0] == {"w": "I", "s": 0.1, "e": 0.4}
    assert tc.started["MediaFormat"] == "webm"
    assert tc.started["LanguageCode"] == "en-US"
    assert tc.started["Media"]["MediaFileUri"].startswith("s3://steply-stt/stt/")
    # vaqtinchalik fayllar va job o'chiriladi
    assert len(s3.deleted) == 2
    assert len(tc.deleted) == 1


def test_aws_flow_failed_job_raises_and_still_cleans_up(monkeypatch):
    s3, tc = _patch_boto(monkeypatch, status="FAILED", reason="bad media")
    path = _audio_file()
    try:
        with pytest.raises(RuntimeError, match="bad media"):
            transcription._transcribe_aws(path)
    finally:
        os.unlink(path)
    assert len(tc.deleted) == 1


def test_aws_flow_rejects_unsupported_format(monkeypatch):
    _patch_boto(monkeypatch)
    path = _audio_file(suffix=".flac")
    try:
        with pytest.raises(ValueError):
            transcription._transcribe_aws(path)
    finally:
        os.unlink(path)


def test_aws_flow_requires_bucket(monkeypatch):
    _patch_boto(monkeypatch)
    monkeypatch.setattr(settings, "AWS_TRANSCRIBE_BUCKET", "")
    path = _audio_file()
    try:
        with pytest.raises(RuntimeError, match="AWS_TRANSCRIBE_BUCKET"):
            transcription._transcribe_aws(path)
    finally:
        os.unlink(path)
