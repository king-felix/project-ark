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
| Requirements matrix | `docs/REQUIREMENTS-MATRIX.md` — R1–R8/A–D, each status with the test that proves it and the sentences still missing |

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
| 11 | Backup / offline recovery kit | **stage 6 done** — `ark-backup` writes one dated, verified bundle (records, shared files, import list, a manifest with a sha256 per file) built as `.partial` and published by rename; `ark-restore` proves a bundle before touching anything, refuses an occupied box without `--force`, and never replaces a shared file; the printed recovery sheet needs only a phone, a card reader and a working card (§11) |
| 12 | Emergency content review | not started |
| 13 | Essentials pack, QR, accessibility, household page | not started |
| — | ARK Arcade | **delivered and tested** — public shelf, guest play, optional named-player saves on the TLS `:8445` origin, owner **Games** page, licence/allowlist/hash gate, weekly discovery, and saves carried by a backup (§12) |
| 14 | Sequence | stages 1–6 and ARK Arcade done; deploy + hardware walk-through outstanding; stage 7 next |
| 15 | Acceptance tests | 231 tests, all green (`pytest -q`); the per-requirement verdict is `docs/REQUIREMENTS-MATRIX.md` |

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

**D23 — a backup is a bundle, not a folder copy.** `ark-backup` takes the
database with SQLite's own `.backup` (the live file is WAL, so `cp` can miss
committed data), writes a sha256 and size for every file into `manifest.json`,
and builds the whole folder as `<name>.partial` before publishing it by rename.
`ark-restore` proves every byte before touching the box, refuses an occupied box
without `--force`, and never replaces a shared file. The tool writes only where
it is told; a copy left on the same card is still not insurance, and the
recovery sheet says that in plain words.

**D24 — the game shelf is content with a trust boundary.** The player is
vendored, so a game page has no `http://` or `https://` asset reference and
works with the internet unplugged. Player pages live on their own TLS origin
(`:8445`); nginx sets `X-ARK-Player-Origin: 1` only there and **blanks** it on
the public `:80` location. The player's tables and cookie are separate from
administrators, `/admin` is `404` on the player origin, and a player cookie
cannot sign in to the owner's door. A candidate's address is rebuilt from a
validated slug and filename, never accepted as a URL.

**D25 — a player name is not an administrator, and a save is bounded.** A guest
can play without a name; a name only keeps saves. Saves are capped, written
`0600` inside `0700` folders by `player_save_put()`, and a stale tab gets `409`
rather than overwriting a newer save. Export is one zip; import accepts only
`saves/<game>/<state-NN.state|battery.srm>`, requires the game on the shelf, and
skips anything that climbs. A backup carries named players' saves as state; the
games themselves are content and are fetched again.

---

## 6. Test evidence

```
$ .venv/bin/python -m pytest -o addopts='--strict-markers' -q
........................................................................ [ 41%]
........................................................................ [ 82%]
..............................                                           [100%]
174 passed in 27.00s
```

(`pytest.ini` already passes `-q`, so a second `-q` hides the summary line; the
command above is the one that prints the count.)

Breakdown: `test_network` 28 · `test_health` 19 · `test_arcade` 18 ·
`test_board` 15 · `test_auth` 15 · `test_backup` 14 · `test_accounts` 13 ·
`test_arcade_install` 11 · `test_storage` 11 · `test_import` 7 · `test_public` 7 ·
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

Stages 3, 4, 5 and 6 and ARK Arcade are closed. What comes next (each with a
test before the code):

1. **Deploy stages 2–6 and ARK Arcade, then walk through them on a phone** —
   copy the database aside first, run
   `--only 20-storage,30-calibre,60-portal,65-admin,70-network,95-backup`, then
   `--only 90-verify`. `.clinerules` §8.9 is the checklist and it has not been
   done. No tag before this passes.
2. **Stage 7 — the household layer.** Emergency content review, the essentials
   pack, QR cards, accessibility and the household page.

### 7.1 Stage 6 in detail — delivered, see §11 for what was built

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

