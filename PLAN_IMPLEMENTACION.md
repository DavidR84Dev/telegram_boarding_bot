# PLAN DE IMPLEMENTACIÓN — Bot de Telegram ↔ Odoo · Ticket de Abordaje con QR

> **Cómo usar este documento:** cada ítem tiene un checkbox. Conforme se implemente y verifique, se marca `[x]`
> y se tacha el texto (`~~texto~~`). El estado real se refleja en la sección **Estado global**.

---

## Estado global

| Fase | Estado |
|---|---|
| Parte B — Odoo (`programacion_operativos`) | ✅ completada |
| Parte A — Bot (`telegram_boarding_bot`) | ✅ completada |
| Verificación final | ✅ completada (acciones de usuario pendientes: V.4) |

---

## Arquitectura

```
┌─────────────┐  webhook HTTPS   ┌──────────────────────┐  XML-RPC (LAN)  ┌─────────────────┐
│  Telegram    │ ───────────────► │  telegram_boarding_  │ ──────────────► │   Odoo 16        │
│  (usuarios)  │ ◄─────────────── │  bot (FastAPI+PTB)   │ ◄────────────── │  reservations.   │
└─────────────┘   sendPhoto/Msg  │  :8010 (nginx:443)   │  notify (HTTP)  │  tickets         │
                                 └──────────────────────┘ ◄─────────────── └─────────────────┘
                                                          approve_ticket → POST /internal/notify-ticket
```

**División de responsabilidades:**
- **Odoo (`reservations.tickets`)**: dueño de los datos — crea/recibe tickets, genera el payload QR
  (URL de validación firmada por `access_token`), expone la validación pública y dispara el push al bot.
- **Bot**: transporte Telegram (webhook), render del pase de abordaje (Pillow), decodificación de QR
  por foto, enlace Telegram↔pasajero (`id_telegram`).

**Decisiones clave (aprobadas):**

| Decisión | Valor |
|---|---|
| Conectividad Telegram | Webhook con nginx + dominio público + certbot |
| Integración Odoo | Híbrido: XML-RPC (lectura/escritura) + endpoint público de validación QR |
| Payload del QR | `{ODOO_PUBLIC_URL}/ticket/validate/{access_token}` |
| Ubicación bot | `/opt/odoo/custom/telegram_boarding_bot/` |
| Decodificación QR foto | `opencv-python-headless` (zbar no está en el servidor) |
| Puerto bot | `8010` (libre; Odoo ocupa 8079) |

---

## PARTE B — Odoo (`programacion_operativos`)

### B1. Modelo `reservations.tickets`

- [x] ~~B1.1 Corregir `approve_ticket`: variable `site` inexistente (`NameError`)~~ ✅ refactor completo (`_generate_qr_image`)
- [x] ~~B1.2 `approve_ticket`: asignar QR a `record.qr_code` (no a variable local)~~ ✅
- [x] ~~B1.3 Payload QR = `{web.base.url}/ticket/validate/{access_token}`~~ ✅ adoptada la URL ya existente del entorno: `{web.base.url}/form/accept/{access_token}` (controlador concurrente `FormAcceptController`)
- [x] ~~B1.4 Fallback `pyqrcode` → lib `qrcode` si la primera no está disponible~~ ✅
- [x] ~~B1.5 Nuevo método `action_board()` (estado `enviado` → `abordado`, idempotente)~~ ✅
- [x] ~~B1.6 Hook `_notify_bot()` (POST al bot, timeout 5 s, fallo solo loggeado, parametrizado por `ir.config_parameter`)~~ ✅

### B2. Controlador público + templates

- [x] ~~B2.1 `controllers/ticket_public.py`: `GET /ticket/validate/<token>` (auth=public)~~ ✅ adaptado: se reutilizó el `FormAcceptController` ya presente en `controllers/main.py` (`GET /form/accept/<token>`)
- [x] ~~B2.2 `GET /ticket/validate/<token>`: página inválido / expirado / válido con datos~~ ✅ con `validate_token()` + plantillas dedicadas
- [x] ~~B2.3 `POST /ticket/board`: confirma abordaje vía token (CSRF, idempotente)~~ ✅ `POST /form/accept/board`
- [x] ~~B2.4 Templates website `views/ticket_templates.xml` (válido/invalid/expired/boarded)~~ ✅ `form_accept` / `form_accept_done` / `form_accept_invalid` (la plantilla referenciada no existía → crash)
- [x] ~~B2.5 Import de controllers en `__init__.py` raíz~~ ✅ ya existía

### B3. Seguridad y manifest

- [x] ~~B3.1 ACL `reservations.tickets` en `security/ir.model.access.csv` (user + manager)~~ ✅ 3 líneas (user/dispatcher/manager)
- [x] ~~B3.2 Manifest: añadir `views/ticket_templates.xml`, bump versión `16.0.0.16.2`~~ ✅

