# pgvector + SQLAlchemy Integration — Research Notes

**Task:** P1 Task 0.3 — confirm the correct Python package, SQLAlchemy column type, Alembic migration form, and the HNSW-vs-IVFFlat decision for SmartScreenAgent's candidate embedding store. This drives Task 9 (`CandidateEmbedding` model with `Vector(1024)`).

**Researched on:** 2026-05-12

## TL;DR

- **Pip package:** `pgvector` (single name covers SQLAlchemy, Django, psycopg adapters). Source: [pgvector-python README](https://github.com/pgvector/pgvector-python).
- **PostgreSQL version:** **PG 13+** supported by the extension; our `docker-compose.yml` pins `pgvector/pgvector:pg16`, which is well within range. Source: [pgvector README](https://github.com/pgvector/pgvector).
- **SQLAlchemy import:** `from pgvector.sqlalchemy import Vector` (the canonical type, also exported as the uppercase alias `VECTOR` following SQLAlchemy's uppercase-type convention). Source: [pgvector-python README — SQLAlchemy section](https://github.com/pgvector/pgvector-python#sqlalchemy).
- **Alembic extension enable:** `op.execute('CREATE EXTENSION IF NOT EXISTS vector')` in `upgrade()`. Source: [pgvector-python README](https://github.com/pgvector/pgvector-python).
- **Decision: HNSW** over IVFFlat for our workload — better recall/QPS at our small-to-medium dataset size and no need for a "train after data lands" step. See §4 below.
- **Embedding dimension: 1024** — aligned with `bge-m3` / `qwen3-embedding-0.6B`, both of which natively emit 1024-dim vectors and have strong Chinese performance. The exact embedding model is **TBD** (see §5).

---

## 1. Install

### Pip package

```bash
pip install pgvector
```

The same `pgvector` PyPI package ships the SQLAlchemy, Django, psycopg2, psycopg3, asyncpg, and Peewee adapters under separate submodules (e.g., `pgvector.sqlalchemy`, `pgvector.asyncpg`). Source: [pgvector-python README](https://github.com/pgvector/pgvector-python).

For async SQLAlchemy (which SmartScreenAgent uses, per the FastAPI+async stack), pair it with `asyncpg`:

```bash
pip install "pgvector[async]"   # optional extras name varies; plain `pip install pgvector asyncpg sqlalchemy[asyncio]` also works
```

> Note: `pgvector` is a **pure-Python adapter** — it does not bundle the PostgreSQL C extension. The C extension must be installed on the database server (we get this automatically from the `pgvector/pgvector:pg16` Docker image).

### Minimum versions

| Component | Minimum | SmartScreenAgent target | Source |
|---|---|---|---|
| PostgreSQL | **13** | 16 (via `pgvector/pgvector:pg16`) | [pgvector README — Installation](https://github.com/pgvector/pgvector#installation) |
| pgvector extension (server side) | 0.5.0+ recommended (HNSW added in 0.5.0) | latest on the `pg16` image | [pgvector README — HNSW](https://github.com/pgvector/pgvector#hnsw) |
| Python `pgvector` package | latest from PyPI | latest | [pgvector-python README](https://github.com/pgvector/pgvector-python) |
| SQLAlchemy | 2.x recommended for `Mapped[]` typing; 1.4 supported via the same adapter | 2.x | [pgvector-python README — SQLAlchemy](https://github.com/pgvector/pgvector-python#sqlalchemy) |

### Max dimensions (capacity check)

- `vector` type supports up to **16,000 dimensions** in storage. Indexes are limited to **2,000 dimensions** for plain `vector`, **4,000** for `halfvec`, and up to **64,000** for binary-quantized vectors. Source: [pgvector README — Reference](https://github.com/pgvector/pgvector#reference).
- We need 1024 — well below all limits, and well below the 2,000-dim indexable cap.

## 2. SQLAlchemy 2.x Model Example

```python
# app/models/candidate_embedding.py
from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector  # `VECTOR` also works (uppercase alias)
from sqlalchemy import ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CandidateEmbedding(Base):
    __tablename__ = "candidate_embeddings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 1024-dim embedding from a Chinese embedding model (see §5).
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)

    # Provenance for re-embedding when the model is swapped.
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_model_version: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    __table_args__ = (
        # HNSW with cosine distance — see §4 for rationale.
        Index(
            "ix_candidate_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
```

Key points (sourced from [pgvector-python README — SQLAlchemy](https://github.com/pgvector/pgvector-python#sqlalchemy)):

- `Vector(N)` declares an N-dimensional column. Pass `None` (i.e. `Vector()`) if you want a dimensionally-untyped column — but **always pin a dimension** in production because most index ops require it.
- The Python value type is `list[float]` (or `numpy.ndarray` if you want zero-copy with numpy — pgvector-python handles both).
- Similarity queries use methods on the column:
  - `Candidate.embedding.cosine_distance(query_vec)` → ordered ascending for "most similar".
  - `Candidate.embedding.l2_distance(query_vec)`.
  - `Candidate.embedding.max_inner_product(query_vec)`.
  - Source: [pgvector-python README — Querying](https://github.com/pgvector/pgvector-python#querying).

Async usage requires registering the `vector` type on each asyncpg connection. With SQLAlchemy's async engine the recommended approach is to wire it through the `connect` event hook (per the [pgvector-python asyncpg section](https://github.com/pgvector/pgvector-python#asyncpg)):

```python
from sqlalchemy.ext.asyncio import create_async_engine
from pgvector.asyncpg import register_vector

engine = create_async_engine(DATABASE_URL)

@event.listens_for(engine.sync_engine, "connect")
def _register_vector(dbapi_conn, _):
    # asyncpg connection — call register_vector inside its event loop.
    dbapi_conn.run_async(lambda c: register_vector(c))
```

(This wiring detail is the most common footgun — without it, raw asyncpg returns vectors as strings.)

## 3. Alembic Migration Snippet

A single migration that enables the extension, adds the column, and creates the index:

```python
# alembic/versions/0009_add_candidate_embedding.py
"""add candidate_embeddings with pgvector"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import UUID

revision = "0009_add_candidate_embedding"
down_revision = "0008_..."
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1) Enable the extension. Idempotent — safe to keep in re-runs.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2) Table with the vector column.
    op.create_table(
        "candidate_embeddings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("embedding_model", sa.String(128), nullable=False),
        sa.Column("embedding_model_version", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_candidate_embeddings_candidate_id",
        "candidate_embeddings",
        ["candidate_id"],
    )

    # 3) HNSW index for cosine similarity. Build it AFTER the table exists.
    #    Use a raw SQL execute because Alembic's create_index does not pass
    #    `postgresql_with` cleanly for HNSW parameters in every release.
    op.execute(
        """
        CREATE INDEX ix_candidate_embeddings_embedding_hnsw
        ON candidate_embeddings
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_embeddings_embedding_hnsw", table_name="candidate_embeddings")
    op.drop_index("ix_candidate_embeddings_candidate_id", table_name="candidate_embeddings")
    op.drop_table("candidate_embeddings")
    # Do NOT drop the extension in downgrade — other tables may use it.
    # op.execute("DROP EXTENSION IF EXISTS vector")
```

Notes & sources:

- `op.execute('CREATE EXTENSION IF NOT EXISTS vector')` is the canonical form. Source: [pgvector-python README — Migrations](https://github.com/pgvector/pgvector-python#migrations) and the pgvector C extension's [`CREATE EXTENSION` line](https://github.com/pgvector/pgvector#installation-notes).
- The `op.create_index(..., postgresql_using="hnsw", postgresql_with={...}, postgresql_ops={...})` form **does** work for HNSW in current Alembic + SQLAlchemy ([pgvector-python README — Indexing](https://github.com/pgvector/pgvector-python#indexing) shows the SQLAlchemy `Index(...)` syntax that Alembic auto-generates). The raw `op.execute` is shown above only because it makes the resulting DDL obvious and easy to verify against the pgvector docs.
- **Never drop the extension in `downgrade()`** unless this migration is the one that originally created it AND nothing else depends on it — a `DROP EXTENSION` cascades into every dependent column.
- The extension's permission model needs superuser to `CREATE EXTENSION` on first install (default in the `pgvector/pgvector:pg16` image). After install, regular users can use the type without elevated rights. Source: [pgvector README — Installation Notes](https://github.com/pgvector/pgvector#installation-notes).

## 4. Decision: HNSW vs IVFFlat

**Decision: use HNSW with `vector_cosine_ops`.** Parameters: `m=16, ef_construction=64` (the pgvector defaults). At query time, set `hnsw.ef_search = 40` initially and tune up if recall is insufficient.

### Side-by-side (sourced from [pgvector README — HNSW](https://github.com/pgvector/pgvector#hnsw) and [pgvector README — IVFFlat](https://github.com/pgvector/pgvector#ivfflat))

| Dimension | HNSW | IVFFlat |
|---|---|---|
| Build time | Slower | Faster |
| Memory footprint | Larger (graph layers) | Smaller |
| Query speed @ same recall | Better | Worse |
| Requires data to exist before `CREATE INDEX` | **No** | **Yes** — `lists` is tied to row count via k-means training; building on an empty table or wrong-sized table degrades recall |
| Recall tuning knob | `hnsw.ef_search` (per-query SET) | `ivfflat.probes` (per-query SET) |
| Build knobs | `m`, `ef_construction` | `lists` |
| Recommended `lists` formula (IVFFlat only) | n/a | `rows/1000` up to 1M rows, `sqrt(rows)` above 1M |
| Added in pgvector | 0.5.0 | 0.4.0 |

### Why HNSW wins for SmartScreenAgent

The plan describes the embedding store as **~1000–10000 candidates** with **cosine similarity** queries for cross-position recommendation. Against that profile:

1. **Data arrives incrementally.** Candidates are screened over time; we cannot wait until the table is full to build the index. HNSW does not require k-means training on existing rows, so we can create the index up-front on an empty table and let it grow naturally. IVFFlat would force us to either (a) build with a bad `lists` value initially and rebuild later, or (b) defer the index until enough rows exist — both add operational complexity. Source: [pgvector README — IVFFlat](https://github.com/pgvector/pgvector#ivfflat) ("Before adding any rows, an IVFFlat index can't be created … For best recall, create the index after the table has some data.").
2. **Build time is irrelevant at our scale.** 10k × 1024-dim vectors is roughly 40 MB of raw data; HNSW build on that volume completes in seconds-to-tens-of-seconds. The "HNSW is slower to build" disadvantage only matters at millions of rows.
3. **Query latency matters for UX.** Cross-position recommendation runs synchronously during HR workflows; we want sub-100ms p95 even with light query optimization. HNSW has a strictly better speed-recall curve. Source: [pgvector README — HNSW](https://github.com/pgvector/pgvector#hnsw) ("HNSW has better query performance than IVFFlat (in terms of speed-recall tradeoff)").
4. **Memory cost is acceptable.** Even at the upper bound (10k × 1024 × 4 bytes = 40 MB raw, with HNSW overhead typically 2-3×, call it ~120 MB), this fits comfortably in PostgreSQL's `shared_buffers` on any reasonable production host.
5. **Tuning surface is simple.** `SET hnsw.ef_search = 40;` (or higher) at the start of a session/transaction is enough to dial recall. Source: [pgvector README — HNSW Query Options](https://github.com/pgvector/pgvector#hnsw-1).

**Re-evaluate IVFFlat only if** the candidate corpus grows past ~1M rows AND HNSW index size becomes a memory bottleneck. At that point, switching is a matter of changing one migration.

### Distance function: cosine

Cross-position recommendation compares the *direction* of candidate skill/experience vectors, not their magnitude (resumes vary wildly in length, which affects L2 distance but not cosine). Use `vector_cosine_ops` and `cosine_distance(...)` for queries. Source: [pgvector README — Vector Operators](https://github.com/pgvector/pgvector#vector-functions).

## 5. Embedding Dimension Choice — Why 1024

The schema fixes `Vector(1024)`. 1024 is chosen because it aligns with the two leading **open-source Chinese-capable** embedding models — both natively emit 1024-dimensional vectors, so we avoid lossy dimension-reduction (Matryoshka truncation) at the storage layer.

### Candidate models

| Model | Native dim | Why considered | Source |
|---|---|---|---|
| **BAAI `bge-m3`** | **1024** | Multilingual (100+ languages incl. Chinese), supports up to 8192-token inputs, ships dense + sparse + ColBERT outputs (we only need dense). Strong MTEB-zh scores. | [BAAI/bge-m3 model card](https://huggingface.co/BAAI/bge-m3) |
| **Qwen `qwen3-embedding-0.6B`** | **1024** (Matryoshka-truncatable to 32–1024) | Qwen3 family, small/fast (0.6B params), top-tier on MTEB-Multilingual when released. Truncatable, but full 1024 is recommended. | [Qwen/Qwen3-Embedding-0.6B model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) |
| OpenAI `text-embedding-3-large` | 3072 (truncatable down to 1024 via the `dimensions` parameter) | Hosted, OpenAI-compatible (works through newapi). Strong English; weaker on Chinese vs. open-source SOTA at the same dimension. | [OpenAI Embeddings docs](https://platform.openai.com/docs/guides/embeddings) |
| OpenAI `text-embedding-3-small` | 1536 (truncatable to 512–1536) | Cheaper, dimensions don't natively align to 1024. | [OpenAI Embeddings docs](https://platform.openai.com/docs/guides/embeddings) |

### Why 1024 specifically

- **Native dim of the strongest open Chinese embedding models** — using their natural output avoids the recall hit from Matryoshka truncation (small but measurable on bge-m3 / Qwen3).
- **Safely within pgvector's 2,000-dim HNSW index cap** ([pgvector README — Reference](https://github.com/pgvector/pgvector#reference)), with comfortable headroom.
- **Manageable storage** — 1024 × 4 bytes + 8-byte header ≈ 4 KB per row. Even 10k candidates is ~40 MB raw + index overhead.
- **Lets us swap between bge-m3 and Qwen3 without a schema change.** Re-embedding existing rows requires only a backfill job that overwrites the `embedding` column (and bumps `embedding_model` / `embedding_model_version` columns from §2).

### TBD — pick the actual model

The plan does **not yet** specify which embedding model to deploy. Recommendation for the implementer of the embedding service:

1. Default to **`bge-m3`** (self-hosted via `infinity-emb` or `text-embeddings-inference`). Most flexible licensing (MIT), best documented multilingual behavior, and a known-good fit at 1024-dim.
2. Fallback: **`Qwen3-Embedding-0.6B`** if benchmarks on a sample of real resumes show meaningfully better recall on Chinese-language CVs.
3. Avoid `text-embedding-3-large` truncated-to-1024 unless we explicitly want a hosted (no GPU) path — the recall gap on Chinese is measurable.

The `embedding_model` and `embedding_model_version` columns in §2's schema exist precisely so we can reembed when this TBD lands.

---

## Sources (consolidated)

- [pgvector-python GitHub README](https://github.com/pgvector/pgvector-python) — pip package, SQLAlchemy import (`from pgvector.sqlalchemy import Vector`/`VECTOR`), `mapped_column(Vector(N))` example, Alembic `op.execute('CREATE EXTENSION IF NOT EXISTS vector')`, HNSW + IVFFlat SQLAlchemy index syntax, asyncpg registration.
- [pgvector GitHub README](https://github.com/pgvector/pgvector) — PG 13+ requirement, PG 13–18 Docker images, dimension caps (16,000 storage / 2,000 index for `vector`), HNSW vs IVFFlat tradeoffs and parameter defaults, `vector_cosine_ops` / `vector_l2_ops` / `vector_ip_ops` operator classes, `lists` formula for IVFFlat.
- [pgvector README — HNSW section](https://github.com/pgvector/pgvector#hnsw) — better speed-recall tradeoff, `m=16, ef_construction=64` defaults, `hnsw.ef_search` query knob.
- [pgvector README — IVFFlat section](https://github.com/pgvector/pgvector#ivfflat) — requires data before index creation, `lists` and `probes` tuning.
- [BAAI/bge-m3 model card](https://huggingface.co/BAAI/bge-m3) — 1024-dim native, multilingual, 8192-token context.
- [Qwen/Qwen3-Embedding-0.6B model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) — 1024-dim native (Matryoshka-truncatable).
- [OpenAI Embeddings guide](https://platform.openai.com/docs/guides/embeddings) — `text-embedding-3-large` 3072-dim default and `dimensions` truncation parameter.

## Open Questions / TBDs

1. **Which embedding model do we actually deploy?** Recommendation in §5 is `bge-m3`, but this needs an A/B benchmark on a real resume sample before code-freeze. Tracked as a decision for the embedding-service task.
2. **Should we also store `halfvec(1024)` alongside `vector(1024)`** for memory savings as the corpus grows? Defer — premature optimization for <10k rows.
3. **`hnsw.ef_search` default** — start at 40, raise if recall measurements come back below ~0.95. Coordinate with whoever benchmarks the recommendation endpoint in Task 9+.
