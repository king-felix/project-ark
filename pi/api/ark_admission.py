"""Project ARK — who may receive a body, and how much of the card it may promise.

The gap this closes
-------------------
nginx and Starlette both write a request body to disk **before** a route sees it.
nginx does it when ``proxy_request_buffering`` is on; Starlette always does it,
rolling a part from memory to a real temp file after 1 MiB.  The old guard
compared a declared ``Content-Length`` with the free space and then let the body
arrive unreserved — so two uploads could each be told there was room for them,
and the check the route made afterwards came too late to matter.  A request
without a ``Content-Length`` was not checked at all.

What this module does
---------------------
* **Every** request that can carry a body is measured against one door, and the
  default door is small (64 KiB).  A new route cannot accidentally accept two
  gigabytes; a door that should take more says so here, once.
* Admission happens **before the first byte is read**: a declared body reserves
  its peak first, and a refusal is answered without the body being touched.
* A body with **no** ``Content-Length`` is admitted in bounded steps — the
  promise grows as the bytes arrive, never more than ``STEP_BYTES`` ahead — and
  is refused the moment the next step cannot be promised.
* The promise covers the **peak**, which for an upload is two copies: the
  framework's temp file and the copy the route makes from it.  That is what the
  card must hold at once, and counting only one of them is how a full card takes
  the portal down.
* The reservation is released when the response ends — **however** it ends:
  success, a refusal, an exception, or the client walking away.
* Long operations re-read the real filesystem as they go, so a download that is
  filling the card from somewhere else is noticed instead of being assumed away.

Every route that writes bytes adopts the request's promise through
``ark_space.cover()``; nothing promises the same byte twice and nothing promises
bytes it will not write.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import ark_maintenance
import ark_space

#: The most any route may receive unless it says otherwise.  A form is small; a
#: route that takes a film names its own limit below.
SMALL_BODY = 64 * 1024
#: Multipart boundaries, part headers and the odd field around the file.
FRAMING = 1024 * 1024
#: How far ahead an unknown-size body promises.  Bigger means fewer ledger writes
#: and more headroom wasted; smaller means more transactions during an upload.
STEP_BYTES = 16 * 1024 ** 2
#: The smallest read ahead worth a ledger write.
CHUNK = 1024 ** 2
#: How often the real filesystem is asked again while a body is arriving.  Small
#: enough that somebody else filling the card is noticed during a large upload,
#: large enough that a phone's upload is not one statvfs per packet.
REFRESH_BYTES = 4 * 1024 ** 2
#: How many large bodies may be in flight at once.  Each one holds a promise and
#: a temp file; letting a hundred arrive together would promise the card a
#: hundred times over before the first byte landed.
SLOTS = max(1, int(os.environ.get("ARK_BODY_SLOTS", "4") or "4"))
#: How long a large body waits for a slot before being told to come back.
SLOT_WAIT = float(os.environ.get("ARK_BODY_WAIT", "5") or "5")

class Busy(ark_space.Refused):
    """The box is already receiving as many large bodies as it will take at once.

    A refusal like any other — the caller shows a sentence and stores nothing — but
    the honest status is 503: the request is fine, the box is occupied.
    """


MEDIA_MAX = 8 * 1024 ** 3          # ark_media.MAX_MEDIA
FILE_MAX = 2 * 1024 ** 3           # ark_storage.MAX_FILE
GAME_MAX = 64 * 1024 ** 2          # ark_arcade_install.MAX_DOWNLOAD
MAP_MAX = 1024 ** 3                # ark_map_store.MAX_ARCHIVE_BYTES
SAVE_MAX = 16 * 1024 ** 2          # ark_arcade.MAX_SAVE
IMPORT_MAX = 32 * 1024 ** 2        # ark_arcade.MAX_IMPORT


@dataclass(frozen=True)
class Door:
    """One address that may receive a body, and the most it may cost.

    ``copies`` is the peak multiplier.  Two means "the framework's temp file and
    the route's own copy exist at the same time", which is every upload that goes
    through a spool.  One means the bytes are held in memory and written once.
    """

    path: str
    label: str
    limit: int
    copies: int = 2

    @property
    def admit(self) -> bool:
        """Small doors are capped but not reserved: 64 KiB cannot cross a 2 GiB reserve."""
        return self.limit > SMALL_BODY


#: The named doors.  Everything not named here gets ``DEFAULT_DOOR`` — which is
#: the point: forgetting to name a route makes it *smaller*, never unbounded.
DOORS = {
    "/files": Door("File Box", "a shared file", MEDIA_MAX + FRAMING),
    "/admin/media/upload": Door("Media", "films", MEDIA_MAX + FRAMING),
    "/admin/upload": Door("Add files", "books and games", MEDIA_MAX + FRAMING),
    "/admin/arcade/upload": Door("Games", "a game", GAME_MAX + FRAMING),
    "/admin/maps/import": Door("Maps", "a map pack", MAP_MAX + FRAMING),
    "/arcade/add": Door("Games", "a game", GAME_MAX + FRAMING),
    "/arcade/player/import.zip": Door("Games", "a save import", IMPORT_MAX + FRAMING),
}

#: Prefixes for paths that carry an identifier.
PREFIX_DOORS = (
    ("/arcade/api/saves/", Door("Games", "a save", SAVE_MAX + FRAMING, copies=1)),
)

DEFAULT_DOOR = Door("", "a form", SMALL_BODY, copies=1)


def door_for(path: str) -> Door:
    """The door for a path.  An unknown address is small, never unbounded."""
    bare = (path or "/").split("?", 1)[0]
    door = DOORS.get(bare)
    if door is not None:
        return door
    for prefix, candidate in PREFIX_DOORS:
        if bare.startswith(prefix):
            return candidate
    return DEFAULT_DOOR


def spool_dir() -> Path:
    """Where Starlette writes a body it has rolled out of memory (``TMPDIR``)."""
    import tempfile
    return Path(tempfile.gettempdir())


def _declared(scope) -> Optional[int]:
    """The declared length, or ``None`` when the body is chunked or unstated.

    The header is a fast path, never proof: the bytes that arrive are counted
    against the door and against the promise regardless of what it said.
    """
    length = None
    for key, value in scope.get("headers") or []:
        if key == b"content-length":
            try:
                length = max(0, int(value))
            except (TypeError, ValueError):
                return None
        elif key == b"transfer-encoding" and b"chunked" in value.lower():
            return None
    return length


def _header(scope, name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key == name:
            return value.decode("latin-1", "replace")
    return ""


def wants_json(scope) -> bool:
    path = str(scope.get("path") or "")
    if path.startswith("/api/"):
        return True
    accept = _header(scope, b"accept").lower()
    return "application/json" in accept and "text/html" not in accept


def status_for(exc: Exception) -> int:
    """The honest status: too big, no room, or the records cannot be read."""
    if isinstance(exc, (ark_space.LedgerUnreadable, Busy, ark_maintenance.Busy)):
        return 503
    text = str(exc).lower()
    if ("not enough room" in text or "filled up" in text
            or "nearly full" in text or "close to its reserve" in text):
        return 507
    return 413


_REFUSAL_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Nothing was stored — Project Ark</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ background:#000; color:#e8e8e8; font:17px/1.5 system-ui,-apple-system,sans-serif;
         margin:0; padding:2rem 1.25rem; }}
  main {{ max-width:34rem; margin:0 auto; }}
  h1 {{ font-size:1.35rem; line-height:1.3; margin:0 0 1rem; }}
  p {{ margin:0 0 1rem; }}
  .note {{ color:#a8a8a8; }}
  a.button {{ display:inline-block; margin-top:.5rem; padding:.85rem 1.1rem;
              min-height:44px; border-radius:.6rem; background:#1b1b1b;
              color:#fff; text-decoration:none; }}
</style>
</head>
<body><main>
<h1>{title}</h1>
<p>{message}</p>
<p class="note">{hint}</p>
<a class="button" href="/">Back to the home page</a>
</main></body></html>
"""