### B4. Tests Odoo

- [x] ~~B4.1 `tests/test_ticket_public.py` (HttpCase: validate, expired, board, approve+qr_code, notify no bloquea)~~ ✅ 17 tests (11 modelo + 6 HTTP)
- [x] ~~B4.2 Upgrade en BD `test_prog_operativos` sin errores~~ ✅ v16.0.0.16.2 registrada
- [x] ~~B4.3 Suite del módulo: sin regresiones nuevas~~ ✅ 248 tests, solo la falla preexistente ENDP001

---

## PARTE A — Bot (`telegram_boarding_bot`)

### A1. Núcleo de la aplicación

- [x] ~~A1.1 `app/config.py` (pydantic-settings, `.env`)~~ ✅
- [x] ~~A1.2 `app/schemas.py` (pydantic: Update de Telegram, NotifyTicketIn, TicketOut)~~ ✅
- [x] ~~A1.3 `app/odoo_client.py` (XML-RPC: auth, search_read, write, call)~~ ✅ patrón `odoo_internal_assistant`
- [x] ~~A1.4 `app/ticket_service.py` (vínculo pasajero, búsqueda tickets, validación token)~~ ✅
- [x] ~~A1.5 `app/qr_service.py` (payload QR + imagen del pase de abordaje con Pillow)~~ ✅ pase 800×450
- [x] ~~A1.6 `app/qr_decoder.py` (OpenCV: decodifica QR de foto recibida)~~ ✅ `opencv-python-headless`
- [x] ~~A1.7 `app/formatting.py` (caption HTML del ticket)~~ ✅

### A2. Telegram

- [x] ~~A2.1 `app/telegram_app.py`: `/start` (vínculo por contacto o célula)~~ ✅
- [x] ~~A2.2 `/ticket`: lista inline → selección → `sendPhoto` con pase + caption~~ ✅
- [x] ~~A2.3 `/validar`: foto del QR → decode → consulta Odoo → respuesta ✅/❌ (+ botón abordar staff)~~ ✅
- [x] ~~A2.4 Fallback para texto/foto sin comando~~ ✅
- [x] ~~A2.5 Teclados: `ReplyKeyboardMarkup` (contacto) + `InlineKeyboardMarkup` (tickets/acciones)~~ ✅

### A3. Servidor HTTP

- [x] ~~A3.1 `app/routers/telegram_webhook.py`: `POST /telegram/webhook` con verificación de `secret_token`~~ ✅
- [x] ~~A3.2 `app/routers/internal.py`: `POST /internal/notify-ticket` con Bearer interno~~ ✅
- [x] ~~A3.3 `app/main.py`: FastAPI + lifespan (init PTB, `setWebhook` automático, shutdown)~~ ✅ (con `APP_ENV=testing` salta el registro real)

### A4. Tests del bot

- [x] ~~A4.1 `tests/conftest.py` (env de prueba, TestClient, mocks)~~ ✅
- [x] ~~A4.2 `test_odoo_client.py`: auth + search_read + Fault (mock XML-RPC)~~ ✅
- [x] ~~A4.3 `test_qr_service.py`: payload + PNG válido + round-trip decode~~ ✅
- [x] ~~A4.4 `test_webhook.py`: 200 con secret, 403 sin secret, 400 JSON malformado~~ ✅
- [x] ~~A4.5 `test_internal.py`: 401 sin Bearer, flujo notify mockeado~~ ✅
- [x] ~~A4.6 `test_handlers.py`: /start, /ticket, /validar (updates simulados)~~ ✅
- [x] ~~A4.7 venv + `pip install -r requirements.txt` + `pytest -q` en verde~~ ✅ **33/33 passed**

### A5. Infraestructura y documentación

- [x] ~~A5.1 `requirements.txt`~~ ✅
- [x] ~~A5.2 `.env.example`~~ ✅
- [x] ~~A5.3 `deploy/nginx-telegram-bot.conf` (443 TLS → 127.0.0.1:8010)~~ ✅
- [x] ~~A5.4 `deploy/telegram-bot.service` (systemd)~~ ✅
- [x] ~~A5.5 `Dockerfile` + `docker-compose.yml`~~ ✅
- [x] ~~A5.6 `README.md`: arquitectura, instalación, nginx+certbot, systemd, webhook, pruebas, troubleshooting~~ ✅
- [x] ~~A5.7 `.gitignore`~~ ✅
- [x] ~~A5.8 Guía detallada de despliegue (ítem V.4)~~ ✅ Anexo A de este documento
- [x] ~~A5.9 `deploy/deploy.sh` automatizado (pasos 6-8)~~ ✅ sintaxis + sed vhost verificados

---

## Verificación final

