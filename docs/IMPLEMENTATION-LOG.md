# IMPLEMENTATION LOG — reliability, central administration, practical usability

Durable checkpoint for the owner's brief. **Read this first after any context
reset.** It records what is done, what was decided and why, what was measured,
and what the next executable step is.

| | |
|---|---|
| Baseline reviewed | `dd3336c` (`v1.0.5`) — the published release |
| Branch | `feature/admin-hardening` (nothing force-pushed; no release tag moved) |
| Platform used for work | macOS 26.x, Python 3.9.6 (LibreSSL), venv in `.venv/` |
| Appliance target | Raspberry Pi 4B, Raspberry Pi OS Lite, Python 3.13.5, nginx 1.22 |
| Deployment status | **not deployed** — code, config and tests only |
| Hardware tests | **none performed** (no browser/phone in this environment) |

---

## 1. Status against the brief

| § | Area | Status |
|---|---|---|
| 3 | Public read-only vs administrator | **split done; anonymous writing is now the owner's choice** — the board and the File Box stay open by default, and an owner switch closes each one without losing anything already written (§3.4) |
| 4 | Central accounts, bootstrap, forced change, multi-admin | **stage 2 complete and tested** |
| 5 | Management transport, backend restriction, identities | **transport done** (8443 origin, 8444 loopback, :80 404; `kiwix-serve` now loopback-only); service-identity split and Calibre/Kiwix route blocking **not done** |
| 6 | File storage, upload safety, import workflow | **stage 3 done** — one write path, unique stored names, size cap + free-space reserve, orphan cleanup, content-keyed import ledger. Quotas per person and a quarantine/approval queue **not done** |
| 7 | Board errors and housekeeping | **done for stage 3** — friendly errors, pagination, reversible moderation (pin / sorted out / hide) with audit lines |
| 8 | Bounded search | **fixed** — process pool + bounded wait, deadline respected |
| 9 | Truthful downloads/readiness/health | **stage 4 done** — one module decides per-section readiness with a reason and a next step, `/api/ready` answers 200/503, `/admin/health` is the owner's page, `/api/health` is liveness only, and progress comes from measured bytes (§8) |
| 10 | Network loss after boot | **stage 5 done** — `ark-net-watch` asks the boot guard's question again every minute, rescues the hotspot after two lost checks, returns home when the network is back and nobody is using the hotspot, never takes the access point from a connected device, and never overrules a choice the owner made with `ark-mode home` / `ark-mode hotspot` (§10) |
| 11 | Backup / offline recovery kit | not started |
| 12 | Emergency content review | not started |
| 13 | Essentials pack, QR, accessibility, household page | not started |
| 14 | Sequence | stages 1–5 done, stage 6 next |
| 15 | Acceptance tests | 128 tests, all green (`pytest -q`) |

---

## 2. What changed in stage 2

New files:

| File | Purpose |
|---|---|
| `pi/api/ark_db.py` | one connection policy, numbered migrations, audit helper, install marker |
| `pi/api/ark_security.py` | scrypt hashing, password policy, opaque sessions, CSRF, throttling |
| `pi/api/ark_admin.py` | `/admin` routes: overview, sign-in, forced setup, accounts, audit |
| `pi/api/ark_recover.py` | root-only recovery logic (called through the wrapper) |
| `pi/bin/ark-tls-cert` | creates/renews the box's own certificate, prints the fingerprint |
| `pi/bin/ark-admin-recover` | `0700 root:root` wrapper; the only web-inaccessible way back in |
| `pi/nginx/ark-admin.conf` | `:8443` management origin + `:8444` loopback setup listener |
| `pi/steps/65-admin.sh` | installs the above and proves each door (404/403/TLS-only) |
| `pi/portal/assets/ark-admin.css` | admin-only styling (48 px targets, focus rings, print-safe) |
| `tests/*` | 42 tests, throwaway databases, `pytest` |

Modified: `pi/api/main.py` (mounts the router, shared DB layer, atoms for the
error page), `pi/nginx/ark.conf` (`/admin` → 404 on :80), `pi/steps/60-portal.sh`
(copies the new modules, checks the 404), `pi/install-ark.sh` (adds the step),
`README.md`, `.gitignore`, `pytest.ini`.

### Documentation brought into line with the code

Stage 2 made four published statements untrue. They were corrected in the same
change rather than left to rot:

