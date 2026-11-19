"""Pruebas del formato HTML del pase de abordaje."""
from app import formatting


def ticket_2_rutas(**overrides):
    base = {
        "id": 42,
        "ticket_number": "Abordaje_ABCD",
        "passenger_name": "Maria Lopez",
        "cellphone": "3001234567",
        "status": "abordado1",
        "status_label": "Abordado 1ra Ruta",
        "seat_number": "12A",
        "route_origin": "Bogota",
        "route_destination": "Medellin",
        "route_origin_bol": True,
        "route_origin_2": "Cali",
        "route_destination_2": "Bogota",
        "route_origin_bol_2": False,
    }
    base.update(overrides)
    return base


def test_caption_una_ruta():
    caption = formatting.ticket_caption(
        ticket_2_rutas(route_origin_2="", route_destination_2="")
    )
    assert "Ruta 1:" in caption
    assert "Ruta 2" not in caption


def test_caption_das_rutas_con_marcas():
    caption = formatting.ticket_caption(ticket_2_rutas())
    assert "Bogota → Medellin ✅" in caption
    assert "Cali → Bogota ⏳" in caption
    assert "Abordado 1ra Ruta" in caption


def test_caption_escapa_html():
    caption = formatting.ticket_caption(ticket_2_rutas(passenger_name="Ana <3 & Cia"))
    assert "Ana &lt;3 &amp; Cia" in caption
    assert "<3" not in caption
