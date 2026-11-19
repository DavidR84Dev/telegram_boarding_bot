# Telegram Boarding Bot

Bot de Telegram para el pase de abordaje. Expone un webhook de Telegram y una API interna que usa Odoo para enviar los pases de los tickets con vehículo asignado.

- **Bot**: FastAPI + python-telegram-bot, escucha en `127.0.0.1:8010`.
- **Odoo**: módulo `programacion_operativos` (modelo `bot.telegram.config`). Odoo.sh.
- **Configuración**: solo las credenciales de conexión a Odoo van en `.env`. El resto (token de Telegram, secreto del webhook, dominio público, token interno y URL pública de Odoo) está en el registro activo de **Configuración Bot > parametros** en Odoo.

## Arquitectura

```
Telegram ──HTTPS──► bot.viacotur.com (VPS del bot, nginx) ──► 127.0.0.1:8010 (uvicorn)
                                                                  │
Odoo.sh ──XML-RPC (HTTPS)──────────────────────────────────────────┘  (lectura de config / tickets)
Odoo.sh ──HTTPS POST /internal/notify-ticket (Bearer)──► bot.viacotur.com
```

## Flujo de tickets

1. **Solicitud (bot)**: `/solicitar` crea el ticket en Odoo en estado **En Solicitud** (`solicitud`) y vincula el chat de Telegram. No se aprueba ni se envía nada todavía.
2. **Asignación (Odoo)**: el despachador asigna el **vehículo** en el ticket.
3. **Envío automático (Odoo → bot)**: al asignar el vehículo, Odoo aprueba el ticket (`enviado`), genera el QR y notifica al bot, que envía el pase de abordaje al chat. El nombre del pasajero se muestra en **mayúsculas**.
4. **Excepción manual**: el botón "Aprobar Ticket" de Odoo aprueba aunque no haya vehículo.
5. **Cancelación**: el usuario puede cancelar un ticket en *En Solicitud* o *Enviado* (`/cancelar`). Los tickets ya abordados no se cancelan.

---

## 1. Requisitos del VPS

- Ubuntu 22.04 o 24.04 con acceso root (`sudo`).
- IP pública fija (anótala: `<IP-VPS>`).
- Puertos abiertos al exterior: **22** (SSH), **80** (certificado), **443** (HTTPS).
- Python 3.12 (`python3.12 --version`). En Ubuntu 22.04: `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.12 python3.12-venv`.

## 2. DNS

Crea o cambia el registro **A** de `bot.viacotur.com` apuntando a `<IP-VPS>`.

- Antes del cambio, baja el TTL del registro a 300 s y espera a que expire el TTL anterior.
- `odoo.viacotur.com` **no se toca**.
- Comprueba la resolución desde el VPS:
  ```bash
  dig +short bot.viacotur.com A    # debe devolver <IP-VPS>
  ```

## 3. Instalación del código

```bash
# Si el usuario 'odoo' no existe en el VPS nuevo:
sudo useradd --create-home --shell /bin/bash odoo 2>/dev/null || true
sudo mkdir -p /opt/odoo/custom
sudo chown -R odoo:odoo /opt/odoo
sudo -u odoo git clone <URL-DEL-REPO> /opt/odoo/custom/telegram_boarding_bot   # o copia la carpeta
cd /opt/odoo/custom/telegram_boarding_bot
sudo -u odoo python3.12 -m venv .venv
sudo -u odoo .venv/bin/pip install -r requirements.txt
```

## 4. Archivo `.env`

```bash
sudo -u odoo cp .env.example .env
sudo -u odoo chmod 600 .env
sudo -u odoo nano .env
```

Valores a rellenar:

| Variable | Valor |
|---|---|
| `ODOO_URL` | URL HTTPS de Odoo.sh, p. ej. `https://viacotur16.odoo.com` |
| `ODOO_DB` | Nombre de la base de datos de producción en Odoo.sh (ver paso 8.1) |
| `ODOO_USERNAME` | Usuario técnico, p. ej. `usuario.tecnico@<dominio>` |
| `ODOO_PASSWORD` | **API key** creada en Odoo.sh (Preferencias > API Keys), no la contraseña |
| `LISTEN_HOST` | `127.0.0.1` (no cambiar) |
| `LISTEN_PORT` | `8010` (no cambiar) |

Comprueba la conexión antes de seguir:

