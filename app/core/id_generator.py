"""UUID identifiers used for newly created exam entities.

Existing prefixed IDs remain valid for backward compatibility; newly generated IDs
are UUID v4 strings and therefore fit the existing VARCHAR primary-key columns.
"""
import uuid


def _uuid() -> str:
    return str(uuid.uuid4())


def generate_mock_exam_id() -> str:
    return _uuid()


def generate_reading_id() -> str:
    return _uuid()


def generate_listening_id() -> str:
    return _uuid()


def generate_speaking_id() -> str:
    return _uuid()
