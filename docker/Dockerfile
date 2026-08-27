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
    HVNB_TLS_KEY_PATH=/etc/hvnb/certs/server.key \
    NODE_OPTIONS=--dns-result-order=ipv4first

# Unter network_mode: host (siehe docker-compose.yml, fuer den iSCSI-Restore-
# Workflow noetig) sieht der Container die IPv6-Adressen des Hosts, die in
# dieser Umgebung nicht tatsaechlich geroutet werden -- Node/npm versuchen
# ohne diese Option zuerst IPv6 und haengen minutenlang, bevor sie auf IPv4
# zurueckfallen (gegen echten Build verifiziert: 'npm ci' ging von >5 Minuten
# Haenger auf 7 Sekunden). curl/plain DNS sind davon nicht betroffen, nur
# Nodes eigener dualstack-Resolver.

RUN dnf install -y --setopt=install_weak_deps=False \
        python3.12 python3.12-pip \
        git nginx openssl gettext shadow-utils \
        epel-release \
    && dnf module -y reset nodejs \
    && dnf module -y install nodejs:20 \
    && dnf install -y --setopt=install_weak_deps=False \
        iscsi-initiator-utils cifs-utils samba-client ntfs-3g kpartx parted \
    && dnf clean all \
    && alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 \
    && python3 -m pip install --upgrade pip \
    && python3 -m pip install --no-cache-dir supervisor

# Werkzeuge fuer den VHDX-Restore-Workflow (siehe restore_infra.py):
# - iscsi-initiator-utils: iSCSI-Login zur geklonten LUN
# - cifs-utils/samba-client: Kopieren der wiederhergestellten VHDX auf die
#   Hyper-V-CSV per SMB (administrative Freigabe C$)
# - ntfs-3g: Lesezugriff auf die NTFS/CSVFS-formatierte Partition der LUN
# - kpartx/parted: Partitionstabelle der geklonten LUN erkennen/einbinden
# Fuer echte iSCSI-/Mount-Operationen braucht der Container zusaetzlich
# CAP_SYS_ADMIN und Blockgeraete-Zugriff -- das kann NICHT ueber dieses
# Image/den Requirements-Wizard automatisiert werden, sondern erfordert eine
# einmalige Anpassung der Container-Startparameter (siehe Settings >
# Restore-Setup > Anforderungen fuer die exakten Flags).

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
