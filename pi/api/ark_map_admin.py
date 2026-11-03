"""PROJECT ARK — the owner's view of the map shelf.

The page is deliberately a list of facts and a small number of forms.  It does
not build packs (that happens on a workstation, where the parsing belongs) and it
does not pretend a deletion on the Ark reaches a phone.  What it does is make the
two states that matter unmistakable:

* **Published and checked** — a reader can download this version now;
* **Anything else** — with the sentence that says why, and the one action that
  moves it forward (check, publish, replace, remove).

The markup follows the rest of the owner's pages: no JavaScript, real forms, the
CSRF token in every one that changes something, and a password on the delete that
cannot be undone.
"""

from __future__ import annotations

import html
from typing import Dict, Iterable, List


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def human_size(n) -> str:
    value = float(max(0, int(n or 0)))
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("kB", 1024)):
        if value >= size:
            return "%.1f %s" % (value / size, unit)
    return "%d B" % int(value)


def _csrf(ctx, token: str) -> str:
    # The token is the same hidden field every owner form carries; the route
    # checks it server-side, so a page that forgot it cannot change anything.
    return '<input type="hidden" name="csrf" value="%s">' % esc(token)


def _status(row: Dict[str, object]) -> str:
    if row.get("problems"):
        return "Needs attention"
    if not row.get("verified"):
        return "Not checked yet"
    if row.get("published") and row.get("ready"):
        return "Offered to readers"
    if row.get("published"):
        return "Published, but its files are not all present"
    return "Checked, not offered"


def _facts(row: Dict[str, object]) -> str:
    bounds = row.get("bounds") or []
    coverage = ""
    if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
        coverage = "%.2f…%.2f N, %.2f…%.2f E" % (
            float(bounds[0]), float(bounds[2]), float(bounds[1]), float(bounds[3]))
    modes = ", ".join((row.get("capabilities") or {}).get("routing") or []) or "none"
    search = (row.get("search") or {}).get("count")
    return (
        '<div class="hint">'
        + " · ".join(filter(None, [
            "Data %s" % esc(row.get("data_date") or "unknown"),
            "Built %s" % esc(row.get("built") or "unknown"),
            "%s" % human_size(row.get("bytes")),
            "Travel: %s" % esc(modes),
            ("%s searchable places" % search) if search is not None else "",
            ("Coverage " + coverage) if coverage else "",
            "Path %s" % esc(row.get("folder") or ""),
        ]))
        + "</div>"
    )


def _problems(row: Dict[str, object]) -> str:
    problems = row.get("problems") or []
    if not problems:
        return ""
    return ('<div class="hint" style="color:#ffb3b5">'
            + "<br>".join("• " + esc(item) for item in problems[:8])
            + "</div>")


def _actions(row: Dict[str, object], ctx, csrf: str) -> str:
    ident = esc(row.get("id"))
    version = esc(row.get("version"))
    parts = []
    parts.append(
        '<form method="post" action="/admin/maps/check" style="display:inline">'
        + _csrf(ctx, csrf)
        + '<input type="hidden" name="id" value="%s">' % ident
        + '<input type="hidden" name="version" value="%s">' % version
        + '<button class="go-btn small" type="submit">Check every byte</button></form>')
    if row.get("published"):
        parts.append(
            '<form method="post" action="/admin/maps/publish" style="display:inline">'
            + _csrf(ctx, csrf)
            + '<input type="hidden" name="id" value="%s">' % ident
            + '<input type="hidden" name="version" value="%s">' % version
            + '<input type="hidden" name="on" value="0">'
            + '<button class="go-btn small" type="submit">Stop offering it</button></form>')
    else:
        parts.append(
            '<form method="post" action="/admin/maps/publish" style="display:inline">'
            + _csrf(ctx, csrf)
            + '<input type="hidden" name="id" value="%s">' % ident
            + '<input type="hidden" name="version" value="%s">' % version
            + '<input type="hidden" name="on" value="1">'
            + '<button class="go-btn small" type="submit">Offer it to readers</button></form>')
    parts.append(
        '<details style="margin-top:8px"><summary>Delete this version for ever</summary>'
        '<p class="hint">This removes the Ark’s copy. Phones that already downloaded it '
        'keep theirs; deleting here cannot reach them.</p>'
        '<form method="post" action="/admin/maps/delete">'
        + _csrf(ctx, csrf)
        + '<input type="hidden" name="id" value="%s">' % ident
        + '<input type="hidden" name="version" value="%s">' % version
        + '<label class="hint"><input type="checkbox" name="confirm" value="yes" required> '
        'I understand this cannot be undone</label>'
        '<label class="hint">Your password <input type="password" name="current_password" '
        'autocomplete="current-password" required></label>'
        '<button class="go-btn small" type="submit">Delete this version</button>'
        '</form></details>')
    return '<div class="nav">' + "".join(parts) + "</div>"