- [x] ~~V.1 Odoo: py_compile OK + upgrade BD test OK + tests nuevos OK + sin regresiones~~ ✅ 17/17 nuevos; suite 248 tests, solo falla preexistente ENDP001
- [x] ~~V.2 Bot: `pytest -q` verde (todos los tests)~~ ✅ 33/33
- [x] ~~V.3 Este checklist 100 % tachado~~ ✅
- [ ] V.4 Acciones del usuario (requieren sudo/dominio): `sudo systemctl restart odoo16`,
      instalar nginx+certbot, crear DNS del dominio, configurar `.env` real con token del bot,
      ejecutar `deploy/` y arrancar el servicio. **Guía detallada → Anexo A.**

---

## Anexo A — Guía detallada de despliegue (ítem V.4)

### Estado actual del servidor (verificado 2026-10-13)

| Punto | Estado | Implicación |
|---|---|---|
| `odoo16` | ✅ activo | El servicio corre |
| qa2 tiene código nuevo (plantilla `form_accept`, ACL tickets) | ❌ NO — último upgrade 14:52; cambios de las ~19:00 | **Upgrade obligatorio** (Paso 1) |
| `telegram_bot.internal_url` en qa2 | ⚠️ `192.168.1.18:8079` (apunta a Odoo, no al bot) | **Corregir** (Paso 2) |
| `telegram_bot.internal_token` | ✅ existe (46 chars) | Mismo valor en `.env` (Paso 7) |
| nginx | ❌ no instalado (certbot sí: `/usr/local/bin/certbot`) | Paso 6 |
| Usuario técnico del bot en Odoo | ❌ no existe (solo `ia.bot@empresa.com`) | Paso 3 |
| Puerto 8010 | ✅ libre | — |
| Versión qa2 `16.0.1.0.0.16.0` > manifest `16.0.0.16.2` | ⚠️ anomalía | `-u` actualiza igual; normalizar a futuro |

### Paso 1 — Actualizar Odoo (código ya en disco)

```bash
sudo systemctl stop odoo16
/opt/odoo/odoo-server/odoo-venv16/bin/python3 /opt/odoo/odoo-server/odoo-bin \
  -c /etc/odoo.conf -d qa2 -u programacion_operativos --stop-after-init \
  --logfile=/tmp/upgrade_qa2.log --log-level=info
sudo systemctl start odoo16
grep -iE "critical|traceback" /tmp/upgrade_qa2.log | head -5   # debe estar vacío
```

Carga: fix `approve_ticket`, `action_board`, plantillas `form_accept*`, ACL de tickets, hook `_notify_bot`.
Validado previamente en `test_prog_operativos` (17/17 nuevos, 248 suite). Riesgo bajo.

### Paso 2 — Corregir parámetros `telegram_bot.*` en Odoo

Ajustes → Técnico → Parámetros del sistema:

```
telegram_bot.internal_url   = http://127.0.0.1:8010 
telegram_bot.internal_token = <sin tocar, o regenerar: openssl rand -hex 32>
```

Anota el token (lo requiere el Paso 7).

### Paso 3 — Usuario técnico del bot en Odoo

Ajustes → Usuarios → Nuevo: login `bot.telegram@empresa.com`; grupo
`programacion_operativos.group_hr_workinghours_manager` (o equivalente con permisos
sobre `reservations.tickets`); Preferencias → **API Keys** → generar → esa es `ODOO_PASSWORD`.

### Paso 4 — Crear el bot en Telegram

