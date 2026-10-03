from fastapi.testclient import TestClient

from api.config import APP_ENV
from api.main import app

client = TestClient(app)


def test_health_returns_envelope_without_db():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["code"], body["message"]) == ("SUCCESS", "OK", None)
    assert body["data"] == {"service_status": "ok", "env": APP_ENV}
    assert body["errors"] == [] and body["warnings"] == []
    assert body["meta"]["base_quarter"] is None and body["meta"]["model_version"] is None
