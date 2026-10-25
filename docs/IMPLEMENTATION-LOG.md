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
| Deployment status | **deployed on the box 25 Oct 2026** — stages 2–6, ARK Arcade, the 25 Oct hardening, R3's book door and the owner-import fix are live; `90-verify` green, `/api/ready` 200, database at layout **v4** (§15, §18.4). Rollback: `/root/ark-snapshot-20261025-092106`; the owner-import redeploy changed no schema and took its own copy at `/root/ark-db-before-owner-import.db` |
| Hardware tests | **scripted proofs only** — the installer, the verifier, the doors, a bundle, `/api/ready`, the arcade origin and `ark-arcade --import` have all answered on the Pi. **No human has used it on a phone yet**, no game has been played by a person, no 2 GiB upload has crossed the proxy, and the watchdog has never seen a real radio event |
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
| 14 | Sequence | stages 1–6, ARK Arcade and R3 **deployed 25 Oct 2026** (§15); the phone walk-through is the next human step; stage 7 after it |
| 15 | Acceptance tests | **267 tests**, all green (`pytest -q`); the per-requirement verdict is `docs/REQUIREMENTS-MATRIX.md` |

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

Stages 3, 4, 5, 6, ARK Arcade and R3 are closed, and the branch went to the Pi
on 25 Oct 2026 (§15). What comes next:

1. **Walk through it on a phone** — the deploy and `90-verify` are green, but no
   human has tapped anything. `.clinerules` §8.9 item 2 is the checklist and it has
   not been done. No tag before it passes.
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

**Delivered on `feature/admin-hardening`, deployed 25 Oct 2026 (§15).** `pytest` **174 passed**
at this checkpoint (14 files; **240** now — §1). The shelf is public and
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

**Next executable step** — the branch was deployed on 25 Oct 2026 (§15), so what
remains is the phone walk-through in the paragraph above (`.clinerules` §8.9 item
2); stage 7 (household layer) remains unstarted.

---

## 13. Hardening + documentation checkpoint — 25 Oct 2026

**Committed on `feature/admin-hardening`, deployed 25 Oct 2026 (§15).** `pytest` **231 passed**
(15 test files) at this checkpoint; **240** now. This checkpoint records the code
that was finished after §12 and the documentation pass that followed it. The whole
of it — including the **v4 migration**, which ran in place on the live database — went
to the Pi on 25 Oct 2026.

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

---

## 14. R3 — the book shelf is a reading door — 25 Oct 2026

**Committed on `feature/admin-hardening`; `pytest` 237 passed (15 files).** The
last NOT STARTED blocking requirement in `docs/REQUIREMENTS-MATRIX.md` is closed
in code, in test, and — for the first time in this repo — **observed on the box**.

| Piece | What changed |
|---|---|
| Route inventory | The installed Calibre-Web 0.6.27 `url_map` was read out of `/srv/ark/venv/lib/python3.13/site-packages/calibreweb/cps/*.py` on the Pi (231 rules, extracted with `ast`, no import of the package) and transcribed into `tests/test_config.py` as `MANAGEMENT_GET`, `MUTATING_POST` and `READING` |
| `pi/nginx/ark.conf` | Two doors on `/books`: a `location ~ ^/books/(?:admin\|…)(?=/|$)` that answers **404** for the management, account and admin-`/ajax` routes (a method rule cannot stop a GET), and `limit_except GET { deny all; }` on the reading door for everything else (every mutating Calibre-Web route uses POST/PUT/DELETE). `/books/ajax/listbooks` and `/books/ajax/book/<uuid>` stay open because the table view pages with them |
| The one exception | `/books/advsearch` is a form POST that only reads, so it has its own location without the method rule. It is the only relaxation, and a test keeps it that way |
| `pi/steps/60-portal.sh` | Nine `must_be <code> <method> <url>` proofs after the page checks: `/books/admin` 404, `/books/upload` 404 (GET and POST), `POST /books/login` 404, `/books/ajax/listusers` 404, `POST /books/shelf/create` 404, `POST /books/book/1` 403, `POST /books/delete/1` 404, `/books/advsearch` 200 |
| `tests/test_config.py` | 6 new tests drive the **shipped** file: a small nginx `location` parser pulls the two doors out of `ark.conf`, the 404 regex is compiled and run against the transcribed inventory, and the importer is asserted to talk to `127.0.0.1:8083` (which is what makes refusing `/upload` and `/login` safe) |

**Hardware evidence, 25 Oct 2026** — the shipped `ark.conf` was copied to the Pi,
its `listen` rewritten to `127.0.0.1:18080`, and a **second** nginx started from
that file beside the live one. Every probe answered as designed:

```
OK GET  /                         200      OK POST /books/shelf/create   404
OK GET  /books/                   200      OK POST /books/book/1         403
OK GET  /books/admin              404      OK POST /books/delete/1       404
OK GET  /books/upload             404      OK GET  /books/advsearch      200
OK POST /books/upload             404      OK GET  /books/opds           200
OK POST /books/login              404      OK GET  /books/static/js/…    200
OK GET  /books/ajax/listusers     404      OK GET  /books/search?q=water 200
```

`/books/ajax/listbooks` answered **302** — Flux's own redirect — which is proof it
was *proxied*, not proof of a reading page; the JS table asks for it with a
session, so a bare curl is not the right probe. The live listener was not
reloaded and no live service was restarted: these answers came from the throwaway
instance on `:18080`, which was stopped and removed afterwards.

**What is still true:** the new `/books` door is **live** — the deploy ran
`60-portal`'s `must_be` proofs against the real listener (log §15) — but nobody has
tapped a book through it in a browser; and the route inventory is a snapshot of 0.6.27, so a package upgrade means
re-reading it (`.clinerules` §8.9, move 5).


## 15. The branch is deployed to the box — 25 Oct 2026

**`pytest` 240 passed (15 files) at that checkpoint — 267 at the tip.** Stages 2–6, ARK
Arcade, the 25 Oct hardening pass and R3's book door now run on the Pi. This is the first
time any of it has been on hardware; the section records what the box said, what that
proved, the two defects the deploy found in its own probes, and what is still unproven.

