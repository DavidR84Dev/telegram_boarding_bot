"""Lógica de negocio sobre ``reservations.tickets`` (vía XML-RPC)."""
from __future__ import annotations

import logging
from typing import Any

from .odoo_client import OdooClient

logger = logging.getLogger(__name__)

MODEL = "reservations.tickets"

TICKET_FIELDS = [
    "id",
    "ticket_number",
    "passenger_name",
    "cellphone",
    "status",
    "vehicle_id",
    "seat_number",
    "route_origin",
    "route_destination",
    "route_origin_bol",
    "route_origin_2",
    "route_destination_2",
    "route_origin_bol_2",
    "access_token",
    "url_access",
    "id_telegram",
    "token_expiration",
]

STATUS_LABELS = {
    "solicitud": "En Solicitud",
    "enviado": "Enviado",
    "abordado1": "Abordado 1ra Ruta",
    "abordado2": "Abordado 2da Ruta",
    "cancelado": "Cancelado",
}


def _client() -> OdooClient:
    return OdooClient()


def _m2o_name(value: Any) -> str:
    """Convierte un Many2one de search_read ([id, nombre]) en texto."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return value[1] or ""
    return value or ""


def normalize(row: dict[str, Any]) -> dict[str, Any]:
    status = row.get("status") or ""
    return {
        "id": row.get("id"),
        "ticket_number": row.get("ticket_number") or "",
        "passenger_name": (row.get("passenger_name") or "").upper(),
        "cellphone": row.get("cellphone") or "",
        "status": status,
        "status_label": STATUS_LABELS.get(status, status),
        "seat_number": row.get("seat_number") or "",
        "route_origin": _m2o_name(row.get("route_origin")),
        "route_destination": _m2o_name(row.get("route_destination")),
        "route_origin_bol": bool(row.get("route_origin_bol")),
        "route_origin_2": _m2o_name(row.get("route_origin_2")),
        "route_destination_2": _m2o_name(row.get("route_destination_2")),
        "route_origin_bol_2": bool(row.get("route_origin_bol_2")),
        "vehicle_id": _m2o_name(row.get("vehicle_id")),
        "access_token": row.get("access_token") or "",
        "url_access": row.get("url_access") or "",
        "id_telegram": str(row.get("id_telegram") or ""),
        "token_expiration": row.get("token_expiration") or "",
    }


def list_tickets_for_user(telegram_user_id: str) -> list[dict[str, Any]]:
    """Tickets del usuario: los del id de Telegram y los de sus celulares.

    Primero recolecta los celulares ya vinculados al id de Telegram y luego
    busca ``id_telegram = user`` OR ``cellphone IN phones`` para mostrar el
    histórico completo del número, aunque ciertos tickets aún no tengan
    ``id_telegram``.
    """
    client = _client()
    linked = client.search_read(
        MODEL, [("id_telegram", "=", telegram_user_id)], ["cellphone"], limit=50
    )
    phones = sorted({(r.get("cellphone") or "").strip() for r in linked if (r.get("cellphone") or "").strip()})
    domain: list[list[Any]] = [("id_telegram", "=", telegram_user_id)]
    if phones:
        domain = ["|", ("id_telegram", "=", telegram_user_id), ("cellphone", "in", phones)]
    rows = client.search_read(MODEL, domain, TICKET_FIELDS, limit=100)
    return [normalize(r) for r in rows]


def get_ticket(ticket_id: int) -> dict[str, Any] | None:
    rows = _client().search_read(MODEL, [("id", "=", ticket_id)], TICKET_FIELDS, limit=1)
    return normalize(rows[0]) if rows else None


def find_by_cellphone(cellphone: str) -> list[dict[str, Any]]:
    rows = _client().search_read(
        MODEL, [("cellphone", "=", cellphone)], ["id"], limit=50
    )
    return [normalize(r) for r in rows]


def link_telegram(cellphone: str, telegram_user_id: str) -> int:
    """Vincula todos los tickets del celular con el id de Telegram."""
    tickets = _client().search_read(MODEL, [("cellphone", "=", cellphone)], ["id"], limit=50)
    ids = [r["id"] for r in tickets]
    if not ids:
        return 0
    _client().write(MODEL, ids, {"id_telegram": telegram_user_id})
    logger.info("Vinculados %d ticket(s) del celular %s a telegram %s", len(ids), cellphone, telegram_user_id)
    return len(ids)


def cancel_ticket(ticket_id: int) -> None:
    """Llama ``action_cancel`` en Odoo (enviado → cancelado)."""
    _client().call(MODEL, "action_cancel", [[ticket_id]])


# ---------------------------------------------------------------------------
# Alta de tickets desde el bot (/solicitar)
# ---------------------------------------------------------------------------

def search_routes(text: str = "", limit: int = 8) -> list[dict[str, Any]]:
    """Sugiere rutas (municipios del país de la compañía) por texto.

    Delega en ``reservations.tickets.search_routes`` de Odoo (mismo filtro
    de país y orden que el catálogo del formulario).
    """
    result = _client().call(MODEL, "search_routes", [text or "", limit])
    return [dict(r) for r in (result or [])]


def create_ticket(values: dict[str, Any]) -> int:
    """Crea un ticket (sin ``id_telegram``) y devuelve su id.

    El id de Telegram se asocia después de aprobar para que el notify
    automático (postcommit) lo vea ``not_linked`` y no duplique el pase.
    """
    return int(_client().create(MODEL, values))


def approve_ticket(ticket_id: int) -> None:
    """Aprueba el ticket en Odoo (solicitud → enviado, genera QR)."""
    _client().call(MODEL, "approve_ticket", [[ticket_id]])


def link_after_approve(ticket_id: int, telegram_user_id: str) -> None:
    """Asocia el id de Telegram al ticket ya aprobado."""
    _client().write(MODEL, [ticket_id], {"id_telegram": telegram_user_id})
