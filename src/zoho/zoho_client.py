"""
Zoho Books API client.

Implementation is in ``src/zoho_client.py`` (legacy path used by sync and backend).
This module re-exports the public API for ``from src.zoho.zoho_client import ...``.
"""

from src.zoho_client import (
    ZohoApiError,
    ZohoAuthError,
    ZohoBooksClient,
    ZohoConfig,
    load_zoho_config,
)

__all__ = [
    "ZohoApiError",
    "ZohoAuthError",
    "ZohoBooksClient",
    "ZohoConfig",
    "load_zoho_config",
]
