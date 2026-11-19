"""Pruebas de los handlers de Telegram con updates simulados (sin red)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import telegram_app
from app.odoo_client import OdooClientError


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_sent_ids():
    telegram_app._sent_ids.clear()
    yield
    telegram_app._sent_ids.clear()


class FakeMessage:
    def __init__(self, text=None, contact=None, chat_id=1):
        self.text = text
        self.contact = contact
        self.chat_id = chat_id
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)

    async def reply_photo(self, photo, caption=None, **kwargs):
        self.replies.append(("photo", caption))


def make_update(chat_id=1, user_id=100, text=None, contact=None, callback_data=None):
    user = SimpleNamespace(id=user_id, first_name="Ana")
    message = FakeMessage(text=text, contact=contact, chat_id=chat_id)
    if callback_data is not None:
        message.reply_text = AsyncMock()
        message.reply_photo = AsyncMock()
        callback_query = SimpleNamespace(
            data=callback_data,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            message=message,
        )
        return SimpleNamespace(
            effective_user=user, effective_chat=SimpleNamespace(id=chat_id),
            message=message, callback_query=callback_query,
        )
    return SimpleNamespace(
        effective_user=user,
        effective_chat=SimpleNamespace(id=chat_id),
        message=message,
        callback_query=None,
    )


# ---------------------------------------------------------------------------
# /start + vinculación
# ---------------------------------------------------------------------------

def test_start_ya_vinculado():
    update = make_update(text="/start")
    with patch.object(telegram_app.ticket_service, "list_tickets_for_user", return_value=[{"id": 1}]):
        run(telegram_app.cmd_start(update, None))
    assert any("vinculada" in r for r in update.message.replies)


def test_start_pide_contacto():
    update = make_update(text="/start")
    with patch.object(telegram_app.ticket_service, "list_tickets_for_user", return_value=[]):
        run(telegram_app.cmd_start(update, None))
    assert 1 in telegram_app._awaiting_contact
    assert any("celular a registrar" in r for r in update.message.replies)


def test_on_text_vincula_celular():
    update = make_update(text="3001234567")
    telegram_app._awaiting_contact.add(1)
    with patch.object(telegram_app.ticket_service, "link_telegram", return_value=2) as mock_link:
        run(telegram_app.on_text(update, None))
    mock_link.assert_called_once_with("3001234567", "100")
    assert 1 not in telegram_app._awaiting_contact
    assert any("Vinculada" in r for r in update.message.replies)


def test_on_text_celular_invalido():
    update = make_update(text="no-es-celular")
    telegram_app._awaiting_contact.add(1)
    with patch.object(telegram_app.ticket_service, "link_telegram") as mock_link:
        run(telegram_app.on_text(update, None))
    mock_link.assert_not_called()


def test_on_contact_propio():
    contact = SimpleNamespace(phone_number="+57 300 999 8877", user_id=100)
    update = make_update(contact=contact)
    telegram_app._awaiting_contact.add(1)
    with patch.object(telegram_app.ticket_service, "link_telegram", return_value=1) as mock_link:
        run(telegram_app.on_contact(update, None))
    # el + y espacios se normalizan a dígitos
    mock_link.assert_called_once_with("573009998877", "100")


# ---------------------------------------------------------------------------
# /ticket -> pase
# ---------------------------------------------------------------------------

def test_cmd_ticket_lista():
    update = make_update(text="/ticket")
    tickets = [{"id": 5, "ticket_number": "Abordaje_X", "status_label": "Enviado"}]
    with patch.object(telegram_app.ticket_service, "list_tickets_for_user", return_value=tickets):
        run(telegram_app.cmd_ticket(update, None))
    assert update.message.replies, "debe responder con el teclado inline"


def test_cmd_ticket_vacio():
    update = make_update(text="/ticket")
    with patch.object(telegram_app.ticket_service, "list_tickets_for_user", return_value=[]):
        run(telegram_app.cmd_ticket(update, None))
    assert any("No tienes tickets" in r for r in update.message.replies)


def test_on_ticket_choice_envia_pase(sample_ticket):
    update = make_update(callback_data="ticket:42")
    with (
        patch.object(telegram_app.ticket_service, "get_ticket", return_value=sample_ticket),
        patch.object(telegram_app.qr_service, "generate_boarding_pass", return_value=MagicMock()),
    ):
        run(telegram_app.on_ticket_choice(update, None))
    update.message.reply_photo.assert_awaited_once()
    caption = update.message.reply_photo.call_args.kwargs["caption"]
    assert "Maria Lopez" in caption


def test_on_ticket_choice_no_enviado():
    update = make_update(callback_data="ticket:42")
    ticket = {"id": 42, "ticket_number": "T", "status": "solicitud", "status_label": "En Solicitud"}
    with patch.object(telegram_app.ticket_service, "get_ticket", return_value=ticket):
        run(telegram_app.on_ticket_choice(update, None))
    update.message.reply_photo.assert_not_awaited()


# ---------------------------------------------------------------------------
# /cancelar -> cancelar un ticket sin abordar
# ---------------------------------------------------------------------------

def _cancelable(**overrides):
    base = {
        "id": 42,
        "ticket_number": "Abordaje_ABCD1234",
        "passenger_name": "Maria Lopez",
        "status": "enviado",
        "status_label": "Enviado",
        "route_origin": "Bogota",
        "route_destination": "Medellin",
    }
    base.update(overrides)
    return base


def test_cmd_cancelar_lista_varios():
    update = make_update(text="/cancelar")
    tickets = [
        _cancelable(id=42),
        _cancelable(id=43, ticket_number="Abordaje_EFGH", route_origin="Cali"),
    ]
    with patch.object(telegram_app.ticket_service, "list_tickets_for_user", return_value=tickets):
        run(telegram_app.cmd_cancelar(update, None))
    assert any("Selecciona el ticket a cancelar" in r for r in update.message.replies)


def test_cmd_cancelar_uno_directo_a_confirmacion():
    update = make_update(text="/cancelar")
    with patch.object(
        telegram_app.ticket_service, "list_tickets_for_user", return_value=[_cancelable()]
    ):
        run(telegram_app.cmd_cancelar(update, None))
    assert any("¿Cancelar este ticket?" in r for r in update.message.replies)


def test_cmd_cancelar_sin_cancelables():
    update = make_update(text="/cancelar")
    tickets = [_cancelable(status="abordado2", status_label="Abordado 2da Ruta")]
    with patch.object(telegram_app.ticket_service, "list_tickets_for_user", return_value=tickets):
        run(telegram_app.cmd_cancelar(update, None))
    assert any("No tienes tickets sin abordar" in r for r in update.message.replies)


def test_on_cancel_choice_muestra_confirmacion():
    update = make_update(callback_data="cancel:42")
    with patch.object(telegram_app.ticket_service, "get_ticket", return_value=_cancelable()):
        run(telegram_app.on_cancel_choice(update, None))
    edit = update.callback_query.edit_message_text
    edit.assert_awaited_once()
    assert "¿Cancelar este ticket?" in edit.call_args.args[0]
    keyboard = edit.call_args.kwargs["reply_markup"]
    data = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "cancel:ok:42" in data
    assert "cancel:no" in data


def test_on_cancel_choice_estado_viejo_alerta():
    """Un botón viejo no cancela un ticket que ya cambió de estado."""
    update = make_update(callback_data="cancel:42")
    ticket = _cancelable(status="abordado1", status_label="Abordado 1ra Ruta")
    with patch.object(telegram_app.ticket_service, "get_ticket", return_value=ticket):
        run(telegram_app.on_cancel_choice(update, None))
    update.callback_query.answer.assert_awaited_with(
        "El ticket Abordaje_ABCD1234 ya no puede cancelarse "
        "(estado: Abordado 1ra Ruta).",
        show_alert=True,
    )
    update.callback_query.edit_message_text.assert_not_awaited()


def test_on_cancel_choice_ticket_inexistente():
    update = make_update(callback_data="cancel:999")
    with patch.object(telegram_app.ticket_service, "get_ticket", return_value=None):
        run(telegram_app.on_cancel_choice(update, None))
    update.callback_query.answer.assert_awaited_with("Ticket no encontrado.", show_alert=True)


def test_on_cancel_choice_ok_cancela():
    update = make_update(callback_data="cancel:ok:42")
    cancelled = _cancelable(status="cancelado", status_label="Cancelado")
    with (
        patch.object(telegram_app.ticket_service, "cancel_ticket") as mock_cancel,
        patch.object(telegram_app.ticket_service, "get_ticket", return_value=cancelled),
    ):
        run(telegram_app.on_cancel_choice(update, None))
    mock_cancel.assert_called_once_with(42)
    edit = update.callback_query.edit_message_text
    edit.assert_awaited_once()
    assert "✅ Ticket N° Abordaje_ABCD1234, ha sido cancelado." in edit.call_args.args[0]


def test_on_cancel_choice_ok_fallo():
    update = make_update(callback_data="cancel:ok:42")
    with patch.object(
        telegram_app.ticket_service,
        "cancel_ticket",
        side_effect=OdooClientError("fault"),
    ):
        run(telegram_app.on_cancel_choice(update, None))
    update.callback_query.answer.assert_awaited_with(
        "No se pudo cancelar el ticket.", show_alert=True
    )
    update.callback_query.edit_message_text.assert_not_awaited()


def test_on_cancel_choice_no_descarta():
    update = make_update(callback_data="cancel:no")
    run(telegram_app.on_cancel_choice(update, None))
    update.callback_query.edit_message_text.assert_awaited_once_with("✔ Cancelación descartada.")


# ---------------------------------------------------------------------------
# Menú (botones del teclado) — sin escribir comandos
# ---------------------------------------------------------------------------

def test_menu_tickets_botón():
    update = make_update(text=telegram_app.BTN_TICKETS)
    tickets = [{"id": 7, "ticket_number": "Abordaje_M", "status_label": "Enviado"}]
    with patch.object(telegram_app.ticket_service, "list_tickets_for_user", return_value=tickets):
        run(telegram_app.on_text(update, None))
    assert update.message.replies, "el botón de tickets debe responder"


def test_menu_cancelar_botón():
    update = make_update(text=telegram_app.BTN_CANCELAR)
    with patch.object(
        telegram_app.ticket_service, "list_tickets_for_user", return_value=[_cancelable()]
    ):
        run(telegram_app.on_text(update, None))
    assert any("¿Cancelar este ticket?" in r for r in update.message.replies)


def test_menu_ayuda_botón():
    update = make_update(text=telegram_app.BTN_AYUDA)
    run(telegram_app.on_text(update, None))
    assert any("Cómo usar" in r for r in update.message.replies)


def test_menu_vincular_botón_pide_contacto():
    update = make_update(text=telegram_app.BTN_VINCULAR)
    with patch.object(telegram_app.ticket_service, "list_tickets_for_user", return_value=[]):
        run(telegram_app.on_text(update, None))
    assert 1 in telegram_app._awaiting_contact


def test_on_text_desconocido_pide_menu():
    telegram_app._awaiting_contact.clear()
    update = make_update(text="hola")
    run(telegram_app.on_text(update, None))
    assert any("menú" in r.lower() for r in update.message.replies)


# ---------------------------------------------------------------------------
# /limpiar -> borra mensajes del bot y reinicia el estado
# ---------------------------------------------------------------------------

def _context_with_bot():
    context = MagicMock()
    context.bot.delete_messages = AsyncMock(return_value=True)
    return context


def test_track_registra_solo_ids_enteros():
    telegram_app._track(1, 42)
    telegram_app._track(1, 43)
    telegram_app._track(1, "no-entero")
    telegram_app._track(1, None)
    assert telegram_app._sent_ids[1] == [42, 43]


def test_track_respeta_tope_por_chat():
    for i in range(telegram_app._SENT_IDS_MAX + 10):
        telegram_app._track(7, i)
    ids = telegram_app._sent_ids[7]
    assert len(ids) == telegram_app._SENT_IDS_MAX
    assert ids[-1] == telegram_app._SENT_IDS_MAX + 9


def test_cmd_limpiar_borra_mensajes_y_reinicia():
    telegram_app._tksol[1] = {"step": "name"}
    telegram_app._awaiting_contact.add(1)
    telegram_app._sent_ids[1] = [11, 12, 13]
    context = _context_with_bot()
    update = make_update(text="/limpiar")
    run(telegram_app.cmd_limpiar(update, context))
    context.bot.delete_messages.assert_awaited_once_with(1, [11, 12, 13])
    assert 1 not in telegram_app._tksol
    assert 1 not in telegram_app._awaiting_contact
    assert 1 not in telegram_app._sent_ids
    assert any("Chat limpio" in r for r in update.message.replies)
    assert any("48 h" in r for r in update.message.replies)


def test_cmd_limpiar_en_lotes_de_100():
    telegram_app._sent_ids[1] = list(range(1, 201))  # 200 ids → 2 lotes
    context = _context_with_bot()
    update = make_update(text="/limpiar")
    run(telegram_app.cmd_limpiar(update, context))
    assert context.bot.delete_messages.await_count == 2
    chats = [c.args for c in context.bot.delete_messages.await_args_list]
    assert chats == [(1, list(range(1, 101))), (1, list(range(101, 201)))]


def test_cmd_limpiar_sin_mensajes_reinicia():
    telegram_app._tksol[1] = {"step": "summary"}
    context = _context_with_bot()
    update = make_update(text="/limpiar")
    run(telegram_app.cmd_limpiar(update, context))
    context.bot.delete_messages.assert_not_awaited()
    assert 1 not in telegram_app._tksol
    assert any("Flujo reiniciado" in r for r in update.message.replies)


def test_cmd_limpiar_fallo_de_borrado_no_lanza():
    telegram_app._sent_ids[1] = [11]
    context = _context_with_bot()
    context.bot.delete_messages = AsyncMock(side_effect=Exception("fueron 48 h"))
    update = make_update(text="/limpiar")
    run(telegram_app.cmd_limpiar(update, context))
    assert 1 not in telegram_app._sent_ids
    assert any("Flujo reiniciado" in r for r in update.message.replies)


def test_boton_limpiar_del_menu():
    telegram_app._sent_ids[1] = [21]
    context = _context_with_bot()
    update = make_update(text=telegram_app.BTN_LIMPIAR)
    run(telegram_app.on_text(update, context))
    context.bot.delete_messages.assert_awaited_once_with(1, [21])
    assert any("limpio" in r for r in update.message.replies)


def test_comando_limpiar_publicado():
    assert any(c.command == "limpiar" for c in telegram_app.BOT_COMMANDS)


def test_reply_registra_id_en_sent_ids():
    class MsgWithId:
        chat_id = 5

        async def reply_text(self, text, **kwargs):
            return SimpleNamespace(message_id=77)

    run(telegram_app._reply(MsgWithId(), "hola"))
    assert telegram_app._sent_ids[5] == [77]


def test_cmd_cancelar_incluye_tickets_en_solicitud():
    update = make_update(text="/cancelar")
    pendiente = _cancelable(status="solicitud", status_label="En Solicitud")
    with patch.object(
        telegram_app.ticket_service, "list_tickets_for_user", return_value=[pendiente]
    ):
        run(telegram_app.cmd_cancelar(update, None))
    assert any("¿Cancelar este ticket?" in r for r in update.message.replies)


def test_on_cancel_choice_solicitud_puede_cancelarse():
    update = make_update(callback_data="cancel:42")
    pendiente = _cancelable(status="solicitud", status_label="En Solicitud")
    with patch.object(telegram_app.ticket_service, "get_ticket", return_value=pendiente):
        run(telegram_app.on_cancel_choice(update, None))
    edit = update.callback_query.edit_message_text
    assert "¿Cancelar este ticket?" in edit.call_args.args[0]
    assert "El ticket quedará cancelado." in edit.call_args.args[0]


def test_on_ticket_choice_solicitud_indica_pendiente_de_vehiculo():
    update = make_update(callback_data="ticket:42")
    ticket = {"id": 42, "ticket_number": "T", "status": "solicitud", "status_label": "En Solicitud"}
    with patch.object(telegram_app.ticket_service, "get_ticket", return_value=ticket):
        run(telegram_app.on_ticket_choice(update, None))
    update.message.reply_photo.assert_not_awaited()
    update.message.reply_text.assert_awaited_once()
    texto = update.message.reply_text.call_args.args[0]
    assert "pendiente de asignación de vehículo" in texto


def test_boarding_validated_ruta_2_pendiente():
    from app import formatting
    ticket = {"ticket_number": "T-1", "status": "abordado1", "route_origin_2": "A", "route_destination_2": "B"}
    text = formatting.boarding_validated(ticket)
    assert "T-1" in text and "validado y aprobado" in text
    assert "Falta la validación de la 2.ª ruta" in text


def test_boarding_validated_ruta_final():
    from app import formatting
    text = formatting.boarding_validated({"ticket_number": "T-1", "status": "abordado2"})
    assert "Abordaje de la 2.ª ruta registrado" in text
    assert "Falta" not in text
