# The Ark — owner's guide

*Everything the box does, in the order you will actually need it.*

Written 24 October 2026 · device answers to **`ark.lan`**, **`ark.local`**, **`10.42.0.1`**

The Ark is a small computer with no internet connection of its own. It holds a library —
encyclopedias, repair guides, medical references, your own books — and it hands that library
to any phone, tablet or laptop that joins its Wi-Fi. No accounts, no sign-in, no internet
required. If the internet is gone, everything below still works.

---

## The one-page version

| I want to… | Open this | Then |
|---|---|---|
| look something up quickly | `http://ark.lan` | type 1–2 words, tap **Search everything** |
| read an encyclopedia offline | `http://ark.lan/kiwix/` | pick a collection, use the search box inside it |
| read a book | `http://ark.lan/books/` | browse or search, then **Read in browser** |
| fix something today | `http://ark.lan/vault/` | open a manual; print the PDF if you want paper |
| hand someone a file | `http://ark.lan/files` | choose file → **Put it in the box** |
| put a book on the shelf | `http://ark.lan/files` | drag the `.epub` in and walk away |
| leave a note for the household | `http://ark.lan/board` | name is optional → **Put it on the board** |
| see what is on the box | `http://ark.lan/api/status` | plain text, safe to read on any phone |
| know whether it actually works | `http://ark.lan/api/ready` | `200` = everything works; `503` = the body says which part and why |

Same box, four addresses. Use whichever your device can resolve.

---

## 1. Getting on

### On your home Wi-Fi
The Ark joins your normal network on its own at boot. On any device already on that Wi-Fi,
open **`http://ark.lan`**.

### On the Ark's own hotspot (internet down, or away from home)
The Ark broadcasts a Wi-Fi network called **`The-Ark-Offline`**. It is open — no password.

1. Join `The-Ark-Offline` on the phone.
2. The phone may pop up a *“Sign in to Wi-Fi”* window. That window is a cage — it cannot
   show the whole site. Close it.
3. Open a browser and type **`http://ark.lan`**. On the front page there is a **Copy Address**
   button if typing is awkward.

Everything works the same on the hotspot; only the address changes (`10.42.0.1` also works).

### If the Wi-Fi goes away while the box is running

It does not need a reboot, and you do not need to do anything. About two minutes after the
home Wi-Fi disappears, the Ark turns its radio into the `The-Ark-Offline` hotspot by itself, and
join as above. When the home Wi-Fi comes back **and nobody is connected to the hotspot**, the box
returns to it on its own — it will not take the hotspot away from a phone that is reading.
Raised by itself, it goes back by itself; if you asked for a network (`sudo ark-mode home` or
`sudo ark-mode hotspot`), that choice is remembered until `sudo ark-mode auto`.

`/srv/ark/logs/net-watch.log` is the record: one line per decision that did something, with the
time. A quiet check writes nothing, so a short log means a quiet day.

### Make it feel like an app (recommended for phones)
Open `http://ark.lan` in the phone browser → menu → **Add to Home Screen**. You now have an
icon that opens the Ark in one tap, and it still works with no internet.

---

## 2. The front page, tile by tile

The home screen is deliberately boring: four headings, plain words, big buttons.

| Section | Tiles | What it really is |
|---|---|---|
| **Find something** | one search box | searches every collection, every manual and the bookshelf at once |
| **Start here** | Offline Library · Books · Manuals & Guides | the three libraries |
| **When something breaks** | Repair guides · Quick card | iFixit-style repair pages, and a one-page card to print |
| **For everybody here** | Message Board · File Box | notes and file swapping for anyone on the Wi-Fi |
| **Not sure how this works?** | Help | the same instructions, in even simpler words |

Every tile has a one-line description under it and a **How to use** badge, because the person
who needs this most will not be the person who set it up.

---

## 3. The features, one at a time

### 3.1 Search everything — `http://ark.lan/search`

**What it is for:** the moment where you do not know *which* library has the answer.

It asks every collection at the same time (in parallel, so a slow one never stalls the page),
plus the manuals and the book shelf, and groups what it finds into three blocks:

- **From the library** — actual encyclopedia articles
- **From the manuals** — your own pages
- **From the bookshelf** — books by title or author

**How to use it:** one or two words. `bleach water`, `burn`, `radiator leak`. Short and
specific beats a sentence. Tap any result to read it right there.

**If nothing matches:** the page will say so and offer links into the library and the manuals.
Narrower search boxes inside each collection often find more than the global one.

