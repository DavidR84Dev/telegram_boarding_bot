"""Webhook de Telegram: POST /telegram/webhook con verificación de secret."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from telegram import Update

from .. import runtime_config
from ..telegram_app import get_application

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> dict:
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    expected = runtime_config.get("telegram_webhook_secret")
    if secret != expected:
        logger.warning("Webhook rechazado: secret inválido")
        raise HTTPException(status_code=403, detail="Invalid secret token")
    try:
        data = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
    application = get_application()
    update = Update.de_json(data, application.bot)
    await application.update_queue.put(update)
    return {"ok": True}
