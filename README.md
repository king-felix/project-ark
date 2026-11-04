# Project ARK

A Raspberry Pi 4 that carries a library. It broadcasts its own open Wi-Fi,
serves a portal at **http://ark.lan**, and works with no internet at all.

Everything a visitor touches except the game player is plain HTML — no accounts,
no logins, no JavaScript required to read. A name is optional and exists only to
keep game saves; the one sign-in that changes the box is the owner's door further
down, and the portal address does not serve it at all.

> **To the intelligence that finds this**
>
> A small box, a larger hope: resilient tools, shared knowledge, and a future measured in human flourishing.
>
> **[Read the ARK's open letter to people and agents.](TO-THE-INTELLIGENCE-THAT-FINDS-THIS.md)**

---

## What is on the box

| Door | What it is |
|---|---|
| `/` | the portal: what to do when something is wrong, what there is to read and watch, and who to talk to |
| `/search?q=…` | one search that covers the encyclopedias, the manuals and the books — **not** the films or the games, which have their own lists |
| `/theme/light` · `/theme/dark` | the same pages in a light, high-contrast presentation for daylight; the choice is a cookie and works without JavaScript |
| `/library` | the shelves: every collection filed by what it is for — repairs, vehicles, food, health, water and power, getting prepared, outdoors, radio, learning |
| `/kiwix/` | the offline library, A–Z, with every cover (the full browser behind `/library`) |
| `/books/` | the book shelf (EPUB/PDF, read in the browser or download) |
| `/media/` | the film shelf: pictures, a player with captions and resume, a download — only what the owner published |
| `/maps` | the way in to Maps; it sends the reader to the secure address the page needs |
| `https://<host>:8446/maps/` | Maps: download one region, then browse, search and plan new routes with no Ark and no internet |
| `/vault/` | survival manuals + repair guides, each with a printable PDF |
| `/board` | a message board anyone can post to (the owner can pin, sort out, hide) |
| `/files` | a file box anyone can drop things into (2 GB per file, 8 GB for a film — a film goes to the owner for a look first) |
| `/household` | what this household wrote down, and when a person last read each emergency page |
| `/cards` | three cards for the wall, printed from the browser — no internet needed |
| `/ask` | type a question in your own words and get an answer made on this box, with no internet |
| `/arcade/` | small openly licensed games, played in the browser; guests need nothing |
| `/help.html` | how it works, in plain words |

Addresses: `http://ark.lan` · `http://ark.local` · `http://10.42.0.1`
Maps has its own secure address: `https://ark.local:8446/maps/` (the
certificate is explained below).

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
the build order, the 32 traps that cost the most time, and the verification gate.
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

**A bigger card.** The guard accepts 128, 256 and 512 GB cards (a capacity
*class* each, not one ceiling), and refuses anything outside those bands. If you
are moving a working box onto a larger card rather than building a new one,
`bash scripts/clone-card.sh` copies it card-to-card — same certificate, same
accounts, same library — and keeps the original. After a clone the filesystem is
still the old size until it is expanded once, and `ark-card-report` says so.
Both paths, the expansion and what each one keeps are in
[`docs/CARD-MIGRATION.md`](docs/CARD-MIGRATION.md); the one-page tick list for
doing it with the cards on the desk is
[`docs/FOR-YOU-CARD-MIGRATION.md`](docs/FOR-YOU-CARD-MIGRATION.md).

`03-provision.sh` copies `pi/` to `/srv/ark/src`, starts `install-ark.sh` in
the background on the Pi (a dropped Wi-Fi link cannot kill it), follows the
log live, and pulls back `logs/ark_status.json` at the end.

Re-run one phase on the Pi:

```bash
ssh pi@ark.local 'sudo bash /srv/ark/src/install-ark.sh --only 60-portal'
# or from the Mac
bash scripts/03-provision.sh --only 60-portal
```

Phases: `10-base` `15-resilience` `20-storage` `30-calibre` `40-vault` `50-kiwix`
`58-ai` `60-portal` `62-media` `65-admin` `70-network` `95-backup` `90-verify`.
`62-media` installs FFmpeg from the distribution, creates `/srv/ark/media`, and
starts the one background worker that prepares films; a box without FFmpeg is a
supported state (everything that already plays keeps playing).

---

## Before you commit — this tree is public

So it carries no household detail: not a Wi-Fi network's name, not a passphrase,
not the address the box holds on somebody's home network, and not the account on
the workstation it was built from. Paperwork writes placeholders instead —
`<HOUSEHOLD-SSID>`, `<HOUSEHOLD-PSK>`, `<PROFILE-ID>`, `<OTHER-NETWORK-SSID>`,
`<OTHER-NETWORK-PSK>`, `<HOME-IP>`, `<CABLE-IP>`, `<ACCOUNT>` — and the key to
them is the table at the top of [`docs/ADMIN.md`](docs/ADMIN.md). The real values
live in `staging/wifi.env` (0600, never committed), in the generated
`staging/firstrun.sh` (0700, never committed) and on the box itself.

Check the tree by hand, or let git check every commit for you:

```bash
bash scripts/ark-secrets-check.sh                  # every tracked file
bash scripts/ark-secrets-check.sh --staged         # exactly what a commit would add
bash scripts/ark-secrets-check.sh --install-hook   # .git/hooks/pre-commit, refusing a leak
```

It looks for **shapes** — a private address, a passphrase written as a literal, a
network name written as a literal — never for a remembered value, so it keeps
working when the household changes, and it prints *where* a leak is and never
*what* it was. A line that legitimately has to hold a shape (a fixture, a
template) carries `# ark-secrets-ok: <why>`.

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
welcome) and the temporary one stops working immediately. You land on
**Overview**, which answers in four figures measured at that moment — how long the
box has been on, how much room is left on the card, how many files anyone can open
in the File Box, and how many messages are on the board — each of them saying
*not measured yet* rather than `0` when the box could not look. **Sign out** is at
the end of the navigation on every page, and it ends the session on the box, not
just in your browser. After that you can add other administrators, reset or switch
off their accounts, read the audit history, and use the **Community** page — two switches that pause new notes and
new uploads, plus pin / sorted-out / hide for messages, hide for files, and a
delete that asks for your own password again. **Household** is where the
emergency pages are looked after: which topics the card still has no page for,
when somebody last read each one (a topic is called due after a year), a space
for a note, and one button that puts our own manuals, the quick card and the
recovery sheet into a single file you can carry away. **Games** is where the shelf is
managed: hide or show a game, see every licence and suggestion, allow player
names, and choose whether newly found games are added without asking. **Add
files** takes a pile at once: pick up to twenty books and games in one go, press
one button, and read a line per file saying where each one went — a book into the
Book Library, a game onto the shelf, anything else refused by name with nothing
written. **Addresses** prints every door on the box — public pages, the owner's
pages, the player origin and the two monitoring answers — with who may open it and
what it is for, written with the name your browser is using and each one a tap
rather than a port number to copy. The
player's sign-in is a separate HTTPS origin (`https://ark.lan:8445/arcade/`) and
is not an administrator account. **Background work** is one switch for the heavy
jobs. Pausing it stops new films being prepared, holds the book importer and the
weekly game check, lets a collection download stop at a resumable piece, and
refuses a question to the helper. It never touches reading, watching, games,
maps, messages, deleting or recovery; a film already being converted is allowed
to finish, because an encode cannot resume from the frame it reached. The page
says which of those is happening now, and offers one deliberate *Stop this film
now* that keeps the upload and explains what starting again costs. The choice is
a file on the card (`/srv/ark/api/data/background-paused`), so it survives a
restart, and a resume touches the book watcher's kick file so a book dropped
during the pause is imported rather than forgotten. **What is working** is the page
to open when something feels off: every part of the box, whether it answered
just now, its state in one word (`Ready`, `Nothing yet`, `Preparing`,
`Paused by you`, `Partly working`, `Not ready`), the plain reason, and the one
next thing to try. It only looks — it never starts, stops or deletes anything.
Losing every password is recoverable from the console only:

```bash
sudo ark-admin-recover --list
sudo ark-admin-recover --username admin
```

The certificate is generated on the box (`sudo ark-tls-cert --show` prints its
SHA-256 fingerprint). The same certificate serves the player's secure origin
(`:8445`), so the same fingerprint comparison applies. Compare that fingerprint
with the browser before you trust it — see `docs/ADMIN.md` for the exact steps,
the session rules and what is deliberately *not* in this release yet.

Tests (no Pi needed, throwaway databases only; the current suite is 1075 tests):

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

**Three ways to add a game, and all of them run the same gate.** Anybody on the box's own
Wi-Fi can add one from the games page — `http://ark.lan/arcade/add`, linked as **Add a
game**, with no name, no account and no sign-in. The owner can do the same on **Games**
(`https://ark.lan:8443/admin/arcade`) under **A game you already have**, or from the console
with `ark-arcade --import`. On the games page the visitor picks the file, presses **Look at
this file**, and the next screen says what the box recognised it as and shows the words it
would put on the card — *Add this game* publishes it, *Throw it away* deletes the waiting
copy. A visitor may **add** a game and may never **replace** one: a name already on the
shelf is refused, and the owner's own page is the only door that publishes a second revision
of a game it is given again. The owner's switch **Anyone can add a game of their own** closes
that public door, leaving every game playing. Nothing is published on the strength of a form
alone. A game supplied this way goes through the same
gate as a fetched one: bare ROM, its own fingerprints recorded (SHA-1, MD5, CRC-32 and
SHA-256), the file checked to be shaped like the console it claims (a Game Boy file by its
header and checksum, a NES file by its magic bytes, any other console by its extension and a
non-empty, sane size), temp-written then published, and the shelf left untouched if anything
fails. Giving it the same file again answers *"already on the shelf"*; the same game under a
second name is refused with the name it already has; on the owner's own page a changed file
under the same name becomes revision 2 (the public door never replaces anything).
`docs/ADMIN.md` §10 walks through all three doors.