### 3.2 Offline library — `http://ark.lan/kiwix/`

**What it is:** whole websites, frozen and stored on the box. They open in the browser and
behave like the real sites — search, links, pictures, no internet.

Currently installed (24 Oct 2026 — 8 collections, about 9 GB):

| Collection | Size |
|---|---|
| iFixit repair guides | 3.3 GB |
| WikiMed medical encyclopedia | 1.7 GB |
| Wikivoyage travel guide | 1.0 GB |
| WikEM emergency medicine | 256 MB |
| Cooking Q&A | 227 MB |
| Woodworking Q&A | 102 MB |
| Amateur Radio Q&A | 74 MB |
| Medical Sciences Q&A | 58 MB |

**How to use it:** open a collection → type in its search box → read. The arrow at the top
returns you to the shelf of collections.

**Reading on a phone with no network:** works — that is the entire point. The collection lives
on the box, the box broadcasts Wi-Fi, the phone reads it.

**About the “Download” button** inside each collection: it hands you the whole collection as a
single `.zim` file. That is only useful if you already run an offline-reader app on that device;
the supported path is to read in the browser, which needs nothing installed.

### 3.3 Books — `http://ark.lan/books/`

**What it is:** your own bookshelf, served from a real Calibre library. Series, tags, covers,
reading order and author pages all survive, because they all live in the library's database.

**How to use it:**

1. Open `http://ark.lan/books/`.
2. Browse by *Newest*, *Authors*, *Series*, *Tags*, or use the search box at the top.
3. Open a book → **Read in browser** (there is a reader built in — it works on phones and
   needs no app), or **Download** the EPUB/PDF to a tablet, Kindle or USB stick.

**Getting books onto it:** see section 4 — drag-and-drop, on the shelf in seconds.
As of 24 October 2026 it holds **1,290 books**, adopted from the Windows Calibre library.

### 3.4 Manuals and guides — `http://ark.lan/vault/`

**What it is:** short, deliberately plain pages written to be read when everything else is off.
No JavaScript, dark background, big text — and a PDF copy of every page for the day the
batteries are gone.

- **Manuals (7):** Water · Food · Fire · Power · Shelter · Sanitation · First Aid
- **Repair guides (3):** Electrical · Household Repairs · Plumbing
- **Print these:** a one-page **Quick Card**, plus 8 printable PDFs

**How to use it:** open the guide, read it top to bottom — they are written short on purpose.
At the bottom of every page is a **Print this page (PDF)** button, and the index lists all
11 PDFs together. Print at 100%, black and white is fine.

**Put paper copies in the house.** That is not sentimentality; it is the only version that works
after a dead battery. `/vault/pdf/` has everything you need for a ring binder.

### 3.5 Message board — `http://ark.lan/board`

**What it is:** a whiteboard for the household. Notes stay on the box; nothing is sent anywhere.

**How to use it:** type a message → **Put it on the board**. Your name is optional (blank shows
as *Anonymous*). Twenty notes fit on a page, newest first; **Older messages** / **Newer messages**
walk back and forward, and the heading above the list counts every note there is.

Good uses: *the tap in the back garden is leaking, I have spare washers* · *power cut 2–3pm
Thursday* · *does anyone have a 13 mm socket?*

Anyone on the Wi-Fi can read and post. That is the feature, and also the warning: do not put
private things on it.

**A warning that matters** stays at the top of the page, and anything already sorted out sinks
below the questions people still have. If the owner has closed the board to new notes, the
button is replaced by a sentence saying so, and everything already written is still readable.
A note the owner hides disappears from this page but is not destroyed.

### 3.6 File Box — `http://ark.lan/files`

**What it is:** swap files with every device on the network, no email, no cloud, no account.
Phone → laptop, laptop → tablet.

**How to use it:**

1. Open `http://ark.lan/files`.
2. **Choose a file** (up to 2 GB), optionally add a short note (*“spare gasket — fits the big tap”*).
3. **Put it in the box.** It is now listed, and anyone on the Wi-Fi can download it.

Two people can each put in a file called `photo.jpg` and nothing is lost: every upload keeps its
own place on the box, and the name you see is just a label on it. An empty upload and anything
over 2 GB are refused with a sentence telling you which, and the box always keeps a little space
in reserve so it can never fill its own card so completely that it stops working.

**The clever part:** if the file you drop in is a book — `.epub`, `.pdf`, `.mobi`, `.azw3`,
`.fb2`, `.djvu`, `.cbz` and friends — it is picked up within seconds and filed onto the book
shelf automatically. No command, no second step. The copy in the File Box stays put so the link
you were given keeps working. See section 4.

