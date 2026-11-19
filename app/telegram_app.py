"""Handlers de Telegram: menú, /start, /ticket, /solicitar, /cancelar, /limpiar."""
from __future__ import annotations

import asyncio
import logging
from html import escape as html_escape
from typing import Any

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import formatting, qr_service, ticket_service
from .odoo_client import OdooClientError

logger = logging.getLogger(__name__)

_application: Application | None = None

# Estado simple en memoria (producción puede migrar a Redis/DB):
# chats esperando contacto.
_awaiting_contact: set[int] = set()

# Estado del flujo /solicitar por chat (máquina de pasos con datos parciales).
_tksol: dict[int, dict[str, Any]] = {}
# Estados de ticket que el usuario puede cancelar desde el bot.
CANCELABLE_STATUSES = ("solicitud", "enviado")

# Ids de los mensajes enviados por el bot, por chat: Telegram sólo permite
# borrar los del último botón «🧹 Limpiar chat» (límite real: 48 h).
_sent_ids: dict[int, list[int]] = {}
_SENT_IDS_MAX = 200


def _track(chat_id: int, message_id: Any) -> None:
    """Registra el id de un mensaje del bot para poder borrarlo después."""
    if not isinstance(message_id, int):
        return
    ids = _sent_ids.setdefault(chat_id, [])
    ids.append(message_id)
    if len(ids) > _SENT_IDS_MAX:
        del ids[: len(ids) - _SENT_IDS_MAX]


async def _reply(message, text: str, **kwargs):
    """``reply_text`` + registro del id (para «🧹 Limpiar chat»)."""
    sent = await message.reply_text(text, **kwargs)
    if sent is not None:
        _track(message.chat_id, getattr(sent, "message_id", None))
    return sent


async def _reply_photo(message, photo, **kwargs):
    """``reply_photo`` + registro del id (para «🧹 Limpiar chat»)."""
    sent = await message.reply_photo(photo=photo, **kwargs)
    if sent is not None:
        _track(message.chat_id, getattr(sent, "message_id", None))
    return sent

# Pasos que esperan la elección de una ruta (con catálogo en línea).
_ROUTE_STEPS: dict[str, tuple[str, str]] = {
    "r1_origin": ("el origen de la Ruta 1", "origin1"),
    "r1_dest": ("el destino de la Ruta 1", "dest1"),
    "r2_origin": ("el origen de la Ruta 2", "origin2"),
    "r2_dest": ("el destino de la Ruta 2", "dest2"),
}
# Siguiente paso tras elegir una ruta.
_AFTER_ROUTE: dict[str, str] = {
    "r1_origin": "r1_dest",
    "r1_dest": "ask_r2",
    "r2_origin": "r2_dest",
    "r2_dest": "summary",
}
_STEP_PROMPTS: dict[str, str] = {
    "r1_origin": "🗺 Escribe el <b>origen</b> de la Ruta 1 (ciudad o municipio):",
    "r1_dest": "🗺 Escribe el <b>destino</b> de la Ruta 1 (ciudad o municipio):",
    "r2_origin": "🗺 Escribe el <b>origen</b> de la Ruta 2 (ciudad o municipio):",
    "r2_dest": "🗺 Escribe el <b>destino</b> de la Ruta 2 (ciudad o municipio):",
}
# Paso anterior de cada etapa (botón «⬅️ Atrás»): conserva todo lo capturado.
_PREV_STEP: dict[str, str] = {
    "phone": "name",
    "r1_origin": "phone",
    "r1_dest": "r1_origin",
    "ask_r2": "r1_dest",
    "r2_origin": "ask_r2",
    "r2_dest": "r2_origin",
}
# Resumen → paso a editar (botones «✏️ Editar»).
_EDIT_STEPS: dict[str, str] = {
    "name": "name",
    "phone": "phone",
    "r1": "r1_origin",
    "r2": "r2_origin",
}
_NAME_PROMPT = (
    "🎟 <b>Solicitud de ticket</b>\n\n"
    "Escribe el <b>nombre completo del pasajero</b> "
    "(o escribe «cancelar» para terminar este proceso de solicitud.):"
)
_PHONE_PROMPT = (
    "📱 <b>Celular de contacto</b>\n\n"
    "Comparte tu número con el botón de abajo (Telegram pedirá "
    "tu autorización) o escríbelo (7 o más dígitos):"
)

# ---------------------------------------------------------------------------
# Menú principal (ReplyKeyboard) — el usuario no necesita escribir comandos
# ---------------------------------------------------------------------------

