# Project ARK

A Raspberry Pi 4 that carries a library. It broadcasts its own open Wi-Fi,
serves a portal at **http://ark.lan**, and works with no internet at all.

Everything a visitor touches is plain HTML — no accounts, no logins, no
JavaScript required.

---

## What is on the box

| Door | What it is |
|---|---|
| `/` | the portal: one search box, four big cards |
| `/search?q=…` | one search that covers the encyclopedias, the manuals and the books |
| `/kiwix/` | the offline library (encyclopedias, medical, repair, food, water) |
| `/books/` | the book shelf (EPUB/PDF, read in the browser or download) |
| `/vault/` | survival manuals + repair guides, each with a printable PDF |
| `/board` | a message board anyone can post to |
| `/files` | a file box anyone can drop things into |
| `/help.html` | how it works, in plain words |

Addresses: `http://ark.lan` · `http://ark.local` · `http://10.42.0.1`

**A plain-English guide for whoever actually uses the box** — every feature, the
drag-a-book-in workflow, the Windows library migration, health checks and a
troubleshooting table — lives in [`docs/USING-THE-ARK.md`](docs/USING-THE-ARK.md).
Keep a copy on a phone or taped inside the lid.

**Building another one, or handing this to someone else?** Start with
[`docs/REBUILD-ME-ARK.txt`](docs/REBUILD-ME-ARK.txt): the complete blueprint,
the build order, the 25 traps that cost the most time, and the verification gate.
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
`70-network` `90-verify`.

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

## The offline library

`50-kiwix.sh` writes a queue from `pi/kiwix/catalog.tsv` and
`ark-zim-download` fetches one collection at a time, resuming after any
interruption. Profiles: `min` (~1 GiB), `core` (~5 GiB), `full` (everything
that fits, leaving 12 GiB free).

```bash
sudo ark-zim-resolve --profile core   # rewrite the queue
sudo systemctl status ark-zim-downloader
touch /srv/ark/zim-pause              # pause politely
```

The library page picks up new collections without a restart.

Only one downloader may run at a time (it holds
`/srv/ark/kiwix/state/downloader.lock`: two copies corrupt each other's `.part`
files). A `.zim` that is present but truncated is *not* counted as installed —
the resolver compares on-disk size with the catalogue and re-fetches it.

### Book library notes

`30-calibre.sh` needs a **real** Calibre database in `/srv/ark/calibre-library`.
The book shelf answers HTTP 500 for every request if `metadata.db` is missing or
is the 0-byte file that `sqlite3 /path/metadata.db 'SELECT …'` silently creates,
so the step validates the file and, when it is unusable, installs the `calibre`
package (`--no-install-recommends`, ~60 MB) and builds a genuine empty library
with `calibre-debug`. Books dropped in the File Box are added by
`ark-import-books`, which prefers `calibredb` and falls back to the library's
own upload endpoint.

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
200/302, and 5 GiB must stay free. The receipt is machine-readable so the Mac
never has to guess.