def refusal_page(message: str, hint: str) -> bytes:
    return _REFUSAL_PAGE.format(title="Nothing was stored", message=message,
                                hint=hint).encode("utf-8")


class Admission:
    """One request's promise, shared by the body and every copy made from it.

    The route reaches this through ``ark_space.cover()``: the same receipt grows
    for the archive a map import extracts, or the book a batch writes into the
    importer's folder, and none of them promise the body's bytes a second time.
    """

    def __init__(self, door: Door, *, owner: str = "") -> None:
        self.door = door
        self.owner = owner or "request"
        self.receipts: dict = {}          # device -> Receipt
        self.body_seen = 0
        self.body_key = "body"
        self.refusal: Optional[Exception] = None
        self._begun = False

    # -- the promise -------------------------------------------------------
    def _receipt_for(self, path):
        device = ark_space.device_key(path)
        receipt = self.receipts.get(device)
        if receipt is None:
            receipt = ark_space.open_receipt(
                path, owner=self.owner, label=self.door.label, kind="body")
            self.receipts[device] = receipt
        return receipt

    def begin(self, declared: Optional[int]) -> None:
        """Promise the peak before the first byte is handed to the framework."""
        if self._begun or not self.door.admit:
            return
        self._begun = True
        receipt = self._receipt_for(spool_dir())
        if declared is not None:
            receipt.need(declared * self.door.copies + FRAMING)
        else:
            # An unknown-size body starts with what the card can actually spare,
            # not with a fixed step that may be larger than the room left — and
            # the framing has to fit inside that room too.
            want = min(STEP_BYTES, self.door.limit)
            room = ark_space.slack(spool_dir())
            if room is None:
                raise ark_space.LedgerUnreadable(
                    "The box could not read its own record of space promised to work "
                    "already running, so it will not receive another large file. "
                    "Nothing was stored.")
            if want + FRAMING > room:
                want = max(0, int(room) - FRAMING)
            receipt.need(want + FRAMING)

    def claim(self, path, need: int, *, owner: str = "", label: str = "",
              key: str = "file"):
        """Adopt this request's promise for a stage that will write ``need`` bytes.

        ``need`` is the stage's **total**, not an increment; repeated calls with
        the same number cost nothing.  A stage on a different filesystem gets its
        own promise, because a card and a separate disk are not one budget.
        """
        receipt = self._receipt_for(path)
        receipt.peak(key, need)
        return receipt

    # -- the body ----------------------------------------------------------
    def saw(self, count: int, declared: Optional[int]) -> None:
        """Account for body bytes the framework has just been handed."""
        if not self._begun:
            self.begin(declared)
        self.body_seen += int(count)
        if not self.door.admit:
            return
        receipt = self._receipt_for(spool_dir())
        # Grow before writing past the promise, so a body that keeps arriving is
        # never unbounded.  A body that *declared* its length already has the whole
        # peak promised; growing it again would ask for room it does not need.
        if declared is None:
            if self.body_seen + CHUNK > receipt.reserved:
                receipt.need(ark_space.growth_step(spool_dir(), self.body_seen,
                                                   self.door.limit) + FRAMING)
        elif self.body_seen > declared + CHUNK:
            # The header is not proof of size.  A client that sends more than it
            # declared is stopped rather than allowed to spend room nobody
            # promised it — the door's cap is the outer bound, this is the inner.
            raise ark_space.Refused(
                "That upload sent more than it said it would. Nothing was stored.")
        receipt.spent(self.body_key, self.body_seen)

    def refresh(self) -> None:
        """Ask the real filesystem again, during a long receipt.

        ``statvfs`` is the only thing that can see somebody else's write.  The
        ledger says what jobs promised; this says what the card actually holds
        now, and it is asked on every step rather than trusted from the start of
        the upload.  The test is the admission inequality itself: the room left
        after every *other* job's promise must still cover what this one has not
        written yet.
        """
        for receipt in list(self.receipts.values()):
            base = ark_space.facts(receipt.path)
            if base["free_bytes"] is None:
                continue                      # cannot measure: nothing to conclude
            promised = ark_space.committed_for(receipt.path)
            if promised is None:
                raise ark_space.LedgerUnreadable(
                    "The box could not read its own record of space promised to work "
                    "already running, so it stopped instead of taking a chance. "
                    "Nothing more was stored.")
            others = max(0, int(promised) - max(0, receipt.reserved - receipt.written))
            left = (int(base["free_bytes"]) - int(base["reserve_bytes"] or 0) - others)
            if left < max(0, receipt.reserved - receipt.written):
                raise ark_space.Refused(
                    "The card filled up while that file was arriving — something else "
                    "on the box is using the space. Nothing was kept. Free some room, "
                    "then try again.")

    def release(self) -> None:
        for receipt in list(self.receipts.values()):
            receipt.release()
        self.receipts.clear()