| Document | Was | Now |
|---|---|---|
| `README.md` | "no accounts, no logins" flatly; phase list without `65-admin` | the single sign-in is named and pointed at the owner's-door section; `65-admin` is in the phase list; "Checking it" describes the new gate checks (404 on :80, both listeners, recovery command, temporary-password count) |
| `docs/USING-THE-ARK.md` | "**It has no logins.**" | "**Reading needs no login**" — anonymous reading/posting/uploading kept as the rule, the one owner's door named and explicitly not on the portal address; cheat sheet gains `ark-tls-cert --show` and `ark-admin-recover --list`; maintainer notes list the nine phases and say where accounts, marker and certificate live |
| `docs/REBUILD-ME-ARK.txt` | no owner's door in the doors list; "no accounts, no logins, ever"; an eight-phase file map; "30 ✓" | owner's door added to the plan; anonymous **reading** stated as the rule with administration as the single exception; phase 65 in the build order with its own proof; nine phases; admin modules, listeners and recovery command in the file chart; gate categories and definition-of-done corrected; trap 26 added (the default password a stranger could reach) |
| `docs/REBUILD-FROM-BACKUP.txt` | "the gate — must be 30/30 with no ✗" | the gate's actual contract (no ✗ plus the closing line), a one-time owner's-door claim step, an owner's-door sanity check, and four session gotchas (parallel tool calls, apostrophes in commit messages, run `pytest` before tagging, post-tag work is not in the archive) |

No code changed for this; `pytest` was re-run afterwards to confirm the tree
still does what the documents now say.

---

## 3. What changed in stage 3 — uploads, imports, the board, search

New file: `pi/api/ark_storage.py` — the only place a file may be written, and the
only place a display name is decided.

| Rule | Why it is that way |
|---|---|
| The name a person sees is a **label**, never a path | `display_name()` strips directories, control characters and anything a URL or filesystem reads as structure. It never decides where the file lives, so `../../etc/passwd` arrives as `passwd`. |
| Every upload gets its **own** place on disk | The stored name is 128 random bits plus the label, published with `os.link()`, which *refuses* to replace an existing file. Three people sharing `photo.jpg` in one minute now produce three rows and three files (was: the third overwrote the second). |
| Nothing half-written is ever visible | Bytes land in `drop/tmp` (not served by anything), are flushed and `fsync`ed, and only then appear in `drop/files`. A refused upload leaves no residue; `reconcile_temp()` clears anything older than six hours at start-up. |
| The cap is enforced while streaming, with a reserve | 2 GiB per file — the same number nginx allows (plus 16 MiB of request framing, `client_max_body_size 2064m`) — and the box refuses to write into its last 512 MiB, because a full card takes the whole portal down, not just one upload. An empty file is refused with a sentence a person can act on. |

### 3.1 The File Box page

Downloads use the stored name (`/drop/<stored>`), so the link is stable and the
label can be anything. Hidden files disappear from the reading page but stay on
the card; the owner's page lists the last fifty with a size and a Hide/Show
button. Deleting a file is the only irreversible action on the box, so it asks
for the owner's own password again and is confined to `drop/files` by a parent
check (`_remove_from_card`).

### 3.2 The book importer

`ark-import-books` now keeps `.imported.json` beside `calibre-incoming/done/`,
keyed by the **content** fingerprint (sha256):

* `done/` is no longer scanned, so imported books cannot be imported again.
* A file is skipped when its hash is already in the ledger — dropping the same
  book again, even renamed, adds nothing.
* A **File Box copy is never moved or deleted**: `/drop/<stored>` keeps working
  after the book is on the shelf. Only files in `calibre-incoming/` are retired
  to `done/`.
* Only files that genuinely became books (or File Box copies, which stay put)
  are written to the ledger; a failed import is offered again on the next run.
* Cost, stated plainly: each run fingerprints every candidate file, so a large
  File Box means a few seconds of hashing per drop. It runs `Nice=10` with idle
  I/O and blocks nothing.

### 3.3 Search

`search_library()` no longer builds an executor per request. One process-wide
pool (`SEARCH_WORKERS = 6`) is used, and the deadline is enforced with a bounded
`future.result(timeout=…)` over the *unfinished* set — the pool is never shut
down on the request path, so the deadline actually returns. Answers that arrive
late are dropped instead of holding the page open (was: ≈20–30 s worst case).

### 3.4 The board and its switches

* Reading is paginated (`?p=`, 20 a page, newest first) and shows a total.
* Moderation is reversible and never deletes: **pin / unpin**, **sorted out /
  still open**, **hide / show again**. Every action writes one audit row.
* The column name written by each action comes from a fixed table
  (`BOARD_ACTIONS`, `FILE_ACTIONS`), never from the request.
* Two owner switches, on the Community page: *anyone nearby can post a message*
  and *anyone nearby can add a file*. Both default to on. Switching off stops new
  writes with a friendly 403 and changes nothing already stored — reading,
  downloading and the whole library keep working.

## 4. Defects reproduced against the checkout (evidence, not hearsay)

