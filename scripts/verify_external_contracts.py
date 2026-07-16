from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER_PARTS = ("example.com", "your-newapi", "sk-your-key")
REQUIRED = (
    "MINERU_BASE_URL",
    "MINERU_API_KEY",
    "NEWAPI_BASE_URL",
    "NEWAPI_API_KEY",
    "LLM_MODEL_EXTRACT",
    "LLM_MODEL_EXTRACT_FALLBACK",
    "LLM_MODEL_JUDGE",
    "LLM_MODEL_JUDGE_FALLBACK",
)


def configuration_errors(environ: Mapping[str, str]) -> list[str]:
    errors: list[str] = []
    for key in REQUIRED:
        value = environ.get(key, "").strip()
        if not value:
            errors.append(f"{key} is required")
        elif any(part in value.casefold() for part in PLACEHOLDER_PARTS):
            errors.append(f"{key} still contains a placeholder/test value")
        elif value in {
            "test-extract",
            "test-extract-fallback",
            "test-judge",
            "test-judge-fallback",
            "sk-test",
        }:
            errors.append(f"{key} still contains a placeholder/test value")
    if environ.get("MINERU_MODE", "").strip().casefold() != "official":
        errors.append("MINERU_MODE must be official")
    return errors


def main() -> int:
    load_dotenv(REPO_ROOT / ".env", override=False)
    load_dotenv(REPO_ROOT / ".env.local", override=True)
    errors = configuration_errors(os.environ)
    if errors:
        print("external contract configuration is incomplete:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env["SMARTSCREEN_EXTERNAL_CONTRACT"] = "1"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "backend/tests/external",
        "-m",
        "external_contract",
        "-q",
        "-rs",
    ]
    try:
        subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"external contract verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
