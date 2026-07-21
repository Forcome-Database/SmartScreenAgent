import pytest

pytestmark = pytest.mark.integration


async def test_openapi_lists_read_routes_with_models(client):
    spec = (await client.get("/openapi.json")).json()
    paths = spec["paths"]
    for route in [
        "/api/v1/jds/{code}/candidates",
        "/api/v1/candidates",
        "/api/v1/candidates/{candidate_id}",
        "/api/v1/candidates/{candidate_id}/scores/{score_id}",
        "/api/v1/candidates/{candidate_id}/raw-file",
        "/api/v1/jds",
        "/api/v1/jds/{code}",
        "/api/v1/jds/{code}/rule-versions",
        "/api/v1/jds/{code}/rule-versions/{from_version}/diff/{to_version}",
    ]:
        assert route in paths, f"missing {route}"
        assert "200" in paths[route]["get"]["responses"]
