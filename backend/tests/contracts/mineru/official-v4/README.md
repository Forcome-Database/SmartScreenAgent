# Official MinerU API v4 contract fixtures

These fixtures are sanitized shapes derived from the official MinerU API v4
documentation for `file-urls/batch` and `extract-results/batch/{batch_id}`.
They contain no credentials, signed query strings, provider bodies, or PII.

`runtime-2026-07-16.json` records the sanitized four-format external probe. It
contains only endpoint/API/model labels, allowlisted host names, pass counts, and
flow-stage outcomes. Batch IDs, data IDs, signed URLs, trace IDs, response bodies,
and credentials are deliberately excluded.
