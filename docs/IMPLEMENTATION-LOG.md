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
| Deployment status | **deployed on the box 25 Oct 2026** — stages 2–6, ARK Arcade, the 25 Oct hardening, R3's book door, the owner-import fix and the name-from-the-bytes work are live; database at layout **v5**, `90-verify` green, `/api/ready` 200 (§15, §18.4, §19, §20). Rollback: `/root/ark-snapshot-20261025-092106`; the later deploys took their own copies at `/root/ark-db-before-owner-import.db`, `/root/ark-db-before-v5.db`, `/root/ark-db-before-addgame.db` and `/root/ark-db-before-stage7.db`. **Not on the box yet: phase `15-resilience` (§26)** — no reboot has been taken with it installed, and phase `58-ai` has not been run (there is no model to run it for) |
| Hardware tests | **scripted proofs only** — the installer, the verifier, the doors, a bundle, `/api/ready`, the arcade origin and `ark-arcade --import` have all answered on the Pi, and a headless Chromium on the Mac has drawn three of the shelf's games from the box's own files (§18.2, §20.2). **No human has used it on a phone yet**, no 2 GiB upload has crossed the proxy, the watchdog has never seen a real radio event, **the boot repair pass has never met a real power cut (§26)**, and **no model has ever been on the box, so the helper has never answered (§25)** |
| Requirements matrix | `docs/REQUIREMENTS-MATRIX.md` — R1–R11/A–D, each status with the test that proves it and the sentences still missing |

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
| 12 | Emergency content review | **stage 7 done (code + test)** — `ark_review` holds the eight topics, each naming the pages that carry it; a page the card does not have is *named as missing*, never assumed; the owner records "I have read this today" and a topic older than a year is shown as **due**. Nothing judges a page and nothing refuses to serve one (§23) |
| 13 | Essentials pack, QR, accessibility, household page | **stage 7 done (code + test)** — `/household` (nine answers the household typed + when each emergency page was last read), `/cards` (three wall cards, inline SVG, no image/font/script/network), `/essentials.zip` (one file: our manuals and guides rewritten for a folder, the quick card, the recovery sheet, `START-HERE` and a sha256 manifest — **not** the three wall cards, which print from `/cards`), the owner's **Household** page, and an accessibility pass (skip link, visible focus ring, ≥16 px body, 44 px targets, print rules) (§23) |
| — | Media — films on the box | **delivered and tested (Mac)** — a film is probed rather than trusted (`direct`/`remux`/`convert`/`unsupported`), stored once, published only by the owner, served in ranges (206/416/HEAD), pictured once, converted in the background one at a time, and deletable without losing a shared payload. A visitor's film waits for the owner; the shelf, the player, resume (browser-local) and captions are public and free. **No phone has played one yet** (log §43, human list §21) |
| — | One storage policy | **delivered and tested** — `ark_space` keeps `max(7 % of the filesystem, 2 GiB)` under every growth path (uploads, books, games, films, collections, models, backups), with cross-process reservations; it replaced three separate numbers. Measured on the box: 16.0 GiB of a 229 GiB card (log §43) |
| — | ARK Arcade | **delivered and tested** — public shelf, guest play, optional named-player saves on the TLS `:8445` origin, owner **Games** page, licence/allowlist/hash gate, weekly discovery, and saves carried by a backup (§12); since 25 Oct a game can also be named from the file itself against an offline catalogue, and added from the owner's page (§19) |
| — | Ask the Ark — a helper on the card | **delivered and tested** — `http://ark.lan/ask` answers from a model file on the card, the engine listens on `127.0.0.1:8089`, the page of facts is built from what is really here, one answer at a time, and **a box with no model is a supported state** that says so in one sentence (§25). No model has ever been on the box |
| — | A power cut | **delivered and tested** — phase `15-resilience`: `ark-self-heal` runs before the portal and once a day, fixing a clock in the past, a half-written small file, a database SQLite cannot open and the temp files a cut leaves behind; plus `fake-hwclock`, tmpfs `/tmp`, a 15 s write-behind, a forced filesystem check, a volatile journal, a 15 s hardware watchdog and `Restart=always` on every long-running unit (§26). **It has never met a real power cut** |
| — | A save survives a cut | **delivered and tested** — every database connection writes `synchronous=FULL`, and a save's bytes are content-addressed (`saves/<player>/<game>/blobs/<slot>-<sha256>.bin`, staged as `.save-*.part`, fsynced, renamed, the folder fsynced) so a record can never name bytes that are gone; schema **v6** is additive. A row written before the change is still read, and retired when a new save replaces it (§27) |
| — | A boot that does not wait for the internet | **delivered and tested** — the three loopback services wait for `local-fs.target` and nothing else, and a half-written upload waits on the **card** (`client_body_temp_path`, `TMPDIR=/srv/ark/tmp`) instead of in the 256 MB tmpfs; the forced card check is withdrawn by the one boot that used it (`ark-fsck-once`), and `90-verify` prints it (§28). **Never run on the box** |
| 14 | Sequence | stages 1–7, ARK Arcade, R3, the name-from-contents work (§19), the helper (§25), the power-cut repair (§26), durable saves (§27) and the boot/upload work (§28) are **code + test**; **everything except phases `15-resilience` and `58-ai` is deployed 25 Oct / 26 Oct 2026** (§15, §19, §20, §24) — the phone walk-through is the next human step; neither of those two phases has been run on the box, and neither has anything in §27–§28 |
| 15 | Acceptance tests | **504 tests, 2 skipped, 24 files** (`504 passed, 2 skipped`; the two skips are QR cross-checks needing `qrcode`/`opencv-python-headless`), all green (`pytest -o addopts='--strict-markers' -q`); the per-requirement verdict is `docs/REQUIREMENTS-MATRIX.md` |

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
2. **Deploy stage 7, then walk it** — the household layer is code + test (§23) and has
   never run on the Pi. No schema change, so `--only 60-portal,65-admin` is enough, then
   `--only 90-verify`; after that open `/household`, `/cards` and a made pack on a phone.

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
sheet, delivered in §11. ARK Arcade followed in §12, and stage 7 — the household
layer — was delivered on 25 Oct 2026 (§23); the deploy walk-through (`.clinerules`
§8.9) is still the next human step, and stage 7 is the next thing to put on the box.


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
2). Stage 7 (the household layer) was delivered on 25 Oct 2026 — §23 — and is not
yet deployed.

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

**`pytest` 240 passed (15 files) at that checkpoint — 299 at the tip.** Stages 2–6, ARK
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
(`<HOUSEHOLD-SSID>-5G-1`, `<HOME-IP>/24`, wifi link `home-wifi`), which is why
anything on the same LAN reaches the portal at `http://ark.local`. The hotspot
profile `ark-hotspot` still exists — SSID `The-Ark-Offline`, `mode=ap`,
`ipv4.method=shared`, `autoconnect=no` — and only `ark-mode hotspot` or
`ark-net-watch` bring it up. `autoconnect=no` is the design (`.clinerules` §8.7:
the watchdog acts only through `ark-mode`), not a defect.

**What was added.** Two networks the household also uses, `<OTHER-NETWORK-SSID>` and
`<OTHER-NETWORK-SSID>-fast`, now exist as NetworkManager profiles on the box:
`/etc/NetworkManager/system-connections/<PROFILE-ID>.nmconnection` and
`<PROFILE-ID>-fast.nmconnection`, both `0600 root:root`, `autoconnect=yes`,
`autoconnect-retries=0`, priorities **7** (2.4 GHz name) and **8** (5 GHz name),
so the home router's 20/10/5 still wins whenever home is in range. They were
written through **stdin** into a `0600` temp file and installed with
`install -m 600 -o root -g root`, so no password ever appeared on a command line
or in a shell history; the reload was `nmcli connection reload`. Verified by
`nmcli … connection show` (SSID, autoconnect, priority, `key-mgmt=wpa-psk`) and
by the stored secret's **length only** (10 characters) — the key itself was
never printed.

**Nothing was activated on purpose.** `nmcli connection up <PROFILE-ID>` would move
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
`bash -n` clean, `0700`, and five `WIFI_PROFILES` lines with `<PROFILE-ID>-fast|…|8`
and `<PROFILE-ID>|…|7` last. `docs/REBUILD-ME-ARK.txt` gains the field in the build
order, a line in "making copies for other people", and **trap 31** — the box
carried to a second house it had never heard of, with the no-password-on-the-
command-line way to add one to a box that is already running.

