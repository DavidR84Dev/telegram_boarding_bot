"""Mensajes HTML para Telegram (caption del ticket de abordaje)."""
from __future__ import annotations

import html
from typing import Any


def _esc(value: Any) -> str:
    return html.escape(str(value if value not in (None, "") else "—"))


def _mark(checked: Any) -> str:
    """Marca de check-in: ✅ abordado, ⏳ pendiente."""
    return "✅" if checked else "⏳"


def _has_route_2(ticket: dict[str, Any]) -> bool:
    return bool(ticket.get("route_origin_2") or ticket.get("route_destination_2"))


def _route_line(ticket: dict[str, Any], number: int) -> str:
    if number == 1:
        origin = ticket.get("route_origin")
        vehicle = ticket.get("vehicle_id")
        destination = ticket.get("route_destination")
        checked = ticket.get("route_origin_bol")
    else:
        origin = ticket.get("route_origin_2")
        vehicle = ticket.get("vehicle_id")
        destination = ticket.get("route_destination_2")
        checked = ticket.get("route_origin_bol_2")
    return (
        f"🗺 <b>Ruta {number}:</b> {_esc(origin)} → {_esc(destination)} "
        f"{_mark(checked)} → (<b>Vehículo:</b> {_esc(vehicle)})" 
    )


def ticket_caption(ticket: dict[str, Any]) -> str:
    lines = [
        f"🎫 <b>{_esc(ticket.get('ticket_number'))}</b>",
        f"👤 <b>Pasajero:</b> {_esc(ticket.get('passenger_name'))}",
        f"🚌 <b>Vehículo:</b> {_esc(ticket.get('vehicle_id'))}",
        # f"💺 <b>Asiento:</b> {_esc(ticket.get('seat_number'))}",
        _route_line(ticket, 1),
    ]
    if _has_route_2(ticket):
        lines.append(_route_line(ticket, 2))
    lines.append(f"📌 <b>Estado:</b> {_esc(ticket.get('status_label', ticket.get('status')))}")
    return "\n".join(lines)


def boarding_validated(ticket: dict[str, Any]) -> str:
    """Aviso de texto al pasajero cuando el validador confirma un abordaje."""
    status = ticket.get("status")
    leg = "1.ª" if status == "abordado1" else "2.ª"
    lines = [
        f"✅ Tu ticket <b>{_esc(ticket.get('ticket_number'))}</b> fue validado y aprobado.",
        f"🚌 Abordaje de la {leg} ruta registrado.",
    ]
    if status == "abordado1" and _has_route_2(ticket):
        lines.append("⏳ Falta la validación de la 2.ª ruta.")
    return "\n".join(lines)
