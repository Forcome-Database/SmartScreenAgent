# PII Column Encryption — pgcrypto vs Application-level Fernet

**Task:** P1 Task 0.4 — Decide how to encrypt `candidates.name/phone/email` (and any future PII columns).
This drives Task 6 (`Candidate` model) which will import the chosen helpers from
`backend/app/security/crypto.py`.

**Researched on:** 2026-05-12

**Note on filename:** Per the plan, the file is named `pgcrypto.md` even though the conclusion
favours application-level Fernet — the filename is the research topic, not the verdict.

## TL;DR — DECISION

> **Use application-level Fernet (`cryptography.fernet`).** Reject `pgcrypto`'s
> `pgp_sym_encrypt/decrypt`.

**Killer reason:** with `pgcrypto`, the passphrase is a literal SQL parameter passed in
every encrypt/decrypt call. It is therefore observable in `pg_stat_activity`,
`log_statement=all` audit trails, `pg_stat_statements` (normalized but still leaky in
parameter form), error logs, and any DB proxy / replication stream. The PostgreSQL docs
explicitly call this out: *"all the data and passwords move between pgcrypto and client
applications in clear text … if you cannot [trust system + DBA], then better do crypto
inside client application."* Source: [PostgreSQL docs F.26.8.3 — Security Limitations](https://www.postgresql.org/docs/current/pgcrypto.html).

Spec §11.1 originally proposed `pgcrypto`; this research **contests** that and the design
spec should be updated accordingly. The plan (Task 6) already anticipates this revision.

---

## 1. Comparison Table

