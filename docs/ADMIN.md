# ADMIN — the owner's door

How to claim the box, sign in, add people, recover access, and what is still
missing. Everything here was tested on the development machine against the real
application code; see the "Tested / not tested" section at the end.

---

## 1. What is public and what is not

| | Public reader (no account) | Administrator |
|---|---|---|
| Read manuals, quick cards, books, encyclopedias | yes | yes |
| Use the one search box | yes | yes |
| Read the message board, download shared files | yes | yes |
| Post to the board, upload to the file box | yes — open by default; the owner may switch it off (§7) | yes |
| Pin / sort out / hide a message, hide a file | no | yes (§7) |
| Delete a shared file for good | no | yes, with the owner's own password (§7) |
| Add/edit/remove administrators, read the audit history | no | yes |
| See what actually works, and why it does not | no — the front page shows one plain sentence | yes (§8) |
| Play the games; keep saves in this browser | yes | yes |
| Use a name for game saves; export/import them | yes, on the separate player origin (`:8445`) | yes |
| Hide/show a game, allow player names, allow automatic additions | no | yes (§10) |
| Change the box's settings | no | only the Community and Games switches so far (§7, §10) |
| Keep a copy of the records, or put one back | no | yes, from the box's own console (§9) |

The public portal never serves `/admin` at all: on port 80 it answers `404`.
Administration is a separate HTTPS origin on port 8443, and while the box is
unclaimed a second listener on `127.0.0.1:8444` is the only way in.

---

## 2. Claiming a brand-new box

1. Connect to the box as the owner (SSH over the home network, or the console):

   ```bash
   ssh -N -L 8444:127.0.0.1:8444 pi@ark.local
   ```

2. Leave that running and open **https://localhost:8444/admin** in the same
   computer's browser.

