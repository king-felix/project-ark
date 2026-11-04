# Storage admission — the complete design

*What may write to the card, how much it may promise, who else was promised the
same bytes, and what happens when the box cannot tell.*

This is the design record for the shared storage-admission policy. It is the
companion to `pi/api/ark_space.py` (the policy), `pi/api/ark_admission.py` (the
request-body door) and the growth paths listed in §2. It says what is enforced,
what is only measured, and what cannot be enforced by an application at all.

---

## 1. The rule

> **An application write may use the card until
> `max(7 % of the receiving filesystem's capacity, 2 GiB)` is left.**

* The capacity is the filesystem **that is actually receiving the bytes**, asked
  with `statvfs` at the moment of use — never a nominal card size.
* The free figure is `f_bavail`: the blocks an ordinary account may write. The
  slice ext4 keeps for root is not counted as room `pi` can have.
* The percentage is the owner's (`ARK_RESERVE_PERCENT`, default 7, sane range
  0–50); the 2 GiB floor is fixed. The reserve is never lowered to make room.

This is an **application admission policy**. It is not a partition, not a quota
for one person, and not a promise about the whole card. See §7 for the limits.

---

## 2. Every path that grows the card

The inventory below is the whole list. "Admission" is how the path decides it may
write; "peak" is the largest number of copies that exist at the same moment.

| Path | Where | Admission | Peak | Released on failure |
|---|---|---|---|---|
| nginx request body buffer | `pi/nginx/*.conf` | `client_max_body_size` per door + `proxy_request_buffering off` | 1× body | nginx |
| Starlette/FastAPI body spool | `TMPDIR=/srv/ark/tmp`, 1 MiB rollover | `ark_admission` middleware, before the first byte | 1× body | middleware `finally` |
| Public File Box upload | `POST /files` → `ark_storage.spool` | request promise adopted by `ark_space.cover` | 2× (framework + route copy) | `finally` + `os.unlink` |
| Owner films | `POST /admin/media/upload` → `ark_media.accept` → `ark_storage.spool` | same | 2× | same |
| Owner batch (books + games + films) | `POST /admin/upload` | same, per file | 2× the press | same |
| Book importer's copy into Calibre | `ark-import-books` | `ark-space --reserve` before each copy | 2× permanent (incoming + library) | `--release` in `finally` |
| Game upload | `POST /admin/arcade/upload`, `POST /arcade/add` | request promise + `ark_arcade_install.spool_upload` | 2× | `unlink` + `finally` |
| Game publish (download/copy) | `ark_arcade_install.publish_candidate` | `ark_space.cover` around the copy | 2× during copy | `unlink` |
| Guest/named saves | `PUT /arcade/api/saves/…` | door (16 MiB, 1 copy) | 1× | request ends |
| Save import | `POST /arcade/player/import.zip` | door (32 MiB, 2 copies) | 2× | request ends |
| Map pack import | `POST /admin/maps/import` → `ark_map_store.import_zip` | request promise, grown for extraction | archive ×2 + extracted | `finally` rmtree |
| Map folder/zip import (console) | `ark-map-store.import_path` | `ark_space.cover` / `hold` | archive + extracted | `finally` rmtree |
| Film derivative (remux/convert) | `ark_media_jobs.make_derivative` | `ark_space.hold(output_budget)` | original + derivative | `unlink` + `hold` release |
| Film poster | `ark_media_jobs.make_thumbnail` | inside the derivative's budget | + < 1 MiB | `unlink` |
| Offline library (ZIM) | `ark-zim-download` | `ark-space --reserve` for what is still to arrive | 1× (resumes in place) | `--release` on every exit |
| AI model | `ark-ai-model-fetch` | `ark-space --reserve` before download/copy | 1× | `--release` |
| Backup bundle | `ark-backup` | `ark_space.open_receipt` before the first file | (keep+1) bundles | `finally` |
| Recovery generation | `ark_recovery.capture` | essential reservation | database + pinned small files | `receipt.release()` in every exit |
| Database replacement | `ark_db_replace.replace` | essential room check (4× worst case) | 4× database | staged files unlinked |
| Carry-away pack | `ark_essentials.build` | essential reservation | sources + zip | `.partial` unlinked |
| Game catalogue rebuild | `ark_romdb_build.write_db` | essential reservation | old + new | `.partial` unlinked |
| Self-heal/db-repair archive | `ark_db_replace` / `ark_self_heal` | essential (recovery) | 1× database | archive pruned by retention |
| Restore | `ark-restore` | essential preflight on the manifest total | bundle + existing | — |
| Logs | logrotate, journald | size-capped externally | — | — |

