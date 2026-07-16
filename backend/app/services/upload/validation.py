from __future__ import annotations

import hashlib
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from backend.app.config import get_settings
from backend.app.services.upload.errors import UploadValidationError

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PNG = "image/png"
JPEG = "image/jpeg"
GENERIC = "application/octet-stream"

_CANONICAL_BY_SUFFIX = {
    ".pdf": PDF,
    ".docx": DOCX,
    ".png": PNG,
    ".jpg": JPEG,
    ".jpeg": JPEG,
}
_OLE_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


@dataclass(frozen=True)
class UploadArtifact:
    path: Path
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str

    def cleanup(self) -> None:
        self.path.unlink(missing_ok=True)


class UploadValidator:
    def __init__(
        self,
        *,
        max_bytes: int | None = None,
        chunk_bytes: int | None = None,
    ) -> None:
        settings = get_settings()
        self.max_bytes = (
            settings.MAX_RESUME_FILE_BYTES if max_bytes is None else max_bytes
        )
        self.chunk_bytes = settings.UPLOAD_CHUNK_BYTES if chunk_bytes is None else chunk_bytes
        if self.max_bytes <= 0 or self.chunk_bytes <= 0:
            raise ValueError("upload size and chunk settings must be positive")

    async def validate(self, upload: UploadFile) -> UploadArtifact:
        filename = upload.filename or ""
        suffix = Path(filename).suffix.lower()
        if not filename.strip():
            raise self._invalid_upload("A filename is required")
        canonical = _CANONICAL_BY_SUFFIX.get(suffix)
        if canonical is None:
            raise self._unsupported()

        path: Path | None = None
        try:
            hasher = hashlib.sha256()
            size = 0
            with tempfile.NamedTemporaryFile(
                prefix="smartscreen-upload-",
                suffix=suffix,
                delete=False,
            ) as temporary:
                path = Path(temporary.name)
                while chunk := await upload.read(self.chunk_bytes):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise UploadValidationError(
                            status_code=413,
                            code="file_too_large",
                            message="Resume file exceeds the configured size limit",
                        )
                    hasher.update(chunk)
                    temporary.write(chunk)

            if size == 0:
                raise self._invalid_upload("Resume file is empty")
            self._validate_content(path, suffix, canonical, upload.content_type)
            return UploadArtifact(
                path=path,
                original_filename=Path(filename).name,
                content_type=canonical,
                size_bytes=size,
                sha256=hasher.hexdigest(),
            )
        except Exception:
            if path is not None:
                path.unlink(missing_ok=True)
            raise

    def _validate_content(
        self,
        path: Path,
        suffix: str,
        canonical: str,
        declared_content_type: str | None,
    ) -> None:
        declared = (declared_content_type or GENERIC).split(";", 1)[0].strip().lower()
        if declared not in {GENERIC, canonical}:
            raise self._unsupported()

        with path.open("rb") as stream:
            head = stream.read(16)
            stream.seek(max(0, path.stat().st_size - 16))
            tail = stream.read(16)

        if suffix == ".pdf":
            if not head.startswith(b"%PDF-"):
                raise self._unsupported()
            self._validate_pdf(path)
            return
        if suffix == ".docx":
            if head.startswith(_OLE_SIGNATURE):
                raise self._invalid_document("Password-protected DOCX files are not supported")
            if not head.startswith(b"PK"):
                raise self._unsupported()
            self._validate_docx(path)
            return
        if suffix == ".png":
            if not head.startswith(b"\x89PNG\r\n\x1a\n"):
                raise self._unsupported()
            return
        if suffix in {".jpg", ".jpeg"}:
            if not head.startswith(b"\xff\xd8\xff"):
                raise self._unsupported()
            if not tail.endswith(b"\xff\xd9"):
                raise self._invalid_document("JPEG file is incomplete or corrupt")
            return
        raise self._unsupported()

    def _validate_pdf(self, path: Path) -> None:
        try:
            reader = PdfReader(path, strict=False)
            if reader.is_encrypted:
                raise self._invalid_document("Password-protected PDF files are not supported")
            len(reader.pages)
        except UploadValidationError:
            raise
        except (PdfReadError, OSError, ValueError) as exc:
            raise self._invalid_document("PDF file is corrupt") from exc

    def _validate_docx(self, path: Path) -> None:
        try:
            with zipfile.ZipFile(path) as archive:
                names = frozenset(archive.namelist())
        except (OSError, zipfile.BadZipFile) as exc:
            raise self._invalid_document("DOCX file is corrupt") from exc
        if not {"[Content_Types].xml", "word/document.xml"} <= names:
            raise self._invalid_document("DOCX file is corrupt")

    @staticmethod
    def _invalid_upload(message: str) -> UploadValidationError:
        return UploadValidationError(
            status_code=400,
            code="invalid_upload",
            message=message,
        )

    @staticmethod
    def _unsupported() -> UploadValidationError:
        return UploadValidationError(
            status_code=415,
            code="unsupported_media_type",
            message="Unsupported resume file type",
        )

    @staticmethod
    def _invalid_document(message: str) -> UploadValidationError:
        return UploadValidationError(
            status_code=422,
            code="invalid_document",
            message=message,
        )