def _slots() -> asyncio.Semaphore:
    """One semaphore per event loop (the portal runs one process, one loop)."""
    loop = asyncio.get_event_loop()
    held = getattr(loop, "_ark_body_slots", None)
    if held is None:
        held = asyncio.Semaphore(SLOTS)
        try:
            loop._ark_body_slots = held
        except AttributeError:                # pragma: no cover - exotic loop
            pass
    return held


class _Guard:
    """The wrapped stream, the wrapped reply, and the one refusal that wins."""

    def __init__(self, app, scope, receive, send, admission: Admission,
                 declared: Optional[int]) -> None:
        self.app = app
        self.scope = scope
        self.receive = receive
        self.send = send
        self.admission = admission
        self.declared = declared
        self.started = False
        self.refused = False
        self.sent_refusal = False
        self.steps = 0

    # -- reading -----------------------------------------------------------
    async def read(self):
        message = await self.receive()
        if message.get("type") != "http.request":
            return message
        if self.refused:
            return {"type": "http.disconnect"}
        body = message.get("body", b"") or b""
        if self.admission.body_seen + len(body) > self.admission.door.limit:
            await self.stop(ark_space.Refused(
                "That upload is bigger than this box accepts. Nothing was stored."))
            return {"type": "http.disconnect"}
        try:
            self.admission.saw(len(body), self.declared)
            step = self.admission.body_seen // REFRESH_BYTES
            if step > self.steps:
                self.steps = step
                self.admission.refresh()
        except (ark_space.Refused, ark_maintenance.Busy) as exc:
            # Stop the parser now: the remaining bytes never reach the framework's
            # temp file, so a full card is not filled further.
            await self.stop(exc)
            return {"type": "http.disconnect"}
        return message

    # -- writing -----------------------------------------------------------
    async def reply(self, message):
        if self.refused:
            if message["type"] == "http.response.start" and not self.sent_refusal:
                await self.refuse(self.admission.refusal)
            return None                       # the app's own error is superseded
        if message["type"] == "http.response.start":
            self.started = True
        return await self.send(message)

    # -- the one refusal ---------------------------------------------------
    async def stop(self, exc: Exception) -> None:
        if self.refused:
            return
        self.refused = True
        self.admission.refusal = exc

    async def refuse(self, exc: Optional[Exception]) -> None:
        if self.sent_refusal:
            return
        self.sent_refusal = True
        self.started = True
        exc = exc or ark_space.Refused("That upload could not be accepted.")
        status = status_for(exc)
        text = str(exc)
        if isinstance(exc, ark_maintenance.Busy):
            text = ("The box is putting its records back just now. Nothing was "
                    "stored — try again in a moment.")
        if status == 503:
            hint = "Nothing was stored. Try again in a moment."
        elif status == 507:
            hint = "Nothing was stored. Free some room, then try again."
        else:
            hint = ("Nothing was stored. The most one file may be is "
                    + ark_space._size_words(self.admission.door.limit) + ".")
        if wants_json(self.scope):
            payload = json.dumps({"ok": False, "reason": text, "detail": hint}).encode()
            await self.send({"type": "http.response.start", "status": status,
                             "headers": [(b"content-type",
                                          b"application/json; charset=utf-8"),
                                         (b"content-length", str(len(payload)).encode()),
                                         (b"cache-control", b"no-store")]})
            await self.send({"type": "http.response.body", "body": payload})
            return
        page = refusal_page(text, hint)
        await self.send({"type": "http.response.start", "status": status,
                         "headers": [(b"content-type", b"text/html; charset=utf-8"),
                                     (b"content-length", str(len(page)).encode()),
                                     (b"cache-control", b"no-store")]})
        await self.send({"type": "http.response.body", "body": page})