```bash
cd /opt/odoo/custom/telegram_boarding_bot
.venv/bin/python -c "from app.odoo_client import OdooClient; print('uid:', OdooClient().authenticate())"
```

Debe imprimir `uid: <número>`. Si falla, revisa `ODOO_DB` y la API key.

## 5. nginx y certificado TLS

```bash
cd /opt/odoo/custom/telegram_boarding_bot
sudo ./deploy/deploy-nginx.sh --bot-only -e <tu-email@dominio>
```

El script:
1. Comprueba que `bot.viacotur.com` resuelve a la IP de este VPS.
2. Instala nginx y certbot si faltan.
3. Emite el certificado Let's Encrypt **solo** para `bot.viacotur.com`.
4. Instala el vhost de `deploy/nginx-bot-only.conf` (webhook, `/internal/`, `/health`; el resto responde 404).
5. Instala y arranca la unidad `telegram-bot`.

## 6. Servicio systemd

Lo instala el script del paso 5. Para comprobarlo:

```bash
systemctl status telegram-bot --no-pager
journalctl -u telegram-bot -n 40 --no-pager
```

Al arrancar debe aparecer:
- `Configuración cargada desde bot.telegram.config (id N)`
- `Webhook registrado: https://bot.viacotur.com/telegram/webhook`
- `Application startup complete.`

Si Odoo no responde en el arranque, el bot reintenta con backoff (2 s, 4 s, 8 s… hasta 5 min) y registra `Odoo no disponible (intento N)`. Pasado ese tiempo, systemd reintenta el arranque cada 30 s, hasta 5 veces en 10 minutos.

## 7. Firewall

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

