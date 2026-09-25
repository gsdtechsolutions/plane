# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Token encryption at rest for Asana connections.

Fernet (AES-128-CBC + HMAC) with a key derived from the deployment SECRET_KEY,
so no extra infrastructure is required and the plaintext PAT is never stored
or returned by any API. Rotating SECRET_KEY invalidates stored tokens — the
connection "verify" action surfaces that as a re-auth prompt.
"""

# Python imports
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


class AsanaCryptoError(Exception):
    """Raised when an encrypted token cannot be decrypted (e.g. SECRET_KEY rotated)."""


def _fernet() -> Fernet:
    digest = hashlib.sha256(("asana-sync:" + str(settings.SECRET_KEY)).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_token(plain: str) -> str:
    return _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt_token(encrypted: str) -> str:
    try:
        return _fernet().decrypt(encrypted.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise AsanaCryptoError("Stored Asana token could not be decrypted; reconnect the integration.") from exc