**Next executable step at that checkpoint** — stage 6: the backup and recovery
sheet, delivered in §11. ARK Arcade followed in §12; stage 7 is still the next
new work after the deploy walk-through (`.clinerules` §8.9).


---

## 11. Stage 6 checkpoint — 24 Oct 2026

At this checkpoint: `pytest` **144 passed** (12 files: `tests/test_backup.py` was
the new 13, `tests/test_health.py` grew from 16 to 19). Branch
`feature/admin-hardening`, nothing deployed, no hardware test: the same
code-and-tests checkpoint as stages 3, 4 and 5. The suite stood at **174 passed**
after ARK Arcade; it is **231** now (§1), and `docs/REQUIREMENTS-MATRIX.md` is the
per-requirement verdict.

**The gap.** Everything the box cannot rebuild — messages, shared files, accounts,
the two switches, the list that stops a re-dropped book being imported twice —
lived in exactly one place: the SD card it was running from. The rebuild
documents assumed a card that still mounts; if it did not, there was no bundle to
put back and no page that worked without the box. And a folder called "backup"
that nobody had verified is a hope, not a backup.

**New files**

| File | Behaviour |
|---|---|
| `pi/bin/ark_bundle.py` | the one definition both tools share: bundle layout, manifest (format 1 JSON), `safe_rel()` / `inside()` path rules, sha256 per file, `verify_bundle()`, `copy_database()` via SQLite's own `backup()` then `PRAGMA journal_mode=DELETE` (never `cp` — the live database is WAL) |
| `pi/bin/ark-backup` | `--to <folder>` (default `/srv/ark/backup`), `--keep 2`, `--no-files`, `--dry-run`, `--list`, `--verify <folder>`, `--quiet`. Builds `ark-backup-<date>-<time>.partial`, writes the manifest last, publishes by rename; sweeps stale `.partial` folders; `flock`s the destination so two runs cannot interleave; records message/file counts, `SCHEMA_VERSION` and `git describe` in the manifest |
| `pi/bin/ark-restore` | `--from <folder>`, `--list`, `--dry-run`, `--force`, `--no-files`, `--ledger auto\|yes\|no`, `--keep-owner`. Verifies every byte *before* touching the box; refuses an occupied box without `--force` and names the flag; never replaces a shared file that is already there; leaves the import list beside an empty shelf as `.imported.json.from-backup`; refuses any manifest entry that climbs out of the bundle or the box; fixes ownership with a missing-user fallback |
| `pi/RECOVERY-SHEET.txt` (symlinked at `docs/RECOVERY-SHEET.txt`) | one printable page, under 90 lines, no words that need looking up — dead box → working box with a phone, a card reader and a working card. Every bundle carries its own copy as `RESTORE-ME.txt` |
| `pi/steps/95-backup.sh` | phase 8: installs both tools in `/usr/local/sbin` (`ark_bundle.py` beside them, so the two can never disagree about what a bundle is), creates `/srv/ark/backup` `0700 pi:pi`, installs the sheet at `/srv/ark/RECOVERY-SHEET.txt` and `/srv/ark/portal/recovery.txt`, proves `--dry-run` plans a run, and prints the four commands the owner needs. **No timer** — a nightly backup that quietly fills the card the box reads from is a defect, not a feature |
| `tests/test_backup.py` | 13 tests at this checkpoint (14 at the tip, after the named-player save case), throwaway paths only |

**Order of checks in `ark-restore` is the design.** Prove the bundle → ask before
replacing records → never overwrite a shared file → never let the import list
hide books. A restore is the one deliberate exception to "a backup only reads",
and the tool says so in its own header.

**Readiness grew a row.** `ark_health.backup_section()` adds **Latest copy** to
**What is working** and to `/api/status`: it counts the finished bundles on the
card (a `.partial` folder is not one), reports the newest one's age and what it
holds, and turns *Not ready* only when there is no copy at all or the newest is
older than `COPY_STALE_HOURS = 168`. It is not in `BLOCKING_SECTIONS`: a missing
copy must never make a box that is serving pages look broken. An unreadable
manifest is skipped rather than guessed at, and an age it cannot read renders as
"at a time the box could not read", never as `0`.