3. Your browser will warn about the certificate (it is the box's own — see §5).
   Check the fingerprint first (§5), then continue.

4. Find the temporary password on the box (it is generated at first start;
   there is no password printed in this repository):

   ```bash
   ssh pi@ark.local 'cat /srv/ark/api/data/bootstrap-password'
   ```

   Then sign in:

   * user: `admin`
   * password: the line that command printed

5. The box immediately asks for your own password. At least 12 characters; a
   short sentence is ideal; up to 1024 characters are kept in full (nothing is
   truncated). The temporary password stops working as soon as you save.

6. Close the tunnel. From now on use **https://ark.lan:8443/admin** from any
   device on the network.

While the box is unclaimed, step 4 only works through the tunnel — a stranger
on the hotspot cannot reach the temporary password's door at all. The public
reading pages keep working the whole time.

---

## 3. Accounts

* **Add**: Administrators → **Add an administrator**. Enter the name, your own
  password (re-authentication is checked on the server), and the box shows a
  one-time password **once**. Write it down and hand it over in person. It is
  never stored in readable form, never logged, and never shown again.
* **First sign-in**: that person is asked to choose their own password, exactly
  like the first administrator. They may do that over the network — only the
  box's *first* claim needs the tunnel.
* **Reset**: **Manage → Give a new temporary password**. All of that account's
  sessions end immediately.
* **Switch off**: **Manage → Switch this account off**. History keeps the name,
  and a switched-off name cannot be silently reused by someone else.
* **Last administrator**: the box refuses to switch off the last enabled
  account that has a real password. An account still waiting on its first
  password does not count. The check runs inside the database transaction, so
  two simultaneous clicks cannot both succeed.
* **Your own password**: **My password** — needs the current password, ends
  every other session for your account.

There is one role ("Administrator") on purpose: no hierarchy to get wrong.

---

## 4. Sessions, timeouts and guessing

| Rule | Value |
|---|---|
| Idle timeout | 30 minutes |
| Absolute lifetime | 8 hours from sign-in |
| On restart of the service | every session ends (sign in again) |
| Cookie | `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/admin`, no `Domain` |
| Stored form | only a SHA-256 digest of a 256-bit random token |
| Wrong passwords | 5 tries free, then a wait that doubles from 60 s to at most 15 min |

Failures are counted per account *and* per client address, and a successful
sign-in clears the count — so nobody is permanently locked out by a typo. The
address is the one nginx observed, not a header the client can invent.

If the box's clock jumps backwards the session is treated as ended rather than
extended; if it jumps forward past the 8-hour mark you sign in again.

---

## 5. The certificate

The box makes its own certificate locally (no internet, no account, no ACME):

```bash
sudo ark-tls-cert --show      # prints the SHA-256 fingerprint
sudo ark-tls-cert             # create, or renew when < 30 days remain
sudo ark-tls-cert --force     # replace now — the fingerprint changes
```

It covers `ark.lan`, `ark.local`, `ark`, `localhost`, `10.42.0.1`, `127.0.0.1`
and `::1`.

**Trust it deliberately, never blindly:**

1. Get the fingerprint from an authenticated channel — the SSH/console command
   above, or the output of `pi/steps/65-admin.sh` during installation.
2. Open `https://ark.lan:8443/admin`; the browser shows the certificate warning.
   Compare the fingerprint it displays with the one from step 1.
3. Only if they match, add an exception for that one address on that one
   device.

Never turn the warning off globally, and never install the certificate on a
device you do not control.

*Renewal*: run `sudo ark-tls-cert` — it does nothing until 30 days remain. The
certificate is valid for 825 days. *Clock*: a box that has been off for months
can come back with the wrong date, which makes the certificate look invalid.
Fix the clock first (`timedatectl` on the box, or let it see the home network
for a minute), then reload the page.

---

## 6. Recovery when every password is lost

Only from the box itself — there is no "forgot password" link, and no universal
default password:

```bash
sudo ark-admin-recover --list
sudo ark-admin-recover --username admin
```

It prints a temporary password for that one account, switches the account back
on, ends its sessions, and requires a new password at the next sign-in. The
event is written to the audit history **without** the password. The command is
`0700 root:root`, lives outside `/srv/ark`, and is not reachable from the web
interface or the hotspot.

If the database is gone but the marker file
`/srv/ark/api/data/ark.installed` survives, the box treats itself as
*recovery-required* and will **not** recreate the bootstrap account. Use the
command above.


---

## 7. Community — the board and the shared files

One page, **Community**, in the admin navigation (`https://ark.lan:8443/admin/community`).
It shows the same messages and files a reader sees, plus the controls to do
something about them.

### Two switches

| Switch | On (default) | Off |
|---|---|---|
| Anyone nearby can post a message | anyone on the Wi-Fi can add a note | new notes are refused with a friendly page; every note already written stays readable |
| Anyone nearby can add a file | anyone on the Wi-Fi can put a file in the box | new uploads are refused; everything already in the box stays downloadable |

Neither switch hides anything, and neither touches the library, the manuals or
the books. Use them when the board or the box is being misused by someone you
cannot identify — that is the honest limit of an open network.

### Messages

Three reversible actions per message, shown as buttons next to it:

* **Pin to top** / **Unpin** — a warning stays above everything else.
* **Sorted out** / **Still open** — marks the answer, and moves it below the
  open items so the next person reads what is still needed first.
* Hide / **Show again** — a hidden note disappears from the reading page but is
  kept on the card, so hiding is never a mistake you cannot undo.

Every action writes one line to the history (**What has happened**), with your
name, the message number and the outcome.

### Shared files

* **Hide / Show again** — same rule: hidden from readers, kept on the card.
* **Delete for ever** — the only action with no way back. It asks for **your own
  password** again, removes the file from the card and its line from the list,
  and records the deletion in the history. If the file was also imported as a
  book, the book stays on the shelf; only the shared copy goes.

### What the page deliberately does not do

It cannot edit anyone's words, it cannot tell you who wrote something, it does
not paginate beyond the last fifty items, and it has no undo for a delete. Say so
plainly to whoever asks instead of promising a control that is not there.

---

## 8. What is working — the readiness page

`https://ark.lan:8443/admin/health`, or **What is working** in the navigation.
Open it first whenever something feels wrong. It answers one question — *can a
visitor actually use this part of the box right now?* — for every part, and it
says **why not** in a sentence you can act on.

| Part | Ready means |
|---|---|
| Offline Library | collections are on the card **and** the reader answered on the box itself |
| Books | the shelf has a real catalogue **and** the shelf page answered |
| Manuals & Guides | the manual pages are installed (plain files — nothing to serve) |
| Message Board | the board can be read (whether or not posting is switched on) |
| File Box | the shared folder exists and can be opened (whether or not uploads are on) |
| Space on the card | at least 512 MiB is still free |
| Latest copy | a finished copy of the records exists and is less than a week old |
| Incoming collections | work in progress — reported, but never counted as a fault |

Two distinctions the page is careful about, because they are the whole point:

* **Content versus working.** A section with nothing on the card yet is
  *Not ready* but not broken ("No collection has arrived yet"); a section whose
  files exist while the program that serves them does not answer is the real
  fault ("3 collections are on the card, but the reader is not answering").
* **Downloading is not a fault.** A queue that is working, paused, or empty
  never makes the box un-ready. Only a queue that has gone silent for over two
  minutes is reported, and even then it does not block reading.

Each entry ends with **What to do:** when there is something to do, and the
page states plainly that *nothing on this page has been changed* — it only
looks. It never starts, stops or deletes anything. Reload to check again; the
cached answers are dropped each time the page is opened, so what you see is
what the box answered a second ago.

Under **Space on the card** the page shows where the room went, measured folder
by folder: *Offline Library*, *Books*, *Manuals & Guides*, *File Box* and
*Settings and records* — and only the folders that actually hold something,
because five rows of "0 B" tell you nothing about what to delete. Under the list
is the measured total, and, when the filesystem answers, the card's own figure
("The card holds 46 GB in all, the operating system included"). Nothing in that
list is an estimate: a folder the box could not read is left out rather than
shown as zero. The same numbers are in `http://ark.lan/api/status` under
`sections` → `storage` → `facts` for anyone who prefers JSON.

The same verdict is machine-readable at `http://ark.lan/api/ready` — **200**
when every part a reader needs works, **503** when one does not, with the
identical reasons in the body. `http://ark.lan/api/health` is deliberately *not*
a verdict: it says only that the backend answered, and points at `/api/ready`.

The route works by asking the loopback addresses (`127.0.0.1:8081/kiwix`,
`127.0.0.1:8083`) for a page. Any HTTP answer counts as "answering" — a 404 is a
running server with a moved page, which is a different problem from a refused
connection. The public pages are unaffected by all of this: nothing here is on
the reader's path.

---

## 9. Copies of the records — and putting them back

Everything the box cannot rebuild lives on one SD card: the messages, the shared
files, the accounts, the Community and Games switches, the list that stops a
re-dropped book being imported twice, and every named player's game saves.
`ark-backup` writes one dated folder holding all of it; `ark-restore` puts it
back. Both run on the box's console; neither is a screen, and nothing on this
page takes a copy for you.

```bash
sudo ark-backup --to /srv/ark/backup        # take one (about a second per 100 MB of shared files)
sudo ark-backup --list                      # what is already there, with sizes and ages
sudo ark-backup --verify /srv/ark/backup/ark-backup-<date>-<time>   # re-read every byte
sudo ark-restore --list                            # what could be put back
sudo ark-restore --from /srv/ark/backup/ark-backup-<date>-<time>    # put it back
```

**What a copy holds:** `database/ark.db` (taken with SQLite's own backup, never a
file copy — the live database is in WAL mode), `files/` (the shared files
themselves), `arcade-saves/` (named players' game saves, `0600` in `0700`
folders), `calibre-incoming/.imported.json` (the "already imported" list),
`manifest.json` (a sha256 and a size for every file) and `RESTORE-ME.txt` (the
same instructions, inside the folder, in case this page is not to hand). Shared
files may be stored compressed inside the bundle; `--verify` proves them and a
restore puts back the original bytes.

**What it deliberately leaves out:** the offline library, the book shelf and the
game files themselves — they are content and are fetched or imported again — the
programming environment, and the Wi-Fi secrets in `/etc/ark`. A copy is small
because of it.

**Two rules make it safe to trust.** The folder is built as
`<name>.partial` and only becomes `<name>` by a rename, so a run that dies never
looks like a finished backup; and `--verify` reads every byte back against the
manifest, because a backup nobody has verified is a hope.

**What a restore will not do.** It refuses a damaged folder before touching
anything. Before it writes, it pauses every writer on the box (the web service
and the book importer) and restarts what it stopped even if the restore fails;
`--no-service` is for a card mounted in another computer's reader, where there is
nothing to pause. It refuses an occupied box (messages, shared files, accounts,
player names or game saves already there) unless you add `--force`, and says so.
It never replaces a shared file that is already on the box — a restore only adds
what is missing, so `ark_storage.spool()` stays the only writer of a new file.
A game save already on the box is likewise kept rather than replaced unless the
restore is forced, and a missing one is added `0600` in `0700` folders. If the
book shelf is empty it leaves the import list beside it as
`.imported.json.from-backup`, because a list of "already imported" books with no
books would hide every book from the importer for ever. `--dry-run` says what it
would do and writes nothing.

**A copy on the box is not survival insurance.** If the card dies, a folder on
that card dies with it. Copy `/srv/ark/backup/ark-backup-*` to a USB stick or a
computer — the printable sheet at `/srv/ark/RECOVERY-SHEET.txt` (also readable at
`http://ark.lan/recovery.txt` while the box still runs) is written for exactly
that day: a phone, a card reader and a working card.

There is **no timer**, on purpose: a nightly backup that quietly fills the card
the box reads from is a defect, not a feature. Take one when something happened —
after a busy week on the board, before a big upload, before changing anything.
`--keep 2` keeps the newest two and deletes older ones.

---

## 10. ARK Arcade — the game shelf

The shelf is public at `http://ark.lan/arcade/`. A guest taps a game and plays;
the player is EmulatorJS, vendored on the box, and the page has no external
script, font or tracking pixel. Unplug the internet and it still plays.

**Player names are not administrators.** A name is optional and only keeps game
saves. It has its own database tables, its own `ark_player` cookie and its own
HTTPS origin:

```
https://ark.lan:8445/arcade/player
```

The public portal answers a player page with a redirect to that origin; the
administrator origin (`:8443`) does not serve `/arcade` at all, and the player
origin answers `404` for `/admin`. A visitor cannot claim the secure origin by
inventing `X-ARK-Player-Origin` on port 80 — nginx blanks that header there and
sets it only on `:8445`.

**On the owner's Games page** (`https://ark.lan:8443/admin/arcade`):

* every game, its console, its licence and whether it is on or off the shelf;
* **Hide from shelf** / **Show on shelf** — hiding is reversible and deletes
  nothing; a hidden game's file is no longer handed out;
* every suggestion the weekly check recorded, with the source's stated licence
  and the reason it was refused;
* **Check and add** — downloads and proves one suggestion, then publishes it;
* **Put away** / **Allow again** — records the owner's answer without deleting
  anything;
* **Anyone can use a name to keep saves** — off means no new names and no new
  sign-ins, and an already-signed-in browser can no longer read or write a save;
  guests can still play, and every save stays on the box for when the switch is
  turned back on;
* **Add newly found games without asking me** — off by default. Even when on,
  the same licence, allowlist, size, SHA-256 and file-shape checks apply.

**Where the games come from.** Only the curated Homebrew Hub source
(`hh3.gbdev.io` API and its `gbdev/database` raw files). The download URL is
rebuilt from validated slug + filename, redirects are refused, the address must
stay under the one allowlisted prefix, the body is capped at 64 MB, and nothing
is published until its SHA-256 and its console header agree. The first shelf is
`pi/arcade/catalog/seed.json`, three pinned files with hashes recorded when they
were fetched; a changed upstream file is refused rather than silently updated.
Collections and the game files themselves are content and are fetched again;
named players' saves are state and are included in a backup.

**A game has to be in English, or have no words at all.** The language and the
evidence for it are recorded beside each game and each suggestion. A suggestion
with no stated language, no evidence, a conflict in the source or a language
other than English is held as *review* with that reason shown on the Games page;
it is not installed. A wordless game is allowed. A game file you supply yourself
goes through the same questions (`sudo ark-arcade --import <file>` asks for title,
console, licence, language and evidence) — ownership of the file does not skip
the proof that it may be copied.

**A player's choices belong to the name.** A named player's favourite games sort
first on their shelf; autosave, favourite and last-played are stored with the name
and follow it to another device. They are preferences, not saves: a guest sees
A–Z and writes nothing, and no player's choice can hide a game the owner left on
the shelf.

**Saves and copies.** A guest's save lives in that browser (EmulatorJS local
storage). A named player gets three server slots per game, stored under
`/srv/ark/arcade/saves/<player>/<game>/` with `0700` folders and `0600` files.
The player page exports every save as one zip and puts one back; an imported zip
may only contain `saves/<game>/<state-NN.state|battery.srm>`, the game must be on
the shelf, and every path that climbs is skipped. A stale browser tab cannot
overwrite a newer save — the API refuses it with a plain sentence and asks for a
reload. Restoring a backup adds a missing save and never replaces one already on
the box.

