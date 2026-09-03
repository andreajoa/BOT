# -*- coding: utf-8 -*-
"""TLS helpers using the operating-system trust store without disabling verification.

On macOS/Homebrew Python, urllib/websockets may not see certificates trusted by
Keychain. ``truststore`` bridges Python's SSLContext to the native OS trust
store. Verification and hostname checking remain enabled.
"""

from __future__ import annotations

import os
import ssl
from pathlib import Path


def create_ssl_context() -> ssl.SSLContext:
    """Return a verified TLS client context backed by the native trust store.

    ``CUSTOM_CA_BUNDLE`` can optionally point to an additional PEM bundle. It
    augments trust; it never disables certificate or hostname verification.
    """
    try:
        import truststore

        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        context = ssl.create_default_context()

    context.check_hostname = True
    context.verify_mode = ssl.CERT_REQUIRED

    custom_ca = os.getenv("CUSTOM_CA_BUNDLE", "").strip()
    if custom_ca:
        ca_path = Path(custom_ca).expanduser()
        if not ca_path.is_file():
            raise FileNotFoundError(f"CUSTOM_CA_BUNDLE nao encontrado: {ca_path}")
        context.load_verify_locations(cafile=str(ca_path))

    return context
