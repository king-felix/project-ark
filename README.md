# Project ARK

A Raspberry Pi 4 that carries a library. It broadcasts its own open Wi-Fi,
serves a portal at **http://ark.lan**, and works with no internet at all.

Everything a visitor touches except the game player is plain HTML — no accounts,
no logins, no JavaScript required to read. A name is optional and exists only to
keep game saves; the one sign-in that changes the box is the owner's door further
down, and the portal address does not serve it at all.

---

## What is on the box

| Door | What it is |
|---|---|
| `/` | the portal: one search box, big plain-English cards |
| `/search?q=…` | one search that covers the encyclopedias, the manuals and the books |
| `/kiwix/` | the offline library (encyclopedias, medical, repair, food, water) |
| `/books/` | the book shelf (EPUB/PDF, read in the browser or download) |
| `/vault/` | survival manuals + repair guides, each with a printable PDF |
| `/board` | a message board anyone can post to (the owner can pin, sort out, hide) |
| `/files` | a file box anyone can drop things into (2 GB per file, links never overwrite) |
| `/arcade/` | small openly licensed games, played in the browser; guests need nothing |
| `/help.html` | how it works, in plain words |

Addresses: `http://ark.lan` · `http://ark.local` · `http://10.42.0.1`

Everything in that table is public and password-free. The owner's door — the
sign-in for whoever looks after the box — is a separate address entirely (see
[The owner's door](#the-owners-door-administrator-access)); it is not a public
page and it is not a feature a visitor ever has to deal with.

**A plain-English guide for whoever actually uses the box** — every feature, the
drag-a-book-in workflow, the Windows library migration, health checks and a
troubleshooting table — lives in [`docs/USING-THE-ARK.md`](docs/USING-THE-ARK.md).
Keep a copy on a phone or taped inside the lid.

**Building another one, or handing this to someone else?** Start with
[`docs/REBUILD-ME-ARK.txt`](docs/REBUILD-ME-ARK.txt): the complete blueprint,
the build order, the 30 traps that cost the most time, and the verification gate.
It is written so an assistant with no memory of this project can rebuild the box
from an empty card.

---

## Building it (in order, on the Mac)

```bash
# 0. one-time: fill in the home Wi-Fi details the Pi should join on first boot
$EDITOR staging/wifi.env          # SSID, password, country
bash scripts/make-firstrun.sh     # renders staging/firstrun.sh (chmod 700)

# 1. flash the card (verifies the disk is really the SD card first)
bash scripts/01-flash.sh

# 2. put the card in the Pi, power it on, then wait for it to appear
bash scripts/02-wait-for-pi.sh

# 3. push the project to the Pi and run the installer there
bash scripts/03-provision.sh
```

`03-provision.sh` copies `pi/` to `/srv/ark/src`, starts `install-ark.sh` in
the background on the Pi (a dropped Wi-Fi link cannot kill it), follows the
log live, and pulls back `logs/ark_status.json` at the end.

Re-run one phase on the Pi:

```bash
ssh pi@ark.local 'sudo bash /srv/ark/src/install-ark.sh --only 60-portal'
# or from the Mac
bash scripts/03-provision.sh --only 60-portal
```

Phases: `10-base` `20-storage` `30-calibre` `40-vault` `50-kiwix` `60-portal`
`65-admin` `70-network` `95-backup` `90-verify`.

---

## How the Pi finds a network

A cold boot comes up **broadcasting** `The-Ark-Offline`: `ark-mode boot` reads
`BOOT_MODE` from `/etc/ark/ark.env` (shipped as `hotspot`), so a box in a cupboard
with no router and nobody standing next to it is still reachable. `ark-net-watch`
asks the same question every minute afterwards, so a network that changes under a
running box is survived without a reboot:

```bash
sudo ark-mode status       # mode, address, portal answer, internet, boot choice, who chose
ark-mode boot --dry-run    # what a cold boot would do — no root, no radio
sudo ark-mode auto         # try the home network, else broadcast the hotspot (no pin)
sudo ark-mode hotspot      # broadcast "The-Ark-Offline" (10.42.0.1), and keep it
sudo ark-mode home         # back to the home network, and keep it
sudo ark-mode test 180     # try the hotspot for 3 minutes, then go back
```

Set `BOOT_MODE="auto"` in `/etc/ark/ark.env` for the older behaviour (try the home
network on the way up, broadcast only if it is absent).

`home` and `hotspot` are a person's answer, so they are pinned in
`/etc/ark/mode.pinned` and the watchdog will not undo them; `auto` forgets the
pin. The watchdog's own rescue calls `ark-mode hotspot --auto`, which leaves no
pin — that is how "the box did this" stays distinguishable from "the owner said
so".

What the watchdog will and will not do:

* two lost checks in a row (about two minutes) before it raises the hotspot;
* never takes the hotspot away while a device is connected to it — and a station
  count it could not read is treated as *somebody may be there*;
* returns home only when the network is in range **and** nobody is on the hotspot,
  after three quiet checks, and waits 30 minutes before trying again after a
  failed return (an access point that flaps is worse than one that stayed up).

Its record is `/srv/ark/logs/net-watch.log` (one line per decision that acted; a
quiet check writes nothing) and its counters are in `net-watch.state` beside it.
`/usr/local/bin/ark-net-watch --dry-run` prints what it sees and would do, and
changes nothing.

The hotspot is open on purpose: a stranger with a dead phone must be able to
read the library. While it is broadcasting, every hostname resolves to the
portal, so phones that probe for connectivity open the Ark by themselves.

Settings live in `/etc/ark/ark.env` (`HOTSPOT_SSID`, `HOTSPOT_CHANNEL`,
`HOME_WIFI_CONNECTION`, `ZIM_TIERS`, …).

---

## The owner's door (administrator access)

The public portal has no login and needs none. Everything that *changes* the
box lives behind one administrator account, served only on the HTTPS
management listener:

* `https://ark.local:8443/admin` — sign in from a phone or laptop on the network.
  `ark.local` is the name that resolves wherever the box sits on a home network
  (it is answered by the box's own mDNS service). `https://ark.lan:8443/admin`
  works too whenever the box is broadcasting **The-Ark-Offline**, because then the
  box itself answers every name. The certificate covers both names, so neither
  produces a warning once its fingerprint is trusted.
* On a box that has never been claimed, that address stays shut on purpose.
  The first claim happens through a private tunnel, so the well-known
  temporary password cannot be used by the first stranger on the hotspot:

  ```bash
  ssh -N -L 8444:127.0.0.1:8444 pi@ark.local
  # then open https://localhost:8444/admin
  # user: admin
  # temporary password: ssh pi@ark.local 'cat /srv/ark/api/data/bootstrap-password'
  ```

The first sign-in asks for your own password (12+ characters, passphrases
welcome) and the temporary one stops working immediately. After that you can
add other administrators, reset or switch off their accounts, read the audit
history, and use the **Community** page — two switches that pause new notes and
new uploads, plus pin / sorted-out / hide for messages, hide for files, and a
delete that asks for your own password again. **Games** is where the shelf is
managed: hide or show a game, see every licence and suggestion, allow player
names, and choose whether newly found games are added without asking. The
player's sign-in is a separate HTTPS origin (`https://ark.lan:8445/arcade/`) and
is not an administrator account. **What is working** is the page
to open when something feels off: every part of the box, whether it answered
just now, the plain reason it did not, and the one next thing to try. It only
looks — it never starts, stops or deletes anything. Losing every password is
recoverable from the console only:

```bash
sudo ark-admin-recover --list
sudo ark-admin-recover --username admin
```

The certificate is generated on the box (`sudo ark-tls-cert --show` prints its
SHA-256 fingerprint). The same certificate serves the player's secure origin
(`:8445`), so the same fingerprint comparison applies. Compare that fingerprint
with the browser before you trust it — see `docs/ADMIN.md` for the exact steps,
the session rules and what is deliberately *not* in this release yet.

Tests (no Pi needed, throwaway databases only; the current suite is 285 tests):

```bash
python3 -m venv .venv && .venv/bin/pip install -r tests/requirements.txt
.venv/bin/python -m pytest
```

The per-requirement verdict — what is done, what is only partial, and the test
that proves each row — is `docs/REQUIREMENTS-MATRIX.md`.

---

## ARK Arcade

`http://ark.lan/arcade/` is a shelf of small homebrew games that run on the box
with the internet unplugged. A guest taps a game and plays; nothing is asked.
A name is optional and only keeps game saves: it lives in its own tables, with
its own cookie, on its own HTTPS origin (`https://ark.lan:8445/arcade/`). It is
never an administrator account and no page in the arcade can reach `/admin`.

```bash
# run as pi, no sudo: /usr/local/sbin is not on pi's PATH, hence the full path
/usr/local/sbin/ark-arcade --status                # what is on the shelf, and who has a name
/usr/local/sbin/ark-arcade --seed                  # install the pinned first shelf (idempotent)
/usr/local/sbin/ark-arcade --seed --from-dir DIR   # ...from files carried over, no internet needed
/usr/local/sbin/ark-arcade --discover --auto       # what the weekly timer runs
/usr/local/sbin/ark-arcade --list                  # suggestions and why each was refused
/usr/local/sbin/ark-arcade --install <id>          # add one, after looking at it
/usr/local/sbin/ark-arcade --match FILE            # what the box makes of one file (writes nothing)
/usr/local/sbin/ark-arcade --import F --slug S --title T --system gb \
  --description "one line a reader sees" --notices "who to thank"   # a game you supply
```

**The box can name a game it has never seen.** `pi/api/ark_romdb.py` reads a game file
itself — the header and the sizes the console needs, then a fingerprint of the bytes with
any cataloguing header stripped — and looks it up in an **offline catalogue** of 56,448
games (`/srv/ark/romdb.sqlite`, 25 MB, built once by `pi/bin/ark-romdb-build` from the
libretro database and copied onto the card; 27,723 of them are on a console this box can
play). A match pre-fills the title, the console, the year, who made it and what kind of
game it is, so nobody types a title that is already in the file. No internet, no service,
no network call — an absent catalogue is simply "nobody has listed this file", and the
file name is used instead. The console spelling and its own list of titles are recorded as
provenance beside the game, and nothing is ever refused for what the catalogue says.

**Two ways to add a game, and both run the same gate.** On the owner's **Games** page
(`https://ark.lan:8443/admin/arcade`) there is **A game you already have**: pick the
file, press **Look at this file**, and the next screen says what the box recognised it as
and shows the words it would put on the card — *Add this game* publishes it, *Throw it
away* deletes the waiting copy. Nothing is published on the strength of a form alone. A
game the owner supplies goes through the same
gate as a fetched one: bare ROM, its own fingerprints recorded (SHA-1, MD5, CRC-32 and
SHA-256), the file checked to be shaped like the console it claims (a Game Boy file by its
header and checksum, a NES file by its magic bytes, any other console by its extension and a
non-empty, sane size), temp-written then published, and the shelf left untouched if anything
fails. Giving it the same file again answers *"already on the shelf"*; the same game under a
second name is refused with the name it already has; a changed file under the same name
becomes revision 2. `docs/ADMIN.md` §10 walks through both ways.

The weekly `ark-arcade-discover.timer` records what the curated community
catalogues offer. Installation is off unless the owner switches it on in
**Games**; even then, the download must come from one of the allowlisted
catalogues, be under 64 MB, match its SHA-256 and look like the console it
claims. What the source says about a licence is recorded on the game's card —
information, not a gate (the owner's decision, 25 Oct 2026). A failure changes
nothing. The player is EmulatorJS 4.2.3, vendored under
`pi/arcade/emulatorjs/`; the games page has no CDN, no external font and no
telemetry.

Guest saves stay in that browser. A named player can keep three server saves per
game, export all of them as one zip, and put them back on another ARK. The save
API is reachable only through the TLS player origin, never the public portal or
the administrator origin. The games themselves are content and are not in a
backup bundle; a named player's saves are.

---

## The offline library

`50-kiwix.sh` writes a queue from `pi/kiwix/catalog.tsv` and
`ark-zim-download` fetches one collection at a time, resuming after any
interruption. Profiles: `mini` (the essentials, roughly 1 GiB), `core`
(roughly 5 GiB) and `full` (everything that fits, leaving 12 GiB free) — the
sizes come from the catalog, and `ark-zim-resolve` prints the real total for
the profile you choose.

```bash
sudo ark-zim-resolve --profile core   # rewrite the queue
sudo systemctl status ark-zim-downloader
sudo touch /etc/ark/zim-pause         # pause politely (the downloader checks this path)
```

The library page picks up new collections without a restart.

Only one downloader may run at a time (it holds
`/srv/ark/kiwix/state/downloader.lock`: two copies corrupt each other's `.part`
files). Files are pulled **single-stream** — `wget -c`, then `curl -C -` — because
the mirrors behind the redirector freeze when asked for several connections; a
`.part` stuck at the same byte count forever is the signature.

A `.zim` that is present but truncated, or full-sized but not readable, is *not*
counted as installed: the downloader compares on-disk size with the catalogue,
compares allocated blocks (`du -B1`) with the claimed size to catch hole-filled
files, and only trusts a bundle once `library.xml` lists it. Anything suspect is
moved to `/srv/ark/quarantine` and fetched again. While a bundle is in flight the
home strip shows a live `n% of <name> (x GB of y GB)`.

### Book library notes

`30-calibre.sh` needs a **real** Calibre database in `/srv/ark/calibre-library`.
The book shelf answers HTTP 500 for every request if `metadata.db` is missing or
is the 0-byte file that `sqlite3 /path/metadata.db 'SELECT …'` silently creates,
so the step validates the file and, when it is unusable, installs the `calibre`
package (`--no-install-recommends`, ~60 MB) and builds a genuine empty library
with `calibre-debug`. Books dropped in the File Box are added by
`ark-import-books`, which prefers `calibredb` and falls back to the library's
own upload endpoint. The File Box copy stays where it is (so the download link
somebody was given keeps working), `calibre-incoming/done` is never rescanned,
and the ledger in `calibre-incoming/.imported.json` is keyed by sha256, so the
same book dropped twice under two names is imported once.

**What the File Box lists.** `http://ark.lan/files` offers only the files that are
really in the shared folder. A record whose file has gone — a records-only restore,
or a folder tidied by hand — is not shown and is not counted, because a link that
answers 404 is worse than a file that is missing. The record itself stays until the
owner deletes it on **Community → Shared files**, where the audit line reads *"the
file was already gone"*; nothing tidies itself.

### Getting books onto it

**A few books:** drop them in the File Box (`http://ark.lan/files`) — a watcher
(`ark-book-import.path`) notices the new file and adds it to the shelf within
seconds. Nothing to run.

**A whole Calibre library, metadata intact:** copy the folder over and adopt it.
On Windows (OpenSSH ships with it):

```powershell
scp -r "$env:USERPROFILE\Calibre Library" pi@ark.local:/srv/ark/imported-library
```

```bash
sudo ark-import-calibre-library /srv/ark/imported-library
```

Titles, authors, series, tags and covers come across because they live in
`metadata.db`. The helper refuses a database written by a newer Calibre than the
Pi's, moves the previous library to `/srv/ark/calibre-library.bak-<date>` (never
deletes it), and puts it back if the shelf does not answer afterwards.

---

## Checking it

```bash
ssh pi@ark.local 'sudo bash /srv/ark/src/install-ark.sh --only 90-verify'
cat logs/ark_status.json
```

Every service must be `enabled` **and** `active`, every door must answer
200/302, and 5 GiB must stay free. The gate also proves the owner's door is
where it belongs: `/admin` must be **404** on the public address, both
management listeners (`:8443`, and `:8444` while the box is unclaimed) must
answer, the recovery command must be installed, and it reports how many
administrators still hold a temporary password — without ever printing one.
The receipt is machine-readable so the Mac never has to guess.

**Is it actually usable?** Two answers, one for people and one for scripts:

* `http://ark.lan/api/ready` — **200** only when every part a reader came for
  works, **503** otherwise. The body is the whole picture either way: one entry
  per part (`library`, `books`, `manuals`, `messages`, `files`, `storage`,
  `backup`, `downloads`) with `ready`, `content` (is there anything on the card yet?),
  `reason` in plain words and `action` (empty when nothing is wrong).
  `downloads` never blocks: work in progress is not a fault.
* `https://ark.lan:8443/admin/health` — the same answer as a page for the owner,
  with the reason and the next step under every part that is not ready.
* `http://ark.lan/api/health` is only *"the backend answered"* and says so in
  its own body. It is not a verdict; `/api/ready` is.

`/api/status` still carries the counts, and now carries the same per-section
`ready`/`reason` pair plus real download bytes: `bytes_on_disk`,
`current_bytes`, `current_percent`, `overall_percent`, `remaining_bytes`,
`rate_bps` (measured from the disk over time, never estimated) and
`eta_seconds`, which is `null` whenever the box has not measured a rate yet.

---

## Keeping a copy

Everything the box cannot rebuild — the messages, the shared files, the accounts,
the Community and Games switches, the book-import list and every named player's
game saves — lives on one SD card. Two commands copy it and put it back:

```bash
sudo ark-backup --to /srv/ark/backup     # one dated folder: records + shared files + fingerprints
sudo ark-backup --list                   # what is there: date, size, what each holds
sudo ark-backup --verify /srv/ark/backup/ark-backup-<date>-<time>   # re-read every byte
sudo ark-restore --list                                       # what could be put back
sudo ark-restore --from /srv/ark/backup/ark-backup-<date>-<time>
```

A copy is built as `<name>.partial` and only becomes `<name>` by a rename, so an
interrupted run can never pass for a finished backup; the database is taken with
SQLite's own backup call, never a file copy (the live file is WAL). A restore
proves every byte first, refuses to overwrite an occupied box without `--force`,
never replaces a shared file that is already there (and keeps a game save already
on the box rather than replacing it), and refuses a manifest that
points outside the box. The offline library, the book shelf and the game files
themselves stay out of a copy on purpose — they are content, fetched or imported
again; named players' saves are state and do travel.

**A copy on the box is not insurance:** copy `/srv/ark/backup/ark-backup-*` to a
stick or a computer. The page for the day the card does not boot is
[`docs/RECOVERY-SHEET.txt`](docs/RECOVERY-SHEET.txt) (installed on the box as
`/srv/ark/RECOVERY-SHEET.txt`, and readable at `http://ark.lan/recovery.txt`
while the box still runs) — a phone, a card reader and a working card is enough.
There is no timer on purpose: a nightly copy that quietly fills the card the box
reads from is a defect, not a feature.

Each finished copy is handed to the owner of the folder it was written into, so
the box's own pages can read it back — **What is working → Latest copy** shows the
date and what it holds. A copy the pages are not allowed to open is reported as
exactly that, never as "no copy", and re-running phase `95-backup` hands an older
one over.

---

## How this was built

Most of this repo was written by **Cline**, an AI coding agent, working on a
real Pi 4B over several days — one page at a time, with every claim checked
against the running box before it went in. The 30 traps in
[`docs/REBUILD-ME-ARK.txt`](docs/REBUILD-ME-ARK.txt) are all scars from things
that actually broke here: a mirror that froze at 66,881 bytes, a downloader that
wrote full-size files full of holes, a Calibre database that answered 500 for
every request.

The original brief it worked from is
[`docs/BOOTSTRAP-BRIEF-cline.txt`](docs/BOOTSTRAP-BRIEF-cline.txt) — deliberately
model-agnostic, so a different agent or a patient human can follow it too.
One-page backup/rebuild sheet:
[`docs/REBUILD-FROM-BACKUP.txt`](docs/REBUILD-FROM-BACKUP.txt).
