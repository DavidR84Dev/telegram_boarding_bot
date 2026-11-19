"""Fixtures compartidos: variables de entorno de prueba antes de importar la app."""
import os

os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("ODOO_URL", "http://127.0.0.1:8069")
os.environ.setdefault("ODOO_DB", "test_db")
os.environ.setdefault("ODOO_USERNAME", "bot@test")
os.environ.setdefault("ODOO_PASSWORD", "secret")

import pytest
from fastapi.testclient import TestClient

from app import runtime_config

# Ajustes que en producción se leen de ir.config_parameter.
runtime_config.set_for_testing(
    {
        "telegram_bot_token": "123456:TEST-TOKEN-NO-REAL",
        "telegram_webhook_secret": "test-webhook-secret",
        "bot_public_domain": "bot.test.local",
        "internal_api_token": "internal-test-token",
        "odoo_public_url": "http://127.0.0.1:8069",
    }
)


@pytest.fixture()
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def auth_headers():
    return {"Authorization": "Bearer internal-test-token"}


@pytest.fixture()
def sample_ticket():
    return {
        "id": 42,
        "ticket_number": "Abordaje_ABCD1234",
        "passenger_name": "Maria Lopez",
        "status": "enviado",
        "status_label": "Enviado",
        "seat_number": "12A",
        "route_origin": "Bogota",
        "route_destination": "Medellin",
        "access_token": "tok-abc",
        "url_access": "http://127.0.0.1:8069/form/accept/tok-abc",
        "id_telegram": "999888777",
        "token_expiration": "",
    }