def pack_card(row: Dict[str, object], ctx, csrf: str) -> str:
    title = esc(row.get("name") or row.get("id"))
    version = esc(row.get("version"))
    state = _status(row)
    published = "yes" if row.get("published") and row.get("ready") else "no"
    return (
        '<div class="card" style="margin:12px 0">'
        '<h3 style="margin-top:0">%s <span class="hint">version %s</span></h3>'
        '<p class="hint"><b>%s</b> · offered: %s</p>'
        "%s%s%s"
        "</div>"
    ) % (title, version, esc(state), published, _facts(row), _problems(row),
         _actions(row, ctx, csrf))


def import_form(ctx, csrf: str) -> str:
    return (
        '<div class="card" style="margin:12px 0">'
        "<h3 style=\"margin-top:0\">Add a pack</h3>"
        '<p class="hint">Build the region on a computer with '
        '<b>scripts/ark-map-pack</b>, then bring the <b>.zip</b> it made here. '
        'The pack is checked byte-for-byte before it is added, and it is not offered '
        'to readers until you say so.</p>'
        '<form method="post" action="/admin/maps/import" enctype="multipart/form-data">'
        + _csrf(ctx, csrf)
        + '<label class="hint">Pack archive (.zip, up to 1 GiB) '
        '<input type="file" name="file" accept=".zip,application/zip" required></label>'
        '<button class="go-btn" type="submit">Check and add the pack</button>'
        "</form></div>"
    )


def incoming_block(items: Iterable[Dict[str, object]], ctx, csrf: str) -> str:
    items = list(items)
    if not items:
        return ""
    rows = []
    for item in items:
        rows.append(
            '<form method="post" action="/admin/maps/import-incoming" '
            'style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:6px 0">'
            + _csrf(ctx, csrf)
            + '<input type="hidden" name="name" value="%s">' % esc(item.get("name"))
            + '<span>%s <span class="hint">(%s)</span></span>'
            % (esc(item.get("name")), human_size(item.get("bytes")))
            + '<button class="go-btn small" type="submit">Check and add</button></form>')
    return (
        '<div class="card" style="margin:12px 0">'
        "<h3 style=\"margin-top:0\">Already waiting in the incoming folder</h3>"
        '<p class="hint">A pack copied to the Ark by SSH or a stick can be checked and '
        "added from here. Nothing is added until you press the button.</p>"
        + "".join(rows) + "</div>"
    )


def page_body(rows: List[Dict[str, object]], storage: Dict[str, object],
              incoming: List[Dict[str, object]], ctx, csrf: str,
              flash: str = "", error: str = "") -> str:
    notes = ""
    if flash:
        notes += '<p class="status">%s</p>' % esc(flash)
    if error:
        notes += '<p class="status" style="color:#ffb3b5">%s</p>' % esc(error)
    room = storage.get("account") or {}
    storage_line = []
    if room.get("free_bytes") is not None:
        storage_line.append("%s free" % human_size(room.get("free_bytes")))
        storage_line.append("%s kept back" % human_size(room.get("reserve_bytes")))
    storage_line.append("%s across %d pack%s" % (
        human_size(storage.get("pack_bytes")), storage.get("packs") or 0,
        "" if (storage.get("packs") or 0) == 1 else "s"))
    largest = storage.get("largest") or []
    largest_text = ""
    if largest:
        largest_text = "Largest: " + ", ".join(
            "%s %s (%s)" % (esc(row.get("name") or row.get("id")),
                            esc(row.get("version")), human_size(row.get("bytes")))
            for row in largest[:5])
    body = notes
    body += (
        "<p>Maps lets a reader carry one region on a phone and use it with no Ark and "
        "no internet. The map, the search index and the routing graph are built on a "
        "workstation; this page receives the finished pack, proves it, and decides "
        "whether readers may download it.</p>"
        '<p class="hint">%s</p>' % esc(" · ".join(storage_line))
    )
    if largest_text:
        body += '<p class="hint">%s</p>' % largest_text
    body += import_form(ctx, csrf)
    body += incoming_block(incoming, ctx, csrf)
    if not rows:
        body += ('<div class="card"><p>No map packs are on the Ark yet. A reader who '
                 'opens Maps will be told that no region is ready, which is the truth; '
                 'nothing else on the box is affected.</p></div>')
    else:
        body += "<h2>Packs on this card</h2>"
        for row in rows:
            body += pack_card(row, ctx, csrf)
    body += (
        '<div class="card"><h3 style="margin-top:0">Choosing a region</h3>'
        "<p>A region is a place you might be when the network is gone — the area "
        "around home, a route you travel, or somewhere you would go for help. Build "
        "the smallest region that covers it: a pack that is too large is slower to "
        "download and harder to keep on a phone.</p>"
        '<p class="hint">The real geographic choice is the owner’s. The demonstration '
        "pack used to prove this pipeline is Liechtenstein, a small non-personal "
        "region; no home location is inferred from anything.</p></div>"
    )
    return body
