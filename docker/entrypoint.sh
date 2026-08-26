#!/usr/bin/env bash
# Startpunkt des Containers: laedt/aktualisiert den Anwendungscode per Git,
# installiert Abhaengigkeiten, baut das Frontend, erzeugt bei Bedarf ein
# selbstsigniertes TLS-Zertifikat und startet Backend + nginx via supervisord.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/app}"
GIT_REPO_URL="${HVNB_GIT_REPO_URL:-}"
GIT_BRANCH="${HVNB_GIT_BRANCH:-main}"

log() { echo "[entrypoint] $(date -u +%FT%TZ) $*"; }

# Podman mountet /sys standardmaessig read-only in den Container (auch
# rootful, auch mit CAP_SYS_ADMIN). Der Kernel registriert neu erscheinende
# iSCSI-Blockgeraete zwar trotzdem, aber der VHDX-Restore-Workflow braucht
# Schreibzugriff auf sysfs fuer den Scan-Trigger; ohne Remount blieb die
# geklonte LUN unauffindbar (gegen echte Hardware verifiziert). CAP_SYS_ADMIN
# ist bereits Voraussetzung fuer den Mount-Schritt des Restores, daher kein
# zusaetzliches Risiko.
mount -o remount,rw /sys 2>/dev/null || log "WARNUNG: /sys konnte nicht read-write remountet werden (Restore-iSCSI-Scan koennte fehlschlagen)."

# Unter rootless Podman/Docker kann der abgebildete UID-Bereich vom
# Dateibesitzer der gemounteten/erstellten Verzeichnisse abweichen; git
# verweigert Operationen dann mit "dubious ownership". APP_DIR ist unser
# eigener Container, daher ist die Freigabe unbedenklich.
git config --global --add safe.directory "${APP_DIR}"

if [ -z "${GIT_REPO_URL}" ]; then
  log "FEHLER: HVNB_GIT_REPO_URL ist nicht gesetzt. Ohne Repository kann kein Code geladen werden."
  exit 1
fi

if [ ! -d "${APP_DIR}/.git" ]; then
  log "Kein bestehendes Repository gefunden, klone ${GIT_REPO_URL} (${GIT_BRANCH})..."
  git clone --branch "${GIT_BRANCH}" --single-branch "${GIT_REPO_URL}" "${APP_DIR}"
else
  log "Bestehendes Repository gefunden, hole aktuellste Version (${GIT_BRANCH})..."
  git -C "${APP_DIR}" fetch --prune origin "${GIT_BRANCH}"
  git -C "${APP_DIR}" checkout "${GIT_BRANCH}"
  git -C "${APP_DIR}" reset --hard "origin/${GIT_BRANCH}"
fi

log "Installiere Backend-Abhaengigkeiten..."
python3 -m pip install --quiet --no-cache-dir "${APP_DIR}/backend"

log "Baue Frontend..."
pushd "${APP_DIR}/frontend" >/dev/null
npm ci --no-audit --no-fund
npm run build
popd >/dev/null

if [ ! -f "${HVNB_TLS_CERT_PATH}" ] || [ ! -f "${HVNB_TLS_KEY_PATH}" ]; then
  log "Kein TLS-Zertifikat gefunden, erzeuge selbstsigniertes Zertifikat..."
  /usr/local/bin/gen-selfsigned-cert.sh
fi

log "Rendere nginx-Konfiguration..."
export FRONTEND_DIST="${APP_DIR}/frontend/dist"
envsubst '${FRONTEND_DIST} ${HVNB_TLS_CERT_PATH} ${HVNB_TLS_KEY_PATH}' \
  < /etc/nginx/templates/hvnb.conf.template > /etc/nginx/conf.d/hvnb.conf

if [ "${HVNB_AUTO_UPDATE_ENABLED:-false}" = "true" ]; then
  log "Auto-Update ist aktiviert (alle ${HVNB_AUTO_UPDATE_INTERVAL_MINUTES:-15} Minuten)."
else
  log "Auto-Update ist deaktiviert. Ein Redeploy erfordert einen Container-Neustart."
fi

log "Starte supervisord (uvicorn + nginx${HVNB_AUTO_UPDATE_ENABLED:+ + updater})..."
exec supervisord -c /etc/supervisord.conf
