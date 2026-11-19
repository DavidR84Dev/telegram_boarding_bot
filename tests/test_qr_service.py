"""Pruebas de generación de QR y pase de abordaje."""
import io

from PIL import Image

from app import qr_service
from app import runtime_config


def test_build_validation_url():
    url = qr_service.build_validation_url("tok123")
    assert url.endswith("/form/accept/tok123")
    assert url.startswith(runtime_config.get("odoo_public_url"))


def test_generate_qr_png_es_png_valido():
    buf = qr_service.generate_qr_png("http://127.0.0.1:8069/form/accept/tok123")
    data = buf.getvalue()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(data))
    assert img.size[0] > 50


def test_generate_boarding_pass(sample_ticket):
    buf = qr_service.generate_boarding_pass(sample_ticket)
    data = buf.getvalue()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(data))
    assert img.size == (800, 450)


def test_generate_boarding_pass_dos_rutas(sample_ticket):
    """El pase con la Ruta 2 sigue cabiendo en el lienzo fijo."""
    ticket = dict(
        sample_ticket,
        route_origin_2="Cali",
        route_destination_2="Bogota",
        route_origin_bol_2=True,
    )
    buf = qr_service.generate_boarding_pass(ticket)
    data = buf.getvalue()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    img = Image.open(io.BytesIO(data))
    assert img.size == (800, 450)
