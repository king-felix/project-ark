"""Shared files: names, caps, atomicity — and the overwrite that used to lose data.

The release this branch started from named an upload after its *display* name and
its minute of arrival, so a third file with the same name inside one minute
replaced the second, and the File Box listed a row for a file that no longer
existed.  These tests pin the replacement: every upload keeps its own place on
disk, the name a person sees is only a label, and a refusal never leaves a
half-written file behind.
"""

from __future__ import annotations

import io
import os
import urllib.parse
from pathlib import Path


def _stored(ark) -> list[str]:
    return sorted(p.name for p in (ark.root / "drop" / "files").iterdir() if p.is_file())


def _temp_leftovers(ark) -> list[str]:
    return sorted(p.name for p in (ark.root / "drop" / "tmp").iterdir() if p.is_file())


def _rows(ark) -> list:
    with ark.db.read() as conn:
        return conn.execute(
            "SELECT id, name, stored, display, size, note, sha256 FROM files ORDER BY id"
        ).fetchall()


# ---------------------------------------------------------------------------
#  the defect itself
# ---------------------------------------------------------------------------
def test_three_same_name_uploads_all_survive(public):
    ark = public.ark
    bodies = [b"first copy of the gasket photo", b"second, clearer photo", b"third one"]
    for body in bodies:
        answer = public.client.post(
            "/files",
            files={"file": ("gasket.jpg", body, "image/jpeg")},
            data={"note": "spare gasket"},
            follow_redirects=False,
        )
        assert answer.status_code == 303, answer.text
        assert "ok=1" in answer.headers["location"]

    rows = _rows(ark)
    assert len(rows) == 3
    assert len({r["stored"] for r in rows}) == 3, "every upload needs its own place on disk"
    assert {r["display"] for r in rows} == {"gasket.jpg"}
    assert sorted(r["size"] for r in rows) == sorted(len(b) for b in bodies)

    on_disk = _stored(ark)
    assert len(on_disk) == 3
    assert {p.read_bytes() for p in (ark.root / "drop" / "files").iterdir()} == set(bodies)


def test_three_same_name_uploads_are_all_listed_and_linked(public):
    ark = public.ark
    for n in range(3):
        public.client.post("/files", files={"file": ("note.txt", f"body {n}".encode(), "text/plain")},
                           follow_redirects=False)
    page = public.client.get("/files")
    assert page.status_code == 200
    assert page.text.count("/drop/") == 3
    for row in _rows(ark):
        assert (ark.root / "drop" / "files" / row["stored"]).is_file()
        # the link a reader is given must actually resolve to that file
        assert urllib.parse.quote(row["stored"]) in page.text


def test_identical_bytes_get_two_places(public):
    """Even byte-for-byte identical uploads are separate things people shared."""
    ark = public.ark
    for _ in range(2):
        public.client.post("/files", files={"file": ("same.txt", b"hello", "text/plain")},
                           follow_redirects=False)
    rows = _rows(ark)
    assert len(rows) == 2
    assert rows[0]["stored"] != rows[1]["stored"]
    assert rows[0]["sha256"] == rows[1]["sha256"], "the hash names the content, not the file"


# ---------------------------------------------------------------------------
#  refusals are polite and leave nothing behind
# ---------------------------------------------------------------------------
def test_oversized_file_is_refused_and_leaves_no_residue(public, monkeypatch):
    import ark_storage
    monkeypatch.setattr(ark_storage, "MAX_FILE", 8)
    answer = public.client.post(
        "/files",
        files={"file": ("big.bin", b"x" * 64, "application/octet-stream")},
        follow_redirects=False,
    )
    assert answer.status_code == 303
    message = urllib.parse.unquote(answer.headers["location"])
    assert "too big" in message.lower()
    assert _stored(public.ark) == []
    assert _temp_leftovers(public.ark) == []
    assert _rows(public.ark) == []


