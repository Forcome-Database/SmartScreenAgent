from __future__ import annotations

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