**Not proven:** neither new profile has ever been used. No join, no DHCP lease,
no portal page from `<OTHER-NETWORK-SSID>`. The guard's negative cases were re-run on 25 Oct
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
#  Placeholders: <HOUSEHOLD-SSID>-5G-1 … <HOUSEHOLD-SSID> <HOUSEHOLD-PSK>
#                <HOUSEHOLD-SSID>-5G-2 <HOUSEHOLD-PSK>
<PROFILE-ID>-fast|<OTHER-NETWORK-SSID>-fast|<OTHER-NETWORK-PSK>|8
<PROFILE-ID>|<OTHER-NETWORK-SSID>|<OTHER-NETWORK-PSK>|7
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
| `WIFI_PROFILES` | exactly 5 lines: `home-wifi` 20, `home-wifi-2` 10, `home-wifi-3` 5, `<PROFILE-ID>-fast` 8, `<PROFILE-ID>` 7 |
| summary numbering | `1.` … `5.`, sequential (the earlier draft repeated `4.`) |
| guard, negatives (§16.6 harness) | 5 × `rc=1` with the expected `✗`; `good-two` `rc=0` |
| guard, the regression itself | token re-inserted into a copy of the template → *“the extra network '<PROFILE-ID>' appears 2 times in the render, not once inside WIFI_PROFILES”*, `rc=1` |

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
| `dscacheutil -q host -a name ark.local` | `<HOME-IP>` (mDNS, via the box's avahi) |
| `curl -k https://ark.local:8443/admin` | **303** → `/admin/login` |
| `curl -k https://ark.local:8445/arcade/player` | **200** |
| `curl -k https://<HOME-IP>:8443/admin` | `000` — no SNI, so nginx serves the management listener's default server, which rejects the handshake on purpose |

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

## 19. The box can name a game it has never seen — 25 Oct 2026

A game file carries its own name. A Game Boy cartridge has a title at `0x134`, a region and a
header checksum; every console has a size it will accept; and the bytes themselves — with the
cataloguing header some dumps carry stripped off — are a fingerprint every copy of that
cartridge shares. So the box stopped asking a person to type a title it could read.

| Piece | What it owns |
|---|---|
| `pi/api/ark_romdb.py` | reads a game file and fingerprints it: `strip_headers()` (a `.smc`, an iNES header, a copier header), `identify_path()` / `identify_bytes()`, `match()` against the catalogue, `game_by_identity()` (the same bytes under another name), `title_from_filename()` / `tags_of()` / `region_of()` for the no-catalogue answer, `stats()`, `may_reuse()` |
| `pi/api/ark_romdb_build.py` + `pi/bin/ark-romdb-build` | the only builder of `/srv/ark/romdb.sqlite`: `--fetch` (needs a connection), `--from-dir` (a carried checkout), `--status`, `--self-test`, `--quiet`, `--out`. Writes `.partial` then renames, and hands the file to the folder's owner because the pages run as `pi` |
| `pi/api/ark_rommeta.py` | the words: `prefill()` returns what to put on the card (`title`, `system`, `facts`, `sentence`, `confidence`), `apply()` fills a candidate **without replacing anything a person typed** (`_ROM_KEEP` + `COALESCE`), `game_facts()` / `confidence_words()` / `also_listed_as()` for the page |
| `ark_arcade_install.py` | `prefill`/`apply` on both the download path and the owner path, `spool_upload()` (one file, into `arcade/tmp`, under a name this box chose), `discard_upload()`, and a startup sweep of stale `rom-*.part` / `upload-*` |
| `ark_admin.py` | **A game you already have** on the Games page: `POST /admin/arcade/upload` shows what the box made of the file, `POST /upload/add` publishes it, `POST /upload/discard` throws the waiting copy away. Every step audited; all three sit behind the sign-in and the TLS origin |
| `ark-arcade --match FILE` | the same question at a console, for a person: the sentence, then the year, maker, publisher, kind of game and region. Writes nothing |
| schema **v5** | `games` and `game_revisions` gain `sha1`/`md5`/`crc32` and `rom_*` facts plus `rom_confidence`; `ALTER TABLE … DEFAULT ''` keeps old rows readable and the migration is numbered, never a rewrite of v1 |
| `pi/nginx/ark-admin.conf` | an upload location on `:8443` with its own body cap, so a game file does not travel through the public portal at all |
| `tests/test_romdb.py` (15) + 4 in `tests/test_arcade.py` | **286 passed** |

**The catalogue is information, exactly like a licence.** 56,448 games, 27,723 of them on a
console this box can play, built from the libretro database on the Mac (25,120,768 B, source
dated `2026.10.07`). Nothing in a record can refuse a file: no catalogue at all, no record, a
record with no licence and a record naming a console this box cannot play all end the same way
— the game is added, named from its file name, with the sentence *"Nobody has listed this
file; the name on it is all this box has."* A file the box cannot play at all is refused before
the owner is asked to fill in a form, and the waiting copy is deleted.

| What the tests prove | Test |
|---|---|
| a file called `unknown-1.gb` becomes *Cave Diver* | `test_a_game_is_named_from_its_bytes_even_when_the_file_name_says_nothing` |
| the fingerprint kept is of the cartridge, not of the wrapper around it | `test_the_fingerprint_kept_describes_the_cartridge_not_the_wrapper` |
| the strongest fingerprint wins and is the one stored | `test_the_strongest_fingerprint_is_the_one_used_and_kept` |
| a zipped game is looked inside | `test_a_game_inside_an_archive_is_looked_inside` |
| no record ⇒ usable, and claiming nothing | `test_a_file_nobody_listed_is_still_usable_and_claims_nothing` |
| a word the owner typed is never replaced | `test_a_word_the_owner_typed_is_never_replaced` |
| only consoles on this box are recognised | `test_the_box_only_recognises_the_consoles_it_can_play` |
| no catalogue at all still names every game | `test_a_box_with_no_catalogue_still_names_every_game_from_its_file_name` |
| the owner-facing words name no algorithm | `test_the_page_words_never_name_an_algorithm` |
| the card shows the facts and the catalogue's own spelling | `test_a_page_shows_the_facts_and_the_catalogues_own_spelling` |
| an upload waits under a name the box chose | `test_an_uploaded_file_waits_under_a_name_the_box_chose` |
| an upload is not a game until somebody confirms it | `test_an_upload_is_not_a_game_until_somebody_confirms_it` |
| a copy of a game already on the shelf is refused by its bytes | `test_an_uploaded_copy_of_a_game_already_on_the_shelf_is_refused_by_its_bytes` |
| a recognised file is stored with what the catalogue said | `test_a_recognised_file_is_stored_with_what_the_catalogue_said` |
| the whole door: pick a file → confirm → on the shelf, one file, one row, one audit line | `test_the_owner_can_add_a_file_and_watches_it_become_a_game` |
| the same cartridge offered twice is named, not added | `test_the_same_cartridge_offered_twice_is_named_not_added_again` |
| a file the box cannot play is refused and leaves nothing behind | `test_a_file_the_box_cannot_play_is_refused_and_changes_nothing` |
| a stranger cannot use the upload door | `test_an_upload_without_a_sign_in_writes_nothing` |

**Two defects found while building it, and one fix made while verifying it.** `spool_upload()`
used `mkstemp`, whose name
(`tmpab12cd.gb`) could never match the `upload-<32hex>.<ext>` the rest of the code (and the
sweep) expects — it is now `secrets.token_hex(16)` with `O_EXCL`, so the waiting name is both
unguessable and the one the box agrees with itself about. And the upload route was written
`async def` with a blocking read; it is a plain `def` now, so Starlette runs it in its thread
pool and a 64 MB file cannot stall the event loop. Then `--status` was found describing the
box's own `/srv/ark/romdb.sqlite` even when `--out` named another file — the same silent fault
as `--list` answering for the default folder (§8.7), one tool over. It answers about the file
it was asked about now, says which file that was, and
`test_a_question_about_a_file_is_answered_about_that_file` fails if that ever regresses.

### 19.1 Deployed on the box, 25 Oct 2026 — and proved through the owner's door

The branch tip was put on the Pi with the owner's explicit go-ahead, in the shape every
deploy before it took, because this one **migrates the database**:

```text
sudo sqlite3 /srv/ark/api/data/ark.db ".backup '/root/ark-db-before-v5.db'"   # layout v4
sudo tar czf /root/ark-src-before-romdb.tgz -C /srv/ark src
sudo bash /srv/ark/src/install-ark.sh --only 20-storage,60-portal,65-admin    # v4 → v5
# the catalogue, built on the Mac (the Pi has no route to GitHub)
scp /tmp/romdb-ship/romdb.sqlite pi@ark.local:/tmp/
ssh pi@ark.local 'sudo install -o pi -g pi -m 0644 /tmp/romdb.sqlite /srv/ark/romdb.sqlite'
sudo bash /srv/ark/src/install-ark.sh --only 90-verify
```

| Evidence | Answer |
|---|---|
| the migration | `schema_version` **5**; the four fingerprint columns (`sha1`, `md5`, `crc32`, `rom_*` facts + `rom_confidence`) are on `games`, and old rows read as `''` |
| the catalogue | `/srv/ark/romdb.sqlite`, **25,120,768 B**, sha256 `6763ecfc0887b55c9fe3f222116754ed9a25678ec7e4e4a7d280eb8985c6177f`, `pi:pi 0644`, 56,448 names (27,723 on a console this box can play) |
| `90-verify` | green — *“everything a visitor touches is working”*, `✓ game names 56448 name(s) on this box`, `✓ game name reader a made-up cartridge was recognised from its own bytes` |
| `/api/ready` | **200**, `ready 8 / total 8`, `blocking: []` |
| a carried file | `ark-arcade --match /tmp/apotris.gba` → *This looks like “Apotris” (Game Boy Advance)*; `2048gb.gb` → *“Nobody has listed this file; the name on it is all this box has.”* |
| the refusal path, live | the admin upload door answers **403** without a sign-in, the public door **404**, and `arcade/tmp` stays empty |
| the live box afterwards | still **23** games, **23** files on the shelf, `arcade/tmp` empty, nothing restarted outside the phase |

**The owner's door, end to end, on the box.** The live admin password is the owner's, so the
door was proved against a **throwaway instance** rather than by guessing a credential: a second
API on `127.0.0.1:18099` with its own `ARK_ROOT` (`/tmp/arksmoke`), its own database, its own
`arcade/`, the **real** `/srv/ark/romdb.sqlite` and a real **4,194,304 B** `apotris.gba`. The
probe (`logs/arksmoke-proof-20261025.py`, kept beside the deploy logs and **not** in git — it is a
throwaway) signed in through the real router, opened the real
**Games** page, posted the file as a browser does, read the answer, confirmed the words, and then
asked the filesystem, the database and the public page. **29 checks, `SMOKE_RESULT PASS`:**

* the Games page offers **A game you already have**, a file picker and *Look at this file*;
* posting 4 MB answers *“**This looks like “Apotris” (Game Boy Advance).**”*, and **nothing is on
  the shelf** yet — the file waits in `arcade/tmp` under a name the box chose (`upload-…`);
* the confirm form arrives filled in, carrying the waiting file's own token;
* confirming publishes **exactly one file**, leaves **nothing waiting**, writes **one row** with
  the title, `system = gba`, `sha1`, `crc32` and `rom_confidence`, adds **two `arcade-upload`
  audit lines** (including *“added revision …”*), and the game then appears on `/arcade/`.

The throwaway root, its database and its log were deleted afterwards, and the live shelf was
re-checked afterwards (23 games, nothing in `arcade/tmp`).

**Still not proved, and it is the same gap as before this deploy:** no **person** has picked a
file in a browser. The proof above is a script speaking HTTP exactly as the page does, against a
throwaway database seeded by rewriting `admins.pass_hash` — it exercised the router, the spool,
`publish_candidate()`, the schema and the public page, but not an owner's thumb, not a 64 MB file
over the real proxy, and not a file added to the **live** shelf. `90-verify`'s game-names block has
only ever read this one catalogue, and the *nobody has listed this file* answer has only been seen
for `2048gb.gb` at a console.

**Two probe defects found on the way, both mine, both now in `.clinerules` §8.7:** the first
version seeded an empty database without `ark_admin.ensure_initialised()` (`no such table: admins`
— the same trap already written down), and the second reported *“signing in is accepted”* for a
sign-in that had failed, because `urllib` silently follows a 303 and a followed redirect looks like
a 200. A probe of an admin door has to ask for the redirect and assert on it.


---

## 20. The white screen was a page starting a game nobody had touched — 25 Oct 2026

The owner reported, a second time, that a game opened in the browser and showed **nothing but a
white box** in the player. Every previous check had said the player was fine: the page answered
200, every asset answered 200, `90-verify` was green, and `EJS_emulator.gameManager.getState()`
returned a real 35,056-byte save state. All of that was true and none of it was the picture.

### 20.1 What was actually happening

A **headless Chromium on the Mac, driving the real page over the network**, asked the emulator
two questions instead of asking HTTP:

| Question | Before the fix |
|---|---|
| does the state change while the page is simply open? | **no** — the save state was byte-identical after 4 s (hash `3885702006` twice) |
| does it change after the page is touched? | **yes** — a click on the player changed it every time |

So the core was *loaded* and *not running*. The canvas held its initial WebGL clear, which is
**white** — and that is the white box: not a broken player, not a missing file, not a missing
core.

The cause is one line of our own page: `EJS_startOnLoaded: true`. It starts the emulator the
instant the page loads, with no gesture from the person in front of it. A browser keeps a page
that has not been touched **silent** (that is the autoplay rule the console had been warning
about all along: *“The AudioContext was not allowed to start”*), the core's audio-driven main
loop never ticks, and the picture never arrives. Loading the page by tapping a link on the
previous page does **not** count: activation belongs to the document that was touched, and the
new document starts with none. That is why the same file played in one test and stayed white in
a phone's browser.


### 20.2 The fix, and how it was proved

* `EJS_startOnLoaded` is **gone**, with a comment saying why and what it looked like. EmulatorJS
  then shows its own **START GAME** button; the tap is the gesture, the audio is allowed, the
  loop runs, the picture appears. The page also says, in one line, *“Tap **START GAME** below
  when you are ready.”*
* The frame now takes the console's **real shape** and is width-capped from it
  (`max-width: min(100%, calc(70vh * ratio))`), because `aspect-ratio` plus a height cap was
  leaving a Game Boy picture in a 3:2 letterbox with black bands at each side.

Proved in Chromium against the shipped code, on a throwaway instance of this same API with a
real `2048gb.gb`:

| | |
|---|---|
| before the tap | black frame, blue **START GAME** button, no canvas, `started: false` |
| after one tap on the button | `started: true`, canvas **622 × 560** (10:9), the 2048 title screen drawn, and the save state changes between two readings |
| the same on a phone-sized viewport (420 × 900) with a NES game added through the new public door | canvas **382 × 358** (16:15), the *Cyber Coaster* title screen drawn |

The picture was read back as an image, not inferred: each check saved a screenshot of the frame and
the title screens were looked at, not trusted from a byte count. Guards:
`test_arcade.py::test_the_game_waits_for_a_real_tap_before_it_starts` (the page must not contain
`EJS_startOnLoaded` and must say what to tap) and
`test_arcade.py::test_the_player_is_sized_on_a_frame_the_emulator_cannot_overwrite`.

**The lesson, again, one layer down:** last time it was “the box had no height”; this time it was
“the box had no gesture”. Both looked identical from `curl`. When a page does not work and every
URL is 200, ask the page — and ask it for something only a running emulator can answer.



### 20.3 A game you already have — now from the games page, for anyone

The owner asked for the upload door to be visible in the interface and open to people who have
not signed in. `http://ark.lan/arcade/add`, linked as **Add a game** from every games page and as
a button under the shelf:

| Piece | What it does |
|---|---|
| `GET /arcade/add` | the form, in plain words; or *“Adding games is switched off”* when the owner has closed it |
| `POST /arcade/add` | spools the file to `arcade/tmp` under a name the box chose (`spool_upload`, the same 64 MB cap and reserve as every other door), reads it, and shows **what the box made of it** — or refuses it before anybody types anything |
| `POST /arcade/add/confirm` | publishes through `ark_arcade_install.import_owner_file()` → `publish_candidate()`, the one gate, under the words the person confirmed; then sends them to the game |
| `POST /arcade/add/discard` | throws the waiting copy away; nothing was ever published |

**A visitor may add, and may never replace.** The owner's page can publish revision 2 of a file
it is given again; this door cannot, because a stranger must not be able to change the game
somebody else is playing. A title that resolves to a slug already on the shelf keeps the waiting
file and asks for another name; the same bytes under another name are named, not added twice.

Audit rows are `arcade-add`, actor *“a visitor”*, so the owner's history shows what came in this
way. The setting is **`public_arcade_upload`**, absent means **open** (a fresh box must be usable
without signing in), and the owner's Games page carries the switch **Anyone can add a game of
their own**. nginx gives that one exact path 68 MB (64 MB + framing) and streams the body on both
the public portal and the `:8445` player origin; every other path under `/arcade/` keeps its 20 MB
save limit.

Proved in Chromium on a phone-sized viewport against the real page: pick `cybercoaster.nes` →
*“Nobody has listed this file; the name on it is all this box has.”* with title `cybercoaster` and
console `nes` already filled in → **Add this game** → redirected to the game's own page with
*“cybercoaster” is on the shelf now* → exactly one file on the shelf, `arcade/tmp` empty → and the
game then **played** (screenshot above).

**Tests: 286 → 299.** New `tests/test_arcade_add.py` (10), one new player test in
`tests/test_arcade.py`, and two in `tests/test_config.py` tying the 68 MB door to `MAX_DOWNLOAD`
and keeping `/arcade/` at 20 MB.

**What the live box found the moment the door was tried.** The first real upload through the
proxy was `apotris.gba`, which is already on the shelf. It spooled correctly, the box said *“This
looks like "Apotris" (Game Boy Advance).”*, and nothing was published — but confirming it under a
**different** typed name answered *“… is already on the shelf”* about a game the visitor had never
named. The confirm step checked the slug built from the **typed** name while the publisher derived
the slug from the **recognised** one (`owner_candidate()` falls back to the prefill title), so the
two disagreed: a second homebrew game with the same recognised title could never be added, and a
rename was answered with the wrong sentence. Nothing on the shelf changed at any point. The
confirm step now hands the typed slug to the publisher (`slug: wanted`), so the name a person
types is the name on the shelf, the duplicate check and the pre-check agree, and the same bytes
under another name are still refused **by their fingerprint** with the name they already have.
New test: `test_arcade_add.py::test_the_name_the_person_types_is_the_name_on_the_shelf`. Proved
again in Chromium: `apotris.gba` renamed to *Apotris Deluxe* → `/arcade/g/apotris-deluxe`, one
file `apotris-deluxe.gba`, nothing waiting.

**Still not proved, and said out loud:** no *person* has yet added a game from their own phone to
the **live** shelf (the accepting proofs are a browser on the Mac against a local instance of this
API, plus a live refusal by fingerprint — §20.4), no **64 MB** file has crossed the real proxy,
there is **no per-person quota** on this door (the owner's switch is the control), and the `:8445`
copy of the page was only read through TLS on the box, never driven with a file.

### 20.4 Deployed on the box, 25 Oct 2026, and what the box said

Database aside first (`sudo sqlite3 … ".backup '/root/ark-db-before-addgame.db'"`, 233,472 B —
the schema does not change here, but `60-portal` restarts `ark-api`, so the copy is cheap and the
rule is the rule). Then `scripts/03-provision.sh ark.local --only 60-portal,65-admin` →
**`ARK_INSTALL_COMPLETE`**, all doors proved; then `--only 90-verify` → green, including the new
line **`✓ add a game (no sign-in) HTTP 200`** and *“everything a visitor touches is working”*.

| Check, on the live box | Answer |
|---|---|
| `http://ark.local/arcade/add` | **200**, and the games page carries the **Add a game** link in its nav and its button under the shelf |
| `https://ark.local:8445/arcade/add` (SNI, via `ark_tls_code`) | **200** |
| a real **4,194,304 B** `.gba` posted through the public proxy | spooled to `arcade/tmp/upload-<32hex>.gba` `0600`, the box answered *“This looks like "Apotris" (Game Boy Advance).”* |
| confirm it under its own name | **200**, *“There is already a game called "Apotris" on this shelf. Choose another name for this one.”* — the waiting copy kept so a new name can be given |
| confirm it under a **new** name | **400**, *“That one was not added: That game is already on the shelf as "Apotris".”* — refused by fingerprint, the waiting copy discarded |
| the shelf afterwards | **23** games, **23** files, `arcade/tmp` empty; audit `arcade-add / a visitor / failed / That game is already on the shelf as "Apotris".` |
| `/api/ready` | **200**, `ready 8 / of 8`, `blocking: []` |
| the player, a headless Chromium over `http://ark.local` at 420 × 900 | no `EJS_startOnLoaded` in the page; **before** the tap no state at all; **after** one tap on START GAME the state changes between readings, canvas **364 × 327** for `2048gb` and **364 × 242** for `apotris`, and the screenshots show the 2048 title screen and the Apotris title screen |

**Nothing was added to the live shelf**, so the 23 curated games are exactly as they were. What
this deploy therefore does **not** prove: a real new game arriving on the live shelf from a
visitor's phone (the accepting path is proved locally and in Chromium, and refused-by-fingerprint
is proved live), a **64 MB** file through the proxy (4 MB is what crossed), and a human's thumb.
Those are the remaining gaps, and they are the same three the owner's walk-through covers.


## 21. The paperwork caught up with the suite — 25 Oct 2026

§20 added thirteen tests (286 → 299: ten in the new `tests/test_arcade_add.py`, the
tap-before-start player test, and two doors for the game upload in `tests/test_config.py`), but
four places still advertised **286**: `README.md` (the test block), `docs/ADMIN.md` §12,
`docs/REQUIREMENTS-MATRIX.md` (its header, plus the add-a-game rows, which said *nine tests* when
the tenth — the typed name decides — had already been written), and the digest in `.clinerules`.
One line of §15 claimed "286 at the tip"; the tip is 299.

The per-file counts in `.clinerules` §8.5 had drifted further, because they are only edited when
somebody remembers: `test_config.py` said **3** and has **18**, `test_backup.py` said **17** and
has **22**, `test_arcade.py` said **26** and has **34**. They are measured now, file by file, with
`--collect-only`, and `test_arcade_add.py` (10) and `test_romdb.py` (15) have rows of their own so
the next change to either has an obvious place to land.

**Measured, this session:** `299 passed in 39.28s`; per file — accounts 14, arcade 34,
arcade_add 10, arcade_install 36, auth 19, backup 22, board 15, config 18, db 7, health 26,
import 7, network 29, public 7, romdb 15, search 8, storage 16, tls_probe 16 (17 files).

**Nothing on the box changed.** This was a documentation correction only: no deploy, no restart,
no schema, no code. The live box was read, not touched — `/api/ready` 200, `/arcade/` 200,
`/arcade/add` 200.



## 22. The box was audited against its own paperwork, and 64 MiB crossed the public door — 25 Oct 2026

Two jobs, both read-mostly: ask the live box every question the documentation already answers,
and make one real file go through the one public door that takes a large body.

### 22.1 The audit — every claim the paperwork makes, asked of the box

The whole audit was read-only (`curl`, `sqlite3` `SELECT`, `systemctl is-*`, `ls`). Nothing was
restarted, deployed or written.

| The paperwork says | What the box answered |
|---|---|
| `/api/ready` 200, `ready 8 / 8`, `blocking: []` | **200**, `ready 8 / 8`, `blocking: []` — each row with its plain sentence (20 collections and the reader answering, 1291 books and the shelf page answering, 11 manual pages, 0 messages, nobody has shared a file, 121.5 GB free, newest copy 15 hours ago, nothing waiting) |
| every public page 200 | `/`, `/help.html`, `/assets/ark.css`, `/search?q=water`, `/board`, `/files`, `/vault/`, `/vault/quick-card.html`, `/arcade/`, `/arcade/add`, `/recovery.txt`, `/api/status` all **200**; `/admin` **404** |
| the management door is `:8443`, TLS only, by **name** | `https://ark.local:8443/admin` → **303** (the sign-in redirect), `https://ark.local:8445/arcade/` → **200**, `https://ark.local:8445/admin` → **404** (see the probe trap below) |
| the book shelf is a reading door | `GET /books/admin` **404**, `GET /books/upload` **404**, `POST /books/upload` **404**, `POST /books/book/1` **403** |
| eleven units enabled **and** active | all twelve asked (`nginx`, `ark-kiwix`, `ark-calibre`, `ark-api`, `ark-boot-guard`, `ark-net-watch.timer`, `ark-zim-downloader`, `ark-book-import.path`, `ark-arcade-discover.timer`, `ssh`, `NetworkManager`, `avahi-daemon`) → `enabled` + `active` |
| schema **v5** | `SELECT MAX(version) FROM schema_version` → **5** (note: `PRAGMA user_version` is **0** — the version lives in the table, not the pragma) |
| 23 games, 23 files, `arcade/tmp` empty | **23** rows, **23** files on the card, `arcade/tmp` empty, `drop/files` empty, 0 `files` rows, 0 notes, 1 administrator |
| 45 suggestions waiting, no players yet | **45** candidates, **0** players |
| ~20 collections, 1291 books | **20** `.zim` files; **1291** books reported by the shelf, **444** of them `.epub` files on the card (the rest are other formats the shelf counts) |
| `/srv/ark/romdb.sqlite` 25,120,768 B `pi:pi 0644` | **25,120,768 B**, owner `pi:pi`, `56448` names |
| one copy, made 25 Oct | **1** bundle (`ark-backup-20261025-093643`), 15 hours old |
| the recovery sheet is the same file in three places | sha256 begins `81cb5362be9093bf` at `pi/RECOVERY-SHEET.txt`, `/srv/ark/RECOVERY-SHEET.txt` and `/srv/ark/portal/recovery.txt` — **identical** |
| `BOOT_MODE=hotspot`; the box is home **now**; no pin | `/etc/ark/ark.env` has `BOOT_MODE="hotspot"`; `ark-mode status` → `mode: home`, `chosen by: auto`, `at boot: hotspot (BOOT_MODE)`, `<HOME-IP>/24`, portal 200; **no** `/etc/ark/mode.pinned` |
| the switches are open on a fresh box | `public_board_post=1`, `public_uploads=1`, `arcade_players=1`, `arcade_auto_install=0`, `session_epoch=12` |
| the watchdog is ticking | `net-watch.state` rewritten at 00:18; **no `net-watch.log`** — because no decision has ever acted, which is what its own header says would happen |

**What disagreed.** Nothing about the box. One sentence about a file: `.clinerules` §8.3 listed
`/srv/ark/logs/net-watch.log` as if it were always there. It is created the first time a decision
*acts* — an empty log is not a quiet log, it is an absent one, and somebody reading the folder on a
healthy day would have concluded the timer was broken. The line says so now.

**A trap this audit walked into, and did not walk into twice.** The first TLS probe built
`--resolve "ark.local:127.0.0.1"` (host and address, no **port**), so all three doors answered
`000` on a box that was serving them perfectly. `curl` needs `host:port:addr`; the second probe
with `--resolve "ark.local:8443:127.0.0.1"` answered 303/200/404. This is §8.7's *"a TLS probe
must ask by NAME"* rule one field over — the rule is about SNI and this was about the port, but
the shape of the mistake is the same: `000` from a hand-written probe is a statement about the
probe until it is proved otherwise.

### 22.2 64 MiB through the public door, thrown away again

§8.9 item 0b left one wire unproved: *a 64 MB file over the real proxy*. The **`/arcade/add`**
location is the only public path that takes a body that size (68 MB in nginx = the arcade's own
64 MiB + framing), and — by design — a visitor's file is spooled to `arcade/tmp` under a name the
box chose before anything is judged, then either published or **thrown away** by a second tap. So
the proof can be complete and leave no game behind.

| Step | Evidence |
|---|---|
| a real file, made on the Mac | `dd if=/dev/urandom bs=1m count=64` → **67,108,864 B** `ark-64mb-proof.gb` (exactly the cap; `spool_upload` refuses only what is **larger**, so this is the boundary itself) |
| posted through the proxy from another machine on the LAN | `POST http://ark.local/arcade/add` → **200**, `size_upload` **67,109,085** B (the 221-byte difference is multipart framing), **8.15 s**, `proxy_request_buffering off` streaming |
| what the box made of it | *"Does this look right?"* — *"Nobody has listed this file; the name on it is all this box has."*, name `ark-64mb-proof`, console offered as a choice (unknown from the bytes), waiting as `arcade/tmp/upload-696a3e4b…gb` |
| thrown away | `POST /arcade/add/discard` → **303** to `?ok=That file was thrown away. Nothing was added.` |
| afterwards | games **23**, files **23**, `arcade/tmp` **0**, `drop/files` **0**, `/api/ready` **200** `8 / 8`, **122 G** free |

The only trace is the box's own receipt: two audit rows — `12 arcade-add / a visitor / ok / looked
at, not added yet` and `13 arcade-add / a visitor / ok / thrown away before it was added`. That is
the audit trail doing exactly its job on a visitor's attempt, so it was left standing and written
down here rather than tidied away. (`11` is the earlier live fingerprint refusal, *already on the
shelf as "Apotris"*.)

Rendering the confirm page had also to be proved: it read the 64 MiB file back to fingerprint it,
and the page answered in the same request — so the upload path is not merely *accepting* bytes, it
is reading them and reporting what they are, in one round trip, on a Pi with an 8 GB card budget.

**Still unproved, and unchanged by this:** a real **2 GiB** file over the wire (the File Box),
a **human's thumb** on any of it, a genuinely new game **accepted onto the live shelf**, and the
live radio transitions the watchdog exists for. `docs/HUMAN-TEST-LIST.md` is the one page that
now collects them.

---

## 23. Stage 7 — the household layer, and the paperwork that goes with a bag of pages — 25 Oct 2026

**Deployed 26 Oct 2026, green — see §24.** At this checkpoint `pytest` was **380 passed,
2 skipped** (21 files), the branch was `feature/admin-hardening`, the schema is untouched
(**v5**, no migration in this stage) and `90-verify` had *not* been asked about `/household`,
`/cards` or `/essentials.zip`. The deploy was deliberately small: `--only 60-portal,65-admin`,
then `--only 90-verify`, then a pack built on the card.

### 23.1 What a household gets

| Piece | Where | What it is |
|---|---|---|
| The household page | `http://ark.lan/household` (`main.household_page`) | Nine answers only a person can write (who lives here, where we meet, who to ring, where the water / power / gas stops, where the first-aid kit is, animals and medicine, anything else) and, under each of the **eight** emergency topics, the plain truth about when somebody last read the page that covers it |
| The eight topics | `pi/api/ark_review.py` `TOPICS` | breathing and bleeding, water, food, fire, power, toilets without water, cold/heat/shelter, small repairs — each names the vault pages that carry it (`manuals/first-aid.html`, `guides/electrical.html`, …) and the question the page must answer. A page the card does not have is **named as missing**, never assumed present |
| What was read | `settings` key `emergency_review` | one JSON row per topic: the day, the account, an optional note. *"Somebody here read this today."* / *"…214 days ago."* / *"Nobody has read this yet."*; `CHECK_EVERY_DAYS = 365` makes a topic **due for a look**. **Take that back** returns it to *nobody has read this*, audited `household-forget` |
| The cards for the wall | `http://ark.lan/cards` (`ark_cards`, `ark_qr`) | **three** cards — `http://ark.lan` (the box's own Wi-Fi), `http://ark.local` (a home network), `http://10.42.0.1` (the number that always works) — each with the sentence that says when it works. The square is drawn **in the page**: one inline `<svg>`, one `<path>`, no image file, no font, no script, no network |
| The pack to carry | `/srv/ark/essentials/ark-essentials.zip` → `/essentials.zip` | our own manuals and guides (every `/vault/…` link rewritten relative, so the pack opens from a folder), the quick card, `RECOVERY-SHEET.txt`, `START-HERE.txt`, its own `index.html`, and `MANIFEST.txt` with a sha256 per member — **16 members** on the box on 26 Oct 2026. Built as `.partial` and published by rename; **deterministic** (sorted members, fixed 1980 stamps), so two builds of the same shelf are the same bytes. The three **wall cards are not in it**; they print from `/cards` (§24.4 corrected three docs that said otherwise) |
| The owner's half | `https://ark.lan:8443/admin/household` (`ark_admin.household*`) | save the nine answers, **I have read this today** (+ note), **Take that back**, **Show the household page to everyone** (`public_household`, absent or `1` = open), and **Make a new copy now**. Audited: `household-save`, `household-switch`, `household-read`, `household-forget`, `household-pack` |

A topic nobody has read, a page the card does not carry and a box with no pack are all
**information, not faults**: none of them is in `BLOCKING_SECTIONS`, and none of them makes
`/api/ready` answer 503. That is the same rule the licence question follows (§17) — the box
says what it knows and refuses nothing.

### 23.2 The doors, and why two of them are proved by their words

`pi/nginx/ark.conf` names `/household`, `/cards` and `/essentials.zip` in the portal-pages
location, and `pi/nginx/ark-admin.conf` adds **one** non-`/admin` path to the management
origin — `location = /essentials.zip` — because the Household page that offers the link lives
behind that door and a link that 404s on the origin that offers it is worse than no link.

`location /` ends in `try_files … /index.html`, so **every unnamed address still answers 200
with the front page**. That is why `60-portal.sh` and `90-verify.sh` grew a `check_says()`
helper: the household page is proved by the words *"The page for the day something goes
wrong"* and the cards by *"Three addresses, three squares"*. `/essentials.zip` is proved by
its **content type**: 404 is honest (nobody has made a pack), a 200 must be
`application/zip` — a 200 that is `text/html` is the portal answering, not the pack, and that
is a failure.

### 23.3 The tests (83 new here — 382 collected, `380 passed, 2 skipped`; **85 new / 384 collected / `382 passed`** after §24.2's two fix tests)

| File | Tests | What it holds |
|---|---|---|
| `tests/test_household.py` | 13 | trimming and control characters, a refusal that names the limit (never a truncation), an unreadable stored row is an empty page and not a crash, `6 of 9` progress, and that a value is a sentence a person typed |
| `tests/test_household_page.py` | 34 | `/household` renders the nine answers escaped, a missing page is named, *"Nobody has read this yet."* vs *read 214 days ago*, a page older than a year is **due**, the switch turns the page off and leaves the cards, manuals and pack alone, `/cards` carries three cards and no external reference, the owner's door saves/reads/forgets/rebuilds, and **every topic names a file that really is in `pi/vault`** |
| `tests/test_qr.py` | 9 (+2 skipped) | structure with nothing installed (finders, timing, always-dark module, quiet zone, both copies of the format block read back) and — on a machine that has them — every module of versions 1–4 and all eight masks against `qrcode`, and the picture decoded back to its text by OpenCV. On the box the two cross-checks skip; the structure and the refusal still run |
| `tests/test_essentials.py` | 24 | a build is whole and says what is in it, two builds are the same bytes, `/vault/…` links are relative where the pack lands, a member over the size cap is refused, a damaged member fails `verify`, `.partial` is swept and never offered, a `START-HERE` and an `index.html` come with it, nothing is written outside `/srv/ark/essentials`, **the shelf's own front page does not ride along beside the pack's** (found on the Pi, §24.2), and a pack that would hold two files of one name is refused before anything is written |
| `tests/test_config.py` | +3 | the portal stays dark, large and self-contained: `--bg:#000`, ≥16 px body text, ≥44 px tap targets, and **nothing a browser would fetch** from outside the box in `index.html`/`help.html` (the box's own three addresses are stripped first, so written-out text like *open http://ark.lan* is not mistaken for a remote asset), and the two static pages start their body with a skip link that points at a `<main>` that really exists |

The portal-plainness test failed twice for the right reason before it passed: a bare `http://`
substring check trips on the sentence a person reads, so the test now strips the box's own
names and looks only at what a page would actually **fetch**.

### 23.4 The accessibility pass

`pi/portal/assets/ark.css` gained the skip link (first in the tab order, visible only while
focused), one visible 3 px focus ring on every link/button/field, `main{display:block}`,
`.line-breaks` so a typed answer keeps its line breaks, a full-width `.go-link` (50 px, the
download that is a link and not a form), the `.wall`/`.wall-card` print styles (white on
purpose — a scanner needs contrast), `prefers-reduced-motion`, and a print block that drops
the navigation, the popup and every `.go-link`. `index.html` and `help.html` gained the two
front-door cards (*This Household*, *Card to Get Back On*) and the skip link; neither page
loads anything from anywhere, which is what the new test asserts.

### 23.5 What reading the paperwork caught

Three statements that were wrong in the docs and are now right — each one a small, plausible
lie of the kind §8.7 collects:

* `README.md` and `pi/portal/index.html` described **nine** cards for the wall. The box prints
  **three** (`ark_household.CARDS`). Fixed in both, and the household test asserts the shape
  rather than a number in prose.
* `docs/ADMIN.md` §12 (*"Not in this release yet"*) still said *"no household page, no
  'download the essentials' pack, no QR cards"*. They exist now; the list says what is really
  still missing (no text-size or contrast controls, no translation, no reminder on the front
  page, no printed one-page version of the household's own answers).
* `pi/api/ark_qr.py`'s own docstring claimed the cross-check ran against **`segno`**; it runs
  against `qrcode` and OpenCV. A docstring that names the wrong library is how the next person
  installs the wrong package and believes the test skipped for a reason.

`docs/REQUIREMENTS-MATRIX.md` §D is now a 20-row acceptance table whose every row cites a test
that exists, plus the honest *"what is still missing"* paragraph; `docs/USING-THE-ARK.md` has
the real eight topics, the console commands (run as `pi`, no root) and the household page
described as a reader sees it; `docs/REBUILD-ME-ARK.txt` lists the two new doors, the pack
folder, the phase-60/65 additions and the body-based proof rule.

### 23.6 What was unproven before the deploy, and what is next

At this checkpoint nothing in this section had been seen by a browser on the Pi: `/household`
and `/cards` answering from the box's own nginx, a pack built on the card and verified, a card
scanned by a real phone camera, the `:8443` `location = /essentials.zip` under TLS, and
anything printed. The deploy (§24) closed the first three of those and found one real defect
doing it; the camera and the printer are still nobody's.

---

## 24. The stage 7 deploy — and the one zip that talked over itself — 26 Oct 2026

| | |
|---|---|
| Command | `bash scripts/03-provision.sh ark.local --only 60-portal,65-admin`, then `--only 90-verify` |
| Insurance first | `sudo sqlite3 /srv/ark/api/data/ark.db ".backup '/root/ark-db-before-stage7.db'"` → 233,472 B, 01:15. **No migration ran**; the layout is still **v5**, because stage 7 adds no schema |
| Result | both runs `ARK_INSTALL_COMPLETE`; `90-verify` → *“everything a visitor touches is working”* |
| New proofs in the deploy | `✓ household page — The page for the day something goes wrong`; `✓ printable cards — Three addresses, three squares`; `✓ the pack to carry — 404`; `✓ Household page behind the sign-in — 303`; `✓ page pack path answers (no pack made yet) — 404` |
| New proofs in the verifier | `✓ household page` and `✓ cards for the wall` (by body sentence), `✓ printable cards HTTP 200`; two soft lines, *none made yet — the owner makes one from their page* and *none yet* for the pack on `:8443` |
| `/api/ready` | **200**, `8 / 8`, `blocking: []` before and after |
| `/household` on the box | eight topics, each saying *Nobody has read this yet*, the nine answers still empty and admitted (*Nobody has written this page yet*), and *There is no copy to carry yet* under **Take it with you** |
| The pages behind the topics | all eight answer 200 from `/vault/…` (`manuals/first-aid`, `water`, `food`, `fire`, `power`, `sanitation`, `shelter`, `guides/plumbing`) |
| `/cards` on the box | three `<svg>` squares and **no external reference**: the only `http://` strings in the page are the box's own three addresses plus the SVG namespace |
| The fix, deployed | `--only 60-portal` again after §24.2 (**no migration**, same `.backup` still good) → `ARK_INSTALL_COMPLETE`, *“the pack to carry 200”*; then `--only 90-verify` → **`✓ everything a visitor touches is working`**, `✓ household page` / `✓ cards for the wall` by body sentence, `✓ the pack to carry — a zip, application/zip`, `✓ page pack to carry — 41.1 kB, 16 files, made 2026-10-26 01:21` |
| `/essentials.zip` after the fix | `200`, **`Content-Type: application/zip`**, `Content-Length: 42038` — the pack is now downloadable from `:80`, where it was honestly 404 before one existed |

### 24.1 The pack, built on the card — twice

The first build on the box, `sudo -u pi /usr/local/sbin/ark-essentials --build`, wrote
`/srv/ark/essentials/ark-essentials.zip` — **43.2 kB, 17 files** — printed
`UserWarning: Duplicate name: 'index.html'`, and `--list` showed `index.html` **twice**. That is
the first pack this box has ever made, and it is what exposed the defect below.

The same command, after the fix was deployed, wrote **41.1 kB, 16 files**, no warning at all,
`index.html` exactly **once**, and:

```
This pack is whole: every file matches its fingerprint
(/srv/ark/essentials/ark-essentials.zip).
```

The front page inside it is the pack's own — `<title>The Ark — essentials, on a stick</title>` —
not phase 4's generated shelf listing, which is the whole point of the fix. Sixteen members is
also the honest count: seventeen minus the shelf's `index.html` that no longer rides along.

### 24.2 Defect: a real shelf has an `index.html` of its own, and the pack has one too

The build said `UserWarning: Duplicate name: 'index.html'` and `--list` printed it **twice**.
Phase 4 writes a generated `index.html` at the top of `/srv/ark/vault` (the plain shelf index),
and `ark_essentials.build()` appends **its own** front page under the same name. `zipfile` only
*warns*; every unzipper keeps whichever member came last, so a pack could open on the shelf's
older listing instead of the pack's front page — silently, on the one file a person opens first
on a stick. The Mac's copy of the vault has no `index.html` (it is generated on the card), so
**no test could have caught this**; only building on the box could.

The fix, in the same commit: `collect()` leaves out the vault's **root** `index.html` on purpose
(a page called `index.html` inside a folder is content and still travels), and `build()` now
refuses outright when two members would share a name — `ValueError("this pack would hold two
files called … — nothing was written")`, raised before the `.partial` is created, with
`ark-essentials` printing it as *Not done: …* rather than a traceback. Two tests:
`test_the_shelfs_own_front_page_does_not_ride_along_beside_the_packs` (one member called
`index.html`, every name unique, and the front page is the pack's own) and
`test_a_pack_that_would_hold_two_files_of_one_name_is_refused` (nothing written, and the words
say why). **382 passed, 2 skipped.**

Proved on the box after deploying it, which is the only place it could be proved:

```
$ sudo -u pi /usr/local/sbin/ark-essentials --build
Wrote /srv/ark/essentials/ark-essentials.zip — 41.1 kB, 16 files, made 2026-10-26 01:21.
$ /usr/local/sbin/ark-essentials --list | grep -c index.html
1
$ /usr/local/sbin/ark-essentials --verify
This pack is whole: every file matches its fingerprint (/srv/ark/essentials/ark-essentials.zip).
```

No `UserWarning`, one `index.html`, no `.partial` left behind, and `unzip -l` counts **16 files**
against the pre-fix 17. The lesson, said plainly: **the box's vault is not the repo's vault** —
phase 4 generates an `index.html` at the top of `/srv/ark/vault` that `pi/vault` has never
contained, so a pack built on the Mac proves the format and nothing about this collision.

### 24.3 What the deploy does not prove

* **No camera has seen a square.** The cards are proved against an independent encoder and a
  decoder on the Mac (`test_qr.py`), and drawn on the box's own page — but no phone has scanned
  a printed card.
* **Nothing has been printed.** The print stylesheet, the white cards and the page breaks are
  read by a test and by the eye on screen only.
* **The pack has not been opened on a phone with the box switched off** — it is built, listed,
  `--verify`-proved and served as `application/zip` from the card, and its `index.html` and
  `START-HERE.txt` have only ever been read by the tools. Nobody has unzipped it on a device that
  is not the Ark.
* **No person has filled in the nine answers**, so every sentence on `/household` is still the
  page's own *Nobody has written this page yet*.
* The store is still empty of read marks: *I have read this* has only met the test suite.
* Nobody has looked at `/household`, `/cards` or **This household** in a browser. The deploy
  proves what nginx and the API answer, and the proofs for the two public pages are **body
  sentences** precisely because a 200 there means nothing (`location /` ends in
  `try_files … /index.html`).

### 24.4 Defect: three docs promised a `cards/` folder the pack has never held

Found by reading `ark-essentials --list` on the box, name by name, against what the paperwork
says. The real pack is fifteen pieces of payload plus `MANIFEST.txt`:

```
MANIFEST.txt  RECOVERY-SHEET.txt  START-HERE.txt  quick-card.html  index.html  manual.css
guides/electrical.html  guides/household-repairs.html  guides/plumbing.html
manuals/{fire,first-aid,food,power,sanitation,shelter,water}.html
```

Three documents said otherwise — `docs/ADMIN.md`'s *Where it is kept* table and its *what it
holds is public content* sentence, `docs/USING-THE-ARK.md`'s **Take it with you** bullet, and
`docs/REBUILD-ME-ARK.txt`'s `/srv/ark/essentials` block — each listing `cards/` (or "the three
wall cards") **inside the pack**. Nothing in `pi/api/ark_essentials.py` has ever written a
`cards/` folder: `grep -n cards` finds no match in the module. The design never asked for one
either (`.clinerules`'s stage 7 row and §D of the requirements matrix both say *the quick card,
the recovery sheet, `START-HERE` and `MANIFEST`*), so this was paperwork drifting ahead of the
product, not a missing feature — the same family as a page that shows a control which does not
exist (§8.6), one medium over.

**Fixed by correcting the words, not by bolting a folder on.** A wall card's whole purpose is to
be printed and stuck up *before* the box dies; buried in a zip on a stick it would be found on
the worst possible day. `/cards` is where it is printed, the household page links there from
**Take it with you**, and all three documents now say exactly that — including *"the three wall
cards are **not** in it"* in so many words, so the next reader does not have to diff a listing
against a sentence. `docs/REQUIREMENTS-MATRIX.md`'s pack row gained the quick card, the recovery
sheet and the same negative sentence.

**No test can catch this one**, and that is worth writing down: every member the pack really
holds is covered by `tests/test_essentials.py`, but a document claiming a member that does not
exist is invisible to pytest. What did catch it was listing the artefact and reading the
paperwork beside it — the same method that found the duplicate `index.html` in §24.2, an hour
earlier and on the same box. Both defects lived in the gap between *what the pack is* and *what
was said about the pack*.


---

## 25. Ask the Ark — a helper that reads this card and nothing else — 25 Oct 2026

Phase `58-ai` (the installer calls it *PHASE 5b/7*), and the one thing on this box that
answers a sentence nobody wrote down first. `http://ark.lan/ask`, public, no sign-in.
Everything it needs is on the card: an engine, one model file, and a page of facts about
what is actually here. The words never leave the box, because there is nowhere for them
to go — the model is a file and the engine has no route.

| Piece | What it owns |
|---|---|
| `pi/api/ark_ai.py` | the whole helper: the brief, the guide excerpts, one answer at a time, the off switch, the honest sentence when there is no brain |
| `pi/bin/ark-ai-serve` | waits for a `.gguf`, starts `llama-server` on the loopback address, restarts when a newer file lands |
| `pi/bin/ark-ai-model-fetch` | the only way a model arrives: `--from` a stick (no internet), `--url` with a fingerprint to check, `.part` → verify → rename, ownership handed to the folder's owner |
| `pi/bin/ark-build-ai-brief` | `--write` / `--dry-run` / `--show`: what is on the card, counted, as `pi` — never the household's nine answers, never the board, never the File Box, never the accounts |
| `pi/systemd/ark-ai.service` | `127.0.0.1:8089` only, `Nice=10`, `CPUWeight=40`, `MemoryMax=5500M`, `ProtectSystem=full`, `PrivateTmp`; **enabled and inactive** on a box with no model, which is the correct state and not a fault |
| `pi/steps/58-ai.sh` | installs the three tools + the unit, installs a carried engine if one is in the source tree, builds the brief as `pi`, says plainly what is missing |
| `pi/nginx/ark.conf` | `/ask` and `/api/ask/stream` — **`proxy_buffering off`** on the stream, so a sentence appears as it is written instead of after the last token |
| `pi/portal/index.html` | the front-page card; the switch closes the door it links to as well as the stream |
| `ark_admin.py` **Community** | `ask_the_ark` — *Anyone nearby can ask the helper a question*, on by default, and closing it leaves every other page working |
| `tests/test_ai.py` | 30 tests: no model ⇒ one honest sentence; a question trimmed and capped, an empty one never sent; both doors closed by one switch and both restored; the brief describes the card and cannot see the household; the fetch refuses a mismatched fingerprint; the serve picks the newest model and waits when there is none |

**The four rules the module keeps.** A question is never refused for *being* a question —
the only refusal is "there is no brain on this card yet", and it names what is missing.
The brief is **built, not remembered**, so the helper points at pages that exist. One
answer at a time, and the second person is told so (`BUSY_SENTENCE`) rather than waiting
for the box to crawl. And every answer ends with the sentence that says it can be wrong
and the guides are the truth — a helper, not an authority.

**A box with no model is a supported state,** and it is the state this box is in: no
engine is carried in this repo, `/ask` answers in one sentence, `/api/ready` does not
block on it, and nothing else is affected. `ark-ai-model-fetch --from /media/usb/…` is
the way a model arrives on a box with no internet, which is the normal state of a box in
a house.

**Unproven, and said so:** no model has ever been on this box, so no answer has ever come
out of it — the streaming proxy, the page's own script and an engine loading a gigabyte
have met the test suite and these docs and nothing else. `docs/HUMAN-TEST-LIST.md` §14 is
the walk-through for the day a model is carried in.


---

## 26. A power cut is not a reboot — phase `15-resilience` — 25 Oct 2026

A Pi in a house with an unreliable supply gets its plug pulled. Four things break, in
order of what they cost: **the clock is in the past** (no battery-backed clock, so every
"3 hours ago" is wrong and a copy can look like it came from the future); **a small file
is half written**; **a database will not open and the box runs on with an empty shelf**;
**half-built uploads and bundles pile up until the card is full.** Phase `15-resilience`
installs one tool that repairs all four at boot, and the operating-system settings that
make the *next* cut cheaper.

| Piece | What it does |
|---|---|
| `pi/api/ark_self_heal.py` | the only place that knows what a cut leaves behind: `fix_clock()` (the newest mtime on the card is the best evidence of the real time; `CLOCK_SLACK = 120 s`), `sweep()` (temp files and `.partial` folders by **pattern and age**, never by guess), `heal_json()` (`ark_status.json`, the import ledger: the broken file is set aside and `<name>.bak` put back if it reads), `heal_state()` (the watchdog's counters — §26.1), `heal_sqlite()` (`quick_check` on a read-only open; only ever replaced by a copy that passes the same check), `heal_library()` (a catalogue listing books that are not there), `run()` = the pass and one report |
| `pi/api/ark_atomic.py` | write-then-rename, with the `.bak` of the version before it — shared by every small file the box rewrites |
| `pi/bin/ark-self-heal` | `--dry-run` (says what it would do, changes nothing), `--json` (what the verifier reads), `--quiet` (only what was actually repaired). Exit 1 means *something is left for a person*, never "the pass crashed" |
| `pi/systemd/ark-self-heal.service` | `Type=oneshot`, `User=root`, `After=local-fs.target`, **`Before=ark-api.service`** — the clock is fixed before a page can read it. `TimeoutStartSec=300` |
| `pi/systemd/ark-self-heal.timer` | once a day at 04:17 (`RandomizedDelaySec=20m`, `Persistent=true`): the boot pass catches the cut, this catches a failing card while the box is up |
| `pi/steps/15-resilience.sh` | installs all of it, then: `fake-hwclock` (a cold boot no longer starts in 1970), `vm.dirty_writeback_centisecs = 1500` and lower swappiness, **tmpfs for `/tmp` and `/var/tmp`** (a cut then has nothing half-written to leave behind), `fsck.mode=force fsck.repair=yes panic=10` on the kernel command line **and the line re-checked for one line only**, a volatile journal (a corrupt journal is the one thing that can stop journald at boot), `RuntimeWatchdogSec=15s` (a hung box reboots itself), `Restart=always` + `StartLimitIntervalSec=0` on every long-running unit, and WAL + `synchronous=FULL` + `quick_check` on Calibre's two databases |
| `pi/steps/90-verify.sh` | reports the last pass and fails only if it **left something for a person**: *no pass recorded yet* is a normal box, a non-empty `problems` is a fault |

**The rules it keeps, which are the reasons it cannot do harm.**

* **It never deletes a database** and never touches a file it does not recognise. A row
  and its file can disagree on purpose (`ark-restore --no-files`), and "tidy up" is how
  data loss gets dressed as maintenance (§8.7, one layer up).
* **Recovery is only ever from a copy that passes the check** the damaged file failed — a
  `.bak` that will not read is set aside, not put back.
* **A repair is not a problem.** Anything put back, set aside or swept counts as
  `healed`; only what is left needing a person lands in `problems`, and only a non-empty
  `problems` makes the verifier say a bad word.
* **Everything is written down**: `logs/self-heal.log` (a line at a time, fsynced,
  rotated at 256 kB with one previous file) and `logs/self-heal.json` for a machine.
* **It never restarts a service.** The boot order does that; a repair tool that starts
  units is a second writer to the same state.

**The settings are the half that prevents the fault instead of repairing it,** and they are
the half a future reader is most likely to call unnecessary: a 15-second write-behind
window is why a cut costs seconds of writes instead of thirty; tmpfs is why there is
nothing half-written to sweep; `fsck.mode=force` is what turns a damaged card into a
repaired one instead of a box that boots into a shell nobody can reach over the network;
the hardware watchdog turns "the box is hung and the whole house is offline" into "the box
rebooted by itself".

**Unproven, and it is the important part:** this has never met a real power cut. Every test
drives the pass against a throwaway folder tree, and the phase has not been run on the box
at all yet (§8.9). Pulling the plug on a live box is the human test —
`docs/HUMAN-TEST-LIST.md` §15.

### 26.1 Defect: the watchdog's counters are not JSON, and every boot threw them away

`logs/net-watch.state` was listed in `JSON_TARGETS`, so the boot pass read it with
`json.loads` — and it is three numbers as `key=value` lines:

```
lost_streak=0
home_streak=3
home_block_until=0
updated=2026-10-25T09:21:06+00:00
```

`json.loads` raises on the first character. The pass therefore concluded that a
perfectly healthy file was damaged, **set it aside at every single boot**, and started the
watchdog from zero — silently, because a repair is not a problem and `--quiet` says
nothing about a repair that "worked". Two consequences, both real: those counters are the
watchdog's memory of *how long* the network has been gone and *when* it last tried a
return, so a box rebooting in a flapping power cut could never see the second lost check
that a rescue needs; and `logs/` quietly accumulated `net-watch.state.corrupt-<stamp>`
files, up to `CORRUPT_KEEP` of them, looking like evidence of a fault that did not exist.

The tests hid it, which is the more interesting half: the two tests that needed *a small
file that came back unreadable* had both used the counters file as their fixture, so they
were asserting against the bug rather than around it. They use `ark_status.json` now,
which is really JSON.

**The fix is a shape check of its own** (`state_problem` + `heal_state`), because the file
is not JSON and never was: `STATE_KEYS = ("lost_streak", "home_streak",
"home_block_until")`, every key present and numeric, an extra `updated=` ignored on purpose
(a timestamp for a person, not a counter). A file that fails is **set aside, never
invented** — the counters restart at zero, which `decide()` already reads as "nothing has
been seen yet", so the only possible consequence is a *slower* rescue, never a wrong one.
It counts as `healed`, and `report["state"]["state"]` says `fine` / `absent` /
`set aside` / `would`.

**Three smaller changes went with it, each closing the same door from another side:**

* `ark-net-watch`'s `write_state()` writes `logs/.net-watch.state.tmp`, `sync`s, and
  `mv -f`s it into place — a cut can no longer leave half a counter file for the sweep to
  find. (It had been `printf … >"$STATE"`, which is exactly the shape a cut truncates.)
* `sweep()` learned `logs/.*.tmp`, so a dot-temp file left by a cut is *cleared* instead
  of surviving as a permanent mystery in `logs/`.
* `90-verify.sh` copies the receipt it is about to replace to `ark_status.json.bak` before
  writing the new one. `heal_json`'s whole *put the old version back* branch depended on
  that copy existing and the only writer of `ark_status.json` was not making one, so the
  fallback could only ever have said "set aside". Now it can really restore.

**Guards.** `tests/test_self_heal.py`: `test_healthy_counters_are_left_alone` (and it
asserts `logs/net-watch.state not in heal.JSON_TARGETS`, so nobody files it back under
JSON), `test_damaged_counters_are_set_aside_and_the_watchdog_starts_again` (the bytes are
kept as `.corrupt-*`), `test_counters_that_are_not_numbers_are_damage`,
`test_missing_counters_are_not_a_fault` (`absent`, not damage),
`test_a_half_written_counter_file_is_swept_and_the_real_one_is_not`,
`test_the_verifier_keeps_the_receipt_from_before` (the copy is taken *before* the replace);
and `tests/test_network.py::test_the_watchdog_writes_its_counters_in_one_move` (the temp
name, the redirect into it, the one `mv`, and **no `>"$STATE"`** anywhere). **467 passed,
2 skipped.**

**The lesson worth keeping:** *a file's shape belongs to its writer, not to the folder it
lives in.* `logs/` holds a JSON receipt and a `key=value` counter file side by side, and
treating the folder as a category is what made a healthy file look broken every morning.
It is the same family as `-1` not being `0`, `None` not being a measurement, and an
absent log not being a broken timer.

**Next step (recorded 26 Oct 2026, after the docs pass).** The docs are reconciled and this
whole checkpoint is committed and pushed — `8cca31b` on `feature/admin-hardening`, level with
`origin`, working tree clean, **467 passed, 2 skipped, 23 files**. Every count in `README.md`,
`docs/ADMIN.md`, `docs/REQUIREMENTS-MATRIX.md`, `docs/HUMAN-TEST-LIST.md` and `.clinerules` was
checked against `pytest` and the folder, and the three sentences that had drifted were corrected
(the essentials pack's real 16 members, the mixed-shebang list, and the deploy claim that had
silently covered `58-ai`). What is left is **the box, and it needs the owner's go-ahead**: take
the database aside (`.backup`, not `cp`), deploy `15-resilience` and `58-ai` (§8.9), reboot once
and read `/srv/ark/logs/self-heal.json`, then carry a model with `ark-ai-model-fetch` and ask
`/ask` one question from a phone. None of that has ever run there — no power cut, no reboot with
the phase, no model on the card.


## 27. A confirmed save is on the card, and a save is never overwritten — 26 Oct 2026

Recorded after the commit (`e0b20f1`), which arrived with tests but without a line here. Two
faults a power cut could reach, and both were found by reading the code a cut would land in
rather than by watching a box die.

**Synchronous writes.** `ark_db.connect()` left SQLite at its default `synchronous=NORMAL`,
which reports a commit as soon as the bytes are in the kernel's cache — so a note that was
posted, or a save that was confirmed, could be gone after a cut with nothing in any log saying
so. `ark_self_heal.harden()` had already set `FULL`, but only on the repair's own handles, never
on the connection the API writes the records through. The pragma is per-connection, so it is
set **on the connection** now, and a test reads it back from the connection the app really uses
and fails if any module weakens it.

**Saves.** A save used to be one file per slot, overwritten in place, so a cut between "file
written" and "row committed" left a row naming bytes that were no longer there — and the reader
then refused a save that had worked a moment before. A save's bytes are content-addressed now:
`saves/<player>/<game>/blobs/<slot>-<sha256>.bin`, staged in the save's **own** folder as
`.save-*.part` (`mkstemp`, so a leftover can never be mistaken for a save), fsynced, renamed,
and the folder fsynced — then the row is pointed at them with a compare-and-set on `revision`,
and only after that commit is the file it replaced removed. A cut at any point leaves either the
old record describing the old bytes or the new record describing the new bytes. Nothing a person
sees changes: the name a page shows is still `state-01.state`, a carried-away zip still holds
`saves/<game>/state-01.state`, and rows written before the change (`blob = ''`) are still read
from the old slot file and retired only when a new save replaces them.

Schema **v6** is additive: `player_saves.blob TEXT NOT NULL DEFAULT ''`. The live card is at
**v5**, so this migration has never run on it.

**Guards.** `tests/test_durability.py` (15): the pragma read back from the app's own connection,
no module allowed to weaken it, a commit visible to a connection that was not there when it was
written, the cut between bytes and record (both copies on the card, the last good save still
read), a replaced save forgotten only after the record moved, an identical save twice, an
abandoned blob cleared only once old, a record whose bytes are gone answering *nothing* rather
than a lie, a row naming a path outside its folder refused, an old-layout save read and then
retired, staged-beside-itself plus a fsynced folder, a failed write leaving nothing that looks
like a save, and the export carrying the bytes the records name. `tests/test_arcade.py` asserts
the blob layout. **482 passed, 2 skipped** at that commit.

**Unproven, as ever:** no cut, no card, no phone. A *real* `synchronous=FULL` write has never been
watched landing on that SD card, and the compare-and-set has only ever met a test's two
connections.

---


## 28. A boot that does not wait for the internet, and a 2 GiB upload that waits on the card — 26 Oct 2026

Two promises were true in the paperwork and false in the machine: *"works with the internet
unplugged"* and *"a 2 GiB File Box"*. Neither was a missing feature. Both were small, quiet
things a phase had made untrue a layer underneath.

| Piece | What it does |
|---|---|
| `pi/systemd/ark-api.service`, `ark-kiwix.service`, `ark-calibre.service` | `After=`/`Wants=` **`local-fs.target`**, and no `network-online.target` at all. The API listens on loopback and every visitor arrives through nginx; kiwix and calibre are loopback-only behind `/kiwix` and `/books`. Waiting for "the internet is up" held the whole portal behind NetworkManager's check — about a minute on a home network, and the full timeout on a box whose entire point is working without one |
| `pi/nginx/ark.conf`, `ark-admin.conf`, `ark-players.conf` | `client_body_temp_path /srv/ark/tmp/nginx 1 2;` — nginx buffers a request body **to disk** before a byte reaches the backend, and since phase `15-resilience` `/tmp` is a **256 MB tmpfs**. A 2 GiB upload parked there would fail at 256 MB with "no space left", or take the box down with it |
| `pi/systemd/ark-api.service` | `Environment=TMPDIR=/srv/ark/tmp` and `ReadWritePaths=… /srv/ark/tmp` — Starlette buffers an upload in a `SpooledTemporaryFile` and rolls over to a real temp file after 1 MB, and the unit has `PrivateTmp=yes`, so its `/tmp` is that same 256 MB of RAM. `TMPDIR` is the only knob |
| `pi/steps/20-storage.sh`, `60-portal.sh`, `65-admin.sh` | `$ARK_ROOT/tmp` is created (`775`, the `pi` account) in **all three** phases: 20 because it belongs to the tree, 60 and 65 because `--only 60-portal` or `--only 65-admin` may be the first phase to run on a box installed before the folder existed — and nginx refuses to start with a missing `client_body_temp_path` parent, so the folder has to exist **before** `nginx -t` |
| `pi/bin/ark-fsck-once` | `--dry-run`, `--quiet`, `--cmdline F`, `--help`. It removes `fsck.mode=force` and `fsck.repair=yes` from the boot line and **refuses to write anything that is not still exactly one line** and is not the old line minus those two words — a newline in `cmdline.txt` stops the Pi dead with nothing on the screen |
| `pi/systemd/ark-fsck-once.service` | `Type=oneshot`, `RemainAfterExit=yes`, `User=root`, `After=local-fs.target`, `Before=ark-self-heal.service` — the check has already run by then, so the line is withdrawn before the repair pass reads the card. Enabled **for ever**, on purpose |
| `pi/steps/15-resilience.sh` | installs the tool, installs and **enables** the unit, and deliberately **does not start it**: the check it withdraws belongs to the *next* boot, which has not happened yet, and starting it now would quietly cancel the very check the phase had just arranged. Then it proves the tool against the real `cmdline.txt` with `--dry-run` (nothing changed) |
| `pi/steps/90-verify.sh` | a **card check** line: *the forced check has been taken back off the boot line* (green), *the next boot checks the card; the boot after that will not* (soft — a box that has not rebooted since the phase is **waiting**, not broken), or *not installed — run phase `15-resilience`*. `ark_status.json` gains `"card_check": withdrawn\|waiting\|absent` |

**Why the check is withdrawn at all.** `fsck.mode=force` on the kernel command line is what
makes the *first* boot after a cut check a card that may have been mid-write. Left there it makes
**every** boot afterwards wait for a full check of a card that was already checked: minutes of a
box that looks dead, on every reboot, for ever — on a box whose recovery story is "pull the plug,
plug it back in". The check belongs to one boot; `ark-fsck-once` is the hand that takes it back,
and it is idempotent: a boot with nothing to withdraw says so and changes nothing.


**Three repairs the same work needed in `pi/api/ark_self_heal.py`.** The phase's own promises
were partly untrue too, and the daily pass is the one place where "true at boot" and "true at
04:17" are different things:

* **A writer on the database.** The boot pass runs before `ark-api` exists; the 04:17 pass runs
  while the box is serving pages. A pass that hardened or snapshotted a database another process
  held was writing under a live writer — so `writer_busy()` (a 0.2 s write-lock attempt) now
  answers `in use` for a sound database that is held (*"it was only checked — nothing on it was
  changed"*) and `busy` for a broken one (*"nothing was changed — … run the pass again when the
  box is quiet"*, and it is what a person must look at). Repair happens at boot; a daily look is
  a diagnosis.
* **A dry run wrote.** `heal_sqlite()` tested `dry_run` *after* the healthy branch, so a
  `--dry-run` on a healthy database still changed its journal mode and **took a copy of it** —
  and the phase runs `--dry-run` to prove the tool, where a proof that alters the card is not a
  proof. The dry run is now the first thing after `quick_check`, and it writes nothing at all: no
  copy, no mode change, no sweep.
* **A clock that believed a file from the future.** A cut leaves the clock in the past, and the
  newest mtime is then the best evidence of the time. A file stamped *years* ahead is not
  evidence — it is evidence that the clock was already wrong when that file was written — so
  `clock_too_far()` (`CLOCK_MAX_AHEAD = 400 days`) leaves the clock alone and says so through
  `problems`, where a person can see it. Following it would put the certificate and every
  "3 hours ago" on the box out by years.

**Guards.** `tests/test_self_heal.py` (66 now) adds
`test_a_dry_run_writes_nothing_at_all`,
`test_a_dry_run_on_a_broken_database_reports_and_restores_nothing`,
`test_a_real_writer_stops_the_pass_touching_its_database`,
`test_a_broken_database_the_box_is_writing_to_is_left_where_it_is`,
`test_the_pass_reports_a_database_it_could_not_repair_because_it_was_in_use`,
`test_a_file_from_the_far_future_is_not_trusted_with_the_clock`, seven for the new tool:
`test_the_forced_check_is_withdrawn_from_the_boot_line_once`,
`test_a_later_boot_has_nothing_to_withdraw_and_changes_nothing`,
`test_a_cmdline_that_is_already_two_lines_is_left_alone`,
`test_a_line_that_would_lose_root_is_never_written`, `test_a_dry_run_withdraws_nothing`,
`test_the_unit_runs_the_withdrawal_on_the_boot_that_used_the_check`,
`test_the_phase_installs_the_withdrawal_and_does_not_run_it_now` — and one for the gate
itself, `test_the_verifier_reports_the_forced_check_without_failing_a_box_that_waiting`
(the receipt carries `card_check`, and *waiting* is a soft line, never a fault).
`tests/test_config.py` (30 now) adds
`test_nothing_a_visitor_waits_for_waits_for_the_internet` (the three units, comment lines stripped
before the search — an explanatory comment must not pass for a dependency),
`test_only_the_fetching_units_wait_for_the_internet` (a unit that really fetches keeps
`network-online.target`: `ark-zim-downloader`, `ark-arcade-discover`),
`test_a_body_on_its_way_to_disk_waits_on_the_card` (all three vhosts, the folder, and the
`levels`), and `test_the_app_spills_an_upload_onto_the_card_too` (`TMPDIR` **and** it inside
`ReadWritePaths`). **504 passed, 2 skipped, 24 files.** `bash -n` on the six shell files and
`python3 -m py_compile` on the module.

**Unproven, and it is the same list as always:** none of this has run on the box. No reboot has
been taken with `ark-fsck-once` installed, so no `cmdline.txt` has been rewritten by it and no
second boot has been seen *not* checking the card; `/srv/ark/tmp/nginx` has never been created by
a real nginx, and no upload has crossed either `/srv/ark/tmp` path at size; and no 2 GiB body has
crossed the proxy (the largest ever seen is 64 MiB, §22.2). One deliberate limit: nginx's own
temp files from an interrupted request live in `tmp/nginx/<a>/<b>/` and the repair pass does not
sweep them — its `sweep()` rules are one folder deep by design, and a recursive delete inside the
one tool that must never lose anything is not a trade worth making for a few megabytes.

**The lesson worth keeping:** *a promise about a size or a boot is a property of the folder and
the unit, not of the code path.* `client_max_body_size 2064m` and "works offline" were both
written down and both true of the app — and a 256 MB tmpfs and a `Wants=network-online.target`
made them false a layer underneath.

## 29. The deploy of §25–§28 — and four things the card already knew — 26 Oct 2026

**What was run.** Pre-flight first, because this deploy reaches a schema change: the live
database was at layout **v5**, so `sqlite3 … ".backup /root/ark-db-before-v6.db"` (233,472 B,
`PRAGMA quick_check` → `ok`), then `/root/ark-etc-before-v6.tgz` (fstab, nginx, systemd units,
`ark.env`, `cmdline.txt`), `/root/ark-src-before-v6.tgz` (11.8 MB) and separate copies of
`/root/fstab.before-v6` and `/root/cmdline.before-v6.txt`. Then, from the Mac:

```
bash scripts/03-provision.sh ark.local --only 15-resilience,20-storage,30-calibre,50-kiwix,58-ai,\
                                       60-portal,65-admin,70-network,95-backup,90-verify
```

**Deploy 1 failed, and every failure was worth having.** `15-resilience` died on its own guard
and `90-verify` failed on two rows. Four defects came out of it, all four are fixed, all four now
have a test:

* **A tmpfs mounted by hand is a state, not a setting.** `/tmp` on the card was a 3.9 GB tmpfs
  with **no line in `/etc/fstab`** (mounted by hand in an earlier session), and `/var/tmp` was
  still on the card. The phase's old branch read the *live mount*, said "the throwaway folders
  already live in memory" and skipped its own `fstab` edit — so the next reboot would have put
  `/tmp` back on the card while the phase reported success, and `/var/tmp` would never have been
  memory at all. The decision now reads the marker line in `/etc/fstab`; a live mount gets one
  sentence ("that is a state, not a setting") and nothing else; both tmpfs lines carry `nofail`.
  Guard: `tests/test_self_heal.py::test_the_memory_folders_are_judged_by_what_persists_not_by_what_is_mounted`.
* **An unanchored `sed` appended to every line, and the guard ran after the write.** The image
  ships `cmdline.txt` as 133 bytes ending in a blank line (`…regdom=US\n\n`, so `wc -l` = 2), and
  `sed -i "s/[[:space:]]*$/ $opt/"` put the option on **both** lines: the file grew a second line
  holding nothing but our options, and the old `wc -l > 1` check then fired *after* the write and
  killed the phase. On the card that was
  `…regdom=US fsck.mode=force panic=10\n fsck.mode=force panic=10\n` — a boot line nobody had
  proved. It was repaired from `/root/cmdline.before-v6.txt` to one line (132 B, mode 755 kept),
  and the phase now **installs `ark-fsck-once` first**, reads line 1 only, appends to that line,
  proves *one line with `root=` still in it* in a temporary file, writes in place, and proves one
  line again afterwards. Guards:
  `test_the_boot_line_is_built_apart_and_proved_before_the_card_is_touched` and the ordering
  assertion inside it (the withdrawal is enabled before the check is arranged).
* **`fake-hwclock.service` does not exist.** Trixie ships `fake-hwclock-load.service`,
  `fake-hwclock-save.service` and `fake-hwclock-save.timer`; enabling the package name answers
  `not-found`, so a healthy box got a warning every run. The phase now enables what is really
  installed (3 units on this card). Guard:
  `test_the_clock_units_are_the_ones_the_package_really_ships`.
* **`ask_section()` said `content: True` about a box that has never been fitted.** `90-verify`
  reads `content` as "there is something here that cannot be served", so the gate **failed a
  healthy box** for the state this project calls supported. A helper with no engine, or with no
  words, now reports `content=False` ("still filling up", a soft line); `content=True` is kept for
  the one case that deserves a failure — words on the card, not answering. Guards:
  `test_the_helper_is_reported_on_the_owner_page_and_never_blocks_readiness` (extended) and
  `test_a_model_that_is_here_but_silent_is_the_one_case_worth_a_failure`.

**Deploy 2** (`--only 15-resilience,60-portal,90-verify`) finished `ARK_INSTALL_COMPLETE` in 40 s,
`90-verify` → *"everything a visitor touches is working"*, `/api/ready` **200**, `8 / 9`,
`blocking: []` (the ninth row is the helper, honestly `ready: false, content: false`).

**What the card now says about itself** — all of this measured on the box, 12:05–12:15:

| Fact | Evidence |
|---|---|
| Layout **v6** on the live database | `select max(version) from schema_version` → `6`, after the `.backup` above |
| The boot line is one line | `…regdom=US fsck.mode=force panic=10`, 1 line, 9 words |
| The forced check belongs to one boot | `ark-fsck-once --dry-run` → *"Would withdraw fsck.repair=yes fsck.mode=force from cmdline.txt; nothing has been changed."*; the unit is `enabled` / `inactive` |
| The memory folders persist | `/etc/fstab` carries two `tmpfs … size=256M,mode=1777,nofail` lines; `findmnt --verify` → ok |
| The cut settings | `RuntimeWatchdogUSec=15s`; `Storage=volatile`; `vm.dirty_writeback_centisecs=1500`, `vm.swappiness=10`, `kernel.panic=10`; `fake-hwclock-load.service` + `fake-hwclock-save.timer` enabled |
| Repair ran, found nothing wrong | real pass 12:10:51 → `healed: 0`, `problems: []`, three databases `fine` (each with a `good.bak`), `romdb.sqlite ok`, `sweep: []`; `--dry-run` afterwards → nothing to repair; timer `enabled` + `active` |
| The body parks on the card | `client_body_temp_path /srv/ark/tmp/nginx 1 2` in `sites-available/ark`, `-admin`, `-players`; `Environment=TMPDIR=/srv/ark/tmp` on `ark-api`; `/srv/ark/tmp/nginx` exists, `www-data`, `0700` |
| Nothing waits for the internet | `systemctl show -p After` on `ark-api`, `ark-kiwix`, `ark-calibre` → `local-fs.target` in all three |
| The helper is enabled and idle | `ark-ai.service` `enabled` + `inactive`; `/ask` → 200 with *"The helper is not fitted on this box"* |
| Every door | `/` 200, `/arcade/` 200, a game page 200, its ROM `HEAD` 200, `/arcade/add` 200, `/books/` 200, `/household` 200, `/cards` 200 (3 `<svg>`), `/ask` 200, `/files` 200, `/board` 200, `:8443/admin` 303, `:8445/arcade/` 200, `/admin` on `:8445` 404, plain HTTP to `:8443` 400, `/essentials.zip` 200 |
| The tools read, and write nothing | `ark-backup --dry-run` → *"Nothing was written"*; `ark-essentials --verify` → *"This pack is whole"*; `ark-arcade --status` → 23 games, 0 players, 45 suggestions |

**Proved by hand, on the card — the first save that has ever lived there (§27).** Through
`https://ark.local:8445` with `--resolve`, exactly as a browser would arrive:

1. a name was made on the player origin only → 303 to `/arcade/?ok=Your+name+is+ready…`;
2. `PUT /arcade/api/saves/1/state/1` with 45 bytes → 200, `sha256 9d0e184c…` — **the bytes that
   were sent**;
3. on the card: `/srv/ark/arcade/saves/1/1/blobs/state-01-9d0e184c…bin`, 45 B, folder `700`, file
   `600`, and the row's `blob` names that file — note **player id / game id**, not names, which
   is what the code does and what three documents had wrong (§30);
4. `GET` the same slot → those same 45 bytes;
5. a second `PUT`, different bytes, `X-ARK-Revision: 1` → 200, **revision 2**, a **second**
   immutable blob, the replaced one gone — the save was never overwritten;
6. a stale page (`X-ARK-Revision: 1` again) → **409**; no player cookie → **401**;
7. `DELETE` the save → 200; the probe's rows and folder were then removed → **0 players, 0 saves,
   0 prefs, 0 sessions**, `saves/` empty, still 23 games, `/api/ready` 200.

**Proved by hand, on the card — a body larger than the tmpfs (§28).** 300 MiB
(314,572,800 B) posted to `/files`: **303** in 13.8 s (≈20 MB/s), the row and the file both
exactly 314,572,800 B, `drop/tmp` and `tmp/nginx` empty afterwards, and — sampled every 0.25 s
across both filesystems — **`/tmp` (RAM) +0 MiB at peak, the card +600 MiB** (300 MiB of the
app's spooled temp file, which Python creates *unlinked* so `du` cannot see it, plus 300 MiB of
published file; `df` is the only honest measure here). Both probes were removed afterwards: 0
file rows, 0 files in the folder, 118 GiB free. So the reason `/tmp` becomes a 256 MB tmpfs is
now measured rather than argued, and the upload path does not use it.

**Still unproven, and no amount of curl can change it:** the **reboot**. `fsck.mode=force` has
never run on that card, `ark-fsck-once` has never rewritten a real `cmdline.txt`, the
`local-fs.target` ordering has never been observed from a cold start, the tmpfs folders have
never been mounted from `fstab`, and no power cut has been simulated. `docs/HUMAN-TEST-LIST.md`
§15 is the walk-through; the first reboot is expected to take minutes and every reboot after it
must be fast.

**Tests:** **508 passed, 2 skipped, 24 files.** `bash -n` on the phase, `python3 -m py_compile`
on `ark_health.py`.

## 30. Two sentences the paperwork had wrong, found by measuring the card — 26 Oct 2026

Both of these were written down confidently in `.clinerules` and `docs/ADMIN.md` and both were
wrong about the box. Neither is a code defect; both would have cost somebody an afternoon.

* **A save's folder is named by id, not by name.** The documents said
  `/srv/ark/arcade/saves/<player>/<game>/`, and the save probe on the card landed in
  `/srv/ark/arcade/saves/1/1/blobs/state-01-9d0e184c….bin` — `new_player_save_dir(player_id,
  game_id)` in `pi/api/ark_arcade.py` is the only thing that decides this, and it uses the two
  numbers. A person looking under `saves/alice/tobutobugirl/` would find nothing and conclude the
  saves were not being kept. Corrected in `.clinerules` §8.3, §8.11 and §8.12 and in
  `docs/ADMIN.md`; the reader-facing sentence ("your saves are kept on this box") never named a
  path, so nothing a reader sees changed.
* **The box does have a route to the internet right now.** `.clinerules` §8.9 and §8.11 both said
  "the Pi has no route to the internet" — and this deploy installed `fake-hwclock` from
  `deb.debian.org` (7,208 B, `Get:1 … trixie/main arm64`), and `https://deb.debian.org/` answered
  **200** from the box minutes earlier. What is true is the thing that matters and is unchanged:
  **nothing this box serves needs the internet**, the model and the games are files, and the
  library is fetched or carried. Whether a cable is in the wall today is not a fact to build a
  page or a script on.





## 31. "The helper is not fitted on this box" — and it was telling the truth, 26 Oct 2026

The owner reported one sentence: *"Any attempt to ask Gemma a question throws: The helper is not
fitted on this box."* Every library, book, guide and game was still there and still working — as
the page itself says. The sentence was accurate, and two things were missing rather than one.

| What the box looks for | What was on the card |
|---|---|
| `/usr/local/bin/llama-server` | nothing — but a working arm64 build sat in `/home/pi/llama.cpp-build/build/bin/llama-server` (v0.7.0-dev, only system libraries, `ldd` clean) |
| `/srv/ark/ai/models/*.gguf` | `google_gemma-4-E2B-it-Q4_K_M.gguf.part` — 3,462,680,032 B, **a complete** GGUF v3 download (`general.architecture = gemma4`, 601 tensors), fetched by a hand-run `curl` at 01:42 and never renamed |

A `.part` is invisible to the helper **on purpose** (a model must never be opened while it is still
arriving), and the engine has to be at the path the unit's `ConditionPathExists` names. Two carriers
had been built and neither had been put where the box looks.

### 31.1 What was done

* the binary was **carried to `$ARK_SRC/ai/llama-server`**, which is where phase `58-ai` says to put
  it, and the phase installed it as `/usr/local/bin/llama-server` (755, `root:root`, sha256
  `7ff4518b…`);
* the finished download was **adopted through the one documented door** —
  `ark-ai-model-fetch --from …gguf.part --name …gguf` — which is what prints the fingerprint that
  now belongs in the paperwork: **`923c4c86177d2ee173a7f5b4fa3d0ac65f5962ab15e6d6a5bc250aec4fd7bf7e`**.
  It copies through `.<name>.part` and renames, so nothing half-arrived is ever visible;
* **the deploy no longer deletes it**: `scripts/03-provision.sh` rsyncs `pi/` with `--delete`, and
  the carried engine lives exactly where the phase tells an operator to put it, so both hops now
  `--exclude 'ai/llama-server'` (`tests/test_config.py::test_the_deploy_does_not_delete_the_engine_it_was_told_to_carry`).

`ark-ai.service` went `enabled + inactive` → **`enabled + active`**, model loaded in 19 s, listening
on `127.0.0.1:8089`, and `/api/ready` reports the ninth row as
*"Questions are answered on this box — 940.4 MB of words, no internet needed."* (`ready: true`).

### 31.2 The engine was faithful; the model was too big for the hardware

Gemma 4 E2B at 3.3 GB answered, and its numbers were the reason it could never appear on a page:

| Measured on the box | Gemma 4 E2B Q4_K_M | Qwen2.5-1.5B-Instruct Q4_K_M |
|---|---|---|
| prompt reading | **4.4 tokens/s** | 6.8 tokens/s |
| writing | **2.3 tokens/s** | 3.2 tokens/s |
| the app's real prompt (871 tokens) | **128 s** of reading | 92 s |
| what came back | `content` **empty**; 120 tokens spent in `reasoning_content` ("Thinking Process: …") | a usable answer |

It is a **reasoning** model: it thinks first and was cut off before it wrote a word of answer. On
top of that, at its speed a full answer is **5 minutes plus** — not a page anybody waits for. So
the documented default (1.5B, 940 MB) was fitted through the same door
(`1adf0b11065d8ad2e8123ea110d1ec956dab4ab038eab665614adba04b6c3370`) and **the Gemma file is still on
the card** for the owner to keep or remove; newest `*.gguf` wins, so the smaller one answers.

### 31.3 Six defects, none of which a 200 from `curl` would have found

1. **The proxy gave up before the page did.** `/ask` was served by the block that serves searches,
   `proxy_read_timeout 120s`, while `ark_ai.ANSWER_SECONDS` is 150 — so the first two real questions
   were `504 Gateway Time-out` at exactly 2m00s on a box that was working perfectly. `/ask` now has
   a `location = /ask` of its own at **300 s**, and
   `tests/test_config.py::test_the_helper_gets_longer_than_its_own_ceiling` refuses a config where
   the proxy becomes the shortest patience of the three.
2. **The prompt was three whole guide pages.** 682 chars of house rules + 1241 of brief + **2668 of
   page excerpts** = 871 tokens = 128 s of reading before a word is written. Reading is the
   expensive half on a Pi 4, so it is now a **budget** (`PROMPT_BUDGET = 3000` chars, one page at
   520, brief 1400) and the answer is capped at `MAX_TOKENS = 160` instead of 380. The measured
   speeds live in the module (`CHARS_PER_TOKEN`, `READ_TOKENS_PER_SECOND`,
   `WRITE_TOKENS_PER_SECOND`) and `tests/test_ai.py::test_the_worst_case_answer_fits_in_the_time_the_page_waits`
   does the arithmetic, so a later "include a bit more" fails a test instead of a person's patience.
3. **"Cut" was not a word the router knew.** *"What do I do about a deep cut"* routed to `general`,
   so **no page at all** was put in front of the model — and it invented one (`/firstaid/`). Fixed
   in three places: plain injury words in `ROUTES`, three letters is enough in `_score`
   (`MIN_WORD`), and a title match is worth four times a body match (`TITLE_WEIGHT`).
4. **A tie was broken by the alphabet.** *First aid* and *household repairs* both contain "cut" and
   "deep"; the sort was `-score` only, so `s…` beat `f…` on one run and `g…` beat `m…` on another.
   Ties now sort by name, and `TOPIC_HINTS` lets a plain word (*cut*, *boil*, *flush*, *fuse*,
   *battery*) **prefer** the page that answers it — a preference, never a filter, and only for a page
   really on the card (`test_a_hint_prefers_a_page_and_never_hides_one`).
5. **The brief carried an instruction, and the model recited it.** The first answer began *"When a
   question is about a person's own house, say which page on the box answers it…"* — the last line
   of the generated brief, verbatim. The rules live in `HOUSE_RULES`; the brief is facts only.
6. **A numbered list arrived as one line.** The model wrote `…steps:1. Boil water.2. Filter water…`
   and the page rendered it as a paragraph. `_ask_paragraphs()` now breaks a run of steps, and a
   decimal in ordinary prose is left alone.

### 31.4 What the box now proves, and what it does not

Three real questions, asked through nginx on the card, all **200**:

| Question | Wall clock | Reading / writing |
|---|---|---|
| *how do I make water safe to drink* (before the budget) | 152.6 s | 871 tok / 128 s + 80 tok / 25 s |
| *how do I make water safe to drink* (after) | **106.9 s** | 632 tok / 92 s + 51 tok / 15 s |
| *what do I do about a deep cut* (before) | 78.0 s | invented `/firstaid/`, recited its rules |
| *what do I do about a deep cut* (after) | **102.1 s** | *"If you get a deep cut, first try to stop the bleeding. Then clean the wound with mild soap and water. Cover it with a sterile bandage or cloth."* |
| *how do I store water* via `/api/ask/stream` | 100 s | chunked text through nginx, first words while it was still writing |

`90-verify` finished green (*"everything a visitor touches is working"*), `/api/ready` 200 with the
ask row `ready: true`, and the brief rebuilt without its instruction (1241 chars → 1166).

**Still unproven, and no amount of `curl` can change it:** no browser or phone has drawn `/ask`; the
page's script path (`/api/ask/stream`) has been read by `curl` on the box but never by JavaScript in
a phone; nobody has asked two questions at once, closed `public_ask`, or read an answer aloud;
**Gemma itself has never produced a full answer** (it is on the card, and at 2.3 tokens/s it is not
the model a page can wait for); and an answer takes **one to two minutes**, which is the honest
speed of a Pi 4 with a 1.5B model and should be read as such in `docs/USING-THE-ARK.md`.

**Tests:** **516 passed, 2 skipped, 24 files** (8 new: the proxy's patience, the prompt budget, the
worst-case arithmetic, the title weight, the plain word, the hint that never hides a page, the
numbered steps, the rsync that must not eat the engine).

---

## 32. A question nobody was reading, and a box that said "somebody else is asking"

**Reported 26 Oct 2026, by the owner, from a phone:** *"Somebody else is asking the helper right
now. Try again in a minute — everything else on the box still works while you wait."* Nobody was
asking anything.

### 32.1 What was really happening

`ark_ai._SLOT` was `threading.Lock()`, taken by `ask()` and by `ask_stream()` and given back in a
`finally`. **`ask_stream()` is a generator**, and a generator's `finally` runs only when somebody
*resumes* it. `/api/ask/stream` handed that generator to `StreamingResponse`, which runs a sync
iterator through `iterate_in_threadpool`; when Starlette cancels an abandoned response, the
cancellation stops the async wrapper but the worker thread stays blocked in `for raw in resp` —
inside the engine's socket — so the generator is never resumed, the `finally` never runs, and the
slot stays taken until the engine has finished writing an answer nobody will ever read. The lock had
no owner and no expiry either, so a wedged answer held it for ever, until the service was restarted.

Measured on the box before the fix (a 6 s `curl -N` against `/api/ask/stream`, then a second
question):

```
curl -sN --max-time 6 .../api/ask/stream?q=…   → client gone after 6 s
ss -tnp | grep :8089                           → ESTABLISHED  …:52442 python   ← still reading
curl /ask?q=hello                              → "Somebody else is asking the helper right now."
ss -tnp | grep :8089   (17 s later)            → 2 sockets, still there
```

Two more paths produced the same sentence, and both are the page's fault rather than the engine's:
the `/ask` script called `form.submit()` when the stream failed — **a second question behind the
first**, which any browser without `Response.body` did every time — and a person asking again while
their first answer was still being written (100 s feels like a long time) was told to wait without
being told how long.

### 32.2 The fix

1. **The slot is a lease.** `begin(kind)` returns `(token, 0)` or `(0, seconds_left)`; `end(token)`
   gives it back **only if that token is still the holder** (a late answer cannot free the slot of
   the question that came after it); `busy_for()` is the honest remaining time, and a lease past
   `LEASE_SECONDS` (= `ANSWER_SECONDS + 60`) is **taken back**, with
   `ark-ai: took the slot back from an answer that outlived 260 seconds` in the journal. `busy()`
   still exists, now backed by the lease. `busy_sentence(seconds)` says *"in about 40 seconds"* when
   the box knows, and the old sentence when it does not.
2. **The engine is hooked, and unhooked.** `_live[token]` holds the engine's response while an
   answer is being read. `cancel(token)` pops it, `close()`s it — which is what breaks a read that
   is already blocked in a worker thread — and releases the lease. Closing the socket is also how
   the engine is told to stop writing, so a core stops burning on words nobody will read.
3. **The route watches the person, not the engine.** `/api/ask/stream` is now `async def`, takes the
   `Request`, pulls each piece in a worker thread with
   `anyio.to_thread.run_sync(…, abandon_on_cancel=True)` (anyio renamed that flag from
   `cancellable` — `_abandon_kwarg()` asks the installed signature instead of guessing), checks
   `request.is_disconnected()` between pieces, and calls `ark_ai.cancel(token)` in a `finally`, so a
   closed tab, a dropped Wi-Fi and a cancelled response all end in the same two things: the engine is
   unhooked and the slot is free. `_advance()` turns `StopIteration` into `None`, because it may not
   cross into an async generator (that would be *async generator raised StopIteration*, a 500 for a
   reader).
4. **The page asks once.** The script checks `'body' in window.Response.prototype` *before* taking
   the form over, keeps one request in flight (`asking`), and on failure writes one plain sentence —
   *"That answer did not arrive. Try once more — everything else on the box keeps working while you
   do."* — instead of submitting the question again.

### 32.3 What the tests now hold

`tests/test_ai.py` is **45** (8 new), and the suite is **524 passed, 2 skipped, 24 files**:

| Test | What it pins |
|---|---|
| `test_the_slot_is_a_lease_that_names_who_holds_it` | a token that is not the holder frees nothing; giving it back twice is not an error |
| `test_the_wait_is_named_in_seconds_and_not_just_in_minutes` | `busy_for()` is a real figure and the sentence carries it; 600 s falls back to the plain sentence |
| `test_a_slot_left_behind_is_taken_back_rather_than_refusing_for_ever` | an expired lease is taken over, the journal says so, and the next question is **answered** |
| `test_the_engine_is_unhooked_from_the_moment_the_reader_goes` | `cancel()` closes the engine's response and frees the slot, and a later `close()` of the old answer cannot free a newer one |
| `test_the_page_gives_the_slot_back_the_moment_its_reader_goes` | the real route: one piece read, then `aclose()` — the slot is free and the next person is not refused |
| `test_nothing_is_left_hooked_or_held_after_an_answer` | after a stream and after a plain `/ask?q=`, `_live` is empty and the slot is free |
| `test_the_page_asks_one_question_and_never_a_second_behind_the_first` | the script checks it can stream, never calls `form.submit()`, and keeps one request in flight |
| `test_the_journal_says_the_person_went_rather_than_a_socket_error` | an abandoned answer ends in one plain journal line, and its token is forgotten |

### 32.4 Proved, and still to prove

Proved here: the eight tests above, `python3 -m py_compile` on both changed modules, and the whole
suite green at **524 passed, 2 skipped**.

Proved on **this machine, against a real server** (`/tmp/ark_askproof.py`, throwaway, removed after
the run — the `pi/api` app under `uvicorn`, a stub engine writing a piece every 0.25 s, and a **raw
socket** that asks for a streamed answer and closes at 3 s, which is what a closed tab does):

```
client gone at 3 s; engine had written 12 pieces
API sockets still to the engine, 2 s after the reader left: 0
second answer: piece0 piece1 piece2 …
PASS: the abandoned question gave the slot back; the second was answered
engine pieces: 14 before the second answer | the abandoned stream failed on the engine side: True
plain page answered: True | busy sentence: False
```

Three things that run says, and that the old code could not: the API's connection to the engine is
**gone** two seconds after the reader left (before the fix it was still `ESTABLISHED` — measured on
the card, §32.1); the **engine** saw its stream fail, so it stopped writing instead of finishing an
answer for nobody; and the next question was **answered** rather than told to wait. The plain page
(`/ask?q=…`) still answers afterwards, so the server-side door holds nothing either.

**Still to prove on the card:** the same 6 s `curl -N` followed by a second question (expected: the
`:8089` socket gone at once, and an answer instead of a refusal), and the two-thumb version — closing
a tab mid-answer on a phone and asking again straight away. Both are steps 8a and 8b of
`docs/HUMAN-TEST-LIST.md` §14.

### 32.5 On the card, 26 Oct 2026 — deployed and proved

Deployed with `--only 60-portal` after a `.backup` to `/root/ark-db-before-ask-lease.db` (**no
migration**; the layout stays **v6**), then `--only 90-verify` → *"everything a visitor touches is
working"*. The same 6-second `curl` that reproduced the defect, and a second question straight after
it:

```
18:42:13  a question is asked through the proxy
18:42:19  client gone after 70 bytes
          sockets from the API to the engine, 2 s later: 0          ← was ESTABLISHED before
18:42:30  second answer after 9 s: 95 bytes
          "To store water, follow the instructions in the "Water: find it, make it safe,
           store it" manual."
          refusal sentence in it: 0
ark-api:  ark-ai: the person who asked went away, so the answer was stopped
ark-ai:   slot release: id 0 | task 421 | stop processing: n_tokens = 610
ark-ai:   slot launch_slot_: id 0 | task 422 | processing task      ← the next question
ark-ai:   … task 422 | prompt eval 1732 ms / 10 tokens, eval 7166 ms / 25 tokens (3.35 tok/s)
```

Four things there, none of which the old code could produce: the API's socket to the engine is
**gone** two seconds after the reader left; the **engine** was told to stop (`cancel task`, then
`release`) so the core comes back instead of finishing an answer for nobody; the next question was
**answered** 11 s later, with real words and no refusal; and the journal says what happened in one
plain line rather than printing `'NoneType' object has no attribute 'peek'` (which is what the first
deploy wrote, and why the message exists).

**Still to prove — a human, not a shell:** closing a tab mid-answer on a phone and asking again
straight away (step 8a), and asking twice on purpose to see *"in about N seconds"* (step 8).

---

## 33. A Wi-Fi the owner can save, and one restart asked for by name — 31 Oct 2026

The box has always been able to do three things with a network, and until now it could only
do two of them from a console: `sudo ark-mode home`, `sudo ark-mode hotspot`, `sudo ark-mode
auto`. That is fine for the person who built it and useless for the household it was built
for — **"carry the Ark to my mother's house and get it on her Wi-Fi"** is not an SSH session,
it is a phone in a kitchen with the router's password on a sticker on the back. There is a
**Network** page on the owner's console now:

```
https://ark.lan:8443/admin/network      (or https://ark.local:8443/admin/network)
```

It has four headings, and the order is the order a person asks the questions:

| The page says | What it does |
|---|---|
| **Where the box is now** | one plain line, then one line about what a cold start does, taken from `BOOT_MODE` (`STANDING_BOOT`) |
| **Restart on my own network for a while** | a picker of saved networks, a number of minutes, and a checkbox — *Restart the box now, without waiting* |
| **Add a Wi-Fi network** | the name the phone shows and its password — *Save this network* |
| **Networks already saved** | the box's own list read back, each by the name the phone shows and the name it is saved as |

**A saved network is not a joined network.** Saving writes a NetworkManager profile with
`autoconnect=false` and `autoconnect-retries=0` (`--autoconnect yes` is the one way to ask for
the opposite, and nothing on the page offers it), so a box in a bag does not quietly start
looking for somebody else's router. Joining is a *request*: it names one network, it waits a
stated number of minutes, and it is used **once** — the boot after that broadcasts
`The-Ark-Offline` as usual. If the named network is not there at that boot, the box
broadcasts instead. That is the whole design premise, one sentence: **the box must never end
up somewhere nobody is standing next to it.**

### 33.1 The three writers, and who owns which file

Nothing new writes anything by itself. The §6 rule ("one file, one author") is what the
whole feature is built out of:

| File | Only writer | Only reader |
|---|---|---|
| `/etc/ark/lan-request` | `ark-mode request-lan` (temp file → `mv`, `0644`) | `ark-mode`'s `cmd_boot`, `ark-mode status`, `90-verify` |
| `/etc/NetworkManager/system-connections/*.nmconnection` | `ark-wifi-add` (`0600 root:root`) | NetworkManager |
| the page itself | `ark_network.py` → `sudo -n ark-mode …` / `sudo -n ark-wifi-add …` | `ark_admin.network_page()` |

`pi/api/ark_network.py` is the page's whole vocabulary and it **reads** both files and shells
out for everything else: `mode_words()`, `request_state()`/`request_words()`, `saved_networks()`
(`[]`, `None` for unreadable — the §8.7 `None` rule, never "no networks"), `add_network()`,
`request_lan()`, `cancel_request()`, `boot_mode()`, and `last_words()` (the one line `ark-mode`
printed, so the page relays the box's own sentence instead of paraphrasing it). A refusal is a
`NetworkRefused` carrying a sentence a person can act on — never a traceback, never a silent
no-op. A **network name and a passphrase have the sizes Wi-Fi really has**:
`SSID_MIN, SSID_MAX = 1, 32` and `PSK_MIN, PSK_MAX = 8, 63`, and `LAN_MINUTES_MAX = 24 * 60`
with `LAN_MINUTES_DEFAULT = 30` (which is what `ark_admin.NETWORK_MINUTES` puts in the field).

**The passphrase goes in on stdin and comes out nowhere.** `ark-wifi-add` reads it from stdin
(the usage text says `printf '%s\n' "$PASSWORD" | sudo ark-wifi-add --ssid "NAME"`), because a
command line is readable by every process on the box and lands in shell history. It is never
put in the database, the audit row, a log line or the page's own memory beyond the one call;
the page writes the audit row as *saved a network called "…"* and nothing else. The profile it
writes is `0600 root:root` (`.clinerules` §6), it keeps the profile's existing `uuid` so saving
the same network twice **replaces** it instead of leaving a second one nobody will remember,
and it reads the file back and dies unless `ssid=` matches what was asked for.

`ark-mode request-lan` takes `--minutes N`, `--network PROFILE`, `--ssid NAME`, `--by WHO`,
`--cancel` and `--restart`, and writes exactly six keys — `requested`, `expires`, `minutes`,
`network`, `ssid`, `by` — to a temp file and renames it, so `cmd_boot` can never read half a
request. A request that lapsed is *discarded*, not honoured (a stale "restart on my network"
would be a way to make a box unreachable). `--restart` uses a **transient** `ark-lan-restart`
unit (`systemd-run --on-active=… --collect --unit=ark-lan-restart systemctl reboot`) rather
than a background `sleep`: it outlives the script, it cannot be forgotten, and the page has
time to finish answering first. `ARK_MODE_REHEARSAL=1` (or `SYSTEMD_RUN_COMMAND`) is the seam
that lets a rehearsal prove the request without rebooting the machine running it.

`ark-mode status` grew one line — `lan request   : waiting … / expired … / nothing waiting` —
which is what `90-verify` reads, so "why did it not come up on my router?" has an answer on
the box and not only in a browser.


### 33.2 The watchdog learns a third word, and gives it the same grace as `home`

`ark-mode` records where the box is in `/etc/ark/mode`, and `pi/bin/ark-net-watch` reads that
file every minute before it decides anything. `cmd_lan` adds a fourth word to the three that
were there (`hotspot`, `home`, `auto`): **`lan`** — the box is on a saved network of the owner's
for administrative work, which is exactly what the page's one-time restart produces
(`lan_up()` → `set_mode lan`, then `pin lan`).

The pure `decide()` table only ever had an opinion about one of those words: a box that believes
it is `home` gets **one more check** before its radio becomes an access point
(`lost_streak < LOST_STREAK`). Every other word fell straight through to `to-hotspot`. So a box
the owner had just put on their own network — in a kitchen, mid sign-in, router password off the
sticker on the back — was **one blinked check away from broadcasting**, and
`ark-mode hotspot --auto` deliberately leaves no pin, so it would have stayed broadcasting at
that house until somebody noticed. Nothing would have looked like a fault: `net-watch.log` would
only show a decision it was right to make under its own old rules.

`lan` gets the same bargain as `home` now, in one condition:

```bash
if [[ "$mode" == "home" || "$mode" == "lan" ]] && (( lost_streak < LOST_STREAK )); then
  printf 'stay'
```

— *the owner asked for a network, so give the link a minute before broadcasting*, in the words
written beside the line. Two rows joined the decision table in `tests/test_network.py` (`lan` with
one lost check → `stay`; `lan` with two → `to-hotspot`), so the grace cannot quietly go back to
being a `home`-only privilege. The table is **16 cases** now, and
`test_the_thresholds_are_counted_not_guessed` still holds `LOST_STREAK` at two, so a `lan` box
gets the same two checks a `home` box does rather than a third rule of its own.

**The word is a state, not a private detail of one script**, so it reaches the rest of the box:
`ark-mode status` prints the mode, `90-verify` reports the `lan request` line beside it,
`ark_network.mode_words()` answers `lan` **exactly as it answers `home`** — from a phone the two
look the same, *the box is on a saved network of yours, so it answers at
`https://ark.local:8443`* — and `STANDING_BOOT["lan"]` gives the page a sentence rather than a
blank if `/etc/ark/ark.env` ever says `lan`. A cold boot can reach `lan` only through a request,
and only **once**; the boot after it broadcasts as usual.

### 33.3 The four phases: two copies, one helper, three lines and one block of proof

The feature spans four phases, and each one had a way of being wrong that would only have shown
up later, on the box, in front of the person it was built for:

| Phase | What it now does | Why |
|---|---|---|
| `60-portal.sh` | copies `ark_network.py` beside `ark_admin.py` in the API's module list | `ark_admin` imports it **at module level**, so a missing file is not "the Network page is gone", it is an `ark-api` that cannot start at all |
| `65-admin.sh` | copies the same module again, and says why in its own header | `--only 65-admin` is the documented way to fix the console door; on its own it would otherwise leave an `ark-api` that cannot import |
| `70-network.sh` | installs `/usr/local/sbin/ark-wifi-add` `0755 root:root`, then **knocks on both doors as the portal's own account** | the profile is written by root and reached with `sudo -n` as `pi`; a door nobody has knocked on is not an installed door, and the alternative is the owner discovering it an hour later as *"The box would not do that."* |
| `90-verify.sh` | three lines, and one block in `ark_status.json` | "why did it not come up on my router?" has to have an answer after the terminal is closed |

**The two knocks change nothing and leave nothing behind.** `ark-wifi-add --check --ssid "ark
rehearsal network"` runs as `runuser -u "$ARK_PI_USER" -- sudo -n …` with the
passphrase on **stdin** (a here-string, never argv), so the proof is the shape the page really
uses; `--check` prints *would save …* and creates not even the folder. The name is deliberately
short: it must be one the helper would **accept**, because a rehearsal the helper refuses proves
nothing about the door — see §33.7, where the first version of this proof asked for a 35-character
name against a 32-character limit and reported a working door as a missing one. A refusal in the
helper's own words (`Not done:`) still counts as having reached it, since a door nobody can knock
on says *sudo: a password is required* or *No such file*, never *Not done:*. `ark-mode request-lan
--minutes 0` must be **refused**, and the phase greps for `greater than zero` — a zero-minute
request is the one legal-looking input that writes nothing, so the door is proved without leaving
a request for the next boot. Both are `ark_warn` rather than a hard failure: a box with no helper
still broadcasts and is still reachable, which is the whole design, so the installer must not be
able to fail a run over a feature that is missing rather than broken.

**The verifier's three lines are graded differently on purpose.** *add a Wi-Fi network* is `ok`
when `/usr/local/sbin/ark-wifi-add` is executable and `soft` otherwise (*helper missing — run
phase 70-network, or the page's button will refuse*); *one-time restart* reads
`ark-mode status | awk '/lan request/'` and is `soft` for `waiting`/`expired` (a request is not a
fault) and `ok` when nothing is waiting; *Network page behind the sign-in* is **hard**, because it
is a door like `:8443` itself — `ark_tls_code ark.lan:8443 /admin/network` must answer `303`. That
probe asks **by name**: `ark_tls_code` adds `--resolve`, because an IP literal sends no SNI, nginx
then serves the rejecting default server, and curl reports `000` on a healthy door (§8.7). A `000`
here means the probe, not the box.

`ark_status.json` gains one block, so the answer survives the terminal:

```json
"network": {"wifi_helper": "yes", "lan_request": "absent", "owner_page": "303"}
```


### 33.4 Two traps: which sentence answers the page, and a restart asked for from inside a request

Neither is a crash. Both would have left the page looking perfectly healthy while telling the
person in front of it the wrong thing — the same family as §8.7's "an HTTP 200 is not evidence".

**The page relays the helper's own words, so it has to relay the right one.** `ark-mode
request-lan` says two sentences, on purpose:

```
asked: the next boot (within 20 minutes) joins "Rosebank Road" — once
if that network is not there, the box broadcasts as usual — this cannot lock it out
```

Every other helper on the box ends with the sentence worth showing, which is why
`ark_network.last_words()` exists and why the page puts **the last line** under its green banner.
Applied here, that shows the reassurance and hides the request: a banner answering a question
nobody asked, with the actual answer sitting one line above it. `ark_admin._what_was_asked()`
prefers the line that starts `asked:` and falls through to `last_words()` for every helper that
prints no such line, so both shapes are covered and no other caller changes.
`test_a_restart_asked_for_from_the_page_is_used_by_exactly_one_boot` asserts the banner **starts
with** `asked:` — *the request, not the aside after it* — and
`test_the_page_hands_the_helper_one_name_and_one_number_and_nothing_more` holds the other shape
(a helper that prints one line still has that line shown).

**A restart asked for from inside a request has two ways to eat its own answer.** The handler
calls the helper through `subprocess.run(..., capture_output=True)`, so anything the helper leaves
behind holding that pipe keeps the handler waiting: a plain `sleep 5 && reboot &` hung off the
helper would make the page sit there for the sleep and then have its answer cut off by the very
restart it asked for. And a restart that happens *before* the page answers loses the one sentence
that says what the box will do next. So the restart is **scheduled rather than performed**, by a
transient unit:

```bash
"${SYSTEMD_RUN_COMMAND:-systemd-run}" --quiet --collect \
  --on-active="$secs" --unit=ark-lan-restart systemctl reboot
```

`systemd-run` returns at once (so nothing is holding the pipe), `--on-active=5`
(`LAN_RESTART_DELAY`) leaves the page time to answer, and `--collect` means the unit is not
remembered afterwards — the code's own words are *it outlives this script and cannot be
forgotten*. The promise it keeps is the sentence in the redirect: *restarting in 5s — open
https://ark.local:8443/admin from your own network when it is back.*

Two seams keep that provable on a machine nobody wants rebooted: `SYSTEMD_RUN_COMMAND` (a
rehearsal substitutes a script that records the call instead of rebooting) and
`ARK_MODE_REHEARSAL=1`, which does not try at all and says *rehearsal: this machine was not
restarted*.

**And if the restart cannot be scheduled, the request stays.** `restart_now()` returns 1,
`ark-mode` prints *the request is saved, but this box did not restart itself — restart it by hand
(sudo reboot)* and exits non-zero, so the page shows exactly that sentence. Losing the request
would be the worse failure: the person did the work of naming a network, and all that is missing
is the reboot.

**The wrinkle, closed on 31 Oct 2026: a refusal that kept the request now says so.** The banner
used to open with *Nothing was asked.* on that path — while the helper's own words in the same
sentence said it had saved the request, and the **Waiting:** block underneath still offered *Take
that request back* (that block is drawn from `ark_network.request_state()`, never from the form,
which is why the two could disagree so visibly). The banner was denying what the page was offering
to undo. `network_lan()` asks the box on the refusal path now and prints **The request is kept.**
only when all three of these hold: the person ticked the restart, the helper was really reached, and
the request the box's clock is reading is still `waiting`.

The middle condition is why `NetworkRefused` grew a `ran` flag. `--minutes 0`, a name with a space
in it and a name that would read as a flag are all refused **inside** `ark_network.request_lan()`
before any subprocess starts, so none of them can have written a request — and a request left
waiting by an earlier person must not be claimed as this one. `ran=True` is set only by a helper
that came back non-zero, and by a timeout (the helper *was* running, so its request may well be on
the card: the conservative half of the two).

Three tests hold it, and they are deliberately three rather than one:

* `test_a_restart_that_failed_while_the_request_was_kept_says_so_first` — a waiting request plus a
  helper that exits 1: *The request is kept.*, not *Nothing was asked.*, the helper's sentence kept
  word for word and escaped, and the **Take that request back** form present;
* `test_a_refusal_that_wrote_no_request_still_says_nothing_was_asked` — the helper refuses and
  nothing is written: the honest sentence is not quietly replaced by the flattering one, and no
  request file appears;
* `test_a_refusal_made_before_anything_runs_does_not_claim_the_old_request` — `--minutes 0` with an
  older request already waiting: still *Nothing was asked.*, and the older request is byte-identical
  afterwards. Without the `ran` flag this is the case that would have started lying.

**A read that demanded root (found 31 Oct 2026, while checking the paperwork against the code).**
`ark-mode`'s own comment said *reading needs no privilege: "status" and "boot --dry-run" touch
nothing*, and so did §8.4 and `docs/ADMIN.md` — but the gate in front of it exempted only
`boot --dry-run`:

```bash
if [[ "${1:-status}" == "boot" && "${2:-}" == "--dry-run" ]] \   # "status" was missing
   || [[ "${ARK_MODE_REHEARSAL:-0}" == "1" ]]; then :; else need_root "$*"; fi
```

So a person following the paperwork got `ark-mode: run this as root (sudo ark-mode status)` for a
command that writes nothing, and the only way to see the box's own account of itself without
`sudo` was `ark-mode boot --dry-run`. The gate now exempts `status` (and an argument-less
`ark-mode`, which asks the same question) as well; nothing else widened, and everything that
writes — `boot`, `auto`, `home`, `hotspot`, `lan`, `request-lan`, `test` — is still root-only.
`tests/test_network_page.py::test_reading_where_the_box_is_needs_no_root_at_all` **removes the
rehearsal seam** and runs the real gate, so it asserts what a person meets rather than what the
test door allows. Measured both ways on 31 Oct 2026 with the seam unset and a throwaway folder:
the repo script answers `status` (and a bare `ark-mode`) with exit 0 and creates no file, while
the same script with the old exemption line alone prints `ark-mode: run this as root (sudo
ark-mode status)` and exits 1 — and `ark-mode hotspot` is refused by **both** copies, which is
the half that must not widen.

### 33.5 What the tests hold

`tests/test_network_page.py` — **37 test functions, 41 collected**, several of them driven with a
table of bad inputs — and `ark_network` joined `MODULES` in
`tests/conftest.py`, so a broken import fails every test file instead of one. **Three layers**,
because each catches a different kind of wrong:

* **the words** — the real `ark_network` against a throwaway folder (`ARK_NM_DIR`,
  `LAN_REQUEST_FILE`, `ARK_ENV_FILE`, `MODE_FILE`, `PIN_FILE`, `ARK_SUDO`, `ARK_MODE_BIN`,
  `ARK_WIFI_ADD`, and the two connection-name overrides `LAN_WIFI_CONNECTION` /
  `HOME_WIFI_CONNECTION` are all read at
  call time, so nothing has to be patched into a module constant): a name of 1–32 and a
  password of 8–63 refused with **the limit named**, a stray space refused rather than trimmed,
  a password never trimmed, a name that would read as a flag or break a `.nmconnection` line
  refused, the box's own access point never offered as somewhere to join, an unreadable folder
  answered as `None` and **not** as an empty one, and `waiting`/`expired`/`nothing waiting`
  read from a real request file;
* **the flow** — the **shipped** `pi/bin/ark-mode` in its rehearsal seam against a fake radio,
  so the script under test is the script that runs on the box: `request-lan` writes a request
  the console can read, `boot` uses it **once** and the boot after that broadcasts, a lapsed
  request is thrown away, a network that will not join leaves the box broadcasting, the network
  a person named is the one that is joined, `--cancel` leaves nothing waiting, and minutes that
  are not a whole number are refused **before** anything is written;
* **the page** — the real routes (`GET /admin/network`, `POST /admin/network/add`,
  `POST /admin/network/lan`, `POST /admin/network/cancel`) through the admin app with the
  loopback-TLS peer the box really has, with the two helpers stubbed and every word they were
  given recorded: a stranger gets 303 to the sign-in and the helpers are **never run**, a form
  with a password is refused over plain HTTP, the page shows where the box is in plain words
  and both jobs with a `type="password"` field, a save reaches the box as
  `wifi-add --ssid Rosebank Road` with the password on **stdin** (the argv itself is asserted
  to be clean), the password is never written back onto the page, a bad one is **Nothing was
  saved.** with the number to fix and no audit row, the audit names only the network, the
  minutes and the name reach `ark-mode request-lan` and nothing else does, the tick-box is a
  second decision with its own audit sentence, a waiting request is shown with one
  **Take that request back** form whose withdrawal the next boot proves, and a `--restart` the box
  could not schedule says **The request is kept.** when a request is really waiting — with a
  refusal that wrote nothing still saying *Nothing was asked.* (three tests, §33.4).

Whole suite: **567 passed, 2 skipped** (`python -m pytest -o addopts='--strict-markers' -q`,
Mac, throwaway databases) — §33 took it from **524** to 567 over **25 files**: the 41 collected in
`tests/test_network_page.py` plus the two `lan` rows that joined the decision table in
`tests/test_network.py` (16 cases now). The two skips are still the QR cross-checks
that need `qrcode`/`opencv-python-headless`. `python3 -m py_compile` clean on
`pi/api/ark_network.py`, `pi/api/ark_admin.py` and `tests/test_network_page.py`; `bash -n` clean
on `pi/bin/ark-mode`, `pi/bin/ark-wifi-add`, `pi/bin/ark-net-watch` and the four phases named in
§33.3.

### 33.6 What a rehearsal proved, and what only the card can

Rehearsed on the Mac, with throwaway paths and the real scripts (`ARK_MODE_REHEARSAL PASS`,
`WIFI_REHEARSAL PASS`, `ARK_NETWORK_REHEARSAL PASS`):

* `ark-mode request-lan --minutes 45 --network "Rosebank Road" --ssid "Rosebank Road" --by
  rehearsal` wrote exactly the six keys, one rename, and printed the `asked:` sentence; a second
  run with `--cancel` printed *"cancelled — nothing is waiting, and the box has not changed"* and
  left no file; `ark-mode status` showed the `lan request   :` line in each state; the rehearsal
  proved the **one-boot** promise by running `ark-mode boot` twice against a fake radio (the
  request joined the named profile, `mode is lan`, the file gone, and the *next* boot went back
  to broadcasting), a request naming no network fell back to the standing one, and minutes of
  `0`, `abc` and `-5` were refused **with nothing written**. `--restart` with
  `ARK_MODE_REHEARSAL=1` prints *"rehearsal: this machine was not restarted"* **and then** the
  sentence it would have said on a real box — *"restarting in 5s — open
  https://ark.local:8443/admin from your own network when it is back"* — because the seam is
  there to rehearse the caller as well as the reboot, and a rehearsal is the only place those two
  lines belong together. With a recorder standing in for `systemd-run`
  (`SYSTEMD_RUN_COMMAND=/tmp/fake-run`) the scheduled command is proved word for word:
  `--quiet --collect --on-active=5 --unit=ark-lan-restart systemctl reboot` — which is the
  whole point of scheduling it (the page gets its answer, the reboot happens after it). A restart
  that could not be scheduled says so out loud — *"the request is saved, but this box did not
  restart itself — restart it by hand (sudo reboot)"* — with the request **kept**, because losing
  it would be the worse failure.
* `ark-wifi-add --ssid "Home Wi-Fi"` with the passphrase on stdin wrote one `.nmconnection`
  `0600`, `autoconnect=false`, `autoconnect-retries=0`, `key-mgmt=wpa-psk`, read back by
  `ssid=`; saving it again said *"Already saved … unchanged"* and left **one** profile with its
   identifier; a new password said *"Saved …"*, replaced the secret, and the old one is gone from
  the file; `--check` printed *"would save …"* and created no folder at all; a 3-character
  password, a 33-character name, an empty password, no name and a leading space were each refused
  with the limit named, and no refusal touched a byte.
* `ark_network` against a throwaway `/etc/NetworkManager/system-connections`: `saved_networks()`
  answered the Wi-Fi names, an absent folder was `[]` and an unlistable one `None`, the request
  read `waiting` with its minutes and who asked, then `absent` after a cancel and `expired` from
  the clock, and `mode_words()` answered a sentence even for a box that has never recorded a
  network.

**Still to prove, and it needs the box:** the page has **never been opened in a browser** —
not on the Pi, not on a phone. Specifically, in `docs/HUMAN-TEST-LIST.md` **§17**:

1. the page draws in a phone's browser over `https://ark.local:8443/admin/network`, and the
   three headings read as plain English with no service name in them;
2. saving the household's own Wi-Fi name + password from the page really writes a profile
   (`sudo ls -l /etc/NetworkManager/system-connections/`) and the phone never shows the
   password back;
3. **the real one:** ask for a restart on the household network, watch the box join it, and
   confirm the *next* restart broadcasts `The-Ark-Offline` again — because "used up, not a
   mode" is the promise, and no test on a Mac can keep it;
4. ask for a restart on a network that is **not** there, and watch the box broadcast instead —
   the lock-out that must never happen;
5. `sudo -n` really works for the portal's own account: `70-network` proves the helper is
   installed, but a `sudoers` rule missing on the card turns the page's button into a refusal
   sentence (`Nothing was saved. …`) and that sentence has never been seen;
6. `90-verify` on the card, whose three new lines have never run there.


### 33.7 The deploy, 1 Nov 2026 — and a proof that called a working door shut

The four phases went onto the card the way §33.3 describes, after the box was reachable again:
its Ethernet port was plugged into the household router and came up as an ordinary client
(`<CABLE-IP>`) **while wlan0 stayed an access point** (`10.42.0.1`). Nothing was switched, no
reboot was taken and the hotspot never dropped, so the radio was never a question.

| | |
|---|---|
| Rollback point | `/root/ark-db-before-network-20261101-062506.db` (SQLite `.backup`), `/root/ark-before-network-20261101-062506.tgz` (`/srv/ark/src`, the three helpers, `/etc/nginx`, `/etc/ark`), `/root/ark_status-before-network-20261101-062506.json` |
| Command | `scripts/03-provision.sh ark.local --only 70-network,60-portal,65-admin`, then `--only 90-verify` |
| Result | both runs `ARK_INSTALL_COMPLETE`; 33 s for the three phases, 5 s for the verifier; `90-verify` → *"everything a visitor touches is working"* |
| Schema | **v6 before and after** — no migration ran, as promised (this feature adds no schema) |
| The box's own words | `mode=hotspot link=1 hotspot=1 in-range=1 clients=0 pinned=1 lost=0 back=0 -> stay` — the new watchdog did not touch the radio while the new helper was installed |
| The receipt (`ark_status.json`) | `network.wifi_helper: "yes"`, `network.owner_page: "303"`, `network.lan_request: "absent"`, readiness `ok: true`, `blocking: []` |
| The verifier's new lines | `✓ add a Wi-Fi network  the page can save one from the box (sudo -n ark-wifi-add)`; `→ network choice: held on "hotspot" by the owner (ark-mode auto undoes it)`; `· network watchdog  no decision logged yet (first check 3 min after boot)` |

**The defect this deploy found: phase 70 reported a working door as a shut one.** The rehearsal
that proves *the page can reach the helper* ran

```
sudo -n /usr/local/sbin/ark-wifi-add --ssid "A network this box has never joined" --check
```

and the card answered, in the helper's own words:

```
! the Network page cannot reach ark-wifi-add as pi — saving a network will fail:
  Not done: a network name is at most 32 characters; that one is 35.
```

The name is **35** characters and `ark-wifi-add` refuses anything over **32** (`SSID_MAX`), so the
helper did exactly what it promises. The rehearsal's own name was illegal, and the phase read
*"the command exited non-zero"* as *"the account cannot reach the helper"* — the one reading the
proof exists to detect, printed by the proof itself. Nothing on the box was wrong: `pi` reached
the helper, `sudo -n` worked, and the sentence that said otherwise was the helper's. The other
knock in the same block (`ark-mode request-lan --minutes 0`) was **green**, because that one
greps for the helper's own refusal instead of trusting the exit code.

Two changes, and both are now held by a test in `tests/test_network_page.py`:

* the rehearsal saves **`ark rehearsal network`** (21 characters) — a name the helper would accept,
  so a green line means a working door and a red one means a broken one;
* a refusal carrying the helper's own `Not done:` marker counts as **having reached it**
  (`reached ark-wifi-add … but the rehearsal itself was refused (the door works)`), and
  *cannot reach* is the last resort only — a door nobody can knock on says
  `sudo: a password is required` or `No such file`, never `Not done:`. So the next limit change
  cannot turn this proof back into a liar.

`test_the_installer_rehearses_a_network_name_the_helper_would_accept` reads the name straight out
of `pi/steps/70-network.sh` and holds it to `ark_network.SSID_MIN`/`SSID_MAX`, so the installer and
the helper cannot drift apart again; `test_the_installer_counts_the_helpers_own_refusal_as_having_reached_it` holds the second half, including that the *cannot reach* sentence appears exactly once
and only as the `else`. **567 → 569 passed, 2 skipped** (the two new tests; `tests/test_network_page.py`
is 43).

**What the deploy also proved, without meaning to.** The box had **rebooted on its own on
31 Oct 2026 at 22:18** — the first cold boot since 26 Oct — so several things §8.9 has been
carrying as "never run" have now run on this hardware:

* `fsck.mode=force` is **gone from `/boot/firmware/cmdline.txt`, which is exactly one line**, and
  `journalctl -b -u ark-fsck-once` shows it starting and finishing on this boot: the withdrawal
  (§28) works, and it worked on the reboot it was armed for. Its journal stamp reads *22:00:05*,
  before the boot — that is `fake-hwclock` handing the box a stale clock until NTP arrives, which
  is §26 doing its job, not a fault;
* `/tmp` and `/var/tmp` are **tmpfs mounted from `/etc/fstab`** (both lines carry `nofail`), so the
  memory folders now come from a setting rather than from a hand mount (§8.9 item 0e);
* `ark-api`, `ark-kiwix` and `ark-calibre` each carry `local-fs.target` in `After=`, so the offline
  ordering (§28) has now been exercised by a real boot rather than by `systemctl show` alone;
* `RuntimeWatchdogUSec=15s`, schema **v6**, `journal_mode=wal`.

**Still unproven, and unchanged by this deploy:** no browser has drawn or clicked the Network page;
no real NetworkManager has been asked to use a profile this page wrote; `sudo -n` has now been
exercised as `pi` on the card by the phase (that is the `✓ add a Wi-Fi network` line) but not from
the page's own code path under a request; and the two questions only a card can answer — *does a
request really cost exactly one boot?* and *does a box on somebody else's Wi-Fi come back?* — are
still unanswered. `docs/HUMAN-TEST-LIST.md` §17 is the walk-through. A power cut has still not been
simulated.


## 34. One button for a pile of files, and one page for every door — 1 Nov 2026

Two small things an owner asks for after a week of using the box, and both of them turned
out to be about **not building anything new**:

1. *"I have forty books and thirty cartridges on a stick. Do I really have to add them one
   at a time?"* — until now yes: a game through **Games → A game you already have** (which
   shows its words and waits for you to agree), a book by `scp`ing it into
   `/srv/ark/calibre-incoming`. There is an **Add files** page now, and it takes twenty
   files in one press.
2. *"Which address was the game shelf on again?"* — answered, until now, by four documents
   that have each been wrong at least once (`README.md`, `docs/ADMIN.md`,
   `docs/USING-THE-ARK.md`, and §8 of `.clinerules`). There is an **Addresses** page now,
   generated from one list in the code, with a test that reads the application and fails if
   a page is served that the list does not name.

```
https://ark.local:8443/admin/upload     ← Add files     (twenty files, one button)
https://ark.local:8443/admin/doors      ← Addresses     (every door, in plain words)
```

### 34.1 The batch door is two old doors with a loop around them

The whole feature is one new module (`pi/api/ark_ingest.py`) and two routes. It introduces
**no ingest path at all**:

| Kind | What the page does | Which existing thing does the work |
|---|---|---|
| a book | writes the bytes into `/srv/ark/calibre-incoming` under a name the box chose | `ark-book-import.path` fires, `ark-import-books` imports, the ledger records it — exactly what a drop into the File Box already did |
| a game | `ark_arcade_install.spool_upload()` then `import_owner_file()` | `publish_candidate()`: the same shape proof, fingerprint, revision rule and single writer of `arcade/games` as `--import` and the one-at-a-time page |

What is genuinely new is the **contract with the other program**: a book is written as
`.upload-<32 hex><ext>.part` — a **leading dot and a `.part` suffix**, both of which
`ark-import-books._is_book()` refuses — and is `os.link`ed to a real name only after every
byte is on the card and `fsync`ed. The path unit may therefore fire as often as it likes
during a 400 MB upload and can never read half a book. The published name is
`<readable stem>-<8 hex><ext>`, so the Book Library shows a title a person recognises and two
people uploading `Book.epub` in the same minute cannot overwrite each other.

The other half of the contract is the **answer**: `ark_ingest.wait_for_library()` polls
`calibre-incoming/.imported.json` — the ledger the importer writes *after* Calibre accepted
the file — for up to 40 seconds. The page then says *"in the Book Library now"* or *"still
being added — open Books in a moment"*. It never infers success from the file having left the
folder, because a book the importer could not add is left exactly where it is, and those two
states are the two things a person needs told apart.

### 34.2 Five defects found by writing the tests, and one by reading the code again

| What was wrong | How it showed | The fix |
|---|---|---|
| `spool_book(item.file, …)` passed the `SpooledTemporaryFile` **object** where the function wanted a `read` callable — `TypeError: 'SpooledTemporaryFile' object is not callable`, every book at once | the first run of the new tests: 13 failures, all of them books | pass `item.file.read`, matching `ark_storage.spool()`'s own convention |
| A book handed over and still importing returned **HTTP 400**, because the status was `200 if done else 400` — so a healthy handover looked like a client error to any probe | `test_a_book_lands_in_the_importers_own_folder` asserting 200 | `200 if (done or late) else 400`: a book the box has *taken* was accepted, whatever the importer is still doing |
| The summary read **"0 of 1 file went on the box. 1 still being added."** — a sentence that denies what the line under it says | the same test | the count is `done + late` ("went on the box"), and the waiting clause is separate |
| A refusal could say **"larger than this page will take (0 MB)"** when a cap was below a megabyte | the batch-ceiling test | `ark_ingest._size_words()`, so the sentence never prints a zero unit |
| A test that counted the staging folder counted the importer's own `.imported.json` as a book | `test_a_book_the_library_already_holds_is_not_added_twice` | the helper reads visible files; a second helper names the hidden ones |
| The sweep had **no rule for `calibre-incoming` at all** — a cut in the middle of a book would leave a hidden `.part` file for ever, invisible to every cleanup | reading `SWEEP_RULES` after writing the writer | one rule, `("calibre-incoming", ".*.part", STALE_UPLOAD_HOURS)`, and a test that sweeps a stale half book and leaves a whole one alone |

The first one is worth naming as a pattern: `ark_arcade_install.spool_upload(stream)` takes
a **file object** and `ark_storage.spool(read)` takes a **callable**. Two functions in the
same family, one word apart, and nothing but a test that uploads a book can tell them apart.


### 34.3 The address list is data, and the test is the guard

`pi/api/ark_doors.py` holds 30 doors in five groups (*The way in*, *Open to anyone on the
box's Wi-Fi*, *Behind the sign-in*, *A name for keeping saves*, *For a monitor rather than a
person*). Each is a name, an address, who may open it and one sentence of what it is for.
`ark_admin.doors_page` renders it with **the host the reader actually used** substituted for
`{host}` — `testserver` under the test client, `ark.local` on a home LAN, `ark.lan` on the
hotspot — because a page that prints a name nobody can resolve is worse than no page.

Three tests hold it:

* every `@router.get` the owner's application serves is in the list (or in `ark_doors.STEPS`,
  the two form pages inside the way in) — so a new administrator page that nobody can find
  fails the suite;
* every door has a name, a who and a what, starts with `http`, and belongs to a group the page
  prints;
* the page answers 303 without a sign-in, and 200 with one.

`/api/ready` is described as *"the honest one"* and `/api/health` as *"Says only that the web
program answered. It is not a verdict on the box."* The two sentences stage 4 was built on now
live where an owner will meet them.

### 34.4 What the proxy got, and why the numbers are the numbers

`pi/nginx/ark-admin.conf` gains exactly one location — `= /admin/upload`, `client_max_body_size
520m`, `client_body_timeout 1800s`, `proxy_request_buffering off`, `proxy_read_timeout 300s` —
and the `^~ /admin` prefix underneath keeps the server's `128k`. 520 m is
`ark_ingest.MAX_BATCH` (512 MiB) plus 8 MiB of multipart framing, so the proxy can never
refuse a batch the page would take, and a body it does take waits on the **card**
(`client_body_temp_path /srv/ark/tmp/nginx`, §13.2) rather than in the 256 MB tmpfs `/tmp`
became in phase 15. `proxy_read_timeout` is longer than the page's own 40-second wait for the
importer plus the time it takes to store the files — the one thing this box has already got
wrong once (a 120 s proxy in front of a 150 s helper, §31).

The caps are the box's own, not new opinions: `MAX_GAME` **is**
`ark_arcade_install.MAX_DOWNLOAD`, `MIN_FREE_RESERVE` **is** `ark_storage.MIN_FREE_RESERVE`,
and `GAME_EXT` **is** the union of `ark_arcade.SYSTEMS`' extensions. `BOOK_EXT` is the
importer's own list — copied, because the page must not import a script that runs under a
different Python — and **pinned by a test that parses `pi/bin/ark-import-books` with `ast` and
compares the two sets**. A book kind Calibre takes and this page does not know would be a file
the owner could not add; the other way round would be a file handed over that the importer then
ignores, sitting in the staging folder for ever.

### 34.5 The state of the box after this checkpoint

**569 tests became 598 (2 skipped, 26 files)** — `tests/test_admin_upload.py` adds **29**:
twenty-two route and module tests, three about the kinds and caps, two about the address list
and one about the sweep. `python3 -m py_compile` is clean on `ark_ingest.py`, `ark_doors.py`,
`ark_admin.py` and `ark_self_heal.py`; `bash -n` is clean on `60-portal.sh`, `65-admin.sh` and
`90-verify.sh`.

Deployed on the card on **1 Nov 2026** with `--only 60-portal,65-admin` (25 s) then `--only
90-verify` (5 s): both `ARK_INSTALL_COMPLETE`, **no migration** (schema `v6` before and after),
rollback at `/root/ark-db-before-upload-20261101-070909.db` and
`/root/ark-before-upload-20261101-070909.tgz` (the source tree and the live database, 20 MB), and
the verifier's two new lines:

```
✓ Add files page behind the sign-in        https://ark.lan:8443/admin/upload asks to sign in
✓ Addresses page behind the sign-in        https://ark.lan:8443/admin/doors asks to sign in
```

### 34.6 What was proved on the card, and what was not

The box was **rebooted at 07:11:40** and every check below was taken again at 07:12–07:14, so
none of it is a statement about a machine that had been running since the last deploy:

| Proved on the card | How |
|---|---|
| both modules import and behave under the box's own Python 3.9 | `/srv/ark/venv/bin/python -c "import ark_ingest, ark_doors"` → `doors 30`, `books 23`, `games 9`, `batch 536870912`, `files 20` |
| the book writer really writes: a readable name, `0644`, no `.part` left | `spool_book()` against a throwaway folder on the card → `Proof Book-329db313.epub`, 1,400 B, one file, `pi:pi` |
| an unknown kind is classified as nothing, not guessed | the same call: `.epub` → `book`, `.gb` → `game`, `.exe` → `''` |
| the 512 MiB reserve refuses a nearly-full folder | the first attempt with `ARK_INCOMING` under `/tmp` (a 256 MB tmpfs) was refused with *"The card is nearly full"* — the check doing its job, on a real filesystem |
| nginx has the exact location, with the number from the module | `nginx -T` → `location = /admin/upload { client_max_body_size 520m; client_body_timeout 1800s; …` |
| the door passes a real 200 KB body to the app, and **the rest of `/admin` still refuses one** | `POST` 200 KB → `/admin/upload` **403** (the app saying "not signed in"); the same body → `/admin/network` **413** (the prefix's 128k). Two bodies, two limits, one listener |
| both pages ask for a sign-in, before and after the reboot | `/admin/upload` **303**, `/admin/doors` **303** |
| the whole gate after the reboot | `90-verify` at **07:13:41**: `ok: true`, `problems: ""`, `ready.ok: true`, `blocking: []`, `owner_pages {upload: 303, doors: 303}`, `mode hotspot`, `card_check withdrawn`, 116.7 GiB free — and the hotspot answering `200` at `http://10.42.0.1/` |
| the box still does what §28/§33 promised, on the same boot | `ark-fsck-once` ran and had nothing to withdraw (`cmdline.txt` is one line, no `fsck.*`); `/tmp` and `/var/tmp` are **tmpfs from `/etc/fstab`**; `ark-api` still carries `local-fs.target` in `After=`; `ark-mode status` ran as `pi` with no root |

**Not proved, and no shell can prove it:** no file has been picked in `Add files` in a browser.
The phone's file picker, a twenty-file batch over the hotspot, a real EPUB accepted by the real
library through this page, and whether a person reads *"still being added"* as patience are
`docs/HUMAN-TEST-LIST.md` §18. Nothing was added to the live shelf or the live library by this
deploy — the only files written by these proofs were in a throwaway folder that was removed.



## 35. The owner's front page — four figures, and a way out — 1 Nov 2026, later the same day

Signed in, the owner landed on a page that counted **administrators**, **messages** and
**`files` rows**. Two of those are not questions anybody asks, and the third was a count of
records rather than of anything a person could open — the §16.2 defect, still standing on the
one page an owner looks at most. The page also had no way out of it: the session ended when
the browser was closed, or when an idle timeout fired, and nowhere else.

Two more things were wrong the moment anyone looked:

* **the room on the card was only on another page.** It is the single fact an owner opens
  this box to check — the card is the whole machine — and it was two taps away, in the middle
  of a long readiness list.
* **the public pages said nothing at all about the owner's door.** `README.md`,
  `docs/ADMIN.md` and `docs/USING-THE-ARK.md` each name it; the portal a person actually
  reads did not.

```
https://ark.local:8443/admin            ← four figures measured now, and Sign out
```

### 35.1 What the page measures, and what each figure is careful about

| Figure | Where it comes from | The care taken |
|---|---|---|
| **How long the box has been on** | `/proc/uptime` — the machine's own answer | `ark_health.uptime_seconds()` answers `None` on a machine with no such file (the Mac the tests run on), and the page prints *not measured yet* with the reason. The note says a power cut starts the count again, because on this box it does (§26) |
| **Room left on the card** | `shutil.disk_usage()` on `/srv/ark` — the filesystem's `free`, **not** `total - used` | ext4 keeps a slice back for root, and the question is *what can I still put on this?* The percentage is **floored**: 99.6 % free on a card that has something on it must never be printed as *100 % is free* |
| **Files anyone can open in the File Box** | `ark_health.files_section()`'s `files` — the folder, through `ark_storage.present_names()` | a leftover row with no file behind it is **not** a file on offer (the §16.2 defect), and when the records and the folder disagree the line says how many are in that state. A folder that cannot be listed gives `None`, printed as *not measured yet* |
| **Messages on the Message Board** | `SELECT COUNT(*) FROM notes` | one tap to **Community**, which is where they are looked after |

Every one of the four is measured while the page is drawn. `None` never reaches the screen as
`0`: `_stat()` prints *not measured yet* and the sentence that explains it, so *there is
nothing there* and *I could not look* stay different answers (§8.7 of `.clinerules`).

Under the figures, **Put something on the box** carries the two doors a person actually uses
— **Add books and games** (§34) and **Add one game** — because the one thing an owner does
in the first week is put things on it.

### 35.2 Signing out is a POST, and it is worth nothing to a forger

`/admin/logout` is a `POST` behind `_gate_tls()` and the form's own token:

* the **session row is deleted** (`sec.drop_session`), so the cookie stops meaning anything
  everywhere at once — not merely in the browser that pressed the button;
* a line goes into the history, `action="sign-out"`, `result="ok"`, because *who signed in*
  is only half of an access record;
* **a second press is harmless**: the honest answer to *sign out* from a page whose session is
  already gone is the sign-in form, not *that form expired*. It clears both cookies and lands
  on `/admin/login`;
* **a forged press gains nothing**: a token that is not this session's own changes nothing,
  answers `403`, and the person stays signed in — so an image tag on somebody else's page
  cannot end an owner's session. A `GET` would have been exactly that hole.

### 35.3 One tuple is the navigation, the *you are here* mark, and the map

`ark_admin.ADMIN_NAV` is the eleven pages in the order they are offered. `_admin_nav()` draws
them and marks the page being read (`aria-current="page"`), `nav_here()` decides which item a
deeper route belongs to (`/admin/accounts/create` → **Administrators**), and nothing is marked
when nothing matches — the sign-in form is not one of these pages. Three tests hold it: every
item is a route the application serves, every item is a page `ark_doors` names (so a new page
cannot be reachable and unlookupable), and a deeper route belongs to its section.

On **Addresses** the host is now the name the reader actually typed (`ctx.host`) rather than a
second reading of the request, and `_door_link()` draws every door that answers on that name as
a **link**. The first-claim listener (`127.0.0.1:8444`) is printed in full and never linked: it
answers on the box itself, so a phone tapping it would be asking the phone.


### 35.4 The public pages now name the owner's door — in words, never as a link

Every page the application renders carries one sentence in its footer:

> Looking after this box? The owner's own pages are at
> **https://ark.local:8443/admin** — they ask for a password.

`pi/portal/index.html` — a static file nginx serves, so it can drift from the app — carries the
same sentence. It is **not a link**: `/admin` on `:80` is refused twice over (nginx `404`, the
app `403`), so a link there would be a dead end wearing a button's clothes.
`tests/test_public.py` reads `/board`, `/files`, `/household`, `/search?q=water` and the front
page and asserts that no public page carries a link to `/admin` **or to `:8443`**, and that
where the sentence appears it names that address and the word *password*;
`tests/test_admin_dashboard.py` holds the footer itself in the rendered page and checks it
against the banned-words list in `.clinerules` §5.

### 35.5 Tests, and what is not proved

`tests/test_admin_dashboard.py` — **19 tests**: the four figures on the page; the room coming
from the filesystem's own `free`; a page that could not measure saying *not measured yet* and
never `0` (both `card_room()` and `uptime_seconds()` stubbed to `None`); uptime read from a
file the test writes; the *you are here* mark, and a deeper route belonging to its section;
every navigation item being a page the app serves, and being named by `ark_doors`; sign-out as
a token-carrying `POST`, ending the session for real, a second press landing on the form, and a
forged token leaving the session intact; the way back to the reader pages using the typed name;
every reachable door on **Addresses** drawn as a link and the setup door not; and the public
sentence — on the pages the app renders, on the static front page, and in plain words.

**617 passed, 2 skipped** over **27 files** — it was 598 + 2 skipped over 26 files at §34, so
the whole of this section is 19 tests and one file.

**Not proved, and no shell can prove it:** nobody has *looked* at this page. `curl` can say the
four figures are in the HTML; it cannot say whether *"4 days, 7 hours"* and *"47.2 GB free"*
beside *"67.0 GB of 114.2 GB is in use, so 41% is free"* read as sentences a person trusts,
whether the sign-out button looks like a button, or whether the footer sentence reads as an
invitation rather than a warning. `docs/HUMAN-TEST-LIST.md` §19 is the walk-through: sign in,
read the four figures aloud, compare the free space with `df -h /srv/ark`, press **Sign out** on
a phone, and confirm the reader pages still open afterwards.

### 35.6 The deploy — and 28 questions put to the box's own code

`60-portal.sh` and `65-admin.sh` are unchanged by this section (no unit, no schema, no nginx
change — the navigation, the footer and the sign-out route all live in `ark_admin.py`,
`main.py` and two static files), so the deploy was the pair of phases that install them and
then the gate:

```
sudo bash /srv/ark/src/install-ark.sh --only 60-portal,65-admin   → ARK_INSTALL_COMPLETE
sudo bash /srv/ark/src/install-ark.sh --only 90-verify            → green, ok: true
```

| | |
|---|---|
| When | **1 Nov 2026** — files written `15:52:13`, the verifier's receipt `15:55:41` |
| Rollback | `/root/ark-db-before-dashboard-20261101-155206.db` (SQLite `.backup`) and `/root/ark-src-before-dashboard-20261101-155206.tgz`, beside the earlier pair from `15:51` — that pair was taken and then retaken a minute later, and both are kept |
| Schema | **v6 before and after** — this section adds no table, no column and no migration |
| Receipt | `mode hotspot`, `ok: true`, `problems: ""`, `ready.ok: true`, `blocking: []`, `owner_pages.upload: 303`, `owner_pages.doors: 303`, `network.wifi_helper: yes`, `card_check: withdrawn`, 20 collections / 1291 books / 61 games / 11 guide pages / 8 package members / 56,448 game names, **116.4 GiB free**, two copies kept (`ark-backup-20261101-155529`) |
| Live door | `http://10.42.0.1/api/ready` → **200** |
| Stylesheet | no cache-busting needed: `/assets/` on `:8443` already answers `Cache-Control: no-cache`, so a browser asks again and picks the new classes up |

**Then the page was asked, not assumed.** `logs/arksmoke-dashboard-20261101.py` (gitignored,
beside `arksmoke-proof-20261025.py`) starts a throwaway `uvicorn` on `127.0.0.1:18099` from
`/srv/ark/api` — the box's own deployed modules — with `ARK_DB` pointed at a **SQLite `.backup`
of the live records** in `/tmp/arksmoke` and `ARK_ROOT=/srv/ark`, so the four figures are
measured from this Ark and the File Box, the shelf and the collections are the ones a reader
would see, while nothing on the live database is written. A session is minted directly
(`sec.create_session`) and every request carries `Host: ark.lan`, `X-Forwarded-Proto: https`
and `X-Real-IP: 127.0.0.1`, through an opener whose `redirect_request` returns `None` — a `303`
must come back as a `303`, because following it hides a refusal behind a form (§8.7 of
`.clinerules`). On the card, `16:01`:

```
PASS front page answers for a signed-in owner :: HTTP 200
PASS the bar carries all 11 pages
PASS every page in the bar is a link :: 11 of 11
PASS the front page is a figure list, not a form
PASS it says how long the box has been on
PASS it says how much room is left
PASS it counts what a reader can open
PASS it counts the Message Board
PASS every figure is measured or says so
PASS the way into each figure is offered
PASS signing out is a form, not a link
PASS the sign-out form carries the token
PASS the public pages are one tap from here
PASS Addresses answers for a signed-in owner :: HTTP 200
PASS Addresses offers taps rather than typing :: 41 links
PASS the door is a tap: https://ark.lan:8443/admin/health
PASS the door is a tap: https://ark.lan:8443/admin/upload
PASS the door is a tap: http://ark.lan/ask
PASS the door is a tap: https://ark.lan:8445/arcade/player
PASS the setup door is printed, never linked
PASS every group of doors is on the page
PASS a forged sign-out is refused :: HTTP 403
PASS and the session is untouched :: HTTP 200
PASS signing out answers with the sign-in form :: HTTP 303 -> /admin/login
PASS the session is gone from the server :: HTTP 303 -> /admin/login
PASS a second press is not an error :: HTTP 303 -> /admin/login
PASS a plain-HTTP request is refused :: HTTP 403
PASS no cookie means the sign-in form :: HTTP 303 -> /admin/login

RESULT 28 checks, 28 passed, 0 failed
SMOKE_RESULT PASS
```

So on the card: the eleven-page bar is drawn and every item is a link, the front page is the
four figures and not a form, **Sign out is a `POST` carrying the session's own token**, a
**forged** token is `403` and leaves the session usable, the real one ends it (`303 →
/admin/login`) and a **second** press is not an error, a plain-HTTP request is refused, and
**Addresses offers 41 taps** — the four sampled doors are links, the loopback setup door is
printed and never linked, and every group `ark_doors` knows about is on the page.

Three things were wrong **in the probe** before it passed, and all three are the same mistake
in different clothes — asserting on the page through a guess rather than through the page's own
contract: the CSRF token was sent as a **header** when the form sends it as a body field
(`csrf=…`, form-encoded), so every sign-out read `403`; `ADMIN_NAV` was unpacked as three
items after it had become `(href, label)` pairs; and the door-group titles were compared raw
when the page renders them through `esc()` — `html.escape(title, quote=True)` is what the
markup actually holds. A probe that fails for a reason of its own looks exactly like a page
that is broken, which is why it is now checked in before it is believed.

**What is still not proved, and no shell can prove it:** nobody has *looked* at this page with
their eyes. `curl` and this probe can say the four figures are in the HTML and the button is a
form; they cannot say whether *"4 days, 7 hours"* beside *"116.4 GB free"* reads as a sentence a
person trusts, whether the button looks like a button on a phone, or whether the footer sentence
reads as an invitation rather than a warning. `docs/HUMAN-TEST-LIST.md` §19 is that
walk-through — ten minutes, a phone, and one **Sign out** press.


## 36. The Ark recorded its own hotspot as the home network — 1 Nov 2026

`/etc/ark/ark.env` on the card said:

```
HOME_WIFI_CONNECTION="ark-hotspot"        ← this box's own broadcast
BOOT_MODE="hotspot"
```

So *home* and *the Ark's own access point* were the same profile, and `ark-mode home` did exactly
this:

1. take the access point down (`nmcli connection down ark-hotspot`);
2. bring *the access point* back up and call it home;
3. report success.

The box announced a home network it had never been on, while broadcasting on channel 6 with nobody
standing next to it — **and it could not be moved onto the house Wi-Fi at all**. Nothing was logged,
no page said anything, and what the wire shows (a box on its own hotspot, no lease from the router)
is exactly what a box with **no** home network saved shows. That is why it survived the 31 Oct cold
boot and a green `90-verify`: no check was ever wrong, and the wrong fact was in a file nothing
questioned.

The name was written by **phase 7 guessing**. `70-network.sh` used to ask NetworkManager whichever
wireless profile happened to be up while the installer ran and store it as the network to join at
home. On 31 Oct 2026 the box was broadcasting, so the observation was `ark-hotspot` — an
observation turned into a setting. A box cannot join its own broadcast; the question was never one
NetworkManager could answer from the live link.

### 36.1 Which Wi-Fi is home is a setting, and one library decides it

`pi/lib/home-network.sh` (sourced by phase 7, never executed directly) holds the whole decision, so
a test can drive it with a stand-in `nmcli`, no root and no radio:

| Function | The question it answers, and what it refuses |
|---|---|
| `ark_home_mode(profile)` | what NetworkManager says the profile is *for*: `ap` = this box broadcasting, `infrastructure` = a network to join, nothing at all = a profile this box does not have. A question, not an action — a missing `nmcli` answers `""` |
| `ark_home_joinable(profile, hotspot)` | **could `ark-mode home` really join it?** The profile exists, it is a station, and it is not the Ark's own access point. The **name check comes first**, so the broadcast is refused even on a machine where `nmcli` cannot answer about its mode |
| `ark_home_choose(configured, hotspot)` | the name worth recording: the configured one when it can be joined → the build-time profile `home-wifi` → any station profile that is up now (a box whose profile was renamed still has the right network saved). One name, or **nothing at all** — an empty answer is a fact to say out loud, never a name to invent |
| `ark_home_record(env, configured, hotspot)` | writes `HOME_WIFI_CONNECTION` into `ark.env` and answers `kept` / `recorded` / `none` / `no-file` / `unwritable` in `ARK_HOME_RESULT`. Temp file → read back → **only then** `mv` into place, with the original mode preserved, so a cut can never leave `ark.env` half-written (§8.8 rule 17) and a wrong answer is never believed before it is proved |

Phase 7 now sources it and ends on one of three sentences: *home network: "…" (a network this box
can join)*; *home network repaired in `/etc/ark/ark.env`: was "ark-hotspot" (not a network this box
can join — the Ark's own access point is never home), now "home-wifi"*; or *no saved Wi-Fi this box
could join — it keeps broadcasting until one is saved*, naming the owner's own page and
`ark-wifi-add`. **A refusal therefore happens at install time, on the console, instead of a month
later on a hotspot.** `none` is not a failure: a box with no saved Wi-Fi is a supported state, and
nothing is written for it.

Staging still makes the first branch the usual one: `staging/firstrun.sh` saves the build-time Wi-Fi
as the profile **`home-wifi`**, and phase 2 writes that name into `ark.env`. Nothing in the library
switches the radio and nothing there writes anything but `ark.env`.

### 36.2 What the console does with a name it cannot use

| Situation | Before | Now |
|---|---|---|
| `ark-mode home`, home is the AP | took the AP down, brought the AP back up, reported success | `home_problem()` returns one sentence and `cmd_home` dies on it **before the radio is touched**: *"\"ark-hotspot\" is the Ark's own access point, so there is no home network to join — set HOME_WIFI_CONNECTION in /etc/ark/ark.env to the Wi-Fi this box should join (the profile saved at build time is \"home-wifi\"), or save one on the owner's Network page"*. The only unusable answer that gets a sentence of its own is *this name is the box's own broadcast* — a name this machine has no profile for is caught by the join failing (the next row), so nothing has to guess about `nmcli` to refuse |
| `ark-mode home`, the join fails | left the box dark — AP down, nothing up | the AP is **re-raised** before the failure is reported, because the box may never end up somewhere nobody is standing next to it |
| `ark-mode hotspot --auto` (the watchdog's rescue) while home is the AP | `nmcli connection down ark-hotspot` — it took down the very access point it was bringing up, and dropped whoever was reading | the AP is **not** deactivated when home is the AP: one profile, one action |
| `ark-mode auto` with an unusable home name | *"looking for home…"* and then nothing | the search is **suppressed** and the reason printed — a sentence about a network that cannot exist is a lie with a delay |
| `ark-mode status` | `home network` printed whatever was in the file, including the AP, with no hint | prints `home network` **and** `cannot join` beside it when the stored name is this box's own access point, and stays silent when it is not. A name with no profile is printed as it stands: the console does not invent a verdict it cannot prove (`ark_home_joinable()` in `lib/home-network.sh` is what asks the radio, and it is the installer's question, not the status line's) |

The watchdog is unchanged and still right: a pin suppresses only the **return home**, and
`ark-mode hotspot --auto` deliberately leaves no pin, so *the box did this* stays distinguishable
from *the owner said so* (§5, §8.7).

### 36.3 What proves it

`tests/test_network.py` gained two sections: the library driven against a fake radio, and the
**real** `ark-mode` driven with a stand-in `nmcli` first on `PATH` (`ARK_MODE_REHEARSAL=1`, a
throwaway `MODE_FILE`/`PIN_FILE`, so no real profile and no radio is touched) — eleven new test
functions, **15 collected** (one is parametrized five ways), so that file is **47 collected** where
it was **32** and the whole suite is **632 passed, 2 skipped**.

| Test | Holds |
|---|---|
| `test_a_network_the_box_could_not_join_is_never_called_home` (parametrized: the right network, the AP named as an AP, the AP named as a station, an empty name, a profile that has never been saved) | `ark_home_joinable()`'s answers, including the box's own broadcast however `nmcli` describes it |
| `test_the_installer_repairs_a_home_network_that_is_the_boxes_own_hotspot` | the 31 Oct 2026 file exactly: `ark-hotspot` in, `home-wifi` out, `kept`/`recorded` reported honestly |
| `test_a_home_network_that_is_already_right_is_left_exactly_as_it_was` | a correct file is byte-identical afterwards — no rewrite, no churn |
| `test_a_box_with_nothing_it_could_join_records_nothing_and_invents_no_name` | `none`: nothing written, no name made up |
| `test_a_renamed_profile_that_is_up_now_is_still_a_home_network` | the fallback that saves a renamed box |
| `test_a_file_that_never_held_a_home_network_gets_the_line_kept_beside_the_rest` | the append path, and the rest of `ark.env` untouched |
| `test_the_installer_asks_the_library_and_never_guesses_from_the_radio` | phase 7 calls `ark_home_record` and holds **no** `nmcli … --active` guess of its own any more |
| `test_going_home_where_home_is_the_boxes_own_hotspot_is_refused_before_the_radio` | the refusal **and** that no `nmcli` command ran |
| `test_the_rescue_never_takes_down_the_access_point_it_is_bringing_up` | `hotspot --auto` when home is the AP: no `down`, nobody dropped |
| `test_a_home_network_that_does_not_answer_puts_the_access_point_back` | the failed join re-raises the AP and still exits non-zero |
| `test_a_status_line_names_the_home_network_and_says_when_it_cannot_join_it` | `status` says `cannot join`, and says nothing when it can |

`bash -n` is clean on `pi/lib/home-network.sh`, `pi/steps/70-network.sh` and `pi/bin/ark-mode`, and
`python3 -m py_compile` on the test file.

### 36.4 Run on the card, 1 Nov 2026 — and what it left unproved

The phase ran on the box the same day it was written, in this order, and **the box is now on the
house Wi-Fi** with the Ethernet cable left in place as a life-line:

1. `--only 70-network,60-portal`, then `--only 90-verify`
   (`logs/deploy-home-20261101-094307.log`, `logs/verify-home-20261101-094430.log`). Phase 7 said the
   sentence the test had predicted, word for word: *"home network repaired in /etc/ark/ark.env: was
   \"ark-hotspot\" (not a network this box can join — the Ark's own access point is never home), now
   \"home-wifi\""*;
2. read it back — `HOME_WIFI_CONNECTION="home-wifi"`, and `ark-mode status` with **no** `cannot join`
   line, which is exactly what the 31 Oct file could not say. `ark.env` is still `644 root:root`
   (`ark_home_record()` keeps the file's own mode), and `BOOT_MODE` was still `hotspot` at this point;
3. nothing had to be saved — the house network was already there as the build-time profile
   `home-wifi` (SSID `<HOUSEHOLD-SSID>-5G-1`), so `ark-wifi-add` was **not** run;
4. `sudo ark-mode home` joined it: `wlan0` took **<HOME-IP>/24** with a default route, `active
   links: Wired connection 1,home-wifi lo`;
5. everything was then proved **over that Wi-Fi address**, so no answer can be the Ethernet cable
   (`ark.local` resolves to both): `ssh pi@<HOME-IP>` (hostname `ark`), `ping -c2 -I wlan0
   192.168.1.1`, `http://<HOME-IP>/` 200, `/api/ready` 200, and both name-doors forced at the
   radio with `--resolve` — `http://ark.local/` **200**, `https://ark.local:8443/admin` **303** (the
   sign-in). The two bare-`ark.local` answers recorded at this step were **not** a proof of anything
   and are corrected in §36.6: with the cable still in, that name resolved to the box's `eth0`
   address;
6. **what a cold boot does was then changed deliberately.** `BOOT_MODE="auto"` was written into
   `/etc/ark/ark.env` through a temp file and `install -m 0644 -o root -g root` (the file's own mode,
   read back with `grep -qx` before the swap), `sudo ark-mode auto` was run, and
   `/etc/ark/mode.pinned` was confirmed **absent** — nothing is being held. `ark-mode boot --dry-run`
   now answers *"boot would try \"home-wifi\", then broadcast (BOOT_MODE=auto)"*.

**`auto`, not a `home` pin, on purpose.** A pin is an owner's standing order and the watchdog honours
it, so a house router that is switched off — or a box carried to another house — would be left with
nothing. `auto` tries home first and still falls back to broadcasting, which is the property that
makes a box in a cupboard safe.

`--only 90-verify` was then run again with the box **on Wi-Fi**
(`logs/verify-home-wifi-20261101.log`): `ARK_INSTALL_COMPLETE`, **69 ✓ and not one warning**,
`→ network mode: home`, and the receipt reading `mode: home`, `ready.ok: true`, `blocking: []`,
`card_check: withdrawn`, `network.wifi_helper: yes` / `owner_page: 303` / `lan_request: absent`. Every
row the plan named as needing the card answered: 20 collections, 1291 books, 61 games, 56448 game
names, one copy 0 h old, 116.4 GiB free, both new owner pages asking to sign in. And
`/srv/ark/logs/net-watch.state` reads `lost_streak=0 home_streak=0` with **no `net-watch.log` at
all** — the watchdog has had nothing to decide, and a quiet log is a healthy one (§8.7).

**Unproved, and it must be said out loud:**

* **no reboot has been taken since `BOOT_MODE="auto"` was written.** The boot path is proved by
  `ark-mode boot --dry-run` only, so the first real cold boot is still the moment it is tested;
* the box has not yet been **seen** reaching the house Wi-Fi with **no cable in it** — it is proved
  serving over `wlan0`, with the Ethernet still present;
* a power cut, and a house network that is *not* there at boot (does it broadcast as it should?) —
  `docs/HUMAN-TEST-LIST.md` §17.

**Two things noticed on the card, neither of them a fault** (log §36.5): the box carries seven
wireless profiles for three networks, two of which (`home-wifi` and `preconfigured`) name the *same*
SSID with `autoconnect=yes`, so NetworkManager has more than one answer to one question — `ark-mode`
settles it by naming `home-wifi` explicitly, and `ark-net-watch` keeps watching afterwards; and the
older `<HOUSEHOLD-SSID>` / `-5G-2` profiles (`home-wifi-2`, `home-wifi-3`) are left over from earlier
attempts. Deleting a profile is the owner's decision, not the installer's, so none were touched.

### 36.5 What the card's radio actually holds, 1 Nov 2026

Read with `nmcli -t -f NAME,TYPE,AUTOCONNECT connection show`, and the SSID of each asked for
separately (`nmcli -t -f 802-11-wireless.ssid connection show "<name>"`), because two profiles can
carry the same name for different networks and the reverse:

| Profile | SSID | autoconnect | What it is |
|---|---|---|---|
| `home-wifi` | `<HOUSEHOLD-SSID>-5G-1` | yes | the build-time profile, and the one `HOME_WIFI_CONNECTION` names — the box is **on** this one |
| `preconfigured` | `<HOUSEHOLD-SSID>-5G-1` | yes | the image's own copy of the same network: a second answer to one question |
| `home-wifi-3` | `<HOUSEHOLD-SSID>-5G-2` | yes | an earlier attempt, kept |
| `home-wifi-2` | `<HOUSEHOLD-SSID>` | yes | an earlier attempt, kept (2.4 GHz) |
| `<PROFILE-ID>` | `<OTHER-NETWORK-SSID>` | yes | another network this box has been on |
| `<PROFILE-ID>-fast` | `<OTHER-NETWORK-SSID>-fast` | yes | the same, 5 GHz |
| `ark-hotspot` | `The-Ark-Offline` | **no** | the access point; `autoconnect=no` on purpose, because `ark-mode` decides |

The one thing that could bite is the duplicate: after a link blip NetworkManager may bring up
`preconfigured` rather than `home-wifi` (same SSID, so the box is still on the house network — the
watchdog sees a home link and stays), but if that copy's passphrase is stale the join fails and NM
tries the next candidate. `ark-mode boot` and `ark-mode home` both name `home-wifi` **explicitly**,
so a boot is deterministic; only an unattended re-join is a race. Tidying it is one command and the
owner's call:

```bash
sudo nmcli connection delete preconfigured home-wifi-2 home-wifi-3   # only after the box is on the cable
```

Nothing in this repo deletes a profile: `ark-wifi-add` writes one, `ark-mode` names one, and the
owner's Network page lists them without offering to remove any.

### 36.6 The name answered twice, and one of the two answers was the cable — 1 Nov 2026

Found by asking the box the same questions again afterwards, from the Mac, over a link the run had
just called "Wi-Fi":

```bash
ping -c2 ark.local                                  # PING ark.local (<CABLE-IP>) — eth0, not wlan0
ssh pi@<HOME-IP> 'ip -4 -br addr show'           # eth0 UP <CABLE-IP>/24 · wlan0 UP <HOME-IP>/24
ssh pi@<HOME-IP> 'ip route show default'         # default dev eth0 metric 100 · default dev wlan0 metric 600
curl -s -o /dev/null -w '%{http_code}\n' http://ark.local/                    # 200 — over the CABLE
curl -s -o /dev/null -w '%{http_code}\n' http://<HOME-IP>/                 # 200 — over the radio
curl -sk -o /dev/null -w '%{http_code}\n' https://<HOME-IP>:8443/admin     # 000 — no SNI, correct
curl -sk -o /dev/null -w '%{http_code}\n' https://ark.local:8443/admin \
     --resolve ark.local:8443:<HOME-IP>                                     # 303 — over the radio
```

The box has **two** addresses on the house network while the cable is in, mDNS answers with both, and
the resolver hands back whichever it likes — it handed back `eth0`'s. So a green `ark.local` says
nothing about `wlan0`, and the step that was recorded as "proved over Wi-Fi" was, for those two
lines, proved over the cable. It changes no conclusion (`<HOME-IP>` and both `--resolve` answers
stand on their own, and `eth0` and `wlan0` share a default route to the same gateway), but it is the
same defect class as §8.7's *"an HTTP 200 is not evidence that a page works"*: **a proof must name the
thing it is a proof of** — the radio's own address, or `--resolve` when the answer has to come from a
**name**, because `:8443` needs SNI and an IP literal is refused on purpose (`000`, correctly).
`docs/ADMIN.md` §14 carries the receipt; `.clinerules` §8.7 carries the rule.

Two more facts read at the same time, both expected: `ark-net-watch.state` is still
`lost_streak=0 home_streak=0` (`updated=2026-11-01T16:51:58+00:00`) with **no `net-watch.log`** — the
watchdog has nothing to decide while both links are up — and the boot line was not touched, so
nothing about this changed what a cold boot does.

## 37. The published tree no longer carries the household's own details — 1 Nov 2026

`.clinerules` §1 used to say where this project lives, what network the box sits on and what address
it holds there: a workstation account inside an absolute path, the name of the house's own Wi-Fi, the
box's two addresses on that LAN, and a family name that means nothing to anyone outside the house. So
did `README.md`, `docs/ADMIN.md`, `docs/USING-THE-ARK.md`, `docs/HUMAN-TEST-LIST.md`,
`docs/REBUILD-ME-ARK.txt` and this log.

Every one of those facts was **true**, which is exactly what made it a problem. A document set that
describes one real house is a disclosure about one real house, and this repo is meant to be published,
cloned and rebuilt by strangers. Commit `a926296` is the tree half of that work; the guard is
`scripts/ark-secrets-check.sh`, and `tests/test_secrets.py` is what holds the guard.

The work was not "delete a line". It was three things in an order that matters: decide what the tree
may say, move what it said into placeholders (§1's legend — `<ACCOUNT>`, `<HOME-IP>`, `<CABLE-IP>`,
`<HOUSEHOLD-SSID>`), and then build something that **fails** when a placeholder is put back — because
the next person to write a runbook will have the real values in front of them and will type them in
good faith.

### 37.1 What the tree used to say, counted

Measured by running the new checker against the **old** blobs of every file the commit rewrote
(`git show a926296:<path>` into a throwaway folder), never against a memory of what had been there:

**73 findings, in 14 of the 19 files the commit touched.**

| Shape | Findings | What it is |
|---|---|---|
| a private address | **57** | where the box sits on the house LAN, and the router it talks to |
| a workstation account in a path | **10** | `/Users/<name>/…` — the machine's owner, and a tree that only runs from one clone |
| a network name | **6** | the house's own Wi-Fi, in prose and in test fixtures |
| a passphrase | **0** | see below |

The per-file counts sum to the same 73:

| File | Findings | File | Findings |
|---|---|---|---|
| `.clinerules` | 19 | `scripts/03-provision.sh` | 2 |
| `docs/IMPLEMENTATION-LOG.md` | 17 | `scripts/fix-pi-shell.sh` | 2 |
| `docs/ADMIN.md` | 12 | `scripts/publish-github.sh` | 1 |
| `tests/test_network_page.py` | 8 | `scripts/make-firstrun.sh` | 1 |
| `tests/test_accounts.py` | 3 | `tests/test_arcade.py` | 1 |
| `tests/test_auth.py` | 2 | `tests/helpers.py` | 1 |
| `scripts/01-flash.sh` | 2 | `scripts/02-wait-for-pi.sh` | 2 |

The passphrase count is **0**, and that is worth writing down rather than leaving as a gap in a table:
the PSK had always lived in `staging/wifi.env` (`chmod 600`, gitignored) and in `firstrun.sh` on the
card, so it never reached the tree. The shape is still one the checker looks for — the job that would
have needed it is the job nobody has had to do yet.

### 37.2 A checker that knows shapes, not values

The obvious way to guard a tree is a list of the real values. It cannot work here, for two reasons
worth saying out loud: the list **is** the leak (a deny-list of household details, committed to the
repo, is the disclosure it exists to prevent), and it can only ever find a value somebody remembered
to add. So `scripts/ark-secrets-check.sh` matches **shapes**: an account in a path; a private address
other than the Ark's own by-design ones; a quoted network name other than the Ark's own broadcast; a
passphrase-shaped literal assigned to a password-ish name.

Three properties make it usable rather than merely correct:

1. **It prints where, never what.** A line number and a category. A checker that echoes the value it
   found turns every log into a second copy of the secret.
2. **`--staged` reads the index, not the working copy.** This was the first real defect in it: stage a
   file containing a secret, then tidy the working copy, and a disk-reading check passes while the
   *commit* carries the value. `--install-hook` wires it to `pre-commit`, so the refusal happens at
   the commit rather than at review — the only moment it can still be undone for free.
3. **A checker that cannot read must never pass.** Exit `0` clean, `1` a finding, `2` *it could not
   run* (a missing path, an unreadable file). Collapsing `2` into `0` is how a guard becomes
   decoration.

There is exactly one per-line excuse, `# ark-secrets-ok: <why>`, and it requires a reason: the shape
that is genuinely fine — a detector describing its own pattern, a placeholder in a legend — says so
where a reader of the diff will see it.

### 37.3 The proof, in both directions

A guard that has never failed has not been tested. Driven against the **old** blobs: **73 findings,
exit 1**. Driven against the tree as committed: **clean, 185 files read, exit 0**. And the third
outcome was proved too — a path that does not exist answers **exit 2**, not a clean pass. Both ends of
the same command, plus the state in between, which is the only evidence that matters for a guard.

The hook's own mode was then proved against a real staged diff rather than an empty index: with this
section and the `.clinerules` digest staged, `--staged` read **2 files** and answered clean, exit 0 —
so the wiring `--install-hook` writes is exercised on the path a commit actually takes, not just on the
"nothing was staged" path, which is clean for the uninteresting reason.

Three things about running it are worth keeping, because all three cost time here before the cause was
found:

* **Run the extraction and the scan sequentially.** Batched in parallel, a scan could read the old
  tree while the extraction was still writing it, and the answer was a clean pass with the wrong
  count — which reads exactly like a broken checker. It was a race in the harness, not in the guard.
* **`tail`'s exit status travels through a pipe.** A scan that reported "no such file" was a
  mis-read of a pipeline, not a missing file; the checker was right both times. Sequence the
  state-changer, then the observer (`.clinerules` §8.7, which is where both notes now live).
* **A bare run and a path argument are two different questions, and only one of them is about git.**
  With no arguments the checker reads **every tracked file** (`git ls-files`) — that is the 185-file
  clean pass. Given a path it walks the **folder**, so `.venv/`, `logs/` and `staging/wifi.env` come
  with it and a clean tree can still answer `1`. That is the argument doing what it says, not the
  guard being wrong: `ark-secrets-check.sh .` is not the same command as `ark-secrets-check.sh`, and
  reading the second as though it were the first made a passing tree look like a failing one for a
  minute. Ask for the tracked tree when the question is "is what we publish clean?", and name a path
  when the question is "is this file I am about to hand over clean?"

### 37.4 `ROOT` comes from the script's own location

Six of the eight tracked `scripts/*.sh` hardcoded `/Users/<name>/Survival-Pi`, so the tree only ran
from one clone, on one machine, under one account — and every one of those lines named the person.
All **eight** now derive it:

```bash
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
```

Six were rewritten by the commit; `scripts/lib-disk-guard.sh` already did it correctly and is
untouched; the seventh file the commit touched under `scripts/` is the checker itself. A **ninth**
file, `scripts/_flash-helper.sh`, is generated by `01-flash.sh` and gitignored — it necessarily carries
the absolute path of the clone and the resolved card device, which is correct for a file that never
leaves the machine, and §2's guard (`ark_assert_sd_card`) still runs *before* it is written.

Three lines of `scripts/ark-secrets-check.sh` still contain `/Users/`. They are the detector's own
pattern and its `<ACCOUNT>` placeholders — justified, not scrubbed, because a scanner that cannot name
the shape it looks for cannot look for it. Measured, so nobody has to take it on trust: **zero**
tracked files contain the account literal, the house network's name, or either of the box's LAN
addresses.

### 37.5 What the tests hold

`tests/test_secrets.py`, inside `pytest.ini`'s suite — **644 passed, 2 skipped** with everything else:
every shape is found, a placeholder is not, an excused line is skipped only when it carries its
reason, `--staged` reads the index rather than the disk, a missing path exits `2`, a dirty tree exits
`1` and a clean one exits `0`, and the checker's own justification lines are the only `/Users/` the
tree is allowed.

### 37.6 The owner's own decisions — and none has been taken

The tree is clean; the **history is not**, and five things can only be decided by the owner. Each
needs its own explicit yes, and none has been given:

| Decision | Why it is needed | What it touches |
|---|---|---|
| a history rewrite (`git filter-repo` / BFG) | the values are in old blobs, and every clone still carries them | every commit hash on a published branch; a force-push |
| a force-push | a rewrite is not published without one — and this repo's own rule forbids force-pushing | `origin/feature/admin-hardening` |
| a repository-visibility change | what is already public cannot be un-published by editing files | GitHub settings, and anything mirrored from them |
| credential rotation | the passphrase and the accounts are what a rewrite cannot recall | the Wi-Fi passphrase, the Pi password, the GitHub password |
| a sanitised git identity | every commit is signed with a real name and a `<ACCOUNT>@MacBook-Air.local` address — a name and a hostname in the metadata, not in the tree | `user.name` / `user.email`, and every future commit |

The rewrite window is **open right now and will not be again**: `a926296` is committed and **not
pushed**. `origin/feature/admin-hardening` is at `08e8f9b`, one behind, and `origin/main` has not
moved — so the cheapest moment to decide is before the next push, never after it.

> **Superseded the same day — see §38.7.** `a926296` was pushed, so that window has **closed** and
> the five decisions above are now a rewrite **plus a force-push** rather than a fast-forward that
> could still have avoided one. The ranking and the ownership of each decision are unchanged; this
> paragraph is left as it was written so the record of what was believed when stays honest.

### 37.7 What a tree scan cannot say

A clean tree is not a clean repository, and the difference is measurable:

* `origin/feature/admin-hardening` **at its tip** still carries all four literals: the account in
  **7** files, the house network's name in **3**, the box's LAN address in **3**, the family name in
  **2**;
* `origin/main`'s tip carries the account in **7** files and none of the other three;
* in that branch's history the literals are touched by **3**, **4**, **6** and **3** commits
  respectively, so they are in the objects and not only in a checkout;
* and **11** commits on `origin/main` carry the account in an author or committer field.

Deleting a file does not delete a blob, and editing a line does not edit a commit. That is the whole
of §37.6, stated as evidence rather than as a worry. What a new clone of this repository inherits
today is the tree of `08e8f9b`, not the tree of `a926296` — and that is the sentence to keep in front
of the owner when the five decisions above are put to them.

---

## 38. A put-back must not carry the old database's journal with it — 1 Nov 2026

> **Corrected in §39.5:** every hazard and measurement below stands, but the put-back treats the
> `-shm` as best effort and only the `-wal` as mandatory — and §39 wired in the repair lock this
> checkpoint left uncalled.

`ark_self_heal.heal_sqlite()` is the one program on this box allowed to replace `ark.db`, and §26
already proved the half of that which matters most: a damaged file is replaced **only** by a copy
that passes the same `quick_check`, and a database with no good copy is left exactly where it is.
This checkpoint asked the question next door — *what does the repair leave **beside** the file?* —
because a SQLite database is not one file. `ark.db` in WAL mode is three: the file, `ark.db-wal`
(the commits not yet in it) and `ark.db-shm` (the index of those commits). A cut leaves all three.

So the put-back has to answer for two things at once, and only the first had ever been tested:
the bytes that come back, and the journal that was already there when they arrived.

### 38.1 The hazard, measured

The shape is an ordinary one on this box: a card whose database is ruined, a good copy taken by
`ark_atomic` earlier (`.bak`), and the ruined database's own journal still sitting in the folder.

Built and read, not imagined (`tests/test_self_heal.py::a_card_with_an_old_journal`): a main file
holding **4** rows, a copy of it taken, then **47** more rows committed by a writer so they exist
only in the journal — **4,152 B** of journal. Put that journal beside the 4-row copy and the file
reads as **51** rows:

| what is in the folder | rows the file answers |
|---|---|
| the 4-row copy, alone | **4** |
| the 4-row copy **plus the old journal** | **51** |

Nothing about that is a corruption. The 51 rows are a valid database; 47 of them are commits from
*a different database*, played back into a file that never held them. `quick_check` passes, the page
count is healthy, and the repair reports `restored`. That is what makes this worth a test rather
than a comment: the failure is silent, and it is the one failure mode a repair must not have —
coming back with **more** than the copy it was given.

### 38.2 Why the journal has to be copied before anything reads it

The obvious way to write this test is to build the card, read it, then snapshot the journal. That
test would be worthless, and measuring why took one probe (`/tmp/ark-wal-probe11.py`, 1 Nov 2026):

| moment | `ark.db-wal` |
|---|---|
| writer open, 47 commits made | **4,152 B** |
| a second connection has read all 51 rows | **4,152 B** — a read does **not** take it |
| the **last** connection closes | **0 B** — SQLite checkpoints the frames into the file and truncates |
| a copy of the journal taken then | **0 B** → beside the 4-row copy, **4 rows** ("replaying it is harmless") |
| a copy taken while the writer held it | **4,152 B** → beside the 4-row copy, **51 rows** |

So the ordering is load-bearing, and the first version of the helper's own explanation was wrong:
a *read* is innocent, the **close** is what empties the journal. `a_card_with_an_old_journal()` now
copies while the writer still holds it and asserts `journal.stat().st_size > 0` afterwards, which is
the guard against this helper quietly becoming the test that proves nothing — the same family as
*"a fixture that is not the real file's shape tests the fixture"* (§26.1).

### 38.3 What the shipped put-back really promises

`_copy_over()` was measured as it ships, not as it reads. On that card:

* it answers **`True`**;
* **neither** `-wal` nor `-shm` is beside the file afterwards, including an `-shm` the test handed
  it deliberately (the index means nothing once the journal it describes is gone);
* the file reads its own **4** rows — not the 51 the journal carried.

That is a narrower promise than "nothing is ever replayed", and the tests say the narrow one:
**the journal does not survive the put-back**. The drain is what carries the guarantee, and a test
that claimed more would be describing SQLite rather than this box.

### 38.4 A repair that cannot be finished, and the whole pass

Two more shapes, both measured:

* **Refusal.** With a *folder* where `ark.db-wal` would be, `_copy_over()` answers **`False`**, the
  database's bytes are unchanged, and the obstruction is still exactly where it was — nothing was
  forced through to get the new file in. A repair that cannot be finished is worse than one that
  did not start, so this is asserted rather than inferred from the return value.
* **The whole pass.** A main file that really is not a database (`quick_check` → `damaged`), the
  old journal beside it, and the kept copy staged as `.good.bak`: `heal_sqlite()` answers
  **`restored`**, and the folder afterwards holds no `-wal`, no `-shm`, and a database reading
  **4** rows. That is the end-to-end version of §38.3 on a card as a cut really leaves it.

### 38.5 What the tests hold — and proof that they would notice

Four tests and two helpers (`bodies_in()`, `a_card_with_an_old_journal()`) in
`tests/test_self_heal.py`, beside the existing "plain `cp` would have missed this row" block:

| test | what it asserts |
|---|---|
| `test_a_journal_beside_a_database_replays_rows_that_file_never_held` | the hazard itself — 4 rows read as 51 (§38.1) |
| `test_the_put_back_takes_the_old_journal_with_it` | `_copy_over` → `True`, both sidecars gone, the file's own 4 rows |
| `test_a_journal_that_will_not_go_stops_the_put_back_and_changes_nothing` | `False`, bytes unchanged, obstruction still present |
| `test_a_database_put_back_beside_an_old_journal_does_not_replay_it` | the whole pass: `damaged` → `restored`, no sidecars, 4 rows |

**Sensitivity, measured rather than assumed.** A green suite has to mean the drain is doing the
work, so the shipped function was patched out (`_drain_journal = lambda target: True`, which is the
defect exactly — a put-back that removes nothing) and the file re-run:

```
FAILED tests/test_self_heal.py::test_the_put_back_takes_the_old_journal_with_it
FAILED tests/test_self_heal.py::test_a_journal_that_will_not_go_stops_the_put_back_and_changes_nothing
FAILED tests/test_self_heal.py::test_a_database_put_back_beside_an_old_journal_does_not_replay_it
3 failed, 2 passed, 68 deselected
```

Three of four. The fourth still passes on purpose: it measures the **hazard**, not the fix, so it
must pass whatever the code does. `tests/test_self_heal.py` is **73 passed** (was 69) and the whole
suite is **653 passed, 2 skipped** over **28 files**; `python3 -m py_compile` is clean on
`pi/api/ark_self_heal.py` and the new test file, `bash -n` on the secrets guard, and
`scripts/ark-secrets-check.sh` reads **186 tracked files and answers clean, rc 0**. No production
line changed — this is evidence about code that was already on the card.

### 38.6 What this does not prove

* **No power cut has ever been simulated on hardware.** These are the shapes a cut leaves, built in
  a temp folder; `docs/HUMAN-TEST-LIST.md` §15 is still the item that would turn them into a
  measurement on the card.
* The put-back has never run **in anger on the box** (§8.9 item 0e) — the only repair pass that has
  run there found nothing to repair (`repaired: 0`).
* Measured on the Mac's **SQLite 3.51.0** under Python 3.9.6; the card runs its own SQLite, whose
  version this pass did not read. Checkpoint-on-last-close is the engine's behaviour rather than
  Python's, but "the same promise on that build" is a claim nobody has taken yet.
* Nothing here says the box's `ark.db` ever *is* in this shape. It says that if it is, the repair
  now has a test that notices when the journal is left behind.

### 38.7 A correction to what §37's digest said about the rewrite window

Found while committing this checkpoint, and it matters because it changes what the owner's five
decisions cost: the digest said the rewrite window was open *only until the next push*, because
`a926296` was committed and **not pushed** with `origin/feature/admin-hardening` at `08e8f9b`.

**That window has closed.** Read locally on 1 Nov 2026:

```
$ git for-each-ref --format='%(refname) %(objectname:short)' refs/remotes/origin/
refs/remotes/origin/feature/admin-hardening 1f5a872
refs/remotes/origin/main dd3336c
$ git merge-base --is-ancestor a926296 refs/remotes/origin/feature/admin-hardening && echo IN
IN
```

So `a926296` — the commit that replaced the household's own details with placeholders — is part of
the **published** history, and three commits have gone out on top of it (`5b6ddef`, `03b41b4`,
`1f5a872`). `origin/main` is still at `dd3336c` (`v1.0.5`, unmoved, never force-pushed). The
practical effect: the five decisions in §37.6 are now a **history rewrite plus a force-push** — which
the rules forbid without the owner's explicit yes — rather than something a fast-forward could still
have avoided. Nothing here decides that for them; §37.6 is where the ranking lives.


## 39. One repair pass at a time — and an index that is only an index — 1 Nov 2026

### 39.1 What was found in the working tree

§38 was committed as evidence about code that was **already on the card**. The working tree at that
commit held two edits that were never finished, and neither could be seen by any test:

* `lock_path()`, `LOCK_SUFFIX`, `LOCK_WAIT` and `_repair_lock()` — a complete `flock` guard **that
  nothing called.** Phase `15-resilience.sh` installs `ark_self_heal.py` onto the box, so the only
  copy a person would ever read there was a lock nothing took. A guard that is written and not
  wired is worse than one never written: it reads as protection in review and protects nothing.
* `_drain_journal()` had been changed so the `-wal` stayed **mandatory** and the `-shm` became
  **best effort**, while §38's prose and the function's own docstring still described both sidecars
  as required — and nothing measured either half.

The choice was to finish the work or throw it away. It was finished, because the hazard the lock
exists for is real and the `-shm` rule is measurable. This entry is the record of both halves.

### 39.2 The hazard: two put-backs of one database

A repair pass can meet another one. The boot pass runs `Before=ark-api.service`; the daily timer
fires at 04:17 ±20 m (`Persistent=true`, so a missed run happens at the next boot); and a person can
run `sudo ark-self-heal` by hand while either is going. Each pass puts its copy back through
`_copy_over()`: read the candidate, drain the old journal, replace atomically, drain again. Let two
of those interleave and the file left in place is assembled from both — the one program on this box
that must never be able to lose anything becomes the program that mixes two whole files into one
that neither pass wrote. Nothing in the receipt would say so: both passes would have answered
`restored` about a file that is now neither copy.

### 39.3 What changed

* **`_repair_lock(target)`** holds `flock` on a file beside the database and answers `True` while
  held, `False` if it cannot be taken within `LOCK_WAIT = 10 s`. A repair is a handful of file
  operations, so a second pass normally waits milliseconds; past ten seconds the survivor reports
  the wait rather than guessing what the other writer is doing. The kernel drops the lock if the
  process dies, which a lock file written by hand cannot promise. On a platform with no `flock`
  (`fcntl is None`) it yields `True`: the lock guards against two passes at once, it is not a
  condition for repairing anything, and refusing every repair there would be the worse failure.
* **`lock_path()`** is `<name>.repair.lock`, beside the database and deliberately **not** SQLite's
  own lock — that one lives in the `-shm` sidecar, which is one of the files a repair removes, and a
  lock a repair can delete is not a lock. No rule in `SWEEP_RULES` can match it, so an old one is
  never swept and a leftover one means nothing.
* **`heal_sqlite()`** takes the lock around the whole decision-and-replacement step — the candidate
  list, each `quick_check`, and `_copy_over()` — and returns a new state, **`locked`**, when it
  cannot be had. `locked` is neither a repair nor a fault: `run()` prints its sentence and does
  **not** put it in `problems`, so a pass that lost the race cannot make the receipt exit 1 about a
  database that is being repaired at that very second. The pass that wins keeps the record.
* **`_drain_journal()`** keeps the `-wal` mandatory and treats the `-shm` as best effort, with the
  measurement written down in its docstring: a junk `-shm` with no `-wal` still answers
  `quick_check ok`, because the index describes a journal and means nothing without it. A card that
  refuses to give up the index is no longer a reason to abandon an otherwise good repair; a card
  that refuses to give up the **journal** still is, and still changes nothing at all.


### 39.4 What the tests hold — seven new ones, and what they would notice

| test | what it asserts |
|---|---|
| `test_two_passes_cannot_repair_the_same_database_at_once` | with the lock held by a second open of the same file, `heal_sqlite` answers `locked`, the bytes are unchanged, and the file is still `damaged` |
| `test_a_database_another_pass_owns_is_not_a_problem_to_look_at` | the whole pass reports it with its own state, puts nothing back, `problems == []`, and no *Still to look at* line |
| `test_the_lock_is_given_back_when_the_pass_is_done` | after a put-back the next pass takes the lock with no wait, and the file reads its own 4 rows |
| `test_a_lock_beside_one_database_does_not_hold_up_another` | a held lock on `other.db` leaves `ark.db` repairable — the lock is per database, not per folder |
| `test_the_repair_lock_is_never_swept` | a 90-day-old `.repair.lock` survives `sweep()` |
| `test_only_the_journal_is_mandatory_and_the_index_is_not` | `-shm` as a folder ⇒ `_drain_journal` answers `True`; `-wal` as a folder ⇒ `False` |
| `test_a_platform_without_flock_still_repairs` | with `heal.fcntl` set to `None`, a damaged database is still put back |

**Sensitivity, measured rather than assumed.** `_repair_lock` was replaced with a no-op context
manager (the defect exactly: a guard that is there and takes nothing) and the shipped function asked
about the same damaged file with the lock held by somebody else:

```
shipped code, lock held : locked   | unchanged: True
no-op lock (the defect) : restored | unchanged: False
```

So the two tests that matter fail without the wiring, and they fail for the right reason. The
"given back" test passes either way and says so: with no lock there is nothing to give back. The
`-shm` rule is held by `test_only_the_journal_is_mandatory_and_the_index_is_not` alone, and that is
deliberate — it asks the rule directly instead of through a fixture that would have to be built to
refuse an `unlink`, which a temp folder does not do.

`tests/test_self_heal.py` is **80 passed** (was 73) and the whole suite is **660 passed, 2 skipped**
over **28 files**; `python3 -m py_compile` is clean on `pi/api/ark_self_heal.py` and the test file.

### 39.5 A correction to §38's prose, and what still stands

§38 described the put-back as removing **both** sidecars. That is still what it does on every card
whose `-shm` is an ordinary file — which is every card anybody has seen, and why
`test_the_put_back_takes_the_old_journal_with_it` is unchanged and still passes. What changed is the
*refusal* rule: a card that will not give up the index no longer abandons the repair, where a card
that will not give up the journal still does. §38's measurements — 4 rows reading as 51 when the old
journal is put beside them, 4,152 B while the writer is open and 0 B once the last connection has
closed — are untouched and remain the reason this drain exists at all.

### 39.6 What this does not prove

* **No two passes have ever raced on the box.** No repair there has had anything to repair
  (`repaired: 0` on 1 Nov 2026), so the lock has met a second open of the same file only in a temp
  folder on the Mac.
* The lock's `flock` semantics were measured on the Mac's own filesystem. ext4 on the card is the
  kernel's, but nobody has raced two passes against it.
* **No power cut has been simulated on hardware** and the put-back has never run in anger (§8.9 item
  0e, `docs/HUMAN-TEST-LIST.md` §15).
* Nothing here makes a repair **safe** — it makes two repairs of one file impossible at the same
  moment. One wrong copy put back deliberately is still the owner's decision, and `ark-restore` is
  still the only program that may replace records on purpose.

### 39.7 On the card, 2 Nov 2026 — the module carried over, and the phase deliberately not re-run

Committed as `c662c88` and pushed to `origin/feature/admin-hardening` (`origin/main` untouched at
`dd3336c` = `v1.0.5`). The box was up (1 day 6 h, `ark-api` active) and was deployed **as the phase
would install this module and nothing else**:

```
$ sha256sum /srv/ark/api/ark_self_heal.py
0614bf57dbefaf3d3e42f79ba02087c77cb1815444f8726dc7d28f602be8b99f   (= the repo copy)
-rw-r--r-- 1 root root 53687  /srv/ark/api/ark_self_heal.py          (root:root 644, as phase 15)
rollback: /root/ark-db-before-lock-20261102.db (290,816 B, sqlite3 .backup, taken first)
```

Proved on the box, under the box's own Python 3.9: `lock suffix: .repair.lock | wait: 10.0`,
`heal_sqlite takes the lock: True`, `locked is its own state: True`. Then a real pass, through the
unit the boot runs (`sudo systemctl start ark-self-heal.service`) — `ok: true`, `healed: 0`,
`problems: []`, **no `.repair.lock` left beside any database**, which is right: on a healthy card
`heal_sqlite()` returns long before the lock exists. `--only 90-verify` came back green: **74 ✓** — a
whole provisioning run, so the verifier's own 69 plus `03-provision.sh`'s five (see §41.4) — **no
warning, no failure**, receipt `ready.ok: true`, `blocking: []`, `card_check: withdrawn`,
`self_heal: {repaired: 0, left_to_look_at: 0}`, `mode: home`, 20 collections (86.5 GiB), 1291 books,
11 guides, 116.3 GiB free.

**Phase `15-resilience` as a whole was deliberately not re-run, and that is the honest record.** Its
first section is exactly this copy, but its boot-line section re-adds `fsck.mode=force
fsck.repair=yes` whenever they are absent — they were withdrawn by `ark-fsck-once` on the 31 Oct cold
boot, so re-running the phase would arm a full card check on the owner's next reboot for a change
that does not touch the card's filesystem. Everything else in the phase (fstab, sysctl, watchdog
drop-ins, journald, Calibre WAL, the units) was already read back from that card on 26 Oct and is
unchanged by this checkpoint; `15-resilience.sh` is idempotent, so the next time it *is* run nothing
here will be undone — it will simply also arm one forced check, which `ark-fsck-once` takes back off
again on that boot.

---

## 40. A search that asked twelve of twenty books, and a "no" that was not one — 2 Nov 2026

### 40.1 What the box was doing

Three defects in the same function, and not one of them could fail a test that existed:

* **`MAX_COLLECTIONS = 12` while the card holds twenty.** The ceiling was written as a guard against a
  pathological card (a downloader queue that had left a hundred half-finished ZIMs in the folder) and it
  was never a page size — but a guard above nothing is a limit, and this one was *below* what the box
  really holds. Eight collections were never asked anything at all, and the page then printed
  **“Nothing matched … yet”** about a search that had not happened in almost half the library.
* **An empty answer and no answer were the same value.** `_parse_feed()` returned `[]` for all three of
  *a real feed with no hits*, *XML that did not parse*, and *the library's own
  `<error>Content not found</error>` document* — which parses perfectly well, reports nothing, and is
  exactly what a wrong collection name produces. `_kiwix_one()` caught its own `URLError`s nowhere, so a
  refusal became an exception the caller swallowed as "nothing found". The page therefore had one
  sentence for two opposite facts: *the books do not have this* and *the books did not answer*.
* **`snippet[:220]` is a slice, not a summary.** It ends wherever character 220 falls. The live box was
  seen serving `…do you carry wat`, and `disinf`, and snippets ending in a dangling comma — a good answer
  that reads as a broken page, in the one place a reader decides whether a hit is worth tapping.

### 40.2 What changed — `pi/api/main.py`

| Piece | Now |
|---|---|
| `MAX_COLLECTIONS` | **20** — above what this card holds, so a real box is never silently clipped. The comment says out loud that it is a guard, not a page size, and why |
| `SEARCH_DEADLINE` | **8.0 s** (was 6.0). Measured, not guessed: see §40.4 |
| `SNIPPET_CHARS` | **220**, unchanged as a *limit*, but it is now handed to a function instead of a slice |
| `shorten(text, limit)` | new. Fits → shown whole with **no** ellipsis (nothing was dropped); else stop at a sentence end when one lands in the back half of the window, else at the last whole word; `rstrip(" ,;:—-–(")` drops a dangling comma or dash; `" …"` is added **only** when words really were left out |
| `_tag(node)` | new. `{http://www.w3.org/2005/Atom}entry` → `entry`. Without it an Atom shelf parsed to a feed with **no** entries, because every item was skipped by its own namespaced tag — a real answer read as an empty one |
| `_FEED_ROOTS` | `("rss", "feed", "rdf", "channel")`. Anything else that happens to parse — an error page from something in front of the reader — is **not** an answer, however well-formed |
| `_parse_feed()` | returns `list[dict] | None`. **`None`** = that was not an answer (unparsed, `<error>` root, a root that is not a feed); **`[]`** = a real answer that held no hits. The two used to be one thing, which is how a lookup that never happened came out looking like a lookup that found nothing |
| `_kiwix_one()` | the same contract; it no longer pretends a refusal is an answer |
| `_search_report()` | new, and the only thing `search()` asks. Returns the hits **and** `asked` / `answered` / `failed` / `late` / `available` |
| `library_state(report)` | new. One of five words: `found`, `empty`, `partial`, `silent`, `no-books` |
| `search_library()` | kept as `_search_report(pattern)["hits"]` so every existing caller still gets a hit list |

### 40.3 The five states, and why only one of them may say "no"

| State | When | The page says | Dot |
|---|---|---|---|
| `found` | at least one hit, whatever else happened | the count, and the blocks | green |
| `empty` | every collection that was asked **answered**, and none had it, **and** the box holds no more than one search asks | *Nothing matched “…” yet* + **All n collections were asked and none of them had it** | green |
| `partial` | the search did not cover the library and nobody had it — either some asked collections never answered, **or** the card holds more books than one search asks | *…the look-up stopped early, so this is not the whole library* + **Look again** | amber (`dot wait`) |
| `silent` | **nothing** answered at all — the lookup failed, it did not come back empty | *The big library could not be looked in just now* + **That is not the same as “nothing found”** | red (`dot no`) |
| `no-books` | nothing to ask: no collection on the card, or no index to read the list from | *The Offline Library has no list of its books yet, so the big look-up had nothing to read* | amber |

Two details that are load-bearing:

* **`partial` has two different causes and `shortfall()` words them by the counts, not by a guess.** Books
  that were asked and never answered — *“Only 7 of 20 collections answered in time”* — and books that were
  never asked — *“Only the first 12 of the box's 20 collections were looked in”* — are different facts, and
  saying the wrong one is a lie in plain English. The comparison is `asked < available`, which is exactly
  the ceiling biting.
* **`no-books` is not `empty` and not `silent`.** A box whose library folder has not filled up yet is not
  broken, and it must not read as *nothing matched*. It is amber, and it says the library fills up as the
  box has time.
* The `found` branch still says a sentence when the library half is missing (`partial` / `silent` /
  `no-books` with hits from the manuals or the shelf), because a broken lookup hidden behind a tidy list of
  manual pages is the same lie one size smaller.



### 40.4 Measured on the card, before the deadline was chosen

`SEARCH_DEADLINE` is not a round number picked to feel safe. The question was *how long do twenty
collections really take on this box*, and the only way to answer it was to ask the live library. A script
on the Mac (`/tmp/time-search.py`) reads the real collection list from
`/kiwix/catalog/v2/entries?count=-1`, then asks all twenty through the box's own nginx with a six-worker
pool — the same shape as the app. **Run three times, because one sample of a timing is not a measurement:**

```
run 1  collections: 20
       pattern='water'        wall=4.31s slowest=3.34s fastest=0.22s
          3.34s  22375 B  wikipedia_en_all_nopic_2026-06
          1.75s  24484 B  gutenberg_en_lcc-r_2026-03
          1.62s  21272 B  wikivoyage_en_all_maxi_2026-09
          1.62s  22278 B  wiktionary_en_all_nopic_2026-08
       pattern='bleach water' wall=2.21s slowest=1.72s fastest=0.03s
          1.72s  23137 B  gutenberg_en_lcc-s_2026-03
          1.69s  23072 B  ifixit_en_all_2025-12

run 2  pattern='water'        wall=8.04s slowest=5.96s fastest=0.47s
          5.96s  22375 B  wikipedia_en_all_nopic_2026-06
          2.84s  24484 B  gutenberg_en_lcc-r_2026-03
          2.38s  23133 B  wikipedia_en_medicine_maxi_2026-10
       pattern='bleach water' wall=4.53s slowest=3.67s fastest=0.04s
          3.67s  22811 B  gutenberg_en_lcc-r_2026-03

run 3  pattern='water'        wall=8.04s slowest=5.43s fastest=0.25s
          5.43s  24149 B  woodworking.stackexchange.com_en_all_2026-10
          4.11s  22375 B  wikipedia_en_all_nopic_2026-06
          3.79s  22951 B  wikiversity_en_all_maxi_2026-05
       pattern='bleach water' wall=4.01s slowest=1.72s fastest=0.04s
          1.72s  21385 B  wikipedia_en_all_nopic_2026-06
```

Three things this says, and two of them are uncomfortable:

1. **A common word over twenty collections costs 4.3–8.0 s of wall clock, and the slowest single book
   takes 3.3–6.0 s of it.** It is not always the full Wikipedia that is slow — run 3's slowest was the
   woodworking Stack Exchange at 5.4 s — so there is no "the big one, ask it first" ordering that would
   help.
2. **The spread between runs is a factor of two** (4.3 s and 8.0 s for the same word on the same card,
   minutes apart). A deadline chosen from a single sample would be a guess about a box having a good
   moment.
3. **`8.0 s` therefore sits at the top of the measured range, not below it.** On a busy card the last
   wave can be cut off — which is *exactly* what the counts and the `partial` state exist for: the reader
   gets the hits that did arrive and a sentence saying *only n of 20 answered in time*, instead of a
   spinner, a 504, or a false "nothing matched". Six seconds would have cut into the normal case; twenty
   seconds would hold a page while somebody is standing there. If the owner's own searching shows
   `partial` often, the next honest change is `SEARCH_WORKERS` (measuring first, `.clinerules` §8.7 warns
   a timed-out worker is abandoned, not stopped) or a longer deadline — a decision to take with evidence,
   not a number to nudge.

`pi/nginx/ark.conf` already gives this route far longer than eight seconds (`proxy_read_timeout 120s` on
the search location, 300 s on `/ask`), so the page is the shortest patience of the three and the proxy can
never turn a slow answer into a 504 — **no nginx change was needed, and none was made.**

`SEARCH_WORKERS` stays at **6**, unchanged and on purpose: twenty collections over six workers is four
waves, the measurement above says that is the shape of the cost, and more concurrency would put more than
six simultaneous Kiwix queries on a Pi 4 while somebody is reading. A worker that misses the deadline is
**abandoned, not stopped** (`.clinerules` §8.7), which is the other reason the counts travel with the hits:
the page can say how many answered instead of pretending.

### 40.5 Tests — `tests/test_search.py`, 13 → 39

**Measured, not assumed:** with `pi/api/main.py` put back to the committed version and this test file
left alone, pytest answers **26 failed, 13 passed** — so every one of the 26 new tests fails against the
code as it was, and the 13 that were already there still pass. Grouped by what they hold:

| Holds | Tests |
|---|---|
| a card holding more books than one search asks is reported, not clipped | `test_a_box_holding_more_books_than_one_search_asks_is_not_an_empty_library`, `test_the_page_says_which_books_it_never_asked` |
| `[]` vs `None` — a real feed with no hits; bytes that are not XML; the library's own `<error>` document, with and without a namespace; a page that parses but is not a feed; a namespaced Atom shelf that really did answer | `test_a_feed_with_no_hits_is_an_empty_answer_and_not_a_failure`, `test_bytes_that_are_not_xml_are_not_an_answer`, `test_the_readers_own_error_document_is_not_an_answer`, `test_an_error_document_carrying_a_namespace_is_still_not_an_answer`, `test_a_page_that_parses_but_is_not_a_feed_is_not_an_answer`, `test_an_atom_shelf_is_read_even_though_every_element_carries_a_namespace` |
| the five states, counted rather than guessed, and `no-books` never reading as `empty` | `test_an_empty_feed_from_every_collection_is_the_only_empty_answer`, `test_a_collection_that_never_answered_is_not_counted_as_an_empty_one`, `test_a_collection_that_raised_is_not_counted_as_having_answered`, `test_the_collections_the_deadline_left_behind_are_counted_as_late`, `test_a_hit_is_found_whatever_else_the_library_did`, `test_a_box_with_no_books_is_not_a_search_that_failed` |
| the words the page may and may not print — *nothing matched* only from `empty`, a gap worded as a gap, a fault worded as a fault, an empty shelf not blamed on the search | `test_the_page_may_say_nothing_matched_only_when_every_book_answered`, `test_the_page_names_how_many_books_answered_when_some_did_not`, `test_a_library_that_answered_nothing_is_worded_as_a_fault_not_as_an_answer`, `test_a_box_with_no_books_says_so_instead_of_blaming_the_search` |
| answers from the manuals never hide a library that never answered | `test_answers_from_the_manuals_do_not_hide_a_library_that_never_answered`, `test_answers_from_the_manuals_say_that_the_library_half_is_missing` |
| `shorten()` — no ellipsis when nothing was dropped, never mid-word, a finished sentence preferred, dangling punctuation dropped, nothing to cut on still bounded, and the cut made on the way in so the page never holds a half word | `test_a_snippet_short_enough_to_fit_is_shown_whole_with_no_ellipsis`, `test_a_long_snippet_is_cut_at_a_whole_word_and_says_so`, `test_a_snippet_prefers_a_finished_thought_when_one_lands_near_the_end`, `test_a_comma_the_cut_left_dangling_is_not_shown`, `test_a_snippet_with_no_space_to_cut_at_is_still_bounded`, `test_a_long_description_reaches_the_page_cut_at_a_word` |

One of the 13 that were already there is the guard that now finally sits above something:
`test_only_a_bounded_number_of_collections_is_asked` still holds that a ceiling exists — what changed is
that the ceiling is no longer *below* what this card holds.

The whole suite: **686 passed, 2 skipped, 28 files** (was 660).

### 40.6 What is deployed and what is not

**The code is committed here; the box is still running the old `main.py`.** `/srv/ark/api/main.py` on the
card predates this whole change, so a search on the live box today still asks twelve collections and still
prints one sentence for three facts. Deploying it means replacing that one file and restarting `ark-api`
— no migration, no schema change, no unit change, no nginx change — and the rollback is the old file.

### 40.7 Still unproven

* **No browser has drawn any of the three "nothing found" states.** They are proved out of the HTML
  (`tests/test_search.py`), which can say the sentence and the dot class are there; it cannot say whether
  amber reads as *try again* rather than *something is wrong*.
* **The timings were taken on a quiet box, and not under load.** One reader, nothing else asking. Nothing
  has searched while two other people were reading, so the wall clock under load is unmeasured — and §40.4
  already shows a factor-of-two spread between runs on an idle card.
* **`partial` is reachable, not theoretical** — a `water` search took 8.04 s twice out of three on this very
  card, which is the deadline to the hundredth of a second — but **nobody has seen the sentence on a
  screen.** The way to see it on purpose is to restart `ark-kiwix` and search in the same second
  (`docs/HUMAN-TEST-LIST.md` §20, step 7). If the owner's own searching never shows it, that is the good
  outcome and has to be reported as one, not as "the test failed".
* **The 24-hit cap inside `_search_report()` is still a cap** — twenty collections × 4 hits is up to 80,
  and the page shows 24. What this section wrongly accepted as a page-size decision was that the 24 were
  taken from a list sorted by collection name, which made the cap a *breadth* limit: on the card, *water*
  filled all 24 places from six collections and the other fourteen could not appear (§41). The cap is now
  spent by `_spread()`, one hit per collection before any collection's second, and a very common word still
  shows the top of each shelf rather than all of it — which is the page-size decision, and deliberate.

---

## 41. Twenty books were asked and six of them answered — 2 Nov 2026

§40 fixed the search that only looked in twelve of the box's twenty collections. It left the other half of
the same fault standing, and its fix made the fault easier to see: `_search_report()` asked every
collection, kept up to four hits from each, sorted the lot **by collection name**, and cut the list at
`LIBRARY_HITS = 24`. Twenty collections answering four each is up to eighty answers for twenty-four
places, and the sort key was the collection's own name — so a common word filled every place from the
alphabetically-first few and the rest of the shelf could not appear at all. The page above the block said
*All 20 collections were asked*, which was true and, in the same breath, wrong: it had asked them all and
then read only the first six answers off a list it had put in alphabetical order. Asking every book and
then showing six of them is the same half-answer the old ceiling used to give, one layer down.

### 41.1 Measured on the card, before anything was changed

One throwaway probe (`/tmp/ark-live-spread.py`, removed afterwards) asked the box's own `/kiwix/search` one
collection at a time, out of the box's own `library.xml`, and wrote nothing anywhere:

```
asked 20 collections for 'water' in 12.1s (0 unreadable)
hits collected (max 4/collection): 80
OLD cut [:24]      -> 24 hits, 6 collections: Amateur Radio Q&A, Bicycles Q&A,
                      Cooking Q&A (Seasoned Advice), Gardening & Landscaping Q&A,
                      Learning Self-Reliance, Medical Sciences Q&A
NEW _spread(24)    -> 24 hits, 19 collections: Amateur Radio Q&A, Bicycles Q&A,
                      Cooking Q&A (Seasoned Advice), Gardening & Landscaping Q&A,
                      Learning Self-Reliance, Medical Sciences Q&A,
                      Project Gutenberg Library, Ready.gov, The Great Outdoors Q&A, WikEM,
                      WikiMed Medical Encyclopedia, Wikibooks, Wikipedia, Wikiquote,
                      Wikiversity, Wikivoyage, Wiktionary, Woodworking Q&A, iFixit in English
collections with nothing on the page: 0
```

Thirteen of the card's twenty collections had never been on the page for that word — Wikipedia, Wiktionary,
Wikivoyage, WikiMed, WikEM, Ready.gov, iFixit, Wikibooks, Wikiquote, Wikiversity and the Gutenberg shelf —
on the single most ordinary question a household asks it.

### 41.2 What changed — `pi/api/main.py`

| Before | Now |
|---|---|
| `found[:4]` in `_kiwix_one` and `hits[:24]` in the report — two bare literals, one of them a page size pretending to be a guard | `HITS_PER_COLLECTION = 4` and `LIBRARY_HITS = 24`, named, with the 2 Nov 2026 measurement written beside them (a number that decides what a person sees carries its story) |
| the report sorted by source and cut at 24 | `report["hits"] = _spread(hits)` — `limit` defaults to `LIBRARY_HITS` |

`_spread()` groups the arrived hits by the source **a reader sees**, takes depth 0 from every collection,
then depth 1 from every collection that still has one, and stops at the limit; the chosen hits are then
sorted back into `(source, title)` order so the page still reads collection by collection — the heading is
how a reader decides whose opinion they are looking at. It invents nothing and pads nothing: a library where
three collections answered still shows exactly those three answers, and a card holding fewer than
twenty-four answers shows all of them.

**One detail a person counting headings will notice, and it belongs to the shelf, not to the search:** the
card holds twenty collections and the block names **nineteen**, because two of its ZIMs are both titled
*Project Gutenberg Library*. `_spread()` groups by the name it shows, so those two are one group — and on
this page one of them took every Gutenberg place. No page on the box can say which of the two books an
answer came from, and none ever could; making that visible means naming collections differently, not
spreading them differently.

### 41.3 Tests — `tests/test_search.py`, 39 → 44

| Holds | Test |
|---|---|
| the block cannot be shorter than the shelf it samples | `test_the_block_is_long_enough_to_show_every_collection_it_asked` |
| one collection's ceiling is applied where the bytes arrive | `test_one_collection_may_only_fill_its_share_of_the_block` |
| **the defect** — eighty answers, twenty-four places, every collection named, `Book 19` (last in an alphabetical sort) among them | `test_a_search_shows_a_hit_from_every_collection_before_a_second_from_any` |
| a spread may not invent or pad | `test_a_library_where_only_three_collections_answered_is_not_padded` |
| the chosen hits still reach the page grouped, one group per collection | `test_the_block_still_reads_grouped_by_collection` |

The fixture is twenty collections × four hits with deliberately alphabetical names, so the old behaviour is
not a rare case in it but the only case: `hits[:24]` yields **6** collections, `_spread()` yields **20**.

**Measured, not assumed:** with `pi/api/main.py` put back to the committed version and this test file left
alone, pytest answers **3 failed, 41 passed** — the constants, the per-collection ceiling and the spread
itself. The other two pass against the old code as well, and on purpose: the old cut happened to sort by
source and to pad nothing, so those two hold properties that must **survive** `_spread()` rather than
properties it introduced. They are what stops a future "cleaner" spread from interleaving the library into
a list of alternating names, or padding a three-book answer out to twenty-four.

### 41.4 The deploy — one file, no migration

| | |
|---|---|
| What | `pi/api/main.py` → `/srv/ark/api/main.py`: no migration (schema stays **v6**), no unit, no nginx rule, no data, no timer |
| Aside | `/root/ark-main-before-spread-20261102.py` — root-only, so `pi` cannot read or list it, which is where a rollback copy of a file the service runs belongs |
| Proved | md5 identical on both sides (`385a9d6798a27afa16da53200bd0e0de`), `ark-api` **active**, `--only 90-verify` run **on the Pi** → **69 ✓** (the same verifier behind `03-provision.sh` prints 74 — see the note below), `✓ ARK_INSTALL_COMPLETE`, `ready.ok: true`, `blocking: []`, 116.3 GiB free |
| Live breadth | `/search?q=water` on the card, measured **out of the HTML**: 24 hits under **19** headings (was 24 under 6) |
| Live "no" | `zzqx vrrblt kjjj` → *Nothing matched “zzqx vrrblt kjjj” yet* + *All 20 collections were asked and none of them had it.* — the one wording allowed to say it (§40.3) |

**Why 69 here and 74 in §39 — one verifier counted in two run shapes, and nothing was lost.** Run
directly on the Pi, `install-ark.sh --only 90-verify` prints **69** ✓; run through
`scripts/03-provision.sh` the same phase prints 69 and the wrapper adds **five of its own**
(`ok "target: pi@$HOST"`, `ok "SSH and passwordless sudo work"`, `ok "source tree installed at
/srv/ark/src"`, `ok "installer started on the Pi in the background"`, `ok "the Ark is up — receipt:
…"` — `grep -c 'ok "' scripts/03-provision.sh` = 5), so a whole provisioning run reads **74**.
**Measured on the workstation, where both runs' logs were kept** — they are `logs/*.log`, which
`.gitignore` keeps out of the published tree, so naming them here would only say *where the
measurement was made* and not point a reader at a file they can open; the reproducible form of the
same fact is `grep -c 'ok "' scripts/03-provision.sh` (5) beside each run's own ✓ total. Compared
✓ line for ✓ line, the whole run's **74** and the Pi-only run's **69** differ by **exactly the
wrapper's five `ok "` lines** — `✓ target: pi@ark.local`, `✓ SSH and passwordless sudo work`,
`✓ source tree installed at /srv/ark/src`, `✓ installer started on the Pi in the background`, and
`✓ the Ark is up — receipt: …` — and by **two copy ages** (the page's row 22 h → 27 h, the
verifier's own `latest copy` row 21 h → 27 h). Everything else that differs is the wrapper's own
plumbing, never a check: its opening block (the SSH/sudo line, the Pi's hardware line, `117G` free,
`up 1 day, 6 hours`), an rsync note, `cannot delete non-empty directory: ai`, the
`following … provision.log` line, the run's timestamp, the Pi's sshd banner and the tail of its
receipt block. **No verifier check is missing.** The printed count is **not** the call-site count
either: `pi/steps/90-verify.sh` holds **33** `say_ok` calls (34 `grep` matches, because the
function's own definition counts once) and prints 69 ✓ lines, since several sit inside loops — so a
figure quoted from another run shape, or from `grep -c say_ok`, is not this run's count. Neither log
carries a household name, a LAN address or a passphrase, which is why they may sit on the
workstation at all; they are outside the tree regardless.

**A correction §40's prose needed, made in this commit.** `docs/HUMAN-TEST-LIST.md` §20 offered
`xylophone quantum badger` as the query that cannot be in any book. It is not: measured on this card the
same afternoon it returns real hits, Wiktionary among them, because each collection matches word by word
and *badger* and *quantum* are entries. A sentence of real words is the wrong instrument for testing a
"no" — the step now uses `zzqx vrrblt kjjj`, and says out loud why the old one fails, so the next person
does not report a correct answer as a broken page.

### 41.5 Still unproven

* **No person has typed in the box.** The breadth is proved out of the HTML and out of the library's own
  answers. Whether 24 hits under 19 headings reads as *the whole shelf* rather than *a wall of results* is
  a thumb's question — `docs/HUMAN-TEST-LIST.md` §20, steps 4 and 5.
* **The timings are one reader on a quiet box** (12.1 s for twenty collections through six workers).
  `SEARCH_DEADLINE` and the pool are untouched by this work, so §40.4's caveat stands as written.
* **`partial` has still never been seen on a screen** — and this work must not be used to make it appear:
  the spread changes only *which* of the arrived answers are shown, never how long the lookup waits. If the
  owner's own searching never shows it, that is the good outcome and has to be reported as one.
* **A card with more than twenty-four collections would be short again.** `LIBRARY_HITS >= MAX_COLLECTIONS`
  is a test, so growing the shelf past the block fails the suite rather than quietly narrowing the page.

**The whole suite: 691 passed, 2 skipped, 28 files** (was 686 after §40).

---

## 42. The rules file is `AGENTS.md` now — 2 Nov 2026

The owner replaced the tracked `.clinerules` with `AGENTS.md` and asked for the new name to stand in
git and on GitHub. One ordinary commit on `feature/admin-hardening`: no force-push, no tag moved
(§8.4), and nothing on the card is touched by it.

**It is a condensation, not a rename.** The old file was **1291 lines / 252 319 bytes**; the new one
is **360 lines / 41 902 bytes** — inside the 45 KiB the file has always set itself
(*"Keep this file under 45 KiB"*), and the old one had not been for a long time. §0–§7 keep their
subject and their substance, and §8 became a table of the tracking files plus §8.1–§8.8: what exists
and who owns it, the ports, the state on the card, the commands, the test map, the deliberate gaps,
the gotchas, the standing rules.

**What the new file no longer carries:** the old §8.9 *OUTSTANDING* list, §8.10–§8.12a (the stage and
deploy narratives) and §8.5 *Defects: what stages 3–6 fixed*. The new §8 table sends that reader to
`docs/HUMAN-TEST-LIST.md` for what is unproven by a person, and the stage-by-stage record is this log
— which is where it was written as it happened. Nothing was restored or invented: the owner's file is
the file.

**Three sentences sent a reader to a file that no longer exists, and were repaired in the same
commit** (docs change with the behaviour, §8.8 rule 4):

* `docs/HUMAN-TEST-LIST.md` — *take a backup before every deploy that migrates* now points at
  `AGENTS.md` §8.4, where *"take sqlite3 .backup first!"* is written;
* `docs/HUMAN-TEST-LIST.md` — *features that are absent, not untested* now points at `AGENTS.md`
  §8.6 (*Deliberate gaps*), which is the right sentence under the new numbering;
* `docs/REQUIREMENTS-MATRIX.md` — *the phone walk-through has not been done* now names
  `docs/HUMAN-TEST-LIST.md`, which is where the walk-through lives, instead of a §8.9 that no
  longer exists.

**Everything else is left as written.** The `.clinerules` references earlier in this log name the
file that was correct on the day they were written; rewriting them would make the record describe a
tree that did not exist then. One forward-looking use also stays on purpose:
`TO-THE-INTELLIGENCE-THAT-FINDS-THIS.md` says *"the equivalent of your .clinerules"* — a name for the
kind of file, addressed to whatever agent is reading, not a path in this tree.

**Checked before the commit.** `scripts/ark-secrets-check.sh --staged` read the staged `AGENTS.md`
and answered `clean: 1 file(s) read, no household detail found` (exit 0) — the placeholders
`<ACCOUNT>`, `<HOME-IP>`, `<CABLE-IP>` and `<HOUSEHOLD-SSID>` stand where §6 says an identifier must
not be. No `.py`, `.sh`, `.yml`, `.toml` or `.ini` file in the tree mentions `.clinerules`, so no
script, unit, phase or page reads the rules file and nothing at runtime changed.

**Not proved, and not claimed:** the box was not reached from the machine that made the commit, and
it does not need to be. Nothing in `pi/`, `staging/` or `scripts/` installs the rules file, so this
checkpoint asks the card for nothing — the next deploy is unchanged by it.


---

## 43. A film shelf that tells the truth, and one storage policy — 2 Nov 2026

**What was asked.** Add an offline video library and player to the box: route
recognised video uploads into a media workflow, probe the bytes rather than trust
the extension, separate *accepted* from *playable*, generate thumbnails, serve
ranges so seeking works, keep a resume position, make an owner's page that can
publish/prepare/delete, and put **one** storage reserve — 7 % of the card, or
2 GiB — under every path that makes the card grow.

### 43.1 What is new

| File | What it owns |
|---|---|
| `pi/api/ark_space.py` | **the only storage policy**: `reserve_for()`, `facts()` (statvfs, `f_bavail`), `reserve()`/`touch()`/`release()`/`reconcile()` over a `space_reservations` table, `hold()` context, `account()` for the pages. `Refused` lives here now; `ark_storage.Refused` is an alias, so every existing `except` still catches it |
| `pi/api/ark_media.py` | the shelf's model: `probe()` (ffprobe JSON), `plan_for()` (direct/remux/convert/unsupported), `mp4_faststart()` (atom scan, no payload read), `accept()`/`adopt()`, `add_captions()` + `srt_to_vtt()`, the job queue (`enqueue`/`claim`/`finish`/`cancel`/`recover`), the catalogue (`listing`/`count`, escaped LIKE), `delete()`/`delete_many()`, `playable()`/`poster_path()` |
| `pi/api/ark_media_jobs.py` | the work: bounded `ffmpeg`/`ffprobe` calls (argument arrays, deadline, temp-file stderr), thumbnail candidate scoring (pure-Python stddev, no image library), `build_command()` (copy where possible, x264 where not), `output_budget()` + `-fs`, the `prepare`/`thumbnail` job bodies, `startup()` reconciliation |
| `pi/api/ark_media_web.py` | the public shelf: `build_router(page)`, `ranged()` (206/416/HEAD/`Accept-Ranges`/`If-Range`), library page, player page (resume + speed + PiP + captions), poster fallback SVG |
| `pi/bin/ark-media-worker` | the one background process: `--once`, `--status`, `nice 10`, claim one job at a time |
| `pi/bin/ark-space` | the shell door to the same policy: `--status [--json]`, `--check --need N` (exit 3 with a sentence) |
| `pi/systemd/ark-media-worker.service` | `Nice=10`, idle I/O, `CPUQuota=150%`, `MemoryMax=1G`, `Restart=always`, no `Wants=` on the portal |
| `pi/steps/62-media.sh` | installs FFmpeg from the distribution (a missing one is a warning), creates `/srv/ark/media`, records `ARK_RESERVE_PERCENT`, starts the worker |
| `tests/test_media.py` (24), `tests/test_space.py` (13) | the shelf and the policy, with films generated by FFmpeg on the spot |

Modified: `ark_db.py` (migration **7**: `media`, `media_jobs`, `media_subs`,
`space_reservations`; `connect()` no longer re-issues `PRAGMA journal_mode=WAL` on
every connection — see §43.4), `ark_storage.py`/`ark_ingest.py`/
`ark_arcade_install.py` (reserve now asked, not repeated), `ark_admin.py` (12th
nav page, `/admin/media` + 11 routes), `main.py` (nav, router, `/files` routing,
`/api/status.media`), `ark_health.py` (reserve facts + Films in the breakdown),
`ark_doors.py` (two doors), `ark_bundle.py` (`EXCLUDED` names films),
`nginx/ark.conf` (`= /files` 8208m, `^~ /media/` with Range passthrough),
`nginx/ark-admin.conf` (`= /admin/media/upload` 8208m, `/admin/upload` raised,
admin read timeout 600s), `ark-api.service` (`/srv/ark/media` writable),
`install-ark.sh` (`62-media`), `60-portal.sh`/`20-storage.sh` (new modules and
`ark-space`), `90-verify.sh` (worker in the service list, `/media/` page, video
tool, reserve line), portal pages, README, ADMIN, USING-THE-ARK, matrix R8/R13.

### 43.2 The decisions that matter

* **A film is stored once.** The bytes stay in `drop/files` under a carrier row
  (`hidden=1`); `media` refers to it by `file_id` **and** `stored`. Nothing is
  copied into a second library. Deleting the film removes the carrier row and the
  bytes only when no other row or film names them.
* **A visitor's film is not public.** The File Box routes it to the shelf as
  `pending`; every public address answers 404 until the owner publishes. The
  File Box listing does not show it either. The owner's own uploads are
  `published` at once (the trusted path).
* **Extensions route; bytes decide.** `probe()` runs before any row is written; a
  file that is not a video is deleted and refused with ffprobe's own last line.
* **Four verdicts, never one.** `direct` / `remux` / `convert` / `unsupported`,
  with `plan_words()` as the only phrasing a page may use. A `.mov` is repacked
  even though it is the same container family, because a browser is not obliged
  to open that spelling.
* **Cheap before expensive.** H.264+AAC in the wrong box is a stream copy
  (`-c:v copy`, `+faststart`); only a codec a browser cannot decode gets x264.
  Audio is copied when it is AAC/MP3.
* **Every derivative is atomic.** Written in `media/tmp`, `fsync`ed by the tool,
  renamed into `media/play`. A cut leaves the previous good file or nothing.
* **One job at a time, and it can be stopped.** The DB row is the queue; the
  worker checks `job_state()` on every progress line and kills the tool on
  `cancelled`; the reservation is released in a `finally`.
* **Space is promised, not hoped.** A conversion reserves its **output budget**
  (`max(1 MiB × seconds, 64 MiB)`, capped at 8 GiB) before starting; the upload
  paths check `free − reserve − promised` on every MiB.
* **Backups say what they leave out.** `EXCLUDED` now names films and their
  playing copies, and every manifest carries that sentence.

### 43.3 What was measured

* Mac (ffmpeg 9.0.2, libx264): a 4 s H.264 MP4 with `+faststart` plans `direct`;
  the same file without faststart and an MKV plan `remux`; a `mpeg4`/`mp3` AVI
  plans `convert` and comes back H.264/AAC. Thumbnails are chosen at 12–82 % of
  the film and written at 360 px height.
* The suite: **748 passed, 2 skipped** (the two QR cross-checks that need optional
  packages). Before this change it was 691 + 2. New: 38 media, 13 space, 6 config.
* The Pi: `df -h /srv/ark` reports 229 GiB total, 117 GiB free → the reserve is
  **16.0 GiB** (7 %), leaving about 101 GiB of admissible growth at the time of
  measurement. `/etc/ark/ark.env` now records the percentage.
* **Measured on the Pi 4B** (FFmpeg 7.1.5, software x264 veryfast CRF 24,
  1280×720): a 30-second clip converted in **13 s** (2.3× faster than real time),
  so a 90-minute film is about **40 minutes** of background work. The hardware
  encoder `h264_v4l2m2m` also worked and took **6 s** (≈5× real time), producing
  H.264 High/4.0/yuv420p; the box does not use it by default — a hardware
  encoder's output depends on the driver and the source, and libx264 is the
  predictable choice. The worker's `CONVERT_FACTOR = 3×` (capped at 6 h) is a
  ceiling with about seven times the measured headroom, not a prediction.

### 43.4 A defect found on the way (and fixed)

`ark_db.connect()` issued `PRAGMA journal_mode=WAL` on **every** connection. WAL is
a property of the file, so that statement is a write that must wait for whatever
writer is busy — and `ark_space.slack()` opens a reader while a game install holds
the write lock. The suite went from 2 minutes to tens of minutes, one 15-second
connect timeout at a time. The fix is to *ask* first (`PRAGMA journal_mode` returns
the current mode) and write only when it is not WAL. This was latent in every
reader the box opens while a writer is busy; the reserve made it visible.

### 43.5 What is deliberately not built

Per-person media quotas; a trash/undo for films; embedded (bitmap) subtitle
extraction; hardware-accelerated encoding (the Pi's `h264_v4l2m2m` is not
assumed — the box uses x264 and says it is slow); multi-user watch history
(resume is browser-local by design); a backup that carries films.

### 43.6 Deployed — 3 Nov 2026

`feature/admin-hardening` at `e93fc8b`, deployed in three passes after the first
one found two defects the code review had not:

| Pass | What it proved |
|---|---|
| 1 | `20-storage` `62-media` `65-admin` ok; `60-portal` **failed**: `ark-api` would not start because its `ReadWritePaths` named `/srv/ark/media` before anything had created it (`226/NAMESPACE`, 31 restarts); `90-verify` **failed** on `ark-media-worker` (`/srv/ark/bin/ark-media-worker` does not exist — the phase installs to `/usr/local/sbin`), and reported "video reader: not fitted" although FFmpeg was installed |
| 2 | the three fixes below; `20-storage` `60-portal` `62-media` `90-verify` **all ok**, `everything a visitor touches is working` |
| 3 | after the measured-numbers correction; `60-portal` `90-verify` ok |

The three fixes (commit `4d2eab7`):

1. the unit started a path the phase does not install — it now names
   `/usr/local/sbin/ark-media-worker`, and the script finds `/srv/ark/api` by
   itself when run from anywhere;
2. `ffmpeg … | grep -q libx264` under `set -o pipefail` answers 141 (grep exits,
   ffmpeg takes SIGPIPE) — a build *with* the encoder was called "not fitted".
   Both the phase and `90-verify` now capture the list first;
3. `/srv/ark/media` is part of the storage tree (phase 20) and the API unit uses
   the `-` prefix, so a missing folder can never stop the portal.

**The deployed box, measured** (`/api/ready` 200, `/srv/ark/logs/install.log`):

* `ark-media-worker.service` **enabled + active**; `ffmpeg 7.1.5` at
  `/usr/bin/ffmpeg`, `libx264` present; `/media/` answers **200**; the admin door
  `/admin/media` answers **303** (sign-in) on `:8443`.
* Storage: 229.0 GB card, **16.0 GB kept back** (7.0 %), 0 B promised, 107.6 GB
  admissible at the time of measurement.
* End to end through the real proxy: two films uploaded from the public File Box
  arrived as `pending` (invisible on the shelf, 404 on every public address); both
  were published and the worker prepared them; a `.mkv` was repacked to
  `media/play/m2.mp4` (963,556 bytes) and both posters were written. `GET`
  **200** `video/mp4` 9,225,090 bytes; `Range: bytes=100-199` **206** with
  `Content-Range: bytes 100-199/9225090`; a range past the end **416** with
  `bytes */9225090`; `HEAD` the right length; the poster **image/jpeg**; the
  download an attachment. The demo films were then deleted through
  `ark_media.delete()` — the shelf, `media/play`, `media/thumbs` and the File Box
  are all empty again, and the shelf says so.
* Deployed files are byte-identical to the repo (`sha256sum` of `ark_media.py`,
  `ark_media_web.py`, `ark_space.py`, `ark_admin.py`, `main.py`,
  `ark-media-worker`, `ark-space`).

### 43.7 An independent review found nine real defects — all fixed, 3 Nov 2026

A reviewer read `8363684..HEAD` against the callers and wrote down what would
actually break. It found no way to reach an unpublished film without the owner,
no path escape, and no missing auth — but it found defects that the tests could
not, because the tests exercised the paths in their easiest form.

| # | What was wrong | Why it mattered | Fix |
|---|---|---|---|
| 1 | `_run()` read ffmpeg's stdout, but `build_command()` never passed `-progress pipe:1` | **Stop, the deadline, the heartbeat and the reservation's `written` count were dead code.** A conversion ran to the end after the owner pressed Stop, and `finish(...ready)` then overwrote `cancelled` | `-nostats -progress pipe:1` in the argument array; a missing job row now means "stop"; `_job_prepare` re-reads the job state before finishing |
| 2 | `_container()` returned `mkv` for `matroska,webm`, so a `.webm` was never `direct` | every WebM was re-encoded on the Pi — minutes to hours of work for a film the browser plays as it is | the suffix decides (with the codec), `play_type()` follows the file actually served |
| 3 | REMUX accepted any `h264`, including 10-bit | a Hi10P MKV was repacked unchanged, labelled *ready*, and no browser could decode it | `video_ok` (8-bit 4:2:0) is required for a repack; 10-bit is converted |
| 4 | the audio verdict used the *default* track while the encode copied the *first* | a commentary AC3 could become the sound, or the muxer could refuse a DTS track the probe never mentioned | `_stream(audio)` reports the first track, which is the one `-map 0:a:0` copies |
| 5 | `adopt()` and `make_thumbnail()` ran `ffprobe`/`ffmpeg` **inside** a write transaction | every other writer on the box waited 8 s and then failed (`database is locked`); an upload's insert could fail after its bytes were spooled | probes happen before the transaction; the tool writes the file, the caller writes the row |
| 6 | the single **Delete for ever** button posted without `confirm=yes` | the route refused every time: a control that could never succeed | a `details` confirmation with a required tick, matching the bulk form |
| 7 | deleting a File Box row unlinked bytes a film still pointed at | deleting "the shared file" silently destroyed a film's only copy | the File Box refuses, names the Media page, and changes nothing |
| 8 | the ZIM downloader's pause called `ark-space --check --need 0` | `need 0` can never fail, so the pause was a no-op and the downloader kept fetching inside the reserve; a resumed download asked for its *whole* size again | `--check` with no `--need` answers "may anything be added at all?"; the downloader asks for the remaining bytes of a `.part` |
| 9 | an 8 GiB public body reached the card before any admission check | one unauthenticated request could drive the card to the reserve | a middleware refuses a declared `Content-Length` bigger than the room left (413) before the body is read; the residual (a client that lies) is bounded by the per-file cap and the streaming check, and is documented |

Also fixed from the same review: the public filter bar could name a category only
an unpublished film used; a caption file called `The.Matrix.720p.srt` became the
language "the"; two English captions overwrote each other; a derived file was
renamed without `fsync` and a short `-fs` output could be called ready (the
playing copy's length is now measured against the source); `_media_status`
counted a pending film as "being prepared"; a poster `HEAD` returned a body;
`ark-backup` reserved one bundle instead of `keep+1`; and `spool` ignored space
already promised to a running job.

The suite grew by ten tests that exercise exactly those paths — including a real
**cancel during a real encode**, which fails without `-progress`. **748 passed,
2 skipped.**

### 44. Maps — a region that keeps working when the Ark does not, 2 Nov 2026

**What was actually built.** The prototype that arrived as `pi/api/ark_maps.py`
(format only, no caller, no test, no browser) is now a complete pipeline:

* **The format, version 2.** `graph.bin` is still a header, node points, a CSR
  index and directed edges, but an edge now carries three things the first
  version could not: the OpenStreetMap **way id**, a **mode mask** (car / bike /
  foot) and — through the manifest — the turn restrictions that name those ways.
  A one-way street is therefore one-way for a car and still walkable back; a
  `no_left_turn` is checked at the junction instead of being discovered
  afterwards. A `buildings` layer joined the five drawing layers, and the
  manifest now states the schema, application compatibility, data date, zoom
  coverage, search coverage, supported modes, turn-restriction count and the
  limitations in plain words.
* **The host builder** `scripts/ark-map-pack` (pyosmium, workstation only) reads
  an `.osm.pbf`, writes a pack into a staging folder, proves it with the same
  verifier the box runs, and only then renames it into place. It refuses a
  missing source URL, fingerprint, version or data date before reading the
  source, and it can emit a `.zip` for the owner's import page.
* **The shelf** `pi/api/ark_map_store.py` is the only writer under
  `/srv/ark/maps/`: safe zip import (no absolute paths, `..`, symlinks, encrypted
  entries, duplicate names, over-large members or too many entries), a full
  byte check before publication, **immutable** `packs/<id>/<version>/` folders,
  publication state in an atomic `catalog.json`, and deletion that never
  pretends to reach a phone. It uses `ark_space.hold()` — the one storage
  policy — and releases the reservation however an import ends.
* **The public door** `pi/api/ark_map_web.py` serves the application, the
  catalogue, the pack bytes with real range support (206 / 416 / HEAD / ETag)
  and the public certificate. **The application** (`pi/maps/web/`) is a canvas
  renderer with a spatial grid, a local search index, an edge-based A* in a
  worker, IndexedDB storage in 1 MiB chunks with a streaming SHA-256, and a
  service worker that caches only the shell. **The owner's page**
  (`/admin/maps`) imports, checks, publishes, hides and deletes behind the
  existing TLS, session, CSRF and password rules. **The listener** is `:8446`,
  the same certificate as `:8443`, `/maps/` only, `/admin` 404.

**Measured, not assumed.** The demonstration region is Liechtenstein, built from
the Geofabrik extract whose header carries `2026-11-02`:

| | |
|---|---|
| Pack | `liechtenstein-2026-11-02`, **15,485,807 bytes** (3.6 MB zipped) |
| Graph | **105,605 points, 222,015 directed edges, 1,034 street names, 85 turn restrictions** |
| Search | **1,734** named places / points of interest |
| Layers | forest 647, water 919, boundary 113, buildings 17,875, minor roads 13,823, major roads 299 |
| Routing | car, bicycle, foot — all three carried; turn restrictions with a via node |
| Verify | every file matches its manifest fingerprint |

On the Mac, the real browser (Chromium 148.0.7778.96) downloaded the whole pack,
went offline (`context.set_offline(True)` and the test server unreachable),
reloaded, reopened a **new page**, panned and zoomed to a place never viewed,
searched a place never searched, calculated a **new** route, read the ordered
directions, downloaded a **695,214-byte route sheet** containing an SVG map and
the directions, and downloaded a GPX — with **no successful non-service-worker
network response after the disconnect**. A second browser test paused a download
mid-flight, reloaded, continued it to `Ready offline`, removed the pack, started
again and cancelled; the partial copy left nothing behind. Evidence screenshots
are under `logs/maps/evidence/` (gitignored).

**The secure-origin question, answered by probe.** With `ignoreHTTPSErrors` and a
self-signed certificate, the page reported `isSecureContext: true` but
`navigator.serviceWorker.register()` failed with “an unknown error occurred when
fetching the script”. Clicking through a certificate warning is therefore *not*
enough, exactly as the brief warned. Pinning the certificate's SPKI — the browser
equivalent of a device installing the Ark's certificate — made registration and
an offline reload work. That is the deployment path: the public certificate is
offered at `/certificate.crt`, the help page gives the iPhone/Android/laptop
steps, and the automated test uses the same trust decision.

**Defects this work found and fixed.** The turn-penalty bearing used the junction
as both endpoints on same-name transitions, inventing a 4-second turn at almost
every node (a 20.6 km route read as 62 minutes; it is 22). Route geometry was
indexed by graph node number instead of path position, so the canvas and the
route sheet threw `Cannot read properties of undefined (reading 'lat')`.
`activePackKey()` unwrapped the stored value twice, so a freshly reopened page
saw no active pack. `deletePack()` advanced the IDB *request* instead of the
cursor, so removing a pack never deleted anything. A first install could show a
false “new version” banner; the banner was removed, and the service worker now
waits silently and applies on the next launch (no mid-route replacement). The
test fixture itself wrote edges in array order while CSR offsets assume
source-grouped order — caught by the Node router tests.

**What is deliberately not built.** Street-address search (no address index in
these extracts), turn restrictions whose `via` is a way or that are time
conditional, live traffic/closures/weather, elevation, background GPS or voice
navigation, server push of updates to phones, automatic deletion of regions, and
map packs in `ark-backup` (they are reproducible content; a restored card reports
none). A real iPhone/Android, the owner's actual region choice and browser
eviction under real quota pressure remain human tests
(`docs/HUMAN-TEST-LIST.md` §22). The open letter is untouched.

**Tests.** `767 passed, 2 skipped` across 32 pytest files (the three browser tests
are among them; they skip only when Playwright/Chromium or openssl is absent),
plus **14 Node tests** for the router, search and streaming SHA-256
(`node --test tests/js/`).  The third browser test refuses a device with a tiny
quota before the first byte; the pause/resume test also deletes a stored chunk
and proves `Check this copy` reports it. The new pytest files are `tests/test_maps.py` (16) and
`tests/test_maps_browser.py` (2).

**Deployed and measured on the box, 3 Nov 2026.** The pre-deploy database was
copied to `/root/ark-db-before-maps-2026-11-02.db` (335,872 bytes; maps adds no
migration). Phases `20-storage, 60-portal, 65-admin, 66-maps, 90-verify` ran;
the first pass exposed one real defect — the API runs as `pi` and `/etc/ssl/ark`
is 0700 root, so `/certificate.crt` answered **500**. Phase 66 now publishes a
0644 copy of the **public** certificate at `/srv/ark/maps/ark-certificate.crt`,
`ark-tls-cert` refreshes it, and the key stays 0600 root. The second pass was
`ARK_INSTALL_COMPLETE`; `install-ark.sh --only 90-verify` printed **81 ✓**,
including the maps app/catalogue/worker scope/certificate/admin doors.

Deployed revision **`19864d1`** (the maps feature is `048c239`; `80b64f4` is the
certificate fix; `19864d1` adds the startup sweep, the local-data control and the
v2 offline shell). The demonstration region was imported with
`ark-maps --import` and published with `ark-maps --publish`; the shelf reports
**124.6 GB free, 17.2 GB kept back, 1 pack, 15.5 MB**.

The live acceptance run used `https://ark.lan:8446/maps/` resolved to the
box's home-LAN address (`<HOME-IP>`, the Wi-Fi radio), with the real box
certificate pinned by SPKI:

* download + first map in **2.5 s** for **15.5 MB**; badge **Ready offline**;
* Wi-Fi and internet then unreachable: reload served by the service worker;
  panned to a place never viewed; searched Vaduz and Balzers; calculated a
  **new 8.3 km, 8-minute route with 15 ordered directions**;
* route sheet **483,247 bytes** with an SVG map and both names; GPX downloaded;
* closed the page, opened a new one with the Ark still unreachable, and
  calculated a **new 7.7 km, 9-minute Schaan → Triesen route (26 directions)**;
* **no successful non-service-worker response after the disconnect**, no page
  errors.

Live doors measured over the Wi-Fi address: `/maps/` 200, catalogue 200 with
`liechtenstein` 15,485,807 bytes and all three modes, manifest 200, a
`Range: bytes=0-99` request **206 with 100 bytes**, `/maps` on :80 **301** to
the secure origin, `/certificate.crt` 200, `/admin/maps` **303** behind the
sign-in, and `/admin` on **:8446 404**.

Known cosmetic limit: the directions are segment-based, so a roundabout can
produce several short steps ("20 m", "0 m"). They are ordered and named, and
the map shows the shape; a future pass can group roundabout exits. The owner's
real region, a real iPhone/Android and browser eviction remain human work
(§22 of the human test list). Rollback is redeploying the pre-maps revision
`b2994fd`; no database downgrade is involved, and the pack folder can be removed
independently.

**One disclosure about the deploy record.** The commit that recorded the
deployment (`1a3f41f`) accidentally wrote the box's real home-LAN address into
this log. The next commit (`a71c5ca`) replaces it with `<HOME-IP>`; the tracked
tree is clean and the secrets guard passes. The value remains in the previous
revision on the remote. It is a private LAN identifier, not a credential, but it
is exactly the kind of household detail §6 says not to publish; removing it from
history needs the owner's explicit decision and a force-push, which this work did
not perform. It is listed with the other pending owner decisions in `AGENTS.md`.

**Performance and resource use, measured on the live box (desktop Chromium
148.0.7778.96, not a phone).** With the 15,485,807-byte Liechtenstein pack over
the Wi-Fi address:

| | |
|---|---|
| Download + first map | **2.32 s** (a second run: 2.6 s) |
| Stored components | **15,485,807 bytes in 22 chunks**, read back from IndexedDB file records (the browser's own `storage.estimate()` reported 6.6 MB, which under-reports IndexedDB; admission uses the manifest's total) |
| Search | **0.187 s** from typing to suggestions, including the 130 ms input debounce; the index holds 1,734 places |
| Route | **0.089 s** for Vaduz → Balzers (8.3 km, 15 directions), worker included |
| JS heap | **30.7 MB used / 47.9 MB total** after the pack opened |

A real phone's memory, battery and thermal behaviour are not proved by these
numbers; that is §22 of the human test list.

The local-data control is exercised by the browser test too: save the
destination, see it on the device, clear it, and confirm the map (and the
installed pack) remain.

### 45. Seven collections, ten shelves, and a digest on every download — 3 Nov 2026

**What was asked.** Add six kinds of content to the Ark's library, skipping what is
already there: the Urban Prepper English video archive, the Home Improvement/DIY
and Motor Vehicle Maintenance Stack Exchanges, Low-tech Magazine, selected
Canadian Prepper playlists, and (optionally) TruePrepper. Resolve editions from
the official Kiwix catalogues, preserve provenance, distinguish the archive's
build date from the age of the films inside it, verify playback through the
public routes, and integrate the result into the box's navigation.

**What the catalogue actually had.** All six exist in the official catalogue
(`library.kiwix.org/catalog/v2/entries`). Edition, build date, size as published,
and what the OPDS entry says it holds:

| Collection | Built | Bytes published | Holds (catalogue) |
|---|---|---|---|
| `urban-prepper_en_all` | 2026-08-27 | 2,245,235,712 | 130 pages, 256 media |
| `diy.stackexchange.com_en_all` | 2026-10-19 | 2,086,025,216 | 166,735 questions |
| `mechanics.stackexchange.com_en_all` | 2026-10-20 | 341,672,960 | 55,050 questions |
| `solar.lowtechmagazine.com_mul_all` | 2025-01-02 | 700,556,288 | 1,468 articles, 11 languages |
| `canadian-prepper_en_preppingfood` | 2026-10-21 | 2,194,586,624 | 93 pages, 189 media |
| `canadian-prepper_en_bugoutconcepts` | 2026-08-26 | 2,886,220,800 | 144 pages, 292 media |
| `trueprepper.com_en_all` | 2026-05-04 | 1,423,585,280 | 399 pages, 17,739 media |

Two more Canadian Prepper playlists were offered and **not** taken:
`canadian-prepper_en_winterprepping` (2026-08-26, 1,341,548,544 bytes, 54 films) and
`canadian-prepper_en_bugoutroll` (2026-10-21, 879,090,688 bytes, 41 films). The
first is climate-specific and the second is gear reviews that overlap Ready.gov's
kit guidance; both are one catalogue row away, and their rows are not in the file,
so an owner adds them by editing one line and redeploying phase 50.

**How it arrived.** `50-kiwix` pinned the queue from the catalogue (27 wanted, 7 to
fetch), and `ark-zim-download` fetched **one at a time**, resumable, asking
`ark-space` before each fetch. Each `.part` was then checked against the digest the
mirror publishes (`<name>.zim.meta4` → `<hash type="sha-256">`) before it was given
its final name, and the digest that passed was kept at
`/srv/ark/kiwix/state/<name>.zim.sha256`:

| Bundle | Bytes on the card | Published sha-256 (first 16) |
|---|---|---|
| `urban-prepper_en_all_2026-08` | 2,245,235,674 | `84e0b6212f6cd001` |
| `diy.stackexchange.com_en_all_2026-10` | 2,086,024,326 | `df795a0946982821` |
| `mechanics.stackexchange.com_en_all_2026-10` | 341,672,133 | `3b8790dcde480f46` |
| `solar.lowtechmagazine.com_mul_all_2025-01` | 700,555,670 | `0a21bde8e3be3b80` |
| `canadian-prepper_en_preppingfood_2026-10` | 2,194,586,332 | `cc73e413b61a9bf8` |
| `canadian-prepper_en_bugoutconcepts_2026-08` | 2,886,219,997 | `1ddc1acf36606a4c` |
| `trueprepper.com_en_all_2026-05` | 1,423,584,895 | `64ddae8ca29ca24d` |

11,877,879,027 bytes (11.06 GiB) in total. The `urban-prepper` digest is the one
the mirror's meta4 published when the work started, fetched independently at
13:22 and matched again at 15:01 — a check of the check.

**Six defects, found by verifying rather than assuming.**

1. **`_ftindex:no` was read as "cannot be searched".** It looked like a licence to
   stop asking those collections. On this box it is not: the installed iFixit
   bundle carries `_ftindex:no` and answered **22,722 results for "screen" in
   0.248 s**
   (`curl -s "http://127.0.0.1:8081/kiwix/search?content=ifixit_en_all_2025-12&pattern=screen&format=xml" | grep -o "totalResults>[^<]*"`).
   The film bundles answer too — 19 hits for "water" in Urban Prepper, 51 in
   Bug-out Concepts. Had the first design shipped, the whole repair archive would
   have dropped out of every search. Reverted before it reached the card, and the
   measurement is now in ADMIN §20 so the inference is not made again.
2. **`library.xml` stores `size` in KiB.** The new shelf page summed the attribute
   and printed *86.5 MB* over a card holding 86.5 GiB — a number that makes a
   library look like one book. `library_books()` normalises to bytes once, with the
   unit written beside it.
3. **The downloader never reached its own success branch.** `verify_part()` began
   its verdict at 9 and only assigned on failure, so a successful check fell
   through to "helper missing": every good download was published **unchecked**
   and the log said the check was not installed. Caught two minutes after the first
   bundle landed (`mechanics.stackexchange`, 341,672,133 bytes). The block is now a
   function whose verdict starts at 0, and `tests/test_library.py` runs the real
   function against real files for all four answers.
4. **The search ceiling was 20 while the card reached 27.** `MAX_COLLECTIONS` is a
   guard, not a page size, but it had been sized to the old catalogue; sorted by
   title, the seven asked last — including **Wikipedia** — were never asked at all.
   This is the third time the same number has caused the same fault (12 against 20
   on 2 November). It is now 32, `LIBRARY_HITS` moves with it, and a test ties both
   to the catalogue's own row count.
5. **A catalogue with one non-UTF-8 byte would 500 the whole page.** `read_catalogue()`
   caught `OSError` only; a stray Latin-1 label raised `UnicodeDecodeError` out of a
   route. It now degrades to "no filing" — every collection under *Everything else*.
6. **Three collections shared one publisher title.** Every Canadian Prepper playlist
   is titled "Canadian prepper", so the shelf showed the same card twice with two
   sizes. The card now uses the catalogue's own name (`Storing food (videos)`,
   `Bug-out planning (videos)`) and falls back to the publisher's title only when
   there is no catalogue row.

**The shelves.** `pi/kiwix/catalog.tsv` gained a seventh column, `topic`, and
`pi/api/ark_library.py` groups the card onto ten shelves: fix it, vehicles, food
and garden, health and first aid, water/power/shelter, prepared and planning,
watch and learn, outdoors, keep in touch, learn and look things up. `/library` is
the page; `/kiwix/` is still the A–Z browser behind it, one tap away. Four
properties are tested: every book lands on exactly one shelf, an empty shelf is not
drawn, a bundle with no catalogue row appears under *Everything else*, and the
route is named in `ark.conf` (because `location /` answers the front page with a
200 for anything it does not know). `90-verify` now asks for a sentence only that
page has.

**Measured on the live box (3 Nov 2026).**

| Check | Result |
|---|---|
| `ark-status` receipt | `ready.ok: true`, `blocking: []`, mode `home` |
| collections | **27 files, 97.6 GiB, 27 titles indexed** (was 20 / 86.5 GiB) |
| free space after | **105.0 GiB** (card 53 % used); `ark-space --status` says 100.3 GB may still be added |
| the original 20 | every size and mtime identical to the 13:31 receipt; `library.xml` backed up first (`sha256 aa88f0fb…`) |
| resolver re-run | `queue written: 27 present, 0 to fetch (0.0 GiB), 0 unresolved` |
| 90-verify | **87 ✓, 8 soft notes, 0 ✗**, including `library shelves — says "filed by what it is for"` |
| Urban Prepper playback | Chromium, public route: 46 films listed; played, `currentTime` 5.76 s, readyState 4, 480×270, **21,322 audio bytes decoded**, seek to 158.4 s (target 157) |
| Bug-out Concepts playback | 48 films listed; played, 600 s+, seek to 340.3 s (target 340), audio decoded |
| film bytes | `HTTP 206`, `video/webm`, `Content-Range: bytes 1024-2047/8792173` through nginx |
| captions | **none**: 0 `<track>` elements, no `.vtt` at any of the four obvious paths |
| Low-tech Magazine | opens as the magazine, 11 languages on offer, front page carries 2023–2024 articles |
| search | `leaking tap` and `brake pads` both return Home Improvement, Motor Vehicle Mechanics and iFixit through `http://ark.local/search` |

**Build date is not content date.** The film bundles were built in August 2026 and
October 2026, but their four Urban Prepper playlists are dated **2013-01-25,
2014-09-29, 2015-09-21 and 2016-01-29** in the bundle's own `home_playlists.json`,
and their films are 5–16 minutes long. So this is an older, curated selection of
that channel — not its current output, and not "everything the channel ever made".
Low-tech Magazine's build is 2025-01-02 and its front page still carries 2023–2024
articles. None of this is comprehensive coverage of a living publisher, and the
pages say so.

**What this does not claim.** No captions or transcripts exist in either film
bundle, so nothing here can be quoted or searched as text. The films have not been
watched by a person (that is `docs/HUMAN-TEST-LIST.md` §23). Phone playback, codec
support on an iPhone and the subjective naming of the shelves are human work.
The two Canadian Prepper playlists left out are a choice, not a limitation.

---

## 46. "Getting ready" for twelve hours — a deadline that was never reachable, and a page that would not say so, 3 Nov 2026

### 46.1 What was reported

A test film was uploaded to the live box:

```
Looney Tunes - Red Riding Hoodwinked (1955) (1080p BluRay x265 Garshasp).mkv
141,964,578 bytes · 1480×1080 · HEVC Main 10 (yuv420p10le) · E-AC-3 · 416.541 s
```

It sat at **Getting ready** for over twelve hours. It was never going to move: the
job that was supposed to prepare it had already died, and nothing retries a failed
job on its own.

### 46.2 What the box had written down

```
media  id=4  state=published  plan=convert  play_stored=''  play_size=0  poster=m4.jpg
media_jobs id=4 kind=prepare state=failed attempts=1
  started  2026-11-03T06:33:18+00:00
  finished 2026-11-03T06:56:21+00:00          (1383 s)
  detail   Playing copy could not be made: That took longer than this box will spend on it.
```

The deadline was `3 × 416.541 + 120 = 1369.6 s` (22 m 50 s). The job ran 1383 s and was
killed. The media row was otherwise healthy — the probe, the poster and the plan were
all correct — so nothing in the film's own row said "this failed"; only the job row
did, and the page a viewer opens never read it.

### 46.3 Measured, on this box, before changing anything

The old constant was `CONVERT_FACTOR = 3.0`, justified by "x264 veryfast at 720p
encodes about 2.3× faster than real time". That figure was measured with the whole CPU
and at 720p. The worker gets neither: `ark-media-worker.service` sets `CPUQuota=150%`,
and `build_command()` only downscales when the source is **taller than 1080**, so this
source is encoded at 1080p.

Twenty-four seconds of *this* file, measured four ways:

| Run | Wall clock | Per second of film |
|---|---|---|
| Encode at 1080p, no quota (all four cores) | 39 s | 1.6× |
| Encode at 1080p, `CPUQuota=150%` (what the worker gets) | **127 s** | **5.3×** |
| Encode at 720p, `CPUQuota=150%` | 95 s | 4.0× |
| Decode only (`-f null -`), `CPUQuota=150%` | 37 s | 1.5× |

So the film needed about **2204 s (37 minutes)** and was given 1370 s — **38 % short of
the need** — and the shortfall is structural: the factor is per second of film, so
every 10-bit HEVC source of more than a minute or so would hit it.

The box was not throttled (`vcgencmd get_throttled` → `0x0`) and nothing else was
running during the job; the journal for 06:30–07:00 holds only the net-watch timer.

### 46.4 Two defects, both fixed

**1. The deadline was measured against a box the worker never gets.**
`CONVERT_FACTOR` 3.0 → **8.0** (1.5× headroom over the measured 5.3×), and
`MAX_TIMEOUT` 6 h → **12 h**, because a two-hour film from such a source needs ~10.6 h
and a 6-hour ceiling would fail it after six hours of work. This film is now allowed
3452 s (57.5 minutes) and needs 2204 s. The ceiling is still a ceiling: a broken file
cannot pin the card for a day, and `MIN_TIMEOUT` is unchanged.

**2. The film's own page could not see a failure.**
`/media/{id}` builds its row with `ark_media.item()` — no `job_state` column — and
`status_of()` read the job state only from the row. It fell through to `plan=convert`
and `play_stored=''`, which is `needs`, and the page printed **Getting ready** for a
film nobody was working on. The `status == "failed"` branch that prints the reason was
unreachable code. `status_of()` now takes the latest job's state explicitly, and the
page says:

* **This film could not be prepared** — plus *the original file is kept, ask the
  owner to try again*, and the reason itself stays on the owner's Media page;
* **Not ready yet — ask the owner to prepare it** when nothing is queued or running
  (this was the same lie in a quieter form: *Getting ready* for work nobody had asked
  for);
* **The file is not here** when the original has gone.

The shelf card and the owner's page already said *Could not be prepared* — only the
page a viewer actually opens was wrong.

**3. An independent review found two more, and both were real.**

* *A poster job decided the verdict.* `last_job()` was kind-agnostic, so a
  **thumbnail** job that succeeded after a failed conversion wiped the failure off the
  page (or, failing itself, blamed preparation for a missing picture).
  `last_job(conn, id, kind="prepare")` and the `listing()` subqueries are now filtered
  to `kind = 'prepare'`: the status speaks about preparation, and only a prepare job
  may answer.
* *A failed job outranked a playing copy.* `status_of()` checked `failed` before
  `play_stored`, so an owner who pressed **Prepare again** on a film that already
  played — and watched that attempt fail — got a working player above the words
  *Could not be prepared*. A film with a playing copy (or a `direct` plan) is now
  `ready` first, and a failed job after it cannot take that away.

The review also caught that the box's raw failure text (an FFmpeg line, or
`TimeoutExpired: …`) had become visible on a **public** page, which §5 does not allow.
It is now printed only on the owner's page; the household sees one plain sentence.
And `docs/USING-THE-ARK.md` still promised *40 minutes for a 90-minute film*, which
this change had just proved false — it now says hours, with the number.

### 46.5 Evidence

* `tests/test_media.py` — 44 passed (38 before; six added):
  `test_a_preparation_that_failed_is_not_advertised_as_getting_ready`,
  `test_a_picture_taken_later_does_not_erase_a_failed_preparation`,
  `test_a_film_nobody_is_working_on_does_not_say_getting_ready`,
  `test_a_film_that_plays_is_ready_even_if_a_later_attempt_failed`,
  `test_a_long_film_gets_the_time_the_box_really_needs`,
  `test_the_deadline_is_still_a_ceiling_not_a_promise`.
* Full suite: **802 passed, 2 skipped**.
* The measurements above were taken on the live box with `systemd-run --scope -p
  CPUQuota=150%` so the quota was real, not assumed.
* **The whole film, converted with the argument array the fixed settings produce**
  (`build_command()`, run on the box as a transient unit with the worker's own
  `CPUQuota=150%`, `Nice=10`, `IOSchedulingClass=idle`): **2080 s — 34.7 minutes** —
  inside the new 3452 s ceiling, and 710 s *outside* the old 1370 s one, so the old
  settings would have killed it again. Output **190,353,137 bytes**, H.264 High
  1480×1080 `yuv420p` + AAC-LC stereo, `duration = 416.541125` against the source's
  416.541 (whole, not truncated), `moov` before `mdat` (faststart). The extra `text`
  track is the source's two Matroska chapters, not a subtitle. Nothing was published:
  the file was written under `/srv/ark/tmp` and deleted afterwards.

### 46.6 What this did not fix *(status as of §46; both open items were decided and
built in §47)*

* **A 1080p 10-bit HEVC film is expensive on a Pi 4**, and now it is honest about it:
  ~37 minutes for 7 minutes of cartoon, ~10.6 hours for a two-hour film. Dropping the
  encode cap from 1080 to 720 would cut that by about 25 % (95 s vs 127 s per 24 s) and
  shrink the files, but it changes what every viewer gets, so it waits for the owner —
  **the owner said yes; §47 does it.**
* **Hardware decoding works on this box and is nearly free**: `-hwaccel drm` decoded
  12 s of this file in 2 s, against 18 s in software for the same span. It is not used
  yet — it needs `/dev/dri` access for the service user and a tested fallback.
  **`pi` already had the access; §47 adds the decoder and the fallback.**
* **A film longer than about two hours** (from a 1080p 10-bit HEVC source) still
  exceeds the 12-hour ceiling and will fail after the full twelve hours rather than
  giving up early. A pace check — compare `out_time_us` against the time left and stop
  when it cannot finish — is the obvious next step and is not built. *(After §47 the
  same source runs at 1.85× instead of 5.3×, so the ceiling now covers about six
  hours of film; the pace check is still not built.)*
* **A long job is retried from the beginning after a cut.** `ark_media.recover()` puts
  an interrupted `running` job back in the queue, up to three attempts, so a film near
  the ceiling that is interrupted twice could occupy the card for three times its
  length. The three-strike rule is the project's own; the interaction with a 12-hour
  ceiling is new and has not been decided on.
* ~~Nothing here has been deployed yet.~~ **Deployed 3 Nov 2026 — §47.6.**

---

## 47. A playing copy that fits a phone, decoded by the Pi itself — 3 Nov 2026

### 47.1 What the owner asked for

Two options offered at the end of §46, both accepted: **downrez anything above
720p**, and **use the Pi's hardware decoder** rather than declining it.

### 47.2 Measured before anything was changed

The same 24 seconds of the same 1480×1080 10-bit HEVC film, every run under the
worker's own `CPUQuota=150%`:

| Run | Wall clock | Per second of film |
|---|---|---|
| 1080p, software decode (what the box did) | 127 s | 5.3× |
| 720p, software decode | 95 s | 3.96× |
| 720p, `-hwaccel drm` | **45 s** | **1.85×** |

The two 720p files were **byte-identical** (`md5 4782a8145dcf55a602ab95f76b7e736b`),
so the hardware decoder is not a quality trade — it is the same film, twice as fast.
`pi` is already in the `video` and `render` groups, and `/dev/dri/renderD128` is
`root:render 0660`, so the worker can open it with no unit change; that was proved
by encoding as `pi` before the code was written.

### 47.3 What changed

* `ark_media_jobs.PLAY_HEIGHT = 720` and a new rule in `build_command()`: the
  **short side** decides, so a 1480×1080 film becomes 986×720, a portrait
  1080×1920 clip becomes 720×1280, 720p and below are untouched, and nothing is
  upscaled. The previous rule only scaled above 1080p.
* `hwaccel_args()` returns `-hwaccel drm` when `/dev/dri/renderD128` exists.
  `ARK_MEDIA_HWACCEL=0` disables it for good; `=1` forces it (tests only).
* `make_derivative()` tries the decoder first and **retries on the CPU when the
  tool refuses before writing a single byte**, logging the refusal. A failure
  halfway through is *not* retried: that is a real failure, and starting a
  20-minute encode again to prove it would waste the card. The signal is
  `wrote["size"]`, which `on_line()` already maintained for the space ledger.
* The deadline is unchanged (`8 × length + 2 min`, at most 12 h) on purpose: it
  must still cover the *slow* path — a file the decoder refuses runs at 3.96× in
  software, so lowering the factor to fit the fast path would break the fallback.

### 47.4 What it means on this box

| Film | Before (§46 fix) | Now |
|---|---|---|
| 7 minutes (the test film) | 37 min | **13 min** |
| 90 minutes | 8 h | **~3 h** |
| 2 hours | 10.6 h | **~3¾ h** |

And every playing copy is at most 720p: the 7-minute film's copy is 986×720 and
190 MB at 1080p became the same film at 720p in a fraction of the time.

### 47.5 Evidence

* `tests/test_media.py` — 48 passed (44 before; four added):
  `test_a_film_above_720p_is_prepared_at_720p_and_a_smaller_one_is_left_alone`,
  `test_the_hardware_decoder_is_asked_for_only_when_the_device_is_there`,
  `test_a_hardware_decoder_that_refuses_a_file_is_tried_again_on_the_cpu`,
  `test_a_conversion_that_failed_halfway_is_not_started_over`.
* Full suite: **806 passed, 2 skipped**.
* The live proof, after deploy, is recorded in §47.6.

### 47.6 Deployed and proved on the live box

**Deploy (3 Nov 2026, 18:03–18:04 UTC).** Rollback points first:
`/root/ark-db-before-720p.db` (SQLite `.backup`, WAL-safe) and
`/root/ark-src-before-720p.tgz`. Then `pi/` → `/srv/ark/src` by the same rsync
`03-provision.sh` uses, and `install-ark.sh --only 60-portal,62-media`: every phase
succeeded, `ark-api`, `ark-media-worker` and `nginx` active, `/api/ready` 200.
The film's own page immediately stopped lying — it read *This film could not be
prepared. The original file is kept. Ask the owner to try again — the reason is on
their page.*

**A defect found by the first live run, and fixed (commit `1797d5e`).** Job 5
started at 18:04:47 and died at 18:11:00 with `OperationalError: attempt to write a
readonly database`, six minutes in. The filesystem was `rw`, the card had 105 GB
free, `dmesg` held no I/O error, and the database was writable again immediately
afterwards. The cause was a **reader**: a `sqlite3` status query issued at 18:11:00
opened the live WAL database and checkpointed it on close, out from under the
worker. That is not an exotic accident — `ark-self-heal` runs every day at 04:17,
and an owner's console can do the same thing — so the worker was made to survive
it: the per-tick writes (space ledger, heartbeat) are telemetry and are now logged
once and skipped, while the write that *publishes* the film stays strict. A job
that dies for any other reason removes its own half-written copy by name, and
recording a failure can no longer take the worker down with it.

**The successful run.** Job 6: started 18:25:35, finished 18:41:51 — **976 s
(16.3 minutes)** for a 416.5-second film, with `-hwaccel drm` in the command line
and no fallback logged. Published `m4.mp4`, **75,389,766 bytes**, H.264 High
**986×720** `yuv420p` + AAC-LC, `duration = 416.541125` (the source is 416.541),
poster `m4.jpg` from the first attempt, job detail *Converted for this box.*

| | Before | After |
|---|---|---|
| Same film, same box | 34.7 min at 1080p, software decode, **failed** | **16.3 min at 720p, hardware decode, published** |
| Copy size | 190 MB (1080p) | **72 MB (720p)** |

**Proved in a browser, over the network.** Chromium loaded
`http://ark.local/media/4` from the Mac, played the film (6 s in: `currentTime`
5.88 s, `readyState` 4, 20 s buffered, not paused), and sought to 200 s, landing at
202.9 s with `readyState` 4. No console errors. `GET /media/4/stream` answers
`206 Partial Content`, `video/mp4`. The shelf now reads **Ready to play**; nothing
on it says *Getting ready* except the filter that means exactly that.

---

## 48. The upload leaves once the film can play — 3 Nov 2026

### 48.1 The question, and what was true before

Right after §47 the owner asked whether the box deletes a film's original
upload after conversion, "to preserve storage space". It did not. The 720p cap
made the copy the film, but `drop/files` kept the source for ever: film 4 held
**141,964,578 bytes** of upload beside its **75,389,766-byte** playing copy —
217 MB for a seven-minute film. The only code that removed an original was
`ark_media.delete()`, i.e. deleting the whole film.

### 48.2 What changed

* `ark_media.release_original(conn, media_id)` removes the upload and its hidden
  File Box row once a **published, verified** playing copy exists. It refuses —
  deliberately — for: a **direct** film (its original *is* what the player
  serves), a film with **no copy**, a film **adopted from the File Box**
  (`files.hidden = 0`: those bytes are somebody's download, not a spare), and a
  stored file **another film still names**. Bytes go before rows, so an
  interrupted release leaves a missing original the next attempt can finish,
  never bytes no record names.
* `ark_media.release_ready_originals(conn)` is the sweep; it runs after every
  publish (`make_derivative`) and **once at worker startup**, so a cut between
  "copy published" and "upload released" cannot leave the larger file behind.
  `ark-media-worker --trim` runs it by hand. A release that fails is logged and
  never turns a ready film into a failed job.
* `playable()` now serves a recorded copy on its own; `has_bytes()` answers
  "are the bytes a viewer needs here?" and `original_present()` stays the
  question "is the upload still on the card?". `footprint()` is what the card
  and the film page report: copy plus any kept upload.
* Pages tell the truth: the shelf card shows the bytes on the card, the film
  page says the original was removed and no longer offers **Download the
  original**, and the owner's page shows *original removed* and stops offering
  **Prepare again** / **New picture** (both need the source; the routes refuse
  with a sentence instead of queuing a job that must fail). The in-player
  fallback link now points at the copy, not at the gone original.

### 48.3 A defect the change exposed

`playable()` used to require the original file before it would serve a playing
copy, and three pages computed `present = original_path(row).is_file()` for
`status_of()`. Released as designed, every converted film would have read *The
file is not here* while its copy sat on the card. The new `has_bytes()` is now
the single answer used by the shelf, the film page and the owner's page.

### 48.4 Evidence

* `tests/test_media.py` — **55 passed** (49 before). New:
  `test_a_film_that_plays_as_it_is_keeps_its_only_copy`,
  `test_the_file_box_share_keeps_its_original`,
  `test_an_original_is_not_released_before_a_playing_copy_exists`,
  `test_the_sweep_releases_only_films_that_already_have_their_copy`,
  `test_preparing_again_a_film_whose_upload_was_released_says_it_is_ready`,
  `test_the_owner_page_says_when_the_original_was_removed`; the repack and
  delete tests were rewritten for the new truth.
* Full suite: **813 passed, 2 skipped**. `scripts/ark-secrets-check.sh`: clean.
* Commit `60a8803`, pushed to `origin/feature/admin-hardening`.

### 48.5 Deployed and proved on the live box

**Before deploying:** film 4's copy was measured
(`md5 493915199a7be3c8198531b8f1325d08`, 75,389,766 bytes, 986×720, duration
416.541125) and `/media/4/stream` answered 206. The worker was stopped (queue
empty), `/root/ark-db-before-trim.db` was written with a **read-only**
connection's `.backup()` (a read-only connection cannot checkpoint the WAL that
killed job 5 — §47.6), and `install-ark.sh --only 60-portal,62-media` finished
with every phase ok.

**The restart did it.** The worker's startup sweep ran at 20:57:46 UTC:

```
startup: released 1 original(s), 141964578 bytes
media worker: ready ({'requeued': 0, 'temp_removed': 0, 'reservations_released': 0,
                     'originals_released': 1, 'bytes_released': 141964578})
```

* The upload is gone from `/srv/ark/drop/files` (the folder is now empty) and
  its `files` row is gone; `media.stored = ''`, `play_stored = 'm4.mp4'`,
  `play_size = 75389766`, `state = published`.
* The copy is byte-identical (same md5) and untouched.
* Free space rose from **112,667,951,104** to **112,809,529,344** bytes across
  the deploy window (+141,578,240; the release itself reported the file's
  141,964,578 bytes and other writes used the difference).
* `GET /media/4` → 200, `/media/4/stream` → 206, `/media/4/download` → **404**
  (there is no original to send), `/api/ready` → 200.
* The film page says *"…removed the original upload to save room. What you are
  watching is that copy."*, reports **Size on this box 71.9 MB**, and has **no**
  download link. The shelf card reads **6:57 · 71.9 MB · Ready to play**.
* Chromium over `http://ark.local/media/4`: played (`currentTime` 5.89 s,
  `readyState` 4, 987×720, 20.6 s buffered), sought to 200 s and landed at
  202.87 s, **no console errors**.

### 48.6 What it costs, and what it does not do

The full-quality source is gone after release; a film wanted at full resolution
has to be kept off the box. A film adopted from the File Box keeps its original
because the File Box still offers it as a download — the shelf adds the copy and
changes nobody's link. Rollback is `/root/ark-db-before-trim.db` plus the tree
at commit `f632714`; neither the film nor its copy was touched.

---

## 49. Two states on the map shelf — 3 Nov 2026

### 49.1 What was asked, and what a state actually is

"Please add maps covering New Mexico and Texas." Measured before building
anything, with pyosmium over the Geofabrik extracts: New Mexico holds
**5,705,969** routable points and Texas **23,686,304** at full detail, against
a format built for **400,000** — about what one phone can carry and parse
(graph, places and layers together). A state cannot be one full-detail pack.
Splitting Texas into 60 packs of 400 k points is not a shelf anybody can use.
So each state is published as a **main-road atlas**: the numbered and primary
network, drawn and routed, with the smaller roads left out of both.

### 49.2 The demo pack was removed by its owner, not by the box

The Liechtenstein demo was already gone when the shelf was inspected. The admin
access log says why: at 21:37:55 UTC the owner's browser posted
`/admin/maps/delete` (twice refused for a wrong password, then accepted) and the
page answered *"liechtenstein version 2026-11-02 was removed from the Ark."*
No fault to fix, and no restore: the demo had done its job.

### 49.3 Three arguments and two defects in the builder

* **`--min-class`** keeps a road class and above (`secondary` for New Mexico,
  `primary` for Texas) and drops the rest before the builder sees them, so the
  router and the drawing stay in agreement.
* **`--min-water-area`** leaves out unnamed water shapes below a size — a state
  is full of stock tanks — while a named lake or reservoir is always kept.
* **`--named-water-lines-only`** stops drawing every unnamed stream segment,
  which alone was **121 MB** of the first New Mexico pack.

Two defects surfaced with the first real state:

* **A stray relation member stretched the region.** The New Mexico extract
  carries a power line's tower at **59°N in British Columbia** (node
  13374219996). `_stretch()` ran for every tagged node and every way, so the
  pack's bounds reached Canada and the verifier refused it as "more than a
  regional extract should". Only an object that *contributes* — a place to
  search, a routable way, a drawn shape — moves the bounds now.
* **A cap of zero read as "capped at 0".** `--max-buildings 0` now says
  "not carried", and the "further buildings were left out" sentence is not
  printed when none were wanted.

### 49.4 The two packs

| | New Mexico — main roads | Texas — main roads |
|---|---|---|
| Roads carried | motorway, trunk, primary, secondary | motorway, trunk, primary |
| Graph | 258,062 points, 464,302 edges | 840,648 points, 1,320,528 edges |
| Turn restrictions | 920 | 6,372 |
| Searchable places | 39,640 | 40,000 (cap) |
| Layers | forest 5,403 · water 14,458 · boundary 2,464 · major 12,362 · minor 6,588 | forest 32,551 · water 47,290 · boundary 526 · major 149,073 · minor 0 |
| Pack size | 58,289,941 bytes (55.6 MB) | 201,286,420 bytes (192.0 MB) |
| Bounds | 31.12–37.64 N, −111.09 to −102.16 E | 25.45–37.00 N, −106.83 to −92.62 E |
| Data date | 2026-11-02 | 2026-11-02 |

Both were built on the Mac (75 s and 396 s of extract reading), verified by the
same check the box runs, copied as zips, and imported with `ark-maps --import`.
Each passed a second `--verify` on the box (every fingerprint matched) and was
published. The catalogue now lists both as ready and offered, and a Range
request on `graph.bin` answers `206`.

### 49.5 A router defect the Texas pack found

Tapping Dallas (from the search) answered *"There is no route between those
points"*. The graph was not broken: Austin's nearest node could reach 771,775
of 840,648 points, and Dallas's nearest node could reach Austin — but Austin
could not reach **that** node. It sat on a one-way pair whose entrance is not
reachable from the south, 287 m from the town point, while a reachable node sat
360 m away. The router snapped to the nearest point and gave up.

`router.js` now retries the next nearest destination points (up to four, within
the same 500 m snap limit) when the first search finds no path — but **only for
a point that had to be snapped**. A request sitting exactly on a graph node
still means that node, so the one-way rule keeps its honest "no route" (the
existing test proves it), and the reported snap distance is measured from the
place the person asked for, never from the alternative node.

### 49.6 Proved in a browser, over the LAN

Chromium on the Mac, `https://ark.local:8446/maps/` with the certificate not
trusted (the documented human step), so the service worker was refused — fetch,
IndexedDB, search and routing all still worked:

* **New Mexico:** downloaded in **10.7 s**, opened with a painted canvas, search
  "Santa Fe" returned County, City, Brew Pub, parks and fire stations; route
  Santa Fe → Albuquerque **102 km, about 59 min**.
* **Texas:** downloaded in **27.8–30.7 s** (192 MB), opened (canvas painted);
  route Austin → Dallas **319 km, about 2 h 57 min**, panel in 5 s; search
  "Houston" returned its places.

### 49.7 What these packs do not do

They are **main-road atlases**: local streets and street addresses are not in
them, so a route inside a town follows the numbered arteries only. A pack that
covers one town at street level is a different, smaller build (no `--min-class`).
Texas is a 192 MB download and 192 MB of browser storage; New Mexico is 55.6 MB.
`Ready offline` still needs the Ark's certificate trusted on the device — the
automated browser refused the service worker for exactly that reason — and a
real phone remains the test in `docs/HUMAN-TEST-LIST.md` §22.

### 49.8 Evidence

* `tests/test_maps.py` — 19 passed (16 before): the stray far-away object, the
  water-area floor, and the named-water-lines rule.
* `tests/js/` — 15 passed (14 before): a town point that snaps to a one-way
  island is routed to a reachable road, and the one-way rule still refuses to
  drive back up the lane.
* Full suite: **816 passed, 2 skipped**.
* Commits `a2d1a98` (builder) and `6d36d16` (router), both pushed; the box's
  maps application is byte-identical to `router.js` in the commit
  (`04d7916a…`).

---

## 50. El Paso, at street level — 3 Nov 2026

### 50.1 What was asked

The owner lives in El Paso and asked for "a more detailed map of El Paso in
particular". The two state packs carry the numbered network only; this one is
the opposite: every street, cropped out of the Texas extract.

### 50.2 A box, and what it keeps

`scripts/ark-map-pack --bbox S,W,N,E` crops an extract. The box is inclusive and
a road with **any** point inside it is kept whole, so a route can leave the box
on the road it arrived on — which is also why the pack's own bounds reach a
little past the box. The point test is `ark_maps.in_box()`, unit-tested, and the
pack states the box in its own limitations.

The box used: **31.35–32.05 N, 106.70–105.90 W** — El Paso County and a margin.

### 50.3 The pack

| | El Paso |
|---|---|
| Graph | 386,511 points, 856,404 edges |
| Street names | 13,294 |
| Turn restrictions | 247 |
| Searchable places | 4,426 |
| Streets drawn | base-minor 67,964 + base-major 3,471 |
| Buildings | 30,000 (the format's cap) |
| Water / forest / boundary | 4,412 / 13 / 73 |
| Pack size | 51,668,403 bytes (49.3 MB) |
| Bounds | 31.10–32.30 N, −106.83 to −105.75 E |
| Data date | 2026-11-02 |

Built in six minutes from the Texas extract (one 353-second pass, 14.2 million
objects left outside the box), verified at build, imported, checked again on the
box byte for byte, and published. It is the third pack on the shelf, beside New
Mexico and Texas.

### 50.4 Proved on the live box

Chromium over the LAN: the catalogue listed **El Paso** first; it downloaded in
**11.9 s** (51.7 MB); the map opened; searches returned
*El Paso International Airport — Terminal*, *The University of Texas at El Paso
— University*, *Ysleta — Suburb* and its schools; a route from the airport to
UTEP measured **13 km, about 11 min**. The only console error was the
certificate refusal of the service worker — the documented human step, not a
map fault.

### 50.5 What it does not have

* **Juárez is not in it.** The Geofabrik Texas extract stops at the Rio Grande;
  the Mexican half of the metro needs a Mexico extract of its own.
* **No street-address search.** The format has no address index; search finds
  named places and points of interest, and street names live in the routing
  graph. The catalogue card says "no street addresses".
* **30,000 building footprints** is the format's cap, not the city's count.
* 49.3 MB of browser storage on a phone that installs it.

### 50.6 Evidence

* `tests/test_maps.py` — 20 passed: `in_box()` keeps the inside, refuses the
  outside, and a missing box keeps everything.
* Full suite: **817 passed, 2 skipped**.
* `ark-maps --published`: `el-paso 2026-11-02 49.3 MB`, beside the two states.

---

## 51. One maintenance protocol: nothing replaces a database while anyone still holds it — 3 Nov 2026

### 51.1 The finding, at `448638b`

`ark-restore` stopped a list of units before replacing `ark.db`, and the list did not
contain `ark-media-worker.service`. The worker is an independent process with its own
database handles and its own job queue; stopping `ark-api.service` does not stop it, and
`Restart=always` keeps it alive. So a restore could replace the records underneath an
active film job, take the old journal away, and leave the worker writing rows for a file
that no longer existed.

The same gap was one layer deeper. `ark_self_heal`'s repair lock made two *repairs* take
turns, but ordinary connections never took it. A reader that kept a connection — a
request, a console command, a worker between jobs — was invisible to the repair, which
could replace the file under it as soon as it had the repair lock.

### 51.2 Every database user, and who stops for whom

| User | What it holds | How it participates |
|---|---|---|
| `ark-api.service` (every public and admin request) | `ark.db`, one connection per request (sometimes two) | `ark_db.connect()` takes the shared gate for the connection's life |
| `ark-media-worker.service` | `ark.db` for claims, heartbeats and results, between `ffmpeg` runs | same gate; **also stopped by `ark-restore`** (the finding) |
| `ark-ai.service` | `ark.db` for the counted brief and the game list | same gate; not stopped — it drains like any reader |
| `ark-arcade` (console + `ark-arcade-discover.timer/.service`) | `ark.db` for the shelf, imports and discovery | same gate; the timer and service are stopped by a restore |
| `ark-book-import.path/.service`, `ark-import-books` | Calibre's `metadata.db`, `drop/files`, the import ledger | no `ark.db`; stopped by a restore because it writes the files and the ledger |
| `ark-zim-downloader.service` | `kiwix/`, no database | stopped by a restore (existing behaviour; it is a card writer) |
| `ark-self-heal.timer/.service` | `ark.db`, Calibre's two databases | repair turnstile + exclusive gate; stopped by a restore |
| `ark-calibre.service` | Calibre's `metadata.db` and `app.db` | **cannot take the gate** (third-party); a repair of either stops it first and starts it again |
| `ark-backup`, `ark-restore` | `ark.db` — one read, one replacement | shared gate / turnstile + exclusive gate |
| `ark-space`, `ark-admin-recover`, `ark-media-worker --status/--once/--trim` | `ark.db` through `ark_db` | the same shared gate; a maintenance window waits for them or reports them |
| `ark-maps`, `ark-essentials`, `ark-romdb-build`, `ark-kiwix`, `ark-net-watch` | no `ark.db` | not in the protocol; they are not writers of these records |

`ark-boot-guard` and `ark-fsck-once` are deliberately **not** stopped or restarted: they
are `RemainAfterExit` one-shots, which report `active` for ever while running nothing, and
starting them again would run their boot logic in the middle of a restore.

### 51.3 The protocol: a gate and a turnstile

`pi/api/ark_maintenance.py` is the one rule, and every writer above either follows it or
is stopped by something that does.

* **The gate** — `<database>.maintenance.lock`, a `flock`. Every ordinary connection
  takes a *shared* hold **before** SQLite opens the file and keeps it until the handle is
  closed. Maintenance takes the *exclusive* hold. Taking it therefore drains every
  existing handle (including one somebody forgot to close) and holds new connections at
  the door for a bounded wait. A new page waits up to `ARK_GATE_ADMIT_WAIT` (5 s), then
  gets a plain 503 sentence; it never hangs.
* **The turnstile** — `<database>.repair.lock`, taken *first* by every maintenance
  operation, so two repairs take turns instead of reading their good copies from either
  side of each other. The gate is always taken second; nothing takes the turnstile while
  holding the gate.
* **The files are never deleted.** The kernel releases the lock when the process dies, so
  a crash is not a permanent lock; the file that remains is only the name the lock was
  held on. They are mode `0666` because root and `pi` both use them — a root-only gate
  would lock every page out.
* **A gate that cannot be had is an outcome, not evidence.** Self-heal reports
  `deferred`; a restore refuses before replacing anything. Neither calls the database
  damaged.
* **Resume is conditional.** A restore starts again only the units that were active, in
  reverse stop order, and only when the outcome says the records are safe to open. A
  database put back beside a journal that would not go leaves the box dark on purpose.

`ark_db.connect()` binds the shared ticket to the connection object (`close()` releases
it; `__del__` is the belt), so "the handle is open" and "the gate is held" are the same
fact, not two facts that can drift apart.

### 51.4 What changed

* `pi/api/ark_maintenance.py` — new; the protocol above, with bounded waits and explicit
  `Busy`/`Lease` outcomes.
* `pi/api/ark_db.py` — every connection takes the shared gate for its lifetime.
* `pi/api/ark_self_heal.py` — repairs take the turnstile, then the gate, and re-ask the
  damage question under it; new `deferred` (busy, nothing changed) and `unsafe` (copy in
  place, journal would not go) states; Calibre's two databases get their owning unit
  stopped and restarted; `_copy_over_status()` records the two moments a caller cannot
  otherwise see.
* `pi/bin/ark-restore` — `WRITERS` now includes `ark-media-worker.service`; the run takes
  the turnstile, stops the units, takes the gate, replaces, releases, then resumes only
  what it stopped and only when safe; a post-copy journal failure is `safe_to_start=False`
  and leaves the box dark with a sentence saying why.
* `pi/bin/ark_bundle.py`, `pi/bin/ark-backup` — the database copy takes the shared gate
  too, so a backup cannot copy a file that is halfway out of the door.  A side effect
  worth naming: `ark-backup` now finds `ark_space` on the box (the module path is
  prepared by `ark_bundle`), so its space check actually runs there instead of being
  silently skipped.  `require()` is a read-only check against the reserve.
* `pi/api/main.py` — a request during a maintenance window answers 503 with a plain
  sentence instead of a 500.
* Phases `15-resilience`, `60-portal` and `95-backup` install the new module (a module the
  installer never copies is a service that will not start).
* `pi/RECOVERY-SHEET.txt`, `docs/ADMIN.md`, `README.md`, `docs/REQUIREMENTS-MATRIX.md`
  updated in the same change.

### 51.5 Proved on the Mac, with real processes

`tests/test_maintenance.py` (13) and the new cases in `tests/test_backup.py` (5) use real
subprocesses — a real `ark_db` connection held by another process, a real `SIGKILL`, real
`ark-restore`/`ark-backup` runs against a throwaway box:

* an open reader blocks a repair (`deferred`, bytes unchanged) and a kill releases the
  lock so the next pass restores;
* a maintenance window makes a new connection answer `busy` within the bound, and the
  public board answers **503** with a sentence;
* two repair processes' intervals do not overlap (measured timestamps);
* a killed exclusive owner is released by the kernel, and the lock file remains `0666`;
* a reader holding the records makes `ark-restore` refuse, resume the API, and succeed on
  the next attempt after the reader is gone;
* the film worker is stopped even though the API's stop does not own it, and only units
  that were running are started again;
* a backup refuses while the records are being replaced;
* a failed post-copy journal drain is `safe_to_start=False`; a pre-copy failure is safe;
* the sweep never matches either lock file, and one database's gate does not block
  another.

The full suite collects **840 tests**; the run without the three browser map tests
(they need Chromium and are unaffected) gives **835 passed, 2 skipped**.

### 51.6 What is not proved

* **No destructive live restore and no power-cut test was performed** (on purpose).
  The restore gate is proved against throwaway boxes on the Mac; the live-box proof
  (§51.7) is a non-destructive `--dry-run`, the readiness gate, and a scratch database
  under `/srv/ark/tmp` exercising the lock across two real processes.
* A restore killed **between** `os.replace` and the post-copy drain leaves the box dark
  (by design) and requires the console step the refusal prints. The unit test covers the
  classification; nothing can safely simulate the crash on the live card.
* Calibre's engine is still not a gate participant; the repair stops it instead. A
  Calibre upgrade that changes its unit name would be caught by the phase tests, not by a
  running box.
* No real phone or reader was used to watch a 503 during a maintenance window.

### 51.7 Deployed and proved on the live box — 3 Nov 2026

**Before touching it:** `sudo ark-backup --to /srv/ark/backup --keep 3` wrote
`ark-backup-20261103-235239` (347.0 kB; 0 messages, 0 shared files, 0 player names,
0 game saves, the import list), and the deployed source tree was saved as
`/srv/ark/tmp/rollback-src-20261103-235238.tar.gz` (20 MB). The previous deployed
revision was `448638b`.

**The deploy** was the established one, not a hand copy:

```
bash scripts/03-provision.sh ark.local --only 15-resilience,60-portal,95-backup,90-verify
```

All four phases reported **ok**, none failed, in 49 s. Phase 15 ran a self-heal pass
at 23:53:09 (`0 repaired`, nothing left to look at); phase 60 restarted `ark-api` and
proved `/api/health`, `/api/status`, `/api/ready`; phase 95 installed the tools and
planned a backup; phase 90 printed the full gate: every line `✓` except the expected
informational `·` (the next boot's forced card check, the first network-watch check,
and the backup count).

The two long-lived holders were restarted afterwards so they would load the new
`ark_db`, and both came up active:

```
sudo systemctl restart ark-media-worker.service ark-ai.service
```

**Hashes on the box matched the commit**, byte for byte:

| File | sha256 (first 16) |
|---|---|
| `/srv/ark/api/ark_maintenance.py` | `d8b2575d35803dc2` |
| `/srv/ark/api/ark_db.py` | `d82a5e433e9f3af1` |
| `/usr/local/sbin/ark-restore` | `f624a793514c3e4d` |
| `/usr/local/sbin/ark_bundle.py` | `6bedeca2c30008fe` |

**Readiness:** `http://ark.local/api/ready` → **HTTP 200**, `ok: true`, `ready: 9/9`,
`blocking: []`. The self-heal receipt (`/srv/ark/logs/self-heal.json`) is
`ok: true`, `healed: 0`, `problems: []`. `ark-media-worker --status` answers and shows
8 films, no queued or running job; `ark-restore --list` lists three bundles.

**The protocol, on the real card, on a scratch database** under `/srv/ark/tmp` (never
the live records), using the deployed `/srv/ark/api` and venv:

* a real `ark_db` connection in another process → `BLOCKED_BY_READER`;
* `SIGKILL` that process → `AFTER_KILL_HELD 0.0` (the kernel released it at once);
* a **different** process trying `ark_db.read()` during a maintenance window →
  `REFUSED Busy` (the same process is admitted by design: a maintenance owner may
  read the database it is repairing);
* the repair turnstile → `TURNSTILE_HELD`;
* both lock files survived the kill and are `-rw-rw-rw-` (`0666`);
* the scratch tree was removed afterwards (`ls /srv/ark/tmp/gate-probe-*` → none).

**Not done, on purpose:** no destructive live restore, no power-cut test, and no live
`ark-restore` of the real records. The restore path is proved against throwaway boxes
on the Mac and the protocol is proved on the card with a scratch database; the
human rehearsal in `docs/HUMAN-TEST-LIST.md` §24 is the next step if a real-card
refusal is wanted without risking the live records.

**Rollback:** the previous tree is the tarball above and commit `448638b` on the Mac.
To go back, `sudo tar -C /srv/ark -xzf /srv/ark/tmp/rollback-src-20261103-235238.tar.gz`,
re-run `15-resilience`, `60-portal` and `95-backup`, then
`sudo systemctl restart ark-api ark-media-worker ark-ai`. The pre-deploy bundle is the
records' rollback.