**Rollback first.** `/root/ark-snapshot-20261025-092106` (pointer in
`/root/ark-snapshot.latest`): `ark.db` taken with SQLite `.backup` (the live file
is WAL, so `cp` can miss committed pages), `api-data.tgz`, `etc-ark.tgz`, the old
`/etc/nginx`, and the old `/srv/ark/src`. The deploy migrates the database to
layout **v4** in place and there is no downgrade path, so that folder is the only
way back.

**What ran:** `scripts/03-provision.sh ark.local --only
20-storage,30-calibre,60-portal,65-admin,70-network,95-backup`, then
`--only 90-verify`. Both finished `ARK_INSTALL_COMPLETE`.

| Proof | Where it came from |
|---|---|
| 20 collections (86.5 GiB) + 20 titles indexed, 1290 books, 11 guides, 121.6 GiB free | `90-verify` |
| Every public page 200 (home, how-to, board, File Box, shelf, game page, live status, manuals, cards, library, book shelf), captive trigger 302 | `90-verify` |
| `/admin` **404** on `:80`; `:8443` **303**; `:8444` **303**; Community and Games behind the sign-in **303** | `90-verify` |
| Management origin hides `/kiwix/`, `/books/`, `/drop/`, `/vault/`, `/` (all 404); plain HTTP to `:8443`/`:8445` rejected 400 | `65-admin` |
| `:8445` answers 200, hides `/admin` 404, public shelf sends a name to `:8445` with **307** | `65-admin` |
| eleven units `enabled` + `active` incl. `ark-net-watch.timer`, `ark-book-import.path`, `ark-arcade-discover.timer` | `90-verify` |
| R3's book door live: `GET /books/` 200, `GET /books/admin` 404, `POST /books/book/1` 403 | `60-portal` `must_be` proofs |
| `/api/ready` → **200**, `ready 7 / total 8`, `blocking: []` (the eighth row is *Latest copy*, never blocking) | `curl http://ark.local/api/ready` from the Mac |
| First real bundle: `ark-backup-20261025-093643`, 203.2 kB, *“is whole: 3 files … every fingerprint matches. Records were at layout version 4”* | `sudo ark-backup --to /srv/ark/backup` then `--verify` |
| `ark-restore --dry-run` on that bundle refused an occupied box — *“Nothing has been changed”* | `sudo ark-restore --dry-run --from …` |

**Two defects the deploy found in its own proofs (both fixed, both now tested).**

1. **A TLS probe must ask by name.** `65-admin.sh` proved `:8443` with
   `https://127.0.0.1:8443/admin`. An IP-literal request carries **no SNI**, nginx
   then serves the management listener's default server, and that block answers
   `ssl_reject_handshake on` — so the handshake died and curl reported `000` on a
   perfectly healthy door. The phase failed its own proof on a correct box.
   `--resolve "<host>:127.0.0.1"` (helper `ark_tls_code` in `pi/lib/common.sh`)
   makes curl send `SNI=<host>` while still connecting to loopback.
2. **…and the fix went in one place only.** Twenty minutes later `90-verify`
   reported the same `000` for `:8443`, `/admin/community` and `/admin/arcade`,
   because the verifier had its own copy of the probe. The helper now lives in
   `pi/lib/common.sh` and both steps call it; `tests/test_tls_probe.py` fails if
   either step grows another TLS probe of its own. (`:8445` has no default
   server, so it always answered — which is exactly why only the `:8443` probes
   failed, and why “it worked last time” was no evidence.)

A third, self-inflicted one is worth recording because no lint would have caught
it: a patch of mine wrote `adm_code="$(ark_tls_code …)""`. `bash -n` **passed** —
the second quote opens a string that a later line closes, so the file is
“balanced”, the assignment swallows the following lines and the verifier dies at
runtime with `adm_code: unbound variable`. `bash -n` cannot see that. There is
now a test that no line in the three shell files ends with a doubled quote, and
the lesson is in `.clinerules` §8.7.

**A finding on the box, and the fix it earned.** `/files` listed one row,
`e2e-drop-test.epub` (created 24 Oct 01:43 by the pre-spool e2e test), whose file
is not in `drop/files`: the download link answered **404**. A row can outlive its
file without anything being corrupted — `ark-restore --no-files` puts the records
back on a card whose File Box is empty, and a folder tidied by hand does the same
— so this was a page defect, not a database defect. `main._present()` now filters
the listing against the folder (and the count above it) so a reader is never
offered a link that cannot open; the row itself is kept, because the owner's
**Shared files** list is where a file that has gone is noticed and deleted, and
its audit line already reads *“record removed; the file was already gone”*.
`tests/test_storage.py::test_a_row_whose_file_has_gone_is_not_offered_to_the_reader`
is the mirror of the long-standing “file on disk without a row” test. The box's
own row was left in place for the owner to delete through the door built for it.

