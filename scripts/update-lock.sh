#!/usr/bin/env bash
# Erzeugt backend/requirements.lock neu (Versionen + Pruefsummen aller Python-
# Abhaengigkeiten des Release-Images) -- in einem Rocky-9-Container mit Python
# 3.12, damit die Aufloesung zur Laufzeitumgebung des Images passt.
#
#   scripts/update-lock.sh              bestehende Versionen behalten, nur Fehlendes ergaenzen
#   scripts/update-lock.sh --upgrade    alles auf die neuesten erlaubten Versionen heben
#
# Braucht Internetzugang (PyPI) und podman. Das Ergebnis vor dem Commit ansehen
# (git diff) und das damit gebaute Image testen.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE="${CONTAINER_ENGINE:-podman}"
UPGRADE=""
[ "${1:-}" = "--upgrade" ] && UPGRADE="--upgrade"

"${ENGINE}" run --rm -v "${ROOT}/backend:/src:Z" quay.io/rockylinux/rockylinux:9 bash -euc "
  dnf install -y -q python3.12 python3.12-pip python3.12-devel gcc krb5-devel >/dev/null
  python3.12 -m pip install -q pip-tools >/dev/null
  cd /tmp && cp /src/pyproject.toml . && cp /src/requirements.lock . 2>/dev/null || true
  echo 'supervisor' > extra.in
  python3.12 -m piptools compile ${UPGRADE} --quiet --generate-hashes --allow-unsafe --strip-extras \
    --no-header -o requirements.lock pyproject.toml extra.in
  { sed -n '/^[a-z0-9]/q;p' /src/requirements.lock 2>/dev/null; sed -n '/^[a-z0-9]/,\$p' requirements.lock; } > /src/requirements.lock.new
  mv /src/requirements.lock.new /src/requirements.lock
"
echo "backend/requirements.lock aktualisiert -- bitte 'git diff backend/requirements.lock' pruefen."