[@BotFather](https://t.me/BotFather) → `/newbot` → nombre + username → guardar el token
(`123456:ABC...` = `TELEGRAM_BOT_TOKEN`).

### Paso 5 — Dominio público

- Registro **A** del subdominio (p. ej. `bot.viacotur.com`) apuntando a la IP pública del servidor.
- Puerto **443** abierto/enrutado hacia el servidor (firewall/router).
- Sin IP pública: alternativa cloudflared (túnel).

### Paso 6 — nginx + certificado TLS

```bash
sudo apt-get update && sudo apt-get install -y nginx
sudo cp deploy/nginx-telegram-bot.conf /etc/nginx/sites-available/telegram-bot.conf
# Editar: reemplazar bot.ejemplo.com por el dominio real (3 líneas)
sudo ln -s /etc/nginx/sites-available/telegram-bot.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d bot.tudominio.com
sudo certbot renew --dry-run
```

Telegram solo acepta 443/80/88/8443 con TLS → nginx es obligatorio para webhook.

### Paso 7 — Configurar `.env`

```bash
cd /opt/odoo/custom/telegram_boarding_bot
cp .env.example .env && chmod 600 .env
```

| Variable | Valor |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Paso 4 |
| `TELEGRAM_WEBHOOK_SECRET` | `openssl rand -hex 32` |
| `BOT_PUBLIC_DOMAIN` | Paso 5 |
| `ODOO_URL` | `http://192.168.1.18:8079` |
| `ODOO_DB` | `qa2` |
| `ODOO_USERNAME` / `ODOO_PASSWORD` | Paso 3 (api-key) |
| `ODOO_PUBLIC_URL` | base del QR (ver decisión QR-LAN abajo) |
| `INTERNAL_API_TOKEN` | **idéntico** a `telegram_bot.internal_token` (Paso 2) |

### Paso 8 — systemd

```bash
sudo cp deploy/telegram-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-bot
sudo systemctl status telegram-bot
journalctl -u telegram-bot -f   # "Webhook registrado: https://bot.../telegram/webhook"
```

> Los pasos 6-8 se automatizan con `deploy/deploy.sh` (ver abajo).

### Paso 9 — Verificación end-to-end

```bash
curl -s http://127.0.0.1:8010/health                     # {"status":"ok"}
curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
# url correcta, pending_update_count 0, sin last_error_message
```

Flujo real: ticket en Odoo con tu celular → `/start` (compartir contacto) →
`approve_ticket` en Odoo → el bot envía el pase automáticamente → `/ticket`,
`/validar` con foto del QR → escanear QR con cámara (WiFi LAN) → página de Odoo.

### Riesgos y decisiones

1. **QR solo-LAN**: `ODOO_PUBLIC_URL` con valor LAN solo valida desde la red interna.
   Validación externa requiere exponer Odoo públicamente (segundo dominio + TLS).
2. **Versión del módulo**: qa2 (`16.0.1.0.0.16.0`) > manifest (`16.0.0.16.2`); `-u`
   actualiza igual, pero conviene normalizar a `16.0.1.0.0.16.1`.
3. **Estado en memoria del bot**: los "esperando contacto/foto" se pierden al reiniciar
   (afecta solo conversaciones a mitad de flujo).
4. **Firewall**: webhook 443 (nginx) → 8010 (bot); `/internal/notify-ticket` solo loopback.

### Automatización: `deploy/deploy.sh`

Script idempotente que ejecuta los pasos 6-8 (nginx, certificado, systemd) con
validaciones previas del `.env`:

```bash
# Interactivo (pregunta dominio y email de certbot):
sudo ./deploy/deploy.sh
# No interactivo:
sudo BOT_DOMAIN=bot.tudominio.com CERTBOT_EMAIL=admin@tudominio.com ./deploy/deploy.sh
# Sin instalar paquetes ni renovar certificado (solo re-aplicar vhost/servicio):
sudo BOT_DOMAIN=bot.tudominio.com ./deploy/deploy.sh --skip-apt --skip-certbot
```

Qué hace: verifica `.env` (existencia y placeholders sin cambiar), instala nginx +
plugin certbot si falta, genera el vhost reemplazando el dominio por `sed`,
valida con `nginx -t`, emite el certificado, instala la unidad systemd, arranca el
servicio y comprueba `/health`. **No toca** `.env` existente ni parámetros de Odoo.

---

## Registro de decisiones / incidencias

---

## Registro de decisiones / incidencias

| Fecha | Decisión / incidencia |
|---|---|
| 2026-10-13 | Aprobado: webhook+nginx, integración híbrida, bot en `/opt/odoo/custom/telegram_boarding_bot/`. |
| 2026-10-13 | Detectado: `approve_ticket` tenía `NameError` (`site`) y no asignaba `record.qr_code`; corregido en B1. |
| 2026-10-13 | Detectado: `zbar` no instalado en servidor → decodificación QR con `opencv-python-headless`. |
| 2026-10-13 | Odoo ya tiene `pyqrcode`/`qrcode`/`Pillow` en el venv → sin nuevas dependencias Python en Odoo. |
| 2026-10-13 | Concurrente ya había creado `FormAcceptController` (`/form/accept/<token>`) pero la plantilla `form_accept` no existía (crash). Se adoptó esa URL como payload QR y se crearon las 3 plantillas. |
| 2026-10-13 | Parte B verificada: 17 tests nuevos en verde, suite completa 248 tests con solo la falla preexistente. |
| 2026-10-13 | Parte A verificada: `pytest -q` → **33/33 passed** (venv Python 3.12, PTB 22.8, OpenCV 5.0). |
| 2026-10-13 | Implementación completa: checklist 100 % tachado salvo V.4 (acciones que requieren sudo/dominio del usuario). |
| 2026-10-13 | Añadido Anexo A (guía V.4 detallada con estado real del servidor) y `deploy/deploy.sh` (automatiza pasos 6-8; verificado `bash -n` + sustitución de dominio). |
| 2026-10-13 | Detectado en qa2: `telegram_bot.internal_url` apuntaba a Odoo (8079) en lugar del bot (8010); quedó documentado como corrección del Paso 2. |