**Commands** (console only):

```bash
sudo ark-arcade --status
sudo ark-arcade --seed
sudo ark-arcade --discover --auto
sudo ark-arcade --list
sudo ark-arcade --install <id>
sudo ark-arcade --refuse <id>
```

`ark-arcade-discover.timer` runs 15 minutes after boot and then weekly; the
service tries the pinned seed first (a failure is non-fatal) and records what the
source offers. Installation does not happen unless the owner switches it on.

---

## 11. Not in this release yet

Stated plainly, because a missing control is better than a fake one:

* Uploads: 2 GiB per file, one at a time, anonymous. There is **no per-person
  quota**, no approval queue and no quarantine — anything uploaded is
  downloadable at once. Uniqueness and safe naming are done (three files with the
  same name no longer overwrite each other).
* Deleting a shared file is for ever; there is no trash and no undo.
* No download pause/resume buttons in the page (the existing `ark-zim-pause`
  file still pauses the queue), no backup or restore *screens* (both are console
  commands, §9), and nothing on
  **What is working** can be changed from the page — it reports the state of the
  box, it does not alter it. The space breakdown says *where* the room went, not
  how to get it back.
* No network controls in the page. Broadcasting the hotspot, going back to the
  home network and reading what the box decided are still SSH jobs — the box
  answers them itself the rest of the time:

  ```bash
  sudo ark-mode status        # mode, address, portal answer, and who chose
  sudo ark-mode auto          # forget any pin; try home, else broadcast
  sudo ark-mode hotspot       # broadcast, and keep it that way (a pin)
  sudo ark-mode home          # back to the home network (a pin)
  sudo ark-net-watch --dry-run             # what the watchdog sees, changes nothing
  tail -n 20 /srv/ark/logs/net-watch.log   # what it actually did, with times
  ```

  `/srv/ark/logs/net-watch.log` is written only when the box *did* something, so a
  short log is a quiet, healthy day; the timer's own state is
  `systemctl status ark-net-watch.timer`. The box does fail over on its own when
  the home network disappears, and returns on its own when it comes back and
  nobody is connected — that is automatic, not a control you can see here, and a
  pin you set with `ark-mode home`/`hotspot` is never overruled.
