"""Configuración del bot leída desde Odoo (modelo ``bot.telegram.config``).

Solo las credenciales de conexión a Odoo (URL, BD, usuario y API key) viven
en ``.env``, porque son necesarias para poder leer la configuración. El resto
de secretos y ajustes se guardan en Odoo, en el registro activo de
``bot.telegram.config`` (módulo ``programacion_operativos``), y se cargan
una vez al arrancar.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from .odoo_client import OdooClientError

logger = logging.getLogger(__name__)

PARAM_MODEL = "bot.telegram.config"

# nombre interno del bot -> campo de bot.telegram.config
PARAM_FIELDS: dict[str, str] = {
    "telegram_bot_token": "token",
    "telegram_webhook_secret": "webhook_secret",
    "bot_public_domain": "public_domain",
    "internal_api_token": "internal_token",
    "odoo_public_url": "odoo_public_url",
}

# Solo registros activos; el modelo ordena por id desc, así que el primero es el más reciente.
ACTIVE_DOMAIN = [["active", "=", True], ["flag", "=", True]]


class RuntimeConfigError(RuntimeError):
    """Falta el registro de configuración en Odoo, o algún campo está vacío."""


_values: dict[str, str] = {}


def load_from_odoo(client: Any) -> None:
    """Lee el primer registro activo de ``bot.telegram.config``. ``client`` es un ``OdooClient``."""
    fields = list(PARAM_FIELDS.values())
    rows = client.search_read(PARAM_MODEL, ACTIVE_DOMAIN, ["id", *fields], limit=1)
    if not rows:
        raise RuntimeConfigError(
            f"No hay ningún registro activo en {PARAM_MODEL}; crea uno en Odoo antes de arrancar el bot"
        )
    row = rows[0]

    missing = [field for field in fields if not row.get(field)]
    if missing:
        raise RuntimeConfigError(
            f"Campos vacíos en {PARAM_MODEL} (id {row.get('id')}): " + ", ".join(missing)
        )

    _values.clear()
    for name, field in PARAM_FIELDS.items():
        _values[name] = str(row[field])
    logger.info("Configuración cargada desde %s (id %s)", PARAM_MODEL, row.get("id"))


def load_from_odoo_with_retry(
    client: Any,
    max_wait_seconds: float = 300.0,
    initial_delay: float = 2.0,
    max_delay: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Como ``load_from_odoo``, pero reintenta con backoff exponencial si Odoo no responde.

    Solo se reintentan los fallos de conexión/autenticación (``OdooClientError``).
    Un registro inexistente o con campos vacíos es un error de configuración y
    se propaga de inmediato.
    """
    delay = initial_delay
    waited = 0.0
    attempt = 1
    while True:
        try:
            load_from_odoo(client)
            return
        except OdooClientError as exc:
            if waited + delay > max_wait_seconds:
                logger.error("Odoo no disponible tras %d intentos; se abandona el arranque", attempt)
                raise
            logger.warning(
                "Odoo no disponible (intento %d): %s. Reintento en %.0f s", attempt, exc, delay
            )
            sleep(delay)
            waited += delay
            delay = min(delay * 2, max_delay)
            attempt += 1


def get(name: str) -> str:
    """Devuelve el valor de un ajuste ya cargado."""
    if name not in PARAM_FIELDS:
        raise KeyError(f"Ajuste desconocido: {name}")
    try:
        return _values[name]
    except KeyError as exc:
        raise RuntimeConfigError(
            f"Ajuste '{PARAM_FIELDS[name]}' no cargado; el bot debe arrancar con Odoo accesible"
        ) from exc


def set_for_testing(values: dict[str, str]) -> None:
    """Solo para pruebas: fija los valores sin consultar Odoo."""
    _values.clear()
    _values.update(values)