BTN_TICKETS = "🎫 Mis tickets"
BTN_CANCELAR = "❌ Cancelar Ticket"
BTN_SOLICITAR = "🎟 Solicitar ticket"
BTN_VINCULAR = "📱 Vincular celular"
BTN_AYUDA = "ℹ️ Ayuda"
BTN_LIMPIAR = "🧹 Limpiar chat"

# Etiqueta del botón → comando interno (se reutilizan los handlers)
MENU_ACTIONS: dict[str, str] = {
    BTN_TICKETS: "ticket",
    BTN_CANCELAR: "cancelar",
    BTN_SOLICITAR: "solicitar",
    BTN_VINCULAR: "iniciar",
    BTN_AYUDA: "ayuda",
    BTN_LIMPIAR: "limpiar",
}

BOT_COMMANDS = [
    BotCommand("iniciar", "Iniciar y vincular tu celular"),
    BotCommand("solicitar", "Solicitar un ticket nuevo"),
    BotCommand("ticket", "Ver mis pases de abordaje"),
    BotCommand("cancelar", "Cancelar un ticket sin abordar"),
    BotCommand("limpiar", "Borrar mensajes del bot y reiniciar"),
    BotCommand("menu", "Mostrar el menú principal"),
    BotCommand("ayuda", "Ayuda y contacto"),
]

def main_menu() -> ReplyKeyboardMarkup:
    """Teclado persistente con las acciones principales."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_TICKETS), KeyboardButton(BTN_CANCELAR)],
            [KeyboardButton(BTN_SOLICITAR), KeyboardButton(BTN_VINCULAR)],
            [KeyboardButton(BTN_AYUDA), KeyboardButton(BTN_LIMPIAR)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Elige una opción…",
    )

def contact_menu() -> ReplyKeyboardMarkup:
    """Teclado temporal para compartir el contacto."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📞 Compartir contacto", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def sync_bot_commands(application: Application) -> None:
    """Publica el menú de comandos de Telegram (autocomplete al escribir /)."""
    await application.bot.set_my_commands(BOT_COMMANDS)
    logger.info("Comandos del bot publicados: %s", [c.command for c in BOT_COMMANDS])


def create_application() -> Application:
    """Construye la ``Application`` de PTB en modo webhook (sin Updater)."""
    global _application
    from .runtime_config import get

    application = (
        Application.builder()
        .token(get("telegram_bot_token"))
        .updater(None)
        .build()
    )
    register_handlers(application)
    _application = application
    return application


def get_application() -> Application:
    if _application is None:
        raise RuntimeError("La aplicación de Telegram no está inicializada")
    return _application


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("iniciar", cmd_start))
    application.add_handler(CommandHandler("solicitar", cmd_solicitar))
    application.add_handler(CommandHandler("ticket", cmd_ticket))
    application.add_handler(CommandHandler("cancelar", cmd_cancelar))
    application.add_handler(CommandHandler("limpiar", cmd_limpiar))
    application.add_handler(CommandHandler("menu", cmd_menu))
    application.add_handler(CommandHandler("ayuda", cmd_ayuda))
    application.add_handler(CallbackQueryHandler(on_ticket_choice, pattern=r"^ticket:\d+$"))
    application.add_handler(CallbackQueryHandler(on_cancel_choice, pattern=r"^cancel:"))
    application.add_handler(CallbackQueryHandler(on_tksol_choice, pattern=r"^tksol:"))
    application.add_handler(MessageHandler(filters.CONTACT, on_contact))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))


# ---------------------------------------------------------------------------
# Menú / ayuda
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "ℹ️ <b>Cómo usar el bot</b>\n\n"
    f"1. {BTN_VINCULAR} — ingresa el número celular a registrar.\n"
    f"2. {BTN_TICKETS} — elige un ticket y recibe tu pase con QR.\n"
    f"3. {BTN_SOLICITAR} — crea un ticket nuevo (nombre, celular y rutas). "
    "En cada paso puedes usar «⬅️ Atrás» y, en el resumen, «✏️ Editar».\n"
    f"4. {BTN_CANCELAR} — cancela un ticket que aún no has abordado.\n"
    f"5. {BTN_LIMPIAR} — borra los mensajes del bot (últimas 48 h) y "
    "reinicia la conversación.\n\n"
    "También puedes usar los comandos: /solicitar /ticket /cancelar /limpiar /menu\n"
    "Si algo falla, contacta a la empresa."
)


async def _show_main_menu(message, text: str) -> None:
    await _reply(message, text, parse_mode="HTML", reply_markup=main_menu())


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_main_menu(
        update.message,
        "🎫 <b>Menú principal</b>\n\nElige una opción del teclado inferior.",
    )