| Defect | Where | Evidence |
|---|---|---|
| `error_page()` wrapped an `HTMLResponse` inside another → `AttributeError`, HTTP 500 on an invalid board post | `main.py` (pre-fix) | `tests/test_public.py::test_invalid_board_post_returns_400_not_500` now asserts a 400; before the fix the same call raised `AttributeError: 'HTMLResponse' object has no attribute 'encode'` |
| Upload naming collides: second upload gets `YYYYmmdd-HHMM-name`, the third **overwrites** it inside the same minute | `main.py` `files_post` (pre-fix) | **fixed** — `tests/test_storage.py` proves three same-name uploads keep three files; live check on 24 Oct: three `evil name.txt` uploads produced three rows, three stored names, three files |
| `search_library()` "12 s deadline" drains: leaving `as_completed(timeout=…)` waits for the executor context manager to join every worker, and 20 collections ÷ 6 workers × 9 s per source is far past the budget | `main.py` | **fixed** — `tests/test_search.py` holds a slow source and asserts the page returns inside the budget |
| nginx allows 4 GiB bodies while the API rejects at 2 GiB | `ark.conf` vs `MAX_UPLOAD` | **fixed** — `client_max_body_size 2064m` = 2 GiB + 16 MiB of framing; `tests/test_config.py` keeps the two numbers tied together |
| `kiwix-serve` listens on `*:8081`, bypassing the proxy | live Pi `ss -ltnH` | **fixed in the unit** — `--address=127.0.0.1` (`pi/systemd/ark-kiwix.service`); not yet re-checked on hardware |
| Database connections never closed (`with db() as conn` is a transaction, not a close) | `main.py` | replaced by `ark_db.read()` / `ark_db.tx()` in stage 2 |
| README says `/srv/ark/zim-pause`; the downloader reads `/etc/ark/zim-pause` | `README.md` vs `pi/bin/ark-zim-download:21` | fixed in stage 2; profile name `min` → `mini` fixed too |
| Importer rescanned `done/`, so an already-imported book could be added again under a new name | `pi/bin/ark-import-books` (pre-fix) | **fixed** — `tests/test_import.py` runs the importer twice and asserts one book; the ledger is keyed by content |

---

## 5. Decision log

Numbered so later stages can cite them. Change one only with a reason written
here.

**D1 — scrypt (stdlib), not Argon2id.** The brief allows either. `hashlib.scrypt`
needs no new dependency on the appliance (OpenSSL 3 is already there), while
`argon2-cffi` adds a compiled wheel to a box that may be rebuilt offline.
Parameters `n=2^15, r=8, p=1` cost 32 MiB and are recorded inside each hash, so
they can be raised later without invalidating accounts (`needs_rehash` +
rehash-on-sign-in already handle the migration path).

**D2 — one extra code path, for the developer machine only.** Apple's Python
links LibreSSL, which has no `hashlib.scrypt`, so the suite could not hash
anything. `pi/api/ark_security.py` falls back to the `cryptography` package's
scrypt when the stdlib one is missing. The Pi never takes that branch, and
`tests/test_db.py::test_hashes_from_the_pi_verify_here` pins a digest produced
by the Pi's `hashlib` and verifies it on the Mac, so the two cannot silently
diverge. The fallback is *scrypt with the same parameters*, never a weaker
scheme.

**D3 — the marker file is the memory of "configured".** `ark.installed` lives
next to the database but outside it, so deleting the database cannot resurrect
the public bootstrap password: the box reports *recovery-required* and points at
`ark-admin-recover`.

**D4 — the first claim needs the loopback listener.** `:8444` is bound to
`127.0.0.1`, reachable through `ssh -L`. nginx sets `X-ARK-Setup-Channel:
loopback` there and *blanks that header* on `:8443`; the API additionally
requires the peer address to be loopback. A caller-supplied header alone can
never open that door (tested).

**D5 — separate origin, shared process.** Admin routes live on the same
FastAPI app but only nginx's TLS listener proxies them; `:80` answers 404 for
`/admin`. Cookies are `Path=/admin`, `Secure`, `SameSite=Strict`, and no
`Domain`, so an XSS on the reading pages cannot ride the admin cookie, and a
cross-site POST cannot attach it.

**D6 — sessions do not survive a restart.** On start the app bumps a `session_epoch`
and deletes session rows. Cost: everyone signs in once after a reboot. Benefit:
a stolen cookie cannot outlive a restart, and a jumped clock cannot extend a
session indefinitely.

**D7 — one role.** "Administrator" only. The brief explicitly forbids an
unnecessary hierarchy; every account can do everything, and the last-admin rule
keeps the box reachable.

**D8 — no JavaScript in the admin area.** Forms, links and `<details>` only.
Progressive enhancement can come later for long jobs, not for control paths.

**D9 — 0700 root:root recovery, run as `pi`.** The wrapper is root-only but
executes the Python as the portal user, so the SQLite WAL files stay owned by
`pi` and the running service keeps working.

**D10 — keep `pi` as the service user for now.** Splitting service identities
(§5 of the brief) touches file ownership across `drop/`, `api/data` and the
venvs and belongs with the upload rework; doing it now would risk the working
library for no user-visible gain.

