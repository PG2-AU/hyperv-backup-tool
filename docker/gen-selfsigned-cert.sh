#!/usr/bin/env bash
# Erzeugt ein selbstsigniertes Zertifikat fuer den ersten Start, falls kein
# echtes Zertifikat (z.B. von einer internen CA) unter /etc/hvnb/certs
# eingebunden wurde. Fuer Produktivbetrieb sollte ein von der internen PKI
# ausgestelltes Zertifikat gemountet werden.
set -euo pipefail

CERT_PATH="${HVNB_TLS_CERT_PATH:-/etc/hvnb/certs/server.crt}"
KEY_PATH="${HVNB_TLS_KEY_PATH:-/etc/hvnb/certs/server.key}"
COMMON_NAME="${HVNB_TLS_COMMON_NAME:-hvnb-backup.local}"

mkdir -p "$(dirname "${CERT_PATH}")"

openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
  -keyout "${KEY_PATH}" \
  -out "${CERT_PATH}" \
  -subj "/CN=${COMMON_NAME}/O=Hyper-V NetApp Backup" \
  -addext "subjectAltName=DNS:${COMMON_NAME}"

chmod 600 "${KEY_PATH}"