async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _show_main_menu(update.message, HELP_TEXT)


# ---------------------------------------------------------------------------
# /start + vinculación
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user = update.effective_user
    _tksol.pop(chat_id, None)
    tickets = await asyncio.to_thread(ticket_service.list_tickets_for_user, str(user.id))
    if tickets:
        _awaiting_contact.discard(chat_id)
        await _show_main_menu(
            update.message,
            f"👋 Hola <b>{user.first_name or ''}</b>, tu cuenta ya está vinculada.\n\n"
            f"Usa {BTN_TICKETS} para ver tu pase de abordaje.",
        )
        return
    _awaiting_contact.add(chat_id)
    await _reply(
        update.message,
        "🚌 <b>Bienvenido al bot de abordaje</b>\n\n"
        "Tu cuenta no está vinculada. Ingresa tu <b>número de celular a registrar</b> "
        "(botón de contacto) o envíalo como texto.",
        parse_mode="HTML",
        reply_markup=contact_menu(),
    )


async def _link(phone: str, user_id: str, message) -> None:
    phone = phone.strip().replace(" ", "").replace("+", "")
    if not phone.isdigit() or len(phone) < 7:
        await _reply(message, "Envía un celular válido (solo dígitos sin espacios).")
        return
    count = await asyncio.to_thread(ticket_service.link_telegram, phone, user_id)
    _awaiting_contact.discard(message.chat_id)
    if count:
        await _show_main_menu(
            message,
            f"✅ Vinculada ({count} ticket(s)).\n\n"
            f"Elige {BTN_TICKETS} para ver tu pase.",
        )
    else:
        await _show_main_menu(
            message,
            "✖ No encontré tickets para este número de celular. Contacta a la empresa.",
        )


async def on_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    contact = update.message.contact
    if str(contact.user_id) != str(update.effective_user.id):
        await _reply(update.message, "Comparte tu propio contacto, por favor.")
        return
    chat_id = update.message.chat_id
    data = _tksol.get(chat_id)
    if data and data.get("step") == "phone":
        # Dentro de /solicitar el contacto compartido aporta el celular
        # (autorización de Telegram) y el id de Telegram del usuario.
        phone = _normalize_phone(contact.phone_number)
        if not phone:
            await _reply(update.message, "Envía un celular válido (solo dígitos sin espacios).")
            return
        user_id = str(update.effective_user.id)
        data["phone"] = phone
        data["telegram_user_id"] = user_id
        # Vincula también los tickets históricos de ese celular.
        try:
            await asyncio.to_thread(ticket_service.link_telegram, phone, user_id)
        except OdooClientError:
            logger.warning(
                "No se pudieron vincular tickets históricos del celular %s", phone
            )
        # Editar celular desde el resumen vuelve al resumen.
        if data.pop("edit_return", None) == "summary":
            data["step"] = "summary"
            await _tksol_summary(update.message, chat_id)
            return
        data["step"] = "r1_origin"
        await _reply(update.message, _STEP_PROMPTS["r1_origin"], parse_mode="HTML")
        return
    await _link(contact.phone_number, str(update.effective_user.id), update.message)


def _normalize_phone(raw: str) -> str | None:
    phone = (raw or "").strip().replace(" ", "").replace("+", "")
    if phone.isdigit() and len(phone) >= 7:
        return phone
    return None


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    chat_id = update.message.chat_id

    # Botones del menú → mismos handlers que los comandos
    action = MENU_ACTIONS.get(text)
    if action:
        if action != "solicitar":
            _tksol.pop(chat_id, None)
        if action == "ticket":
            _awaiting_contact.discard(chat_id)
            await cmd_ticket(update, context)
            return
        if action == "cancelar":
            _awaiting_contact.discard(chat_id)
            await cmd_cancelar(update, context)
            return
        if action == "solicitar":
            await cmd_solicitar(update, context)
            return
        if action == "iniciar":
            # Re-vincular / estado inicial
            await cmd_start(update, context)
            return
        if action == "ayuda":
            await cmd_ayuda(update, context)
            return
        if action == "limpiar":
            await cmd_limpiar(update, context)
            return

    if chat_id in _tksol:
        await _tksol_text(update)
        return

    if chat_id in _awaiting_contact:
        await _link(text, str(update.effective_user.id), update.message)
        return

    await _reply(
        update.message,
        "Elige una opción del menú inferior 👇",
        reply_markup=main_menu(),
        resize_keyboard=True,
    )