**Still unproven, and no honest way round it:** nobody has tapped a book through
the new door; the amber/red dot and the *What to do:* line have never been seen in
a browser; `ark-zim-download`'s measured rate has never come from a real
download; the watchdog has never seen a real NetworkManager scan or a real AP
station dump; EmulatorJS has never been played on the Pi and the live game source
has never been asked for a file from the box (**the Pi still has no route to the
internet** — the game library has to be built on the Mac and carried over); no
2 GiB upload has crossed the proxy; and no restore has ever replaced records. The
phone walk-through in `.clinerules` §8.9 item 2 is the next human step, and at
that checkpoint the owner's admin account was still unclaimed (`1 account, 1
still using a temporary password`) — claimed on 25 Oct at 14:55 UTC, §16.7.


## 16. File Box truth-telling, and who owns a copy — 25 Oct 2026

**`pytest` 248 passed (15 files)** — eight new tests (§16.4). Three defects, all
found by reading what the box said about itself after it was serving. None of the
three could be seen from the code alone: each one needed a box that had been used.

### 16.1 A link that could not open (fixed, deployed)

`/files` offered a row whose file was not in `drop/files`; the link answered
**404**. A row can outlive its file without anything being corrupted —
`ark-restore --no-files` puts the records back on a card whose File Box is empty,
and a folder tidied by hand does the same — so this was a **page** defect, not a
database defect, and the fix belongs in the pages.

* `pi/api/ark_storage.py::present_names(root=None)` is now the one place that
  answers "what is really in the shared folder?" — a set of names, or **`None`**
  when the folder cannot be read (the `None` rule, §8.7: an unreadable folder is
  not an empty one).
* `main._present()` filters the listing *and the count above it* through it, so
  the number a reader sees is the number of links that open. `tests/test_storage.py`
  mirrors the long-standing "file on disk without a row" test with
  `test_a_row_whose_file_has_gone_is_not_offered_to_the_reader`.
* The row itself is **kept**. The owner's Community page is where a file that has
  gone is noticed and deleted, and its audit line already reads *"record removed;
  the file was already gone"*. Nothing here deletes a record or a file on its own
  — that would be data loss wearing a maintenance costume.

### 16.2 The owner page counted records, not files (fixed)

`files_section()` in `ark_health` counted rows, so **What is working** could say
*"1 file shared"* while the portal was empty. Two pages disagreeing about the same
folder is exactly the stage-4 defect in a smaller place.

* The section now selects the rows, filters them through
  `ark_storage.present_names()`, and reports `files` (what a reader can open),
  `files_missing` (records whose file has gone) and `uploads` (the switch).
* Every row left over and nothing openable is **not ready**, `content: false`
  (empty, not broken) and says: *"Nothing here can be opened: 1 record is left
  over from a file that has gone."* — action: open the Community page and delete
  the leftover record. No `404`, no `row`, no `orphan`, no `SQLite` in that
  sentence (§5).
* An unreadable folder gives `files_missing: None` and the unfiltered count, never
  a zero.

### 16.3 The copy the pages could not open (fixed)

The deploy took its first bundle with `sudo ark-backup --to /srv/ark/backup`. The
bundle was whole and `--verify` proved every byte — and **Latest copy** on the
owner page said *no copy has been made yet*. Both statements were about the same
folder, and the page was wrong for a boring reason: the folder is `0700 pi:pi`,
the bundle inside it was `root:root 0700`, and the page runs as `pi`.

* `ark-backup` now hands each published bundle (and its lock file) to **the owner
  of the destination folder** — `_give_to_owner()` / `_adopt()` in
  `pi/bin/ark-backup`, root only, never fatal. Using the folder's owner rather
  than a hard-coded `pi` also means a copy written onto a USB stick belongs to
  whoever can read the stick.
* `pi/steps/95-backup.sh` hands over the bundles that **already exist**, so
  re-running phase 8 is the whole repair for copies taken before this fix.
* `ark_health._scan_copies()` now returns two lists: the copies it can read, and
  the copies it may not open. `copies_unreadable()` is the second one, and
  `backup_section()` reports it as *"There is a copy on the card, but the box's
  own pages are not allowed to open it, so how old it is cannot be checked."* —
  with `unreadable: 1` in the facts. "A copy I cannot read" is a third state and
  must never collapse into "no copy"; that is §8.7's `None` rule again.
* `90-verify.sh` now asks the same question the page asks, **as `pi`**, with
  `runuser -u "$ARK_PI_USER" -- test -r …/manifest.json`. It runs as root, which
  is exactly why it passed a bundle the page could not open; the mismatch is now
  reported softly (*"the pages cannot open … — re-run phase 95-backup"*).

### 16.4 The tests that hold all three

| Test | Proves |
|---|---|
| `test_health.py::test_the_file_box_counts_the_files_a_reader_can_open` | one row + one file → `files: 1`, `files_missing: 1`, ready, *"1 file shared"* |
| `test_health.py::test_a_file_box_with_nothing_openable_says_so_plainly` | only leftover rows → not ready, `content: false`, the plain sentence, the delete instruction, no jargon |
| `test_health.py::test_an_unreadable_folder_is_not_an_empty_one` | `present_names() → None` → unfiltered count, `files_missing: None` |
| `test_health.py::test_the_file_box_refresh_agrees_with_the_listing` | `/files` and the owner page count the same file once |
| `test_health.py::test_a_copy_the_pages_cannot_open_is_reported_not_denied` | a `000` bundle → `copies: 0`, `unreadable: 1`, "not allowed to open it", still **not** blocking |
| `test_backup.py::test_a_bundle_made_as_root_is_handed_to_the_box` | every path in the bundle and the lock get the folder's owner |
| `test_backup.py::test_a_bundle_made_by_the_box_is_left_alone` | run as `pi`, no `chown` is attempted at all |
| `test_backup.py::test_the_phase_installs_both_tools_and_proves_a_dry_run` | phase 8 still parses, and adopts existing bundles |
| `test_backup.py::test_the_verifier_asks_whether_the_pages_can_open_the_copy` | `90-verify` asks as `pi`, so the gate can no longer pass a copy the page cannot open — and reports it softly |

### 16.5 Where the box stands

`--only 60-portal,95-backup` ran against the live box, then `--only 90-verify`.
What the box says, in its own words (`logs/verify-16.log`):

```
· File Box     Nothing here can be opened: 1 record is left over from a file that has gone.
✓ Latest copy  The newest copy was made in the last hour — 0 messages, 1 shared file, …
· copies kept  1 in /srv/ark/backup
✓ ARK_INSTALL_COMPLETE  (every phase succeeded)      failed: none
```

* `/files` now renders **“The box is empty — Add the first file…”** and the
  leftover record's name appears nowhere on the page (the row is still in the
  database, as §16.1 says it must be).
* `95-backup` printed *“handed 1 existing copy/copies to pi so the owner page can
  read them”*; `stat` now answers `pi:pi 700` for the bundle and `pi:pi 644` for
  `.lock`, where both were `root:root`. **Latest copy** reads
  `ark-backup-20261025-093643`, in `sections` and not in `blocking`.
* The verifier's new `copy readable` line is **silent**, which is the pass: it
  asked with `runuser -u pi -- test -r …/manifest.json` and got a yes. The same
  test before the hand-over answered `PAGE-CANNOT-READ` — the defect reproduced
  on the box, not only with `000` inside `tests/test_health.py`.
* `/api/ready` answers **503**, `blocking: ["File Box"]`, until that record is
  deleted. That is the rule and not a regression: an empty File Box is ready
  (nothing to serve yet, `ready: true`), but a File Box that *claims* a file it
  cannot open is not. `90-verify` renders it as a note (`content: false` → `·`)
  beside the ready rows rather than as a failure, and the action line names the
  one click that clears it. Nothing deletes it for the owner.

**Still unproven:** the *"Nothing here can be opened"* sentence has not been read
on a screen — the text above came from `/api/status`, not the page; nobody has
tapped the delete that clears it and watched `/api/ready` return to 200; and
**What is working** had never been opened at that checkpoint because the owner's
account was still unclaimed. *Since §16.7 the account is claimed and the page
answers 200 with the right title — but still not read by a human on a screen.*
The phone walk-through (`.clinerules` §8.9 item 2) is the next human step, and it
no longer needs the SSH tunnel.

### 16.6 The networks the box will remember — 25 Oct 2026

**First, the question that started this:** `The-Ark-Offline` is not on the air
because the box is not in hotspot mode. It is on the home network
(`House of Leaves-5G-1`, `192.168.1.26/24`, wifi link `home-wifi`), which is why
anything on the same LAN reaches the portal at `http://ark.local`. The hotspot
profile `ark-hotspot` still exists — SSID `The-Ark-Offline`, `mode=ap`,
`ipv4.method=shared`, `autoconnect=no` — and only `ark-mode hotspot` or
`ark-net-watch` bring it up. `autoconnect=no` is the design (`.clinerules` §8.7:
the watchdog acts only through `ark-mode`), not a defect.

