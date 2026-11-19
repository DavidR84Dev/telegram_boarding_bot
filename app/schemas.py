from typing import Literal

from pydantic import BaseModel, ConfigDict


class NotifyTicketIn(BaseModel):
    """Payload que Odoo envía al notificar un ticket aprobado."""

    model_config = ConfigDict(extra="ignore")

    ticket_id: int
    ticket_number: str = ""
    passenger_name: str = ""
    id_telegram: str = ""


class HealthOut(BaseModel):
    status: str


class NotifyBoardingIn(BaseModel):
    """Payload que el validador de QR envía al registrarse un abordaje."""

    model_config = ConfigDict(extra="ignore")

    ticket_id: int
    status: Literal["abordado1", "abordado2"]
