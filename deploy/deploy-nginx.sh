#!/usr/bin/env bash
#
# deploy-nginx.sh — Opción C: webhook HTTPS con nginx + certbot (sin cloudflared)
#
# Requisitos:
#   - DNS en GoDaddy (u otro): bot.yz y odoo.yz tipo A → IP pública del servidor
#   - .env completo (ver .env.example)
#   - Ejecutar como root: sudo ./deploy/deploy-nginx.sh
#
# Qué hace:
#   1. Valida .env y resolución DNS de los hostnames
#   2. Instala nginx/certbot si faltan
#   3. Instala el vhost (bot + odoo)
#   4. Certificado TLS con certbot (HTTP-01 vía puerto 80)
#   5. Unidades systemd: telegram-bot; desactiva cloudflared-telegram-bot
#   6. Chequeos: /health local, https bot, https odoo, getWebhookInfo
#
set -euo pipefail

PROJECT_DIR="/opt/odoo/custom/telegram_boarding_bot"
SERVICE_NAME="telegram-bot"
CF_SERVICE_NAME="cloudflared-telegram-bot"
VHOST_SRC="$PROJECT_DIR/deploy/nginx-telegram-bot.conf"
VHOST_DST="/etc/nginx/sites-available/nginx-telegram-bot.conf"

BOT_DOMAIN="${BOT_DOMAIN:-bot.viacotur.com}"
ODOO_DOMAIN="${ODOO_DOMAIN:-odoo.viacotur.com}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"
SKIP_APT=false
SKIP_CERTBOT=false
BOT_ONLY=false

info() { printf '\033[1;34m[•]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[✔]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[✖]\033[0m %s\n' "$*" >&2; }
die()  { err "$*"; exit 1; }

usage() { sed -n '2,30p' "$0"; exit 0; }

while [ $# -gt 0 ]; do
    case "$1" in
        -d|--domain)      BOT_DOMAIN="${2:?falta valor}"; shift 2 ;;
        -o|--odoo-domain) ODOO_DOMAIN="${2:?falta valor}"; shift 2 ;;
        -e|--email)       CERTBOT_EMAIL="${2:?falta valor}"; shift 2 ;;
        --skip-apt)       SKIP_APT=true; shift ;;
        --skip-certbot)   SKIP_CERTBOT=true; shift ;;
        --bot-only)       BOT_ONLY=true; shift ;;
        -h|--help)        usage ;;
        *) die "Opción desconocida: $1 (usa -h)" ;;
    esac
done

# ---------------------------------------------------------------------------
# 0. Prerequisitos
# ---------------------------------------------------------------------------
[ "$(id -u)" -eq 0 ] || die "Ejecuta como root: sudo $0 ..."
[ -f "$PROJECT_DIR/.env" ] || die "Falta $PROJECT_DIR/.env"
[ -f "$PROJECT_DIR/.venv/bin/uvicorn" ] || die "Falta el venv del proyecto"
[ -f "$VHOST_SRC" ] || die "Falta $VHOST_SRC"

if grep -qE 'ABC-your-token|change-me-32-bytes-hex|bot\.ejemplo\.com|api-key-o-password' "$PROJECT_DIR/.env"; then
    warn "El .env aún tiene placeholders."
    read -rp "¿Continuar? [s/N] " ans
    case "$ans" in s|S|si|SI) ;; *) die "Corrige el .env." ;; esac
fi

SERVER_IP="$(curl -sf --max-time 5 https://api.ipify.org || true)"
if [ -z "$SERVER_IP" ]; then
    SERVER_IP="$(hostname -I | awk '{print $1}')"
fi
ok "IP pública del servidor: $SERVER_IP"

# DNS: cada hostname debe resolver a la IP del servidor (tipo A)
DNS_HOSTS=("$BOT_DOMAIN")
[ "$BOT_ONLY" = true ] || DNS_HOSTS+=("$ODOO_DOMAIN")
for host in "${DNS_HOSTS[@]}"; do
    resolved="$(dig +short "$host" A 2>/dev/null | tail -1 || true)"
    if [ "$resolved" = "$SERVER_IP" ]; then
        ok "DNS $host → $resolved"
    else
        warn "DNS $host → '${resolved:-vacío}' (se espera $SERVER_IP). Certbot fallará si no apunta aquí."
        read -rp "¿Continuar de todos modos? [s/N] " ans
        case "$ans" in s|S|si|SI) ;; *) die "Ajusta el registro A en GoDaddy y vuelve." ;; esac
    fi
done

# ---------------------------------------------------------------------------
# 1. Paquetes
# ---------------------------------------------------------------------------
if [ "$SKIP_APT" = true ]; then
    info "Saltando apt (--skip-apt)"
else
    if ! command -v nginx >/dev/null 2>&1; then
        info "Instalando nginx..."
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nginx
    fi
    if ! command -v certbot >/dev/null 2>&1; then
        info "Instalando certbot..."
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq certbot python3-certbot-nginx || \
            curl -sSf https://dl.eff.org/certbot-auto -o /usr/local/bin/certbot && chmod +x /usr/local/bin/certbot
    fi
    ok "nginx + certbot listos"
fi

# ---------------------------------------------------------------------------
# 2. Vhost (sin certificados aún: forzamos solo HTTP para poder emitir)
# ---------------------------------------------------------------------------
info "Instalando vhost temporal (solo :80) para el desafío ACME ..."
SERVER_NAMES_EXTRA=""
[ "$BOT_ONLY" = true ] || SERVER_NAMES_EXTRA=" $ODOO_DOMAIN"
cat > "$VHOST_DST" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $BOT_DOMAIN${SERVER_NAMES_EXTRA};
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 200 'acme-ok\\n'; add_header Content-Type text/plain; }
}
EOF
[ -e "/etc/nginx/sites-enabled/nginx-telegram-bot.conf" ] || \
    ln -sf "$VHOST_DST" "/etc/nginx/sites-enabled/nginx-telegram-bot.conf"
