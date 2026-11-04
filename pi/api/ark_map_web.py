"""PROJECT ARK — the public door to the maps application and its packs.

This module serves four different things and keeps them apart on purpose:

* the **application**: `index.html`, its modules, styles, icons, help page and
  service worker, all from `pi/maps/web` (installed at `/srv/ark/maps/web`);
* the **catalogue**: only packs the owner published *and* whose files are still
  present at the sizes their manifest states;
* the **pack bytes**: one immutable version at a time, with real range support
  so a phone can resume, and a fingerprint as the ETag;
* the **certificate**: the public half of the Ark's own certificate, so a device
  can be taught to trust the secure address once.

It never serves `/admin`, never reads a request for a pack outside the published
tree, and never caches pack bytes in the service worker (the worker skips
`/maps/api/` entirely).  A pack file is answered only when its name is in that
pack's own manifest — an arbitrary path under the folder is a 404.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

import ark_map_store
import ark_maps

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".geojson": "application/geo+json; charset=utf-8",
    ".webmanifest": "application/manifest+json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".txt": "text/plain; charset=utf-8",
    ".bin": "application/octet-stream",
}

APP_ASSETS = (
    "index.html", "help.html", "app.css", "app.js", "sha256.js", "store.js",
    "search.js", "router.js", "router-worker.js", "map.js", "sw.js",
    "app.webmanifest", "icon.svg", "icon-180.png", "icon-512.png",
)

#: A page that can keep itself offline must not be embeddable, must not load a
#: script from anywhere else, and must not be able to send its reader anywhere.
#: `connect-src 'self'` is what confines the catalogue and the pack bytes to the
#: Ark; there is no analytics, no font CDN and no third-party tile server.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; "
       "img-src 'self' data: blob:; connect-src 'self'; worker-src 'self'; "
       "manifest-src 'self'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'self'")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(self), camera=(), microphone=()",
}

CHUNK = 256 * 1024
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def certificate_path() -> Path:
    """The public certificate, from a place the API's user may actually read.

    `/etc/ssl/ark` is 0700 root:root because the private key lives there.  The
    API runs as `pi`, so it cannot traverse it; phase 66 publishes a 0644 copy
    of the *public* half beside the map shelf, and `ark-tls-cert` refreshes that
    copy whenever the certificate is renewed.
    """
    override = os.environ.get("ARK_CERT_PATH")
    if override:
        return Path(override)
    public = Path(os.environ.get("ARK_ROOT", "/srv/ark")) / "maps" / "ark-certificate.crt"
    if public.is_file():
        return public
    return Path("/etc/ssl/ark/ark-admin.crt")


def certificate_response() -> Response:
    """The public certificate, as a file a person can install.  Never the key."""
    path = certificate_path()
    if not path.is_file():
        return JSONResponse(
            {"error": "This box has not made its certificate yet.",
             "detail": "The owner can create it from the owner pages or with ark-tls-cert."},
            status_code=404)
    data = path.read_bytes()
    return Response(data, media_type="application/x-x509-ca-cert", headers={
        **SECURITY_HEADERS,
        "Content-Disposition": 'attachment; filename="ark-certificate.crt"',
        "Cache-Control": "no-cache",
    })


def _content_type(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(),
                             mimetypes.guess_type(path.name)[0] or "application/octet-stream")


def _asset_headers(path: Path) -> Dict[str, str]:
    headers = dict(SECURITY_HEADERS)
    headers["Content-Security-Policy"] = CSP
    headers["Cache-Control"] = "no-cache"
    if path.name == "sw.js":
        headers["Service-Worker-Allowed"] = "/maps/"
    return headers


#: Where the service worker's cache name is written.  ``sw.js`` is served with
#: this replaced by the release fingerprint, so **any** change to any application
#: file produces a byte-different worker script — which is the only signal a
#: browser uses to install one.  A hand-edited version constant was the old
#: arrangement and it drifted: an already-installed phone kept the old page for
#: ever, because nothing in it had changed as far as the browser could see.
RELEASE_TOKEN = "__ARK_RELEASE__"

_RELEASE_CACHE: Dict[str, object] = {"key": "", "value": ""}


def _asset_files() -> list:
    folder = ark_map_store.web_root()
    found = []
    for name in sorted(APP_ASSETS):
        path = folder / name
        if path.is_file():
            found.append((name, path))
    return found


def app_release() -> str:
    """A short fingerprint of the whole application, from its real bytes.

    Cheap enough to ask on every page load (the files are small and the answer is
    reused until one of them changes), and impossible to forget to bump: it *is*
    the files.
    """
    files = _asset_files()
    key = "|".join("%s:%d:%d" % (name, path.stat().st_size, int(path.stat().st_mtime))
                   for name, path in files)
    if _RELEASE_CACHE.get("key") == key and _RELEASE_CACHE.get("value"):
        return str(_RELEASE_CACHE["value"])
    digest = hashlib.sha256()
    for name, path in files:
        digest.update(name.encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
    value = digest.hexdigest()[:12]
    _RELEASE_CACHE["key"] = key
    _RELEASE_CACHE["value"] = value
    return value


def app_release_facts() -> dict:
    """What a page (and the owner's Health page) may say about this release."""
    files = _asset_files()
    return {
        "release": app_release(),
        "app_version": ark_maps.APP_VERSION,
        "files": len(files),
        "missing": [name for name in APP_ASSETS
                    if not (ark_map_store.web_root() / name).is_file()],
    }


def _read_asset(name: str) -> Optional[Tuple[Path, bytes]]:
    folder = ark_map_store.web_root()
    if name not in APP_ASSETS:
        return None
    path = folder / name
    if not path.is_file():
        return None
    data = path.read_bytes()
    if name == "sw.js":
        # The same substitution makes the worker's *own* bytes change whenever the
        # application does, so the browser has a reason to update it.
        data = data.replace(RELEASE_TOKEN.encode("utf-8"),
                            app_release().encode("utf-8"))
    return path, data


def _parse_range(header: str, size: int) -> Optional[Tuple[int, int]]:
    """One range, or `None` when the header is absent/ignorable.

    Raises `ValueError` for a range that cannot be satisfied, which the caller
    turns into a 416 with `Content-Range: bytes */size`.
    """
    if not header:
        return None
    match = _RANGE.match(header.strip())
    if not match:
        raise ValueError("only a single `bytes=` range is understood")
    first, last = match.groups()
    if size <= 0:
        raise ValueError("an empty file has no ranges")
    if first == "":
        if last == "":
            raise ValueError("a range needs a start or an end")
        length = int(last)
        if length <= 0:
            raise ValueError("a suffix range needs a positive length")
        return max(0, size - length), size - 1
    start = int(first)
    if start >= size:
        raise ValueError("the range starts past the end of the file")
    end = int(last) if last else size - 1
    if end < start:
        raise ValueError("the range ends before it starts")
    return start, min(end, size - 1)


def _iter_file(path: Path, start: int, length: int) -> Iterator[bytes]:
    remaining = length
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            block = handle.read(min(CHUNK, remaining))
            if not block:
                break
            remaining -= len(block)
            yield block


def build_router() -> APIRouter:
    router = APIRouter(include_in_schema=False)

    # -- the catalogue ------------------------------------------------------

    @router.get("/maps/api/catalog")
    def catalog():
        store = ark_map_store.Store()
        try:
            rows = store.published_rows()
        except Exception as exc:          # noqa: BLE001 — a broken card, not a broken app
            return JSONResponse({"app_version": ark_maps.APP_VERSION, "packs": [],
                                 "error": "The pack shelf could not be read: %s" % (exc,)},
                                status_code=200, headers={"Cache-Control": "no-store"})
        return JSONResponse({
            "app_version": ark_maps.APP_VERSION,
            "pack_schema": ark_maps.PACK_SCHEMA,
            "packs": rows,
        }, headers={"Cache-Control": "no-store", **SECURITY_HEADERS})

    # -- which build of this page is loaded --------------------------------

    @router.get("/maps/api/app")
    def app_facts():
        """The release the page should be showing, and the version packs require.

        ``app_version`` is the *format* compatibility number packs name in their
        manifest (``app.min``..``app.max``); ``release`` is which build of the
        page this is.  Two different questions, and confusing them is how a page
        tells a phone its pack is incompatible when the only change was a colour.
        """
        return JSONResponse(app_release_facts(),
                            headers={"Cache-Control": "no-store", **SECURITY_HEADERS})

    # -- one pack's description ---------------------------------------------

    @router.api_route("/maps/api/packs/{ident}/{version}/manifest.json",
                      methods=["GET", "HEAD"])
    def pack_manifest(ident: str, version: str, request: Request):
        row = _published_row(ident, version)
        if not row:
            return _not_found()
        folder = Path(row["folder"])
        path = folder / "manifest.json"
        if not path.is_file():
            return _not_found()
        data = path.read_bytes()
        headers = {**SECURITY_HEADERS, "Cache-Control": "no-cache",
                   "Content-Type": "application/json; charset=utf-8",
                   "Content-Length": str(len(data))}
        etag = '"%s"' % hashlib.sha256(data).hexdigest()
        headers["ETag"] = etag
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
        if request.method == "HEAD":
            return Response(status_code=200, headers=headers)
        return Response(data, headers=headers)

    # -- one pack's bytes, with ranges --------------------------------------

    @router.api_route("/maps/api/packs/{ident}/{version}/{name}",
                      methods=["GET", "HEAD"])
    def pack_file(ident: str, version: str, name: str, request: Request):
        row = _published_row(ident, version)
        if not row:
            return _not_found()
        folder = Path(row["folder"])
        manifest = row.get("_manifest")
        if not isinstance(manifest, dict):
            try:
                manifest = ark_maps.read_manifest(str(folder))
            except (OSError, ValueError):
                return _not_found()
        allowed = {}
        for entry in manifest.get("files") or []:
            if isinstance(entry, (list, tuple)) and len(entry) == 3:
                allowed[str(entry[0])] = (int(entry[1]), str(entry[2]))
        if name not in allowed or os.path.basename(name) != name:
            return _not_found()
        path = folder / name
        if not path.is_file():
            return _not_found()
        size = int(allowed[name][0])
        actual = path.stat().st_size
        if size and actual != size:
            return JSONResponse(
                {"error": "%s is not the size its manifest states" % name},
                status_code=503, headers={"Cache-Control": "no-store"})
        etag = '"%s"' % allowed[name][1]
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers={"ETag": etag,
                                                      "Cache-Control": "public, max-age=31536000, immutable"})
        start, end, status = 0, size - 1, 200
        header = request.headers.get("range") or ""
        if header and request.headers.get("if-range") in (None, "", etag):
            try:
                parsed = _parse_range(header, size)
            except ValueError:
                return Response(status_code=416, headers={
                    "Content-Range": "bytes */%d" % size,
                    "Accept-Ranges": "bytes",
                })
            if parsed:
                start, end = parsed
                status = 206
        length = max(0, end - start + 1)
        headers = {
            "Content-Type": _content_type(path),
            "Content-Length": str(length),
            "Accept-Ranges": "bytes",
            "ETag": etag,
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        }
        if status == 206:
            headers["Content-Range"] = "bytes %d-%d/%d" % (start, end, size)
        if request.method == "HEAD":
            return Response(status_code=status, headers=headers)
        return StreamingResponse(_iter_file(path, start, length), status_code=status,
                                 headers=headers)

    # -- the certificate ----------------------------------------------------

    @router.get("/maps/certificate.crt")
    def certificate():
        return certificate_response()

    # -- the application ----------------------------------------------------

    @router.api_route("/maps/", methods=["GET", "HEAD"])
    @router.api_route("/maps/index.html", methods=["GET", "HEAD"])
    def index(request: Request):
        return _serve_asset("index.html", request)

    @router.api_route("/maps/help", methods=["GET", "HEAD"])
    @router.api_route("/maps/help.html", methods=["GET", "HEAD"])
    def help_page(request: Request):
        return _serve_asset("help.html", request)

    @router.api_route("/maps/{name}", methods=["GET", "HEAD"])
    def app_asset(name: str, request: Request):
        return _serve_asset(name, request)

    return router


