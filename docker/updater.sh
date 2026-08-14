#!/usr/bin/env bash
# Wird von supervisord als Dauerprozess gestartet, wenn HVNB_AUTO_UPDATE_ENABLED=true.
# Prueft periodisch auf neue Commits im konfigurierten Branch und fuehrt bei
# Aenderungen automatisch ein Redeploy (git pull, Abhaengigkeiten, Frontend-Build,
# Neustart von Backend/nginx) durch.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/app}"
GIT_BRANCH="${HVNB_GIT_BRANCH:-main}"
INTERVAL_MINUTES="${HVNB_AUTO_UPDATE_INTERVAL_MINUTES:-15}"

log() { echo "[updater] $(date -u +%FT%TZ) $*"; }

if [ "${HVNB_AUTO_UPDATE_ENABLED:-false}" != "true" ]; then
  log "Auto-Update deaktiviert, Prozess beendet sich."
  exit 0
fi

while true; do
  sleep "$((INTERVAL_MINUTES * 60))"

  git -C "${APP_DIR}" fetch --quiet origin "${GIT_BRANCH}"
  LOCAL_REV="$(git -C "${APP_DIR}" rev-parse HEAD)"
  REMOTE_REV="$(git -C "${APP_DIR}" rev-parse "origin/${GIT_BRANCH}")"

  if [ "${LOCAL_REV}" = "${REMOTE_REV}" ]; then
    continue
  fi

  log "Neue Version gefunden (${LOCAL_REV:0:7} -> ${REMOTE_REV:0:7}), aktualisiere..."
  git -C "${APP_DIR}" reset --hard "origin/${GIT_BRANCH}"
  python3 -m pip install --quiet --no-cache-dir "${APP_DIR}/backend"

  pushd "${APP_DIR}/frontend" >/dev/null
  npm ci --no-audit --no-fund --silent
  npm run build --silent
  popd >/dev/null

  log "Starte uvicorn und nginx neu..."
  supervisorctl -c /etc/supervisord.conf restart uvicorn
  supervisorctl -c /etc/supervisord.conf restart nginx
  log "Update abgeschlossen (${REMOTE_REV:0:7})."
done