Two paths are deliberately **not** swept or blocked, and the reason is in the
sweep table: `kiwix/*.part` and `ai/models/.*.part` are resumable downloads, so a
half-arrived file is useful work, not rubbish. `drop/tmp`, `arcade/tmp`,
`calibre-incoming`, `media/tmp`, `backup`, `essentials` and Starlette's own
`tmp????????` files **are** swept by age (`ark_self_heal.SWEEP_RULES`), because a
power cut leaves them and nothing will ever finish them.

---

## 3. The arithmetic

A reservation is a row in `space_reservations`, in the database every process
already shares:

```
bytes     the peak the operation may need at once
written   bytes that are on the card now
device    the filesystem's st_dev
pid, boot the process that made it, and the boot it was made in
```

Admission asks:

```
free − reserve − (every other promise not yet written) ≥ need
```

Three properties make that honest:

1. **`written` is subtracted from the promise, exactly once.** `statvfs` already
   reflects bytes that exist; the ledger only adds the part that does not exist
   yet. A byte that is on the card is not also promised — otherwise the box would
   refuse work it can do.
2. **`bytes` is a peak, never a sum of stages.** A request body and the copy the
   route makes from it overlap in time, so what must fit is the larger of the
   two beside the rest — not both added together. `Receipt.peak(key, total)`
   promises "what other stages have on the card now, plus this stage's total".
3. **Growth is bounded by real room.** An operation whose size is not known
   (`ark_space.first_step`, `growth_step`) promises at most one step *and* at
   most what is actually free at that moment. A fixed 16 MiB step is a fine idea
   until the card has 8 MiB left.

### Worked example — a 2 GiB File Box upload on a card with 4 GiB of room

| Moment | free | reserve | promised | written | admitted |
|---|---|---|---|---|---|
| before | 4.0 GiB | 0 | 0 | 0 | needs 2.0 + framing ≤ 4.0 ✔ |
| body arriving | 3.0 GiB | 0 | 1.0 GiB | 1.0 GiB | constant ✔ |
| route copy | 2.0 GiB | 0 | 2.0 GiB | 2.0 GiB | peak covers both ✔ |
| response ends | 2.0 GiB | 0 | 0 | 0 | promise released; the file is real |

The peak promised was `declared × 2 + framing`; the framework's temp and the
route's temp both existed, and the card held 4 GiB at the worst moment. A check
that counted the file once would have admitted 2 GiB more than the card had.

---

## 4. A promise belongs to a process, not to a clock

The first ledger had only a heartbeat. After fifteen minutes a worker that was
still encoding and a worker killed by a power cut looked the same, so
`reconcile()` could throw away a legitimate job's space — or keep a dead one's.

A row now records the **pid** and the **boot id**. `ark_space.live()` decides:

| State | Verdict |
|---|---|
| boot id differs from this boot | dead — nothing from the old boot is running |
| this boot, pid alive | **live**, whatever the heartbeat says |
| this boot, pid gone | dead |
| no pid (row from before migration 8) | live for `LEGACY_GRACE` (24 h), then dead |
| heartbeat "in the future" | live — the clock went backwards, not the worker |

A long-running worker therefore keeps its promise without refreshing anything,
and a power cut releases everything on the next start. `reconcile()` runs at
portal and media-worker start; `ark-self-heal` does not need to know about it.

**Clock assumptions never release a live promise.** A heartbeat is only a hint
for rows that have nothing else.

---

## 5. Request bodies — before and during receipt

### 5.1 Doors

Every request that can carry a body is measured against a door in
`pi/api/ark_admission.py`. The **default is 64 KiB**: a route nobody named cannot
accept two gigabytes by accident. The named doors are `/files` (8 GiB + framing,
2 copies), `/admin/media/upload`, `/admin/upload`, `/admin/arcade/upload`,
`/admin/maps/import`, `/arcade/add`, `/arcade/player/import.zip` and
`/arcade/api/saves/…` (16 MiB, 1 copy).

