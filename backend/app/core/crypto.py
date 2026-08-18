"""Verschluesselung von Secrets (z.B. NetApp-Cluster-Kennwoerter) fuer die
Ablage in der DB. Nutzt Fernet (symmetrisch) mit einem aus SECRET_KEY
abgeleiteten Schluessel, damit kein zusaetzliches Secret verwaltet werden muss.
"""

import base64
import hashlib

from cryptography.fernet import Fernet

from app.core.config import get_settings


def _fernet() -> Fernet:
    settings = get_settings()
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
