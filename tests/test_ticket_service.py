"""Pruebas del alta de tickets y catálogo de rutas (``ticket_service``)."""
from unittest.mock import MagicMock, patch

from app import ticket_service


def _patch_client():
    client = MagicMock()
    return client, patch.object(ticket_service, "_client", return_value=client)


def test_ticket_fields_incluye_vehiculo_y_asiento_por_separado():
    assert "vehicle_id" in ticket_service.TICKET_FIELDS
    assert "seat_number" in ticket_service.TICKET_FIELDS
    assert "vehicle_idseat_number" not in ticket_service.TICKET_FIELDS


def test_search_routes_delega_en_odoo():
    client, p = _patch_client()
    client.call.return_value = [{"id": 1, "name": "Bogota"}]
    with p:
        rows = ticket_service.search_routes("bog", 8)
    client.call.assert_called_once_with(
        ticket_service.MODEL, "search_routes", ["bog", 8]
    )
    assert rows == [{"id": 1, "name": "Bogota"}]


def test_search_routes_texto_vacio():
    client, p = _patch_client()
    client.call.return_value = []
    with p:
        assert ticket_service.search_routes() == []
    client.call.assert_called_once_with(ticket_service.MODEL, "search_routes", ["", 8])


def test_create_ticket_devuelve_id():
    values = {"passenger_name": "Ana", "cellphone": "3001234567"}
    client, p = _patch_client()
    client.create.return_value = 9
    with p:
        assert ticket_service.create_ticket(values) == 9
    client.create.assert_called_once_with(ticket_service.MODEL, values)


def test_approve_ticket_usa_lista_de_ids():
    client, p = _patch_client()
    with p:
        ticket_service.approve_ticket(9)
    client.call.assert_called_once_with(ticket_service.MODEL, "approve_ticket", [[9]])


def test_cancel_ticket_usa_lista_de_ids():
    client, p = _patch_client()
    with p:
        ticket_service.cancel_ticket(9)
    client.call.assert_called_once_with(ticket_service.MODEL, "action_cancel", [[9]])


def test_link_after_approve_escribe_id_telegram():
    client, p = _patch_client()
    with p:
        ticket_service.link_after_approve(9, "100")
    client.write.assert_called_once_with(
        ticket_service.MODEL, [9], {"id_telegram": "100"}
    )


def test_normalize_incluye_das_rutas():
    row = {
        "id": 5,
        "ticket_number": "Abordaje_X",
        "status": "abordado1",
        "route_origin": [7, "Bogota"],
        "route_destination": [9, "Medellin"],
        "route_origin_bol": True,
        "route_origin_2": [3, "Cali"],
        "route_destination_2": [4, "Barranquilla"],
        "route_origin_bol_2": False,
    }
    t = ticket_service.normalize(row)
    assert t["route_origin"] == "Bogota"
    assert t["route_origin_bol"] is True
    assert t["route_origin_2"] == "Cali"
    assert t["route_destination_2"] == "Barranquilla"
    assert t["route_origin_bol_2"] is False
    assert t["status_label"] == "Abordado 1ra Ruta"


def test_list_tickets_for_user_sin_celulares_vinculados():
    client, p = _patch_client()
    # Primera llamada: buscar celulares del id_telegram (vacío)
    # Segunda llamada: buscar tickets solo por id_telegram
    client.search_read.side_effect = [
        [],  # sin celulares vinculados
        [{"id": 1, "ticket_number": "T1", "cellphone": "300", "status": "enviado"}],
    ]
    with p:
        rows = ticket_service.list_tickets_for_user("100")
    # Domain solo por id_telegram (sin OR de celulares)
    domain = client.search_read.call_args_list[1].args[1]
    assert domain == [("id_telegram", "=", "100")]
    assert len(rows) == 1
    assert rows[0]["ticket_number"] == "T1"


def test_list_tickets_for_user_incluye_celulares_vinculados():
    client, p = _patch_client()
    client.search_read.side_effect = [
        # Celulares ya vinculados a este id de Telegram
        [{"cellphone": "3001112233"}, {"cellphone": "3001112233"}, {"cellphone": ""}],
        # Tickets: id_telegram OR cellphone IN phones
        [
            {"id": 1, "ticket_number": "TG", "cellphone": "3001112233", "status": "enviado"},
            {"id": 2, "ticket_number": "HIST", "cellphone": "3001112233", "status": "enviado"},
        ],
    ]
    with p:
        rows = ticket_service.list_tickets_for_user("100")
    domain = client.search_read.call_args_list[1].args[1]
    assert domain == [
        "|",
        ("id_telegram", "=", "100"),
        ("cellphone", "in", ["3001112233"]),
    ]
    assert {r["ticket_number"] for r in rows} == {"TG", "HIST"}


def test_normalize_pasajero_en_mayusculas():
    row = {"id": 1, "passenger_name": "Maria Lopez", "status": "solicitud"}
    assert ticket_service.normalize(row)["passenger_name"] == "MARIA LOPEZ"


def test_normalize_pasajero_vacio():
    assert ticket_service.normalize({"id": 1, "passenger_name": False})["passenger_name"] == ""
