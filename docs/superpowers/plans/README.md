# SmartScreenAgent Delivery Plan Index

This directory tracks active delivery plans. Current scope and dependency decisions come from [`../specs/2026-07-13-current-state-and-roadmap-design.md`](../specs/2026-07-13-current-state-and-roadmap-design.md).

## Status Values

- **Ready:** specification is approved and an executable implementation plan exists.
- **Ready for planning:** predecessor gates passed, but the package specification or implementation plan is not yet approved.
- **Blocked:** work package depends on an incomplete predecessor.
- **In progress:** implementation has started and plan checkboxes are being maintained.
- **Complete:** exit gate passed and the roadmap traceability matrix was updated.

Historical plans may contain unchecked boxes even when their work was committed. They are marked with historical-status banners and are not tracked here.

## Ordered Work Packages

| Order | Package | Status | Depends on | Plan |
|---:|---|---|---|---|
| 0 | WP0 Reproducible integration baseline | Complete | Current repository | [`2026-07-13-wp0-integration-baseline.md`](2026-07-13-wp0-integration-baseline.md) |
| 1 | WP1 Security and raw-file integrity | Complete | WP0 | Approved [specification](../specs/2026-07-16-wp1-security-and-raw-file-integrity-design.md) and [completion evidence](2026-07-16-wp1-security-and-raw-file-integrity.md#completion-evidence) |
| 2 | WP2 Production parser contract and validated AI output | Complete | WP1 | Approved [specification](../specs/2026-07-16-wp2-production-parser-and-validated-ai-output-design.md) and [completion evidence](2026-07-16-wp2-production-parser-and-validated-ai-output.md#completion-evidence-2026-07-20) |
| 3 | WP3 Durable asynchronous ingestion and batch processing | Complete | WP2 | [`2026-07-20-wp3-durable-async-ingestion.md`](2026-07-20-wp3-durable-async-ingestion.md) |
| 4 | WP4 Read APIs and rule lifecycle | Ready for planning | WP1 and WP3 | Written after WP3 API-state review |
| 5 | WP5 HR web workspace | Blocked | WP4 | Written after WP4 OpenAPI approval |
| 6 | WP6 Review, golden set, and rule simulation | Blocked | WP4 and WP5 | Written after the HR golden path is usable |
| 7 | WP7 Cost, calibration, and operational reporting | Blocked | WP3 and WP6 | Written after labeled feedback exists |
| 8 | WP8 DingTalk recruitment sync and MCP/Hermes | Blocked | WP3 and WP4 | Written after stable application services exist |
| 9 | WP9 JD intelligence and cross-position recommendation | Blocked | WP6 and WP7 | Written after offline evaluation data exists |

## Current Evidence

On 2026-07-13, the strict local WP0 gate passed 102 non-integration tests and 16 integration tests with zero skips, plus static, migration, and clean-state checks. Hosted [GitHub Actions run 29237545679](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29237545679) then passed both Python matrix jobs and the integration job at commit `b3447ec`. Exact results are recorded in the [WP0 completion evidence](2026-07-13-wp0-integration-baseline.md#completion-evidence).

On 2026-07-16, the approved WP1 implementation passed the local strict gate: 142 non-integration tests and 36 integration tests with zero skips, Alembic head `b57c2f9e1a6d`, Ruff, mypy, real PostgreSQL/Redis/Celery/MinIO checks, and post-run clean-state checks. The implementation was split into scoped commits, hosted [GitHub Actions run 29474031067](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29474031067) passed Python 3.10, Python 3.14, and strict integration, and the configured deployment database upgraded with zero candidate and legacy rows requiring disposition. WP1 is Complete and WP2 is Ready for planning; exact evidence is in the [WP1 plan](2026-07-16-wp1-security-and-raw-file-integrity.md#completion-evidence).

On 2026-07-20, WP2 completed. Offline suite 233 passed with Ruff and mypy clean on Python 3.10 and 3.14; the local Windows/Python 3.14 external-contract gate passed 9/9 (MinerU official API v4 four formats and new-api `gpt-5.6-sol` structured output). Hosted [GitHub Actions run 29714208508](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29714208508) passed both Python matrix jobs and strict integration at commit `57f9afe` (WP2 range `2145b74..57f9afe`, PR #2). WP2 is Complete and WP3 is Ready for planning; exact evidence is in the [WP2 plan](2026-07-16-wp2-production-parser-and-validated-ai-output.md#completion-evidence-2026-07-20).

On 2026-07-21, WP3 completed. The durable async ingestion path (state machine, `IngestionJobService`, worker orchestration, idempotent score upsert, Beat sweeper with stranded-`queued` recovery, async upload/batch/status endpoints) passed the local gate: 252 offline and 64 integration tests (real PostgreSQL/Redis/MinIO/worker, including end-to-end and crash-recovery), Ruff, mypy, and the Alembic round trip to head `2f27938b430b`. Hosted [GitHub Actions run 29795950194](https://github.com/Forcome-Database/SmartScreenAgent/actions/runs/29795950194) passed Python 3.10, Python 3.14, and strict integration at commit `4bd7130` (WP3 range `5c57cab..4bd7130`, PR #3). WP3 is Complete and WP4 is Ready for planning.

## Planning Rules

1. Create one feature specification and one implementation plan per work package.
2. Do not write detailed plans for blocked packages; later contracts must reflect the implementation decisions of their predecessors.
3. Update the active plan's checkboxes during execution.
4. On completion, record verification evidence in the plan and update the authoritative roadmap matrix.
5. A skipped integration suite does not satisfy a package exit gate.

## Dependency View

```text
WP0 -> WP1 -> WP2 -> WP3 -> WP4 -> WP5 -> WP6 -> WP7
                         |      |
                         +----> WP8

WP6 + WP7 -> WP9
```

WP4 design may overlap the end of WP3, and WP5 visual design may overlap the end of WP4. Implementation cannot cross an incomplete dependency gate.
