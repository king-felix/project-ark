"""Shared test fixtures: a throwaway box per test, and a client for the router.

Every test gets its own ARK_ROOT under tmp_path, so nothing here can touch a
real library, a real database or a real reader.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

API_DIR = Path(__file__).resolve().parents[1] / "pi" / "api"
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

MODULES = ("main", "ark_admin", "ark_security", "ark_db")


@pytest.fixture()
def ark(tmp_path, monkeypatch):
    """A fresh, isolated box: database, marker and drop directory in tmp_path."""
    root = tmp_path / "srv-ark"
    for sub in ("api/data", "drop/files", "kiwix", "vault"):
        (root / sub).mkdir(parents=True)
    monkeypatch.setenv("ARK_ROOT", str(root))
    monkeypatch.setenv("ARK_DB", str(root / "api/data/ark.db"))
    monkeypatch.setenv("ARK_MARKER", str(root / "api/data/ark.installed"))
    monkeypatch.setenv("ARK_TRUSTED_PROXIES", "testclient,127.0.0.1,::1")
    monkeypatch.setenv("KIWIX_INTERNAL", "http://127.0.0.1:9/kiwix")   # nothing listens
    for name in MODULES:
        sys.modules.pop(name, None)

    import ark_admin
    import ark_db
    import ark_security

    ark_admin.ensure_initialised()
    yield SimpleNamespace(db=ark_db, sec=ark_security, admin=ark_admin, root=root)
    for name in MODULES:
        sys.modules.pop(name, None)


@pytest.fixture()
def client(ark):
    """A client for the admin router alone — the real one, mounted the same way."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(ark.admin.router)
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def public(ark):
    """The whole portal app, as the Pi runs it."""
    import main

    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        yield SimpleNamespace(client=c, app=main, ark=ark)
