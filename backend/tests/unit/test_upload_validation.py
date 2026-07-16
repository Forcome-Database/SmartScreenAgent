import io
import tempfile
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfWriter
from starlette.datastructures import Headers, UploadFile

from backend.app.services.upload.errors import UploadValidationError
from backend.app.services.upload.validation import UploadValidator


def _upload(name: str, body: bytes, content_type: str) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(body),
        filename=name,
        headers=Headers({"content-type": content_type}),
    )


def _pdf(*, encrypted: bool = False) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    if encrypted:
        writer.encrypt("secret")
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def _docx() -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
    return stream.getvalue()


@pytest.mark.parametrize(
    "kwargs",
    [{"max_bytes": 0}, {"max_bytes": -1}, {"chunk_bytes": 0}, {"chunk_bytes": -1}],
)
def test_validator_rejects_nonpositive_limits(kwargs):
    with pytest.raises(ValueError, match="must be positive"):
        UploadValidator(**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "body", "declared", "canonical"),
    [
        ("resume.pdf", _pdf(), "application/pdf", "application/pdf"),
        (
            "resume.docx",
            _docx(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("resume.png", b"\x89PNG\r\n\x1a\nbody", "image/png", "image/png"),
        ("resume.jpg", b"\xff\xd8\xffbody\xff\xd9", "image/jpeg", "image/jpeg"),
        ("resume.pdf", _pdf(), "application/octet-stream", "application/pdf"),
    ],
)
async def test_supported_upload_is_streamed_hashed_and_typed(
    name, body, declared, canonical
):
    artifact = await UploadValidator(max_bytes=1024 * 1024, chunk_bytes=7).validate(
        _upload(name, body, declared)
    )
    try:
        assert artifact.original_filename == name
        assert artifact.size_bytes == len(body)
        assert len(artifact.sha256) == 64
        assert artifact.content_type == canonical
        assert artifact.path.read_bytes() == body
        assert artifact.path.suffix == Path(name).suffix
    finally:
        artifact.cleanup()
    assert not artifact.path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "body", "declared", "status", "code"),
    [
        ("", b"x", "application/octet-stream", 400, "invalid_upload"),
        ("resume.pdf", b"", "application/pdf", 400, "invalid_upload"),
        ("resume.exe", b"MZ", "application/octet-stream", 415, "unsupported_media_type"),
        ("resume.doc", b"\xd0\xcf\x11\xe0", "application/msword", 415, "unsupported_media_type"),
        ("resume.pdf", b"not-pdf", "application/pdf", 415, "unsupported_media_type"),
        ("resume.pdf", _pdf(), "image/png", 415, "unsupported_media_type"),
        ("resume.pdf", b"%PDF-broken", "application/pdf", 422, "invalid_document"),
        ("resume.pdf", _pdf(encrypted=True), "application/pdf", 422, "invalid_document"),
        (
            "resume.docx",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1encrypted",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            422,
            "invalid_document",
        ),
        (
            "resume.docx",
            b"PK\x03\x04broken",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            422,
            "invalid_document",
        ),
        ("resume.jpg", b"\xff\xd8\xffbody", "image/jpeg", 422, "invalid_document"),
    ],
)
async def test_invalid_upload_has_stable_error(name, body, declared, status, code):
    before = set(Path(tempfile.gettempdir()).glob("smartscreen-upload-*"))
    with pytest.raises(UploadValidationError) as exc_info:
        await UploadValidator(max_bytes=1024 * 1024, chunk_bytes=5).validate(
            _upload(name, body, declared)
        )
    assert exc_info.value.status_code == status
    assert exc_info.value.code == code
    after = set(Path(tempfile.gettempdir()).glob("smartscreen-upload-*"))
    assert after == before


@pytest.mark.asyncio
async def test_upload_over_limit_stops_and_cleans_up():
    with pytest.raises(UploadValidationError) as exc_info:
        await UploadValidator(max_bytes=5, chunk_bytes=2).validate(
            _upload("resume.png", b"\x89PNG\r\n\x1a\nbody", "image/png")
        )
    assert exc_info.value.status_code == 413
    assert exc_info.value.code == "file_too_large"


@pytest.mark.asyncio
async def test_upload_at_exact_limit_is_allowed():
    body = b"\x89PNG\r\n\x1a\nbody"
    artifact = await UploadValidator(max_bytes=len(body), chunk_bytes=3).validate(
        _upload("resume.png", body, "image/png")
    )
    try:
        assert artifact.size_bytes == len(body)
    finally:
        artifact.cleanup()