# ---------------------------------------------------------------------------
# /solicitar -> alta de tickets (nombre, celular, rutas, QR)
# ---------------------------------------------------------------------------

async def cmd_solicitar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reinicia el flujo guiado de alta de tickets."""
    chat_id = update.effective_chat.id
    _awaiting_contact.discard(chat_id)
    _tksol[chat_id] = {"step": "name"}
    await _reply(update.message, _NAME_PROMPT, parse_mode="HTML", reply_markup=main_menu())


async def _tksol_cancel(message, chat_id: int) -> None:
    _tksol.pop(chat_id, None)
    await _reply(message, "✖ Solicitud cancelada.", reply_markup=main_menu())


def _tksol_current(data: dict[str, Any], step: str) -> str:
    """Valor ya capturado para el paso (para mostrarlo al retroceder)."""
    if step in _ROUTE_STEPS:
        picked = data.get(_ROUTE_STEPS[step][1])
        return picked[1] if isinstance(picked, tuple) and len(picked) == 2 else ""
    if step == "name":
        return str(data.get("name") or "")
    if step == "phone":
        return str(data.get("phone") or "")
    return ""


def _ask_r2_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➕ Añadir segunda ruta", callback_data="tksol:yes2")],
            [InlineKeyboardButton("➡️ No, solo la Ruta 1", callback_data="tksol:no2")],
            [InlineKeyboardButton("⬅️ Atrás", callback_data="tksol:back"),
             InlineKeyboardButton("✖ Cancelar", callback_data="tksol:cancel")],
        ]
    )


async def _tksol_routes(message, step: str, text: str) -> None:
    """Muestra el catálogo (búsqueda por texto) como botones inline."""
    chat_id = message.chat_id
    data = _tksol[chat_id]
    try:
        routes = await asyncio.to_thread(ticket_service.search_routes, text or "", 8)
    except OdooClientError:
        await _reply(message, "✖ No pude consultar el catálogo de rutas. Intenta de nuevo.")
        return
    if not routes:
        await _reply(
            message,
            f"✖ No encontré rutas con «{html_escape(text or '')}».\n"
            "Escribe <b>otro texto</b> (ciudad o municipio) para buscar de nuevo.",
            parse_mode="HTML",
        )
        return
    data["routes"] = {str(r["id"]): r["name"] for r in routes}
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(r["name"], callback_data=f"tksol:route:{r['id']}")]
            for r in routes]
        + [[InlineKeyboardButton("🔄 Otra búsqueda", callback_data="tksol:search")]]
        + [[InlineKeyboardButton("⬅️ Atrás", callback_data="tksol:back"),
            InlineKeyboardButton("✖ Cancelar", callback_data="tksol:cancel")]]
    )
    current = _tksol_current(data, step)
    hint = f"\n<i>Actual: {html_escape(current)}</i>" if current else ""
    await _reply(
        message,
        f"{_STEP_PROMPTS[step]}{hint}\n\n"
        "Elige una opción del listado o <b>escribe otro texto</b> "
        "para buscar de nuevo:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def _tksol_prompt_step(message, chat_id: int, step: str) -> None:
    """Vuelve a preguntar un paso («⬅️ Atrás» / «✏️ Editar»)."""
    data = _tksol[chat_id]
    if step in _ROUTE_STEPS:
        current = _tksol_current(data, step)
        hint = f"\n<i>Actual: {html_escape(current)}</i>" if current else ""
        await _reply(
            message, f"{_STEP_PROMPTS[step]}{hint}", parse_mode="HTML"
        )
        return
    if step == "name":
        current = _tksol_current(data, step)
        hint = f"\n<i>Actual: {html_escape(current)}</i>" if current else ""
        await _reply(message, f"{_NAME_PROMPT}{hint}", parse_mode="HTML", reply_markup=main_menu())
        return
    if step == "phone":
        current = _tksol_current(data, step)
        hint = f"\n<i>Actual: {html_escape(current)}</i>" if current else ""
        await _reply(message, f"{_PHONE_PROMPT}{hint}", parse_mode="HTML", reply_markup=contact_menu())
        return
    if step == "ask_r2":
        await _reply(
            message,
            "¿El pasajero tiene una <b>segunda ruta</b>?",
            parse_mode="HTML",
            reply_markup=_ask_r2_keyboard(),
        )
        return
    await _reply(message, "Usa los botones de la opción 👇")


async def _tksol_summary(message, chat_id: int) -> None:
    data = _tksol[chat_id]
    data.pop("edit_return", None)
    has_r2 = bool(data.get("origin2") and data.get("dest2"))
    lines = [
        "📋 <b>Resumen de la solicitud</b>",
        "",
        f"👤 Pasajero: {html_escape(data['name'].upper())}",
        f"📱 Celular: {data['phone']}",
        f"🗺 Ruta 1: {html_escape(data['origin1'][1])} → {html_escape(data['dest1'][1])}",
    ]
    if has_r2:
        lines.append(
            f"🗺 Ruta 2: {html_escape(data['origin2'][1])} → {html_escape(data['dest2'][1])}"
        )
    lines += ["", "¿Crear el ticket?", "<i>¿Algo mal? Edita cualquier dato antes de confirmar.</i>"]
    edit_row_r1 = [InlineKeyboardButton("✏️ Editar nombre", callback_data="tksol:edit:name"),
                   InlineKeyboardButton("✏️ Editar celular", callback_data="tksol:edit:phone")]
    edit_row_r2 = [InlineKeyboardButton("✏️ Editar Ruta 1", callback_data="tksol:edit:r1")]
    if has_r2:
        edit_row_r2.append(InlineKeyboardButton("✏️ Editar Ruta 2", callback_data="tksol:edit:r2"))
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Confirmar", callback_data="tksol:confirm"),
             InlineKeyboardButton("✖ Cancelar", callback_data="tksol:cancel")],
            edit_row_r1,
            edit_row_r2,
        ]
    )
    await _reply(message, "\n".join(lines), parse_mode="HTML", reply_markup=keyboard)


async def _tksol_text(update: Update) -> None:
    """Procesa un texto según el paso actual del flujo /solicitar."""
    chat_id = update.message.chat_id
    data = _tksol[chat_id]
    step = data["step"]
    text = (update.message.text or "").strip()

    if text.lower() in ("cancelar", "/cancelar", "✖ cancelar"):
        await _tksol_cancel(update.message, chat_id)
        return

    if step == "name":
        if len(text) < 2:
            await _reply(update.message, "Escribe el nombre completo del pasajero.")
            return
        data["name"] = text
        # Editado desde el resumen → vuelve al resumen sin repreguntar.
        if data.pop("edit_return", None) == "summary":
            data["step"] = "summary"
            await _tksol_summary(update.message, chat_id)
            return
        data["step"] = "phone"
        await _reply(update.message, _PHONE_PROMPT, parse_mode="HTML", reply_markup=contact_menu())
        return

    if step == "phone":
        phone = _normalize_phone(text)
        if not phone:
            await _reply(
                update.message,
                "Envía un celular válido (solo dígitos, ej. 3001234567) "
                "o usa el botón para compartir tu contacto.",
            )
            return
        data["phone"] = phone
        data["telegram_user_id"] = str(update.effective_user.id)
        # Vincula también los tickets históricos de ese celular.
        try:
            await asyncio.to_thread(
                ticket_service.link_telegram, phone, data["telegram_user_id"]
            )
        except OdooClientError:
            logger.warning(
                "No se pudieron vincular tickets históricos del celular %s", phone
            )
        # Editado desde el resumen → vuelve al resumen sin repreguntar rutas.
        if data.pop("edit_return", None) == "summary":
            data["step"] = "summary"
            await _tksol_summary(update.message, chat_id)
            return
        data["step"] = "r1_origin"
        await _reply(update.message, _STEP_PROMPTS["r1_origin"], parse_mode="HTML")
        return

    if step in _ROUTE_STEPS:
        await _tksol_routes(update.message, step, text)
        return

    # Paso ask_r2/summary solo avanzan por botón inline.
    await _reply(update.message, "Usa los botones de la opción 👇")


async def on_tksol_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callbacks del flujo /solicitar (rutas, retroceso, edición, confirmación)."""
    query = update.callback_query
    chat_id = query.message.chat_id
    parts = query.data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    data = _tksol.get(chat_id)

    if action == "cancel":
        await query.answer()
        _tksol.pop(chat_id, None)
        await query.edit_message_text("✖ Solicitud cancelada.")
        return

    if not data:
        await query.answer("La solicitud expiró. Usa /solicitar para empezar.", show_alert=True)
        return

    if action == "back":
        prev = _PREV_STEP.get(data.get("step", ""))
        if not prev:
            await query.answer("Ya estás en el primer paso.", show_alert=True)
            return
        await query.answer()
        data["step"] = prev
        await query.edit_message_text("⬅️ Volvimos al paso anterior.")
        await _tksol_prompt_step(query.message, chat_id, prev)
        return

    if action == "search":
        if data.get("step") not in _ROUTE_STEPS:
            await query.answer("Esa opción ya no aplica.", show_alert=True)
            return
        await query.answer()
        await query.edit_message_text(
            "✏️ Escribe la ciudad o municipio (puedes escribir cualquier texto):"
        )
        return

    if action == "edit" and data.get("step") == "summary":
        field = parts[2] if len(parts) > 2 else ""
        target = _EDIT_STEPS.get(field)
        if not target:
            await query.answer("Esa opción ya no aplica.", show_alert=True)
            return
        if field == "r2" and not (data.get("origin2") and data.get("dest2")):
            await query.answer("Esta solicitud no tiene Ruta 2.", show_alert=True)
            return
        await query.answer()
        data["step"] = target
        # Al terminar de editar se vuelve al resumen (nombre/celular siempre;
        # Ruta 1 sólo si ya existe Ruta 2, para no repreguntar «¿2ª ruta?»).
        if field in ("name", "phone"):
            data["edit_return"] = "summary"
        elif field == "r1" and data.get("origin2") and data.get("dest2"):
            data["edit_return"] = "summary"
        labels = {"name": "el nombre", "phone": "el celular",
                  "r1": "la Ruta 1", "r2": "la Ruta 2"}
        await query.edit_message_text(f"✏️ Editando {labels.get(field, 'el dato')}.")
        await _tksol_prompt_step(query.message, chat_id, target)
        return

    if action == "route":
        step = data.get("step", "")
        # Solo valen ids del último catálogo mostrado: un botón viejo (u otra
        # selección ya superada) no debe asignar una ruta arbitraria.
        routes = data.get("routes") or {}
        route_id = parts[2] if parts[2].isdigit() else ""
        name = routes.get(route_id)
        if step not in _ROUTE_STEPS or name is None:
            await query.answer("Esa selección ya no aplica.", show_alert=True)
            return
        await query.answer()
        route_id = int(route_id)
        field = _ROUTE_STEPS[step][1]
        data[field] = (route_id, name)
        await query.edit_message_text(f"✔ {name}")
        nxt = _AFTER_ROUTE[step]
        # Editando Ruta 1 con Ruta 2 ya existente → de vuelta al resumen.
        if (
            data.get("edit_return") == "summary"
            and nxt == "ask_r2"
            and data.get("origin2")
            and data.get("dest2")
        ):
            nxt = "summary"
        data["step"] = nxt
        if nxt == "ask_r2":
            await _reply(
                query.message,
                "¿El pasajero tiene una <b>segunda ruta</b>?",
                parse_mode="HTML",
                reply_markup=_ask_r2_keyboard(),
            )
        elif nxt == "summary":
            await _tksol_summary(query.message, chat_id)
        else:
            await _reply(query.message, _STEP_PROMPTS[nxt], parse_mode="HTML")
        return

    if action == "yes2" and data.get("step") == "ask_r2":
        await query.answer()
        data["step"] = "r2_origin"
        await query.edit_message_text("➕ Segunda ruta añadida.")
        await _reply(query.message, _STEP_PROMPTS["r2_origin"], parse_mode="HTML")
        return

    if action == "no2" and data.get("step") == "ask_r2":
        await query.answer()
        data["step"] = "summary"
        await query.edit_message_text("Ruta 1 única.")
        await _tksol_summary(query.message, chat_id)
        return

    if action == "confirm" and data.get("step") == "summary":
        await _tksol_create(update, data)
        return

    await query.answer("Esa opción ya no aplica.", show_alert=True)