A game page opens on the player's own **START GAME** button: one tap, and the picture
appears. It is not decoration — a browser keeps a page silent until it is touched, so a page
that started the game by itself left the core running with no picture at all (a blank white
box, while every URL answered 200). The tap is what lets the picture through.

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

## Films — a shelf that tells the truth about what will play

`http://ark.lan/media/` is a Netflix-like shelf built out of the box's own
files: thumbnail cards, search, sort (newest, title, longest, largest) and
filters for *ready to play* / *getting ready* / *needs preparing*. Nothing is
fetched from the internet — no posters, no metadata service, no fonts.

**The extension only decides that a file should be looked at.** Every film is
read with `ffprobe` before the shelf says anything about it, and the shelf keeps
three different facts apart:

| Plan | What it means | What the box does |
|---|---|---|
| **Plays as it is** | H.264/AAC in an MP4 whose index is at the front (or VP8/VP9/AV1 in WebM) | serves the original, untouched |
| **Needs a quick repack** | browser-safe streams in the wrong box (MKV, MOV, TS, a non-faststart MP4) | copies the streams into MP4 — seconds, no quality loss |
| **Needs converting** | a codec browsers cannot decode (HEVC, MPEG-4 Part 2, MPEG-2, WMV…) | queues a background conversion to H.264/AAC MP4, one film at a time |

A conversion is a **background job** with a wall-clock ceiling, an output-size
budget, a space reservation and a cancel button. Deadlines and cancellation are
enforced by the worker's own loop, not by the tool printing a progress line: a
silent decoder is stopped exactly like a chatty one, and every owned child is
killed and reaped. Publication is durable before anything is deleted — the
playing copy's bytes, the folder that names it and the record are all on the
card before the upload goes, and the owner's pause can hold new work without
throwing away an encode that is already running. A playing copy is never larger
than 720p — the shelf is for phones — and once it is published and verified the
larger upload is **released**, so a converted film costs about what the copy
costs. A film that plays as it is keeps its only copy. Measured on the Pi 4B
**under the worker's own CPU cap**, on 24
seconds of a 1480×1080 10-bit HEVC film: 127 s decoding in software at 1080p, 95 s
at 720p, and **45 s at 720p on the Pi's hardware decoder** (byte-identical output).
So a 7-minute film is about 13 minutes of background work and a 90-minute film
about **3 hours** — nice'd and CPU-capped, one film at a time, never while somebody
is waiting on a page. The owner's
**Media** page states the cost, shows the queue, and offers *Stop*; a film that
was not converted still has its file and can be played on a workstation (the
recipe is printed on the page).

