"""Pruebas de /internal/notify-ticket (Bearer interno + flujo mockeado)."""
from unittest.mock import AsyncMock, MagicMock, patch


def test_notify_sin_token(client):
    response = client.post("/internal/notify-ticket", json={"ticket_id": 1})
    assert response.status_code == 401


def test_notify_token_incorrecto(client):
    response = client.post(
        "/internal/notify-ticket",
        json={"ticket_id": 1},
        headers={"Authorization": "Bearer otro"},
    )
    assert response.status_code == 401


def test_notify_ticket_inexistente(client, auth_headers):
    with patch("app.routers.internal.ticket_service.get_ticket", return_value=None):
        response = client.post(
            "/internal/notify-ticket", json={"ticket_id": 999}, headers=auth_headers
        )
    assert response.status_code == 200
    assert response.json() == {"sent": False, "reason": "ticket_not_found"}


def test_notify_no_vinculado(client, auth_headers, sample_ticket):
    ticket = dict(sample_ticket, id_telegram="")
    with patch("app.routers.internal.ticket_service.get_ticket", return_value=ticket):
        response = client.post(
            "/internal/notify-ticket", json={"ticket_id": 42}, headers=auth_headers
        )
    assert response.json() == {"sent": False, "reason": "not_linked"}


def test_notify_envia_foto(client, auth_headers, sample_ticket):
    mock_bot = MagicMock()
    mock_bot.bot.send_photo = AsyncMock(return_value=True)
    with (
        patch("app.routers.internal.ticket_service.get_ticket", return_value=sample_ticket),
        patch("app.routers.internal.qr_service.generate_boarding_pass", return_value=MagicMock()),
        patch("app.routers.internal.get_application") as mock_app,
    ):
        mock_app.return_value = mock_bot
        response = client.post(
            "/internal/notify-ticket", json={"ticket_id": 42}, headers=auth_headers
        )
    assert response.json() == {"sent": True}
    mock_bot.bot.send_photo.assert_awaited_once()
    kwargs = mock_bot.bot.send_photo.call_args.kwargs
    assert kwargs["chat_id"] == 999888777


def test_notify_falla_envio(client, auth_headers, sample_ticket):
    mock_bot = MagicMock()
    mock_bot.bot.send_photo = AsyncMock(side_effect=Exception("chat no encontrado"))
    with (
        patch("app.routers.internal.ticket_service.get_ticket", return_value=sample_ticket),
        patch("app.routers.internal.qr_service.generate_boarding_pass", return_value=MagicMock()),
        patch("app.routers.internal.get_application") as mock_app,
    ):
        mock_app.return_value = mock_bot
        response = client.post(
            "/internal/notify-ticket", json={"ticket_id": 42}, headers=auth_headers
        )
    assert response.json() == {"sent": False, "reason": "send_failed"}


def test_notify_en_solicitud_no_envia(client, auth_headers, sample_ticket):
    """Sin vehículo el ticket sigue en solicitud: el bot no envía pase."""
    ticket = dict(sample_ticket, status="solicitud")
    with patch("app.routers.internal.ticket_service.get_ticket", return_value=ticket):
        response = client.post(
            "/internal/notify-ticket", json={"ticket_id": 42}, headers=auth_headers
        )
    assert response.json() == {"sent": False, "reason": "status_solicitud"}


# --- /internal/notify-boarding (aviso de abordaje al pasajero) ---

def _boarded(sample_ticket, **extra):
    return dict(sample_ticket, status="abordado1", route_origin_bol=True, **extra)


def test_boarding_sin_token(client):
    response = client.post("/internal/notify-boarding", json={"ticket_id": 42, "status": "abordado1"})
    assert response.status_code == 401


def test_boarding_status_invalido(client, auth_headers):
    response = client.post(
        "/internal/notify-boarding", json={"ticket_id": 42, "status": "enviado"}, headers=auth_headers
    )
    assert response.status_code == 422


def test_boarding_ticket_inexistente(client, auth_headers):
    with patch("app.routers.internal.ticket_service.get_ticket", return_value=None):
        response = client.post(
            "/internal/notify-boarding", json={"ticket_id": 9, "status": "abordado1"}, headers=auth_headers
        )
    assert response.json() == {"sent": False, "reason": "ticket_not_found"}


def test_boarding_no_vinculado(client, auth_headers, sample_ticket):
    ticket = _boarded(sample_ticket, id_telegram="")
    with patch("app.routers.internal.ticket_service.get_ticket", return_value=ticket):
        response = client.post(
            "/internal/notify-boarding", json={"ticket_id": 42, "status": "abordado1"}, headers=auth_headers
        )
    assert response.json() == {"sent": False, "reason": "not_linked"}


def test_boarding_estado_no_coincide(client, auth_headers, sample_ticket):
    """Si Odoo ya avanzó (p. ej. abordado2), no se envía el aviso de abordado1."""
    ticket = dict(sample_ticket, status="abordado2")
    mock_bot = MagicMock()
    mock_bot.bot.send_message = AsyncMock(return_value=True)
    with (
        patch("app.routers.internal.ticket_service.get_ticket", return_value=ticket),
        patch("app.routers.internal.get_application", return_value=mock_bot),
    ):
        response = client.post(
            "/internal/notify-boarding", json={"ticket_id": 42, "status": "abordado1"}, headers=auth_headers
        )
    assert response.json() == {"sent": False, "reason": "status_mismatch"}
    mock_bot.bot.send_message.assert_not_awaited()


def test_boarding_envia_texto(client, auth_headers, sample_ticket):
    mock_bot = MagicMock()
    mock_bot.bot.send_message = AsyncMock(return_value=True)
    with (
        patch("app.routers.internal.ticket_service.get_ticket", return_value=_boarded(sample_ticket)),
        patch("app.routers.internal.get_application", return_value=mock_bot),
    ):
        response = client.post(
            "/internal/notify-boarding", json={"ticket_id": 42, "status": "abordado1"}, headers=auth_headers
        )
    assert response.json() == {"sent": True}
    kwargs = mock_bot.bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == 999888777
    assert "fue validado y aprobado" in kwargs["text"]
    assert "Abordaje de la 1.ª ruta" in kwargs["text"]


def test_boarding_falla_envio_se_registra(client, auth_headers, sample_ticket, caplog):
    mock_bot = MagicMock()
    mock_bot.bot.send_message = AsyncMock(side_effect=Exception("chat not found"))
    with (
        patch("app.routers.internal.ticket_service.get_ticket", return_value=_boarded(sample_ticket)),
        patch("app.routers.internal.get_application", return_value=mock_bot),
        caplog.at_level("WARNING"),
    ):
        response = client.post(
            "/internal/notify-boarding", json={"ticket_id": 42, "status": "abordado1"}, headers=auth_headers
        )
    assert response.json() == {"sent": False, "reason": "send_failed"}
    assert "No se pudo avisar al pasajero" in caplog.text
