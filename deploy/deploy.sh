#!/usr/bin/env bash
#
# deploy.sh — Despliegue del Telegram Boarding Bot con Cloudflare Tunnel
#
# Qué automatiza:
#   1. Validación previa del .env (existencia y placeholders sin cambiar).
#   2. Descarga del binario cloudflared local (si falta) o uso del del sistema.
#   3. Unidad systemd del bot (telegram-bot) + arranque.
#   4. Unidad systemd de cloudflared (telegram-boarding-bot) + arranque.
#   5. Chequeos: /health local, https del tunnel, getWebhookInfo de Telegram.
#
# Requisitos previos (interactivos, una sola vez con la cuenta Cloudflare):
#   - cloudflared tunnel login        (genera ~/.cloudflared/cert.pem)
#   - cloudflared tunnel create telegram-boarding-bot
#   - cloudflared tunnel route dns telegram-boarding-bot <hostname>
#   - DNS: bot.<zona> y odoo.<zona> apuntando al túnel (CNAME)
#
# No toca: el .env existente ni ningún parámetro de Odoo.
#
# Uso:
#   sudo ./deploy/deploy.sh                       # valores por defecto
#   sudo BOT_DOMAIN=bot.viacotur.com ODOO_DOMAIN=odoo.viacotur.com ./deploy/deploy.sh
#   sudo ./deploy/deploy.sh --skip-cf-check       # no comprueba binario cloudflared
#
set -euo pipefail

PROJECT_DIR="/opt/odoo/custom/telegram_boarding_bot"
SERVICE_NAME="telegram-bot"
CF_SERVICE_NAME="cloudflared-telegram-bot"
CF_CONFIG="$PROJECT_DIR/deploy/cloudflared/config.yml"
CF_BIN="$PROJECT_DIR/bin/cloudflared"

BOT_DOMAIN="${BOT_DOMAIN:-bot.viacotur.com}"
ODOO_DOMAIN="${ODOO_DOMAIN:-odoo.viacotur.com}"

# ---------------------------------------------------------------------------

info() { printf '\033[1;34m[•]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[✔]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[✖]\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

usage() {
    sed -n '2,28p' "$0"
    exit 0
}

SKIP_CF_CHECK=false

while [ $# -gt 0 ]; do
    case "$1" in
        -d|--domain)       BOT_DOMAIN="${2:?falta valor para $1}"; shift 2 ;;
        -o|--odoo-domain)  ODOO_DOMAIN="${2:?falta valor para $1}"; shift 2 ;;
        --skip-cf-check)   SKIP_CF_CHECK=true; shift ;;
        -h|--help)         usage ;;
        *) die "Opción desconocida: $1 (usa -h)" ;;
    esac
done

# ---------------------------------------------------------------------------
# 0. Prerequisitos
# ---------------------------------------------------------------------------

[ "$(id -u)" -eq 0 ] || die "Ejecuta como root (sudo $0 ...)"
[ -d "$PROJECT_DIR" ] || die "No existe $PROJECT_DIR"
[ -f "$PROJECT_DIR/.env" ] || die "Falta $PROJECT_DIR/.env (copia .env.example y rellena primero)"
[ -f "$PROJECT_DIR/.venv/bin/uvicorn" ] || die "Falta el venv ($PROJECT_DIR/.venv): python3 -m venv .venv && pip install -r requirements.txt"

# Placeholders sin cambiar en .env
if grep -qE 'ABC-your-token|change-me-32-bytes-hex|bot\.ejemplo\.com|api-key-o-password' "$PROJECT_DIR/.env"; then
    warn "El .env contiene valores de ejemplo sin cambiar:"
    grep -nE 'ABC-your-token|change-me-32-bytes-hex|bot\.ejemplo\.com|api-key-o-password' "$PROJECT_DIR/.env" || true
    read -rp "¿Continuar de todos modos? [s/N] " ans
    case "$ans" in s|S|si|SI) ;; *) die "Corrige el .env y vuelve a ejecutar." ;; esac