**What was added.** Two networks the household also uses, `Rodclan` and
`Rodclan-fast`, now exist as NetworkManager profiles on the box:
`/etc/NetworkManager/system-connections/rodclan.nmconnection` and
`rodclan-fast.nmconnection`, both `0600 root:root`, `autoconnect=yes`,
`autoconnect-retries=0`, priorities **7** (2.4 GHz name) and **8** (5 GHz name),
so the home router's 20/10/5 still wins whenever home is in range. They were
written through **stdin** into a `0600` temp file and installed with
`install -m 600 -o root -g root`, so no password ever appeared on a command line
or in a shell history; the reload was `nmcli connection reload`. Verified by
`nmcli … connection show` (SSID, autoconnect, priority, `key-mgmt=wpa-psk`) and
by the stored secret's **length only** (10 characters) — the key itself was
never printed.

**Nothing was activated on purpose.** `nmcli connection up rodclan` would move
the radio off the network the box is being tested over and could cut the SSH
session doing the test; neither network is in range here anyway. The first real
join is still ahead of us (recorded under *not proven* below).

**The repo learned it too**, so a rebuilt card keeps them:
`WIFI_EXTRA_PROFILES` in the gitignored `staging/wifi.env` — one
`id|ssid|password|priority` per line — is rendered into `WIFI_PROFILES` by
`scripts/make-firstrun.sh` through a new `__WIFI_EXTRA_PROFILES__` placeholder in
`staging/firstrun.sh.tmpl`. The renderer refuses an extra network with an id that
is not filename-safe, an SSID over 32 characters, a password under 8 characters,
a non-numeric priority, an SSID already used by a home slot, or a character
(`"`, `$`, backtick, `\`) that could not survive the double-quoted bash string it
is substituted into. A good render was proved on this Mac: 9 850 bytes, LF only,
`bash -n` clean, `0700`, and five `WIFI_PROFILES` lines with `rodclan-fast|…|8`
and `rodclan|…|7` last. `docs/REBUILD-ME-ARK.txt` gains the field in the build
order, a line in "making copies for other people", and **trap 31** — the box
carried to a second house it had never heard of, with the no-password-on-the-
command-line way to add one to a box that is already running.

**Not proven:** neither new profile has ever been used. No join, no DHCP lease,
no portal page from `Rodclan`. The guard's negative cases were re-run on 25 Oct
2026 in one batch — uppercase id, duplicate SSID, short password, a `$`, and a
missing priority each refused with their own `✗` sentence (`rc=1`), and the good
two-network case rendered both lines with `rc=0` at `-rwx------`. That re-run is
also what caught the placement defect in §16.8. Nothing on the Pi was restarted,
and the hotspot profile was left exactly as it was.

### 16.7 The owner's account is claimed — 25 Oct 2026, 14:55 UTC

**What was done.** The first administrator (`admin`, `is_bootstrap = 1`) was
claimed through the box's own route, not around it: the temporary password from
`/srv/ark/api/data/bootstrap-password` was used to sign in at
`https://ark.lan:8444/admin/login` (the loopback listener, from the Pi itself),
which answered `303 → /admin/setup`, and `POST /admin/setup` then replaced it
with a password the owner chose and gave to the console operator. Nothing new
writes a password hash: `ark_admin.setup_submit()` did it, exactly as it would
for a person with a browser and a tunnel.

**Evidence, as measured.**

| Step | Answer |
|---|---|
| `GET /admin/login` on `:8444` | 200, CSRF token issued (32 chars) |
| `POST /admin/login` with the temporary password | **303 → /admin/setup** |
| `GET /admin/setup` | 200 (*Choose your own password*) |
| `POST /admin/setup` | **303 → /admin** |
| fresh session, `POST /admin/login` with the new password | **303 → /admin** (no forced setup) |
| `GET /admin/health` with that session | 200, *What is working — Ark administrator* |
| `GET /admin` with that session | 200, *Overview — Ark administrator* |
| `POST /admin/login` with the **temporary** password | **401 refused** |
| `SELECT username, must_change, password_changed_at FROM admins` | `admin`, `must_change = 0`, `password_changed_at 2026-10-25T14:55:06+00:00` |
| `audit_log` ids 3–6 | `sign-in ok` → `password-setup ok` (*temporary password replaced*) → `sign-in ok` → `sign-in denied` |

**How the secrets travelled.** The temporary password was copied into a `0600`
file on the Pi by a pipe (`sudo cat … | tr -d '\n'`), the new one arrived over
SSH **stdin** into another `0600` file, and `curl` read both with
`--data-urlencode name@file`. No password appeared in an argument list, in `ps`,
or in a shell history, locally or on the box. Both files and every cookie jar
and response page were deleted afterwards; the length of each was printed
(`20` and `15` bytes) and nothing else about them was. **The password itself is
not written in this repository** and must never be: the box stores only the
scrypt hash.

**What this changes.** `.clinerules` §8.9 item 4 said the account was unclaimed
and that **What is working** had never been opened; the account is now claimed
and the page has been *fetched* (200, correct title) — but still not read on a
screen by a human. The phone walk-through is now possible without an SSH tunnel:
`https://ark.lan:8443/admin` on the phone signs in directly. `/api/ready` is
unchanged at **503 `blocking: ["File Box"]`** until the ghost
`e2e-drop-test.epub` record is deleted; claiming the account does not touch it.

**Deliberately left alone.** `/srv/ark/api/data/bootstrap-password` still exists
(`0600 pi:pi`). Its value can no longer sign in — the hash it was generated
against has been replaced — so it is inert, and it is left in place because the
setup page and `RECOVERY-SHEET.txt` name the file. Nothing was restarted, no
service was touched, and the hotspot profile was not involved.

**Not proven by this.** No human has typed the password on a phone; the forced
setup page was driven by `curl`, so its *look* is still unseen; the two
administrator sessions that exist are the operator's own and expire within 30
minutes of idleness.

### 16.8 The two networks landed in the wrong place — found and fixed 25 Oct 2026

**What the re-render showed.** With the terminal back, `bash scripts/make-firstrun.sh`
was run again (the first run had been interrupted by the wedge) and the rendered
`staging/firstrun.sh` was *read*, not just counted. Its own header read:

```
#  Placeholders: House of Leaves-5G-1 … House of Leaves perfectballoon089
#                House of Leaves-5G-2 perfectballoon089
rodclan-fast|Rodclan-fast|rodriguez6|8
rodclan|Rodclan|rodriguez6|7
#                US ssh-ed25519 …
```

`__WIFI_EXTRA_PROFILES__` had been listed **twice** in `staging/firstrun.sh.tmpl`:
once where it belongs (the tail of the `WIFI_PROFILES` string) and once as a
*scalar in the comment header* that lists the placeholders. A multi-line block
substituted into a comment is no longer a comment — its middle lines become bare
`id|ssid|psk|priority`, which bash reads as a **pipeline of commands**. On a
fresh card that would have written two `command not found` errors into
`/var/log/ark-firstrun.log` and booted anyway, because `firstrun.sh` runs under
`set +e`. Harmless in effect, wrong in principle, and invisible to `bash -n`
(syntactically valid) and to the placeholder scan (the token *was* substituted).

**The fix, in two parts.**

1. `staging/firstrun.sh.tmpl` no longer lists the block among the placeholders —
   it appears exactly once, in `WIFI_PROFILES="…|5__WIFI_EXTRA_PROFILES__"`.
   The header comment above the block already explains what it is.
2. `scripts/make-firstrun.sh` now checks **where the block landed**, not merely
   that it was substituted: it parses the rendered `WIFI_PROFILES` string and
   refuses to publish unless every line has exactly four `|`-separated fields
   (which also catches a `|` in a home SSID or password, something the builder
   never checked and which would otherwise render a profile that authenticates
   with a truncated key), the number of lines is exactly `3 + <extras>`, and each
   extra line occurs **exactly once** in the whole render. Any failure exits
   non-zero before `chmod`/`bash -n`, and names the network by id only — never
   the password.

**Evidence, as measured on the Mac.**

| What | Result |
|---|---|
| `bash -n scripts/make-firstrun.sh` | clean |
| re-render | *“rendered … (9781 bytes)”*, `✓ +x, LF-only, syntax OK`, `-rwx------`, 0 CR LF lines |
| header of the render | four `#` comment lines, then the template — no bare profile lines |
| `WIFI_PROFILES` | exactly 5 lines: `home-wifi` 20, `home-wifi-2` 10, `home-wifi-3` 5, `rodclan-fast` 8, `rodclan` 7 |
| summary numbering | `1.` … `5.`, sequential (the earlier draft repeated `4.`) |
| guard, negatives (§16.6 harness) | 5 × `rc=1` with the expected `✗`; `good-two` `rc=0` |
| guard, the regression itself | token re-inserted into a copy of the template → *“the extra network 'rodclan' appears 2 times in the render, not once inside WIFI_PROFILES”*, `rc=1` |

The 9781 characters are 9793 bytes because the template carries six em-dashes:
`len(text)` counts characters, `wc -c` counts bytes. Both are correct.

**Not proven.** No card has been flashed with this render, so no Pi has yet read
either profile; the guard is render-time only (it cannot see a hand-edited
`firstrun.sh`); and the `|`-in-a-home-password case is refused rather than
supported — a home password containing `|` now needs a different character.


## 17. A licence is information, and the shelf is full — 25 Oct 2026

The owner's instruction, twice, in as many messages: *"licences become information,
not a door — I don't even care about the licence."* That is a change to the code, not
a change of taste in titles, so it is recorded here as one.

### 17.1 What was removed, and what stayed

| Was | Is |
|---|---|
| `ark_arcade.license_problem()` refused a blank, "freeware", "all rights reserved", NC, ND or unknown licence | returns `""` for every input. The function stays so the shelf, the owner page and the tests ask one question; the licence text is still stored on the row and shown on the game's card as provenance |
| `ark_arcade_install.rights_facts()` refused a source that said nothing about the right to copy, and refused "evidence" shorter than 12 characters | always `(True, "")`. What a source says is recorded beside the game |
| `language_facts()` refused an unrecorded language and a missing evidence sentence | refuses only a language the source **records** as non-English, and a source that contradicts itself. A wordless game and a game whose language nobody recorded are both allowed |
| `review_reason()` returned the licence or the rights sentence | returns only the language sentence |
| `owner_candidate()` refused a file with no licence | keeps the licence if the owner records one; the shape and language proofs still run |

Why the language rule survived at all: it is not a rights question, it is whether
someone in the household can read the game. Most homebrew entries state no language
at all, so the old rule was refusing almost everything — 38 of 40 candidates on the
box sat in *review*, and none of them for a reason a person could act on.

### 17.2 Two real defects found by trying to build the shelf

**One source prefix for every console.** `entry_to_candidate()` built every download
address under `gbdev/database`. Game Boy and Game Boy Color entries really are there;
Game Boy Advance entries are in **`gbadev-org/games`** and NES entries in
**`nesdev-org/homebrew-db`**. Every GBA and NES candidate was therefore offered an
address that answers 404: the box could name the game and never fetch it. Now
`SOURCE_RAW_BY_SYSTEM` maps a console to the one repository that holds it, `_raw_prefix()`
takes the *resolved system*, `_trusted()` accepts exactly those three prefixes, and a
console with no repository (SNES, Genesis) yields `""` — so `entry_to_candidate()`
returns `None` instead of inventing an address.

**An entry was dropped when the search answer omitted `platform`.** The catalogue's
`/search` response sometimes leaves `platform` out, and `_system()` returned `""` for a
`.gb` file whose extension names exactly one console. Two shortlisted 2021 games
(*Dawn Will Come*, *Porklike GB*) were reported as "cannot be named". `_system()` now
falls back to `_EXT_SYSTEM`; `.bin` is deliberately absent from that table because two
consoles share the extension.


### 17.3 The shelf, as built

`pi/arcade/catalog/seed.json` now holds **23 games**: 10 Game Boy, 6 Game Boy Color,
6 Game Boy Advance, 1 NES — 46,787,812 bytes. A throwaway builder on the Mac read the
harvested catalogue, fetched each default file, refused anything the box's own
`rom_facts()` did not accept (and, for Advance, anything without the GBA cartridge
logo at `0x04`), then wrote the entry with its measured size and SHA-256. The blurbs
are one plain sentence each, because they are read on the shelf.

New: **`ark-arcade --seed --from-dir DIR`** publishes that list from files carried over.
It goes through `publish_candidate(..., source=…)`, so the recorded SHA-256 and size are
still enforced — a different file with the right name is refused, and re-running skips
what is already there. This exists because a box may have no route to the catalogue at
all; on this box the route happened to work, and `60-portal` fetched all 20 new titles
itself.

### 17.4 A cold boot broadcasts

`BOOT_MODE` in `/etc/ark/ark.env` decides, and it is now written by phase 7 as
**`hotspot`** (only when absent — an existing answer is never overwritten).
`ark-mode boot` reads it; `ark-boot-guard` calls `ark-mode boot` instead of `ark-mode
auto`; the hotspot choice **pins**, so `ark-net-watch` will not walk the box back onto a
router nobody asked for, and `ark-mode auto` releases it. `ark-mode boot --dry-run`
prints the decision without root and without touching the radio, which is how the boot
path is proved on a live box that must not be rebooted during a working session.

### 17.5 `ark.lan` on a Mac: the name, not the door

The reported defect — `https://ark.lan:8443/admin` unreachable — is a **name
resolution** fact, not nginx. Measured from the Mac:

| Probe | Result |
|---|---|
| `dscacheutil -q host -a name ark.lan` | no answer |
| `dscacheutil -q host -a name ark.local` | `192.168.1.26` (mDNS, via the box's avahi) |
| `curl -k https://ark.local:8443/admin` | **303** → `/admin/login` |
| `curl -k https://ark.local:8445/arcade/player` | **200** |
| `curl -k https://192.168.1.26:8443/admin` | `000` — no SNI, so nginx serves the management listener's default server, which rejects the handshake on purpose |

`.lan` is not a special-use name: nothing asks mDNS about it. So `ark.lan` resolves only
while the box is broadcasting (its own dnsmasq wildcard answers every name) or where a
network's DNS knows it, and `.local` is the name for a home LAN. The certificate already
covers both. The words were wrong, not the server: `README.md`, `docs/ADMIN.md`,
`docs/USING-THE-ARK.md`, `pi/portal/index.html` and `pi/portal/help.html` now say which
name resolves where, and the troubleshooting row says why.


### 17.6 Deployed, and the File Box told the truth again

Source synced to `/srv/ark/src` (rsync, as `03-provision.sh` does), then
`--only 60-portal,70-network`, then `--only 90-verify`. Rollback points taken first:
`/root/ark-db-before-games.db` (SQLite `.backup`, WAL-safe) and
`/root/ark-src-before-games.tgz`.

The leftover `e2e-drop-test.epub` row — a record whose file had long gone, and the only
reason `/api/ready` answered 503 — was removed on the box by a one-off script that
deleted that **one named record** and wrote the same audit line the owner page writes
(*"record removed; the file was already gone"*). It did not sweep the folder: a row can
outlive its file by design, and a tidy-up pass would be data loss wearing a maintenance
costume.

**Evidence, as measured on the box and from the Mac.**

| What | Result |
|---|---|
| `pytest` on the Mac | **252 passed** (was 248) |
| `install-ark.sh --only 60-portal,70-network` | `ARK_INSTALL_COMPLETE`, 0m36s, `failed: none` |
| `install-ark.sh --only 90-verify` | *"everything a visitor touches is working"*, `ARK_INSTALL_COMPLETE` |
| `ark-arcade --status` | **23 game(s)** on the shelf, 0 players, 0 suggestions pending |
| `curl http://ark.local/api/ready` | **200**, `ready 8 / total 8`, `blocking: []` |
| `/arcade/`, `/arcade/g/apotris`, `/arcade/g/cybercoaster`, `/arcade/g/aevilia` | 200, 200, 200, 200 |
| `/arcade/g/apotris/rom` | 200, **4,194,304 B** (Apotris) |
| game page asset references | **none** matching `http(s)://` — the page still works with the internet unplugged |
| `/arcade/player` on `:80` | 307 → `:8445` (by design) |
| `ark-mode status` | mode `home`, *at boot: hotspot (BOOT_MODE)* |
| `ark-mode boot --dry-run` | *"boot would broadcast \"The-Ark-Offline\" and stay there (BOOT_MODE=hotspot)"*, nothing changed |
| free space | **121.5 GiB** (root filesystem 45% used; card is 229 GB) |
| `ark-arcade --discover` re-run | **45 offered, 0 refused, 0 need a look** (was 0 offered, 40 refused, 4 need a look) — the stored refusals were re-evaluated under today's rules, so the owner page no longer quotes a rule that was deleted |

**Not proven.** No game has been **played** on the Pi — EmulatorJS has not run on that
hardware, no phone has signed in, no save has been kept. The carried-file path is proved
in tests only; the box fetched its shelf over its own internet route. **SNES and Genesis
have no catalogue entries at all** (the source returns none for either), so a game for
those two consoles must be hand-carried with `--import` — the consoles are supported by
the player, the sourcing is not. And the first real **cold boot** into broadcast mode has
not happened: the setting and the dry run are proved, the reboot is not.

**Next step.** Play one game on the phone with the box off the router; then reboot it
once and watch `The-Ark-Offline` appear with nobody touching anything; then carry one
SNES title over with `--import` and record what that path asks for.


## 18. The player was running invisibly, and the library could not find its own books — 25 Oct 2026

Both defects were found the same way, and the method is worth recording as much as the
fixes: the box was asked **what a browser sees**, not what the code intends. A headless
Chromium (`playwright`, installed under `/tmp` on the Mac) loaded the real pages over
`http://ark.local` and recorded every request, every console message and the emulator's
own internal state. Neither defect was visible to `curl`: every URL answered 200,
`90-verify` was green, `/api/ready` was 200.

### 18.1 The library answered, but could not resolve a single book

`curl /kiwix/` was 200 and OPDS listed every collection, so the library looked healthy.
Search answered `<error>Content not found</error>` for every name that was tried.

Root cause, proved with `kiwix-manage … show`: the catalogue held

```
path: /srv/ark/srv/ark/kiwix/bicycles.stackexchange.com_en_all_2026-10.zim
```

a path that does not exist. `ark-kiwix-library` built the index with
`tmp="$(mktemp)"` — in `/tmp` — and then moved it to `/srv/ark/kiwix/library.xml`.
**`kiwix-manage` writes each bundle's path relative to the folder holding the library
file**, so building it in `/tmp` and moving it recorded `/srv/ark` + `/srv/ark/kiwix/…`.
`kiwix-serve` then had a catalogue of twenty books it could not open: no error, no
warning, and the file list looked perfect.

* Fix: the index is built **in the folder it describes**
  (`mktemp "$ZIM_DIR/.library-XXXXXX"`), so the paths come out as bare file names. The
  published file is handed to the folder's owner (`chown --reference=$ZIM_DIR`), because
  this script runs as root from the downloader and as `pi` from the service — and a
  catalogue left owned by the other one cannot be edited in place. The first attempt at a
  `flock` in that folder proved the point by taking the whole library down with
  `library.xml.lock: Permission denied`; there is no lock file now, and two builders
  racing is harmless because each publishes a complete index by rename.
* The **handle** a collection answers to is the ZIM's **file name without `.zim`** —
  `bicycles.stackexchange.com_en_all_2026-10`. Not the `id` (a UUID), and not the `name`
  attribute, which drops the date suffix and so names a different file than the one on the
  card:

  | `content=` | items |
  |---|---|
  | `bicycles.stackexchange.com_en_all_2026-10` | **25** |
  | `bicycles.stackexchange.com_en_all` | 0 |
  | `2c464b36-1ea7-2646-ba6a-3b7504ec4a63` | 0 |

  The portal asked with the UUID, so every search was silently empty.
* `ark_health.library_books()` now carries `stem` (the `path` basename minus `.zim`, or
  `""` when the index cannot say), and `main._handle()` prefers it. A hit's own
  `/kiwix/content/<handle>/` link is resolved against the handle **as well as** the id, so
  a result names the collection that owns it.
* One more trap, recorded because it cost an hour: the search endpoint is
  **`/kiwix/search`**, not `/search`. Asking the bare path returns kiwix's own 404 page,
  whose text says "Content not found" — indistinguishable from a real lookup failure
  unless the URL is read carefully.

### 18.2 The player was playing, and nobody could see it

The player's page answered 200, every asset answered 200, and the vendored EmulatorJS was
byte-identical to upstream 4.2.3 (all 25 files hash-clean). The screen was a black
rectangle with no menu bar.

* **The box the game fills had no height.** `#arcade-stage` was an empty `<div>` with a
  border and no height; EmulatorJS's own stylesheet makes `.ejs_canvas` `height: 100%`, so
  the canvas had nothing to resolve against and collapsed. The emulator was *running* the
  whole time — `gameManager.getState()` returned 35,056 bytes — behind a canvas a few
  pixels tall. Proved by asking the emulator instead of the network:

  | Question | Before | After |
  |---|---|---|
  | `getState().length` | 35,056 | 35,056 |
  | stage box | 834 × 216 (bogus) | 833 × 572 |
  | canvas | not composited, black | the 2048 title screen, then Apotris's menu |

* **EmulatorJS overwrites its own element's `style` attribute** (it writes
  `--ejs-primary-color` into it), so an inline `aspect-ratio` on `#arcade-stage` is erased
  as the player starts. The shape of the screen now lives on a `#arcade-frame` around it,
  and `SYSTEMS[...]["aspect"]` decides it per console (Game Boy 10/9, NES 16/15, GBA 3/2,
  SNES 4/3, Genesis 10/7) — a Game Boy screen is nearly square, and a 4/3 box around it
  leaves black columns on a phone.
* **The ROM route answered HEAD with 405.** The player asks for a game's size with a `HEAD`
  before downloading it (it keeps a copy in the browser and compares lengths), and
  `@router.get` does not answer HEAD. `Allow: GET` is not an answer a browser can use. The
  ROM route, the emulator assets, the shelf stylesheet, the notices page and
  `/drop/<name>` now all answer `GET` **and** `HEAD`.
* **`localization/en-US.json` was missing**, so the console said
  `Missing language en-US !!` on every page load. En-US is vendored now (and listed in
  `SHA256SUMS`, which the vendored-file test checks in both directions).

**Two false leads, recorded so they are not chased again:** the wasm cores were not
missing (the `.data` files are 7z archives and the browser unpacks them itself), and the
405 on HEAD did not stop the download (the player falls through to a GET). What settled it
was the emulator's own `getState()`/`getSaveFile()` — a running core and a black screen at
the same time.

### 18.3 Deployed, and measured

`--only 50-kiwix,60-portal` (the first attempt failed on the lock file described above),
then `--only 50-kiwix`, then `--only 60-portal` for the frame, then `--only 90-verify`:
every run `ARK_INSTALL_COMPLETE`, `failed: none`.

| What | Result |
|---|---|
| `pytest` on the Mac | **261 passed** (was 252) |
| `kiwix-manage … show` | `path: /srv/ark/kiwix/bicycles.stackexchange.com_en_all_2026-10.zim` — real, and `library.xml` is `pi:pi` |
| `/kiwix/search?content=…_2026-10&pattern=brake&format=xml` | 25 `<item>`s, `totalResults 9,717` |
| portal `/search?q=brake` | hits from the bicycles **and** ham collections, each linking `/kiwix/content/<stem>/questions/…` |
| portal `/search?q=water+filter` | **24** result links |
| eight searches in a row | HTTP 200 every time, `ark-kiwix` `active`, `NRestarts=0`, no segfault/OOM |
| `/api/ready` | **200**, `ready 8`, `blocking: []` |
| `HEAD /arcade/g/2048gb/rom` on `:80` | **200**, `content-length: 32768` |
| `arcade/g/2048gb` in Chromium | `started: true`, `getState()` 35,056 B, **2048-gb title screen drawn** |
| `arcade/g/apotris` in Chromium | `started: true`, `getState()` **430,168 B**, **Apotris menu drawn** — the mgba core, a second console |
| every request the game page made | no HTTP ≥ 400 at all (the 404 and the 405 are both gone) |
| `90-verify` | *"everything a visitor touches is working"* |

**Still not proven.** No human has looked at either page — the evidence is a headless
browser on the Mac and the box's own answers. No save has been written to a player name
through the new frame, the phone walk-through in §8.9 is still the gate before a tag, and
no game has been played **on** the Pi (the emulator runs in the reader's browser; the Pi
only serves bytes).

### 18.4 Giving the box a game of your own — the shelf duplicated it — 25 Oct 2026

Asking *"can I drop a ROM in myself?"* was answered by doing it on the box for the first
time (`ark-arcade --import` had only ever met the test suite). The copy went on, appeared
on `/arcade/` and played — and giving the **same file** a third time produced
`Added zz-import-test (revision 2)` instead of *"already on the shelf, unchanged"*.

**The defect.** A candidate from the catalogue carries the SHA-256 the source stated, and
`publish_candidate()` skips work when the shelf's own game already has that fingerprint.
`owner_candidate()` never set one, so `candidate.get("sha256")` was `None`, the dedup
branch could never fire for an owner-supplied file, and **every re-import of an identical
ROM became a new revision with a second copy left on the card** (`stars.gb` → `stars-r2.gb`).
A person copying files twice, or re-running their own command, would have grown the shelf
without ever being told.

**The fix.** `owner_candidate()` records `arc.sha256_file(source)`; `ark_arcade.game_by_sha()`
finds another game with the same bytes under a different name, and `publish_candidate()`
refuses it with the name it already has (*"That game is already on the shelf as "Stars"."*).
Three behaviours, three tests: identical file + same slug ⇒ `installed False`,
*"already on the shelf, unchanged"*, one file on disk, revision 1; same bytes + another slug
⇒ refused, one game, no second file; different bytes + same slug ⇒ revision 2 (a real
update still works).

**Two small things that make a hand-added game look like the rest of the shelf.** `--import`
had no way to write the one line a reader sees, so every owner-supplied game said *"Ready to
play."*; `--description` now does, and `--notices` (credits) is rendered under the title —
it was stored but never shown. Both are escaped (`arc.esc`), proved by
`test_arcade.py::test_what_a_person_wrote_about_a_game_reaches_the_page_as_text` with a `<b>`
payload in the credits.

**Also corrected:** the docs told the owner to run the shelf command with `sudo`. The web
service and the weekly check run as `pi`, and `/srv/ark/arcade/games` is `pi:pi`; a
root-run import leaves root-owned game files and root-written rows in a database `pi` has
to keep writing — the same class of fault as the root-made backup bundle that the owner
page could not read (§16.3). Every command shown is now `/usr/local/sbin/ark-arcade …`,
run as `pi` (that directory is not on `pi`'s `PATH`, which is the only reason for the full
path) — `README.md`, `docs/ADMIN.md`, `docs/USING-THE-ARK.md` and
`docs/REBUILD-ME-ARK.txt`. The same pass found two claims that had never been true:
`docs/USING-THE-ARK.md` said a game could be added from the **Games** page behind the
owner's sign-in, and `docs/REBUILD-ME-ARK.txt` still offered to *"restore the pinned
three"* of a 23-game shelf. Both are corrected; there is no add-a-game button.

| What | Result |
|---|---|
| `pytest` on the Mac | **267 passed** (was 262) |
| the test that fails without the fix | `test_the_same_file_given_twice_is_not_put_on_the_shelf_twice` |
| proven on the box | `--dry-run` → *"Would add Import test (gb) as zz-import-test."*; real run → `Added zz-import-test (revision 1)`; the file served, the page drawn, then the test rows and files removed and the shelf back to 23 games, `/api/ready` 200 |

**Deployed the same day, and re-proved on the box.** The database was taken aside first
(`sudo sqlite3 … ".backup '/root/ark-db-before-owner-import.db'"`, 225,280 B — `.backup`,
never `cp`, because the live database is WAL), then `--only 60-portal`, then
`--only 90-verify`: both `ARK_INSTALL_COMPLETE`, `failed: none`. There was **no schema
change** — the layout stayed v4 and no migration ran — so the deploy carried only the
new module code, and `/root/ark-db-before-owner-import.db` is the way back.

| What the box was asked | What it said |
|---|---|
| the same file, same `--slug`, a third time | `zz-import-test was already on the shelf` — no revision 3, one file on the card |
| those bytes under a new name (`zz-import-other`) | `Not done: That game is already on the shelf as "2048".` — nothing written |
| a different game's bytes under a new name | `Not done: That game is already on the shelf as "5 mazes".` |
| a new file with `--description` and `--notices` (credits carrying `<b>`) | `Added zz-import-words (revision 1)`; its page answered `A small game about mazes.` and `Made by &lt;b&gt;friends&lt;/b&gt;.` — rendered **and** escaped |
| after removing the test rows and files | `23 game(s) on the shelf`, `/arcade/` 200, `/arcade/g/2048gb` 200, `/api/ready` 200 `ready 8 / total 8`, `blocking: []`; `90-verify` *"everything a visitor touches is working"* |

**Two small facts worth knowing before reading that table.** The refusal names the
**earliest** game holding those bytes (`ORDER BY id`), not the copy just made — in the
second row that is the shelf's own `2048`, because the test copy *was* 2048's bytes;
naming the original is the useful answer. And the public page
for a slug that is not on the shelf answers **200** with *"That game is not here"* (a
friendly page, not an error), while its ROM route answers 404 — so no probe may read
that 200 as "the game exists".

**What this does not add: a drop folder.** There is still no `drop/games` watched by a
unit, no upload button on the **Games** page, no zip handling and no cover art
(`docs/REQUIREMENTS-MATRIX.md` R7). A game the owner supplies goes on with one console
command, and the shelf tells the truth about a file it already has.
