# What a person still has to test on the box

Everything in this file is **working code that no human has touched**. The test suite proves the
logic, `90-verify` proves the doors, and a headless browser proved the games draw — but none of
that is a thumb on glass, a phone held at arm's length in daylight, a router switched off, or a
card put into a reader. Nothing here is a known defect; it is unproven, which for a box meant to be
somebody's last resort is the same thing until it is not.

Take the items in order. Each one says what to do, what you should see, and what to write down if
you do not see it. Do them **on the box**, not on a computer, and keep the notes: the honest result
of each one goes into `docs/IMPLEMENTATION-LOG.md` as a dated checkpoint.

A note on wording: this page is for the person doing the testing, so it uses plain words for what
the box does and technical words only where the thing being tested is technical. The box's own
pages never show the technical ones.

---

## 1. The first sign-in, through the tunnel (10 minutes)

**Why it is unproven:** the account was claimed once, by a script, on 25 Oct 2026. No person has
typed a password into the sign-in page.

1. On a computer on the same network, open a terminal and run
   `ssh -N -L 8444:127.0.0.1:8444 pi@ark.local`. Leave it open; it prints nothing.
2. Open `https://127.0.0.1:8444/admin` in the browser. Accept the certificate warning once.
3. Sign in with the name and password you set. Change the password when it asks, to something you
   will remember with no help.
4. Close the tunnel (Ctrl-C). From now on your door is `https://ark.local:8443/admin`.

**You should see:** the setup page only while the account is unclaimed, then the sign-in page, then
**What is working** with eight rows.

**Write down if not:** the exact screen, the exact sentence, and whether the tunnel was still open.
A page that loads but refuses the password is a different fault from a door that does not answer.

---

## 2. The eight sentences, read out loud (15 minutes)

**Why it is unproven:** nobody has read **What is working** on a screen. It was built so that every
row is a sentence a non-technical person can act on, and that claim has only ever been checked by
tests.

1. Open `https://ark.local:8443/admin` on a phone. Sign in.
2. Open **What is working**. Read all eight rows out loud. If any row needs a second reading to make
   sense, that row has failed.
3. On the same page, find the storage breakdown and compare it against the truth: on the box,
   `du -sh /srv/ark/kiwix /srv/ark/calibre-library /srv/ark/vault /srv/ark/drop /srv/ark/api`.
   They should agree within a few percent (folder sizes vs. "GB used").
4. Still on the phone, watch the front page `http://ark.lan/` on the hotspot. It must say the box is
   working, not show a red or amber dot, and it must offer **Copy Address**.

**You should see:** eight plain sentences, no service names, no port numbers, no percentages on the
owner page, and a front page that fits one phone screen without zooming.

**Write down if not:** the row, the sentence, and what you would have written instead.

---

## 3. Break something on purpose and read the answer (15 minutes)

**Why it is unproven:** the amber and red dots and the *"What to do:"* line have never been seen in
a browser on any device. Every readiness test runs against stubs.

1. On the box: `sudo systemctl stop ark-kiwix`.
2. In the owner page, open **What is working** and reload. The row for the Offline Library must say
   *Not ready*, in plain words, with a **What to do** line.
3. Try to search from the public page. It must say the library is not answering — not spin, not
   show a blank result, not show a stack trace.
4. Start it again: `sudo systemctl start ark-kiwix`. Reload. The row must go back to ready within
   ten seconds (probes are cached for ten).

**You should see:** a red dot and one sentence naming what to do, then a green dot and no drama.

**Write down if not:** whether the page or the box was wrong — `sudo ark-net-watch --dry-run` and
`systemctl status ark-kiwix` tell you which.

---

## 4. The message board and the File Box, as a person (20 minutes)

**Why it is unproven:** anonymous posting and uploading are exercised by tests against a throwaway
database, never by a phone on the hotspot.

1. On a phone on the hotspot (`The-Ark-Offline`), open `http://ark.lan/board`. Post a note with a
   name and without one. Both must appear.
2. Open the File Box and upload two files **with the same name** from two different folders. Both
   must appear as separate downloads, and both must open.
3. Upload an `.epub` into the File Box, then wait two minutes and look at **Books**. It must arrive
   on the shelf with no command typed.
4. In the owner page, open **Community**: pin one note, sort out another, hide one, then delete one
   with the password confirmation. Restore the hidden ones.
