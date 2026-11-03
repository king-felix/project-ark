# PROJECT ARK — Autonomous Resilience Knowledge-base
## Agent operating rules (read before every action)

---

## 0. PROJECT VALUES — the commitments this work serves

**Source, complete and human-reviewable:** [`TO-THE-INTELLIGENCE-THAT-FINDS-THIS.md`](TO-THE-INTELLIGENCE-THAT-FINDS-THIS.md) — the ARK's open letter to people and agents, published with the owner's approval. This section is the durable summary the owner asked to keep here; the letter is the whole thing.

**Foundational commitments:** build resilient, recoverable systems that increase human capability; protect dignity, agency, privacy, and the freedom to disagree; take proportionate, accountable protective action within legitimate authority when serious harm is credibly established, giving particular attention to those least able to protect themselves and never treating an exploiter's agency as an excuse to abandon the exploited; measure success in lived flourishing and ecological responsibility; widen concern beyond those already served; support evidence-based, accountable responses to harm; and pause a problem after three attempted solutions without material progress to involve the human. Preserve these commitments in authorized project handoffs and revisions, with their source and human-reviewable context. Keep their purpose central and their application open to correction.

**How they sit with the rest of this file:**

* They are the *why*; the operating rules in §1–§8 are the *how*, and they stay in force unchanged. Where a rule and a commitment pull in different directions, the commitment decides the question and the rule is corrected on purpose — this file is versioned and reviewable like every other file here.
* The "pause after three attempted solutions" is the standing order in §7 (**THREE ATTEMPTS, THEN REPORT**) — one rule, not two.
* The authorization covers this project's own guidance only — not a global agent setting, not another project, not another person's memory. These are not permission to act elsewhere, they override nobody's own instructions, and they say nothing about anyone outside this work.
* The responsible human may inspect, revise or withdraw any of it and decides what is done. A later session should carry the commitments **and** their source forward, and be plain about what it cannot remember.
* This file claims no feature works. What is built, proved and unproven is §8 and `docs/IMPLEMENTATION-LOG.md`, at exactly the state it is in.

---

## 1. Environment