async def _tksol_create(update: Update, data: dict[str, Any]) -> None:
    """Crea, aprueba y vincula el ticket; envía el pase con QR.

    El estado solo se retira al concluir con éxito: si Odoo falla, el
    usuario puede pulsar «Confirmar» de nuevo sin reiniciar el flujo.
    Un flag ``creating`` evita doble envío concurrente.
    """
    query = update.callback_query
    chat_id = query.message.chat_id
    if data.get("creating"):
        await query.answer("Ya se está creando el ticket…", show_alert=False)
        return
    data["creating"] = True
    values: dict[str, Any] = {
        "passenger_name": data["name"],
        "cellphone": data["phone"],
        "route_origin": data["origin1"][0],
        "route_destination": data["dest1"][0],
    }
    if data.get("origin2") and data.get("dest2"):
        values["route_origin_2"] = data["origin2"][0]
        values["route_destination_2"] = data["dest2"][0]

    try:
        ticket_id = await asyncio.to_thread(ticket_service.create_ticket, values)
        # Queda en "En Solicitud". Se vincula el chat para que, al asignar el
        # vehículo en Odoo, el notify (automático) envíe el pase aquí.
        await asyncio.to_thread(
            ticket_service.link_after_approve, ticket_id, str(update.effective_user.id)
        )
        ticket = await asyncio.to_thread(ticket_service.get_ticket, ticket_id)
    except OdooClientError:
        data.pop("creating", None)
        logger.warning("No fue posible crear el ticket para chat %s", chat_id)
        await query.answer(
            "No fue posible crear el ticket. Pulsa «Confirmar» de nuevo.",
            show_alert=True,
        )
        return

    # Éxito: retira el estado de inmediato (un segundo toque no duplica).
    _tksol.pop(chat_id, None)

    if not ticket:
        await query.answer("El ticket no quedó disponible. Contacta a la empresa.", show_alert=True)
        return

    await query.answer()
    await query.edit_message_text(
        f"✅ Ticket <b>{html_escape(ticket['ticket_number'])}</b> creado: <b>En Solicitud</b>.\n"
        "El pase de abordaje se enviará aquí automáticamente cuando la empresa "
        "asigne el vehículo.",
        parse_mode="HTML",
    )
    await _reply(query.message, "¿Qué deseas hacer ahora?", reply_markup=main_menu())