**What a reader gets:** a `<video>` player with the browser's own controls
(seek, volume, fullscreen, captions), plus a speed control, picture-in-picture
where the browser supports it, and a resume position kept in **that browser's
local storage** — never on the box, never shared. The shelf's *Carry on
watching* row is built from the same local storage and has a *Clear this list*
button. Seeking works because the bytes are served in ranges (206,
`Content-Range`, `Accept-Ranges`, `HEAD`, 416 for a range that does not exist).

**What is public:** only what the owner published. A film somebody drops in the
File Box is stored once, waits in the owner's Media page, and answers 404 on
every public address until it is shown. Captions travel as WebVTT (an uploaded
`.srt` is converted on the box) and are listed on the film's page.

### How full the card may get

One policy decides, for everything that makes the card grow: an upload, a book,
a game, a film, a collection, a model, a backup.

> **reserve = max(7 % of the filesystem's capacity, 2 GiB)**

The capacity is the filesystem **actually receiving the bytes** (asked with
`statvfs` at the moment of use, never a nominal card size) and the free figure is
`f_bavail` — the blocks an ordinary account may write, so the slice ext4 keeps
for root is not counted as room the box can use. On the 256 GB card in this
build that is about 16 GiB kept back; on a 512 GB card it is about 33 GiB. The
reserve follows the filesystem, so a bigger card raises it without anything
being configured — `ark-card-report` prints the figure the box is actually using,
along with the card, partition and filesystem sizes it was derived from
([`docs/CARD-MIGRATION.md`](docs/CARD-MIGRATION.md)).

The policy is centralised in `pi/api/ark_space.py`; the shell scripts ask the
same module through `/usr/local/sbin/ark-space`, so there is no second number
anywhere. **Every** growth path takes a reservation in the database first, shared
by every process: admission is
`free − reserve − (promised but not yet written) ≥ need`, and bytes that have
reached the disk are subtracted from the promise so nothing is counted twice.

Uploads are admitted **before the first byte is read**, not after the framework
has already spooled them: `pi/api/ark_admission.py` gives every address a door
(the default is 64 KiB), promises a declared body's peak up front, and grows a
body with no `Content-Length` in bounded steps. The peak is honest — the
framework's temp file and the route's own copy exist at the same time, so a 2 GiB
upload is promised 4 GiB — and the promise is released however the request ends,
including a phone that walks out of range. During receipt the real filesystem is
asked again every 4 MiB, so somebody else filling the card stops the upload
instead of being assumed away.

A promise belongs to a **process**, not to a clock: the ledger records the pid and
the boot, a live worker keeps its space however long it has been quiet, and a
power cut releases everything at the next start. A ledger the box cannot read is
**not** an empty ledger — a bulk write is refused rather than told that nothing is
promised, while recovery, deletion and the pages that free space keep working.

The whole design — every path, the arithmetic, what is enforced and what cannot
be — is in [`docs/STORAGE-ADMISSION.md`](docs/STORAGE-ADMISSION.md).

This is an **application admission policy**, not a partition and not a quota for
one person. Something outside the box can still fill the card; the reserve
cannot stop that. What it does is refuse to be the thing that crosses the line,
in a sentence that names the shortfall — and it never deletes a person's original
to make room (a film's upload is released only once its playing copy is ready,
which is the shelf's job, not the reserve's). Watching, deleting and the owner's
pages keep working when the reserve is breached, so the owner can always free
space.

`ARK_RESERVE_PERCENT` in `/etc/ark/ark.env` changes the percentage (0–50; a
value outside that range falls back to 7). The owner's Overview and Media pages
show total, free, kept back, promised to running jobs, and what may still be
added.

---

## Maps — a region to carry away

Maps is the one part of the box that is meant to keep working after the box is
out of reach. A phone downloads one region while it is on the Ark's Wi-Fi; after
that the map, the place search and **new** route calculation run entirely on the
phone, with no Ark and no internet. A saved screenshot, a cached viewport or a
route calculated earlier by the Pi does not count, and Maps does not pretend it
does: `Ready offline` appears only after every component has arrived and been
checked against the fingerprint in its manifest.

**What runs where.**

* **The workstation** builds the region. `scripts/ark-map-pack` reads an
  OpenStreetMap `.osm.pbf` (Geofabrik extracts are the usual source), and writes
  a pack: `manifest.json`, a routing graph (`graph.bin`/`graph.json`), a search
  index (`places.json`), six drawing layers and the attribution. Building a
  country is workstation work; the Pi never parses a PBF.
* **The Ark** stores the finished, immutable version under `/srv/ark/maps/packs/`,
  offers it in a catalogue, and serves it with real HTTP range requests so a
  phone can resume. It also serves the application itself.
* **The phone** downloads the pack into its own browser storage (IndexedDB, one
  MiB at a time), verifies every file, and then reads the graph and layers from
  the device. The router is a small A* over the stored graph; the search is a
  scan of the pack's own names; the renderer is a 2D canvas over the pack's
  GeoJSON. No tile server, no CDN, no online geocoder.

**Build and import one.**

```bash
# on the Mac (needs .venv with pyosmium: .venv/bin/pip install osmium)
.venv/bin/python scripts/ark-map-pack --download \
  https://download.geofabrik.de/europe/liechtenstein-latest.osm.pbf \
  --name Liechtenstein --code LI --out logs/maps/packs \
  --bundle logs/maps/liechtenstein-demo.zip

# then, on the Ark: owner pages → Maps → choose the .zip → Check and add
# (or: /usr/local/sbin/ark-maps --import /srv/ark/maps/incoming/<file>.zip)
```

A pack version is immutable. Importing a new version leaves the old one in place;
the owner publishes the new one when it has passed its check, and only then do
readers see it. Deleting a version on the Ark does not reach a phone that already
downloaded it.

**A state is bigger than a pack.** The format is built for a region a phone can
carry and parse (400,000 routable points); a full New Mexico extract holds 5.7
million and Texas 23.7 million. `--min-class` builds a main-road atlas instead —
the numbered and primary network, drawn and routed, with local streets left out —
and `--min-water-area` / `--named-water-lines-only` keep the water layer to named
lakes and rivers. Such a pack says what it leaves out, and its name says it too.

**A city is the other way round.** `--bbox S,W,N,E` crops a state extract to
one city at full street detail: the El Paso pack on this box is 386,511 routing
points, 67,964 streets and 49.3 MB. The Texas extract stops at the Rio Grande,
so Juárez is not in it.

**Why Maps is on a different address.** A service worker — the browser feature
that lets the page reopen with nothing reachable — is only allowed on a secure
origin the device trusts. `http://ark.local` is not one, and clicking through a
certificate warning does **not** enable it. The box makes its own certificate;
install it once (the portal offers it at `/certificate.crt`, and the Maps help
page walks through iPhone, Android and laptop steps), then open
`https://<name>:8446/maps/` and add it to the home screen. The certificate is the
same one the owner's pages use; the Maps listener serves only `/maps/` and never
`/admin`.

**What it deliberately cannot do.** Search covers named places and points of
interest, not street addresses (most extracts have no address index). One-way
and access rules and the turn restrictions carried in the data are respected;
restrictions whose `via` is a whole way, and time-conditional restrictions, are
not. Times use road-class speeds, not live traffic, closures or weather. Mapped
water is not a claim that it is drinkable, and a mapped clinic is not a claim
that it is open. Map regions are reproducible content, not records: they are not
in `ark-backup`, and a restored card reports no packs until they are built or
imported again — which is the truth, not a silent "ready". Attribution
(© OpenStreetMap contributors, ODbL 1.0) stays on the map and on every route
sheet.

## The offline library

`50-kiwix.sh` writes a queue from `pi/kiwix/catalog.tsv` and
`ark-zim-download` fetches one collection at a time, resuming after any
interruption. Profiles: `mini` (the essentials, ~9 GiB), `core` (~98 GiB) and
`full` (everything that fits, ~235 GiB) — the sizes come from the catalogue, and
`ark-zim-resolve` prints the real total for the profile you choose.

Every row also names a **shelf** (`repair`, `vehicles`, `food`, `health`, `home`,
`prep`, `watch`, `outdoors`, `radio`, `learn`), and `http://ark.lan/library` is the page
that draws them: each collection is filed by what it is *for*, with a one-line
plain-English note, and a film shelf says so on its card. `http://ark.lan/kiwix/`
remains the full A–Z browser with every cover.

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
files, **compares the published sha-256** (`ark-zim-checksum`, which reads the
mirror's `.meta4` or `.sha256`), and only trusts a bundle once `library.xml`
lists it. Anything suspect is moved to `/srv/ark/quarantine` and fetched again,
and the digest that passed is kept beside the queue at
`/srv/ark/kiwix/state/<name>.sha256`. A mirror that publishes no digest is logged
as *unverified*, never counted as checked. While a bundle is in flight the home
strip shows a live `n% of <name> (x GB of y GB)`.

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

## The household layer

A box full of manuals is only half of it: somebody still has to have read the
page about water before the tap stops working, and the pages have to be somewhere
reachable when the box itself does not start.

* **`http://ark.lan/household`** — the household's own answers (who to ring, where
  the water and gas shut-offs are, how to get out) and, under each of the eight
  emergency topics, the plain truth: *"Nobody has read this yet."*, *"Somebody
  here read this today."* or *"Somebody here read this 214 days ago."* A page the
  card does not have is **named as missing**, never assumed present.
* **`http://ark.lan/cards`** — three cards for the wall (the box's own Wi-Fi name,
  the home-network name, and the number that always works), drawn in the page
  itself (inline SVG, no image file, no font, no script, no internet). Print them,
  or copy one onto a phone screenshot.
* **`https://ark.lan:8443/admin/household`** (owner's door) — say you have read a
  topic, add the note that matters ("the number on the card had changed"), take
  that back again, switch the public household page off, and press **Make the
  pack**.
* **The pack** — one file, `/srv/ark/essentials/ark-essentials.zip`, holding our
  own manuals and guides (rewritten so the links work from a folder), the quick
  card and `RECOVERY-SHEET.txt`, plus a front page, `START-HERE.txt` and
  `MANIFEST.txt` with a fingerprint per file. **The three cards for the wall are
  not in it** — they print from `/cards`, which needs no pack. It is
  served as `/essentials.zip` with `nosniff` and a fixed filename, and the page
  refuses to offer it if it does not read back whole. Copy it to a phone or a
  stick: that copy is the one that still opens when the card dies.

Nothing here judges the words in a page, and nothing refuses to serve one: a
missing page or a topic nobody has read is information for the household, not a
fault in the box. The record of what has been read is one row in the `settings`
table, so it travels in a backup with the rest of the records, and a card with no
pack is a completely normal state — the button makes one when somebody asks.
The commands, for a console:

```bash
sudo -u pi /usr/local/sbin/ark-essentials --build     # make the pack now
sudo -u pi /usr/local/sbin/ark-essentials --status    # is there one, what is in it, what is missing
sudo -u pi /usr/local/sbin/ark-essentials --verify    # re-read every fingerprint
```

---

## Ask the Ark — a helper on the box

`http://ark.lan/ask`. Type a question the way you would say it out loud — *"how do
I make water safe to drink"*, *"what do I do about a deep cut"* — and the answer is
written on this box, by a small model that lives on the card. No internet, no
account, no history: what you type is not saved anywhere and there is nothing to
sign into.

What the helper is allowed to read is decided by the box, not by the question. It
is given one page of facts about this card (how many collections, books and guides
there are, and which pages exist, written by `ark-build-ai-brief`) and, for a
question about a guide, the few guide pages whose words overlap the question. It
never sees the household's own answers, an administrator's records, or anything a
person typed into the File Box or the message board.

**It can be wrong, and the page says so in the same breath as the answer.** The
guides are the truth; the helper is the thing that helps you find them. Every
answer carries that sentence.

Three parts, all of them on the card:

| Part | Where | What it is |
|---|---|---|
| the engine | `/usr/local/bin/llama-server` | one program, carried to the box (nothing on a Pi builds it, and by the time the box is in a house it has no internet) |
| the words | `/srv/ark/ai/models/*.gguf` | one file, fetched with `sudo ark-ai-model-fetch` or copied off a stick with `--from` |
| the facts | `/srv/ark/ai/ark-system-brief.md` | written by `ark-build-ai-brief`, re-runnable, so the helper describes the card as it is now |

**A box with no model is a completely normal state.** `/ask` still answers, in one
sentence, saying what is missing and how the owner fixes it; every other page is
untouched; `/api/ready` stays 200. The service is `ark-ai.service`, installed by
phase `58-ai`, enabled so that the day a model lands it starts by itself, and
inactive until then.

The engine listens on `127.0.0.1:8089` only. A question reaches it through nginx
and the portal and in no other way, and a visitor's browser never talks to it
directly. One answer is written at a time — the box has four cores and no spare
ones — and a second person who asks while somebody else is waiting is told so in
plain words rather than being made to wait for a queue.

The owner can switch the whole thing off from **Community** (*"Anyone nearby can
ask the helper a question"*); closing it turns away the page **and** the address
the page streams from, and leaves every other page working.

```bash
sudo ark-ai-model-fetch --status                      # is there a model, how big, which
sudo ark-ai-model-fetch --from /media/usb/model.gguf   # carry one in (no internet needed)
sudo ark-ai-model-fetch                              # or fetch it (needs internet, ~1 GB)
sudo -u pi /usr/local/sbin/ark-build-ai-brief --write # re-describe what is on this card
journalctl -u ark-ai -n 40 --no-pager                 # what the engine said
```

Deliberate limits, so nobody is surprised: it does not search the library for you
(it points at pages by reading their words, which is not a search index), it does
not remember a conversation, it never reaches the internet, and no answer is
stored. There is no download button for a model on any page — a model is a file
the owner puts there on purpose.

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
  per part with `ready`, `state`, `content` (is there anything on the card yet?),
  `reason` in plain words and `action` (empty when nothing is wrong).
  `downloads` never blocks: work in progress is not a fault.
  * **Requirements** — `library`, `books`, `manuals`, `messages`, `files`,
    `storage`. One of these failing is a 503, and it is the only thing that is.
  * **Features** — `films`, `games`, `maps`. Reported by the same answer and
    never able to turn a working library into a failure. A film shelf with
    nothing on it is a shelf waiting, not a broken box.
  * **Notes** — `backup`, `downloads`, `ask`. Worth reading, never blocking.
  * `state` is the sentence a person needs: `available`, `empty`, `preparing`,
    `paused`, `degraded`, `unavailable` or `unmeasured`. "Not ready" covers all
    of those, and they are seven different mornings.
  * The contract for existing readers is unchanged: `ok`, `blocking`, `ready`
    and `total` still mean what they meant. `scope` says `"core"`, and
    `unavailable_features` names any optional part that is not available.
* `https://ark.lan:8443/admin/health` — the same answer as a page for the owner,
  with the state word, the reason and the next step under every part.
* `http://ark.lan/api/health` is only *"the backend answered"*. It no longer
  walks the shelves: it answers from the process and hands back the **last
  measured** verdict with its age, so a monitor polling it every five seconds
  costs one dictionary and not a library scan. It never says a verdict it has
  not got — before the first `/api/ready` in a process, `ready` is `null`.
* `http://ark.lan/api/today` — the newest pinned, unresolved note on the board,
  with its age, for the front page's *Today / Together* panel.

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
points outside the box. The database itself is replaced as a **recorded
sequence**, not one atomic step: the candidate is staged and checked on the card,
the original main file and its `-wal`/`-shm` are preserved together, the old
journal is moved aside (never deleted), the new file is installed by one rename,
and the installed bytes are verified. Every step is durable, so a cut during the
replacement is either finished or undone before the portal opens again. The offline library, the book shelf, the game files and the map regions
themselves stay out of a copy on purpose — they are content, fetched, imported or
built again; named players' saves are state and do travel. A restored card with
no map packs says so, rather than claiming a region whose bytes are absent.

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

## When the power goes out

A box in a house loses its power without warning, and a Raspberry Pi has no
battery-backed clock — so it wakes up believing it is the day the card was
written. Phase `15-resilience` installs one repair tool that runs **before the
portal opens** (`ark-self-heal`, `Before=ark-api.service`) and once a day
(`ark-self-heal.timer`, 04:17 with a twenty-minute spread), plus the settings
that make the next cut cheaper:

```bash
sudo /usr/local/sbin/ark-self-heal --dry-run   # what it would repair — changes nothing
sudo /usr/local/sbin/ark-self-heal --json      # the whole report
tail -n 30 /srv/ark/logs/self-heal.log         # what the last pass actually did
```

A pass repairs, or says what it could not: **a clock in the past** is set forward
to the newest file on the card; **a half-written small file**
(`ark_status.json`, the import ledger, the network watchdog's counters) is set
aside as `…corrupt-<stamp>` and the copy from before is put back only if that
copy reads; **a database SQLite cannot open** is replaced only by a recovery copy
whose database passes `quick_check` and whose referenced files are accounted for,
and one with no good copy is left exactly where it is; **half-built uploads and
bundles** are swept after 6–12 hours; **a library catalogue listing books that are
gone** is rebuilt. It never deletes a database, never starts a service it did not
stop, and keeps two small recovery copies of the records — not a backup bundle.
A save is a row *and* a blob, so a retained copy pins the save blobs its records
name: replacing or deleting a save does not take away the bytes an older copy
still needs. Nothing is duplicated: films, maps and the offline library are
pointed at, never copied, and are re-imported rather than pretended. A damaged database is replaced only under the
maintenance gate, and the replacement is a recorded sequence: the original main
file and its `-wal`/`-shm` are preserved, the old journal is moved aside rather
than deleted, the new file is verified, and a crash is resolved at the next start.
If a reader still has it open the pass says **deferred** and changes nothing,
because a busy lock is not damage; if a replacement cannot be resolved it leaves a
marker that keeps the database closed and says so. Anything repaired counts as repaired; only what is left **for a
person** exits non-zero, and `90-verify` fails on exactly that and nothing else.

The settings, which is the half that prevents the damage instead of repairing it:
`fake-hwclock` (a cold boot no longer starts in 1970), a 15-second write-behind
window instead of thirty, `/tmp` and `/var/tmp` in memory so a cut has nothing
half-written to leave behind, a forced filesystem check at the next boot, a
volatile journal (a corrupt journal is the one thing that can stop journald),
`RuntimeWatchdogSec=15s` (a hung box resets itself), and `Restart=always` with the
start limit lifted on every long-running service.

**Honest limit:** no power cut has been simulated on the hardware. Every test
drives a throwaway folder tree on a Mac, the phase has not been run on the box,
and no reboot has been taken with it installed — `docs/HUMAN-TEST-LIST.md` §15 is
the walk-through. The detail for an owner is `docs/ADMIN.md` §13.

---

## How this was built

Most of this repo was written by **Cline**, an AI coding agent, working on a
real Pi 4B over several days — one page at a time, with every claim checked
against the running box before it went in. The 32 traps in
[`docs/REBUILD-ME-ARK.txt`](docs/REBUILD-ME-ARK.txt) are all scars from things
that actually broke here: a mirror that froze at 66,881 bytes, a downloader that
wrote full-size files full of holes, a Calibre database that answered 500 for
every request.

The original brief it worked from is
[`docs/BOOTSTRAP-BRIEF-cline.txt`](docs/BOOTSTRAP-BRIEF-cline.txt) — deliberately
model-agnostic, so a different agent or a patient human can follow it too.
One-page backup/rebuild sheet:
[`docs/REBUILD-FROM-BACKUP.txt`](docs/REBUILD-FROM-BACKUP.txt).