**Verifier.** `90-verify.sh` reports the two tools, the newest copy (name and age,
soft when there is none or it is over a week old), how many are kept, and whether
the printed sheet is on the card; `ark_status.json` gains
`"backup": {"copies": …, "newest": …, "made": …}`. `install-ark.sh`'s `ALL_STEPS`
now runs `95-backup` before `90-verify`.

**Tests** (`tests/test_backup.py`, 13 at this checkpoint; 14 at the tip). The records, the shared files and the
import list arrive in one bundle; a damaged copy is refused rather than restored;
a half-written `.partial` is swept and never listed; only the newest `--keep`
bundles survive; a restore puts everything back in the same absolute paths; an
occupied box is not overwritten without `--force`; a shared file already on the
box is never replaced; the import list stays beside an empty shelf; a manifest
pointing outside the box is refused; a dry run writes nothing; the recovery sheet
is printable and free of words that need looking up; every bundle carries it; and
the phase installs both tools and proves a dry run. `tests/test_health.py` adds
the never-copied case, the age and counts wording (including a nine-day-old copy
turning amber), and the `.partial` that is not counted.

**Seen working on a Mac, never on the Pi.** Both tools have been driven by hand
against a throwaway box in `/tmp` (a box built with the real schema, WAL and all):
`--dry-run` wrote nothing and named the bundle it would build; a real run wrote
`ark-backup-20261024-200527` (4 files, 23.3 kB) and no `.partial` was left behind;
`--verify` re-read every fingerprint and agreed; `--list` was honest about an
empty default folder; `--restore --from …` into a second empty box put the record
and the shared file back, and left `.imported.json.from-backup` beside the missing
shelf exactly as designed; a restore over the still-populated box refused without
`--force` and said so in plain words. What has **not** happened: `95-backup.sh` has
only been `bash -n`'d, no bundle has ever been written on the Pi, no restore has
ever been performed there, `/recovery.txt` has never been opened in a browser,
`90-verify.sh`'s backup block has only been syntax-checked, and the **Latest copy**
row has only ever met fixture folders in tests.

**Still absent, deliberately:** a timer; any button in the owner's page that takes
or restores a copy (both are console commands); encryption of a bundle (it holds
the accounts table, so the folder is `0700`); incremental or off-site copies.
`ark-backup` writes where it is told, and **a copy left on the same card does not
survive that card dying** — copying the folder to a stick or a computer is still
the owner's job, and the sheet says so. The offline library and the book shelf stay
out of a bundle on purpose: they are content that `ark-zim-resolve` and the
importer fetch or rebuild, not state.

**Next work after this checkpoint** — ARK Arcade (§12), then stage 7: the
household layer (emergency content review, essentials pack, QR cards,
accessibility, the household page). Before any release: deploy the whole branch
and walk through it on a phone (`.clinerules` §8.9).

---

## 12. ARK Arcade checkpoint — 24 Oct 2026

**Delivered on `feature/admin-hardening`, not deployed.** `pytest` **174 passed**
at this checkpoint (14 files; **231** now — §1). The shelf is public and
password-free; a name exists only to keep
game saves and is not an administrator account. Nothing has been played in a real
browser or fetched from the live source on the Pi.

**What was built.**

