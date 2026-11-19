"""Pruebas de POST /internal/reload-config."""
from app import runtime_config
from app.odoo_client import OdooClientError

RELOAD_HEADERS = {"Authorization": "Bearer internal-test-token"}


def test_reload_sin_token(client):
    assert client.post("/internal/reload-config").status_code == 401


def test_reload_token_incorrecto(client):
    r = client.post("/internal/reload-config", headers={"Authorization": "Bearer malo"})
    assert r.status_code == 401


def test_reload_ok(client, monkeypatch):
    llamadas = []

    def fake_load(_client):
        llamadas.append(True)

    monkeypatch.setattr(runtime_config, "load_from_odoo", fake_load)
    r = client.post("/internal/reload-config", headers=RELOAD_HEADERS)
    assert r.status_code == 200
    assert r.json() == {"reloaded": True}
    assert llamadas == [True]


def test_reload_odoo_caido_devuelve_503(client, monkeypatch):
    def fake_load(_client):
        raise OdooClientError("No fue posible conectar con Odoo")

    monkeypatch.setattr(runtime_config, "load_from_odoo", fake_load)
    r = client.post("/internal/reload-config", headers=RELOAD_HEADERS)
    assert r.status_code == 503