**D11 — tests live in `tests/`, outside `pi/`.** They need fastapi/httpx/pytest,
which the appliance does not install. `tests/requirements.txt` documents the
extra packages, and the suite uses only temporary directories.

**D12 — the display name is a label; the stored name is the address.** The
person's filename is never used as a path, so no upload can escape `drop/files`
or overwrite another. Uniqueness comes from 128 random bits *and* from `os.link`,
which refuses an existing target — belt and braces, because "almost certainly
unique" is not the same as "cannot collide". `/drop/<stored>` is therefore a
permanent, unguessable link; the label can be anything.

**D13 — a File Box copy is never the importer's to move.** Retiring a book out of
`drop/files` used to kill the download link the moment the book reached the
shelf. The importer now leaves that copy alone and records its fingerprint in
`.imported.json` instead, so the book is not added twice and the link still works.
Cost: the file exists twice (File Box + library). The owner can delete the File
Box copy from the Community page — that removes the link, never the book.

**D14 — moderation is reversible, and the column name never comes from the
request.** Pin, sorted-out and hide are three booleans with an inverse action
each; nothing is deleted and every action writes one audit row. The SQL column is
chosen from `BOARD_ACTIONS` / `FILE_ACTIONS`, so a crafted form field cannot reach
`execute()`. Deleting a file is the single irreversible action and costs the
owner's own password again.

**D15 — the search pool outlives the request; the deadline is a bound, not a
promise to wait.** One process-wide `ThreadPoolExecutor` with
`SEARCH_WORKERS = 6`; the request waits on the *unfinished* set with
`future.result(timeout=remaining)` and returns whatever arrived. Late answers are
dropped. Shutting the pool down per request is what made the old 12 s deadline
take 20–30 s.

**D16 — ready means a reader could use it, so the content is not enough.** A
section is `ready` only when the content is on the card *and* the program that
serves it answered on the loopback address (`urllib` probe of
`127.0.0.1:8081/kiwix` and `127.0.0.1:8083`). Any HTTP status counts as an
answer — a 404 is a running server with a moved page, which is a different
problem from a refused connection. `ark_health.py` is the only place that
decides, and every section carries `reason` (a sentence a person can read) and
`action` (empty when nothing is wrong). Two probes per refresh, cached 10 s,
0.8 s timeout, and the owner's page clears the cache because it asked *now*.

**D17 — "empty" and "broken" are two different answers, and downloading is never
a fault.** Each section carries `content` next to `ready`: a box that is still
filling up is *not ready* with `content=false` ("No collection has arrived yet"),
while files that exist behind a dead service are *not ready* with
`content=true` ("3 collections are on the card, but the reader is not
answering"). Only `BLOCKING_SECTIONS` (library, books, manuals, messages, files,
storage) can make the box un-ready; `downloads` reports progress, and a queue
that has gone silent for over two minutes is reported as stalled, but neither
ever blocks reading. The front page uses the
same flag to pick green / amber / red, so a dot can be wrong no longer.

**D18 — a percentage or a countdown that was not measured is `null`, never
0.** `ark-zim-download` writes `current_rate_bps` from the byte delta against
elapsed time (0 while stalled); `ark_health.progress()` adds the finished bytes
(`bytes_on_disk`) to the partly written one (`current_bytes`) before dividing,
reports `overall_percent` for the whole queue while keeping `percent` as the
current collection for older readers, and yields `eta_seconds = None` whenever
the queue has no total or no measured rate. `reported: false` says the queue has
not written a status file at all — the difference between "nothing is
downloading" and "nobody told us".

**D19 — the network question is asked every minute, and the decision is a pure
function.** `ark-boot-guard` answers once, on the way up; a home router can be
rebooted at any hour afterwards. `ark-net-watch` (oneshot, timer
`OnBootSec=3min` + `OnUnitActiveSec=60s`) reads six facts — saved mode, any link
up, hotspot up, home network in range, clients on the hotspot, owner pin — plus
three counters from `/srv/ark/logs/net-watch.state`, and hands them to
`decide()`, which returns `stay`, `to-hotspot` or `to-home` and touches nothing.
`--decide` exposes that function alone (no radio, no root, no NetworkManager), so
the whole table is a table-driven test instead of a re-implementation in Python.

**D20 — a reader on the hotspot outranks a tidier network.** The hotspot is only
given up when the home network is in range **and** `iw … station dump` counts
zero stations. A count that could not be taken (`iw` missing, radio busy) is
`-1`, which the table treats as *somebody may be there* — the safe answer, because
the cost of being wrong is dropping the page someone is reading. Two consecutive
lost checks (`LOST_STREAK=2`) are needed before the hotspot is raised, so a
router blinking for one minute does not rewrite the network.