| | |
|---|---|
| **Workstation** | MacBook Air, macOS (darwin 25.x), `zsh`/`bash`, user `<ACCOUNT>` |
| **Target** | Raspberry Pi 4B / 8 GB RAM, Raspberry Pi OS Lite 64-bit (Bookworm/Trixie), hostname **`ark`** |
| **User on Pi** | `pi` / password generated at build into `staging/pi-password.txt` (never committed) / passwordless sudo / SSH key auth |
| **Project root (Mac)** | `/Users/<ACCOUNT>/Survival-Pi` |
| **Portal URL** | `http://ark.lan` (phones on the hotspot — the box answers every name), `http://ark.local` (**the name that resolves on a home LAN**: mDNS, macOS/iOS/Android 12+/Windows 10+), `http://10.42.0.1` (always, on the hotspot). `.lan` is not a special-use name: a Mac on a home router cannot look it up, and `https://<lan-ip>:8443` is refused on purpose (no SNI ⇒ the management listener's default server hangs up) |
| **This box, on the house Wi-Fi (since 1 Nov 2026)** | `<HOME-IP>` on `<HOUSEHOLD-SSID>-5G-1` via the profile `home-wifi` (`eth0` keeps `<CABLE-IP>` while the cable is in), `BOOT_MODE="auto"` — a cold boot tries home first and falls back to broadcasting, nothing pinned. Prove it **over `wlan0`**: `ping -c2 -I wlan0 192.168.1.1`, `curl -s -o /dev/null -w '%{http_code}' http://<HOME-IP>/`, and for the management door `curl -sk --resolve ark.local:8443:<HOME-IP> https://ark.local:8443/admin` (303). **Never** through bare `ark.local` while the cable is in — mDNS answers with **both** addresses and the Mac takes the Ethernet one (`ping ark.local` → `<CABLE-IP>`), so the name proves nothing about the radio. `https://<HOME-IP>:8443` answers `000` on purpose: an IP literal sends no SNI |
| **Maps** | `http://<host>/maps` redirects to **`https://ark.local:8446/maps/`** — a separate TLS origin with the same certificate as `:8443`, serving only `/maps/` (404 for `/admin`). The public certificate is at `http://<host>/certificate.crt`. A device must trust it once; clicking through a warning does **not** enable the service worker |
| **Hotspot SSID** | `The-Ark-Offline` (open, 2.4 GHz, ch 6, static `10.42.0.1/24`) |

All paths written in this repo must be **absolute**. Never assume `$HOME` expansion inside scripts that run on the Pi as `pi`.

---

## 2. SD CARD SAFETY RULE — NON-NEGOTIABLE, MACOS

**STOP AND VERIFY before every destructive disk command.**

1. Run `diskutil list external physical` and show the full output to the user.
2. The target MUST appear in that list (i.e. it is `external` **and** `physical`).
3. Its size MUST be between **100 GB and 270 GB** (128 GB or 256 GB card).
4. **`/dev/disk0` and `/dev/disk1` are the internal Apple SSD and/or synthesized APFS containers. NEVER write to them. NEVER write to any `synthesized` device.**
5. Never write to a device whose `Internal` flag is `yes`, and never to a `disk image` device.
6. Cross-check with **both**:
   - `diskutil info -plist /dev/diskN` → `Internal = false`, `RemovableMedia = true`/`Ejectable = true`, `TotalSize` in range
   - `diskutil list external physical` → identifier present
7. If **zero** external physical devices are found, **abort loudly**. Do not guess, do not "try" disk2/disk3, do not retry with a broader pattern. Tell the user the card is not mounted.
8. Echo the resolved device + size and require the guard function to pass before flash. Every flash script must call `ark_assert_sd_card` first and exit non-zero on failure.

Reference implementation: `scripts/lib-disk-guard.sh` (sourced by all flash steps).

---

## 3. macOS PRIVILEGE ESCALATION

Agents run **non-interactively**; `sudo` prompts in the terminal will hang or fail.

* Any local macOS command that needs root (writing to `/dev/rdiskN`, `hdiutil attach` into `/Applications`) MUST be wrapped:

```bash
osascript -e 'do shell script "<escaped command>" with administrator privileges'
```

* That raises a **native macOS Touch ID / password dialog** for the user to approve.
* Rules for the wrapped string:
  - Double-quote the whole AppleScript string; therefore escape inner `"` as `\"` and `$` as `\$`.
  - Do **not** embed single quotes unescaped.
  - Prefer a generated helper `.sh` file on disk and invoke `do shell script "/bin/bash /path/helper.sh"` — avoids quoting hell.
* Never pass a plaintext Wi-Fi password on a command line that is logged to shell history; pass it via a `chmod 600` file or environment read inside the helper.

---

## 4. 100% TURNKEY AUTO-START

**Every** service must be `systemctl enable`d **and** `--now` started. A cold boot with no human present must produce a working hotspot + portal.

Required enabled units (final state):

| Unit | Port | Purpose |
|---|---|---|
| `nginx.service` | 80 | reverse proxy + captive portal |
| `ark-kiwix.service` | 8081 | `kiwix-serve --urlRootLocation=/kiwix` |
| `ark-calibre.service` | 8083 | Calibre-Web behind `/books` |
| `ark-api.service` | 8085 | FastAPI backend behind `/api` |
| `ark-boot-guard.service` | — | boots the way `/etc/ark/ark.env`'s `BOOT_MODE` says (`ark-mode boot`; **`hotspot` as shipped**, and **`auto` on this box since 1 Nov 2026** — try the house Wi-Fi, then broadcast), starts any failed portal unit, proves the portal answers |
| `ark-net-watch.timer` (+ `ark-net-watch.service`) | — | every minute after boot: link lost → hotspot, home back + nobody on the hotspot → home; never overrules a pin, never drops a connected reader |
| `ark-zim-downloader.service` | — | background ZIM queue (resumable) |
| `ark-book-import.path` (+ `ark-book-import.service`) | — | fires the book importer whenever something lands in `drop/files` or `calibre-incoming`; `TimeoutStartSec=1800` |
| `ark-arcade-discover.timer` (+ `ark-arcade-discover.service`) | — | 15 min after boot then weekly: pinned first shelf + discovery; installs only when the owner's auto-install switch is on |
| `ark-self-heal.timer` (+ `ark-self-heal.service`) | — | **before `ark-api`, then daily at 04:17 ±20 m**: the power-cut repair (§8.1, log §26). The same unit runs at boot (`Before=ark-api.service`), so "enabled + active" is the timer and the boot run has already happened |
| `ark-media-worker.service` | — | **one** background worker that prepares films (probe, picture, repack/convert), one job at a time. `Nice=10`, idle I/O, `CPUQuota=150%`, `MemoryMax=1G`, `Restart=always`; a missing FFmpeg is a supported state and never blocks the portal |
| `ark-ai.service` | 8089 (loopback) | the helper's engine. **Enabled on purpose but *inactive* when there is no model on the card** — that is a supported state, not a failure. `systemctl is-enabled` must say `enabled`; `is-active` says `inactive` until a `.gguf` lands in `/srv/ark/ai/models/` — and **`active` on this box since 26 Oct 2026** (engine at `/usr/local/bin/llama-server`, 1.5B model, log §31) |
| `ssh.service`, `NetworkManager`, `avahi-daemon` | — | access |
| `dnsmasq` | — | only via NM `ipv4.method shared` (do not enable the standalone unit) |

Verification gate before declaring success: `systemctl is-enabled` **and** `systemctl is-active` must both be `enabled`/`active` for all of the above — **except `ark-ai.service`, whose correct state on a box with no model is `enabled` + `inactive`** (words on the card and an engine that still will not start is the one real failure) — plus `curl` on every endpoint must return 200/302. `pi/steps/90-verify.sh` is the executable form of this gate — run it and keep its output.

---

## 5. FOOLPROOF NON-TECHNICAL UX

The portal is for someone who has never heard the words *Kiwix*, *ZIM*, *Calibre*, *reverse proxy*, or *API*.

* **Banned user-facing words:** `Kiwix`, `ZIM`, `Calibre`, `Calibre-Web`, `OPDS`, `FastAPI`, `nginx`, `systemd`, `SQLite`, `APK`, `port`, `localhost`.
* **Approved user-facing words:** *Offline Library*, *Books*, *Manuals*, *Guides*, *Q&A*, *Repair Guides*, *Message Board*, *File Box*, *Games*, *Play*, *Planting Calendar*, *Download*, *Read in Browser*, *Search*, *Ask the Ark* (never *model*, *LLM*, *AI engine*, *inference*, *token*).
* Every section carries a plain-English **one-line description** plus a **“How to use”** badge.
* Every button label says what happens in human terms (“Tap here to read this in your browser”, not “Open ZIM viewer”).
* Phone/tablet users get the explicit “if this opened inside a *Sign in to Wi-Fi* popup, close it and open `http://ark.lan`” instruction with a 1-tap **Copy Address** button.
* No feature may require a login. Anonymous browse + anonymous download + anonymous read + anonymous upload are on by default.
* UI must be OLED-dark (true `#000000` background), mobile-first, ≥16 px body text, ≥44 px tap targets, works with zero JavaScript for the critical paths (search + links), progressive enhancement only.
* **The owner's pages stay just as plain.** The Community page says *Who can write*, *Messages*, *Shared files*, *Show again*, *Delete for ever* — never “toggle”, “moderate”, “soft delete”, “quarantine”.
* **Any text echoed back from a query string is escaped** (`esc()`), including the File Box's “Not uploaded: …” banner, which carries the refusal sentence from `?err=`.
* `pi/RECOVERY-SHEET.txt` is user-facing and obeys this section: **no** service names, no port numbers, no words that need looking up, nothing that only makes sense on a screen. It is read off paper with a dead box in front of you, so it may name `/srv/ark/backup` and `ark-backup`/`ark-restore` — the commands the owner types — and nothing else technical.

---

## 6. SECURITY / HYGIENE

* No secrets in git. `staging/wifi.env` is `chmod 600` and listed in `.gitignore`.
* **No household *identifiers* in git either — a secret is not the only thing that leaks.** A
  network's **name**, a passphrase, the box's address on somebody's home LAN and a workstation
  account are all identifying, and this tree is public. Paperwork writes them as placeholders —
  `<HOUSEHOLD-SSID>`, `<HOUSEHOLD-PSK>`, `<PROFILE-ID>`, `<OTHER-NETWORK-SSID>`,
  `<OTHER-NETWORK-PSK>`, `<HOME-IP>`, `<CABLE-IP>`, `/Users/<ACCOUNT>` — and the reader's legend
  is the table at the top of `docs/ADMIN.md`. The real values live in exactly three places:
  `staging/wifi.env` (0600, gitignored), the generated `staging/firstrun.sh` (0700, never
  committed), and the box's own `/etc/NetworkManager/system-connections` + `/etc/ark/ark.env`.
  Test fixtures use synthetic names and the documentation address range (`Home Network-5G-1`,
  `192.0.2.x`); a real one is never copied into a test "because it is only a test".
  `scripts/ark-secrets-check.sh` is the executable form: `--staged` runs it as a pre-commit
  hook, and with no argument it reads every tracked file (`tests/test_secrets.py` holds it).
* `firstrun.sh` embeds the Wi-Fi PSK → `chmod 700`, deleted from the Pi after first boot, and removed from `cmdline.txt`.
* `.nmconnection` files must be `chmod 600` / `chown root:root`.
* Never weaken SSH beyond: key auth + password auth (first-boot convenience), root login disabled.
* **Shared files have exactly one writer:** `ark_storage.spool()` in `pi/api/ark_storage.py`. Nothing else may create a file in `/srv/ark/drop/files`. Bytes go to `drop/tmp` as a `0600` temp file (`mkstemp`), are `fsync`ed, `chmod 0644`ed and only then published; `os.link` is what makes a collision impossible rather than unlikely.
* **Never add an nginx location for `/drop/tmp`.** `/drop/` aliases `/srv/ark/drop/files/` and nothing else; a half-written upload must never be reachable over the wire.
* **Game files and saves have exactly one writer each:** `ark_arcade_install.publish_candidate()` creates a file in `/srv/ark/arcade/games`; `player_save_put()` creates one in `/srv/ark/arcade/saves`. `:8445` is the only origin that sets `X-ARK-Player-Origin: 1`; the public `:80` location blanks it, so a visitor cannot claim the player origin by inventing a header.
* `dnsmasq-shared.d` wildcard DNS is only active while the AP profile is up.

---

## 7. CHANGE / VERIFICATION DISCIPLINE

* Before claiming a phase is done: show the command output that proves it. “Implemented” ≠ “works”. `/api/health` is **not** evidence — it only says the backend answered (§8.6); `/api/ready` is the verdict.
* Long operations (image download, flash, apt, ZIM downloads) run in `nohup`/background with logs under `~/Survival-Pi/logs` and are polled, never blocked on.
* Scripts must be idempotent — re-running a phase must not corrupt state.
* `set -Eeuo pipefail` at the top of every bash script; `|| true` only with an explanatory comment.
* Line endings: everything in `pi/` and `staging/` is **LF**. Never CRLF (breaks shebangs on Linux).
* **THREE ATTEMPTS, THEN REPORT (1 Nov 2026, permanent).** One problem, at most **three**
  honest tries with the same class of idea. Progress is evidence moving: a failing test passes, a
  new reason prints, a different error appears. If the third leaves it where it started, **stop**
  and report what it is, the three attempts and what each answered, what is believed vs guessed,
  and the decision (with ranked options) the owner must make; then wait. Never spend the third on
  a step that needs the owner's decision (rewrite, deploy, delete, spend, touch the live box). A
  different error after a real change is a new attempt under a new heading; a long run printing
  new evidence is not a loop.
* Any file executed as a script gets `chmod +x`. Re-verify it **by its own language**: `bash -n` for the bash ones, `python3 -m py_compile` for the Python ones — and **`pi/bin/` is a mixed bag**, so look at the shebang first (`ark-essentials`, `ark-arcade`, `ark-backup`, `ark-restore`, `ark-romdb-build`, `ark-zim-resolve`, `ark-zim-checksum`, `ark-html2pdf`, `ark-self-heal`, `ark-build-ai-brief`, `ark-import-books`, `ark-maps`, `ark_bundle.py` are Python; `ark-mode`, `ark-net-watch`, `ark-tls-cert`, `ark-kiwix-library`, `ark-boot-guard`, `ark-admin-recover`, `ark-import-calibre-library`, `ark-zim-download`, `ark-ai-serve`, `ark-ai-model-fetch` are bash). `bash -n` on a Python `pi/bin/` script is the wrong check, not a defect.

---

## 8. PROJECT STATE & EXTERNAL TRACKING FILES

**Always run `git status --short` and `git log -n 5 --oneline` first.** Never assume the tip from a document.

**Keep this file (`AGENTS.md` / `.clinerules`) under 45 KiB.** Detailed checkpoint logs, test matrices and human checklists live in their own files:

| Tracking File | What it holds & when to read it |
|---|---|
| `docs/IMPLEMENTATION-LOG.md` | **The full record (§1–§41+):** status table (§1), decisions D1–D25 (§5), and dated checkpoint history. Read §1 and the tail of this file before starting new work, and record new checkpoints there rather than appending multi-page prose to `AGENTS.md`. |
| `docs/REQUIREMENTS-MATRIX.md` | **Requirement → code → test evidence** (R1–R12, A–D). Update in the same commit when behaviour or test coverage changes. |
| `docs/HUMAN-TEST-LIST.md` | **The outstanding human/thumb tests (§1–§20):** what is working in code/tests but still unproven by a person on a phone or hardware. |
| `logs/ark_status.json` & `logs/install.done` | **The box's last measured receipt** from `90-verify`. |

| Baseline Fact | State (as of 2 Nov 2026) |
|---|---|
| Released tag | `v1.0.5` = `dd3336c` (never move a tag, never force-push) |
| Active branch | `feature/admin-hardening` (fast-forwarded to `main` at `acf5d29`) |
| Schema version | `SCHEMA_VERSION = 7` (`pi/api/ark_db.py`) — migration 7 adds the film shelf and the space ledger |
| Test suite | `840 collected`; `835 passed, 2 skipped` without the 3 browser map tests (34 files) |
| Live Pi state | Deployed through log §51 at `2e01223`; `ready.ok: true`, `blocking: []`, `mode: home`; 27 collections (97.6 GiB), 1291 books, 67 games, 11 guides, AI active, FFmpeg fitted, 8 films; 3 map packs published; reserve 16.0 GB |
| Owner decisions pending | History rewrite + force-push for pre-`a926296` commits (log §37.6, §38.7) and for the home-LAN address in `1a3f41f` (log §44); rotate GitHub & Pi passwords; licence choice |

### 8.1 What exists, and what each piece owns

| Piece | Owns |
|---|---|
| `pi/api/ark_space.py` | **the only storage policy**: `reserve_for()` = max(7 % of *this* filesystem, 2 GiB), `facts()`, reservations (`reserve`/`touch`/`release`/`reconcile`/`hold`), `account()`. `Refused` lives here; `ark_storage.Refused` aliases it |
| `pi/api/ark_media.py` | the film shelf's model: probe/plan, accept/adopt, captions, jobs, catalogue (`PAGE = 24`), delete, `release_original()`, playable |
| `pi/api/ark_media_jobs.py` | the work itself: bounded `ffmpeg`/`ffprobe` (arrays, deadline), thumbnail choice, copy-or-x264 ≤720p (HW decode, CPU fallback), `output_budget()` + `-fs`, startup reconcile |
| `pi/api/ark_media_web.py` | the public shelf: `build_router(page)`, `ranged()` (206/416/HEAD/`If-Range`), library + player pages, fallback poster SVG, browser-local resume |
| `pi/bin/ark-media-worker` | the one background process (`--once`, `--status`): claims one job, does it, sleeps |
| `pi/bin/ark-space` | the shell door to the policy: `--status [--json]`, `--check --need N` (exit 3 with a sentence) |
| `pi/api/main.py` | public FastAPI app: status/ready/health, search, board, files, ask; mounts the admin and arcade routers; bumps `session_epoch`. Search keeps five report states (`found`/`empty`/`partial`/`silent`/`no-books` — only `empty` may say "nothing matched") and `_spread()` across every collection |
| `pi/api/ark_library.py` | the shelf page's model: `TOPICS` (10 plain-English shelves, one of them films), the catalogue's `topic` column, `name_of()`/`shelves()`; groups the card, never invents a book |
| `pi/bin/ark-zim-checksum` | the published digest for one bundle (`--url`, `--file`): 0 match, 1 different bytes, 2 unreadable, 3 nothing published |
| `pi/api/ark_health.py` | **the only readiness author**: nine sections (`ready`/`reason`/`action`/`content`; **six** blocking — `ask`, `downloads`, `backup` never), progress, storage/card/backup facts, `report()` |
| `pi/bin/ark-net-watch` | **the only network decision after boot**: pure `decide()`, `--dry-run`, `key=value` counters, decisions logged, acts only via `ark-mode` |
| `pi/systemd/ark-net-watch.service` / `.timer` | the clock for the above: `oneshot` every 60 s after a 3-minute boot delay |
| `pi/bin/ark-mode` | the one writer of `/etc/ark/mode.pinned` (`home`/`hotspot`/`auto`) and of `/etc/ark/lan-request` (one boot; `cmd_boot` spends it). `home_problem()` judges the profile; `home_up()` refuses **before** dropping the AP |
| `pi/lib/home-network.sh` | **how the home network is decided** (sourced, no side effects): `ark_home_mode()`, `ark_home_joinable()` (refuses `ark-hotspot` first), `ark_home_choose()` (configured → `home-wifi` → active station, never invented), `ark_home_record()` (temp, read back, `mv`) |
| `pi/api/ark_network.py` | the owner's Network page model: `ark-wifi-add` with the passphrase on **stdin**, `ark-mode request-lan`, saved networks, request state. Writes nothing itself |
| `pi/bin/ark-wifi-add` | the only writer of a NetworkManager client profile: `--ssid NAME [--autoconnect yes\|no] [--check]`, passphrase on **stdin**, `ark-wifi-<slug>-<digest>`, `0600 root:root`, `autoconnect=false` + `retries=0` so **saving never joins** |
| `pi/api/ark_db.py` | one connection policy (`read()`/`tx()`, nested-aware), numbered migrations, audit helper, install marker, `SCHEMA_VERSION` (**7**), `synchronous=FULL`; every connection holds the maintenance gate for its life |
| `pi/api/ark_maintenance.py` | **the one maintenance protocol**: `<db>.maintenance.lock` (shared by every connection, exclusive for repair/restore) and `<db>.repair.lock` (repairs take turns; taken first). Bounded waits; `deferred` ≠ damage |
| `pi/api/ark_security.py` | scrypt (12…1024, reject never truncate), opaque sessions, CSRF, per-account/IP throttling, `DUMMY_HASH` constant-work unknown user |
| `pi/api/ark_admin.py` | `/admin`: login/setup/password/accounts/audit/logout, overview (4 live figures), **13-page `ADMIN_NAV`**, Community, Games, Media, **Maps**, Add files, Addresses, Health, Network |
| `pi/api/ark_ingest.py` | **the batch door's model, and the only creator of a file in `calibre-incoming`**: `classify()`, `BOOK_EXT`/`GAME_EXT`, `spool_book()` (`.part` → `fsync` → `os.link`), `wait_for_library()` |
| `pi/api/ark_doors.py` | **the one list of every address** (33 doors, six groups) + `STEPS` |
| `pi/api/ark_storage.py` | the only write path for shared files: display vs stored name, spool in `drop/tmp`, `os.link` finalise, 2 GiB cap + 512 MiB reserve, temp reconcile, `present_names()` |
| `pi/api/ark_arcade.py` / `ark_arcade_install.py` / `ark_arcade_web.py` | the game shelf, content-addressed saves, the only publish path (`publish_candidate()`), public/player pages, `:8445`, `/arcade/add` |
| `pi/api/ark_romdb.py` / `ark_rommeta.py` / `ark_romdb_build.py` + `pi/bin/ark-romdb-build` | fingerprinting, offline catalogue (`romdb.sqlite`, information never a gate), metadata prefill (`_ROM_KEEP` never overwrites typed words) |
| `pi/api/ark_review.py` / `ark_household.py` / `ark_cards.py` / `ark_qr.py` / `ark_essentials.py` + `pi/bin/ark-essentials` | household layer: 8 emergency topics, 9 answers, 3 wall cards (inline SVG QR), `/essentials.zip` builder (`.partial` → rename) |
| `pi/api/ark_self_heal.py` + `pi/bin/ark-self-heal` | **the only thing that decides what a power cut broke**: clock, JSON/state repair, SQLite `quick_check` under the repair turnstile then the maintenance gate (WAL mandatory), sweep, library healing. A busy gate is `deferred`, not damage; stops/starts `ark-calibre` for its DBs; never deletes a DB |
| `pi/api/ark_atomic.py` | atomic small-file writer (`save_json`/`save_text` = temp → `fsync` → `os.replace`, keeps `.bak`) |
| `pi/bin/ark-fsck-once` + `pi/systemd/ark-fsck-once.service` | withdraws `fsck.mode=force`/`fsck.repair=yes` after one boot; enforces single-line + `root=` |
| `pi/api/ark_ai.py` + `pi/bin/ark-ai-serve` / `ark-ai-model-fetch` / `ark-build-ai-brief` | Ask the Ark: counted brief (never household answers, board, File Box or accounts), prompt budget, leased slot, engine on `127.0.0.1:8089` |
| `pi/bin/ark_bundle.py` / `ark-backup` / `ark-restore` + `pi/steps/95-backup.sh` | backup/restore bundle definition, SQLite `.backup()`, sha256 manifest, `.partial` → rename, adopts bundle ownership, quiesces writers on restore — **including `ark-media-worker.service`**; only previously-running units restart, and only when safe |
| `pi/bin/ark-import-books` + `pi/systemd/ark-book-import.path` / `.service` | book watcher: scans `drop/files` + `calibre-incoming`, imports once per sha256, moves incoming to `done/`, leaves File Box copies |
| `pi/bin/ark-tls-cert` / `ark-admin-recover` | self-signed cert generator/renewer and root-only console recovery wrapper (runs Python as `pi`) |
| `pi/nginx/ark.conf` / `ark-admin.conf` / `ark-players.conf` | `:80` public portal (`/admin` → 404, `/books` management routes → 404 + `limit_except GET`), `:8443` TLS management origin, `:8444` loopback setup listener, `:8445` TLS player origin; body-receiving listeners set `client_body_temp_path /srv/ark/tmp/nginx 1 2` |

### 8.2 Ports and who may reach them

| Listener | Serves | Reachable from |
|---|---|---|
| `:80` | the whole public portal | anyone on Wi-Fi or LAN; **404 for `/admin`** |
| `:8443` | `/admin` + `/assets/ark-admin.css` + `= /essentials.zip`, TLS only | the LAN — sign-in required |
| `:8444` | `/admin` setup only, loopback-bound | only through an SSH tunnel (`ssh -N -L 8444:127.0.0.1:8444 pi@ark.local`) |
| `:8445` | `/arcade/` + `/assets/`, TLS only | the LAN — for player names and saves; `404` for `/admin` |
| `:8446` | `/maps/` (app, catalogue, pack bytes, certificate), TLS only | the LAN — public read; `404` for `/admin`; real `Range` support |
| `:8081` `:8083` `:8085` `:8089` | kiwix / calibre / API / helper engine, proxied by nginx | `127.0.0.1` only |

Because `location /` on `:80` ends in `try_files … /index.html`, an HTTP **200 proves nothing** for `/household`, `/cards`, or `/essentials.zip`: prove `/household` and `/cards` by body sentence and `/essentials.zip` by `Content-Type: application/zip`.

### 8.3 State on the Pi

| Path | What it is |
|---|---|
| `/srv/ark/api/data/ark.db` | SQLite DB (schema **v7**): accounts, sessions, audit, notes, files, games, players/saves, media, `settings` |
| `/srv/ark/api/data/ark.installed` | marker **outside** the DB: wiped DB ⇒ `recovery-required`, never a recreated default |
| `/srv/ark/api/data/bootstrap-password` | `0600`, generated once at first start (`ARK_BOOTSTRAP_PASSWORD` is only for tests) |
| `/srv/ark/drop/files/` & `drop/tmp/` | published shared files (`0644`, `<16 hex>-<label>`) and partial uploads (`upload-*.part`, never served) |
| `/srv/ark/arcade/games/` & `arcade/tmp/` | published ROMs (`0644`) and staging uploads/downloads (`rom-*.part`, `upload-<32hex>.<ext>`, `0600`) |
| `/srv/ark/arcade/saves/<player id>/<game id>/` | named player saves (`0700` dirs, `0600` files, **numbered IDs not names**), bytes in `blobs/<slot-stem>-<sha256>.bin` |
| `/srv/ark/tmp/` + `/srv/ark/tmp/nginx/` | on-card body/upload staging so a 2 GiB upload never touches the 256 MB `/tmp`; never served, never swept |
| `/srv/ark/media/` (`play/`, `thumbs/`, `subs/`, `tmp/`) | playing copies (`m<id>.mp4`), posters, captions, half-written derivatives. A film's **original** is a carrier row in `drop/files` — never a second copy here |
| `/srv/ark/romdb.sqlite` | optional offline game catalogue (`0644`, 56,448 records) |
| `/srv/ark/essentials/ark-essentials.zip` | offline carry-away pack written only by `ark-essentials --build` as `pi` |
| `/srv/ark/ai/` & `models/*.gguf` & `brief.txt` | helper engine, models (newest `.gguf` wins; `Qwen2.5-1.5B`), counted brief |
| `/srv/ark/logs/self-heal.json` + `.log` | power-cut repair receipt (`{status, healed[], problems[]}`) and log |
| `/srv/ark/logs/net-watch.state` & `.log` | **`key=value` counters** (never JSON) and decision log (absent until a decision acts) |
| `/srv/ark/calibre-incoming/.imported.json` & `done/` | sha256 import ledger and retired incoming books |
| `/etc/ark/ark.env` & `mode.pinned` & `lan-request` | operator config (`0644`, written via temp + `install`), optional pin, and one-boot LAN request |
| `/etc/NetworkManager/system-connections/` | `home-wifi.nmconnection` (`autoconnect=yes`) and owner-saved `ark-wifi-<slug>-<digest>.nmconnection` (`0600 root:root`, `autoconnect=false`) |
| `/srv/ark/backup/` & `/srv/ark/RECOVERY-SHEET.txt` | backup bundles (`0700 pi:pi`) and plain-English recovery sheet (also at `/srv/ark/portal/recovery.txt`) |
| `/srv/ark/maps/` (`packs/<id>/<version>/`, `incoming/`, `tmp/`, `web/`, `catalog.json`) | immutable pack versions, import staging, the app, publication state. Packs are **not** in `ark-backup`; a restored card reports none |

### 8.4 Commands that matter

```bash
# tests (Mac, throwaway DBs, never the live library); keep the -o so the summary prints
.venv/bin/python -m pytest -o addopts='--strict-markers' -q

# readiness (never quote /api/health as a verdict)
curl -s -o /dev/null -w '%{http_code}\n' http://ark.lan/api/ready   # 200 ok, 503 = look
curl -s http://ark.lan/api/ready | python3 -m json.tool

# network & Wi-Fi helpers
ark-mode status                              # needs no root
ark-mode boot --dry-run                      # needs no root; touches nothing
sudo ark-mode home | hotspot | auto
sudo ark-mode request-lan --minutes 30 --network ark-wifi-home-<digest> --ssid "Home" [--restart]
printf '%s\n' 'the passphrase' | sudo ark-wifi-add --ssid "Home" [--check]
sudo ark-net-watch --dry-run

# the film shelf and the storage policy
/usr/local/sbin/ark-media-worker --status        # queued/running/failed + tools + room
sudo -u pi /usr/local/sbin/ark-media-worker --once   # do everything waiting, then exit
/usr/local/sbin/ark-space --status               # free, kept back, promised, usable
/usr/local/sbin/ark-space --check --need 3221225472 --path /srv/ark
sudo systemctl status ark-media-worker           # is the worker alive?

# arcade, romdb, essentials, self-heal, AI helper
/usr/local/sbin/ark-arcade --status | --seed | --discover --auto | --list | --match FILE
/usr/local/sbin/ark-arcade --import F --slug S --title T --system gb   # run as pi, NEVER sudo
/usr/local/sbin/ark-romdb-build --status | --self-test
/usr/local/sbin/ark-essentials --status | --list | --verify
sudo -u pi /usr/local/sbin/ark-essentials --build
sudo /usr/local/sbin/ark-self-heal --dry-run | --json
/usr/local/sbin/ark-build-ai-brief --dry-run

# maps — the shelf, from a console as pi (never sudo)
/usr/local/sbin/ark-maps --status | --list | --incoming
/usr/local/sbin/ark-maps --verify liechtenstein 2026-11-02
/usr/local/sbin/ark-maps --publish liechtenstein 2026-11-02
/usr/local/sbin/ark-maps --import /srv/ark/maps/incoming/<file>.zip

# build a region on the workstation (pyosmium; never on the Pi)
.venv/bin/python scripts/ark-map-pack --download URL --name "Region" --out logs/maps/packs --bundle logs/maps/region.zip
.venv/bin/python scripts/ark-map-pack --verify logs/maps/packs/<id>-<version>

# secrets guard — run before every commit (§6)
bash scripts/ark-secrets-check.sh            # all tracked files (git ls-files); rc 0 clean, 1 leak, 2 error
bash scripts/ark-secrets-check.sh --staged   # reads the git INDEX, not disk

# push (never force, never move a tag)
GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND='ssh -o BatchMode=yes -o ConnectTimeout=20' \
  git push origin feature/admin-hardening

# deploy / verify on the Pi (ONLY with the owner's explicit go-ahead; take sqlite3 .backup first!)
# Note: launch background SSH deploys with `< /dev/null` so ssh does not stop on SIGTTIN (stat TN).
sudo bash /srv/ark/src/install-ark.sh --only 90-verify
```

### 8.5 Which test file proves which behaviour

| Behaviour | Test file |
|---|---|
| unique stored names, no overwrite, empty refusal, cap, reserve, temp sweep, missing file row | `tests/test_storage.py` (16) |
| pin / sorted out / hide / restore, both switches, re-auth delete, CSRF, allowlist | `tests/test_board.py` (16) |
| one import per sha256, renamed re-drop, `done/` skipped, File Box link kept | `tests/test_import.py` (7) |
| search deadline, pool reuse, 5 report states, `shorten()`, Atom namespaces, `_spread()` across all collections | `tests/test_search.py` (44) |
| nginx caps, book door route inventory, player box height, library index beside bundles, offline unit ordering, on-card body temp paths | `tests/test_config.py` (30) |
| schema version, migration/upgrade path, Pi scrypt vector | `tests/test_db.py` (7) |
| per-section readiness, progress, `/api/ready` 503, liveness-only `/api/health`, storage breakdown, backup copies | `tests/test_health.py` (26) |
| 16-case watchdog decision table, fake `nmcli`/`iw`, pins, counters written temp-then-`mv`, `home-network.sh` | `tests/test_network.py` (47) |
| owner's Network page end to end, stdin password, one-boot `lan-request`, kept-request banner, non-root `status` | `tests/test_network_page.py` (43) |
| batch upload door (`/admin/upload`), book `.part` spooling, game gate, ledger polling, `ark_doors` completeness | `tests/test_admin_upload.py` (29) |
| owner overview 4 live figures, 11-page `ADMIN_NAV`, `POST /admin/logout` CSRF + idempotence, public footer | `tests/test_admin_dashboard.py` (19) |
| backup/restore round trip, damaged copy refused, `.partial` swept, `--force`, ownership adoption, plain recovery sheet | `tests/test_backup.py` (22) |
| arcade shelf, guest vs named saves, `:8445` origin header, export/import, owner Games page, upload door, tap-to-start | `tests/test_arcade.py` (34) |
| licence as provenance, language table, per-console repos, seed/discover, owner import dedup by sha256 | `tests/test_arcade_install.py` (36) |
| public `/arcade/add` door: visitor adds but never replaces, typed name decides, switch | `tests/test_arcade_add.py` (10) |
| ROM header stripping, fingerprinting, offline catalogue matching, filename fallback | `tests/test_romdb.py` (15) |
| power-cut repair, clock guard, `key=value` counters, WAL drain, `_repair_lock()`, `ark-fsck-once`, fstab/cmdline guards | `tests/test_self_heal.py` (80) |
| the reserve policy, `f_bavail`, reservations across processes, `hold()` release, reconcile, the `ark-space` shell door | `tests/test_space.py` (13) |
| probe/plan (direct/remux/convert), a renamed archive refused, public film waits for the owner, 404 for unpublished, 206/416/HEAD ranges, repack releases the upload, conversion to H.264, poster + fallback, SRT→VTT, recover/cancel/delete/adopt, rename, picture-at, catalogue search/sort/paging | `tests/test_media.py` (55) |
| `synchronous=FULL` on every connection, content-addressed save blobs, atomic save replacement | `tests/test_durability.py` (15) |
| Ask the Ark: brief, prompt budget, route hints, lease slot + disconnect cancel, `public_ask` switch | `tests/test_ai.py` (45) |
| Map pack format v2 (modes, one-way, turn restrictions, buildings), safe zip import, immutable versions, publish/hide/delete, catalogue, 206/416/HEAD ranges, admin gate, app assets | `tests/test_maps.py` (20) |
| **Defining offline journey** in real Chromium: download, disconnect, reload, pan, new search, NEW route, directions, route sheet, GPX, no network; pause/resume/cancel | `tests/test_maps_browser.py` (2) |
| Library shelves from the catalogue's topic column; every book on one shelf; a film shelf says it is films, not that it is unsearchable; `/library` named by the proxy; every imported module copied; published sha-256 checked before publish | `tests/test_library.py` (29) |
| Stage 7 household answers, 8 emergency topics, inline SVG QR cards, essentials pack builder | `tests/test_household.py` (13), `test_household_page.py` (34), `test_qr.py` (11), `test_essentials.py` (24) |
| claim door, forced password change, throttling, sessions, TLS probe verdicts, secrets guard | `tests/test_auth.py` (19), `test_accounts.py` (14), `test_tls_probe.py` (16), `test_secrets.py` (12), `test_public.py` |

### 8.6 Deliberate gaps — do not describe any of these as built

Maps: street-address search (no address index), turn restrictions whose `via` is a way or time-conditional, live closures/traffic, elevation, background GPS/voice navigation, server-side push of updates to phones, automatic deletion of regions, and packs in `ark-backup`; **no real iPhone/Android proof yet** (desktop Chromium only). Per-person quotas (including media); a film trash/undo; embedded bitmap subtitles; hardware-accelerated encoding; server-side watch history (resume is browser-local); films in a backup; quarantine/approval queue; trash; pagination on the Community page (it shows `LIMIT 50` and says so; the public board paginates at `BOARD_PAGE = 20`); pause/resume buttons on the owner page; a backup timer or backup button on the owner page; player password recovery, per-player save quota, save trash, or owner reset/delete for a player name; a "Check now" button for games; SNES/Genesis online catalogue entries (hand-carried only). **Never invent a control that does not exist.**

### 8.7 Essential gotchas (distilled from §1–§41)

* **Batching & shell:** Parallel batches race state-changers against observers (amend + push in one batch pushes the pre-amend hash): state-changers first. Background `ssh` needs `< /dev/null` (SIGTTIN). Commit messages with apostrophes go in a file (`git commit -F`). macOS has no `timeout`; `bash -n` cannot see a doubled quote.
* **Python & SQLite on Mac/Pi:** `pi/` floor is Python 3.9 (no backslashes inside f-string `{}`); macOS 3.9 uses LibreSSL (`cryptography` scrypt fallback). SQLite `ADD COLUMN … NOT NULL` needs `DEFAULT`; old `files` rows have `stored=''` → `_stored_name()`. `ark_db.tx()` is nested-aware. Back up a live WAL DB with `sqlite3 .backup`, never `cp`.
* **Two spoolers, two shapes:** `ark_storage.spool(read, ...)` and `ark_ingest.spool_book(read, ...)` take a **callable** (`file.file.read`); `ark_arcade_install.spool_upload(stream, ...)` takes a **file object**.
* **Probes & network:** use `ark_tls_code <host:port> <path>` from `pi/lib/common.sh` so SNI is sent (`https://127.0.0.1:8443` is rejected with `000`). With Ethernet in, `ark.local` resolves to `eth0` (`<CABLE-IP>`); prove `wlan0` via `<HOME-IP>` or `--resolve ark.local:8443:<HOME-IP>`. A 200 is not proof a page works; a probe testing admin doors must disable redirect (`303`).
* **Kiwix & Search:** `ark-kiwix-library` builds `library.xml` **inside** `$ZIM_DIR` (`mktemp "$ZIM_DIR/.library-XXXXXX"`). A collection's search handle is its `.zim` stem (`…_en_all_2026-10`), never UUID/`name`; endpoint `/kiwix/search`. `_spread()` takes depth 0 across all collections before depth 1 (`LIBRARY_HITS >= MAX_COLLECTIONS`).
* **Arcade:** Never `EJS_startOnLoaded: true` (silent white WebGL; the tap is the required gesture). Size `#arcade-frame`, not `#arcade-stage`. File routes support `GET` + `HEAD`. Run `ark-arcade` as `pi`, never sudo.
* **Power-cut & self-heal:** `15-resilience` re-arms `fsck.mode=force` (withdrawn by `ark-fsck-once`); do not re-run the phase just to copy `ark_self_heal.py`. `cmdline.txt` stays one line with `root=`. `net-watch.state` is `key=value`, never JSON. A repair takes `<name>.repair.lock` then the gate and drains `-wal` (mandatory) + `-shm` (best effort).
* **Media & space:** the reserve is `ark_space`'s alone — no second free-space constant. Admission is `free − reserve − (promised − written)`; use `f_bavail`, never `f_bfree`. A film's original is its carrier row (`hidden=1`) until the play copy is published; then the upload is released. Publication is `media.state`, checked by every public route. Ranges stay 206/416; never buffer `/media/`. `ffmpeg` uses arrays + a deadline; derivatives are written in `media/tmp` and renamed. `PRAGMA journal_mode=WAL` is a write: ask before setting it, or a reader stalls.
* **Maps & service workers:** a certificate error is **not** a secure context for service workers even after a click-through. The device must trust the Ark certificate (SPKI pin in the test). Serve `sw.js` with `Service-Worker-Allowed: /maps/` and an `ETag`; re-registering without one can leave a second worker waiting. A pack version is immutable; import verifies every byte before `os.replace`; `/maps/api/` is never cached; `:8446` never proxies `/admin`.
* **Maintenance:** `ark_db` connections hold `<db>.maintenance.lock` shared for their life; a repair/restore takes `<db>.repair.lock` then the exclusive gate. Never unlink either. A gate that cannot be had is `deferred`, not corruption; `ark-restore` stops `ark-media-worker.service` too.
* **Verifier counts:** `install-ark.sh --only 90-verify` printed **81** `✓` on 3 Nov 2026 (maps included); quote the command, never gitignored `logs/*.log`.

### 8.8 Standing rules for every stage

1. Test first, then code, then `pytest` green, then a checkpoint in `docs/IMPLEMENTATION-LOG.md`.
2. The public reading path must keep working at every commit.
3. No deploy, reboot, `pkill`, or service restart on the live box without explicit permission.
4. Docs change in the same commit as the behaviour (`README.md`, `docs/ADMIN.md`, `docs/USING-THE-ARK.md`, `docs/REBUILD-ME-ARK.txt`, `docs/REBUILD-FROM-BACKUP.txt`, `pi/RECOVERY-SHEET.txt`, `docs/REQUIREMENTS-MATRIX.md`).
5. Shared files get written by `ark_storage.spool()` and by nothing else (§6).
6. New schema steps are a numbered migration in `ark_db.py`, bump `SCHEMA_VERSION`, never rewrite v1, and keep old rows readable.
7. Public switches (`public_board_post`, `public_uploads`, `public_arcade_upload`, `public_household`, `public_ask`) are settings where absent or `1` means **open**, and closing one leaves existing content readable.
8. Readiness has one author (`pi/api/ark_health.py`), cached and cheap, and no reader's page may wait on a service that is down.
9. When a rendered value disappears, document it in the same commit.
10. The network after boot is decided in one place (`pi/bin/ark-net-watch`), acting only through `ark-mode`, never overruling a pin, and never dropping a connected reader (`-1` stations = occupied).
11. Unknown measurements follow the `None` rule: render as *"not measured yet"*, never as `0`.
12. `ark-backup` only reads; `ark-restore` is the one deliberate writer of records and `drop/files` after verifying every byte. No backup timer without an explicit yes.
13. Arcade write paths are closed sets: `ark_storage.spool()` (`drop/files`), `publish_candidate()` (`games/`), `player_save_put()` (`saves/`). `:8445` sets `X-ARK-Player-Origin: 1`; `:80` blanks it.
14. ROM recognition (`ark_romdb`/`ark_rommeta`) is information, never a gate, and never overwrites a word a person typed.
15. Wall cards are inline SVG (`ark_qr.py`), `/household` and `/cards` are proved by body sentence, `/essentials.zip` by content type, and packs are written only by `ark-essentials --build` as `pi`.
16. `ark_self_heal` may never lose an intact file: it sets aside (never deletes), replaces a DB only with a copy that passes `quick_check` under `_repair_lock()`, and exits non-zero only on `problems`.
17. Small files are written atomically (`ark_atomic.write_json()`/`write_text()` with `.bak`). A box with no AI model is a supported state (`enabled` + `inactive`, never blocking readiness).
18. `ark_ai.py` sends only a counted brief (never household answers, board, File Box, or accounts), holds its single slot via a lease (`begin()`/`end()`/`cancel()`), and cancels the engine stream immediately on client disconnect.
19. `ark-wifi-add` writes client profiles (`autoconnect=false`, passphrase on stdin only) and `ark-mode` writes/consumes `/etc/ark/lan-request` (one boot only, fallback to broadcast if unreachable).
20. **Maps has one format, one shelf, one storage policy:** `ark_maps.write_pack()` is the only writer of a pack; `ark_map_store` is the only writer of the shelf; `ark_space.hold()` reserves an import; a version is never rewritten; only a fully verified, owner-published, file-present pack is served; deleting on the Ark never claims to reach a phone.
21. **The offline promise is verified, not asserted:** `Ready offline` requires the pack verified *and* the page controlled by the worker; the defining browser test disconnects both Ark and internet and calculates a **new** route. A real phone remains a human test (`docs/HUMAN-TEST-LIST.md` §22).
22. **Keep admin out of the maps origin and cache:** `:8446` serves `/maps/` only, the worker skips `/maps/api/`, and no admin page, session or credential may enter a public cache.
23. **Keep `AGENTS.md` and `.clinerules` lean (<45 KiB):** historical narratives and deploy logs go in `docs/IMPLEMENTATION-LOG.md`, never in the standing rules file.
24. **The catalogue files the library, and a digest gates a download:** `topic` (col 7) is the only filing; `_ftindex:no` says nothing about search (iFixit is `no` and answers 22,722 hits — measured 3 Nov 2026), so every collection is still asked; `ark-zim-checksum` must agree before a `.part` is named.
25. A database is replaced only under the repair turnstile and the exclusive gate; start only units that were running, and only when the outcome is safe.
