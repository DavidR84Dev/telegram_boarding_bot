"""Generación del QR y de la imagen del ticket de abordaje (Pillow)."""
from __future__ import annotations

import io
import logging

import qrcode
from PIL import Image, ImageDraw, ImageFont

from . import runtime_config

logger = logging.getLogger(__name__)

HEADER_COLOR = (25, 60, 120)
LABEL_COLOR = (120, 120, 120)
CANVAS_SIZE = (800, 450)


def build_validation_url(token: str) -> str:
    base = runtime_config.get("odoo_public_url").rstrip("/")
    return f"{base}/form/accept/{token}"


def _load_font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def generate_qr_png(payload: str) -> io.BytesIO:
    """QR negro sobre blanco del payload (URL de validación)."""
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=2
    )
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def generate_boarding_pass(ticket: dict) -> io.BytesIO:
    """ticket de abordaje 800x450: cabecera, datos del pasajero y QR."""
    width, height = CANVAS_SIZE
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)

    draw.rectangle([0, 0, width, 70], fill=HEADER_COLOR)
    draw.text((24, 22), "TICKET DE ABORDAJE", font=_load_font(28), fill="white")

    origin = ticket.get("route_origin") or "—"
    destination = ticket.get("route_destination") or "—"
    lines = [
        ("PASAJERO", ticket.get("passenger_name", "").upper() or "—"),
        ("TICKET", ticket.get("ticket_number") or "—"),
        ("VEHÍCULO", ticket.get("vehicle_id") or "—"),
        # ("ASIENTO", ticket.get("seat_number") or "—"),
        ("RUTA", f"{origin} → {destination}"),
        ("ESTADO", ticket.get("status_label") or ticket.get("status") or "—"),
    ]
    origin_2 = ticket.get("route_origin_2")
    destination_2 = ticket.get("route_destination_2")
    if origin_2 or destination_2:
        lines.insert(4, ("RUTA 2", f"{origin_2 or '—'} → {destination_2 or '—'}"))
    # Con la ruta 2 las 6 líneas necesitan un paso menor para caber.
    step = 54 if len(lines) > 5 else 62
    y = 100
    for label, value in lines:
        draw.text((24, y), label, font=_load_font(14), fill=LABEL_COLOR)
        draw.text((24, y + 20), str(value), font=_load_font(20), fill="black")
        y += step

    payload = ticket.get("url_access") or build_validation_url(ticket.get("access_token") or "")
    qr_img = Image.open(generate_qr_png(payload)).convert("RGB").resize((200, 200))
    canvas.paste(qr_img, (width - 240, 110))
    draw.text((width - 240, 330), "Escanea para validar", font=_load_font(14), fill=LABEL_COLOR)

    out = io.BytesIO()
    canvas.save(out, format="PNG")
    out.seek(0)
    return out
