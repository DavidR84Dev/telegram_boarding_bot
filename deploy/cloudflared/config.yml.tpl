# cloudflared — plantilla de referencia
# El archivo operativo es config.yml (mismo directorio), ya con el UUID real.
# Este template sirve para regenerar el config en otro entorno:
#   sed -e 's/TUNNEL_UUID/<uuid>/' -e 's/BOT_DOMAIN/bot.../' \
#       -e 's/ODOO_DOMAIN/odoo.../' -e 's|CRED_FILE|/home/odoo/.cloudflared/<uuid>.json|' \
#       config.yml.tpl > config.yml

tunnel: TUNNEL_NAME
credentials-file: CRED_FILE

ingress:
  - hostname: BOT_DOMAIN
    service: http://127.0.0.1:8010
  - hostname: ODOO_DOMAIN
    service: http://127.0.0.1:8079
  - service: http_status:404
