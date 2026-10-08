#!/usr/bin/env bash
# Baut das Release-Image (docker/Dockerfile.release) und daraus die Paketdatei
# fuer die Offline-Installation.
#
#   scripts/build-release.sh <version>        z.B. scripts/build-release.sh 1.0.0
#
# Ergebnis in dist/:
#   hvnb-<version>.tar.gz          Image als Archiv (auf dem Zielserver: podman load)
#   hvnb-<version>.tar.gz.sha256   Pruefsumme
#   hvnb-<version>.tar.gz.sig      Signatur, nur wenn HVNB_RELEASE_SIGNING_KEY gesetzt ist
#                                  (Pfad zu einem privaten Schluessel im PEM-Format)
#   hvnb-update                    Einspiel-Skript fuer den Zielserver (Kopie von scripts/)
# sowie das Image lokal als localhost/hvnb-backup:<version>.
#
# Gebaut wird der committete Stand (HEAD). Nicht committete Aenderungen brechen
# den Build ab, damit ein Paket immer genau einem Commit entspricht
# (HVNB_ALLOW_DIRTY=1 hebt das fuer Testbauten auf).
set -euo pipefail

VERSION="${1:-}"
if ! [[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.]+)?$ ]]; then
  echo "Aufruf: $0 <version>   (z.B. 1.0.0 oder 1.0.0-rc1)" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE="${CONTAINER_ENGINE:-podman}"
IMAGE="${HVNB_IMAGE_NAME:-localhost/hvnb-backup}"
cd "${ROOT}"

if [ -n "$(git status --porcelain)" ] && [ "${HVNB_ALLOW_DIRTY:-0}" != "1" ]; then
  echo "Abbruch: nicht committete Aenderungen im Arbeitsverzeichnis (git status)." >&2
  exit 1
fi

COMMIT="$(git rev-parse HEAD)"
mkdir -p release-build dist

# VERSION.json / CHANGELOG.json: im Image gibt es kein .git, die App liest
# Version und Versionsverlauf aus diesen Dateien (backend/app/core/release.py).
python3 - "${VERSION}" "${COMMIT}" <<'PY'
import json, subprocess, sys
from datetime import datetime, timezone

version, commit = sys.argv[1], sys.argv[2]
count = int(subprocess.check_output(["git", "rev-list", "--count", "HEAD"], text=True).strip())
with open("release-build/VERSION.json", "w", encoding="utf-8") as fh:
    json.dump(
        {"version": version, "commit": commit, "commit_count": count, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
        fh, indent=2,
    )
fs, rs = "\x1f", "\x1e"
log = subprocess.check_output(
    ["git", "log", "-n500", f"--pretty=format:%H{fs}%h{fs}%ad{fs}%s{fs}%b{rs}", "--date=iso-strict"], text=True,
)
entries = []
for record in log.split(rs):
    parts = record.strip("\n").split(fs)
    if len(parts) < 4 or not parts[0]:
        continue
    body = parts[4].strip() if len(parts) > 4 and parts[4].strip() else None
    entries.append({"hash": parts[0], "short_hash": parts[1], "date": parts[2], "subject": parts[3], "body": body})
with open("release-build/CHANGELOG.json", "w", encoding="utf-8") as fh:
    json.dump(entries, fh, ensure_ascii=False)
PY

echo "== Baue ${IMAGE}:${VERSION} (Commit ${COMMIT:0:7})"
"${ENGINE}" build -f docker/Dockerfile.release \
  --build-arg "HVNB_VERSION=${VERSION}" --build-arg "HVNB_COMMIT=${COMMIT}" \
  -t "${IMAGE}:${VERSION}" .

PACKAGE="dist/hvnb-${VERSION}.tar.gz"
echo "== Schreibe ${PACKAGE}"
"${ENGINE}" save "${IMAGE}:${VERSION}" | gzip -9 > "${PACKAGE}"
( cd dist && sha256sum "hvnb-${VERSION}.tar.gz" > "hvnb-${VERSION}.tar.gz.sha256" )

if [ -n "${HVNB_RELEASE_SIGNING_KEY:-}" ]; then
  openssl dgst -sha256 -sign "${HVNB_RELEASE_SIGNING_KEY}" -out "${PACKAGE}.sig" "${PACKAGE}"
  echo "== Signiert: ${PACKAGE}.sig"
else
  echo "== Hinweis: nicht signiert (HVNB_RELEASE_SIGNING_KEY nicht gesetzt)"
fi

# Das Einspiel-Skript gehoert zum Paket, damit es auch ohne Repository-Zugang
# auf den Zielserver kommt.
cp scripts/hvnb-update dist/hvnb-update
cp scripts/hvnb-git-autoupdate dist/hvnb-git-autoupdate

ls -lh "${PACKAGE}"* dist/hvnb-update
