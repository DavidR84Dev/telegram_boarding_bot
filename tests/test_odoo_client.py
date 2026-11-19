"""Pruebas del cliente XML-RPC (mockeado, sin red)."""
from unittest.mock import MagicMock, patch

import pytest

from app.odoo_client import OdooClient, OdooClientError


def _client_with_mocks(auth_uid=7, execute_return=None):
    with patch("app.odoo_client.xmlrpc.client.ServerProxy") as proxy:
        proxy.side_effect = [MagicMock(), MagicMock()]  # common, objects
        client = OdooClient(
            url="http://127.0.0.1:8069",
            database="db",
            username="user",
            password="pass",
            timeout=5,
        )
        client._common.authenticate.return_value = auth_uid
        client._objects.execute_kw.return_value = execute_return if execute_return is not None else []
        return client


def test_authenticate_ok():
    client = _client_with_mocks(auth_uid=7)
    assert client.authenticate() == 7


def test_authenticate_falla():
    client = _client_with_mocks(auth_uid=False)
    with pytest.raises(OdooClientError):
        client.authenticate()


def test_search_read_devuelve_filas():
    rows = [{"id": 1, "ticket_number": "Abordaje_X"}]
    client = _client_with_mocks(execute_return=rows)
    result = client.search_read("reservations.tickets", [("id", "=", 1)], ["id", "ticket_number"], limit=1)
    assert result == rows
    # execute_kw recibe db, uid, pass, model, method, args, kwargs
    args, kwargs = client._objects.execute_kw.call_args
    assert args[3] == "reservations.tickets"
    assert args[4] == "search_read"
    assert args[5] == [[("id", "=", 1)]]


def test_write_ok():
    client = _client_with_mocks(execute_return=True)
    assert client.write("reservations.tickets", [1, 2], {"id_telegram": "999"})
    args = client._objects.execute_kw.call_args[0]
    assert args[4] == "write"
    assert args[5] == [[1, 2], {"id_telegram": "999"}]


def test_create_devuelve_id():
    client = _client_with_mocks(execute_return=5)
    assert client.create("reservations.tickets", {"passenger_name": "Ana"}) == 5
    args = client._objects.execute_kw.call_args[0]
    assert args[4] == "create"
    assert args[5] == [{"passenger_name": "Ana"}]


def test_search_devuelve_ids_con_orden():
    client = _client_with_mocks(execute_return=[3, 1])
    ids = client.search("res.country.state", [("name", "ilike", "bog")], limit=8, order="name")
    assert ids == [3, 1]
    args = client._objects.execute_kw.call_args[0]
    assert args[4] == "search"
    assert args[5] == [[("name", "ilike", "bog")]]
    assert args[6] == {"limit": 8, "order": "name"}


def test_search_sin_orden_no_lo_envia():
    client = _client_with_mocks(execute_return=[1])
    client.search("res.country.state", [], limit=5)
    args = client._objects.execute_kw.call_args[0]
    assert args[6] == {"limit": 5}


def test_error_de_red_se_envuelve():
    client = _client_with_mocks()
    client._objects.execute_kw.side_effect = ConnectionError("red caida")
    with pytest.raises(OdooClientError):
        client.search_read("reservations.tickets", [], ["id"], limit=1)
