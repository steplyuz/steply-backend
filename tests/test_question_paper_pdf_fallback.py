from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from app.modules.exams.paper import question_paper_pdf as pdf_module
from app.modules.exams.paper.question_paper_pdf import render_listening_pdf, render_reading_pdf, render_writing_pdf


def _sample_exam():
    return {
        "title": "Steply Reading test",
        "cefr_level": "B1",
        "duration_minutes": 60,
        "parts": [
            {
                "part_number": 5,
                "title": "Gap fill",
                "passage": "A passage with real test content.",
                "questions": [
                    {"question_number": 30, "type": "GAP_FILL", "text": "Complete the sentence with one word."},
                    {
                        "question_number": 31,
                        "type": "MULTIPLE_CHOICE",
                        "text": "Choose the correct answer.",
                        "options": [{"label": "A", "value": "First"}, {"label": "B", "value": "Second"}],
                    },
                ],
            }
        ],
    }


def test_reading_question_pdf_is_generated_without_master_template(monkeypatch):
    monkeypatch.setattr(pdf_module, "READING_MASTER", Path("/missing/READING_MASTER.pdf"))
    payload = render_reading_pdf(_sample_exam())
    reader = PdfReader(BytesIO(payload))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert payload.startswith(b"%PDF-")
    assert reader.pages
    assert "Complete the sentence" in extracted
    assert "Choose the correct answer" in extracted


def test_listening_question_pdf_is_generated_without_master_template(monkeypatch):
    monkeypatch.setattr(pdf_module, "LISTENING_MASTER", Path("/missing/LISTENING_MASTER.pdf"))
    payload = render_listening_pdf(_sample_exam())
    reader = PdfReader(BytesIO(payload))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert payload.startswith(b"%PDF-")
    assert reader.pages
    assert "A passage with real test content" in extracted


def test_writing_question_pdf_is_generated_without_master_template(monkeypatch):
    monkeypatch.setattr(pdf_module, "WRITING_MASTER", Path("/missing/WRITING_MASTER.pdf"))
    payload = render_writing_pdf({
        "title": "Steply Writing test", "cefr_level": "B2", "duration_minutes": 60,
        "tasks": [{
            "part_number": 1, "sub_part": 1, "context_text": "You are applying for a course.",
            "instruction": "Write an email to the course coordinator.", "topic": "Explain your reasons.",
            "format": {"min_words": 120, "max_words": 180},
        }, {
            "part_number": 2, "context_text": "Some people prefer remote work.",
            "instruction": "Discuss the advantages and disadvantages.", "topic": "Give examples.",
            "format": {"min_words": 250, "max_words": 300},
        }],
    })
    reader = PdfReader(BytesIO(payload))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert payload.startswith(b"%PDF-")
    assert "course coordinator" in extracted
    assert "advantages and disadvantages" in extracted


def test_answer_sheet_uses_packaged_template_when_deployment_template_is_missing():
    from app.modules.exams.mock.pdf_service import AnswerSheetPDFService

    payload = AnswerSheetPDFService().generate_answer_sheet(
        "Oʻktam Gʻaniyev", "MK-123456-001", "2026-10-16", "Mock Exam", "MK-123456-001"
    )
    reader = PdfReader(BytesIO(payload))
    assert payload.startswith(b"%PDF-")
    assert len(reader.pages) == 1


def test_writing_result_pdf_handles_uzbek_unicode():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import asyncio
    import importlib.util
    source = Path(__file__).resolve().parents[1] / "app" / "modules" / "tests" / "writing" / "services" / "pdf_service.py"
    spec = importlib.util.spec_from_file_location("steply_writing_pdf_service_test", source)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    PDFService = module.PDFService

    result = SimpleNamespace(
        id=101,
        user_id="candidate-1",
        exam_id="writing-1",
        user=SimpleNamespace(profile=SimpleNamespace(full_name="Oʻktam Gʻaniyev", username="oktam")),
        exam=SimpleNamespace(title="Writing — Oʻzbekcha topshiriq"),
        created_at=datetime.now(timezone.utc),
        is_finalized=True,
        raw_score=5.5,
        scaled_score=6.0,
        cefr_level="B2",
        answers=[],
    )
    payload = asyncio.run(PDFService().generate_pdf_report(result))
    reader = PdfReader(BytesIO(payload))
    assert payload.startswith(b"%PDF-")
    assert reader.pages


def test_shared_answer_options_are_rendered_once_per_part():
    payload = render_reading_pdf({
        "title": "Shared answer bank test",
        "parts": [{
            "part_number": 2,
            "title": "Choose from the options",
            "questions": [
                {"question_number": 1, "type": "MULTIPLE_CHOICE", "text": "First question", "options": [
                    {"label": "A", "value": "Option Alpha"},
                    {"label": "B", "value": "Option Beta"},
                ]},
                {"question_number": 2, "type": "MULTIPLE_CHOICE", "text": "Second question", "options": [
                    {"label": "A", "value": "Option Alpha"},
                    {"label": "B", "value": "Option Beta"},
                ]},
            ],
        }],
    })
    reader = PdfReader(BytesIO(payload))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert extracted.count("Option Alpha") == 1
    assert extracted.count("Option Beta") == 1
    assert "First question" in extracted
    assert "Second question" in extracted


def test_writing_pdf_feedback_expands_structured_ai_analysis():
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace
    source = Path(__file__).resolve().parents[1] / "app" / "modules" / "tests" / "writing" / "services" / "pdf_service.py"
    spec = importlib.util.spec_from_file_location("steply_writing_feedback_pdf_test", source)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    service = module.PDFService.__new__(module.PDFService)
    answer = SimpleNamespace(feedbacks=[SimpleNamespace(
        source="AI",
        content='{"version":2,"summary":"Yaxshi tuzilgan matn.","rubric_results":{"english_band":{"band_score":7,"comments":{"grammar":"Few errors."}}},"errors":[{"original":"he go","correction":"he goes","explanation":"Third-person singular."}],"corrected_text":"He goes to school.","word_count":120,"word_penalty":0}',
    )])
    rendered = service._pick_feedback(answer)
    assert "Yaxshi tuzilgan matn." in rendered
    assert "English band: 7/9" in rendered
    assert "he goes" in rendered
    assert "He goes to school." in rendered