If the owner has paused sharing, the upload button is replaced by a sentence saying so, and every
file already in the box still downloads normally. The owner can also hide a file or, in the last
resort, remove it for good. The list shows the newest 50 files; when there are more, it says so
and the count in the heading is the real total.

### 3.7 Is it happy? — `http://ark.lan/api/status`

A plain-text page for the curious (or for you, at 2am): how many collections are installed and
how big they are, how many books are on the shelf, how many guide pages exist, what is
downloading right now, and how much space is left.

It now also says, for each part of the box, **whether a visitor could use it** and — when the
answer is no — the reason in a sentence. Two words are worth knowing:

- **ready** — someone could use it this second.
- **content** — whether there is anything on the card for that part yet. A part that is simply
  empty ("no collection has arrived yet") is *not* broken, and the page says so instead of
  pretending everything is fine.

The front page shows the same thing in one line: a **green** dot when everything works, **amber**
while the box is still filling up, and **red** (with the reason) when something that exists
cannot be served.

If you want the short answer rather than the whole picture, open
**`http://ark.lan/api/ready`**: it answers `200 OK` only when every part works, and `503` with
the reasons when one does not. Both are safe to read on any phone — there is nothing to press.
`http://ark.lan/api/health` is the odd one out on purpose: it only says *"the backend answered"*,
which is not the same as *"the box works"*.

---

## 4. Getting books onto it

### 4.1 One or two books, from any device — 30 seconds

No software, no commands, no login.

1. On the PC (or phone) open **`http://ark.lan/files`**.
2. Drag the `.epub` / `.pdf` / `.mobi` into the **Choose a file** box.
3. Tap **Put it in the box**.

That is it. Within a few seconds the book appears at **`http://ark.lan/books/`** with its title,
author and cover. Nothing else to do.

**If it does not appear:**

- Give it 30 seconds and reload `/books/` — the watcher fires within seconds, but a busy box can lag.
- Check the note under the File Box: a file that was refused (empty, or over 2 GB) says so.
- A `.pdf` that is really a scan will still import, but it lands as a PDF book, not a text one.
- Still missing? On the box: `sudo ark-import-books --status` shows the count,
  `--list` shows what it would import, and `journalctl -u ark-book-import` shows the last run.

### 4.2 Your whole Calibre library, from Windows

This keeps everything: series, tags, covers, reading order, custom columns. It is two commands.

**Step 1 — copy the folder over** (PowerShell on the Windows PC):

```powershell
scp -r "$env:USERPROFILE\Calibre Library" pi@ark.local:/srv/ark/imported-library
```

- It will ask for the `pi` password — the one this Ark was built with (whoever built it has it in `staging/pi-password.txt`). That prompt is normal — the PC has no key yet.
- `ark.local` is the name to use on Windows (`ark.lan` will not resolve there).
- Lots of small files means it looks slow; that is the file count, not a fault. Keep the PC awake.
- If the transfer is painfully slow, this single-stream version is much faster and needs no zip:

```powershell
tar -C "$env:USERPROFILE" -cf - "Calibre Library" | ssh pi@ark.local "mkdir -p /srv/ark/imported-library && tar -C /srv/ark/imported-library -xf -"
```

**Step 2 — adopt it** (only after step 1 has finished):

```bash
ssh pi@ark.local 'sudo ark-import-calibre-library /srv/ark/imported-library'
```

You will see something like:

```
   source library: 412 book(s), 1.8G
   old library kept at /srv/ark/calibre-library.bak-20261024-021500
 ✓ library adopted: 412 book(s) (HTTP 200)
```

What it does, and what it refuses to do:

- It validates `metadata.db` **before** touching anything. No database, or one the Calibre on
  the box genuinely cannot open, and it stops and tells you — the live shelf is untouched.
- If the copy comes from a *newer* Calibre (a higher schema number), it does not guess: it asks
  the installed Calibre to open a throwaway copy of the database. If that works — as it did for
  the Windows library here, schema 27 read by 8.5 — it carries on and says so. If it does not,
  it refuses and tells you to update Calibre or add `--force`.
- The previous library is moved to `…/calibre-library.bak-<date>`, never deleted.
- If the shelf fails to answer afterwards, it puts the old library back automatically.
- The source folder is copied, not moved: `/srv/ark/imported-library` stays where it is, so you
  can delete it (or copy it again) whenever you like.
- Save the backup until you have clicked around the new shelf and are happy, then delete it.

