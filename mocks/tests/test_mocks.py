import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import DEFAULT_SEED, create_app

SEED = json.loads(DEFAULT_SEED.read_text(encoding="utf-8"))
COMPANIES = {c["key"]: c for c in SEED["companies"]}
APPROVE_INN = COMPANIES["approve"]["company"]["inn"]
BKI_FAILURE_INN = COMPANIES["bki_failure"]["company"]["inn"]
UNKNOWN_INN = "0000000000"


@pytest.fixture
def client():
    return TestClient(create_app(DEFAULT_SEED, latency_ms=0))


def test_health_counts_seed_companies(client):
    assert client.get("/health").json() == {"status": "ok", "companies": len(COMPANIES)}


@pytest.mark.parametrize("key", sorted(COMPANIES))
def test_every_company_is_served_in_seed_format(client, key):
    company = COMPANIES[key]
    inn = company["company"]["inn"]
    if company["mock_behavior"].get("bki"):
        client.post("/admin/reset")
        for _ in range(company["mock_behavior"]["bki"]["fail_first_n"]):
            client.get("/bki/report", params={"inn": inn})

    egrul = client.get(f"/egrul/{inn}").json()
    pb = client.get(f"/pb/{inn}").json()
    bki = client.get("/bki/report", params={"inn": inn}).json()

    assert egrul == {
        "inn": inn,
        "name": company["company"]["name"],
        "legal_form": company["company"]["legal_form"],
        **company["mocks"]["egrul"],
    }
    assert pb == {"inn": inn, **company["mocks"]["pb"]}
    assert bki == {"inn": inn, **company["mocks"]["bki"]}


def test_unknown_inn_is_404(client):
    for url in (f"/egrul/{UNKNOWN_INN}", f"/pb/{UNKNOWN_INN}", f"/bki/report?inn={UNKNOWN_INN}"):
        response = client.get(url)
        assert response.status_code == 404
        assert response.json()["code"] == "not_found"


@pytest.mark.parametrize("bad", ["123", "12345678901", "abcdefghij"])
def test_invalid_inn_is_422(client, bad):
    assert client.get(f"/egrul/{bad}").status_code == 422
    assert client.get("/bki/report", params={"inn": bad}).status_code == 422


def test_bki_fails_first_two_requests_then_recovers(client):
    statuses = [
        client.get("/bki/report", params={"inn": BKI_FAILURE_INN}).status_code for _ in range(4)
    ]
    assert statuses == [503, 503, 200, 200]
    # Other services for the same company are not affected
    assert client.get(f"/egrul/{BKI_FAILURE_INN}").status_code == 200


def test_reset_repeats_the_failure(client):
    for _ in range(3):
        client.get("/bki/report", params={"inn": BKI_FAILURE_INN})
    assert client.post("/admin/reset").json() == {"status": "reset"}
    assert client.get("/bki/report", params={"inn": BKI_FAILURE_INN}).status_code == 503


def test_healthy_company_never_fails(client):
    for _ in range(3):
        assert client.get("/bki/report", params={"inn": APPROVE_INN}).status_code == 200


def test_seed_path_from_environment(monkeypatch, tmp_path: Path):
    data = {"companies": [COMPANIES["approve"]]}
    path = tmp_path / "companies.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("MOCKS_SEED_PATH", str(path))
    assert TestClient(create_app(latency_ms=0)).get("/health").json()["companies"] == 1