fi

# public_domain en bot.telegram.config debe coincidir con el dominio elegido (se usa en setWebhook)

# Credenciales del túnel (cert + credentials json)
CF_HOME="${CLOUDFLARED_ORIGDIR:-/home/odoo/.cloudflared}"
if [ ! -f "$CF_HOME/cert.pem" ]; then
    die "Falta $CF_HOME/cert.pem — ejecuta como odoo: cloudflared tunnel login"
fi
TUNNEL_ID="$(ls "$CF_HOME"/*.json 2>/dev/null | head -1 | xargs -r -n1 basename | sed 's/\.json$//' || true)"
if [ -z "$TUNNEL_ID" ]; then
    die "Falta credentials del túnel en $CF_HOME — ejecuta: cloudflared tunnel create telegram-boarding-bot"
fi
[ -f "$CF_CONFIG" ] || die "Falta $CF_CONFIG"

ok "Prerequisitos verificados (bot: $BOT_DOMAIN, odoo: $ODOO_DOMAIN, tunnel: $TUNNEL_ID)"

# ---------------------------------------------------------------------------
# 1. Binario cloudflared
# ---------------------------------------------------------------------------

if [ "$SKIP_CF_CHECK" = true ]; then
    info "Saltando comprobación de cloudflared (--skip-cf-check)"
elif command -v cloudflared >/dev/null 2>&1; then
    ok "cloudflared del sistema: $(command -v cloudflared) ($(cloudflared --version 2>/dev/null | head -1))"
    if [ ! -x "$CF_BIN" ]; then
        mkdir -p "$(dirname "$CF_BIN")"
        ln -sf "$(command -v cloudflared)" "$CF_BIN"
        ok "Enlace creado en $CF_BIN"
    fi
elif [ -x "$CF_BIN" ]; then
    ok "cloudflared local: $CF_BIN ($("$CF_BIN" --version | head -1))"
else
    info "Descargando cloudflared a $CF_BIN ..."
    mkdir -p "$(dirname "$CF_BIN")"
    curl -sL -o "$CF_BIN" \
        https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
    chmod +x "$CF_BIN"
    ok "cloudflared instalado en $CF_BIN"
fi

# ---------------------------------------------------------------------------
# 2. Unidad systemd del bot
# ---------------------------------------------------------------------------

# Puerto del bot (por defecto 8010 si no está en .env)
LISTEN_PORT="${LISTEN_PORT:-$(grep -E '^LISTEN_PORT=' "$PROJECT_DIR/.env" | cut -d= -f2- || true)}"
LISTEN_PORT="${LISTEN_PORT:-8010}"

info "Instalando unidad systemd ($SERVICE_NAME) ..."
# Evita "address already in use": si un uvicorn manual (u otro proceso) retiene el puerto,
# lo liberamos antes de arrancar la unidad (loop de reintentos de systemd included).
systemctl stop "$SERVICE_NAME" 2>/dev/null || true
systemctl reset-failed "$SERVICE_NAME" 2>/dev/null || true
if command -v fuser >/dev/null 2>&1; then
    if fuser -k "${LISTEN_PORT}/tcp" >/dev/null 2>&1; then
        warn "Liberado puerto $LISTEN_PORT (proceso previo sobre el bot)"
        sleep 2
    fi
elif command -v lsof >/dev/null 2>&1; then
    OLD_PID="$(lsof -t -iTCP:"$LISTEN_PORT" -sTCP:LISTEN 2>/dev/null | head -1 || true)"
    if [ -n "${OLD_PID:-}" ]; then
        warn "Liberado puerto $LISTEN_PORT (matando PID $OLD_PID)"
        kill "$OLD_PID" 2>/dev/null || true
        sleep 2
    fi
