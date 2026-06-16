"""Shared pytest setup for the commission test suite.

Two things every test relies on:

* The repo root must be importable as ``src.*`` regardless of where pytest is
  launched from.
* Roster / people config must be deterministic. ``roster.py`` will read the
  business ``Config_People`` sheet from the master template at import time when
  present, which would make assertions depend on a binary file that changes.
  Setting ``COMMISSION_NO_TEMPLATE_CONFIG`` forces the in-code defaults
  (Paul Perlman -> "Paul", etc.) so the tests pin a known roster.

Both must happen BEFORE any ``src.commission`` module is imported, so they live
here in the top-level conftest.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Force the in-code roster/people defaults (ignore the template's Config_People).
os.environ["COMMISSION_NO_TEMPLATE_CONFIG"] = "1"

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# CRITICAL SAFETY: the tests seed throwaway databases and MUST NEVER touch the
# production Postgres instance. ``DATABASE_URL`` (from the environment or the
# project ``.env``, which connection.py loads at import) would otherwise make
# every ``get_connection(db_path=...)`` ignore the temp path and hit Postgres.
# We neutralize it at the source: blank the module-level constant that both
# ``get_connection`` and ``init_database`` read, then hard-assert we are on
# SQLite before any test runs.
os.environ.pop("DATABASE_URL", None)

from src.db import connection as _connection  # noqa: E402

_connection.DATABASE_URL = ""

assert not _connection.using_postgres(), (
    "Refusing to run tests against Postgres — DATABASE_URL is still set. "
    "Tests must run on a throwaway SQLite database only."
)