El puerto 8010 queda solo en `127.0.0.1`; no hace falta abrirlo. Excepción: en el servidor de QA, donde `telegram_bot_operativos` corre en Docker y llama a este bot, ver la [sección 12](#12-servidor-de-qa-e-integración-con-telegram_bot_operativos).

---

## 8. Configuración en Odoo.sh

### 8.1 Nombre de la base de datos

En Odoo.sh, la base de producción tiene un nombre concreto (no `qa2`). Ese nombre es el valor de `ODOO_DB` en `.env`. Si no lo tienes a mano, consúltalo en el panel de Odoo.sh del proyecto.

### 8.2 Módulo instalado

En la base de producción de Odoo.sh debe estar instalado `programacion_operativos` (versión `16.0.1.0.0.16.17` o superior), que incluye el modelo `bot.telegram.config` y el menú **Configuración Bot**. Sin él, el bot no arranca.

### 8.3 API key del usuario técnico

1. Entra con el usuario técnico (p. ej. `usuario.tecnico@<dominio>`).
2. Preferencias > **API Keys** (en algunas versiones: Seguridad de la cuenta > Nuevas claves de API).
3. Copia la clave (se muestra una sola vez) y pégala en `ODOO_PASSWORD` del `.env` del VPS.

### 8.4 Registro de configuración del bot

1. Programación Operativos > Configuración > **Configuración Bot > parametros**.
2. Crea **un** registro activo (el bot usa el más reciente que tenga `Activo` y `Activado` marcados) con:

| Campo | Valor |
|---|---|
| `Token` | Token de @BotFather. Si lo rotaste, usa el nuevo |
| `Webhook_secret` | El mismo valor que se usó al registrar el webhook (`openssl rand -hex 32`) |
| `Public_Domain` | `bot.viacotur.com` |
| `Internal_Token` | Token de la API interna (`openssl rand -hex 32`). Debe ser igual al del paso 8.5 |
| `Odoo_Public_URL` | URL pública de Odoo.sh, p. ej. `https://viacotur16.odoo.com` (la usan los enlaces del QR) |

Solo usuarios con permiso de Ajustes (`base.group_system`) ven este registro. Los campos `Token`, `Webhook_secret` e `Internal_Token` solo son visibles para administradores.

### 8.5 Parámetro de URL del bot

Ajustes > Técnico > **Parámetros del sistema** (ir.config_parameter):

| Clave | Valor |
|---|---|
| `telegram_bot.internal_url` | `https://bot.viacotur.com` |

El token interno **no** se guarda aquí: Odoo lo toma del campo `Internal_Token` del registro activo de `bot.telegram.config` (paso 8.4), la misma fuente que valida el bot. Odoo llama al bot con `Authorization: Bearer <internal_token>` sobre HTTPS.

> Desde `programacion_operativos` 16.0.1.0.0.16.19 el parámetro `telegram_bot.internal_token` se eliminó (quedaba duplicado). Los campos `Odoo_Username` y `Odoo_Password` también se eliminaron del registro: el bot se conecta a Odoo con su propio `.env`.

### 8.6 Protección de `/internal/` (TLS + token, y IP opcional)

- **Medida obligatoria: TLS + token Bearer.** Odoo.sh no tiene IP de salida fija. Sus llamadas salen de la infraestructura de Odoo y pueden cambiar. Por eso no se puede restringir por IP de forma fiable.
- **Medida opcional: restricción por IP.** Solo si Odoo pasa a tener IP fija (p. ej. un servidor propio): en `/etc/nginx/sites-enabled/nginx-telegram-bot.conf`, en el bloque `location /internal/`, descomenta:
  ```nginx
  allow <IP-fija-de-Odoo>;
  deny  all;
  ```
  Después: `sudo nginx -t && sudo systemctl reload nginx`.

### 8.7 Recarga de configuración

Cuando cambies valores del registro (salvo token y secreto del webhook, que se aplican al arrancar):

```bash
curl -s -X POST -H "Authorization: Bearer <internal_token>" https://bot.viacotur.com/internal/reload-config
# respuesta esperada: {"reloaded":true}
```

Si cambias el **token de Telegram** o el **webhook_secret**, reinicia el servicio:

```bash
sudo systemctl restart telegram-bot
```

---

## 9. Verificación end-to-end

```bash
# 9.1 Salud local y pública
curl -s http://127.0.0.1:8010/health            # {"status":"ok"}
curl -s https://bot.viacotur.com/health         # {"status":"ok"}

# 9.2 Webhook registrado en Telegram (sustituye <TOKEN> por el token)
curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
# url debe ser https://bot.viacotur.com/telegram/webhook y last_error_message vacío

# 9.3 Recarga de configuración
curl -s -X POST -H "Authorization: Bearer <internal_token>" https://bot.viacotur.com/internal/reload-config

# 9.4 Sin token, la API interna rechaza
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://bot.viacotur.com/internal/notify-ticket   # 401
```

Después, desde Telegram:

1. Envía `/start` al bot y comprueba que responde.
2. En Odoo.sh, aprueba un ticket vinculado a un chat. El pase debe llegar al chat y `journalctl -u telegram-bot` debe mostrar `POST /internal/notify-ticket 200`.

Prueba de arranque sin Odoo (opcional): detén Odoo.sh o cambia temporalmente `ODOO_URL` a un host inexistente, reinicia el servicio y comprueba en el log los reintentos `Odoo no disponible (intento N)`. Restaura el `.env` después.

## 10. Operación habitual

| Tarea | Comando |
|---|---|
| Ver estado | `systemctl status telegram-bot` |
| Ver logs | `journalctl -u telegram-bot -f` |
| Reiniciar | `sudo systemctl restart telegram-bot` |
| Recargar configuración | `POST /internal/reload-config` (paso 8.7) |
| Renovar certificado | Automático (`certbot.timer`). Prueba: `sudo certbot renew --dry-run` |

**Rotación del token de Telegram:** en @BotFather, `/revoke` y crea uno nuevo. Actualiza el campo `Token` del registro en Odoo (paso 8.4) y reinicia el servicio.

**Rotación de la API key de Odoo:** crea una nueva en Odoo.sh, actualiza `ODOO_PASSWORD` en `.env` y reinicia. Revoca la anterior.

---

## 11. Notas

- **Servidor anterior**: el certificado Let's Encrypt del servidor actual cubría `bot.viacotur.com` y `odoo.viacotur.com`. Cuando `bot.viacotur.com` deje de apuntar allí, emite de nuevo el certificado solo para `odoo.viacotur.com` (o elimina el dominio `bot` del certificado). Si no, la renovación fallará.
- **Desarrollo**: la base `qa2` local puede seguir como entorno de pruebas. Este bot en producción debe apuntar a la base de Odoo.sh.
- **Secretos**: `.env` y el registro `bot.telegram.config` contienen secretos. Restringe el acceso a los backups de la base de datos.
- **Pruebas**: `.venv/bin/python -m pytest -q` (136 pruebas).

---

## 12. Servidor de QA e integración con `telegram_bot_operativos`

En el servidor de QA este bot convive con Odoo local (`qa2`, puerto `8079`) y con el validador de QR [`telegram_bot_operativos`](https://github.com/DavidR84Dev/telegram_bot_operativos), que corre en Docker y llama a `POST /internal/notify-boarding` para avisar al pasajero de su abordaje.

```
Telegram ──HTTPS──► bot.viacotur.com ──► router (dst-nat 80/443) ──► servidor QA: nginx ──► :8010 (este bot)
                                                                                              ▲
Contenedor "app" de telegram_bot_operativos ──host.docker.internal:8010──────────────────────┘
Ambos bots ──XML-RPC──► Odoo local :8079 (base qa2)
```

### 12.1 Configuración específica de QA

| Elemento | Valor en QA |
|---|---|
| `ODOO_URL` (`.env`) | `http://127.0.0.1:8079` (Odoo en el mismo servidor; no usar la IP de la LAN, que puede cambiar) |
| `odoo_public_url` (`bot.telegram.config`) | URL de Odoo alcanzable por quien abra el QR (en QA, la IP LAN del servidor con `:8079`). El validador solo lee el token del QR, no el dominio |
| `telegram_bot.internal_url` (`ir.config_parameter`) | `http://127.0.0.1:8010` (Odoo local llama al bot sin pasar por nginx) |
| Escucha del bot | `0.0.0.0:8010` mediante el override [`deploy/telegram-bot.service.d/listen.conf`](deploy/telegram-bot.service.d/listen.conf), para que el contenedor del validador lo alcance |
| Firewall | Regla extra que permite **solo** a las redes de Docker llegar a Odoo y al bot |

Instalación del override y de la regla de firewall:

```bash
sudo mkdir -p /etc/systemd/system/telegram-bot.service.d
sudo cp deploy/telegram-bot.service.d/listen.conf /etc/systemd/system/telegram-bot.service.d/
sudo systemctl daemon-reload && sudo systemctl restart telegram-bot
sudo ufw allow proto tcp from 172.16.0.0/12 to any port 8079,8010
```

Con la política `deny incoming` de ufw (sección 7), el puerto 8010 sigue cerrado para la LAN e internet; solo lo alcanzan los contenedores. Para revertir: borrar `listen.conf`, `daemon-reload`, reiniciar y `sudo ufw delete allow proto tcp from 172.16.0.0/12 to any port 8079,8010`.

### 12.2 Cambio de IP del servidor (lección aprendida)

Si el servidor de QA obtiene otra IP por DHCP, se rompen varias piezas a la vez, aunque el código no cambie:

| Síntoma | Causa | Solución |
|---|---|---|
| Log: `Odoo no disponible (intento N)` en bucle | `ODOO_URL` apunta a la IP vieja | Usar `http://127.0.0.1:8079` y reiniciar |
| `getWebhookInfo` → `last_error_message: Connection refused` y `pending_update_count` creciendo | El router reenvía 80/443 a la IP vieja | Actualizar el dst-nat del router a la IP nueva |
| `getWebhookInfo` → `Connection timed out` y nada en `/var/log/nginx/access.log` | Una regla `filter`/`forward` del router aún acepta solo la IP vieja | Actualizar esa regla (y limpiar las conexiones viejas del router) |
| QR del pasajero abre una IP que no responde | `odoo_public_url` desactualizado | Corregirlo en `bot.telegram.config` y `POST /internal/reload-config` |

Prevención: reservar la IP del servidor en el DHCP del router.

Diagnóstico rápido:

```bash
curl -s http://127.0.0.1:8010/health                         # bot vivo
journalctl -u telegram-bot -n 40 --no-pager                  # arranque y errores
curl -s "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"  # errores de entrega del webhook
sudo tail -f /var/log/nginx/access.log                       # ¿llega tráfico externo?
sudo grep 'DPT=443' /var/log/ufw.log | tail                  # ¿ufw lo bloquea?
```

Cuando el webhook se restablece, Telegram entrega solo los mensajes en cola (los guarda hasta 24 h); no hace falta reiniciar el bot.

> Detrás del router los webhooks llegan con la IP del router como origen (masquerade). Si se activa la restricción por IP de la sección 8.6, limitar antes la regla `srcnat` del router a la interfaz WAN.