**D21 — a pin is a person's answer; a failed return is not retried every minute.**
`ark-mode home` and `ark-mode hotspot` write `/etc/ark/mode.pinned`; `ark-mode
auto` deletes it; the watchdog calls `ark-mode hotspot --auto`, which is the one
call that leaves no pin — that is how "the box did this to itself" and "the owner
said so" stay distinguishable. A pin suppresses the return home **only**: a box
that has lost its link is still rescued, a pinned hotspot is never closed by the
watchdog, and a pinned home network is still replaced by the hotspot when the link
dies. After a return that failed (`ark-mode auto` ran, the mode is still
`hotspot`) the next attempt waits `HOME_RETRY_SECONDS=1800`: an access point that
flaps is worse for a reader than one that stayed up. A quiet check writes nothing,
so `net-watch.log` holds decisions, not a heartbeat.

**D22 — the storage breakdown is measured, folder by folder, and absent is not
zero.** `ark_health.storage_breakdown()` walks five folders (Offline Library,
Books, Manuals & Guides, File Box, Settings and records) and lists only the ones
that hold something, because five rows of "0 B" tell the owner nothing about what
to delete; `card_facts()` adds `shutil.disk_usage()` for the whole card and
returns `None` when it cannot ask. `storage_section()` carries `breakdown`,
`breakdown_bytes`, `card_total_bytes` and `card_used_bytes`; the owner's page
renders the table and the measured total, and renders nothing at all when the
card is empty. No figure on that page is an estimate.

---

## 6. Test evidence

```
$ .venv/bin/python -m pytest -o addopts='--strict-markers' -q
........................................................................ [ 56%]
................................................................         [100%]
128 passed in 19.93s
```

(`pytest.ini` already passes `-q`, so a second `-q` hides the summary line; the
command above is the one that prints the count.)

Breakdown: `test_network` 28 · `test_health` 16 · `test_board` 15 · `test_auth`
15 · `test_accounts` 13 · `test_storage` 11 · `test_import` 7 · `test_public` 7 ·
`test_db` 7 · `test_search` 6 · `test_config` 3.

Environment: macOS, Python 3.9.6, fastapi/uvicorn/httpx/pytest from
`tests/requirements.txt`, scrypt via the `cryptography` fallback.
Database: a fresh `tmp_path` per test; the live library is never opened.

Live smoke check on the development machine (uvicorn on `127.0.0.1:8099`,
`ARK_ROOT=/tmp/ark-smoke2`), 24 Oct 2026:

```
GET /search?q=water 200 · GET /board 200 · GET /files 200
three uploads named "../../etc/evil name.txt", "evil name.txt", "evil name.txt"
  → 303, 303, 303
  → files table: 3 rows, display "evil name.txt", stored
     1836769baabb5282-…, f72373dc8f4ddb4c-…, 9b13a4a3bb715cd7-…
  → drop/files: 3 files · drop/tmp: empty · page lists all three
```

Cross-implementation check, run on the appliance:

```
$ ssh pi@ark.local python3 -c 'hashlib.scrypt(b"cross-check passphrase", salt=b"\0"*16, n=1<<15, r=8, p=1, dklen=32)'
1e8c96673daca25043c299e0c4b507211a24f3b7622a08abda33fc7563e5b8be
```
…and the same value is the one embedded in `tests/test_db.py`, where it is
verified through the Mac's fallback.

---

## 7. Next executable step

Stages 3, 4 and 5 are closed. What comes next (each with a test before the code):

1. **Stage 6 — backup and the offline recovery sheet.**
2. **Stage 7 — the household layer.** Emergency content review, the essentials
   pack, QR cards, accessibility and the household page.

### 7.1 Stage 6 in detail — not started, nothing of it exists

| Piece | What it must do |
|---|---|
| `pi/bin/ark-backup` | one dated bundle to a path it is given: the database via `sqlite3 .backup` (never `cp` — WAL), `drop/files/`, `calibre-incoming/.imported.json`, and a manifest with per-file sha256, sizes, `SCHEMA_VERSION`, `git describe` and the time. Written as `<name>.partial`, published by rename, so a half-finished backup cannot pass for a whole one |
| `pi/bin/ark-restore` | verify the manifest and refuse a bad bundle; refuse an installed box without `--force`; restore into the same absolute paths, `chown -R pi:pi`, then say what it put back |
| `docs/RECOVERY-SHEET.txt` | one printable page, plain words (§5 of the rules), usable with a phone and a card reader and nothing that lives on the card |
| a phase (`95-backup.sh` or folded in) | install both binaries, create the folder, prove `--dry-run`, print the restore command |
| `tests/test_backup.py` | round-trip: manifest verifies, a corrupted file is refused, `.imported.json` returns so a re-drop is still not a duplicate, an installed database is not overwritten without `--force`, no `.partial` survives |

A backup only reads; `ark_storage.spool()` stays the single writer of
`/srv/ark/drop/files`, and a restore is the one deliberate exception. Collections
(ZIMs) are content, not state, and stay out of the default bundle. Defaults: write
to `/srv/ark/backup`, keep the newest two, run when asked — a nightly timer needs
the owner's explicit yes.

