"""Pruebas de la carga de ajustes desde bot.telegram.config."""
import pytest

from app import runtime_config


@pytest.fixture(autouse=True)
def _restore_values():
    """Evita que estas pruebas alteren la configuración del resto de la suite."""
    saved = dict(runtime_config._values)
    yield
    runtime_config._values.clear()
    runtime_config._values.update(saved)


class FakeOdoo:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def search_read(self, model, domain, fields, limit=50):
        self.calls.append({"model": model, "domain": domain, "fields": fields, "limit": limit})
        return self.rows


def _record(**overrides):
    rec = {
        "id": 7,
        "token": "t",
        "webhook_secret": "s",
        "public_domain": "bot.example.com",
        "internal_token": "i",
        "odoo_public_url": "https://odoo.example.com",
    }
    rec.update(overrides)
    return rec


def test_load_from_odoo_ok():
    fake = FakeOdoo([_record()])
    runtime_config.load_from_odoo(fake)

    call = fake.calls[0]
    assert call["model"] == "bot.telegram.config"
    assert call["limit"] == 1
    assert ["active", "=", True] in call["domain"]
    assert ["flag", "=", True] in call["domain"]
    assert runtime_config.get("telegram_bot_token") == "t"
    assert runtime_config.get("telegram_webhook_secret") == "s"
    assert runtime_config.get("bot_public_domain") == "bot.example.com"
    assert runtime_config.get("internal_api_token") == "i"
    assert runtime_config.get("odoo_public_url") == "https://odoo.example.com"


def test_load_from_odoo_sin_registro_activo():
    with pytest.raises(runtime_config.RuntimeConfigError, match="registro activo"):
        runtime_config.load_from_odoo(FakeOdoo([]))


def test_load_from_odoo_campo_vacio():
    with pytest.raises(runtime_config.RuntimeConfigError, match="token"):
        runtime_config.load_from_odoo(FakeOdoo([_record(token="")]))


def test_load_from_odoo_campo_falso():
    with pytest.raises(runtime_config.RuntimeConfigError, match="public_domain"):
        runtime_config.load_from_odoo(FakeOdoo([_record(public_domain=False)]))


def test_get_sin_cargar():
    runtime_config._values.clear()
    with pytest.raises(runtime_config.RuntimeConfigError):
        runtime_config.get("telegram_bot_token")


def test_get_clave_desconocida():
    with pytest.raises(KeyError):
        runtime_config.get("no_existe")


class FlakyOdoo:
    """Falla ``n`` veces con error de conexión y luego devuelve el registro."""

    def __init__(self, failures, rows=None):
        self.failures = failures
        self.rows = rows if rows is not None else [_record()]
        self.calls = 0

    def search_read(self, model, domain, fields, limit=50):
        self.calls += 1
        if self.calls <= self.failures:
            from app.odoo_client import OdooClientError

            raise OdooClientError("No fue posible conectar con Odoo")
        return self.rows


def test_retry_recupera_cuando_odoo_vuelve():
    waits = []
    fake = FlakyOdoo(failures=2)
    runtime_config.load_from_odoo_with_retry(fake, sleep=waits.append)
    assert fake.calls == 3
    assert waits == [2.0, 4.0]
    assert runtime_config.get("telegram_bot_token") == "t"


def test_retry_agota_el_tiempo_y_propaga():
    from app.odoo_client import OdooClientError

    waits = []
    fake = FlakyOdoo(failures=99)
    with pytest.raises(OdooClientError):
        runtime_config.load_from_odoo_with_retry(
            fake, max_wait_seconds=10, initial_delay=2, sleep=waits.append
        )
    assert sum(waits) <= 10
    assert waits == [2, 4]


def test_retry_no_reintenta_error_de_configuracion():
    waits = []
    with pytest.raises(runtime_config.RuntimeConfigError):
        runtime_config.load_from_odoo_with_retry(FakeOdoo([]), sleep=waits.append)
    assert waits == []
