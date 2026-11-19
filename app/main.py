"""Entrypoint FastAPI: lifespan (PTB + setWebhook) y routers."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from telegram import Update

from . import runtime_config
from .config import get_settings
from .odoo_client import OdooClient
from .routers import internal, telegram_webhook
from .schemas import HealthOut
from .telegram_app import create_application, sync_bot_commands

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
# httpx registra la URL completa de cada petición a Telegram, que incluye
# el token del bot (/bot<TOKEN>/...). Solo se muestran avisos y errores.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.app_env != "testing":
        # XML-RPC es síncrono: se ejecuta en un hilo para no bloquear el event loop.
        await asyncio.to_thread(runtime_config.load_from_odoo_with_retry, OdooClient())
    application = create_application()
    if settings.app_env != "testing":
        await application.initialize()
        # Arranca el worker que consume application.update_queue y ejecuta
        # los handlers. Sin esto, el webhook responde 200 pero nadie procesa.
        await application.start()
        webhook_url = f"https://{runtime_config.get('bot_public_domain')}/telegram/webhook"
        await application.bot.set_webhook(
            url=webhook_url,
            secret_token=runtime_config.get("telegram_webhook_secret"),
            allowed_updates=Update.ALL_TYPES,
        )
        logger.info("Webhook registrado: %s", webhook_url)
        try:
            await sync_bot_commands(application)
        except Exception:
            logger.exception("No se pudieron publicar los comandos del bot")
    yield
    if settings.app_env != "testing":
        await application.stop()
        await application.bot.delete_webhook()
        await application.shutdown()


app = FastAPI(title="Telegram Boarding Bot", version="1.0.0", lifespan=lifespan)
app.include_router(telegram_webhook.router)
app.include_router(internal.router)


@app.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status="ok")