| Piece | What it owns |
|---|---|
| `pi/arcade/emulatorjs/` | EmulatorJS 4.2.3, vendored locally: loader, minified player, five cores (`gambatte`, `fceumm`, `snes9x`, `mgba`, `genesis_plus_gx`) and `SHA256SUMS`. No CDN, no external font, no telemetry |
| `pi/api/ark_db.py` | `SCHEMA_VERSION = 3`: `games`, `game_revisions`, `providers`, `acquisition_candidates`, `acquisition_jobs`, `players`, `player_sessions`, `player_saves`, `arcade_login_attempts`; migration appends, never edits v1/v2 |
| `pi/api/ark_arcade.py` | the shelf's rules: `SYSTEMS`, slug/ROM-name guards, `rom_facts`, `license_problem`, game queries, player register/auth/backoff/session lifecycle, and the whole save layer (`SaveConflict`, `SaveRefused`, atomic writes, SHA-256, export zip, bounded import) |
| `pi/api/ark_arcade_install.py` | the only download path: source allowlist, no redirects, 64 MB cap, licence/console checks, temp write + fsync, SHA-256/size/`rom_facts`, `os.replace`, one DB transaction; discovery, seed, owner decisions, auto-install (off unless the owner says yes), temp sweep |
| `pi/api/ark_arcade_web.py` | public shelf, game page, ROM and emulator files, player sign-in/registration/saves, save API, export/import. Player pages redirect to the TLS origin; the API refuses a request that did not come through it |
| `pi/bin/ark-arcade` | `--seed`, `--discover`, `--auto`, `--install`, `--refuse`, `--allow`, `--list`, `--status`, `--dry-run` |
| `pi/nginx/ark-players.conf` | `:8445` TLS-only player origin; sets `X-ARK-Player-Origin: 1`, serves only `/arcade/` and the stylesheet, answers `404` for `/admin` |
| `pi/nginx/ark.conf` | public `/arcade/` proxy; **blanks** `X-ARK-Player-Origin`, so a visitor cannot claim the secure origin; 20 MiB body limit on that path |
| `pi/systemd/ark-arcade-discover.*` | weekly discovery 15 minutes after boot; tries the pinned seed first (failure non-fatal), then records what the source offers; installation still requires the owner's switch |
| `ark_admin.py` **Games** | shelf list with licence/state, hide/show (reversible, deletes nothing), suggestions with **Check and add** / **Put away** / **Allow again**, name switch, auto-install switch, every action audited |
| `ark-backup` / `ark-restore` | now carry `arcade/saves/…` (state, not content): missing saves are added `0600` in `0700` folders and an existing save is never replaced |
| `pi/steps/60-portal.sh` / `65-admin.sh` / `90-verify.sh` | install the modules, binary, data folders and first shelf; install/prove the `:8445` origin; report games, names, saves and the player origin |

**The gate chain, in order.** Licence stated by the entry must allow copying →
address rebuilt from a validated slug and filename (an entry cannot supply a URL)
→ prefix allowlist with redirects refused and a 64 MB cap → streamed into
`/srv/ark/arcade/tmp` with fsync → SHA-256, size and console-header checks →
`os.replace` into `/srv/ark/arcade/games` → rows in one transaction, with the new
file unlinked if the database refuses it. A failure leaves the shelf exactly as
it was.

**The boundaries tested.**

* a guest gets 401 from every save endpoint and a game page with no external
  address;
* a named player's save round-trips, a stale tab gets 409, another name gets 404,
  a missing page token gets 403;
* export → import returns the bytes; a zip entry that climbs is skipped and no
  file appears outside `saves/<player>/<game>/`;
* a hidden game has no page and no ROM; `/arcade/emulator/` refuses `../`;
* licence, allowlist, redirect, archive, traversal and renamed-web-page each
  refuse, and a refused install leaves no row and no file;
* the pinned seed dry run touches nothing; auto-install is off unless the owner's
  setting is `1`; the owner can hide/show a game and close player names;
* a player cookie does not sign in to `/admin`.

**Seen on the Mac, never on the Pi.** The three pinned seed files were fetched
again on 24 Oct and every SHA-256 matched the one in `seed.json`; a real
`--seed` into a throwaway box installed all three and `--status` listed them. The
live discovery source was asked once: eight entries recorded, two offered
(Waternet, MIT; Max Pirate Extra Boom, MIT), six refused for no licence or
CC-BY-NC-ND, and Waternet was then installed into that throwaway box (32,768
bytes, SHA-256 recorded in the row and the revision). The rendered shelf and game
page carried no `http://` or `https://` reference, and the ROM came back at 200.

