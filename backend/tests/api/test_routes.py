import pathlib

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _client(tmp_path: pathlib.Path) -> TestClient:
    settings = Settings(cors_origins=[], audit_path=tmp_path / "audit.jsonl", log_level="WARNING")
    return TestClient(create_app(settings))


def test_health_and_state(tmp_path: pathlib.Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/health").json() == {"status": "ok"}
    r = client.get("/api/state", headers={"X-Request-ID": "abc123"})
    assert r.status_code == 200
    assert r.headers["X-Request-ID"] == "abc123"
    body = r.json()
    assert body["clock_label"] == "05:00"
    assert body["proposal"] is None
    assert len(body["sources"]) == 7


def test_invalid_event_returns_error_envelope(tmp_path: pathlib.Path) -> None:
    r = _client(tmp_path).post(
        "/api/events", json={"kind": "SAM_POPUP", "position": {"x_km": 5000, "y_km": 1}}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "x_km" in r.json()["error"]["message"]


def test_approve_without_proposal_is_conflict(tmp_path: pathlib.Path) -> None:
    r = _client(tmp_path).post("/api/proposal/A/approve")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CONFLICT"


def test_path_parameters_are_validated(tmp_path: pathlib.Path) -> None:
    client = _client(tmp_path)
    assert client.post("/api/proposal/abc/approve").status_code == 422
    assert client.get("/api/missions/DROP%20TABLE/explain").status_code == 422


def test_generate_approve_flow(tmp_path: pathlib.Path) -> None:
    client = _client(tmp_path)
    proposal = client.post("/api/plan/generate").json()["proposal"]
    approved = client.post(f"/api/proposal/{proposal['recommended_coa_id']}/approve").json()
    assert approved["plan"]["version"] == 1
    assert approved["plan_violations"] == []
    assert client.get("/api/audit").json()[0]["action"] == "APPROVE"