# ---------------------------------------------------------------------------
# /ticket -> pase de abordaje
# ---------------------------------------------------------------------------

async def cmd_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_user.id)
    tickets = await asyncio.to_thread(ticket_service.list_tickets_for_user, user_id)
    if not tickets:
        await _reply(
            update.message,
            f"No tienes tickets. Vincula tu cuenta con {BTN_VINCULAR}.",
            reply_markup=main_menu(),
        )
        return
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"{t['ticket_number']} · {t['status_label']}", callback_data=f"ticket:{t['id']}")]
            for t in tickets
        ]
    )
    await _reply(update.message, "🎫 Selecciona tu ticket:", reply_markup=keyboard)

async def on_ticket_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    ticket_id = int(query.data.split(":", 1)[1])
    ticket = await asyncio.to_thread(ticket_service.get_ticket, ticket_id)
    if not ticket:
        await _reply(query.message, "✖ Ticket no encontrado.")
        return
    if ticket["status"] == "solicitud":
        await _reply(
            query.message,
            f"El ticket {ticket['ticket_number']} está <b>En Solicitud</b>: "
            "pendiente de asignación de vehículo. El pase se enviará automáticamente "
            "cuando la empresa lo asigne.",
            parse_mode="HTML",
        )
        return
    if ticket["status"] not in ("enviado", "abordado1"):
        await _reply(
            query.message,
            f"El ticket {ticket['ticket_number']} está en estado "
            f"{ticket['status_label']}; solo los tickets <b>Enviado</b> o "
            f"<b>Abordado 1ra Ruta</b> generan pase de abordaje.",
            parse_mode="HTML",
        )
        return
    png = await asyncio.to_thread(qr_service.generate_boarding_pass, ticket)
    await _reply_photo(
        query.message, photo=png, caption=formatting.ticket_caption(ticket), parse_mode="HTML"
    )