**Said out loud.** No game has been played on the Pi; `:8445` and its certificate
have only been configured; the live source has never been asked for a game from
the box; the weekly timer has never fired there. A named player has no password
recovery, no save quota, no trash, and the owner cannot reset or delete a player
name. The games are not part of a backup bundle (content, fetched again); the
named players' saves are. Before release: deploy the whole branch with
`--only 20-storage,30-calibre,60-portal,65-admin,70-network,95-backup`, then
`--only 90-verify`; play one game on a phone with the internet unplugged, make a
name, save, export, restore into a second browser, and watch the weekly timer's
first run.

**Next executable step** — deploy the branch on the Pi and walk the paragraph
above (`.clinerules` §8.9); stage 7 (household layer) remains unstarted.

---

## 13. Hardening + documentation checkpoint — 25 Oct 2026

**Committed on `feature/admin-hardening`, not deployed.** `pytest` **231 passed**
(15 test files). This checkpoint records the code that was finished after §12 and
the documentation pass that followed it. No migration or feature in this section
has run on the Pi.

| Area | What changed |
|---|---|
| First sign-in | The public `ChangeMe-ARK!` literal is gone. `ark_security.bootstrap_password()` generates a 20-character password once into `/srv/ark/api/data/bootstrap-password` (`0600`, `O_EXCL`, fsync) and returns it idempotently. `ARK_BOOTSTRAP_PASSWORD` remains only as the test/automation hook. The setup page names the file. A wiped database plus a surviving `ark.installed` marker still becomes `recovery-required` |
| Sign-in timing | Administrator and player sign-in verify against `DUMMY_HASH` when the name does not exist, so an unknown name costs the same scrypt work as a real one. Password policy is unchanged: 12…1024 characters, rejected not truncated |
| Schema v4 | `SCHEMA_VERSION = 4`. Adds `player_game_prefs` (`autosave`, `favourite`, `last_played`) and the evidence columns `language`, `language_evidence`, `rights_evidence`, `provenance`, `release`, `notices` on games/candidates/revisions. Existing candidates with no language or no rights evidence become `review` once. Old rows keep reading through defaults |
| Arcade eligibility | `language_facts()`, `rights_facts()` and `review_reason()` are one gate: English or wordless with evidence; an unrecorded language, a conflict or missing evidence is held for review with a plain reason. Owner-supplied files go through `owner_candidate()`/`import_owner_file()` and the same proof; ownership is not permission |
| Arcade player choices | `published_games(conn, player_id)` puts favourites first for a named player and A–Z for a guest. Preferences follow the name across devices; they never hide a game and guests write nothing |
| Restore | `ark-restore` quiesces every writer, restarts what it stopped in `finally`, supports `--no-service` for a reader-mounted card, and uses `replace=True` only under `--force` for records and named-player saves. It still never replaces an existing shared file |
| Backup | Manifest format 1 remains the definition; shared files may be compressed in the bundle and are proved/restored byte-for-byte. Vendor/emulator payload handling is explicit, not a path guess |
| TLS proof | `tests/test_tls_probe.py` drives `ark_plain_http_verdict` against real sockets and a real TLS listener, and asserts the installer uses the verdict rather than `curl`'s exit status |
| Docs | `README.md`, `docs/ADMIN.md`, `docs/USING-THE-ARK.md`, `docs/REBUILD-ME-ARK.txt`, `docs/REBUILD-FROM-BACKUP.txt`, `docs/REQUIREMENTS-MATRIX.md`, `pi/RECOVERY-SHEET.txt` and `.clinerules` now describe the bootstrap-password file, schema v4, language/review gate, player preferences, restore quiesce and the pending deploy |

**Still pending — say it plainly.** Nothing here has met the Pi: no v4 migration,
no generated bootstrap password read on the box, no restore with systemd, no
arcade language/review decision against the live catalogue, no player preference
kept on a phone. Before release: copy the database aside with SQLite `.backup`,
deploy with `--only 20-storage,30-calibre,60-portal,65-admin,70-network,95-backup`,
run `--only 90-verify`, then complete the phone walk-through in `.clinerules`
§8.9.