* No household page, no "download the essentials" pack, no QR cards, no
  theme/text-size controls yet.
* Arcade: no per-player save quota or trash (a deleted save is for ever), no way
  for an owner to reset or delete a player name, and no "check for new games"
  button on the owner's page — the weekly timer does the check. A forgotten
  player password cannot be recovered; the person makes a new name, and the old
  saves remain on the box under the old one.
* Emergency content review (first aid, water, electrical) is still pending.

The audit history, the account rules above, the transport boundary, the
Community controls and the readiness page are complete and tested.

---

## 12. Tested / not tested

**Tested** — 231 automated tests, disposable databases, `pytest` (the per-requirement verdict is `docs/REQUIREMENTS-MATRIX.md`):

* bootstrap account exists once; a wiped database becomes recovery-required
  instead of re-creating the default; the marker lives outside the database;
* LAN clients cannot reach the bootstrap login or the password-setup step;
  forged `X-Forwarded-For` / `X-ARK-Setup-Channel` headers do not help;
* the temporary password is refused as a new password; 12-character minimum;
  confirmation mismatch; atomic one-shot setup; the old session dies;
* CSRF required on every POST; plain HTTP refused; security headers present;
* a hash written by the Pi (hashlib/OpenSSL) verifies on the development
  machine and vice versa — compared with a fixed vector;