Left over from stages 3–5, on purpose (do not describe these as done):

* **Per-person quotas and a quarantine/approval queue** — the cap is per file
  (2 GiB) and the box protects its last 512 MiB, nothing is metered per device,
  and every upload is immediately downloadable.
* **Trash** — hide is reversible but delete is for ever; there is no middle step.
* **Anonymous uploads are still anonymous** — the switch turns writing off, it
  does not tell the owner *who* wrote.
* **Nothing on the owner's page can act on the network.** `ark-mode` still needs
  a console; there is no "broadcast now" button, and the watchdog's own log is a
  file to read, not a screen. Network-loss failover has never been tried on the
  real box (see §10).

Definition of done for each: a failing test first, then the fix, then
`pytest` green, then a checkpoint line here.

Rules that still apply: never force-push, never move `v1.0.5`, keep the public
reading path working at every commit, do not deploy or reboot the live box
without the owner's explicit go-ahead.

---

## 8. Stage 3 checkpoint — 24 Oct 2026

`pytest` **84 passed** (9 files, breakdown in §6). Branch `feature/admin-hardening`,
nothing deployed, no hardware test — this is a code-and-tests checkpoint only.

* **New:** `pi/api/ark_storage.py` — the single write path (128-bit stored name,
  `os.link` finalise, spool in `drop/tmp`, cap while streaming, 512 MiB reserve,
  orphan-temp sweep). `tests/test_storage.py` (11 tests) is its contract.
* **Schema v2** — `notes.pinned/resolved/hidden`, `files.stored/display/sha256/hidden`
  (`settings` itself is v1). Old rows keep `stored` as the empty string and are
  read through `_stored_name()`, so an upgraded box keeps serving the links
  people already have.
* **Owner controls** — `/admin/community`: `public_board_post` and
  `public_uploads` switches (both default **on**), pin / sorted out / hide /
  restore for notes, hide / restore for files, delete only after the owner types
  their own password again. Every action writes an audit row.
* **Bounded search** — one process-wide pool of 6, `MAX_COLLECTIONS = 12`,
  `SEARCH_DEADLINE = 6.0 s`; `tests/test_search.py` holds a slow source and
  asserts the page returns inside the budget.
* **Scripts** — `65-admin.sh` proves the Community page answers only behind the
  sign-in on `:8443`; `90-verify.sh` additionally reports the Community page and
  the File Box folders. Both `bash -n` clean.
* **Docs changed in the same commit** — `README.md`, `docs/ADMIN.md`,
  `docs/USING-THE-ARK.md`, `docs/REBUILD-ME-ARK.txt`,
  `docs/REBUILD-FROM-BACKUP.txt`, `.clinerules`.
* **Next executable step** — stage 4: truthful readiness and health.
  Still open from stage 3: per-person quotas, a quarantine step between upload
  and download, and a trash instead of for-ever delete.

---

## 9. Stage 4 checkpoint — 24 Oct 2026

Shipped in **`58d504b`** on `feature/admin-hardening`.

`pytest` **97 passed** (10 files, breakdown in §6 — `tests/test_health.py` is the
new 13). Branch `feature/admin-hardening`, nothing deployed, no hardware test and
no browser test: a code-and-tests checkpoint, exactly like stage 3.

**The defect.** `/api/status` answered a bare `ready: true` per section whenever a
*file* existed. A box whose library reader had died reported the library as ready;
`/api/health` answered `{"ok": true}` unconditionally, so it could never be
evidence of anything. Download progress was the current collection's percentage
only, with no rate, no remaining bytes and no whole-queue figure.

**New:** `pi/api/ark_health.py` — the one place that decides.

| Piece | Behaviour |
|---|---|
| path helpers | `ark_root()`, `zim_dir()`, `library_xml()`, `status_file()`, `calibre_db()`, `vault_dir()`, read at call time from `ARK_ROOT` / `ZIM_DIR`, so `main.py` no longer freezes paths at import |
| `library_books()`, `library_bytes()`, `book_count()`, `guide_pages()` | content facts, each tolerant of a missing or corrupt file |
| `answers(url)` / `forget_probes()` | loopback probe, any HTTP status counts, cached 10 s, 0.8 s timeout (`ARK_PROBE_TIMEOUT`) |
| seven section builders | `library`, `books`, `manuals`, `messages`, `files`, `storage`, `downloads` — each returns `ready`, `reason`, `action`, `content`, `facts` |
| `progress()` | measured bytes: `current_percent`, `overall_percent`, `remaining_bytes`, `rate_bps`, `eta_seconds`, `stalled`, `reported` |
| `report()` | `ok`, `ready`/`total`, `blocking` (labels), `checked`, `sections` |

