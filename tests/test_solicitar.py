"""Pruebas del flujo /solicitar (alta de tickets)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import telegram_app
from app.odoo_client import OdooClientError


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean_state():
    telegram_app._tksol.clear()
    telegram_app._awaiting_contact.clear()
    telegram_app._sent_ids.clear()
    yield
    telegram_app._tksol.clear()
    telegram_app._awaiting_contact.clear()
    telegram_app._sent_ids.clear()


@pytest.fixture(autouse=True)
def _stub_link_telegram():
    """Evita llamadas reales a Odoo al compartir/escribir el celular."""
    with patch.object(
        telegram_app.ticket_service, "link_telegram", return_value=0
    ) as mock:
        yield mock


class FakeMessage:
    def __init__(self, text=None, contact=None, chat_id=1):
        self.text = text
        self.contact = contact
        self.chat_id = chat_id
        self.replies = []
        self.reply_kwargs = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        self.reply_kwargs.append(kwargs)

    async def reply_photo(self, photo, caption=None, **kwargs):
        self.replies.append(("photo", caption))
        self.reply_kwargs.append(kwargs)


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


def send_text(text, chat_id=1, user_id=100):
    update = make_update(chat_id=chat_id, user_id=user_id, text=text)
    run(telegram_app.on_text(update, None))
    return update


def choose(callback_data, chat_id=1, user_id=100):
    update = make_update(chat_id=chat_id, user_id=user_id, callback_data=callback_data)
    run(telegram_app.on_tksol_choice(update, None))
    return update


def start_flow(chat_id=1):
    update = make_update(chat_id=chat_id, text="/solicitar")
    run(telegram_app.cmd_solicitar(update, None))
    return update


def pick_routes(pairs):
    """Parchea search_routes para devolver ``pairs`` [(id, nombre), ...]."""
    rows = [{"id": i, "name": n} for i, n in pairs]
    return patch.object(
        telegram_app.ticket_service, "search_routes", return_value=rows
    )


def walk_to_summary():
    """Ejecuta el flujo hasta el resumen con Ruta 1 y Ruta 2 elegidas."""
    start_flow()
    send_text("Maria Lopez")
    send_text("3001234567")
    with pick_routes([(7, "Bogota")]):
        send_text("Bogo")
    choose("tksol:route:7")
    with pick_routes([(9, "Medellin")]):
        send_text("Med")
    choose("tksol:route:9")
    choose("tksol:yes2")
    with pick_routes([(3, "Cali")]):
        send_text("Cal")
    choose("tksol:route:3")
    with pick_routes([(4, "Barranquilla")]):
        send_text("Bar")
    return choose("tksol:route:4")


# ---------------------------------------------------------------------------
# /solicitar — pasos de texto
# ---------------------------------------------------------------------------

def test_comando_solicitar_inicia_flujo():
    update = start_flow()
    assert telegram_app._tksol[1] == {"step": "name"}
    assert any("nombre completo" in r for r in update.message.replies)


def test_boton_menu_solicitar():
    send_text(telegram_app.BTN_SOLICITAR)
    assert telegram_app._tksol[1]["step"] == "name"


def test_comandos_publicados_incluyen_solicitar():
    assert any(c.command == "solicitar" for c in telegram_app.BOT_COMMANDS)


def test_nombre_avanza_a_celular():
    start_flow()
    update = send_text("Maria Lopez")
    assert telegram_app._tksol[1] == {"step": "phone", "name": "Maria Lopez"}
    assert any("celular" in r.lower() for r in update.message.replies)


def test_nombre_corto_repite():
    start_flow()
    send_text("A")
    assert telegram_app._tksol[1]["step"] == "name"


def test_celular_invalido_repite():
    start_flow()
    send_text("Maria Lopez")
    send_text("abc")
    assert telegram_app._tksol[1]["step"] == "phone"


def test_celular_valido_avanza_a_ruta(_stub_link_telegram):
    start_flow()
    send_text("Maria Lopez")
    update = send_text("300 123 4567")
    assert telegram_app._tksol[1]["step"] == "r1_origin"
    assert telegram_app._tksol[1]["phone"] == "3001234567"
    assert telegram_app._tksol[1]["telegram_user_id"] == "100"
    _stub_link_telegram.assert_called_once_with("3001234567", "100")
    assert any("Ruta 1" in r for r in update.message.replies)


def test_contacto_compartido_aporta_celular_en_flujo(_stub_link_telegram):
    start_flow()
    send_text("Maria Lopez")
    contact = SimpleNamespace(phone_number="+57 300 111 2233", user_id=100)
    update = make_update(contact=contact)
    run(telegram_app.on_contact(update, None))
    assert telegram_app._tksol[1]["step"] == "r1_origin"
    assert telegram_app._tksol[1]["phone"] == "573001112233"
    assert telegram_app._tksol[1]["telegram_user_id"] == "100"
    # Vincula los tickets históricos de ese celular al id de Telegram.
    _stub_link_telegram.assert_called_once_with("573001112233", "100")


def test_cancelar_por_texto_aborta():
    start_flow()
    send_text("Maria Lopez")
    update = send_text("cancelar")
    assert 1 not in telegram_app._tksol
    assert any("cancelada" in r for r in update.message.replies)


def test_boton_menu_interrumpe_flujo():
    start_flow()
    send_text("Maria Lopez")
    with patch.object(telegram_app.ticket_service, "list_tickets_for_user", return_value=[]):
        send_text(telegram_app.BTN_VINCULAR)
    assert 1 not in telegram_app._tksol
    assert 1 in telegram_app._awaiting_contact


# ---------------------------------------------------------------------------
# /solicitar — búsqueda y selección de rutas
# ---------------------------------------------------------------------------

def test_busqueda_rutas_ofrece_botones():
    start_flow()
    send_text("Maria Lopez")
    send_text("3001234567")
    with pick_routes([(7, "Bogota"), (8, "Bogota Norte")]):
        update = send_text("Bogo")
    markup = update.message.reply_kwargs[-1]["reply_markup"]
    botones = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "tksol:route:7" in botones and "tksol:route:8" in botones
    assert telegram_app._tksol[1]["routes"] == {"7": "Bogota", "8": "Bogota Norte"}


def test_busqueda_sin_resultados_pide_otro_texto():
    start_flow()
    send_text("Maria Lopez")
    send_text("3001234567")
    with pick_routes([]):
        update = send_text("zzz")
    assert any("No encontré" in r for r in update.message.replies)
    # Queda claro que puede reescribir el texto (no sólo «cancelar»).
    assert any("otro texto" in r for r in update.message.replies)
    assert telegram_app._tksol[1]["step"] == "r1_origin"


def test_falla_odoo_en_busqueda_no_corta_flujo():
    start_flow()
    send_text("Maria Lopez")
    send_text("3001234567")
    with patch.object(
        telegram_app.ticket_service,
        "search_routes",
        side_effect=OdooClientError("x"),
    ):
        update = send_text("Bogo")
    assert any("No pude consultar" in r for r in update.message.replies)
    assert telegram_app._tksol[1]["step"] == "r1_origin"


def test_seleccion_origen_avanza_a_destino():
    start_flow()
    send_text("Maria Lopez")
    send_text("3001234567")
    with pick_routes([(7, "Bogota")]):
        send_text("Bogo")
    update = choose("tksol:route:7")
    data = telegram_app._tksol[1]
    assert data["step"] == "r1_dest"
    assert data["origin1"] == (7, "Bogota")
    update.callback_query.edit_message_text.assert_awaited_once_with("✔ Bogota")
    prompt = update.message.reply_text.call_args.args[0]
    assert "destino" in prompt and "Ruta 1" in prompt


def test_seleccion_destino_pregunta_segunda_ruta():
    start_flow()
    send_text("Maria Lopez")
    send_text("3001234567")
    with pick_routes([(7, "Bogota")]):
        send_text("Bogo")
    choose("tksol:route:7")
    with pick_routes([(9, "Medellin")]):
        send_text("Med")
    update = choose("tksol:route:9")
    data = telegram_app._tksol[1]
    assert data["step"] == "ask_r2"
    assert data["dest1"] == (9, "Medellin")
    pregunta = update.message.reply_text.call_args.args[0]
    assert "segunda ruta" in pregunta.lower()


def test_ruta_de_mensaje_viejo_no_aplica():
    start_flow()
    send_text("Maria Lopez")
    send_text("3001234567")
    update = choose("tksol:route:7")
    update.callback_query.answer.assert_awaited_with(
        "Esa selección ya no aplica.", show_alert=True
    )
    assert telegram_app._tksol[1]["step"] == "r1_origin"


# ---------------------------------------------------------------------------
# /solicitar — corrección: «⬅️ Atrás», «🔄 Otra búsqueda» y «✏️ Editar»
# ---------------------------------------------------------------------------

def _botones(update):
    markup = update.message.reply_kwargs[-1]["reply_markup"]
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _ultima_respuesta(update):
    respuestas = [r for r in update.message.replies if isinstance(r, str)]
    return respuestas[-1]


def test_busqueda_ofrece_otra_busqueda_y_atras():
    start_flow()
    send_text("Maria Lopez")
    send_text("3001234567")
    with pick_routes([(7, "Bogota")]):
        update = send_text("Bogo")
    botones = _botones(update)
    assert "tksol:search" in botones
    assert "tksol:back" in botones
    assert "tksol:cancel" in botones
    assert "escribe otro texto" in _ultima_respuesta(update)


def test_otra_busqueda_vuelve_a_pedir_texto():
    start_flow()
    send_text("Maria Lopez")
    send_text("3001234567")
    with pick_routes([(7, "Bogota")]):
        send_text("Bogo")
    update = choose("tksol:search")
    update.callback_query.edit_message_text.assert_awaited_once()
    texto = update.callback_query.edit_message_text.call_args.args[0]
    assert "Escribe la ciudad o municipio" in texto
    assert telegram_app._tksol[1]["step"] == "r1_origin"
    # Y se puede escribir de nuevo: una nueva búsqueda cambia los resultados.
    with pick_routes([(8, "Bogota Norte")]):
        update = send_text("Bogot")
    assert telegram_app._tksol[1]["routes"] == {"8": "Bogota Norte"}


def test_atras_desde_destino_vuelve_a_origen_conservando_dato():
    start_flow()
    send_text("Maria Lopez")
    send_text("3001234567")
    with pick_routes([(7, "Bogota")]):
        send_text("Bogo")
    choose("tksol:route:7")
    assert telegram_app._tksol[1]["step"] == "r1_dest"
    update = choose("tksol:back")
    data = telegram_app._tksol[1]
    assert data["step"] == "r1_origin"
    assert data["origin1"] == (7, "Bogota"), "el dato anterior se conserva"
    prompt = update.message.reply_text.call_args.args[0]
    assert "Actual: Bogota" in prompt


def test_atras_desde_origen_vuelve_a_celular():
    start_flow()
    send_text("Maria Lopez")
    send_text("3001234567")
    update = choose("tksol:back")
    data = telegram_app._tksol[1]
    assert data["step"] == "phone"
    assert data["phone"] == "3001234567"
    prompt = update.message.reply_text.call_args.args[0]
    assert "Actual: 3001234567" in prompt


def test_atras_en_pregunta_segunda_ruta():
    start_flow()
    send_text("Maria Lopez")
    send_text("3001234567")
    with pick_routes([(7, "Bogota")]):
        send_text("Bogo")
    choose("tksol:route:7")
    with pick_routes([(9, "Medellin")]):
        send_text("Med")
    update = choose("tksol:route:9")
    assert telegram_app._tksol[1]["step"] == "ask_r2"
    # El teclado de ask_r2 ofrece Atrás y Cancelar.
    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    botones = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "tksol:back" in botones
    assert "tksol:cancel" in botones
    update = choose("tksol:back")
    assert telegram_app._tksol[1]["step"] == "r1_dest"


def test_atras_en_primer_paso_alerta():
    start_flow()
    update = choose("tksol:back")
    update.callback_query.answer.assert_awaited_with(
        "Ya estás en el primer paso.", show_alert=True
    )
    assert telegram_app._tksol[1]["step"] == "name"


def test_resumen_editar_nombre_vuelve_al_resumen():
    walk_to_summary()
    choose("tksol:edit:name")
    assert telegram_app._tksol[1]["step"] == "name"
    update = send_text("Maria Lopez Corregida")
    data = telegram_app._tksol[1]
    assert data["step"] == "summary", "no debe repreguntar celular ni rutas"
    assert data["name"] == "Maria Lopez Corregida"
    assert "MARIA LOPEZ CORREGIDA" in _ultima_respuesta(update)


def test_resumen_editar_celular_vuelve_al_resumen():
    walk_to_summary()
    choose("tksol:edit:phone")
    assert telegram_app._tksol[1]["step"] == "phone"
    update = send_text("3009998888")
    data = telegram_app._tksol[1]
    assert data["step"] == "summary"
    assert data["phone"] == "3009998888"
    assert "3009998888" in _ultima_respuesta(update)


def test_resumen_editar_ruta1_con_ruta2_existente_vuelve_al_resumen():
    walk_to_summary()
    choose("tksol:edit:r1")
    assert telegram_app._tksol[1]["step"] == "r1_origin"
    with pick_routes([(7, "Bogota")]):
        send_text("Bogo")
    choose("tksol:route:7")  # → r1_dest
    with pick_routes([(9, "Medellin")]):
        send_text("Med")
    choose("tksol:route:9")  # con Ruta 2 existente → resumen directo
    data = telegram_app._tksol[1]
    assert data["step"] == "summary"
    assert data["dest1"] == (9, "Medellin")
    assert "edit_return" not in data, "el marcador se limpia al llegar"
    assert data["origin2"] == (3, "Cali"), "Ruta 2 intacta"


def test_resumen_editar_ruta1_sin_ruta2_pregunta_segunda_ruta():
    start_flow()
    send_text("Maria Lopez")
    send_text("3001234567")
    with pick_routes([(7, "Bogota")]):
        send_text("Bogo")
    choose("tksol:route:7")
    with pick_routes([(9, "Medellin")]):
        send_text("Med")
    choose("tksol:route:9")
    choose("tksol:no2")
    choose("tksol:edit:r1")
    with pick_routes([(7, "Bogota")]):
        send_text("Bogo")
    choose("tksol:route:7")
    with pick_routes([(9, "Medellin")]):
        send_text("Med")
    update = choose("tksol:route:9")
    # Sin Ruta 2 previa: la cadena normal permite añadirla.
    assert telegram_app._tksol[1]["step"] == "ask_r2"
    assert "segunda ruta" in update.message.reply_text.call_args.args[0].lower()


def test_resumen_editar_ruta2_recorre_solo_ruta2():
    walk_to_summary()
    choose("tksol:edit:r2")
    assert telegram_app._tksol[1]["step"] == "r2_origin"
    with pick_routes([(3, "Cali")]):
        send_text("Cal")
    choose("tksol:route:3")
    assert telegram_app._tksol[1]["step"] == "r2_dest"
    with pick_routes([(4, "Barranquilla")]):
        send_text("Bar")
    update = choose("tksol:route:4")
    data = telegram_app._tksol[1]
    assert data["step"] == "summary"
    assert data["origin2"] == (3, "Cali")
    assert data["dest2"] == (4, "Barranquilla")


def test_resumen_sin_ruta2_no_ofrece_editar_ruta2():
    start_flow()
    send_text("Maria Lopez")
    send_text("3001234567")
    with pick_routes([(7, "Bogota")]):
        send_text("Bogo")
    choose("tksol:route:7")
    with pick_routes([(9, "Medellin")]):
        send_text("Med")
    choose("tksol:route:9")
    update = choose("tksol:no2")
    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    botones = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "tksol:edit:r2" not in botones
    assert "tksol:edit:r1" in botones


def test_editar_ruta2_ausente_alerta():
    """Un botón «Editar Ruta 2» viejo (sin Ruta 2) alerta y no avanza."""
    start_flow()
    send_text("Maria Lopez")
    send_text("3001234567")
    with pick_routes([(7, "Bogota")]):
        send_text("Bogo")
    choose("tksol:route:7")
    with pick_routes([(9, "Medellin")]):
        send_text("Med")
    choose("tksol:route:9")
    choose("tksol:no2")
    update = choose("tksol:edit:r2")
    update.callback_query.answer.assert_awaited_with(
        "Esta solicitud no tiene Ruta 2.", show_alert=True
    )
    assert telegram_app._tksol[1]["step"] == "summary"


# ---------------------------------------------------------------------------
# /solicitar — resumen y creación
# ---------------------------------------------------------------------------

def test_resumen_con_dos_rutas():
    update = walk_to_summary()
    data = telegram_app._tksol[1]
    assert data["step"] == "summary"
    resumen = update.message.reply_text.call_args.args[0]
    assert "Ruta 1: Bogota → Medellin" in resumen
    assert "Ruta 2: Cali → Barranquilla" in resumen
    markup = update.message.reply_text.call_args.kwargs["reply_markup"]
    botones = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert botones == [
        "tksol:confirm", "tksol:cancel",
        "tksol:edit:name", "tksol:edit:phone",
        "tksol:edit:r1", "tksol:edit:r2",
    ]


def test_resumen_con_una_sola_ruta():
    start_flow()
    send_text("Maria Lopez")
    send_text("3001234567")
    with pick_routes([(7, "Bogota")]):
        send_text("Bogo")
    choose("tksol:route:7")
    with pick_routes([(9, "Medellin")]):
        send_text("Med")
    choose("tksol:route:9")
    update = choose("tksol:no2")
    resumen = update.message.reply_text.call_args.args[0]
    assert telegram_app._tksol[1]["step"] == "summary"
    assert "Ruta 1: Bogota → Medellin" in resumen
    assert "Ruta 2" not in resumen


def test_confirm_crea_en_solicitud_vincula_sin_aprobar_ni_enviar_pase(sample_ticket):
    walk_to_summary()
    with (
        patch.object(
            telegram_app.ticket_service, "create_ticket", return_value=77
        ) as mock_create,
        patch.object(telegram_app.ticket_service, "approve_ticket") as mock_approve,
        patch.object(
            telegram_app.ticket_service, "link_after_approve"
        ) as mock_link,
        patch.object(
            telegram_app.ticket_service, "get_ticket", return_value=sample_ticket
        ),
        patch.object(
            telegram_app.qr_service, "generate_boarding_pass", return_value=b"png"
        ),
    ):
        update = choose("tksol:confirm")

    mock_create.assert_called_once_with(
        {
            "passenger_name": "Maria Lopez",
            "cellphone": "3001234567",
            "route_origin": 7,
            "route_destination": 9,
            "route_origin_2": 3,
            "route_destination_2": 4,
        }
    )
    mock_approve.assert_not_called()
    mock_link.assert_called_once_with(77, "100")
    assert 1 not in telegram_app._tksol
    update.callback_query.edit_message_text.assert_awaited_once()
    texto = update.callback_query.edit_message_text.call_args.args[0]
    assert "En Solicitud" in texto
    update.message.reply_photo.assert_not_awaited()


def test_confirm_error_odoo_conserva_estado_para_reintentar():
    walk_to_summary()
    with (
        patch.object(
            telegram_app.ticket_service,
            "create_ticket",
            side_effect=OdooClientError("x"),
        ),
        patch.object(telegram_app.qr_service, "generate_boarding_pass") as mock_png,
    ):
        update = choose("tksol:confirm")
    update.callback_query.answer.assert_awaited_with(
        "No fue posible crear el ticket. Pulsa «Confirmar» de nuevo.",
        show_alert=True,
    )
    update.message.reply_photo.assert_not_awaited()
    mock_png.assert_not_called()
    # El estado se conserva: el usuario puede pulsar Confirmar otra vez.
    assert 1 in telegram_app._tksol
    assert telegram_app._tksol[1]["step"] == "summary"
    assert "creating" not in telegram_app._tksol[1]


def test_confirm_reintento_tras_error_crea_correctamente(sample_ticket):
    walk_to_summary()
    with (
        patch.object(
            telegram_app.ticket_service,
            "create_ticket",
            side_effect=[OdooClientError("x"), 77],
        ) as mock_create,
        patch.object(telegram_app.ticket_service, "link_after_approve"),
        patch.object(
            telegram_app.ticket_service, "get_ticket", return_value=sample_ticket
        ),
    ):
        # Primer intento falla
        run(telegram_app.on_tksol_choice(make_update(callback_data="tksol:confirm"), None))
        assert 1 in telegram_app._tksol
        # Segundo intento (estado conservado) crea el ticket
        update = choose("tksol:confirm")

    assert mock_create.call_count == 2
    assert 1 not in telegram_app._tksol
    update.callback_query.edit_message_text.assert_awaited_once()


def test_confirm_crear_evita_doble_submit():
    walk_to_summary()
    telegram_app._tksol[1]["creating"] = True
    update = choose("tksol:confirm")
    update.callback_query.answer.assert_awaited_with(
        "Ya se está creando el ticket…", show_alert=False
    )


def test_paso_phone_muestra_boton_contacto():
    start_flow()
    update = send_text("Maria Lopez")
    markup = update.message.reply_kwargs[-1].get("reply_markup")
    assert markup is not None
    # Teclado con botón de compartir contacto (request_contact=True)
    buttons = [b for row in markup.keyboard for b in row]
    assert any(getattr(b, "request_contact", False) for b in buttons)


def test_cancel_callback_limpia_estado():
    start_flow()
    update = choose("tksol:cancel")
    assert 1 not in telegram_app._tksol
    update.callback_query.edit_message_text.assert_awaited_once_with(
        "✖ Solicitud cancelada."
    )


def test_callback_sin_estado_alerta():
    update = choose("tksol:confirm")
    update.callback_query.answer.assert_awaited_with(
        "La solicitud expiró. Usa /solicitar para empezar.", show_alert=True
    )


# ---------------------------------------------------------------------------
# /ticket -> reenvío del pase
# ---------------------------------------------------------------------------

def test_pase_reenviable_tras_primera_lectura(sample_ticket):
    """El usuario puede pedir de nuevo el pase para escanear la Ruta 2."""
    update = make_update(callback_data="ticket:42")
    ticket = dict(sample_ticket, status="abordado1", status_label="Abordado 1ra Ruta")
    with (
        patch.object(telegram_app.ticket_service, "get_ticket", return_value=ticket),
        patch.object(
            telegram_app.qr_service, "generate_boarding_pass", return_value=MagicMock()
        ),
    ):
        run(telegram_app.on_ticket_choice(update, None))
    update.message.reply_photo.assert_awaited_once()


def test_pase_no_enviado_en_cancelado(sample_ticket):
    update = make_update(callback_data="ticket:42")
    ticket = dict(sample_ticket, status="cancelado", status_label="Cancelado")
    with patch.object(telegram_app.ticket_service, "get_ticket", return_value=ticket):
        run(telegram_app.on_ticket_choice(update, None))
    update.message.reply_photo.assert_not_awaited()