* add/reset/disable administrators; temporary passwords never reach the audit
  log; disabled accounts lose their sessions; last-administrator protection;
* idle timeout, absolute lifetime, restart invalidation, backward clock jump;
* throttling after five wrong passwords, and recovery afterwards;
* the board's invalid-post 500 is fixed (now a friendly 400 with the same
  wording a person can act on);
* migrations are idempotent and preserve an existing board and file list, and
  migration 2 adds the moderation columns to a database that already has rows;
* uploads: three files with the same name keep three copies; `../` in a filename
  cannot leave the folder; an oversized or empty file is refused with a sentence
  a person can read; a crashed upload leaves nothing in the served folder;
* the File Box and the board honour both switches (off ⇒ refused, nothing lost);
* moderation: pin / sorted out / hide and their inverses; every action audited;
  an unknown action changes nothing; a hidden message is gone from the reading
  page but still in the database;
* deleting a file needs the owner's own password, refuses a wrong one, and can
  only remove a file that sits in the shared folder;
* the book importer: one import per content, `done/` never rescanned, a File Box
  copy left in place so its link survives, a failed import offered again;
* search: a hanging collection cannot hold the page past its budget; one shared
  pool, bounded number of collections; the numbers in `ark.conf` and
  `ark_storage.py` agree, and the library engine no longer listens on every
  address;