class BodyAdmissionMiddleware:
    """A raw ASGI middleware so it sees the body before any parser does.

    ``BaseHTTPMiddleware`` would work too, but this is the narrowest place to
    count bytes: the framework's own ``receive`` is wrapped, so a multipart
    parser, a plain ``request.body()`` and a streaming reader are all covered by
    the same arithmetic.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope.get("type") != "http"
                or scope.get("method") not in ("POST", "PUT", "PATCH")):
            return await self.app(scope, receive, send)
        door = door_for(str(scope.get("path") or "/"))
        declared = _declared(scope)
        guard = _Guard(self.app, scope, receive, send,
                       Admission(door, owner=f"body:{door.path or scope.get('path')}"),
                       declared)
        if declared is not None and declared > door.limit:
            return await guard.refuse(ark_space.Refused(
                "That upload is bigger than this box accepts."))
        if declared == 0:
            return await self.app(scope, receive, send)

        token = ark_space.bind(guard.admission)
        slot = False
        try:
            if door.admit:
                try:
                    await asyncio.wait_for(_slots().acquire(), timeout=SLOT_WAIT)
                    slot = True
                except asyncio.TimeoutError:
                    return await guard.refuse(Busy(
                        "The box is already receiving other files. Nothing was stored. "
                        "Wait for that one to finish, then try again."))
            if declared is not None and door.admit:
                # Reserve before the first byte is read.  A refusal here never
                # touches the body at all.
                try:
                    guard.admission.begin(declared)
                except (ark_space.Refused, ark_maintenance.Busy) as exc:
                    return await guard.refuse(exc)
            try:
                await self.app(scope, guard.read, guard.reply)
            except (ark_space.Refused, ark_maintenance.Busy) as exc:
                await guard.stop(exc)
            if guard.refused and not guard.sent_refusal:
                await guard.refuse(guard.admission.refusal)
        finally:
            if slot:
                _slots().release()
            ark_space.unbind(token)
            guard.admission.release()