fi
cp "$PROJECT_DIR/deploy/$SERVICE_NAME.service" "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"
sleep 4
systemctl is-active --quiet "$SERVICE_NAME" \
    || die "El servicio $SERVICE_NAME no arrancó. Revisa: journalctl -u $SERVICE_NAME -n 50"
ok "Servicio $SERVICE_NAME activo"

# ---------------------------------------------------------------------------
# 3. Unidad systemd de cloudflared
# ---------------------------------------------------------------------------

info "Instalando unidad systemd ($CF_SERVICE_NAME) ..."
# cloudflared es solo outbound; si hay una instancia desprendida, la paramos
# para no duplicar conexiones al túnel.
pkill -f "cloudflared tunnel .*run telegram-boarding-bot" 2>/dev/null || true
sleep 1
cp "$PROJECT_DIR/deploy/$CF_SERVICE_NAME.service" "/etc/systemd/system/$CF_SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable "$CF_SERVICE_NAME"
systemctl start "$CF_SERVICE_NAME"
sleep 4
systemctl is-active --quiet "$CF_SERVICE_NAME" \
    || die "El servicio $CF_SERVICE_NAME no arrancó. Revisa: journalctl -u $CF_SERVICE_NAME -n 50"
ok "Servicio $CF_SERVICE_NAME activo"

# ---------------------------------------------------------------------------
# 4. Chequeos finales
# ---------------------------------------------------------------------------

info "Chequeo local /health ..."
if curl -sf --max-time 5 http://127.0.0.1:8010/health >/dev/null; then
    ok "El bot responde en 127.0.0.1:8010"
else
    warn "/health no respondió; revisa: journalctl -u $SERVICE_NAME -n 50"
fi

info "Chequeo https://$BOT_DOMAIN/health (vía Cloudflare Tunnel) ..."
if curl -sf --max-time 15 "https://$BOT_DOMAIN/health" >/dev/null; then
    ok "El tunnel responde para $BOT_DOMAIN"
else
    warn "https://$BOT_DOMAIN/health no respondió. Comprueba DNS (CNAME al túnel) y: journalctl -u $CF_SERVICE_NAME -n 50"
fi

info "Chequeo https://$ODOO_DOMAIN (Odoo vía tunnel) ..."
if curl -sf --max-time 15 -o /dev/null "https://$ODOO_DOMAIN/web/login"; then
    ok "Odoo responde en https://$ODOO_DOMAIN"
else
    warn "https://$ODOO_DOMAIN no respondió (¿CNAME odoo → túnel?)."
fi

# Token del bot: se lee de bot.telegram.config (Odoo) con la misma ruta que usa el bot.
TOKEN=$(cd "$PROJECT_DIR" && "$PROJECT_DIR/.venv/bin/python" -c "
from app import runtime_config
from app.odoo_client import OdooClient
runtime_config.load_from_odoo(OdooClient())
print(runtime_config.get('telegram_bot_token'))
" 2>/dev/null || true)
if [ -n "$TOKEN" ]; then
    info "Estado del webhook en Telegram:"
    curl -s --max-time 10 "https://api.telegram.org/bot${TOKEN}/getWebhookInfo" \
        | python3 -c "import json,sys; d=json.load(sys.stdin)['result']; print('  url:', d.get('url')); print('  pending:', d.get('pending_update_count')); print('  last_error:', d.get('last_error_message') or 'ninguno')" \
        || warn "No se pudo consultar getWebhookInfo"
fi

echo
ok "Despliegue completado."
info "Pendientes manuales (una vez):"
echo "  - Registro bot.telegram.config (Odoo): token, webhook_secret, public_domain, internal_token, odoo_public_url"
echo "  - Usuario técnico Odoo con API Key (ODOO_PASSWORD)"
echo "  - DNS: $BOT_DOMAIN y $ODOO_DOMAIN como CNAME del Named Tunnel (ya creados con route dns si no había registros previos)"
echo "  - Verificación end-to-end desde Telegram (/start, approve_ticket, /validar)"