**Routes.** `/api/ready` → 200 when `ok`, else **503**, same body either way.
`/api/health` → liveness only, with `scope: "backend-process"`, a note pointing at
`/api/ready`, and the real verdict riding along as `ready` (never hidden, never
dressed up as health). `/api/status` → unchanged counts plus per-section
`ready`/`reason` and a `download_reason`; `downloads` gains the full measured set.
The public reading path is untouched: no page depends on any of this.

**Owner page.** `/admin/health` ("What is working" in the navigation): one row per
part, `Ready` / `Not ready`, the reason, and **What to do:** when there is
something to do; probes are asked fresh on every load; the page says in its own
words that it never starts, stops or deletes anything. The overview links to it
and carries the one-line verdict. No banned word appears on it — the test asserts
that.

**Progress.** `ark-zim-download` now measures its own rate (byte delta over
elapsed time, 0 when it is not moving) and writes `current_rate_bps` next to the
byte counters it already wrote. Nothing in the pipeline invents a number: with no
total there is no percentage, with no measured rate there is no countdown.

**Front page.** The strip shows the current collection's percentage and bytes, the
whole-queue percentage, and an ETA only when one was measured; the dot is green
(working), **amber** (a part is still empty) or **red** (a part exists and cannot
be served), always followed by the plain reason. `plain()` strips markup from
anything the API sends before it reaches `innerHTML`.

**Scripts.** `60-portal.sh` syntax-checks the new module and calls `/api/ready`
after `/api/status` (200 ok; 503 warns with the blocking parts named).
`90-verify.sh` prints a readiness block (ready / empty / broken per part) and puts
`ready` into `ark_status.json`. Both `bash -n` clean; neither has run on the Pi.

**Tests** (`tests/test_health.py`, 13): a real loopback `http.server` stands in
for the reader and the shelf, so "answering" and "refused" are two states a test
can produce — the reason the old green dot could lie. Covered: empty-box
reasons and actions; content present but the reader silent ⇒ not ready; a live
reader ⇒ ready; an index with no books; a 0-byte catalogue versus a working shelf;
a full card named as the reason; 200 finished + 100 in flight of 1000 = 30 % with
a 14 s ETA; unknown totals ⇒ `None`; downloads never block, but a queue silent
for 10 minutes is `stalled`; `/api/ready` 503 with a reason and action for every
part; `/api/health` liveness only; `/api/status` reasons identical to
`/api/ready`; the owner page's labels/fixes/no-jargon and the overview link.

**Docs changed in the same commit** — `README.md`, `docs/ADMIN.md` (§8 is the new
readiness page; the stale "no health panel" bullet is gone and a duplicated
"Not tested" block was removed), `docs/USING-THE-ARK.md` (§3.7, the download
field table, the 60-second check, troubleshooting rows), `.clinerules`.

**Not done, and not to be claimed:** pause/resume *buttons* (the `ark-zim-pause`
file still works), a storage breakdown, a reason attached to the last import,
network-loss failover, any backup/restore screen. `/api/ready` is not on the
reader's path, so it cannot slow the portal down; it has not been measured under
a load the way search was.

Also never seen: the amber/red dot in any browser, the probes against a real
`kiwix-serve` or Calibre-Web, and a real download's measured rate. The
`.clinerules` §8.9 list is a checklist, not decoration. And the backup folder in
`~/Downloads/Project-Ark` is still built from `v1.0.5`, so it holds none of
stages 2–4 — `docs/REBUILD-FROM-BACKUP.txt` now says so at the top.

**Next executable step** — stage 5: the network after boot. Before any
release: deploy stages 2–4 and walk through them on a phone (§8.9 of
`.clinerules`).


---

## 10. Stage 5 checkpoint — 24 Oct 2026

`pytest` **128 passed** (11 files: `tests/test_network.py` is the new 28,
`tests/test_health.py` grew from 13 to 16). Branch `feature/admin-hardening`,
nothing deployed, no hardware test: the same kind of code-and-tests checkpoint as
stages 3 and 4.

**The defect.** `ark-boot-guard` decided the network once, twenty seconds after a
cold start. A router rebooted at eight in the evening left the box on a home
network that no longer existed: the portal was up, nothing could reach it, and the
only cure was a reboot. The owner's own choice of network was also unrecorded, so
the guard's opinion and the owner's could not be told apart, and the storage row
on **What is working** said only "30 GB free" — no answer to *what is using it*.

**New:** `pi/bin/ark-net-watch` (+ `ark-net-watch.service` oneshot,
`ark-net-watch.timer` `OnBootSec=3min` / `OnUnitActiveSec=60s`).

