from backend.app.schemas.read import CandidateListItem, RankedCandidateItem


def test_list_items_expose_no_pii_fields():
    ranked = set(RankedCandidateItem.model_fields)
    flat = set(CandidateListItem.model_fields)
    forbidden = {"name", "phone", "email", "name_cipher", "raw_file_key", "parsed_markdown"}
    assert ranked.isdisjoint(forbidden)
    assert flat.isdisjoint(forbidden)
    assert {"candidate_id", "score_id", "total_score", "grade"} <= ranked
    assert {"candidate_id", "created_at", "latest_state", "scored_jd_codes"} <= flat
