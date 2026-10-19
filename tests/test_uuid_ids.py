import uuid

from app.core.id_generator import (
    generate_listening_id,
    generate_mock_exam_id,
    generate_reading_id,
    generate_speaking_id,
)


def test_generated_exam_ids_are_uuid_v4_strings():
    generators = (
        generate_mock_exam_id,
        generate_reading_id,
        generate_listening_id,
        generate_speaking_id,
    )
    for generate in generators:
        value = generate()
        parsed = uuid.UUID(value)
        assert parsed.version == 4
        assert str(parsed) == value


def test_generated_exam_ids_are_unique():
    generators = (generate_reading_id, generate_listening_id, generate_speaking_id)
    values = [generate() for generate in generators for _ in range(10)]
    assert len(values) == len(set(values))
