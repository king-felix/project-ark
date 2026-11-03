"""The defining test, in a real browser.

    download a region → make the Ark unreachable → close and reopen →
    pan to a place never seen → search a place never searched →
    calculate a NEW route → read the directions → export a route sheet

The pack is built from made-up OpenStreetMap objects, so this test needs no
network and no country extract.  What it proves is the machinery: the same
`graph.bin`, `places.json` and layer files the real builder writes, the same
router and renderer, and the same service worker.  The Liechtenstein pack is
exercised separately against the live box, where its size and data date are real.

The browser is Chromium with the test certificate pinned by SPKI.  That is the
precise equivalent of a phone that has installed the Ark's certificate: Chrome
refuses to register a service worker on an origin with a certificate error, which
is why a click-through warning is not enough (and why the help page says so).
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import socket
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import Error as PlaywrightError, sync_playwright  # noqa: E402

import ark_maps  # noqa: E402

EVIDENCE = Path(__file__).resolve().parents[1] / "logs" / "maps" / "evidence"


class DemoSource:
    """A small square with two roads, a path, a pharmacy and three names."""

    def points(self):
        return [
            (1, {"place": "town", "name": "Alpha"}, 47.0000, 8.0000),
            (2, {"place": "village", "name": "Beta"}, 47.0100, 8.0100),
            (3, {"place": "hamlet", "name": "Gamma"}, 47.0000, 8.0100),
            (4, {"amenity": "pharmacy", "name": "Central Pharmacy"}, 47.0050, 8.0050),
            (5, {"place": "hamlet", "name": "Delta"}, 47.0100, 8.0000),
        ]

    def ways(self):
        return [
            (10, {"highway": "residential", "name": "North Road"},
             [(100, 47.0000, 8.0000), (101, 47.0100, 8.0000),
              (102, 47.0100, 8.0100)]),
            (11, {"highway": "residential", "name": "West Road"},
             [(102, 47.0100, 8.0100), (103, 47.0000, 8.0100),
              (100, 47.0000, 8.0000)]),
            (12, {"highway": "footway", "name": "Diagonal Path"},
             [(100, 47.0000, 8.0000), (104, 47.0050, 8.0050),
              (102, 47.0100, 8.0100)]),
            (13, {"building": "yes", "name": "Hall"},
             [(200, 47.0050, 8.0050), (201, 47.0050, 8.0060),
              (202, 47.0056, 8.0060), (203, 47.0056, 8.0050), (200, 47.0050, 8.0050)]),
            (14, {"natural": "water"},
             [(300, 46.9990, 8.0030), (301, 46.9990, 8.0040),
              (302, 46.9995, 8.0040), (303, 46.9995, 8.0030), (300, 46.9990, 8.0030)]),
        ]

    def relations(self):
        return []


def _zip_pack(folder: Path, target: Path) -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                bundle.write(path, "%s/%s" % (folder.name, path.relative_to(folder)))
    return target


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _spki(cert: Path) -> str:
    """Base64 SHA-256 of the certificate's public key, as Chromium wants it."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        parsed = x509.load_pem_x509_certificate(cert.read_bytes())
        der = parsed.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        return base64.b64encode(hashlib.sha256(der).digest()).decode()
    except Exception:
        public = subprocess.run(
            ["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"],
            check=True, capture_output=True)
        der = subprocess.run(
            ["openssl", "pkey", "-pubin", "-outform", "der"],
            input=public.stdout, check=True, capture_output=True).stdout
        return base64.b64encode(hashlib.sha256(der).digest()).decode()


