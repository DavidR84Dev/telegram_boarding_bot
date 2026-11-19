"""Pruebas del endpoint /telegram/webhook (secret + JSON)."""
SECRET_HEADERS = {"X-Telegram-Bot-Api-Secret-Token": "test-webhook-secret"}


def test_webhook_ok(client):
    response = client.post("/telegram/webhook", json={"update_id": 1}, headers=SECRET_HEADERS)
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_webhook_secret_invalido(client):
    response = client.post(
        "/telegram/webhook",
        json={"update_id": 1},
        headers={"X-Telegram-Bot-Api-Secret-Token": "malo"},
    )
    assert response.status_code == 403


def test_webhook_sin_secret(client):
    response = client.post("/telegram/webhook", json={"update_id": 1})
    assert response.status_code == 403


def test_webhook_json_invalido(client):
    response = client.post(
        "/telegram/webhook", content=b"no-json", headers=SECRET_HEADERS
    )
    assert response.status_code == 400


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