def test_empty_file_is_refused(public):
    answer = public.client.post("/files", files={"file": ("empty.txt", b"", "text/plain")},
                                follow_redirects=False)
    assert answer.status_code == 303
    assert "empty" in urllib.parse.unquote(answer.headers["location"]).lower()
    assert _stored(public.ark) == []


def test_a_full_box_refuses_before_writing(public, monkeypatch):
    import ark_storage
    monkeypatch.setattr(ark_storage, "free_bytes", lambda path=None: 1024)
    answer = public.client.post("/files", files={"file": ("x.txt", b"data", "text/plain")},
                                follow_redirects=False)
    assert answer.status_code == 303
    assert "full" in urllib.parse.unquote(answer.headers["location"]).lower()
    assert _stored(public.ark) == []
    assert _temp_leftovers(public.ark) == []


# ---------------------------------------------------------------------------
#  the storage rules themselves
# ---------------------------------------------------------------------------
def test_display_name_is_a_label_not_a_path(ark):
    import ark_storage
    assert ark_storage.display_name("../../etc/passwd") == "passwd"
    assert ark_storage.display_name("C:\\Users\\sam\\report.pdf") == "report.pdf"
    assert ark_storage.display_name("bad\x00name\x1f.txt") == "badname.txt"
    assert ark_storage.display_name("  holiday photo.JPG  ") == "holiday photo.JPG"
    assert ark_storage.display_name("") == "file"
    assert ark_storage.display_name("...") == "file"
    long = ark_storage.display_name("x" * 400 + ".pdf")
    assert long.endswith(".pdf") and len(long) <= 120


def test_spool_never_overwrites_even_with_a_repeated_token(ark, monkeypatch):
    """The final name is picked so that a collision is impossible, not unlikely."""
    import ark_storage
    monkeypatch.setattr(ark_storage.secrets, "token_hex", lambda n: "deadbeefdeadbeef")
    first = ark_storage.spool(io.BytesIO(b"one").read, "photo.jpg")
    second = ark_storage.spool(io.BytesIO(b"two").read, "photo.jpg")
    third = ark_storage.spool(io.BytesIO(b"three").read, "photo.jpg")
    assert len({first.stored, second.stored, third.stored}) == 3
    assert (ark.root / "drop" / "files" / first.stored).read_bytes() == b"one"
    assert (ark.root / "drop" / "files" / second.stored).read_bytes() == b"two"
    assert (ark.root / "drop" / "files" / third.stored).read_bytes() == b"three"


def test_spool_writes_outside_the_public_folder_then_moves(ark, monkeypatch):
    import ark_storage
    seen = []
    real_link = os.link

    def watching_link(src, dst, **kwargs):
        seen.append((Path(src), Path(dst)))
        return real_link(src, dst, **kwargs)

    monkeypatch.setattr(ark_storage.os, "link", watching_link)
    ark_storage.spool(io.BytesIO(b"payload").read, "guide.pdf")
    (src, dst), = seen
    assert src.parent.name == "tmp", "the partial file must not be inside the public folder"
    assert dst.parent.name == "files"
    assert src.exists() is False
    assert dst.read_bytes() == b"payload"


def test_reconcile_removes_only_abandoned_temp_files(ark):
    import ark_storage
    tmpdir = ark.root / "drop" / "tmp"
    old = tmpdir / "upload-old.part"
    old.write_bytes(b"abandoned")
    os.utime(old, (1, 1))
    fresh = tmpdir / "upload-new.part"
    fresh.write_bytes(b"in flight")
    assert ark_storage.reconcile_temp(max_age=3600) == 1
    assert not old.exists() and fresh.exists()


def test_the_temp_folder_is_never_served_by_the_web_server():
    conf = (Path(__file__).resolve().parents[1] / "pi" / "nginx" / "ark.conf").read_text()
    assert "alias /srv/ark/drop/files/" in conf
    assert "drop/tmp" not in conf
