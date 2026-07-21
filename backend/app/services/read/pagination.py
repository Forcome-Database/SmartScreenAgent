from __future__ import annotations

from dataclasses import dataclass

from fastapi import Query
from pydantic import BaseModel

from backend.app.config import get_settings


@dataclass(frozen=True)
class Page:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def resolve_page(page: int | None, page_size: int | None) -> Page:
    settings = get_settings()
    resolved_page = max(1, page or 1)
    size = page_size if page_size is not None else settings.READ_PAGE_SIZE_DEFAULT
    resolved_size = max(1, min(size, settings.READ_PAGE_SIZE_MAX))
    return Page(page=resolved_page, page_size=resolved_size)


def page_params(
    page: int = Query(1, ge=1),
    page_size: int | None = Query(None, ge=1),
) -> Page:
    return resolve_page(page, page_size)


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