**Windows alternative without typing commands:** WinSCP (free) → connect to `pi@ark.local` →
drag the `Calibre Library` folder into `/srv/ark/imported-library` → run step 2.

### 4.3 From a Mac or Linux

```bash
rsync -av --progress "$HOME/Calibre Library/" pi@ark.local:/srv/ark/imported-library/
ssh pi@ark.local 'sudo ark-import-calibre-library /srv/ark/imported-library'
```

(`rsync` is resumable — if the Wi-Fi drops, run it again and it continues.)

### 4.4 What happens to a book you drop in

- Supported: `.epub` `.pdf` `.mobi` `.azw` `.azw3` `.azw4` `.fb2` `.djvu` `.cbz` `.cbr`
  `.lit` `.pdb` `.prc` `.ibooks` `.kepub` `.html` `.htm`
- It is added by the same engine Calibre uses, so metadata is read properly (title, author,
  series, cover) — not just the filename.
- **Your original is never deleted, and a File Box link never breaks.** A file you dropped in
  the File Box is *copied* onto the shelf and stays in the box, so the download link you sent
  somebody keeps working. A file you put in the staging folder `/srv/ark/calibre-incoming/` is
  moved to `done/` once it is safely in the library. Either way the box remembers the book by its
  *content*, so dropping the same book again under a different name does not put a second copy on
  the shelf (the File Box may still hold two separate shared files — those are two things people
  uploaded, and both keep their links).
- Wrong file, or you changed your mind? Delete it in `/books/`, or re-add a corrected copy.
- Two watchers do this: one for `/srv/ark/drop/files` (the File Box) and one for
  `/srv/ark/calibre-incoming` (a staging folder for big batches). What has already been imported
  is listed in `/srv/ark/calibre-incoming/.imported.json`; `done/` is never scanned again, so a
  second pass cannot offer the same book twice.

---

## 5. Growing or refilling the library

The Ark downloads its own content, in the background, resumably. It uses the home Wi-Fi when it
is there and simply pauses when it is not.

**Ask for more content.** Three profiles, increasing size (a fresh card wants `full`):

```bash
sudo ark-zim-resolve --profile mini   # ~9 GiB : medicine, repair, travel, cooking, water/food safety
sudo ark-zim-resolve --profile core   # ~86 GiB: + Wikipedia without photos, farming, gardening,
                                      #          textbooks, self-reliance, bicycle repair, dictionary
sudo ark-zim-resolve --profile full   # + electronics, technology books, MDWiki, Wikipedia with photos
```

`--profile` only *resolves* what to fetch and writes the queue; the downloader service picks it
up within a minute. Watch it with:


```bash
tail -f /srv/ark/logs/zim-download.log
```

**Rules it follows without being asked:**

- It never fills the card: **12 GiB stays free**, always. Anything that would breach that is
  skipped, with a line in the log saying why.
- It compares the file on disk against the catalogue, so a half-finished download is re-fetched,
  not counted as installed.
- A bundle the reader cannot actually open is re-fetched rather than trusted — that check caught
  two short files on this build already.
- One downloader at a time (a lock file), so two runs cannot corrupt each other.
- Safe to unplug: the downloader continues where it stopped.
- `full` also *lists* Wikipedia with photos (118.7 GiB). It needs about 131 GiB free, so the
  reserve rule skips it and says so in the log — on a 256 GB card it can only live there alone.


**How much is left?** `http://ark.lan/api/status` → `downloads`. The numbers there are measured
from the card, not estimated:

| Field | What it means |
|---|---|
| `pending` | collections still to come |
| `current`, `current_bytes`, `current_total` | the one being fetched, and its real bytes |
| `percent` | how far through that one collection |
| `overall_percent` | how much of the whole queue is already on the card |
| `remaining_bytes` | bytes still to fetch |
| `rate_bps` | bytes per second, measured over the last few seconds — `0` when it is not moving |
| `eta_seconds` | time left at that rate — **`null`** until a rate has actually been measured |
| `stalled` | `true` when nothing has been reported for over two minutes |

A `null` where you expected a number is the honest answer, not a bug: the box will not invent a
percentage or a countdown it has not measured. The front page uses exactly the same figures, so
"34 % of `wikipedia_en_medicine_maxi` (3.2 GB of 9.4 GB), about 2 h 10 min left" is real.

---

## 6. Health check in 60 seconds

Do this after any power cut, and before you rely on the box for a trip.