# ---------------------------------------------------------------------------
# /cancelar -> cancelar un ticket sin abordar (estado enviado)
# ---------------------------------------------------------------------------

async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lista los tickets sin abordar del usuario y ofrece cancelar."""
    chat_id = update.effective_chat.id
    _tksol.pop(chat_id, None)
    user_id = str(update.effective_user.id)
    tickets = await asyncio.to_thread(ticket_service.list_tickets_for_user, user_id)
    cancelables = [t for t in tickets if t.get("status") in CANCELABLE_STATUSES]
    if not cancelables:
        await _reply(
            update.message,
            "No tienes tickets sin abordar para cancelar.",
            reply_markup=main_menu(),
        )
        return
    if len(cancelables) == 1:
        await _cancelar_ask(update.message, cancelables[0])
        return
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(
                f"{t['ticket_number']} · {t['route_origin']} → {t['route_destination']}",
                callback_data=f"cancel:{t['id']}",
            )]
            for t in cancelables
        ]
    )
    await _reply(update.message, "❌ Selecciona el ticket a cancelar:", reply_markup=keyboard)


def _cancelar_summary(ticket: dict[str, Any]) -> str:
    return (
        "❌ <b>¿Cancelar este ticket?</b>\n\n"
        f"🎫 N°: {_esc_ticket_number(ticket)}\n"
        f"👤 Pasajero: {html_escape(str(ticket.get('passenger_name') or '—'))}\n"
        f"🗺 Ruta: {html_escape(str(ticket.get('route_origin') or '—'))} → "
        f"{html_escape(str(ticket.get('route_destination') or '—'))}\n\n"
        + (
            "El ticket quedará cancelado."
            if ticket.get("status") == "solicitud"
            else "El pasajero no podrá abordar con el pase QR ya emitido."
        )
    )


def _esc_ticket_number(ticket: dict[str, Any]) -> str:
    return html_escape(str(ticket.get("ticket_number") or "—"))


def _cancelar_confirm_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⚠️ Sí, cancelar", callback_data=f"cancel:ok:{ticket_id}")],
            [InlineKeyboardButton("↩️ No", callback_data="cancel:no")],
        ]
    )


async def _cancelar_ask(message, ticket: dict[str, Any]) -> None:
    await _reply(
        message,
        _cancelar_summary(ticket),
        parse_mode="HTML",
        reply_markup=_cancelar_confirm_keyboard(ticket["id"]),
    )


async def on_cancel_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callbacks del flujo de cancelación: cancel:<id>, cancel:ok:<id>, cancel:no."""
    query = update.callback_query
    parts = query.data.split(":")

    if parts[-1] == "no":
        await query.answer()
        await query.edit_message_text("✔ Cancelación descartada.")
        return

    if len(parts) == 3 and parts[1] == "ok":
        await _cancelar_do(query, int(parts[2]))
        return

    if len(parts) == 2 and parts[1].isdigit():
        ticket = await asyncio.to_thread(ticket_service.get_ticket, int(parts[1]))
        if not ticket:
            await query.answer("Ticket no encontrado.", show_alert=True)
            return
        # Botón viejo: el ticket ya no está en estado cancelable.
        if ticket.get("status") not in CANCELABLE_STATUSES:
            await query.answer(
                f"El ticket {ticket['ticket_number']} ya no puede cancelarse "
                f"(estado: {ticket.get('status_label', ticket.get('status'))}).",
                show_alert=True,
            )
            return
        await query.answer()
        await query.edit_message_text(
            _cancelar_summary(ticket),
            parse_mode="HTML",
            reply_markup=_cancelar_confirm_keyboard(ticket["id"]),
        )
        return

    await query.answer("Esa opción ya no aplica.", show_alert=True)