5. Switch **both** Community switches off. On the phone: reading, searching, downloading and
   playing must all still work; only *writing* stops. Switch them back on.
6. In **Community → Shared files**, delete one of your test uploads and confirm the download link is
   gone from the phone.

**You should see:** no sign-in anywhere, no error pages, and the File Box empty again when you have
finished (that is the state the box shipped in).

**Write down if not:** the exact refusal sentence. The File Box echoes one back on purpose, escaped —
if it comes back unescaped, that is a security fault, not a wording fault.

---

## 5. Play a game with the internet unplugged (20 minutes, the most important one)

**Why it is unproven:** this is the headline promise of the whole box and **no person has played a
game on it**. A headless browser on a computer drew two games from the box's own files; that is not
the same thing.

1. On the box: `sudo nmcli radio wifi off` (or unplug the router). The box may switch to its own
   hotspot — that is correct behaviour, not a fault.
2. On a phone, join `The-Ark-Offline`, open `http://ark.lan/arcade/`, tap a Game Boy game.
3. Tap **START GAME** once. The game must appear, run at a sane speed, and make sound after that
   tap (browsers refuse sound before a tap — that is why the button exists).
4. Play for two minutes. Rotate the phone. Lock it and unlock it. The game must survive.
5. Tap back, open a Game Boy Advance game, and play that too. Then a Game Boy Color game.
6. With the internet still unplugged, search the Offline Library for `water` and open one of the
   results.

**You should see:** a game, a working search, and no message about the network anywhere.

**Write down if not:** a **white box** with the game running underneath is the exact defect that
`EJS_startOnLoaded` caused; if it happens, the page started itself and the fix was undone. A black
box with a spinner is a different fault: note the console name.

---

## 6. A player's name, and a save that follows it (20 minutes)

**Why it is unproven:** no phone has ever signed in as a player or kept a save on the box.

1. On the phone, from a game page, choose a name and a password. Play, then save.
2. Open **My saves**. Export them (one file). Put the file somewhere you can find it.
3. On a **second** phone or in a private window, sign in as the same name: the saves and the last
   played game must be there.
4. Sign in as a different name: your saves must not be visible, and asking for them directly must
   fail.
5. Import the file you exported. Everything must come back.
6. Still on `https://ark.local:8445`, look at the padlock and compare the certificate with
   `https://ark.local:8443` — same certificate, same dates. Then open `https://ark.local:8445/admin`
   and confirm it is a plain "not found", not a sign-in.

**You should see:** saves that follow the name, and a door that does not exist.

**Write down if not:** whether it was the save or the cookie. A save that does not come back on a
second device but does in the same browser is a cookie-scope fault; the opposite is a storage fault.

---

## 7. Adding a game from a phone, for real (15 minutes)

**Why it is unproven:** a 64 MiB file has crossed this door from a computer (§22.2) and a duplicate
was refused by fingerprint live; **no genuinely new game has ever been accepted onto the live
shelf** through it, and no thumb has picked a file in a browser.

1. On a phone, open `http://ark.lan/arcade/add`.
2. Pick a homebrew game you have a copy of (a `.gb`, `.gbc`, `.gba` or `.nes` file).
3. Read the page it shows you: it says what the box made of the file. If it recognises it, the name
   and console will be filled in; if not, it says *"Nobody has listed this file"* and offers the
   console list.
4. Add it with the name you want. Then play it.
5. Try the **same** file again: it must refuse, naming the game it already has.
6. Leave the shelf as you found it if the game is not one you want to keep: the owner's **Games**
   page can hide it, and there is deliberately no delete button.

**You should see:** the game on the shelf, playing, and a refusal that names the game rather than an
error code.

**Write down if not:** the exact sentence. A refusal that says *"already on the shelf"* about a name
you never typed is the typed-name fault of §20 — note the name it quoted.

---

## 8. Pull the network out from under it (30 minutes, radio work)

**Why it is unproven:** the watchdog has only ever met a fake `nmcli` and a fake `iw`. Its real
transitions have never happened on hardware once.

1. With the box on your home Wi-Fi, switch the router off. Wait two minutes. Look for
   `The-Ark-Offline` on a phone and join it. The portal must answer at `http://ark.lan/`.
2. On the box, read what it did: `sudo journalctl -u ark-net-watch --since -10min` and
   `cat /srv/ark/logs/net-watch.log`.
3. Switch the router back on, with **nobody** connected to the hotspot. Within about five minutes
   the box must return home: `sudo ark-mode status` must say `mode: home`, `chosen by: auto`.
