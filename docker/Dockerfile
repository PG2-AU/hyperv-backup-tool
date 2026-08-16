# Hyper-V NetApp Backup Tool - Container-Image (Rocky Linux)
#
# Das Image enthaelt nur die Runtime (Python, Node.js, nginx, git, supervisor).
# Der eigentliche Anwendungscode wird beim Container-Start per `git pull`
# aus dem konfigurierten Repository geladen (siehe entrypoint.sh), damit
# immer die aktuellste gepushte Version ausgefuehrt werden kann, ohne das
# Image neu bauen zu muessen.

FROM rockylinux:9

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    APP_DIR=/opt/app \
    HVNB_TLS_CERT_PATH=/etc/hvnb/certs/server.crt \
    HVNB_TLS_KEY_PATH=/etc/hvnb/certs/server.key

RUN dnf install -y --setopt=install_weak_deps=False epel-release \
    && dnf install -y --setopt=install_weak_deps=False \
        python3.12 python3.12-pip \
        git nginx supervisor openssl gettext shadow-utils \
    && dnf module -y reset nodejs \
    && dnf module -y install nodejs:20 \
    && dnf clean all \
    && alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 \
    && python3 -m pip install --upgrade pip

RUN useradd --system --create-home --home-dir /home/hvnb --shell /bin/bash hvnb

RUN mkdir -p ${APP_DIR} /etc/hvnb/certs /var/log/hvnb /data \
    && chown -R hvnb:hvnb ${APP_DIR} /etc/hvnb/certs /var/log/hvnb /data

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/updater.sh /usr/local/bin/updater.sh
COPY docker/gen-selfsigned-cert.sh /usr/local/bin/gen-selfsigned-cert.sh
COPY docker/nginx.conf.template /etc/nginx/templates/hvnb.conf.template
COPY docker/supervisord.conf /etc/supervisord.conf

RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/updater.sh /usr/local/bin/gen-selfsigned-cert.sh

EXPOSE 443

VOLUME ["/data", "/etc/hvnb/certs"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