@pytest.fixture()
def maps_box(ark, tmp_path):
    """A live HTTPS Ark with one published demo pack and the real app."""
    if not shutil.which("openssl"):
        pytest.skip("openssl is not available to make the test certificate")
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "2",
        "-subj", "/CN=ark.test", "-addext", "subjectAltName=DNS:ark.test",
    ], check=True, capture_output=True)
    ark.root.joinpath("api/data").mkdir(parents=True, exist_ok=True)
    import os
    os.environ["ARK_CERT_PATH"] = str(cert)

    from ark_map_store import Store
    store = Store()
    folder = tmp_path / "demo-pack"
    ark_maps.build_pack(
        str(folder), DemoSource(), source_url="https://example.org/demo.osm.pbf",
        region_name="Demo Region", region="DR", pack_version="2026-11-01",
        data_date="2026-11-01", source_sha256="b" * 64, pack_id="demo-region",
        built="2026-11-02T00:00:00Z", limitations="A demonstration region.")
    archive = _zip_pack(folder, tmp_path / "demo.zip")
    with archive.open("rb") as handle:
        store.import_zip(handle, filename="demo.zip")
    store.publish("demo-region", "2026-11-01", True)

    import uvicorn
    import main
    port = _free_port()
    config = uvicorn.Config(main.app, host="127.0.0.1", port=port,
                            ssl_certfile=str(cert), ssl_keyfile=str(key),
                            log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    if not server.started:
        pytest.skip("the test HTTPS server did not start")
    yield {"port": port, "cert": cert, "spki": _spki(cert), "root": ark.root}
    server.should_exit = True
    thread.join(timeout=10)


def _launch(port: int, spki: str):
    playback = sync_playwright().start()
    browser = playback.chromium.launch(args=[
        "--host-resolver-rules=MAP ark.test 127.0.0.1",
        f"--ignore-certificate-errors-spki-list={spki}",
    ])
    context = browser.new_context(viewport={"width": 420, "height": 900},
                                  accept_downloads=True)
    return playback, browser, context


def test_a_downloaded_region_reopens_and_routes_with_no_network(maps_box):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    url = "https://ark.test:%d/maps/" % maps_box["port"]
    try:
        playback, browser, context = _launch(maps_box["port"], maps_box["spki"])
    except PlaywrightError as exc:
        pytest.skip("Chromium could not start: %s" % (exc,))
    try:
        page = context.new_page()
        errors = []
        responses = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("response", lambda response: responses.append(
            (time.time(), response.status, response.url, response.from_service_worker)))

        page.goto(url, wait_until="load")
        page.get_by_role("button", name="Download for offline use").first.wait_for(timeout=15000)
        assert "does not download a region" in page.inner_text("#packs-view")

        page.get_by_role("button", name="Download for offline use").first.click()
        page.locator("#map-view").wait_for(state="visible", timeout=60000)
        assert page.locator("#offline-badge").is_visible()
        assert "Ready offline" in page.inner_text("#offline-badge")
        page.screenshot(path=str(EVIDENCE / "01-map-online.png"), full_page=True)

        # Both the Ark and the internet are now unreachable to this browser.
        context.set_offline(True)
        offline_mark = time.time()
        page.reload(wait_until="load")
        page.locator("#map-view").wait_for(state="visible", timeout=20000)
        assert "Maps" in page.title()
        assert page.locator("#offline-badge").is_visible()

        # Pan and zoom somewhere the first view did not show at this detail.
        page.locator("#map").focus()
        for _ in range(4):
            page.keyboard.press("+")
        for _ in range(6):
            page.keyboard.press("ArrowRight")
        page.wait_for_timeout(400)
        assert "zoom" in page.inner_text("#map-live")
        assert errors == [], errors
        page.screenshot(path=str(EVIDENCE / "02-map-offline-panned.png"), full_page=True)

        # A destination never searched before disconnection.
        page.fill("#from-input", "Alpha")
        page.locator("#from-suggestions button").first.wait_for(timeout=5000)
        page.locator("#from-suggestions button").first.click()
        page.fill("#to-input", "Beta")
        page.locator("#to-suggestions button").first.wait_for(timeout=5000)
        page.locator("#to-suggestions button").first.click()
        page.click("#route-button")
        page.locator("#route-panel").wait_for(state="visible", timeout=10000)
        page.wait_for_function("document.querySelector('#route-summary').textContent.length > 0")
        assert "Route ready" in page.inner_text("#route-status")
        steps = page.locator("#directions li").count()
        assert steps >= 3, page.inner_text("#directions")
        page.screenshot(path=str(EVIDENCE / "03-route-offline.png"), full_page=True)

        # A route sheet with a map and directions, and a GPX file.
        with page.expect_download(timeout=15000) as download:
            page.click("#download-sheet")
        sheet_path = Path(download.value.path())
        sheet = sheet_path.read_text(encoding="utf-8")
        assert "Route sheet" in sheet and "<svg" in sheet
        assert "Alpha" in sheet and "Beta" in sheet
        assert "OpenStreetMap" in sheet
        with page.expect_download(timeout=10000) as download:
            page.click("#download-gpx")
        gpx = Path(download.value.path()).read_text(encoding="utf-8")
        assert "<gpx" in gpx and "<trkpt" in gpx

        # Close the page and reopen it with nothing reachable: the service
        # worker, the pack and the graph must all still be there.
        page.close()
        reopened = context.new_page()
        reopened = context.new_page()
        reopened.goto(url, wait_until="load")
        reopened.wait_for_timeout(2000)
        reopened.locator("#map-view").wait_for(state="visible", timeout=20000)
        assert reopened.locator("#offline-badge").is_visible()

        # A NEW route between two different places, after the reopen.
        reopened.fill("#from-input", "Gamma")
        reopened.locator("#from-suggestions button").first.wait_for(timeout=5000)
        reopened.locator("#from-suggestions button").first.click()
        reopened.fill("#to-input", "Delta")
        reopened.locator("#to-suggestions button").first.wait_for(timeout=5000)
        reopened.locator("#to-suggestions button").first.click()
        reopened.click("#route-button")
        reopened.locator("#route-panel").wait_for(state="visible", timeout=10000)
        reopened.wait_for_function("document.querySelector('#route-summary').textContent.length > 0")
        assert "Route ready" in reopened.inner_text("#route-status")
        assert reopened.locator("#directions li").count() >= 2
        reopened.screenshot(path=str(EVIDENCE / "04-reopened-new-route.png"), full_page=True)

        # Nothing that succeeded after the disconnect came from the network.
        leaked = [row for row in responses
                  if row[0] > offline_mark and row[1] < 400 and not row[3]]
        assert leaked == [], leaked
        assert errors == [], errors
    finally:
        context.close()
        browser.close()
        playback.stop()


def test_a_paused_download_keeps_its_pieces_and_resumes(maps_box):
    """An interrupted download is not a lost one — and a cancelled one is gone."""
    url = "https://ark.test:%d/maps/" % maps_box["port"]
    try:
        playback, browser, context = _launch(maps_box["port"], maps_box["spki"])
    except PlaywrightError as exc:
        pytest.skip("Chromium could not start: %s" % (exc,))
    try:
        page = context.new_page()
        page.on("dialog", lambda dialog: dialog.accept())

        def open_installed():
            if not page.eval_on_selector("#installed-block", "details => details.open"):
                page.click("#installed-block summary")

        def slow(route):
            # A real phone on a hotspot is not a loopback socket.  Slowing the
            # pack responses down is what makes the pause button reachable
            # before the download has already finished.
            time.sleep(0.20)
            route.continue_()

        page.route("**/maps/api/packs/**", slow)
        page.goto(url, wait_until="load")
        page.get_by_role("button", name="Download for offline use").first.wait_for(timeout=15000)
        page.get_by_role("button", name="Download for offline use").first.click()
        page.locator("#download-view").wait_for(state="visible", timeout=10000)
        page.wait_for_timeout(700)
        page.click("#pause-download")
        page.locator("#packs-view").wait_for(state="visible", timeout=15000)
        open_installed()
        assert "Download paused" in page.inner_text("#installed-list")

        page.reload(wait_until="load")
        open_installed()
        assert "Download paused" in page.inner_text("#installed-list")
        page.unroute("**/maps/api/packs/**")
        page.get_by_role("button", name="Continue download").first.click()
        page.locator("#map-view").wait_for(state="visible", timeout=60000)
        assert page.locator("#offline-badge").is_visible()

        # Remove it, start again, and cancel: a cancelled copy leaves nothing.
        page.click("#back-to-packs")
        page.locator("#packs-view").wait_for(state="visible", timeout=10000)
        open_installed()
        page.get_by_role("button", name="Remove from this device").first.click()
        page.wait_for_function(
            "() => document.querySelector('#installed-list').innerText.includes('Nothing is installed')",
            timeout=8000)
        page.route("**/maps/api/packs/**", slow)
        page.get_by_role("button", name="Download for offline use").first.click()
        page.locator("#download-view").wait_for(state="visible", timeout=10000)
        page.wait_for_timeout(500)
        page.click("#cancel-download")
        page.locator("#packs-view").wait_for(state="visible", timeout=15000)
        open_installed()
        page.wait_for_function(
            "() => document.querySelector('#installed-list').innerText.includes('Nothing is installed')",
            timeout=8000)
    finally:
        context.close()
        browser.close()
        playback.stop()