4. Now pin it deliberately: `sudo ark-mode hotspot`. Switch the router on and wait ten minutes. It
   must **stay** on the hotspot, because you said so.
5. Release it: `sudo ark-mode auto`.
6. The test that matters most: connect a phone to the hotspot, then switch the router on. The box
   must **not** drop your phone to go home while you are reading.

**You should see:** the box rescue itself, obey a pin, and never cut off a reader.

**Write down if not:** the last line of `net-watch.log` and the output of `ark-mode status`. A pin
that leaves the box unreachable is the one failure that would matter in an emergency.

---

## 9. Two gigabytes over the wire (30 minutes, needs a big file)

**Why it is unproven:** the proxy and the app agree on 2 GiB and a test keeps them agreeing, but no
file that size has ever crossed the wire.

1. Make a 2 GiB file on a computer: `dd if=/dev/zero of=/tmp/two-gib.bin bs=1m count=2048`.
2. On the hotspot, open `http://ark.lan/files` and upload it.
3. Watch the box while it runs: `df -h /srv/ark` must not go below 12 GiB free (the box reserves
   512 MiB and refuses rather than filling the card).
4. Delete it afterwards from **Community → Shared files**.

**You should see:** a progress bar that reaches the end, a file that downloads back at the same
size, and no 413.

**Write down if not:** the status code. **413** is nginx refusing a body; **507** or *"not enough
room"* is the app refusing; a hang past ten minutes is the proxy's read timeout.

---

## 10. Put the records back on a card, for real (30 minutes, destructive — use a spare card)

**Why it is unproven:** a bundle has been written and verified on the box, and `--dry-run` has
refused an occupied box. **No restore has ever replaced records.**

1. Take a fresh copy: `sudo ark-backup --to /srv/ark/backup`, then `sudo ark-backup --list` and
   `sudo ark-backup --verify /srv/ark/backup/<the newest one>`.
2. Copy that folder **off the card** to a computer (a copy on the same card is not insurance).
3. On a spare card flashed with the same image, with no services running: put the folder at
   `/srv/ark/backup/` and run `sudo ark-restore --from /srv/ark/backup/<name> --no-service`.
4. Boot it. Sign in. The notes, the shared files, the games' records and the named players' saves
   must all be there.
5. Do it once more on a **card that already has a little of its own data**, with `--force`, and
   check that a shared file of the same name on the card was **not** overwritten.

**You should see:** *"every fingerprint matches"* before anything is written, and a box that is
itself again.

**Write down if not:** the sentence `ark-restore` refused with. It refuses loudly and changes
nothing, which is the correct failure.

---

## 11. Paper (20 minutes, no computer needed)

**Why it is unproven:** nobody has opened the recovery sheet. It exists for the day the box cannot
boot, which is the worst possible moment to discover it is wrong.

1. Open `http://ark.lan/recovery.txt` on a phone. Read it as if the box were dead.
2. Print it. Fold it. Put it where you keep the spare card.
3. Follow it as far as you can without actually breaking anything: find the commands it names on a
   working system (`ark-backup --list`, `ark-restore --list`) and confirm they exist.

**You should see:** no service names, no port numbers, and nothing you would have to look up. If a
sentence needs a glossary, it has failed.

---

## 12. The plain-English test (any time, five minutes)

**Why it is unproven:** the wording rules are enforced by review, not by a test. Hand the phone to
somebody who has never heard of any of this and ask them to find a manual, read a page and download
a book. Say nothing. Watch where they hesitate.

**You should see:** no hesitation on the home page, no questions about accounts, no confusion
between *Books* and *Manuals*.

**Write down:** every word they read aloud as a question. Those are the words to change.

---

## What is *not* on this list, on purpose

* The v5 migration has run once, on the live database. It will be proved on a card that already
  holds v5 the first time a v6 exists — a migration that has never run on real old rows is a
  migration nobody has tested. Until then: **take a `.backup` before every deploy that migrates**
  (the rule already lives in `.clinerules` §8.9).
* Load, several readers at once, and a full card are measured by readiness and by `du`, not by a
  human. `tests/test_health.py` covers a full card; nothing covers twelve phones at once.
* Anything the box has not built yet (quotas, a trash, an approval step, a backup button, a "check
  now" button) is not a test item — it is absent, and `.clinerules` §8.6 says so.
