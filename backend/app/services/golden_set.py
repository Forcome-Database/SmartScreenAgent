# backend/app/services/golden_set.py
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import JD, Candidate, GoldenSet, User
from backend.app.services.read.pagination import Page

VALID_LABELS = ("advance", "reject", "borderline")
_REQUIRED_COLUMNS = {"candidate_id", "jd_code", "label"}


class InvalidCSV(Exception):
    """Raised when the upload is not CSV or lacks the required header."""


class GoldenImportTooLarge(Exception):
    """Raised when a single import exceeds the configured row cap."""


@dataclass(frozen=True)
class ParsedRow:
    row: int
    candidate_id: int
    jd_code: str
    label: str


@dataclass(frozen=True)
class RowError:
    row: int
    candidate_id: int | None
    jd_code: str | None
    reason: str


def parse_golden_csv(content: bytes, *, max_rows: int) -> tuple[list[ParsedRow], list[RowError]]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    header = {(name or "").strip() for name in (reader.fieldnames or [])}
    if not _REQUIRED_COLUMNS.issubset(header):
        raise InvalidCSV()
    parsed: list[ParsedRow] = []
    errors: list[RowError] = []
    for i, raw in enumerate(reader, start=1):
        if i > max_rows:
            raise GoldenImportTooLarge()
        cid_text = (raw.get("candidate_id") or "").strip()
        jd_code = (raw.get("jd_code") or "").strip()
        label = (raw.get("label") or "").strip()
        try:
            cid = int(cid_text)
        except ValueError:
            errors.append(RowError(i, None, jd_code or None, "invalid_candidate_id"))
            continue
        if label not in VALID_LABELS:
            errors.append(RowError(i, cid, jd_code or None, "invalid_label"))
            continue
        if not jd_code:
            errors.append(RowError(i, cid, None, "missing_jd_code"))
            continue
        parsed.append(ParsedRow(i, cid, jd_code, label))
    return parsed, errors


async def import_golden_set(
    db: AsyncSession, *, parsed: list[ParsedRow], importer_id: int
) -> tuple[int, int, list[RowError]]:
    if not parsed:
        return 0, 0, []
    jd_codes = {r.jd_code for r in parsed}
    jd_rows = (await db.execute(select(JD.code, JD.id).where(JD.code.in_(jd_codes)))).all()
    jd_map: dict[str, int] = {code: jd_id for code, jd_id in jd_rows}
    cand_ids = {r.candidate_id for r in parsed}
    known_cands = set(
        (await db.execute(select(Candidate.id).where(Candidate.id.in_(cand_ids)))).scalars().all()
    )
    # keys that already exist, so we can count created vs updated
    resolved_keys = [
        (r.candidate_id, jd_map[r.jd_code])
        for r in parsed
        if r.jd_code in jd_map and r.candidate_id in known_cands
    ]
    existing: set[tuple[int, int]] = set()
    if resolved_keys:
        existing_rows = (
            await db.execute(
                select(GoldenSet.candidate_id, GoldenSet.jd_id).where(
                    tuple_(GoldenSet.candidate_id, GoldenSet.jd_id).in_(resolved_keys)
                )
            )
        ).all()
        existing = {(cand_id, jd_id) for cand_id, jd_id in existing_rows}
    created = updated = 0
    errors: list[RowError] = []
    seen: set[tuple[int, int]] = set()
    for r in parsed:
        jd_id = jd_map.get(r.jd_code)
        if jd_id is None:
            errors.append(RowError(r.row, r.candidate_id, r.jd_code, "unknown_jd_code"))
            continue
        if r.candidate_id not in known_cands:
            errors.append(RowError(r.row, r.candidate_id, r.jd_code, "unknown_candidate"))
            continue
        key = (r.candidate_id, jd_id)
        if key in existing or key in seen:
            updated += 1
        else:
            created += 1
        seen.add(key)
        await db.execute(
            pg_insert(GoldenSet)
            .values(
                candidate_id=r.candidate_id,
                jd_id=jd_id,
                label=r.label,
                imported_at=func.now(),
                imported_by_user_id=importer_id,
            )
            .on_conflict_do_update(
                constraint="uq_golden_set_cand_jd",
                set_={
                    "label": r.label,
                    "imported_at": func.now(),
                    "imported_by_user_id": importer_id,
                },
            )
        )
    await db.commit()
    return created, updated, errors


async def list_golden_set(
    db: AsyncSession, *, jd_code: str | None, page: Page
) -> tuple[list[tuple[GoldenSet, str, str]], int]:
    base = (
        select(GoldenSet, JD.code, User.display_name)
        .join(JD, JD.id == GoldenSet.jd_id)
        .join(User, User.id == GoldenSet.imported_by_user_id)
    )
    if jd_code is not None:
        base = base.where(JD.code == jd_code)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await db.execute(
            base.order_by(GoldenSet.imported_at.desc(), GoldenSet.id.desc())
            .offset(page.offset)
            .limit(page.page_size)
        )
    ).all()
    return [(g, code, name) for g, code, name in rows], total