# El vhost viejo de certbot previo puede romper nginx -t; lo separamos
if [ -e /etc/nginx/sites-enabled/default ]; then
    rm -f /etc/nginx/sites-enabled/default
fi
nginx -t
systemctl enable nginx
systemctl restart nginx
ok "nginx en :80 (modo desafío)"

# ---------------------------------------------------------------------------
# 3. Certificado TLS
# ---------------------------------------------------------------------------
if [ "$SKIP_CERTBOT" = true ]; then
    info "Saltando certbot (--skip-certbot)"
else
    if [ -z "$CERTBOT_EMAIL" ]; then
        read -rp "Email para Let's Encrypt: " CERTBOT_EMAIL
    fi
    [ -n "$CERTBOT_EMAIL" ] || die "Email vacío"
    CERT_DOMAINS=(-d "$BOT_DOMAIN")
    [ "$BOT_ONLY" = true ] || CERT_DOMAINS+=(-d "$ODOO_DOMAIN")
    info "Emitiendo certificado para: ${CERT_DOMAINS[*]//-d /}"
    certbot certonly --webroot -w /var/www/html \
        "${CERT_DOMAINS[@]}" \
        --non-interactive --agree-tos -m "$CERTBOT_EMAIL" --keep-until-expiring
    ok "Certificado emitido"
fi

# ---------------------------------------------------------------------------
# 4. Vhost completo con TLS (el del repo)
# ---------------------------------------------------------------------------
info "Instalando vhost definitivo (TLS → bot y odoo) ..."
FINAL_SRC="$VHOST_SRC"
if [ "$BOT_ONLY" = true ]; then
    FINAL_SRC="$PROJECT_DIR/deploy/nginx-bot-only.conf"
fi
sed -e "s/bot\.viacotur\.com/$BOT_DOMAIN/g" -e "s/odoo\.viacotur\.com/$ODOO_DOMAIN/g" \
    "$FINAL_SRC" > "$VHOST_DST"
nginx -t
systemctl reload nginx
ok "Vhost TLS activo"

# ---------------------------------------------------------------------------
# 5. systemd del bot; cloudflared fuera del camino
# ---------------------------------------------------------------------------
info "Instalando unidad systemd ($SERVICE_NAME) ..."
# Liberar puerto si hay un uvicorn suelto
if command -v fuser >/dev/null 2>&1; then
    fuser -k 8010/tcp >/dev/null 2>&1 || true
    sleep 1
fi
systemctl stop "$CF_SERVICE_NAME" 2>/dev/null || true
systemctl disable "$CF_SERVICE_NAME" 2>/dev/null || true
cp "$PROJECT_DIR/deploy/$SERVICE_NAME.service" "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
sleep 3
systemctl is-active --quiet "$SERVICE_NAME" \
    || die "El servicio $SERVICE_NAME no arrancó. journalctl -u $SERVICE_NAME -n 50"
ok "Servicio $SERVICE_NAME activo (cloudflared deshabilitado)"

# ---------------------------------------------------------------------------
# 6. Chequeos
# ---------------------------------------------------------------------------
info "Chequeo /health local ..."
curl -sf --max-time 5 http://127.0.0.1:8010/health >/dev/null \
    && ok "127.0.0.1:8010 OK" || warn "/health local no respondió"

info "Chequeo https://$BOT_DOMAIN/health ..."
if curl -sf --max-time 15 "https://$BOT_DOMAIN/health" >/dev/null; then
    ok "https://$BOT_DOMAIN/health OK"
else
    warn "https://$BOT_DOMAIN/health falló (¿DNS/防火illa 443?)"
fi

if [ "$BOT_ONLY" != true ]; then
    info "Chequeo https://$ODOO_DOMAIN/web/login ..."
    if curl -sf --max-time 15 -o /dev/null "https://$ODOO_DOMAIN/web/login"; then
        ok "https://$ODOO_DOMAIN OK"
    else
        warn "https://$ODOO_DOMAIN no respondió"
    fi
fi

# Token del bot: se lee de bot.telegram.config (Odoo) con la misma ruta que usa el bot.
TOKEN=$(cd "$PROJECT_DIR" && "$PROJECT_DIR/.venv/bin/python" -c "
from app import runtime_config
from app.odoo_client import OdooClient
runtime_config.load_from_odoo(OdooClient())
print(runtime_config.get('telegram_bot_token'))
" 2>/dev/null || true)
if [ -n "$TOKEN" ]; then
    info "getWebhookInfo:"
    curl -s --max-time 10 "https://api.telegram.org/bot${TOKEN}/getWebhookInfo" \
        | python3 -c "import json,sys; d=json.load(sys.stdin)['result']; print('  url:', d.get('url')); print('  pending:', d.get('pending_update_count')); print('  last_error:', d.get('last_error_message') or 'ninguno')" \
        || warn "No se pudo consultar getWebhookInfo"
fi

echo
ok "Despliegue nginx+certbot completado."
info "Asegúrate de que bot.telegram.config (Odoo) tenga:"
echo "  public_domain=$BOT_DOMAIN"
echo "  odoo_public_url=<URL pública de Odoo.sh>"
echo "Guía completa: README.md (sección 'Odoo.sh')."