| # | Dimension | `pgcrypto` (`pgp_sym_encrypt`) | App-level Fernet (`cryptography.fernet`) |
|---|---|---|---|
| 1 | **Where the key lives at runtime** | Passed as a literal `text` parameter on **every** SQL call. Lives transiently in client memory, on the wire, and in the server's per-statement memory. ([PG docs F.26.7](https://www.postgresql.org/docs/current/pgcrypto.html)) | Loaded once into Python process memory from env var; never touches the DB or its logs. ([Fernet docs](https://cryptography.io/en/latest/fernet/)) |
| 2 | **Risk of key leaking into PG logs** | **High.** Visible in `pg_stat_activity.query`, `log_statement=all`, `auto_explain`, error messages from constraint violations, replication WAL when statement logging is on, and any DB-side trace tools. PG docs warn the value is sent "in clear text" between client and server. ([PG docs F.26.8.3](https://www.postgresql.org/docs/current/pgcrypto.html)) | **Zero.** The DB only ever sees ciphertext bytes; the key is never serialized to SQL. |
| 3 | **Search / index support on encrypted column** | None usable in practice — `pgp_sym_encrypt` is non-deterministic (random IV + salt per `s2k-mode=3`), so equality search would require decrypting every row. ([PG docs F.26.7, s2k-mode default](https://www.postgresql.org/docs/current/pgcrypto.html)) | None — Fernet is also non-deterministic (random IV per call). ([Fernet docs — "Implementation"](https://cryptography.io/en/latest/fernet/)) For dedup/lookup we use a **separate `phone_hash`** column (SHA-256, see §3 code). |
| 4 | **Performance per row** | Slower: defaults to 65536–253952 S2K iterations (PBKDF-like) per call by design, on top of AES-128. ([PG docs F.26.7 — s2k-count default](https://www.postgresql.org/docs/current/pgcrypto.html)) Adds DB CPU on the hot path. | Faster: AES-128-CBC + HMAC-SHA-256, no per-call KDF (the 32-byte key is used directly). ([Fernet spec](https://github.com/fernet/spec/blob/master/Spec.md)) Encrypt/decrypt is microseconds at our row sizes. |
| 5 | **Key rotation complexity** | Hard: must re-encrypt every row with a new passphrase in a single transaction, *and* the new passphrase appears in every UPDATE statement. No standard helper. | Easy: `MultiFernet([new, old])` — encryption always uses the first key, decryption tries each in turn. `.rotate(token)` re-emits a token under the new key on read. ([Fernet docs — MultiFernet](https://cryptography.io/en/latest/fernet/#cryptography.fernet.MultiFernet)) |
| 6 | **Backup safety (encrypted at rest in `pg_dump`)** | Yes — column stores `bytea` ciphertext, dumps preserve it. | Yes — column stores ASCII Fernet token (URL-safe base64), dumps preserve it. ([Fernet docs](https://cryptography.io/en/latest/fernet/)) |
| 7 | **Implementation complexity in our stack (SQLAlchemy 2.x async)** | Requires `func.pgp_sym_encrypt(...)` / `func.pgp_sym_decrypt(...)` wrapped in every query, or a custom `TypeDecorator` that emits raw SQL expressions — awkward with async session and Alembic. Forces the DB to do the crypto on every read. | Clean: a `TypeDecorator(impl=Text)` with `process_bind_param`/`process_result_value` is purely Python and runs identically under sync and async SQLAlchemy. ([SQLAlchemy 2.0 — Custom Types](https://docs.sqlalchemy.org/en/20/core/custom_types.html)) Established pattern in the ecosystem (e.g. `sqlalchemy-utils` `EncryptedType`, `advanced-alchemy` `EncryptedString`). |

---

## 2. Detailed pgcrypto Notes

From [PostgreSQL docs — `pgcrypto`](https://www.postgresql.org/docs/current/pgcrypto.html):

- **Signatures:**
  ```sql
  pgp_sym_encrypt(data text, psw text [, options text]) returns bytea
  pgp_sym_decrypt(msg bytea, psw text [, options text]) returns text
  ```
- **Key passing:** the second argument is a literal SQL text parameter. There is no
  built-in mechanism to source it from a session variable, GUC, or external secret —
  the application must inline it into every query.
- **Algorithm options:** `cipher-algo` defaults to `aes128`; AES-192/256, Blowfish,
  CAST5, 3DES are available. `s2k-mode=3` (default) means random iteration count
  65536–253952 — deliberate KDF slowness.
- **No side-channel resistance:** docs explicitly warn that decryption timing
  varies by ciphertext (Section F.26.8.3).
- **Security limitation (verbatim, F.26.8.3):**
  > "All `pgcrypto` functions run inside the database server. That means that all
  > the data and passwords move between `pgcrypto` and client applications in clear
  > text. Thus you must: 1. Connect locally or use SSL connections. 2. Trust both
  > system and database administrator. If you cannot, then better do crypto inside
  > client application."

  This is PostgreSQL itself recommending the Fernet-style approach for our threat
  model (we run RDS-like managed Postgres in the future; we should not have to
  trust every DBA tail-ing logs).

## 3. Code Skeleton — `backend/app/security/crypto.py`

Production-ready. Task 6 can copy this verbatim.

```python
"""PII encryption helpers for SmartScreenAgent.

Symmetric encryption uses Fernet (AES-128-CBC + HMAC-SHA-256) from the
`cryptography` library. The key is loaded once at import time from the
`SSA_PII_KEY` environment variable; rotation is handled via SSA_PII_KEY_OLD
(comma-separated list of retired keys still valid for decryption).

Refs:
- https://cryptography.io/en/latest/fernet/
- https://github.com/fernet/spec/blob/master/Spec.md
"""

from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

_KEY_ENV = "SSA_PII_KEY"
_OLD_KEYS_ENV = "SSA_PII_KEY_OLD"  # comma-separated, optional

# Fernet keys are url-safe base64 of exactly 32 bytes -> 44 ASCII chars ending with '='.
_FERNET_KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{43}=$")


def _validate_key(key: str) -> bytes:
    """Fail fast if the key is missing or malformed."""
    if not key:
        raise RuntimeError(
            f"{_KEY_ENV} is not set. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    if not _FERNET_KEY_RE.match(key):
        raise RuntimeError(
            f"{_KEY_ENV} is not a valid Fernet key "
            "(must be 32 random bytes, url-safe base64-encoded, 44 chars)."
        )
    return key.encode("ascii")


@lru_cache(maxsize=1)
def _cipher() -> MultiFernet:
    """Build a MultiFernet with the current key first, then retired keys.

    Encryption always uses the first (current) key; decryption tries each in
    turn. This lets us rotate keys without a stop-the-world re-encrypt.
    """
    current = _validate_key(os.environ.get(_KEY_ENV, ""))
    olds_raw = os.environ.get(_OLD_KEYS_ENV, "").strip()
    old_keys = [_validate_key(k.strip()) for k in olds_raw.split(",") if k.strip()]
    fernets = [Fernet(current), *[Fernet(k) for k in old_keys]]
    return MultiFernet(fernets)


def encrypt_pii(plaintext: str) -> str:
    """Encrypt a PII string. Returns ASCII Fernet token (safe for TEXT column).

    Empty string is encrypted normally (the ciphertext is still random and
    authenticated). `None` is the caller's responsibility — the SQLAlchemy
    TypeDecorator should short-circuit None before calling this.
    """
    if not isinstance(plaintext, str):
        raise TypeError(f"encrypt_pii expects str, got {type(plaintext).__name__}")
    token = _cipher().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_pii(ciphertext: str) -> str:
    """Decrypt a Fernet token. Raises ValueError on tamper / wrong key / malformed."""
    if not isinstance(ciphertext, str):
        raise TypeError(f"decrypt_pii expects str, got {type(ciphertext).__name__}")
    try:
        plaintext = _cipher().decrypt(ciphertext.encode("ascii"))
    except InvalidToken as exc:
        raise ValueError("PII ciphertext is invalid, tampered, or under an unknown key") from exc
    return plaintext.decode("utf-8")


def hash_pii(phone: str | None, name: str | None) -> str:
    """Deterministic SHA-256 hex digest for dedup / equality lookup.

    Uses a domain-separation tag so this hash is never confused with a generic
    SHA-256 elsewhere. Normalizes inputs (strip + lowercase) to make the hash
    stable across the same logical person.

    Note: this hash is NOT keyed — it's a dedup index, not a secret. If we
    later need keyed dedup (e.g. blind index), switch to HMAC-SHA-256 with a
    separate `SSA_PII_INDEX_KEY`.
    """
    norm_phone = re.sub(r"\D", "", phone or "")
    norm_name = (name or "").strip().lower()
    payload = f"ssa-pii-v1|phone={norm_phone}|name={norm_name}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

### Suggested SQLAlchemy `TypeDecorator` wrapper

(Belongs in the same module so Task 6 can write `Mapped[str] = mapped_column(EncryptedPII)`.)

```python
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

class EncryptedPII(TypeDecorator):  # type: ignore[type-arg]
    """Transparent Fernet encryption for a TEXT column."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: D401
        if value is None:
            return None
        return encrypt_pii(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return decrypt_pii(value)
```

The encrypt/decrypt path is pure Python with no I/O, so it runs identically in async
sessions ([SQLAlchemy 2.0 Custom Types](https://docs.sqlalchemy.org/en/20/core/custom_types.html)).

## 4. Key Management Plan

### Loading

- Production: `SSA_PII_KEY` is injected via environment (Docker secret / K8s secret /
  `.env` file outside the repo). Never committed.
- The module fails fast at first use if the key is missing or malformed (see
  `_validate_key`) — better to crash on boot than silently encrypt with `None`.

### Generating a key

The exact one-liner (also embedded in the error message):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Output is a 44-character URL-safe base64 string ending with `=`. Paste into the
project's secret store under the key `SSA_PII_KEY`.

### Rotation procedure

1. Generate new key, set `SSA_PII_KEY=<new>` and `SSA_PII_KEY_OLD=<previous>`.
2. Deploy. Reads decrypt under either key (`MultiFernet` tries the list); writes
   re-encrypt under the new key.
3. (Optional) Run a one-off job that reads-and-rewrites every PII row to migrate
   cold data to the new key — `MultiFernet.rotate(token)` does this per-row.
4. After the migration job finishes, unset `SSA_PII_KEY_OLD`.

Ref: [MultiFernet docs](https://cryptography.io/en/latest/fernet/#cryptography.fernet.MultiFernet).

### Future: migration to KMS (high-level, not implemented now)

When we move to a managed cloud, replace `_cipher()` with an envelope-encryption
flow: fetch a **Data Encryption Key (DEK)** from AWS KMS / GCP KMS / HashiCorp Vault
on process start (or per-tenant with a cache), Fernet-wrap it, and keep the
**Key Encryption Key (KEK)** in the KMS. The `encrypt_pii` / `decrypt_pii` public
signatures stay the same — only the internals of `_cipher()` change. This keeps
Task 6 and downstream callers stable across the migration.

Pattern reference: [Envelope Encryption for SQLAlchemy Fields](https://devhuddle.ai/envelope-encryption-for-sqlalchemy-fields/).

## 5. Test Strategy

Tests to live in `backend/tests/security/test_crypto.py`.

| # | Test | What it proves |
|---|---|---|
| 1 | `round_trip`: encrypt → decrypt returns the original string (incl. Chinese, emoji, long strings) | Basic correctness; UTF-8 boundaries OK |
| 2 | `empty_string`: `encrypt_pii("")` returns a non-empty token; `decrypt_pii(...)` returns `""` | Edge case — empty inputs still authenticated |
| 3 | `non_determinism`: two encryptions of the same plaintext produce different tokens | Random IV is in use; rules out "same ciphertext leaks plaintext equality" |
| 4 | `wrong_key`: decrypting a token under a fresh key raises `ValueError` (not `InvalidToken`) | API contract — callers catch a stdlib exception, not a `cryptography` one |
| 5 | `tampered_token`: flip one base64 char in a token → `decrypt_pii` raises `ValueError` | HMAC integrity check works |
| 6 | `malformed_token`: `decrypt_pii("not-a-token")` raises `ValueError` | No crash on garbage input |
| 7 | `missing_key`: importing/using module with `SSA_PII_KEY` unset → `RuntimeError` with the one-liner in the message | Fail-fast UX |
| 8 | `invalid_key_format`: `SSA_PII_KEY="short"` → `RuntimeError` mentioning Fernet format | Fail-fast on misconfigured deploy |
| 9 | `key_rotation`: encrypt with key A, set `SSA_PII_KEY=B`, `SSA_PII_KEY_OLD=A` (clear `lru_cache`), decrypt succeeds; new encrypt uses B | Rotation works without data migration |
| 10 | `hash_pii_stable`: same inputs in different casings / with formatting → same hash | Dedup index does what we expect |
| 11 | `hash_pii_distinct`: different phones → different hashes (collision smoke test on 100 random inputs) | Hash is doing something |
| 12 | `typedecorator_round_trip` (integration, with SQLite): insert a row through ORM, read it back, ciphertext on disk ≠ plaintext, ORM-level value == plaintext | End-to-end TypeDecorator wiring |

For test 9, expose a `_reset_cipher_cache()` helper that calls `_cipher.cache_clear()` —
only used in tests, marked with a docstring saying so.

---

## Sources

- [PostgreSQL — F.26 pgcrypto](https://www.postgresql.org/docs/current/pgcrypto.html) — function signatures, S2K defaults, security warnings (F.26.8.3).
- [cryptography.io — Fernet](https://cryptography.io/en/latest/fernet/) — algorithm, API, MultiFernet, InvalidToken.
- [Fernet spec](https://github.com/fernet/spec/blob/master/Spec.md) — exact bit layout (version | timestamp | IV | ciphertext | HMAC).
- [SQLAlchemy 2.0 — Custom Types / TypeDecorator](https://docs.sqlalchemy.org/en/20/core/custom_types.html) — `process_bind_param` / `process_result_value` contract.
- [Miguel Grinberg — Encryption at Rest with SQLAlchemy](https://blog.miguelgrinberg.com/post/encryption-at-rest-with-sqlalchemy) — TypeDecorator + Fernet reference pattern.
- [advanced-alchemy — EncryptedString source](https://docs.advanced-alchemy.litestar.dev/latest/_modules/advanced_alchemy/types/encrypted_string.html) — production example of the same pattern.
- [DevHuddle — Envelope Encryption for SQLAlchemy Fields](https://devhuddle.ai/envelope-encryption-for-sqlalchemy-fields/) — future KMS migration pattern.
