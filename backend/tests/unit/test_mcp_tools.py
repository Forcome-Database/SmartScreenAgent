from __future__ import annotations

import re

from backend.app.mcp.tools import SCORE_SUMMARY_SQL, project_dimensions


def test_a_dimension_projection_keeps_only_comparable_fields() -> None:
    persisted = {
        "dimensions": [
            {
                "id": "independence",
                "tier": "high",
                "score": 10,
                "confidence": 0.9,
                "evidence_quotes": ["quotable evidence"],
                "reasoning": "private reasoning",
                "suggested_interview_questions": ["a question"],
            }
        ]
    }

    (projected,) = project_dimensions(persisted)

    assert set(projected) == {"id", "tier", "score"}
    rendered = str(projected)
    assert "quotable" not in rendered
    assert "reasoning" not in rendered


def test_a_missing_dimension_payload_projects_to_nothing() -> None:
    assert project_dimensions(None) == []
    assert project_dimensions({}) == []


def test_a_malformed_entry_is_dropped_rather_than_partially_copied() -> None:
    assert project_dimensions({"dimensions": ["not-an-object", 3]}) == []


def test_the_score_projection_never_names_a_private_column_or_key() -> None:
    """Layer 1 of design §11.3, checked against the statement itself.

    `score_summary` folds the judge payload in PostgreSQL, so the quotes and
    the reasoning never cross the wire into this process. The proof that they
    are *not read* — rather than read and then stripped — is that the only
    statement the tool issues cannot name them.
    """
    sql = str(SCORE_SUMMARY_SQL)

    for private in (
        "evidence_quotes",
        "reasoning",
        "confidence",
        "suggested_interview_questions",
        "rule_dimensions",
        "name_cipher",
        "phone_cipher",
        "email_cipher",
        "extracted_json",
        "raw_file_key",
        "candidates",
    ):
        assert private not in sql, f"{private} must never be selected"

    # The three judge fields it does name, and nothing else from that payload.
    assert "'id', d.value ->> 'id'" in sql
    assert "'tier', d.value ->> 'tier'" in sql
    assert "'score', d.value -> 'score'" in sql


def test_the_score_projection_reads_exactly_three_judge_fields() -> None:
    """The whitelist the blacklist above cannot express.

    Naming forbidden keys catches a widening that says `reasoning`. It cannot
    catch one that selects `s.judge_dimensions` wholesale, or that emits
    `d.value` itself under some new key — neither names anything blacklistable,
    and `judge_dimensions` cannot go on the blacklist because the statement
    legitimately reads it. Either widening would then be clamped back by
    `project_dimensions`, so the integration field-set assertion would pass too
    and the quotes would be inside this process undetected.

    So enumerate instead every read the statement makes out of the judge
    payload and require the list to be exactly this one. A fourth extraction,
    whatever it is called, fails here.
    """
    sql = str(SCORE_SUMMARY_SQL)

    # A wildcard is the one widening both guards miss. `SELECT s.*` names
    # nothing on the blacklist above, never writes `s.judge_dimensions` so the
    # extraction counts below are unchanged, and is invisible to the
    # integration field-set assertions because `score_summary` builds its
    # output dict from explicit keys — yet every evidence quote would be inside
    # this process.
    assert re.search(r"\bs\s*\.\s*\*", sql) is None, "a wildcard would select the quotes"

    # Every mention of the judge column, with whatever key it extracts. A bare
    # `s.judge_dimensions` — the wholesale select — matches with an empty key.
    assert re.findall(r"s\.judge_dimensions(\s*->>?\s*'\w+')?", sql) == [
        " -> 'dimensions'",  # the type guard
        " -> 'dimensions'",  # the array walked by jsonb_array_elements
    ]

    # Every mention of an array element, likewise, in statement order.
    assert re.findall(r"d\.value(\s*->>?\s*'\w+')?", sql) == [
        " ->> 'id'",
        " ->> 'tier'",
        " -> 'score'",
        "",  # jsonb_typeof(d.value) = 'object'
        " -> 'id'",  # jsonb_typeof(d.value -> 'id') = 'string'
    ]

    # …and exactly three of them are emitted, under exactly these keys.
    arguments = re.search(r"jsonb_build_object\(([^)]*)\)", sql)
    assert arguments is not None
    parts = [part.strip() for part in arguments.group(1).split(",")]
    # `strict` so an odd argument list — a value emitted without a key — is a
    # hard failure rather than a silently truncated pairing.
    assert list(zip(parts[::2], parts[1::2], strict=True)) == [
        ("'id'", "d.value ->> 'id'"),
        ("'tier'", "d.value ->> 'tier'"),
        ("'score'", "d.value -> 'score'"),
    ]
