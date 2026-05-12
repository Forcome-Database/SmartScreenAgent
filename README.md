# SmartScreenAgent

AI-driven resume screening agent for HR.

## Quick start

```bash
uv sync
cp .env.example .env  # edit values
docker compose up -d  # start PG / Redis / MinIO
uv run alembic upgrade head
uv run uvicorn backend.app.main:app --reload
```

See `docs/specs/2026-05-12-resume-screening-agent-design.md` for design.