| Piece | Behaviour |
|---|---|
| `decide()` | pure: mode, any-link, hotspot, home-in-range, clients, pinned, lost streak, home streak, home-blocked → `stay` / `to-hotspot` / `to-home` |
| `--decide …` | prints that answer alone — no radio, no root, no NetworkManager, so the table is a test |
| `--dry-run` | prints `mode=… link=… hotspot=… in-range=… clients=… pinned=… lost=… back=… -> …`, writes nothing (the observer only reads) |
| observation | `nmcli connection show --active` (any wireless/ethernet profile counts, not only the saved name), `nmcli -g 802-11-wireless.ssid`, `nmcli device wifi list`, `iw … station dump` |
| state | `/srv/ark/logs/net-watch.state` — `lost_streak`, `home_streak`, `home_block_until`, `updated` |
| log | `/srv/ark/logs/net-watch.log` — one timestamped line per decision that acted, plus what happened next; a quiet check writes nothing |
| acting | `ark-mode hotspot --auto` (no pin) or `ark-mode auto`; a failed return blocks retries for `HOME_RETRY_SECONDS=1800` |

**`ark-mode` pins.** `home` and `hotspot` write `/etc/ark/mode.pinned`; `auto`
removes it; `hotspot --auto` is the rescue path and leaves no pin. `status` gained
a line — `chosen by : owner (home)` or `chosen by : auto` — so the one question
that used to need a guess ("did the box decide this, or did I?") is answered by the
box. `test 180` still pins for the duration and unpins when it goes back to auto,
so the watchdog cannot cut a deliberate test short.

**Storage breakdown** (D22). `ark_health.storage_breakdown()` measures Offline
Library, Books, Manuals & Guides, File Box and Settings and records; only non-empty
folders are listed; `card_facts()` reports the whole card from `shutil.disk_usage()`
and `None` when it cannot ask. `ark_admin._storage_breakdown()` renders it under
the storage row of **What is working** with a measured total and the card's own
figure; nothing is estimated and no empty folder is dressed up as "0 B". The same
numbers ride along in `/api/status` → `sections[storage].facts`.

**Scripts.** `70-network.sh` installs the binary and both units, enables and starts
the timer, then runs `--dry-run` and echoes what the watchdog sees *without touching
the radio*; its verify block warns when the timer is not enabled or not running, and
its closing line names the phase as "at boot and afterwards". `90-verify.sh` adds
`ark-net-watch.timer` to the enabled+active list, prints a `held on "home" by the
owner` line when a pin exists, and prints the last watchdog decision (or says the
first check is three minutes after boot). Both `bash -n` clean.

**Tests** (`tests/test_network.py`, 28). The decision table is 14 cases, including
*one lost check is a blink*, *two is a network*, *somebody is reading ⇒ stay*,
*`-1` clients ⇒ stay*, *the owner pinned the hotspot ⇒ stay*, *a return already
failed ⇒ stay*, and *a pin never leaves the box dark*. Then the observer against a
fake `nmcli`/`iw`: a healthy home left alone; a lost network counted and then
rescued; a hotspot with a station never closed; an uncountable hotspot treated as
occupied; the return home after `RETURN_STREAK` quiet checks; a dry run writing
nothing (state byte-identical, no log file); a quiet real run writing nothing; a
lost network really calling `hotspot --auto` and recording the call, the log line
and the state; the pin contract in `ark-mode`; the units; and the two phases that
install and verify them. `tests/test_health.py` adds the measured breakdown, the
empty-folder omission and the owner page's `4.0 kB` line.

**Live smoke check** (macOS, uvicorn on `127.0.0.1:8137`, throwaway `ARK_ROOT`, a
4096-byte collection and a 700-byte guide):

```
sections[storage].facts.breakdown = [Offline Library 4096, Manuals & Guides 700,
                                     Settings and records 106507]
breakdown_bytes = 111303       card_total_bytes = 245107195904
card_used_bytes = 212101849088  free_bytes = 33005346816
ark-net-watch --dry-run (no nmcli on this Mac):
  mode=unknown link=0 hotspot=0 in-range=0 clients=-1 pinned=0 lost=1 back=0 -> to-hotspot
```

The dry run is the point: with no radio at all the watchdog still reaches a
decision, and it is the safe one.

**Docs changed in the same commit** — `README.md`, `docs/ADMIN.md` (§8 gains the
breakdown; §9 no longer claims there is none), `docs/USING-THE-ARK.md` (the
troubleshooting rows and the cheat sheet), `docs/REBUILD-ME-ARK.txt`, `.clinerules`.

**Never run on the Pi — say so, do not imply otherwise.** The timer has never been
enabled on the box; `iw … station dump` has never been asked a real access point for
its station list; no home network has been switched off to watch the hotspot come
up; no pin has been set by a human; `70-network.sh`'s new block and `90-verify.sh`'s
network lines have been `bash -n`'d and nothing more. The 3-minute first check and
the 30-minute back-off have never elapsed in real time.

**Still absent, deliberately:** any network control in the owner's web page, a
client list ("who is on the hotspot"), signal strength, a warning before the return
home, and a choice of hotspot channel from the page.

**Next executable step** — stage 6 (§7.1): the backup and the offline recovery
sheet. Before any release: deploy stages 2–5 and walk through them on a phone
(`.clinerules` §8.9).