* readiness: an empty box names what is missing and what to do; a collection is
  only ready when the reader actually answers (a silent loopback address is
  *not ready*, a live one is); an index with no books, and a catalogue that is a
  0-byte file, are both told apart from a working shelf; a full card is named as
  the reason; downloads never make the box un-ready, but a queue silent for over
  two minutes is reported as stalled;
* progress: 200 finished bytes plus 100 in flight out of 1000 is **30 %** with a
  14-second estimate at the measured rate; an unknown total reports `null`, never
  0; `/api/ready` answers 503 with a reason and an action for every part,
  `/api/health` says it is liveness only, and `/api/status` carries the same
  reasons;
* the owner's **What is working** page lists every part with the reason and the
  next step, contains none of the jargon the rest of the box hides, and the
  overview links to it;
* the space breakdown measures each folder, lists no empty folder as "0 B", and
  the owner's page shows the measured size (4.0 kB for a 4096-byte collection);
* the network watchdog's decision table (14 cases): one lost check is a blink and
  two is a network, a reader on the hotspot is never disconnected, an uncountable
  client list is treated as occupied, the owner's pin is respected but never
  leaves the box dark, and a failed return is not retried immediately; the
  observer, against a fake `nmcli`/`iw`; a dry run that writes nothing at all;
  and the real call to `ark-mode hotspot --auto` (recorded, logged, state filed);
