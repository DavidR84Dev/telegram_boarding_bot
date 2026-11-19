from functools import lru_cache

from pydantic import HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de arranque desde variables de entorno (o `.env`).

    Solo contiene lo necesario para conectar con Odoo. El token de Telegram,
    el secreto del webhook, los dominios y el token interno se leen del
    registro activo de ``bot.telegram.config`` (ver ``app/runtime_config.py``).

    En pruebas se fija ``APP_ENV=testing`` para que el lifespan no
    intente registrar el webhook real en Telegram.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "production"
    log_level: str = "INFO"

    # Odoo (XML-RPC)
    odoo_url: HttpUrl
    odoo_db: str
    odoo_username: str
    odoo_password: SecretStr

    # Servidor
    listen_host: str = "127.0.0.1"
    listen_port: int = 8010
    rpc_timeout_seconds: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