1. Open `http://ark.lan` — front page loads, status line is green.
2. `http://ark.lan/books/` — the shelf lists your books.
3. `http://ark.lan/vault/` — the manuals index loads.
4. Open one collection in `/kiwix/` and search it — a hit means the content is readable, not just
   present.
5. `http://ark.lan/api/ready` — answers `200` when every part works. If it answers `503`, the
   body names the part and the reason; that is the difference between a box that is *up* and a
   box that *works*.
6. `http://ark.lan/api/status` — space left, and nothing stuck on the same download twice running.
7. `sudo ark-mode status` — the mode, and **chosen by**: `owner` means a person pinned it,
   `auto` means the box decided. `tail -n 5 /srv/ark/logs/net-watch.log` says what the box did
   about the network — an empty or short log is normal, because a quiet check writes nothing.


For whoever looks after the box, one more: `https://ark.lan:8443/admin` shows a
sign-in page. That is the owner's door — it changes the machine, it is never
part of what a visitor sees, and on a box that has never been claimed it stays
shut on purpose (`docs/ADMIN.md` explains the one-time claim).

From the box itself, one command prints a receipt with every check:

```bash
sudo bash /srv/ark/src/steps/90-verify.sh
```

Re-provisioning is idempotent: re-running any phase repairs it rather than duplicating anything.

---

## 7. Troubleshooting

| Symptom | What it means | Fix |
|---|---|---|
| `ark.lan` does not load | device on a different Wi-Fi, or the name will not resolve | try `http://10.42.0.1`; on Windows use `http://ark.local` |
| Odd "Sign in to Wi-Fi" popup | that is the phone's captive window, not the Ark | close it, open `http://ark.lan` in the real browser |
| Front page fine, `/books/` shows an error | library database missing or unreadable | `sudo ark-import-calibre-library <path>`, or re-run phase `30-calibre` |
| Front page dot is amber | the box is still filling up — a part has nothing on the card yet | nothing; `/api/ready` names which part and why |
| Front page dot is red, or `/api/ready` answers 503 | a part exists but cannot be served (the program behind it is not answering) | open `https://ark.lan:8443/admin/health` and follow **What to do:** |
| Book dropped in, never appears | wrong extension, empty file, or over 2 GB | `sudo ark-import-books --list`; `journalctl -u ark-book-import -n 30` |
| Collection opens but searches find nothing | bundle present but unreadable (interrupted download) | auto-detected and re-fetched; `tail -f /srv/ark/logs/zim-download.log` |
| Nothing downloading any more | no network the box can use — the watchdog has already raised the hotspot if nothing was reachable | `sudo ark-mode status`, then `tail -n 10 /srv/ark/logs/net-watch.log`; `sudo ark-mode auto` sends it back to the home Wi-Fi now |
| Downloads crawl or fail repeatedly | flaky Wi-Fi, or the mirror is throttling | they retry with backoff; nothing is lost |
| The home Wi-Fi went off while the box was running | that is the case the box now handles itself | nothing — within about two minutes it broadcasts `The-Ark-Offline` and keeps reading working. `tail -n 10 /srv/ark/logs/net-watch.log` shows the decision |
| The hotspot is up but the home Wi-Fi is fine | it comes back on its own once nobody is using the hotspot | or `sudo ark-mode auto` to go back at once |
| The hotspot never closes even though nobody is connected | the box could not read the station list, so it assumes somebody is reading | `sudo ark-mode status`, then `sudo ark-mode auto`; check `journalctl -u ark-net-watch` |
| Cannot reach it from a phone at all | hotspot not up | `sudo ark-mode hotspot`, then join `The-Ark-Offline` |
| Out of space | something large added by hand | **What is working** shows which folder holds what; `sudo du -sh /srv/ark/*` for the detail; the downloader keeps 12 GiB free on its own |
| Forgot the address | — | `ark.lan`, `ark.local`, `10.42.0.1` — all the same box |

---

## 8. Command cheat sheet

```bash
ssh pi@ark.local                       # password: in staging/pi-password.txt (or your key)

sudo ark-mode status                   # which network is it on right now, and who chose it
sudo ark-mode home                     # use the home Wi-Fi, and keep it that way (pin)
sudo ark-mode hotspot                  # broadcast The-Ark-Offline, and keep it (pin)
sudo ark-mode auto                     # forget the pin; try home, else broadcast
sudo ark-mode test 180                 # hotspot for 3 min, then back to auto (SSH drops)

sudo ark-import-books --status         # how many books are on the shelf
sudo ark-import-books                  # scan the drop folders now (normally automatic)
sudo ark-import-calibre-library PATH   # adopt a whole Calibre library
sudo ark-zim-resolve --profile full    # queue more offline content
sudo ark-tls-cert --show               # the owner's-door certificate fingerprint
sudo ark-admin-recover --list          # locked out? console only, never the web

tail -f /srv/ark/logs/zim-download.log # watch downloads
tail -f /srv/ark/logs/net-watch.log    # watch the box's own network decisions
systemctl list-units 'ark-*' --all     # every Ark service and its state
sudo reboot
```

