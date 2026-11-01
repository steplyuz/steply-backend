"""Lokal STT (faster-whisper): natija formati va provayder tanlash. Haqiqiy model yuklanmaydi."""
import asyncio
import os
import tempfile
import types

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/local_whisper.db"

import pytest

from app.core.config import settings
from app.modules.tests.speaking import transcription


def _fake_segment(text, words):
    return types.SimpleNamespace(
        text=text,
        words=[types.SimpleNamespace(word=w, start=s, end=e) for (w, s, e) in words],
    )


class _FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, path, **kwargs):
        self.calls.append((path, kwargs))
        segments = iter([
            _fake_segment(" I like", [(" I", 0.1, 0.4), (" like", 0.5, 0.9)]),
            _fake_segment(" tea.", [(" tea.", 2.0, 2.3), ("  ", 2.4, 2.5)]),
        ])
        info = types.SimpleNamespace(duration=2.6)
        return segments, info


@pytest.fixture
def audio_file():
    f = tempfile.NamedTemporaryFile(suffix=".webm", delete=False)
    f.write(b"\x1a\x45\xdf\xa3" + b"0" * 50)
    f.close()
    yield f.name
    os.unlink(f.name)


def test_local_transcribe_maps_segments_to_common_format(monkeypatch, audio_file):
    model = _FakeModel()
    monkeypatch.setattr(transcription, "_get_whisper_model", lambda: model)
    res = transcription._transcribe_local(audio_file)
    assert res["text"] == "I like tea."
    assert res["words"] == [
        {"w": "I", "s": 0.1, "e": 0.4},
        {"w": "like", "s": 0.5, "e": 0.9},
        {"w": "tea.", "s": 2.0, "e": 2.3},
    ]
    assert res["duration"] == 2.6
    path, kwargs = model.calls[0]
    assert path == audio_file
    assert kwargs["word_timestamps"] is True and kwargs["language"] == "en"


def test_transcribe_dispatches_to_local_when_selected(monkeypatch, audio_file):
    model = _FakeModel()
    monkeypatch.setattr(transcription, "_get_whisper_model", lambda: model)
    monkeypatch.setattr(settings, "STT_PROVIDER", "local")
    res = asyncio.run(transcription.transcribe(audio_file))
    assert res["text"] == "I like tea."
    assert len(model.calls) == 1


def test_local_model_is_loaded_once(monkeypatch):
    created = []

    class _Factory:
        def __init__(self, *args, **kwargs):
            created.append((args, kwargs))

    import sys
    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = _Factory
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.setattr(transcription, "_whisper_model", None)
    transcription._get_whisper_model()
    transcription._get_whisper_model()
    assert len(created) == 1
    args, kwargs = created[0]
    assert args[0] == settings.WHISPER_MODEL_SIZE
    assert kwargs["device"] == settings.WHISPER_DEVICE
    assert kwargs["compute_type"] == settings.WHISPER_COMPUTE_TYPE
