"""⑮ GET /api/admin/verification/accuracy (명세 8.5 · 8.6 · 9장). 데이터는 data/dev 더미.

8.6 '엔드포인트별 오류 코드' 표 대응: UNAUTHORIZED + 공통 DB_UNAVAILABLE · METHOD_NOT_ALLOWED.
"""

import pytest
from sqlalchemy import text

from api import config
from tests.api_helpers import failure, success

URL = "/api/admin/verification/accuracy"
TOKEN = "test-admin-token"


@pytest.fixture
def admin_token(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    return {"X-Admin-Token": TOKEN}


def test_accuracy_token_missing(api_client, admin_token):
    body = failure(api_client.get(URL), 401, "UNAUTHORIZED")
    assert body["message"] == "관리자 인증이 필요해요."


def test_accuracy_token_mismatch(api_client, admin_token):
    failure(api_client.get(URL, headers={"X-Admin-Token": "wrong"}), 401, "UNAUTHORIZED")


@pytest.mark.parametrize("sent", [None, "", "anything"])
def test_accuracy_server_token_unset(api_client, monkeypatch, sent):
    monkeypatch.setattr(config, "ADMIN_TOKEN", "")  # 서버 값이 비면 어떤 토큰도 401
    headers = {} if sent is None else {"X-Admin-Token": sent}
    failure(api_client.get(URL, headers=headers), 401, "UNAUTHORIZED")


def test_accuracy_ok(api_client, admin_token):
    body = success(api_client.get(URL, headers=admin_token))
    assert body["data"] == {
        "items": [
            {"field_name": "district", "checked_n": 2, "accuracy_pct": 50.0},
            {"field_name": "amount", "checked_n": 3, "accuracy_pct": 66.7},
            {"field_name": "age", "checked_n": 3, "accuracy_pct": 100.0},
        ],
        "total": 3,
    }
    assert body["meta"]["base_quarter"] is None and body["meta"]["model_version"] is None


def test_accuracy_empty(api_client, dummy_db, admin_token):
    dummy_db.execute(text("DELETE FROM program_verification"))  # 테스트 SAVEPOINT 안 — 끝나면 되돌린다
    assert success(api_client.get(URL, headers=admin_token))["data"] == {"items": [], "total": 0}


def test_accuracy_unauthorized_before_db(dead_db_client, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_TOKEN", TOKEN)
    failure(dead_db_client.get(URL), 401, "UNAUTHORIZED")  # DB가 죽어 있어도 토큰 검사가 먼저


def test_accuracy_db_unavailable(dead_db_client, admin_token):
    failure(dead_db_client.get(URL, headers=admin_token), 503, "DB_UNAVAILABLE")


def test_accuracy_method_not_allowed(api_client, admin_token):
    failure(api_client.post(URL, headers=admin_token), 405, "METHOD_NOT_ALLOWED")
