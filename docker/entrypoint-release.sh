#!/usr/bin/env bash
# Startpunkt des Release-Images (docker/Dockerfile.release): die Anwendung ist
# fertig im Image enthalten, hier wird nichts geladen oder gebaut. Erzeugt bei
# Bedarf ein selbstsigniertes TLS-Zertifikat, rendert die nginx-Konfiguration
# und startet Backend + nginx via supervisord.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/app}"

log() { echo "[entrypoint] $(date -u +%FT%TZ) $*"; }

VERSION="$(python3 -c "import json;print(json.load(open('${APP_DIR}/VERSION.json')).get('version','?'))" 2>/dev/null || echo '?')"
log "AU Storage Manager for Hyper-V, Release ${VERSION}"

if [ -n "${HVNB_GIT_REPO_URL:-}" ] || [ "${HVNB_AUTO_UPDATE_ENABLED:-false}" = "true" ]; then
  log "Hinweis: HVNB_GIT_REPO_URL/HVNB_AUTO_UPDATE_* werden vom Release-Image nicht mehr ausgewertet und koennen aus der .env entfernt werden."
fi

if [ ! -f "${HVNB_TLS_CERT_PATH}" ] || [ ! -f "${HVNB_TLS_KEY_PATH}" ]; then
  log "Kein TLS-Zertifikat gefunden, erzeuge selbstsigniertes Zertifikat..."
  /usr/local/bin/gen-selfsigned-cert.sh
fi

log "Rendere nginx-Konfiguration..."
export FRONTEND_DIST="${APP_DIR}/frontend/dist"
envsubst '${FRONTEND_DIST} ${HVNB_TLS_CERT_PATH} ${HVNB_TLS_KEY_PATH}' \
  < /etc/nginx/templates/hvnb.conf.template > /etc/nginx/conf.d/hvnb.conf

log "Starte supervisord (uvicorn + nginx)..."
exec supervisord -c /etc/supervisord.conf
