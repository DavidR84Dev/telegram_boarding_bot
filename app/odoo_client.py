from __future__ import annotations

import http.client
import xmlrpc.client
from typing import Any
from urllib.parse import urlparse

from .config import get_settings


class OdooClientError(Exception):
    """Error seguro, apto para registrar sin revelar detalles internos."""


class TimeoutSafeTransport(xmlrpc.client.SafeTransport):
    def __init__(self, timeout: int):
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host: str):
        return http.client.HTTPSConnection(host, timeout=self.timeout)


class TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout: int):
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host: str):
        return http.client.HTTPConnection(host, timeout=self.timeout)


class OdooClient:
    """Cliente XML-RPC mínimo para el usuario técnico del bot.
    """

    def __init__(
        self,
        url: str | None = None,
        database: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout: int | None = None,
    ):
        settings = get_settings()
        url = (url or str(settings.odoo_url)).rstrip("/")
        database = database or settings.odoo_db
        username = username or settings.odoo_username
        password = password or settings.odoo_password.get_secret_value()
        timeout = timeout or settings.rpc_timeout_seconds

        parsed = urlparse(url)
        transport = (
            TimeoutSafeTransport(timeout) if parsed.scheme == "https" else TimeoutTransport(timeout)
        )
        self._common = xmlrpc.client.ServerProxy(
            f"{url}/xmlrpc/2/common", transport=transport, allow_none=True
        )
        self._objects = xmlrpc.client.ServerProxy(
            f"{url}/xmlrpc/2/object", transport=transport, allow_none=True
        )
        self._db, self._username, self._password = database, username, password
        self._uid: int | None = None

    def authenticate(self) -> int:
        if self._uid:
            return self._uid
        try:
            uid = self._common.authenticate(self._db, self._username, self._password, {})
        except (OSError, xmlrpc.client.Error) as exc:
            raise OdooClientError("No fue posible conectar con Odoo") from exc
        if not isinstance(uid, int) or isinstance(uid, bool) or uid <= 0:
            raise OdooClientError("No fue posible autenticar el usuario técnico de Odoo")
        self._uid = uid
        return uid

    def _execute(self, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None) -> Any:
        try:
            return self._objects.execute_kw(
                self._db, self.authenticate(), self._password, model, method, args, kwargs or {}
            )
        except (OSError, xmlrpc.client.Error) as exc:
            raise OdooClientError(f"La consulta a Odoo no pudo completarse ({model}.{method})") from exc

    def search_read(
        self,
        model: str,
        domain: list[list[Any]],
        fields: list[str],
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        result = self._execute(
            model, "search_read", [domain], {"fields": fields, "limit": limit}
        )
        return list(result)

    def search(
        self,
        model: str,
        domain: list[list[Any]],
        limit: int = 50,
        order: str | None = None,
    ) -> list[int]:
        kwargs: dict[str, Any] = {"limit": limit}
        if order:
            kwargs["order"] = order
        result = self._execute(model, "search", [domain], kwargs)
        return list(result)

    def create(self, model: str, values: dict[str, Any]) -> int:
        """Crea un registro y devuelve su id."""
        result = self._execute(model, "create", [values])
        return int(result)

    def write(self, model: str, ids: list[int], values: dict[str, Any]) -> bool:
        return bool(self._execute(model, "write", [ids, values]))

    def call(self, model: str, method: str, args: list[Any]) -> Any:
        """Ejecuta un método de modelo, p. ej. ``action_board`` con [[id]]."""
        return self._execute(model, method, args)