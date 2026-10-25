# Project ARK

A Raspberry Pi 4 that carries a library. It broadcasts its own open Wi-Fi,
serves a portal at **http://ark.lan**, and works with no internet at all.

Everything a visitor touches is plain HTML — no accounts, no logins, no
JavaScript required. The one sign-in on the box is the owner's door further
down, and the portal address does not serve it at all.

---

## What is on the box

| Door | What it is |
|---|---|
| `/` | the portal: one search box, four big cards |
| `/search?q=…` | one search that covers the encyclopedias, the manuals and the books |
| `/kiwix/` | the offline library (encyclopedias, medical, repair, food, water) |
| `/books/` | the book shelf (EPUB/PDF, read in the browser or download) |
| `/vault/` | survival manuals + repair guides, each with a printable PDF |
| `/board` | a message board anyone can post to (the owner can pin, sort out, hide) |
| `/files` | a file box anyone can drop things into (2 GB per file, links never overwrite) |
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
the build order, the 26 traps that cost the most time, and the verification gate.
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
`65-admin` `70-network` `90-verify`.

---

## How the Pi finds a network

`ark-mode` decides, and `ark-boot-guard` runs it at every boot:

```bash
sudo ark-mode status       # mode, address, portal answer, internet
sudo ark-mode auto         # try the home network, else broadcast the hotspot
sudo ark-mode hotspot      # broadcast "The-Ark-Offline" (10.42.0.1)
sudo ark-mode home         # back to the home network
sudo ark-mode test 180     # try the hotspot for 3 minutes, then go back
```

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

* `https://ark.lan:8443/admin` — sign in from a phone or laptop on the network.
* On a box that has never been claimed, that address stays shut on purpose.
  The first claim happens through a private tunnel, so the well-known
  temporary password cannot be used by the first stranger on the hotspot:

  ```bash
  ssh -N -L 8444:127.0.0.1:8444 pi@ark.local
  # then open https://localhost:8444/admin
  # user: admin    temporary password: ChangeMe-ARK!
  ```

The first sign-in asks for your own password (12+ characters, passphrases
welcome) and the temporary one stops working immediately. After that you can
add other administrators, reset or switch off their accounts, read the audit
history, and use the **Community** page — two switches that pause new notes and
new uploads, plus pin / sorted-out / hide for messages, hide for files, and a
delete that asks for your own password again. Losing every password is
recoverable from the console only:

```bash
sudo ark-admin-recover --list
sudo ark-admin-recover --username admin
```

The certificate is generated on the box (`sudo ark-tls-cert --show` prints its
SHA-256 fingerprint). Compare that fingerprint with the browser before you
trust it — see `docs/ADMIN.md` for the exact steps, the session rules and what
is deliberately *not* in this release yet.

Tests (no Pi needed, throwaway databases only):

```bash
python3 -m venv .venv && .venv/bin/pip install -r tests/requirements.txt
.venv/bin/python -m pytest
```

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

---

## How this was built

Most of this repo was written by **Cline**, an AI coding agent, working on a
real Pi 4B over several days — one page at a time, with every claim checked
against the running box before it went in. The 26 traps in
[`docs/REBUILD-ME-ARK.txt`](docs/REBUILD-ME-ARK.txt) are all scars from things
that actually broke here: a mirror that froze at 66,881 bytes, a downloader that
wrote full-size files full of holes, a Calibre database that answered 500 for
every request.

The original brief it worked from is
[`docs/BOOTSTRAP-BRIEF-cline.txt`](docs/BOOTSTRAP-BRIEF-cline.txt) — deliberately
model-agnostic, so a different agent or a patient human can follow it too.
One-page backup/rebuild sheet:
[`docs/REBUILD-FROM-BACKUP.txt`](docs/REBUILD-FROM-BACKUP.txt).
