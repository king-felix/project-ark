"""Importing a dropped book must be idempotent and must not break a File Box link.

Two defects lived here: ``candidates()`` walked the *incoming* folder including
its own ``done/`` folder, so every scan offered already-imported books again; and
``_retire()`` moved a book out of ``/srv/ark/drop/files``, which is exactly the
folder the File Box serves — every link to an imported book died at that moment.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "pi" / "bin" / "ark-import-books"


def _module():
    """Load the hyphenated script as a module, exactly as the Pi runs it."""
    spec = importlib.util.spec_from_loader("ark_import_books", loader=None)
    module = importlib.util.module_from_spec(spec)
    source = SCRIPT.read_text(encoding="utf-8")
    exec(compile(source, str(SCRIPT), "exec"), module.__dict__)
    return module


class _Box:
    """A throwaway copy of the three folders the importer walks."""

    def __init__(self, module, tmp_path, monkeypatch):
        self.drop = tmp_path / "drop" / "files"
        self.incoming = tmp_path / "calibre-incoming"
        self.done = self.incoming / "done"
        for folder in (self.drop, self.incoming, self.done):
            folder.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(module, "DROP_DIRS", (str(self.drop), str(self.incoming)))
        monkeypatch.setattr(module, "DONE_DIR", str(self.done))
        monkeypatch.setattr(module, "book_count", lambda: 0)
        monkeypatch.setattr(module.shutil, "which", lambda name: None)
        self.uploaded: list[str] = []

        def fake_upload(paths):
            self.uploaded.extend(os.path.basename(p) for p in paths)
            for path in paths:
                module._retire(path) if not str(path).startswith(str(self.drop)) else None
            return True

        monkeypatch.setattr(module, "upload_via_web", fake_upload)
        monkeypatch.setattr(module, "calibredb_add", lambda paths: False)
        self.module = module
        self.ledger = self.incoming / ".imported.json"

    def put(self, folder: Path, name: str, data: bytes = b"book") -> Path:
        path = folder / name
        path.write_bytes(data)
        return path


def test_retired_books_are_not_offered_again(tmp_path, monkeypatch):
    module = _module()
    box = _Box(module, tmp_path, monkeypatch)
    box.put(box.done, "already-imported.epub")
    box.put(box.incoming, "new.epub")
    found = module.candidates(list(module.DROP_DIRS))
    assert found == [str(box.incoming / "new.epub")]


def test_partial_and_hidden_files_are_ignored(tmp_path, monkeypatch):
    module = _module()
    box = _Box(module, tmp_path, monkeypatch)
    box.put(box.drop, "still-arriving.epub.part")
    box.put(box.drop, ".hidden-book.epub")
    box.put(box.drop, "notes.txt.part")
    box.put(box.drop, "real.epub")
    found = [os.path.basename(p) for p in module.candidates(list(module.DROP_DIRS))]
    assert found == ["real.epub"]


def test_a_book_is_imported_once_and_its_hash_is_recorded(tmp_path, monkeypatch):
    module = _module()
    box = _Box(module, tmp_path, monkeypatch)
    box.put(box.incoming, "first.epub", b"same content")

    assert module.main([]) == 0
    assert module.main([]) == 0          # a rescan must not import it twice
    assert module.main([]) == 0
    assert box.uploaded == ["first.epub"]

    ledger = json.loads(box.ledger.read_text(encoding="utf-8"))
    digest = module._sha256(box.done / "first.epub")
    assert digest in ledger, "the ledger is keyed by content, not by name"
    assert ledger[digest]["name"] == "first.epub"


def test_the_ledger_survives_a_rewritten_file(tmp_path, monkeypatch):
    """A stale ledger entry must never hide a genuinely different book."""
    module = _module()
    box = _Box(module, tmp_path, monkeypatch)
    first = box.put(box.incoming, "book.epub", b"edition one")
    assert module.main([]) == 0
    retired = box.done / "book.epub"
    retired.unlink()
    box.put(box.incoming, "book.epub", b"edition two")
    assert module.main([]) == 0
    assert box.uploaded == ["book.epub", "book.epub"]


def test_the_file_box_copy_stays_put_so_links_keep_working(tmp_path, monkeypatch):
    """A book shared in the File Box is imported, but the served copy is not moved."""
    module = _module()
    box = _Box(module, tmp_path, monkeypatch)
    shared = box.put(box.drop, "abc123-manual.pdf", b"%PDF-1.4 ...")

    assert module.main([]) == 0
    assert box.uploaded == ["abc123-manual.pdf"]
    assert shared.exists(), "the File Box link must survive the import"
    assert shared.read_bytes() == b"%PDF-1.4 ..."
    assert module.main([]) == 0
    assert box.uploaded == ["abc123-manual.pdf"], "the ledger stops a second upload"


def test_new_content_under_an_old_name_is_imported_again(tmp_path, monkeypatch):
    module = _module()
    box = _Box(module, tmp_path, monkeypatch)
    first = box.put(box.drop, "same-name.pdf", b"edition one")
    assert module.main([]) == 0
    first.unlink()
    box.put(box.drop, "same-name.pdf", b"edition two")
    assert module.main([]) == 0
    assert box.uploaded == ["same-name.pdf", "same-name.pdf"]


def test_a_failed_import_is_not_recorded_as_done(tmp_path, monkeypatch):
    module = _module()
    box = _Box(module, tmp_path, monkeypatch)
    box.put(box.incoming, "problem.epub")
    monkeypatch.setattr(module, "upload_via_web", lambda paths: False)
    assert module.main([]) == 1
    assert not box.ledger.exists()
    assert (box.incoming / "problem.epub").exists(), "it stays for the next attempt"
