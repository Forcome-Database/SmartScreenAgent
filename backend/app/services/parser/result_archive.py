from __future__ import annotations

import json
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from backend.app.services.parser.errors import MinerUResultError

_DRIVE_PATH = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class MinerUArchiveResult:
    markdown: str
    content_list: list[dict[str, Any]] | None
    member_count: int
    uncompressed_bytes: int


def _normalized_member_name(name: str) -> str:
    if not name or "\x00" in name:
        raise MinerUResultError("unsafe ZIP member path")
    normalized = name.replace("\\", "/")
    if normalized.startswith("/") or _DRIVE_PATH.match(normalized):
        raise MinerUResultError("unsafe ZIP member path")
    path = PurePosixPath(normalized)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise MinerUResultError("unsafe ZIP member path")
    return str(path)


def _validate_member_type(info: zipfile.ZipInfo) -> None:
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise MinerUResultError("special ZIP member is not allowed")
    if info.flag_bits & 0x1:
        raise MinerUResultError("encrypted ZIP member is not allowed")


def _read_utf8(archive: zipfile.ZipFile, info: zipfile.ZipInfo, label: str) -> str:
    try:
        return archive.read(info).decode("utf-8")
    except (OSError, RuntimeError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise MinerUResultError(f"invalid {label} in MinerU result archive") from exc


def read_mineru_result_archive(
    path: Path,
    *,
    max_members: int,
    max_uncompressed_bytes: int,
    max_compression_ratio: float,
) -> MinerUArchiveResult:
    if max_members <= 0 or max_uncompressed_bytes <= 0 or max_compression_ratio <= 0:
        raise ValueError("MinerU result archive limits must be positive")

    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > max_members:
                raise MinerUResultError("MinerU result archive has too many members")

            names: set[str] = set()
            markdown_infos: list[zipfile.ZipInfo] = []
            content_infos: list[tuple[str, zipfile.ZipInfo]] = []
            total_uncompressed = 0
            for info in infos:
                normalized = _normalized_member_name(info.filename)
                key = normalized.casefold()
                if key in names:
                    raise MinerUResultError("duplicate ZIP member name")
                names.add(key)
                _validate_member_type(info)
                if info.is_dir():
                    continue

                total_uncompressed += info.file_size
                if total_uncompressed > max_uncompressed_bytes:
                    raise MinerUResultError("MinerU result uncompressed size exceeds limit")
                if info.file_size:
                    if info.compress_size <= 0:
                        raise MinerUResultError("MinerU result compression ratio exceeds limit")
                    if info.file_size / info.compress_size > max_compression_ratio:
                        raise MinerUResultError("MinerU result compression ratio exceeds limit")

                lower = normalized.casefold()
                if lower.endswith(".md"):
                    markdown_infos.append(info)
                elif lower.endswith("_content_list_v2.json"):
                    content_infos.append(("v2", info))
                elif lower.endswith("_content_list.json"):
                    content_infos.append(("v1", info))

            if len(markdown_infos) != 1:
                raise MinerUResultError("MinerU result must contain exactly one Markdown file")
            markdown = _read_utf8(archive, markdown_infos[0], "Markdown").strip()
            if not markdown:
                raise MinerUResultError("MinerU result Markdown is empty")

            content_list: list[dict[str, Any]] | None = None
            if content_infos:
                version, preferred = sorted(content_infos, key=lambda item: item[0])[0]
                raw_content = _read_utf8(archive, preferred, "content list")
                try:
                    parsed = json.loads(raw_content)
                except json.JSONDecodeError as exc:
                    raise MinerUResultError(
                        "invalid content list in MinerU result archive"
                    ) from exc
                if version == "v1":
                    if not isinstance(parsed, list) or not all(
                        isinstance(item, dict) for item in parsed
                    ):
                        raise MinerUResultError("invalid content list in MinerU result archive")
                    content_list = parsed
                else:
                    if not isinstance(parsed, list) or not all(
                        isinstance(page, list) and all(isinstance(item, dict) for item in page)
                        for page in parsed
                    ):
                        raise MinerUResultError("invalid content list in MinerU result archive")
                    content_list = [item for page in parsed for item in page]

            return MinerUArchiveResult(
                markdown=markdown,
                content_list=content_list,
                member_count=len(infos),
                uncompressed_bytes=total_uncompressed,
            )
    except MinerUResultError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise MinerUResultError("invalid MinerU result ZIP") from exc
