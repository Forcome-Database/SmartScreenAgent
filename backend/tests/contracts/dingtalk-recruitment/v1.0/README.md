# DingTalk recruitment contract fixtures — NOT RECORDED FROM A REAL RESPONSE

Read this before trusting anything in this directory.

Unlike `../../mineru/` and `../../newapi/`, these files were **not** captured
from a live response and **not** derived from an authoritative specification.
Every path, parameter, and field name here is transcribed from
`docs/specs/2026-05-12-resume-screening-agent-design.md` §8.2, which names
`GET /v1.0/recruitment/candidates` but — unlike its OAuth section — cites no
`oas-ref:` for it. A read of the project's DingTalk OAS on 2026-07-27 found no
`recruitment` namespace at all, and the recruitment API permission has not been
granted, so no live probe could be run.

They are therefore **executable assumptions, not evidence**. Their whole job is
to pin our reading of the documentation so that when the real endpoint is
finally reachable, a mismatch fails loudly in one place instead of drifting
silently in production.

| File | Shape it pins |
|---|---|
| `candidates-page.json` | A normal page: `hasMore`, `nextCursor`, `list[]` of `candidateId`/`updateTime`/`jobCode`/`resume{fileName,fileType,downloadUrl}` |
| `candidates-empty.json` | An empty result is not an error |
| `candidates-malformed.json` | A row missing `candidateId` must raise, never yield `None` |
| `jobs-page.json` | A normal JD metadata page: `list[]` of `jobCode`/`name`/`description`, for WP8 §10 JD sync |

`hasMore` and `nextCursor` are recorded but deliberately unused: the adapter does
not page (WP8 §9.1 caps a run at `SYNC_MAX_ITEMS_PER_RUN` and the cursor picks up
the remainder on the next run).

## When the permission is granted

Run `backend/tests/external/test_dingtalk_recruitment_contract.py`
(`-m external_contract`, `DINGTALK_PROBE_ACCESS_TOKEN=...`). If the real shape
differs, change `backend/app/services/sync/dingtalk.py` and these fixtures
together — no other file in the codebase knows these endpoints exist.

The names in the fixtures are synthetic. Never re-record a fixture here from a
real response without stripping the candidate's name, contact details, signed
download URL, and any access token.