nginx agrees with the doors:

* the server-wide `client_max_body_size` is **64k** (it used to be 2064m), so a
  chunked POST to an ordinary page cannot be written to the card at all;
* `proxy_request_buffering off` on every listener, so a body reaches the
  application as it arrives and is counted as it arrives — with buffering on,
  nginx wrote the whole body before the backend could refuse it;
* each door names its own limit, and `/arcade/` was raised from 20m to 40m
  because a legal 32 MiB save import was being refused by the proxy before the
  application could say what was wrong.

### 5.2 Declared length

A `Content-Length` is a **fast path, never proof**. When it is present the
middleware promises the whole peak before the first byte: `length × copies +
framing`. A refusal is answered without reading the body. A client that sends
more than it declared is stopped at `declared + 1 MiB` — the header sized the
promise, so it does not get to spend more.

### 5.3 No `Content-Length` (chunked)

The first step promises `min(16 MiB, what is free)` including framing; each step
after grows by at most one step and at most the room that exists then. A body
that keeps arriving is refused the moment the next step cannot be promised, mid
stream, with a sentence — not after it has filled the card.

### 5.4 During receipt

Every 4 MiB the middleware re-reads the real filesystem and re-applies the
admission inequality. `statvfs` is the only thing that can see somebody else's
write: the ledger says what jobs promised, `statvfs` says what the card holds
now. If an outside process takes the room, the upload stops with *"The card
filled up while that file was arriving"*.

### 5.5 Bounded concurrency

At most `ARK_BODY_SLOTS` (default 4) large bodies are in flight at once; a fifth
waits `ARK_BODY_WAIT` (5 s) and is then told, with 503, to come back. Small
bodies (a form, a note) are not gated: they cannot grow anything.

### 5.6 The route adopts the promise

`ark_space.cover()` asks the current admission — bound with a `contextvar`, which
anyio copies into the worker thread Starlette runs a `def` route in — for a
receipt on the same filesystem. The spool grows *that* receipt and leaves the
release to the request. Nothing promises the same bytes twice, and a stage on a
different filesystem (a card plus a separate disk) gets its own receipt, because
two filesystems are not one budget.

### 5.7 Release

The middleware releases every receipt in a `finally`, which runs on success, on
a refusal, on an exception, and when the client disconnects and the task is
cancelled. A process killed outright leaves a row whose pid is gone, which the
next `reconcile()` releases. **No timer releases a promise that belongs to a
live process.**

---

## 6. What happens when something goes wrong

| Situation | What the box does |
|---|---|
| Card full (the reserve would be crossed) | 507 with a sentence; nothing stored; the route's own copy is unlinked |
| Body over the door's limit | 413 before the body is read |
| Second large body while one is arriving | 503, "the box is already receiving other files" |
| Client disconnects mid-body | task cancelled; promise released in `finally` |
| Route raises | promise released; no half file |
| Ledger unreadable | **bulk writes refused** (`LedgerUnreadable`, 503); pages print *not measured yet*, never 0 |
| Ledger unreadable, essential work | recovery, restore, db replacement, the carry-away pack and the catalogue still run, bounded by real free space only |
| Somebody else fills the card | the next 4 MiB check stops the upload with a sentence |
| Process killed | pid gone → released by the next reconcile |
| Power cut | boot id differs → everything released at start |
| Clock jumps backwards | a promise "in the future" is fresh; nothing is released |
| Worker runs for hours | kept: liveness is the pid, not the heartbeat |

`LedgerUnreadable` is deliberately a subclass of `Refused`, so every caller that
already shows a refusal keeps working; it is a separate class so a page can say
*why*.

---

## 7. What this cannot do

* **An unrelated OS or shell write can still fill the card.** A package install,
  a log, `dd`, another process on the same filesystem — the application can see
  them (via `statvfs`, on every step) and can stop its own work, but it cannot
  prevent them. The reserve is an admission policy, not a partition.
* **It cannot stop nginx writing a body it was told to buffer.** The mitigation is
  configuration: buffering off, small default caps. A future edit that turns
  buffering back on removes the guarantee, and `tests/test_config.py` checks the
  setting.
* **It is not a per-person quota.** One visitor can use all the room that is
  free; the policy is about the box, not about fairness.
* **It does not delete a person's original to make room.** A refusal is a
  refusal. Sweeps only remove files nothing will ever finish (see §2).