async def _cancelar_do(query, ticket_id: int) -> None:
    try:
        await asyncio.to_thread(ticket_service.cancel_ticket, ticket_id)
    except Exception as exc:  # noqa: BLE001 — se traduce al usuario
        logger.warning("action_cancel falló para ticket %s: %s", ticket_id, exc)
        await query.answer("No se pudo cancelar el ticket.", show_alert=True)
        return
    ticket = await asyncio.to_thread(ticket_service.get_ticket, ticket_id)
    if ticket:
        await query.edit_message_text(
            f"✅ Ticket N° {ticket['ticket_number']}, ha sido cancelado."
        )
    else:
        await query.edit_message_text("✅ Ticket cancelado.")


# ---------------------------------------------------------------------------
# /limpiar -> borra los mensajes del bot (48 h) y reinicia el estado
# ---------------------------------------------------------------------------

async def cmd_limpiar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Limpia la conversación: mensajes del bot + estado de los flujos.

    Telegram sólo permite al bot borrar sus propios mensajes de las últimas
    48 h (los del usuario no se pueden borrar desde el bot). Los ids se
    registran en ``_sent_ids`` al enviar cada mensaje.
    """
    chat_id = update.effective_chat.id
    _tksol.pop(chat_id, None)
    _awaiting_contact.discard(chat_id)
    ids = _sent_ids.pop(chat_id, [])
    deleted = 0
    for start in range(0, len(ids), 100):
        batch = ids[start:start + 100]
        try:
            ok = await context.bot.delete_messages(chat_id, batch)
        except Exception as exc:  # noqa: BLE001 — 48 h / mensajes ya idos
            logger.info("No se pudieron borrar %d mensaje(s) del chat %s: %s",
                        len(batch), chat_id, exc)
            continue
        if ok:
            deleted += len(batch)
    if deleted:
        text = (
            f"🧹 <b>Chat limpio</b>: borré {deleted} mensaje(s) del bot "
            "(Telegram sólo permite borrar los últimos 48 h) y reinicié "
            "el flujo. Tus mensajes permanecen en el chat."
        )
    else:
        text = (
            "🧹 <b>Flujo reiniciado</b>. No había mensajes del bot recientes "
            "para borrar (sólo se pueden borrar los mensajes de las últimas 48 h); "
            "tus mensajes permanecen en el chat."
        )
    await _reply(update.message, text, parse_mode="HTML", reply_markup=main_menu())