---

## 9. What it deliberately does not do

- **It does not touch the internet.** No telemetry, no cloud, no account anywhere off this box.
  Content is fetched only when you ask for it, by the machine running the downloader.
- **Reading needs no login.** Anyone on the Wi-Fi can read, post and upload — that is the point in a
  household emergency, and it also means the board and the File Box are not private. There is
  exactly one sign-in on the box: the owner's door on port 8443, which changes the machine itself
  and is never served on the portal address. A visitor never sees it, and nothing a visitor does
  asks them who they are. The owner can pause new notes and new uploads with one switch, and can
  hide or remove something that should not be there — but never learns who wrote it.
- **It does not need JavaScript** for anything that matters: search, reading, uploading and
  posting are plain HTML forms — and so is the owner's door.
- **It is not a backup.** Everything lives on one SD card. Keep the originals on the PC, and keep
  a copy of the library folder somewhere else.
- **Not built yet** (candidates, not promises): a planting calendar, an offline map pack, a photo
  archive of the house and its shut-off valves.

---

## 10. Notes for whoever maintains it

- Everything lives under `/srv/ark` — `kiwix/` (collections), `calibre-library/` (books),
  `vault/` (manuals and PDFs), `drop/files/` (File Box, the only folder the web server is allowed
  to serve), `drop/tmp/` (half-arrived uploads, never served), `calibre-incoming/` (staging, with
  `.imported.json` and `done/`), `portal/` (front page), `logs/`.
- The owner's door keeps its accounts in `/srv/ark/api/data/ark.db`, with the marker file
  `ark.installed` beside it (that marker is what turns a wiped database into
  "recovery-required" instead of a fresh default password). Its certificate lives under
  `/etc/ssl/ark/` (`ark-admin.crt`, and `ark-admin.key` at 0600 root:root), and the only offline
  way back in is `/usr/local/sbin/ark-admin-recover` (root-only, console-only).
  `docs/ADMIN.md` is the whole story.
- Provisioning is phase-based and idempotent: `scripts/03-provision.sh <host> --only <phase>`.
  Phases: `10-base` `20-storage` `30-calibre` `40-vault` `50-kiwix` `60-portal` `65-admin`
  `70-network` `90-verify`.
- Every service is `enable`d and started, so a cold boot with nobody present still produces a
  working hotspot and portal. The expected unit list is in the repo README.
- The book importer is a `systemd` path unit (`ark-book-import.path`), not a timer — it reacts to
  a file landing instead of polling on a schedule.
- The network watchdog is `ark-net-watch.service` (oneshot) driven by `ark-net-watch.timer`
  (first check 3 minutes after boot, then every minute). It keeps its counters in
  `/srv/ark/logs/net-watch.state` (`lost_streak`, `home_streak`, `home_block_until`) and its
  decisions in `/srv/ark/logs/net-watch.log`. The decision itself is a pure shell function, so
  `ark-net-watch --decide MODE LINK HOTSPOT IN_RANGE CLIENTS PINNED LOST BACK BLOCKED` prints the
  answer without touching the radio, and `--dry-run` prints what it sees and would do. A pin lives
  in `/etc/ark/mode.pinned`; `ark-mode` writes it, `ark-mode auto` removes it, and
  `ark-mode hotspot --auto` is the rescue path that deliberately leaves none.
- Non-negotiable project rules: SD-card guard before any flash, no secrets in git,
  `set -Eeuo pipefail`, LF line endings, and no jargon on any page a household will read.
- **Building another one?** Start from `docs/REBUILD-ME-ARK.txt` — the full blueprint,
  including the traps that cost the most time the first time. Hand it to any capable
  assistant along with this repo and it can rebuild the box from an empty card.
- Settings live in one file on the box: `/etc/ark/ark.env` (hotspot name and channel,
  content tiers, download rate limit, free-space reserve). Edit, then re-run the
  matching phase.

*If a future change makes a page above wrong, fix the page the same day — a guide that lies is
worse than no guide.*




