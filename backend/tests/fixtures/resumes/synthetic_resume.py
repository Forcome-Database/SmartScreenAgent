from __future__ import annotations

# ruff: noqa: E501 -- literal Office Open XML namespace/content-type values.
import base64
import io
import zipfile
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent


def build_synthetic_resume(path: Path) -> Path:
    suffix = path.suffix.casefold()
    if suffix == ".pdf":
        path.write_bytes(_build_pdf())
    elif suffix == ".docx":
        path.write_bytes(_build_docx())
    elif suffix in {".png", ".jpg", ".jpeg"}:
        encoded = (FIXTURE_DIR / f"synthetic{suffix}.b64").read_text(encoding="ascii")
        path.write_bytes(base64.b64decode(encoded))
    else:
        raise ValueError(f"unsupported synthetic resume suffix: {suffix}")
    return path


def _build_pdf() -> bytes:
    stream = (
        b"BT /F1 18 Tf 72 720 Td (SYNTHETIC RESUME) Tj "
        b"0 -30 Td /F1 12 Tf (EXPORT EXPERIENCE 2020-2026) Tj ET"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode())
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    return bytes(output)


def _build_docx() -> bytes:
    document = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>SYNTHETIC RESUME</w:t></w:r></w:p>
    <w:p><w:r><w:t>EXPORT EXPERIENCE 2020-2026</w:t></w:r></w:p>
    <w:sectPr/>
  </w:body>
</w:document>"""
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
    relationships = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()
