import json
import stat
import zipfile
from pathlib import Path

import pytest

from backend.app.services.parser.errors import MinerUResultError
from backend.app.services.parser.result_archive import read_mineru_result_archive


def _write_zip(path: Path, members: dict[str, bytes], *, symlink: str | None = None) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
        if symlink is not None:
            info = zipfile.ZipInfo(symlink)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"target")


def test_reads_one_markdown_and_content_list(tmp_path: Path) -> None:
    archive_path = tmp_path / "result.zip"
    content_list = [{"type": "text", "text": "张三"}]
    _write_zip(
        archive_path,
        {
            "resume/auto/resume.md": "# 张三\n外贸经历".encode(),
            "resume/auto/resume_content_list_v2.json": json.dumps(
                [content_list], ensure_ascii=False
            ).encode(),
        },
    )

    result = read_mineru_result_archive(
        archive_path,
        max_members=10,
        max_uncompressed_bytes=1024 * 1024,
        max_compression_ratio=100,
    )

    assert result.markdown == "# 张三\n外贸经历"
    assert result.content_list == content_list
    assert result.member_count == 2
    assert result.uncompressed_bytes > 0


def test_prefers_stable_flat_content_list_v1(tmp_path: Path) -> None:
    archive_path = tmp_path / "result.zip"
    flat = [{"type": "text", "text": "stable"}]
    _write_zip(
        archive_path,
        {
            "full.md": b"# Resume",
            "resume_content_list.json": json.dumps(flat).encode(),
            "resume_content_list_v2.json": json.dumps([[{"type": "paragraph"}]]).encode(),
        },
    )

    result = read_mineru_result_archive(
        archive_path,
        max_members=10,
        max_uncompressed_bytes=1024 * 1024,
        max_compression_ratio=100,
    )

    assert result.content_list == flat


@pytest.mark.parametrize(
    "member",
    ["../escape.md", "/absolute.md", "C:/drive.md", "folder\\..\\escape.md"],
)
def test_rejects_unsafe_member_paths(tmp_path: Path, member: str) -> None:
    archive_path = tmp_path / "result.zip"
    _write_zip(archive_path, {member: b"# x"})

    with pytest.raises(MinerUResultError, match="unsafe ZIP member"):
        read_mineru_result_archive(
            archive_path,
            max_members=10,
            max_uncompressed_bytes=1024,
            max_compression_ratio=100,
        )


def test_rejects_symlink_member(tmp_path: Path) -> None:
    archive_path = tmp_path / "result.zip"
    _write_zip(archive_path, {"resume.md": b"# ok"}, symlink="linked.md")

    with pytest.raises(MinerUResultError, match="special ZIP member"):
        read_mineru_result_archive(
            archive_path,
            max_members=10,
            max_uncompressed_bytes=1024,
            max_compression_ratio=100,
        )


def test_rejects_duplicate_or_missing_markdown(tmp_path: Path) -> None:
    two = tmp_path / "two.zip"
    _write_zip(two, {"one.md": b"one", "two.md": b"two"})
    with pytest.raises(MinerUResultError, match="exactly one Markdown"):
        read_mineru_result_archive(
            two, max_members=10, max_uncompressed_bytes=1024, max_compression_ratio=100
        )

    none = tmp_path / "none.zip"
    _write_zip(none, {"result.json": b"{}"})
    with pytest.raises(MinerUResultError, match="exactly one Markdown"):
        read_mineru_result_archive(
            none, max_members=10, max_uncompressed_bytes=1024, max_compression_ratio=100
        )


def test_rejects_member_count_size_ratio_and_malformed_content_list(tmp_path: Path) -> None:
    many = tmp_path / "many.zip"
    _write_zip(many, {"resume.md": b"ok", "extra.txt": b"x"})
    with pytest.raises(MinerUResultError, match="too many members"):
        read_mineru_result_archive(
            many, max_members=1, max_uncompressed_bytes=1024, max_compression_ratio=100
        )

    large = tmp_path / "large.zip"
    _write_zip(large, {"resume.md": b"x" * 2048})
    with pytest.raises(MinerUResultError, match="uncompressed size"):
        read_mineru_result_archive(
            large, max_members=10, max_uncompressed_bytes=1024, max_compression_ratio=100
        )
    with pytest.raises(MinerUResultError, match="compression ratio"):
        read_mineru_result_archive(
            large, max_members=10, max_uncompressed_bytes=4096, max_compression_ratio=2
        )

    malformed = tmp_path / "malformed.zip"
    _write_zip(
        malformed,
        {"resume.md": b"ok", "resume_content_list_v2.json": b"not-json"},
    )
    with pytest.raises(MinerUResultError, match="content list"):
        read_mineru_result_archive(
            malformed,
            max_members=10,
            max_uncompressed_bytes=1024,
            max_compression_ratio=100,
        )
