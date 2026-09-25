# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

from .client import GitHubUnavailable


def _fernet():
    # A dedicated rotation key keeps GitHub App credentials distinct from every
    # other use of SECRET_KEY. GITHUB_DELIVERY_SECRET_KEY allows rotating the
    # encryption key independently of the Django secret.
    dedicated = getattr(settings, "GITHUB_DELIVERY_SECRET_KEY", "") or os.environ.get("GITHUB_DELIVERY_SECRET_KEY", "")
    material = (dedicated or settings.SECRET_KEY).encode() + b"plane-github-delivery-v1"
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def encrypt_secret(value):
    if not isinstance(value, str) or not value:
        raise GitHubUnavailable("GitHub credential storage rejected an empty value.")
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value):
    if not isinstance(value, str) or not value:
        raise GitHubUnavailable("A stored GitHub credential is missing.")
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as error:
        raise GitHubUnavailable(
            "Stored GitHub credentials could not be read. Reconnect the GitHub account."
        ) from error