* **It cannot make a physical card bigger.** The reserve is what keeps the portal
  answering; the answer to "full" is still "delete something".

---

## 8. Operating it

```bash
ark-space --status                 # free, kept back, promised, may be added
ark-space --status --json          # the same numbers for a program
ark-space --check --need N         # exit 0 if it fits, 3 with a sentence
ark-space --check --need 0         # "may anything be added at all?"
ark-space --reserve --need N --owner NAME --pid $$   # prints the promise id
ark-space --touch ID --written N   # how much of the promise is on the card
ark-space --release ID             # give the rest back
ark-space --reserve --essential …  # free-space only (recovery, records)
```

`ARK_RESERVE_PERCENT` lives in `/etc/ark/ark.env`. `ARK_BODY_SLOTS` and
`ARK_BODY_WAIT` tune request concurrency. `ARK_SPACE_FAKE`,
`ARK_SPACE_FAKE_TOTAL` and `ARK_SPACE_FAKE_FREE` exist **only** for the tests
that need a small card shared by real processes; a real box never sets them, and
all three must be present before the policy pretends.

The owner sees total, free, kept back, promised to running jobs, and what may
still be added on **Overview → Health** and on the **Media** page. When the
ledger cannot be read, those figures say *not measured yet* and the sentence
says why.

---

## 9. Where the pieces live

| File | Owns |
|---|---|
| `pi/api/ark_space.py` | the policy, the ledger, receipts, liveness, `cover`, `essential_room` |
| `pi/api/ark_admission.py` | doors, the ASGI body middleware, the in-request promise |
| `pi/api/ark_storage.py` | the one write path for shared files (adopts the request's promise) |
| `pi/api/ark_ingest.py` | the book spool (same) |
| `pi/api/ark_arcade_install.py` | the game spool and publish copy (same) |
| `pi/api/ark_map_store.py` | archive + extraction under one growing promise |
| `pi/api/ark_media_jobs.py` | a derivative's output budget, reserved for the whole encode |
| `pi/api/ark_recovery.py` | an essential promise across a recovery capture |
| `pi/api/ark_db_replace.py`, `pi/bin/ark-restore` | essential room for putting records back |
| `pi/bin/ark-space` | the shell door to all of the above |
| `pi/nginx/*.conf` | the proxy's half: small default, no buffering, per-door caps |
| `pi/api/ark_db.py` | migration 8 (pid, boot, kind); `current_transaction()` so a ledger write joins the caller's transaction instead of deadlocking on SQLite's one writer |

---

## 10. Tests — and what they do not prove

`tests/test_admission.py` (28) and `tests/test_space.py` (13) cover:

* six real processes on one simulated card: exactly two of six 4 MiB promises
  succeed, and the ledger sums to 8 MiB;
* a child that reserves and is killed: its row is released by `reconcile()`;
* a live process with a six-hour-old heartbeat: kept;
* a row from another boot with a live pid: released;
* a heartbeat in the future: kept;
* an unreadable ledger: bulk refused with `LedgerUnreadable`, pages say
  *not measured yet*, essential work still admitted;
* a declared body refused before it is read (507, no file, no row);
* the same file admitted on a card with room for one copy and refused on a card
  with room for two — the peak, not the file size;
* a chunked body admitted in steps, and a chunked body too big stopped mid-stream
  with nothing stored;
* an unknown address is a 64 KiB door, never unbounded;
* the route's spool adopts the request's receipt (one row, not two);
* a real uvicorn, a real socket, half a body, then a disconnect: the promise is
  released and no file is left;
* another writer taking the card mid-upload: the upload stops with a sentence;
* deleting a file still works on a full card; a recovery copy is still kept when
  every spare byte is promised to another job;
* peak arithmetic and `written` accounting (no byte promised twice).

**Not proved here:**

* no physical power cut was performed (the boot-id rule is tested by writing a
  row from another boot, not by pulling the plug);
* no real phone was used (the disconnect test uses a socket);
* a card cannot be filled to `ENOSPC` without root, so the refusal is proved
  against a bounded simulated capacity, not a real full filesystem;
* one process outside the application can still fill the card; the test proves
  the box *notices and stops*, not that it can prevent it.

Those remain in `docs/HUMAN-TEST-LIST.md`.
