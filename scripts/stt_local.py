"""Audio faylni lokal Whisper bilan matnga aylantiradi. API kaliti va internet (model yuklangach) kerak emas.

Ishlatish (loyiha ildizida, venv faollashgan holda):
    python scripts\\stt_local.py <audio-fayl> [--json]

Sozlamalar .env dan olinadi: WHISPER_MODEL_SIZE, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.tests.speaking import transcription  # noqa: E402


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)
    path = args[0]
    if not Path(path).is_file():
        print(f"Fayl topilmadi: {path}")
        sys.exit(1)
    res = transcription._transcribe_local(path)
    print(res["text"] or "(matn topilmadi)")
    print(f"\nSo'zlar: {len(res['words'])} | davomiylik: {res['duration']} s")
    if "--json" in sys.argv:
        print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
