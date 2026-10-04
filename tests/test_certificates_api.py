"""⑰ certificates · ⑯ health — DB를 쓰지 않는 엔드포인트 (명세 8.5 · 8.6). 오류 코드는 공통 코드만."""

from fastapi.testclient import TestClient

from api.main import app
from common.certificate_normalizer import catalog

client = TestClient(app)


def test_certificates_match_catalog():
    response = client.get("/api/certificates")
    body = response.json()
    assert response.status_code == 200 and (body["status"], body["code"]) == ("SUCCESS", "OK")
    data = body["data"]
    assert data["total"] == len(data["items"]) == len(catalog()) == 12
    assert [i["name"] for i in data["items"]] == [e.name for e in catalog()]
    assert data["items"][0] == {
        "name": "조리기능사", "aliases": ["한식조리사", "한식조리기능사"], "category": "조리"
    }


def test_certificates_ok_without_db(dead_db_client):
    response = dead_db_client.get("/api/certificates")
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 12


def test_certificates_method_not_allowed():
    response = client.post("/api/certificates")
    body = response.json()
    assert response.status_code == 405
    assert (body["status"], body["code"], body["data"]) == ("FAILURE", "METHOD_NOT_ALLOWED", None)


def test_health_ok_even_when_db_down(dead_db_client):
    response = dead_db_client.get("/health")
    assert response.status_code == 200
    assert response.json()["data"]["service_status"] == "ok"