* arcade: a guest reaches no named save (401) and the game page carries no
  external address; a named player's save round-trips, a stale tab is refused
  with 409, another name sees 404, a missing page token is 403, export/import
  works and a climbing zip path is skipped; a hidden game has no page and no
  ROM; the emulator route cannot walk out of its folder; the licence, allowlist,
  redirect, archive and renamed-web-page gates each refuse; the pinned seed dry
  run touches nothing; the owner can hide/show a game and close player names;
  the owner's Games page and both switches;
* copies: one folder holds the records, the shared files, the named players'
  saves and the import list; a damaged copy is refused instead of restored;
  a half-written one is swept and never listed; only the newest two are kept; a
  restore puts everything back in the same places, refuses an occupied box
  without `--force`, never replaces a shared file, and keeps the import list
  from hiding books on an empty shelf; a
  manifest that points outside the box is refused; a dry run writes nothing; the
  recovery sheet needs no words looked up, and **Latest copy** on the readiness
  page says *not ready* only when there is no copy or it is over a week old —
  without ever blocking a reader.

**Not tested** — no hardware or browser access in this environment:

* anything on the Raspberry Pi: the new nginx listener, the certificate, a real
  browser warning, a phone on the hotspot, the SSH tunnel end to end;
* the loopback binding of `kiwix-serve` as a *running* service (the unit is
  checked as text only);
* `ark-tls-cert` itself (the script passes `bash -n` and uses standard
  `openssl` calls, but it has not been run on the Pi);
* `ark-admin-recover` against the live database — reviewed, not executed there;
* a full 2 GiB upload across the real network (the cap is tested with a smaller
  limit; the wire behaviour is nginx's);
* the readiness probes against the *real* library and shelf on the Pi (the tests
  use a local stand-in server, so "answering" and "refused" are both exercised —
  but no test has yet seen `kiwix-serve` and Calibre-Web in their real states);
* the front page's amber/red dot and the download speed measurement against a
  real download (the arithmetic is tested with fixed numbers);
* the network watchdog on the box itself: the timer has never been enabled, no
  real access point has been asked for its station list, no home network has been
  switched off to watch the hotspot come up, no pin has been set by a human, and
  the 3-minute first check and 30-minute back-off have never elapsed in real
  time. Everything above is a table, a fake `nmcli`/`iw`, and `bash -n`;
* the backup tools on the box itself: no copy has ever been written there, no
  restore has ever been performed, `/recovery.txt` has never been opened in a
  browser, and the phase that installs them has only been syntax-checked. Both
  tools *have* been driven by hand on a Mac against throwaway folders — a copy,
  its verification, a restore into an empty box and a refusal on an occupied one
  all behaved — but macOS is not the box (there is no `pi` account there, so
  ownership was left alone);
* anything in the arcade on the Pi: EmulatorJS has never been played in a real
  browser on this hardware, no game has been fetched through the installer
  against the live source, the `:8445` player origin and its certificate have
  only been `nginx -t`'d, and no phone has signed in or kept a save. The success
  of the weekly discovery timer on a real network has also not been observed.

To install and verify this branch on the Pi, **copy the database aside first** —
`60-portal` restarts the backend and runs every migration through schema v4 in
place, and there is no downgrade path:

```bash
sudo sqlite3 /srv/ark/api/data/ark.db ".backup '/root/ark-db-before-v4.db'"
sudo bash /srv/ark/src/install-ark.sh --only 20-storage,30-calibre,60-portal,65-admin,70-network,95-backup
sudo bash /srv/ark/src/install-ark.sh --only 90-verify
```

(`70-network` installs the watchdog and starts its timer; nothing in it switches
the radio, so it is safe over the same Wi-Fi you are connected to. `95-backup`
installs the two copy tools and the printed sheet; it takes no copy and starts no
timer.)

Then work through §2 with a phone, open
`https://ark.lan:8443/admin/health` on a box that is still filling up — every
part that is empty says so without calling the box broken — and play one game
with the internet unplugged before trusting the shelf.

