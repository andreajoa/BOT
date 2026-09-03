# -*- coding: utf-8 -*-
"""Process-wide TLS bootstrap for the bot runtime.

Loaded automatically by Python when ``runtime_bootstrap`` is on PYTHONPATH.
Uses macOS/Linux native trust stores while keeping certificate and hostname
verification enabled.
"""

try:
    import truststore

    truststore.inject_into_ssl()
except Exception:
    # Standard-library certificate verification remains active as fallback.
    # We deliberately never disable TLS verification here.
    pass