def _serve_asset(name: str, request: Request) -> Response:
    found = _read_asset(name)
    if not found:
        return Response("Not found", status_code=404,
                        media_type="text/plain; charset=utf-8",
                        headers={"Cache-Control": "no-store"})
    path, data = found
    headers = _asset_headers(path)
    headers["Content-Type"] = CONTENT_TYPES.get(path.suffix.lower(),
                                                mimetypes.guess_type(path.name)[0]
                                                or "application/octet-stream")
    headers["Content-Length"] = str(len(data))
    # A validator matters more than it looks.  Without one the browser cannot
    # prove the service worker script is unchanged, so every visit installs a
    # "new" worker, which waits, and the page offers an update that is not one.
    etag = '"%s"' % hashlib.sha256(data).hexdigest()
    headers["ETag"] = etag
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag,
                                                  "Cache-Control": "no-cache"})
    if request.method == "HEAD":
        return Response(status_code=200, headers=headers)
    return Response(data, headers=headers)


def _published_row(ident: str, version: str) -> Optional[Dict[str, object]]:
    if not _SAFE_ID.match(ident or "") or not _SAFE_VERSION.match(version or ""):
        return None
    store = ark_map_store.Store()
    for row in store.rows():
        if row.get("id") != ident or row.get("version") != version:
            continue
        if not row.get("published") or not row.get("ready"):
            return None
        try:
            row["_manifest"] = ark_maps.read_manifest(str(row["folder"]))
        except (OSError, ValueError):
            return None
        return row
    return None


def _not_found() -> Response:
    return Response("Not found", status_code=404, media_type="text/plain; charset=utf-8",
                    headers={"Cache-Control": "no-store"})
