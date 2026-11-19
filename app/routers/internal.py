"""API interna: Odoo notifica tickets aprobados y puede pedir recarga de configuración.

Se expone por HTTPS (nginx) y exige ``Authorization: Bearer <internal_token>``.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Header, HTTPException

from .. import formatting, qr_service, runtime_config, ticket_service
from ..odoo_client import OdooClient, OdooClientError
from ..schemas import NotifyBoardingIn, NotifyTicketIn
from ..telegram_app import _track, get_application

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_bearer(authorization: str | None) -> None:
    expected = f"Bearer {runtime_config.get('internal_api_token')}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/internal/notify-ticket")
async def notify_ticket(
    payload: NotifyTicketIn,
    authorization: str | None = Header(default=None),
) -> dict:
    _check_bearer(authorization)

    ticket = await asyncio.to_thread(ticket_service.get_ticket, payload.ticket_id)
    if not ticket:
        return {"sent": False, "reason": "ticket_not_found"}
    if not ticket.get("id_telegram"):
        return {"sent": False, "reason": "not_linked"}
    if ticket.get("status") != "enviado":
        return {"sent": False, "reason": f"status_{ticket.get('status')}"}

    png = await asyncio.to_thread(qr_service.generate_boarding_pass, ticket)
    caption = formatting.ticket_caption(ticket)
    application = get_application()
    chat_id = int(ticket["id_telegram"])
    try:
        sent = await application.bot.send_photo(
            chat_id=chat_id,
            photo=png,
            caption=caption,
            parse_mode="HTML",
        )
        # Registro para que «🧹 Limpiar chat» pueda borrar este pase.
        _track(chat_id, getattr(sent, "message_id", None))
    except Exception as exc:  # noqa: BLE001 — el fallo del envío se reporta, no propaga
        logger.warning("No se pudo enviar el ticket %s: %s", ticket["ticket_number"], exc)
        return {"sent": False, "reason": "send_failed"}
    return {"sent": True}


@router.post("/internal/notify-boarding")
async def notify_boarding(
    payload: NotifyBoardingIn,
    authorization: str | None = Header(default=None),
) -> dict:
    """Avisa al pasajero que su ticket fue validado en el abordaje.

    El validador de QR lo llama al detectar el cambio de estado en Odoo. El estado
    se relee de Odoo: si ya cambió, se responde ``status_mismatch`` y el validador
    reintenta en su próximo sondeo con el estado actual.
    """
    _check_bearer(authorization)

    ticket = await asyncio.to_thread(ticket_service.get_ticket, payload.ticket_id)
    if not ticket:
        return {"sent": False, "reason": "ticket_not_found"}
    if not ticket.get("id_telegram"):
        logger.warning("Aviso de abordaje omitido: el ticket %s no tiene ID de Telegram", ticket["ticket_number"])
        return {"sent": False, "reason": "not_linked"}
    if ticket.get("status") != payload.status:
        return {"sent": False, "reason": "status_mismatch"}

    application = get_application()
    chat_id = int(ticket["id_telegram"])
    try:
        await application.bot.send_message(
            chat_id=chat_id,
            text=formatting.boarding_validated(ticket),
            parse_mode="HTML",
        )
    except Exception as exc:  # noqa: BLE001 — el fallo se reporta, no propaga
        # Típico: el pasajero nunca inició chat con este bot (Telegram no permite escribirle).
        logger.warning("No se pudo avisar al pasajero del ticket %s (%s): %s",
                       ticket["ticket_number"], payload.status, exc)
        return {"sent": False, "reason": "send_failed"}
    return {"sent": True}


@router.post("/internal/reload-config")
async def reload_config(authorization: str | None = Header(default=None)) -> dict:
    """Vuelve a leer ``bot.telegram.config`` sin reiniciar el servicio.

    Nota: el token de Telegram y el secreto del webhook solo se aplican al
    arrancar; si cambian, hay que reiniciar el servicio.
    """
    _check_bearer(authorization)
    try:
        await asyncio.to_thread(runtime_config.load_from_odoo, OdooClient())
    except (OdooClientError, runtime_config.RuntimeConfigError) as exc:
        logger.warning("Recarga de configuración fallida: %s", exc)
        raise HTTPException(status_code=503, detail="No se pudo recargar la configuración") from exc
    return {"reloaded": True}
