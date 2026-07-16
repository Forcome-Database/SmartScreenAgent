import zipfile

from pypdf import PdfReader

from backend.tests.fixtures.resumes.synthetic_resume import build_synthetic_resume


def test_synthetic_resume_factory_builds_all_supported_formats(tmp_path) -> None:
    paths = [build_synthetic_resume(tmp_path / f"resume{suffix}") for suffix in (
        ".pdf",
        ".docx",
        ".png",
        ".jpg",
    )]

    assert "SYNTHETIC RESUME" in (PdfReader(paths[0]).pages[0].extract_text() or "")
    with zipfile.ZipFile(paths[1]) as archive:
        document = archive.read("word/document.xml")
        assert b"SYNTHETIC RESUME" in document
    assert paths[2].read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert paths[3].read_bytes().startswith(b"\xff\xd8\xff")
    assert all(path.stat().st_size > 100 for path in paths)
