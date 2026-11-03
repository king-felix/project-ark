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
**What *is* proved (26 Oct 2026, log §29):** the API underneath it — a name made on the player
origin, a 45-byte save written, the bytes found on the card in
`saves/<player id>/<game id>/blobs/state-01-<sha256>.bin` (`700`/`600`), read back identical, a
second revision stored as a **second** file, a stale page refused with 409 and no cookie refused
with 401. What has never happened is a **person** doing it in a browser, on a phone, with a real
game's save. Everything below is that missing half.

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

**Why it is unproven:** the proxy and the app agree on 2 GiB and a test keeps them agreeing, and
**300 MiB has crossed the public door** on the live box (26 Oct 2026, log §29: 13.8 s, `/tmp` at
+0 MiB, the card +600 MiB, nothing left in `drop/tmp` or `tmp/nginx`) — but no file that size has
ever crossed the wire, and 2 GiB is where the reserve and the 64-bit paths start to matter.

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

## 13. The household page, the cards and the pack (30 minutes, needs a printer)

**Why it is unproven:** the deploy of 26 Oct 2026 proved what nginx and the API answer — the body
sentences, the 303 on the owner's page, a pack of `41.1 kB, 16 files` that `--verify` read back —
and nothing about a person using it. No phone has scanned a card, nothing has been printed, the
pack has never been opened on a device that is not the Ark, and not one of the nine answers has
been filled in (`docs/IMPLEMENTATION-LOG.md` §24.3).

1. Open `http://ark.lan/household` on a phone. Read the eight topics out loud and tap each page it
   names — every one must open. Every topic says *Nobody has read this yet*.
2. As the owner (`https://ark.lan:8443/admin/household`), press **I have read this today** on two
   topics with a short note, then reload `/household`: the two must now say somebody read it today,
   the count must move off zero, and the other six must still admit nobody has.
3. Press **Take that back** on one of them and confirm it returns to *Nobody has read this yet*.
4. Fill in **three** of the nine answers (where the water stops, who to ring, where the first-aid
   kit is) and reload the page as a visitor. The three must appear in your own words; the other six
   must say *Nobody has written this page yet* — never an empty line.
5. Open `/cards` and print it — or screenshot it — then **scan one square with a real phone
   camera, off paper**. It must open the portal. This is the one thing a test can only approximate.
6. Tap **Download the pages as one file**. Take the zip to a computer with **no network at all**,
   unzip it, open `START-HERE`, then `index.html`, then a manual from the list. Every link must stay
   inside the folder: no `http://ark.lan` left anywhere, nothing fetched, nothing broken. There is
   **no `cards/` folder inside and that is correct** — the cards are page 5's job, done in advance
   (`docs/IMPLEMENTATION-LOG.md` §24.4).
7. Turn **Show the household page to everyone** off. `/household` and `/cards` must go away for a
   visitor, `/essentials.zip` must still download, and the manuals, guides, books, games and File
   Box must be untouched. Turn it back on.

**You should see:** plain sentences in the household's own words, a date and never a percentage, a
card that scans, and a pack that opens from a folder with the network switched off.

**Write down:** any topic whose page does not really answer the question beside it, any answer that
reads oddly once it is on the page, and whether the printed card is big enough to scan from a wall.

---

## 14. The helper, with a model on the card (30 minutes — a model **is** fitted now)

**Where the box stands on 26 Oct 2026** (log §31): the engine is installed, a 1.5B model is on the
card, `/ask` answers real questions through nginx, and `/api/ready` reports the helper as ready.
Three questions were asked from the box's own shell and answered in 102–107 s; the streamed route
was read with `curl`. **What no machine has done:** drawn the page in a browser or a phone, used the
page's own script in a real browser, been asked twice at once, or read an answer aloud. Everything
below the page is covered by `tests/test_ai.py` (45 tests), including the one slot as a lease and the
cancel that frees it the moment a reader walks away (log §32).

**Also true since 26 Oct 2026** (log §32.5): a question whose reader goes away — closed tab, reload,
a phone that lost the hotspot — now **stops the engine** and frees the slot within a second, so
*somebody else is asking* can only ever be true while a real answer is being written, and it names
the seconds.

**Read this first:** an answer takes **one to two minutes** on a Pi 4. That is the box's speed with a
940 MB model and not a fault; the page says "this can take a minute on a small box" and nginx waits
300 s so a slow answer is never a 504.

1. Open `http://ark.lan/ask` on a phone. The box to type in must be there, the three example
   questions tappable, and **no** "not fitted" sentence — the helper is fitted on this card.
2. Tap *"How do I make water safe to drink?"* and wait. Words should appear as they are written
   (that is the page's script reading `/api/ask/stream`; it has only ever been read by `curl` on the
   box until now). The answer must be plain prose with **no** words from the banned list (no
   *Kiwix*, *ZIM*, *port*, *localhost*), and *"It can be wrong — the guides are the truth"* must be
   under it.
3. Ask *"what do I do about a deep cut"*. The answer must be first-aid advice, not a description of
   which page to read, and it must not invent an address like `/firstaid/` — the box reads
   `manuals/first-aid.html` for this question (that was a real defect on 26 Oct 2026, log §31.3).
4. Type a question 2,000 characters long: it must be refused **with the limit named** (never
   silently shortened, never a broken page).
5. Ask *"how many books are there"*: the number must match **Books** on the front page, not a number
   the model invented. Rebuild the facts first with
   `sudo -u pi /usr/local/sbin/ark-build-ai-brief --write` if the shelf has changed.
6. Open `https://ark.lan:8443/admin/health`: the helper's row must say what is fitted, and it must
   **never** be able to make the box look broken — move `models/*.gguf` aside and `/api/ready` must
   still answer 200 with the helper's row not ready.
7. In **Community**, turn *"Anyone nearby can ask the helper a question"* off. `/ask` must refuse
   **and** `/api/ask/stream?q=hello` must refuse — type that address by hand, because a page that
   closes while the address stays open is exactly the hole this step is for. Turn it back on.
8. Ask the same question from two phones within a second of each other: one gets words, the other is
   told somebody else is asking — **with the number of seconds to wait** ("in about 40 seconds"),
   not just "a minute". Then ask about the household's nine answers: the helper must not know them,
   and must not quote one back.
8a. **Close the tab in the middle of an answer, then ask again straight away.** This is the defect
   that made the page say *somebody else is asking* when nobody was (log §32). The second question
   must be **answered**, not refused, and it must not matter that the first answer never finished.
8b. At the box's own keyboard, while nothing is being asked:
   `curl -sN --max-time 6 "http://127.0.0.1/api/ask/stream?q=tell+me+about+water" >/dev/null` and
   then `sudo ss -tnp | grep :8089` **at once** — the connection must be **gone** (before the fix it
   stayed `ESTABLISHED` for the whole answer) — and `curl "http://127.0.0.1/ask?q=hello"` must begin
   answering instead of returning the *somebody else* sentence.
9. **Optional, and it will be slow:** Gemma 4 E2B (3.3 GB) is still in `/srv/ark/ai/models/` and the
   **newest** `*.gguf` is the one used — Gemma is older, so the 1.5B answers. At 2.3 tokens/s Gemma
   cannot answer a page in under five minutes and it thinks before it writes, so it is not the model
   a page can wait for (log §31.2). Leave it, or delete it to free 3.3 GB.

**You should see:** one plain answer, made on the box, in about a minute or two, with the guides as
the authority and the household's own words never leaving the page they were typed on.

**Write down:** roughly how long the first answer took on a phone, whether the words appeared
gradually or all at once, whether any answer named a page that does not exist, and any question where
the helper answered *about the box* instead of about the question.

---

## 15. Pull the plug (30 minutes, the one that cannot be tested any other way)

**Why:** every other test in this repo runs against a throwaway folder tree. `ark-self-heal`
has never met a real power cut, the forced filesystem check has never run on this card, and
the hardware watchdog has never reset anything. Only the plug can prove those.

**Where the box stands on 26 Oct 2026** (log §29): phase `15-resilience` **has run** — every
setting is in place and was read back — and `fsck.mode=force panic=10` is on the boot line
**right now**, with `ark-fsck-once.service` enabled and waiting for the boot that will use it.
So **the next reboot of this box checks the whole card** (about 102 GB is in use: expect several
minutes with nothing on the screen — that is one boot only). If you would rather not pull the
plug today, a plain `sudo systemctl reboot` is still worth doing on its own: it clears the armed
check and is the only way to see the tmpfs folders mounted from `/etc/fstab` and the three
serving units come up through `local-fs.target` instead of the network. Steps 1–6 are the cut;
step 7 is the withdrawal.

**Before you start:** a copy that is **not on this card** (a stick, or a computer) — pull the
plug mid-write and you are proving the repair, not tempting fate. Note the time on a clock
you trust, and note what `/api/ready` says now.

1. Unplug the box **while it is doing something**: open `/search?q=water` on a phone and pull
   the power as the page loads. That is the harshest realistic moment — a small file is being
   rewritten and a database transaction is open.
2. Plug it back in and wait. **Do not press anything.** It will take longer than usual: the
   next boot checks the card (`fsck.mode=force`) — on this card that is **several minutes**
   with nothing on the screen, not a hang. That check is taken back off the boot line by the
   boot that used it — step 7 is what proves that.
3. On a phone, open `http://ark.local`. The portal must appear. Then `/api/ready` must answer
   **200**, or 503 naming a part that needs a look.
4. Over SSH:

   ```bash
   date                                    # the right time, not 1970 or the card's birthday
   sudo /usr/local/sbin/ark-self-heal --json | head -40
   tail -n 40 /srv/ark/logs/self-heal.log
   systemctl status ark-self-heal.service ark-self-heal.timer --no-pager
   ```

   **You should see** a pass that names what it repaired — most likely the clock, possibly a
   `.corrupt-*` file beside `ark_status.json` or the watchdog's counters. **Nothing should
   say "still to look at"** except a genuine fault, and nothing that was there before the cut
   may be missing.
5. Open the pages a person cares about: `/`, `/files`, `/board`, `/household`, `/cards`, one
   game, one book. Every one must work, and the File Box must still list the files it listed
   before the cut.
6. Do it a second time, and this time pull the plug **while a file is uploading** from a
   phone. After the reboot `/files` must not offer a half-file, and `/srv/ark/drop/tmp` must
   contain nothing (or only something newer than six hours). Look in `/srv/ark/tmp` as well:
   that is where a half-written *request* waits on its way in (the File Box's own temp folder is
   `drop/tmp`, one step further along). Anything left there is the web server's own file from
   the interrupted request — it serves nothing, the repair pass deliberately does not reach into
   the web server's numbered files, and `sudo du -sh /srv/ark/tmp` says whether it matters.
7. Read what the boot did about the forced check, then reboot once more and time it:

   ```bash
   cat /boot/firmware/cmdline.txt                 # fsck.mode=force must be GONE
   systemctl status ark-fsck-once.service --no-pager   # "Withdrew fsck.mode=force …"
   cat /srv/ark/ark_status.json | python3 -m json.tool | grep -A3 card_check
   sudo systemctl reboot                          # the second boot must be fast
   ```

   **You should see:** the words gone from the boot line after the first boot, and a second
   boot that is noticeably quicker than the one after the cut. If `fsck.mode=force` is still
   there, every reboot from now on checks the whole card — run
   `sudo systemctl start ark-fsck-once` once, then look again.
8. Leave it a day, then read `logs/self-heal.log` again: the 04:17 pass must have run and
   repaired nothing.

**Write down:** how long the box took to come back (and how long the second boot took), what
the pass repaired (verbatim), and anything that a person would have noticed as broken
afterwards. If a `problems` line ever mentions a database you had not touched, stop — that is
the one case where the repair tool should have left everything exactly where it was, and the
recovery sheet comes out.

**What this proves that nothing else can:** `fsck.mode=force` really runs and really goes away
again, `fake-hwclock` really keeps the time, tmpfs really has nothing to sweep, the watchdog
unit really still runs after a cut, and a half-written upload never becomes a file.

---

## 16. Boot it with nothing to connect to (15 minutes)

**Why it is unproven:** the portal, the library and the shelf now wait for the card to be
mounted and for nothing else. That was a change to when three units start, and no box has
been rebooted with the network unplugged since — which is the one boot the change exists for.

**Before you start:** nothing to prepare. This test is *supposed* to be boring.

1. Unplug the network cable, or switch off the router the box normally joins. Leave the
   hotspot alone: three minutes after the boot the network watchdog may start broadcasting,
   which is correct and is not what this test is about.
2. `sudo systemctl reboot` and start a timer.
3. **Without touching anything**, open `http://ark.local` on a phone on the home network — or
   `http://ark.lan` if the box has come up as its own access point. Time how long it takes to
   answer.
4. Over SSH, check the three units that used to wait for the internet:

   ```bash
   systemctl show -p After ark-api.service ark-kiwix.service ark-calibre.service
   systemctl list-units --state=failed --no-pager
   curl -s -o /dev/null -w '%{http_code}\n' http://ark.local/api/ready
   curl -s -o /dev/null -w '%{http_code}\n' http://ark.local/arcade/
   ```

   **You should see:** no `network-online.target` or `NetworkManager-wait-online` in those
   `After=` lines, no failed unit, and 200 from both addresses. The two units that *do* fetch
   something (the magazine queue and the weekly game check) are allowed to wait; they are not
   part of this.

5. Read a page and play a game without the internet, then plug the network back in and confirm
   nothing restarts and nothing is lost.

**Write down if not:** the whole `After=` line of the unit that was slow, and
`journalctl -b -u <that unit> -n 40`. A box that takes about a minute is usually still waiting
for a connection it does not need.

---

## 17. Carry it to another house, and get it on their Wi-Fi (20 minutes, two people help)

**Why it is unproven:** the **Network** page (`https://ark.local:8443/admin/network`) was written
on 31 Oct 2026 and has **never been opened in a browser**, on any device. Saving a network and
asking for one restart have both been proved by tests and by rehearsals on a laptop — never by a
phone in a kitchen with somebody else's router password on a sticker.

**Two things this test is really about**, and neither can be proved on a laptop:

* the **passphrase** never comes back out — not on the page, not in the records, not in a log;
* the **one restart** promise: the request is *used up*, not a change of habit. The start after
  it broadcasts `The-Ark-Offline` again, and a request naming a network that is not there leaves
  the box broadcasting instead of unreachable.

**Before you start:** you need to be signed in to the owner's console already (`§12` is where
that is set up), and you need a **second** Wi-Fi that the box can see — a phone hotspot is fine.

1. Open `https://ark.local:8443/admin/network` **on the phone**. Tap *Network* in the console's
   menu rather than typing the address if the menu has it.

   **You should see:** four headings — *Where the box is now*, *Restart on my own network for a
   while*, *Add a Wi-Fi network*, *Networks already saved* — and the first line under the first
   heading says where the box is in ordinary words, with no service name, no port number and no
   word you would have to look up.

2. Type the name and password of the **phone's own hotspot**, wrong on purpose in one way
   (seven characters, say) and save it.

   **You should see:** **Nothing was saved.**, with the number that is missing in the sentence —
   and the page still shows the form you filled in.

3. Now do it properly, and watch the phone's browser address bar as you type.

   **You should see:** the password characters as dots, and after saving, *Saved "…"* with the
   name spelled the way the phone spells it. **The password must appear nowhere on the page** —
   not in the form, not in the banner, nowhere.

4. Over SSH (this is the one step that needs a keyboard), check what the box really wrote:

   ```bash
   sudo ls -l /etc/NetworkManager/system-connections/
   sudo grep -E '^(ssid|autoconnect|autoconnect-retries|key-mgmt)=' /etc/NetworkManager/system-connections/<the new one>
   ```

   **You should see:** one file, **`-rw-------` and owned by `root`**, naming the network with
   `autoconnect=false` and `autoconnect-retries=0`. That is the promise that a box in a bag does
   not go looking for somebody else's router by itself. (The `psk` line is there too — that is
   correct, and it is why the file is `0600`; do not paste it anywhere.)

5. Back on the page, under *Restart on my own network for a while*: pick the network you just
   saved, leave the minutes at 30, **untick** the box, and press **Ask for one restart on my
   network**.

   **You should see:** a green line beginning **asked:** — *the next boot … joins "…" — once* —
   and then, lower down, *Waiting: …* with a **Take that request back** button.

6. Restart the box (`sudo systemctl reboot`) with the phone's hotspot **on**.

   **You should see:** after the reboot the box joins the hotspot instead of broadcasting, and
   `https://ark.local:8443/admin/network` from the phone reaches it over that hotspot, with
   *Where the box is now* saying it is on your network.

7. **The important step.** Restart it a **second** time.

   **You should see:** the hotspot back — `The-Ark-Offline` — and the page (once you are on it)
   saying the request is not waiting any more. **The request must be used up.** If it joined the
   hotspot again, the request has become a mode, which is the one thing this design must not do;
   write down `sudo cat /etc/ark/lan-request` (it should not exist) and `sudo ark-mode status`.

8. Ask for one restart naming a network that **is not there** — switch the phone's hotspot off
   first — and restart.

   **You should see:** the box broadcasting `The-Ark-Offline` after the boot, reachable the way
   it always is. **It must never be dark**: this is the lock-out that cannot be allowed, and it
   is why the request exists at all instead of a setting.

9. On the box, the three checks a gate can make:

   ```bash
   /usr/local/sbin/ark-mode status            # the "lan request" line: waiting / expired / nothing
   ls -l /usr/local/sbin/ark-wifi-add         # -rwxr-xr-x root root
   sudo bash /srv/ark/src/install-ark.sh --only 90-verify | tail -20
   ```

   **You should see:** *nothing waiting* (or a request you meant to leave), the helper present,
   and `90-verify`'s **Network page behind the sign-in** line green with *add a Wi-Fi network*
   and *one-time restart* reported softly. Anything else in the verify output is its own item.

**The one case a thumb cannot force, but you can read for:** if the box **cannot schedule its own
restart**, it keeps the request — the page then opens with **The request is kept.** instead of
**Nothing was asked.**, repeats the box's own reason (*restart it by hand*), and *Take that
request back* is still there. You should only ever see that on a card where the reboot could not be
arranged; three tests hold it either way (`docs/IMPLEMENTATION-LOG.md` §33.4). What must never
happen is **Nothing was asked.** printed above a **Waiting:** block offering to take a request
back — the banner denying what the page is offering to undo.

**Write down if not:** the exact green or red sentence the page showed (it is the box's own
wording), `sudo ark-mode status`, the contents of the page's own **Take that request back** line
if one is waiting, and `journalctl -b -u NetworkManager -n 40` for a join that failed. A page
that answers **Nothing was saved.** when the password was right is almost always the account the
portal runs as not being allowed to run the helper without a password — the question to ask is
whether **`pi`** can:

```bash
sudo -u pi sudo -n /usr/local/sbin/ark-wifi-add --ssid "test network" --check \
  <<<"a-rehearsal-password"
```

(The password goes on **stdin**, never on the command line — that `<<<` is the rehearsal's.)

That must print *would save …* and nothing about a password. If it refuses, the answer is the
box's own passwordless sudo for `pi` (the image ships `pi ALL=(ALL) NOPASSWD:ALL`); nothing in
this repo adds a rule of its own — phase `70-network` only **proves** this door, with exactly
these two lines under `runuser -u pi --`, which is why it warns rather than fails.

**What this proves that nothing else can:** a thumb on the page, a passphrase that does not come
back, a real NetworkManager profile written by a real helper, and — the whole design in one
sentence — **a box that is on your network for exactly one start, and never anywhere you cannot
reach it.**

---

## 18. Twenty books and twenty cartridges, in one press (10 minutes, needs a phone)

**Why it is unproven:** the **Add files** page (`https://ark.local:8443/admin/upload`) was
written on 1 Nov 2026 and has **never had a file picked in it**, on any device. Everything
underneath is proved — the routing, the caps, the two existing ingest paths, the refusals and
the answer from the importer's own ledger (`tests/test_admin_upload.py`, 29 tests) — but a
file picker is a piece of user interface, and no test can press it.

**Two things this test is really about:**

* **the book path is somebody else's program.** This page writes a file into the folder the
  book importer watches and then *asks the importer's record* whether it worked. A passphrase
  of a test cannot prove that a real Calibre accepted a real EPUB on a card with a full queue;
* **the list answers in the right order.** Twenty files means twenty sentences, and a person
  must be able to read them without asking what "rejected" meant.

**Before you start:** a phone or laptop on the same Wi-Fi as the box, signed in to the owner's
console (§12), and **one `.epub` and one game file** you do not mind having on the shelf (a
`.gb` or `.gba` is easiest).

1. Open `https://ark.local:8443/admin/upload` and tap **Add files** in the console's bar.

   **You should see:** one *Choose files* control (your phone's own file picker, which should
   let you pick **more than one**), one big button (*Put these files on the box*), and under
   it the two lists — which kinds of book and which kinds of game this page reads — and the
   limits in plain numbers.

2. Pick the `.epub` **and** the game file in the same go, then tap the button once.

   **You should see:** the same page again, with **"2 of 2 files went on the box"** and a row
   for each file saying where it went. The book's row may say *still being added* — that is
   the box telling the truth about another program's work, not a failure.

3. Open `http://ark.lan/books/` in a second tab and reload it.

   **You should see:** the book, with its title and cover, within a minute.

4. Open `http://ark.lan/arcade/` and reload it.

   **You should see:** the game on the shelf, under the name the box recognised. Tap it, tap
   **START GAME**, and confirm it runs.

5. Now do the wrong things on purpose, in one press: pick a photograph (`.jpg`), and an empty
   file you have made with a `.epub` name.

   **You should see:** **"0 of 2 files went on the box. 2 refused"**, each named, each with a
   reason a person can read — *"the box does not know what to do with that kind of file"* and
   *"That file was empty…"*. Nothing on the shelf changed, and the books page did not gain a
   blank book.

6. Open `https://ark.local:8443/admin/doors` (the **Addresses** link).

   **You should see:** five groups of addresses with the name you reached the page by — every
   one of them starts with `https://ark.local` (or `ark.lan`, if you are on the hotspot). Read
   the *Open to anyone* group aloud: none of it should need a word looked up.

**If step 2 says 400 and every row is refused with "kind of file",** the browser sent a name
without an extension — a phone's "Take photo" style picker does that. Pick the files from a
file manager instead; that is a real limitation and worth writing down if it bites.

**What this proves that nothing else can:** a thumb on a file picker, a real book accepted by a
real library, a real cartridge playing afterwards, and a page that refuses four kinds of wrong
without a single thing changing on the card.

---

## 19. The first page after signing in, and the way out (10 minutes, needs a phone)

**Why it is unproven:** the owner's console was rebuilt on 1 Nov 2026 — a front page that
measures four things, a navigation bar on every page, a way back to the reader pages, a
**Sign out** button, and one sentence on the public pages naming the owner's door. Every piece
of it is proved from the outside (`tests/test_admin_dashboard.py`, 19 tests; `tests/test_public.py`),
and **nobody has looked at it in a browser.** A shell can say the four figures are in the HTML;
it cannot say whether they read as sentences.

**Two things this test is really about:**

* **the figures are the truth, not a decoration.** The free space on that page is the one number
  an owner checks before adding a film or a shelf of books, and it must match what the machine
  says;
* **a person can get out.** Signing out is a `POST` with a form token, which is invisible to
  whoever presses it — the only question a browser can answer is whether the button is where a
  thumb expects it, and whether pressing it really ends the session on *every* device.

**Before you start:** a phone on the same Wi-Fi as the box, signed in to the owner's console
(§12), and an SSH session on the box for one comparison.

1. Open `https://ark.local:8443/admin` (or `https://ark.lan:8443/admin` on the hotspot).

   **You should see:** a bar across the top with eleven pages in it — *Overview, What is
   working, Add files, Network, Household, Games, Community, Administrators, My password,
   History, Addresses* — with the page you are on drawn as chosen, then **four figures** with a
   label under each, then **Put something on the box** with two buttons, then **Sign out**.

2. Read the four figures aloud, in order.

   **You should hear:** *"How long the box has been on"*, *"Room left on the card"*,
   *"Anyone can open these in the File Box"*, *"On the Message Board"* — no product names, no
   service names, nothing you have to look up.

3. On the box, run `df -h /srv/ark` and compare it with **Room left on the card**.

   **You should see:** the same number, to the nearest whole unit. The line under it says how
   much is in use and what percentage is free — and that percentage must never say *100 %*
   while the card has something on it.

4. Tap **Where the room went** under that figure.

   **You should see:** **What is working**, and the folders listed with measured sizes. Then tap
   the bar's **Overview** to come back.

5. Open `http://ark.lan/` in a new tab and scroll to the bottom of the page.

   **You should see:** one sentence naming `https://ark.local:8443/admin` and the word
   *password*. It must **not** be a tappable link: `/admin` on a plain page is refused on
   purpose, and a link there would be a dead end.

6. Go back to the console and press **Sign out**. Then press the browser's back button and try
   to reach `https://ark.local:8443/admin/doors` directly.

   **You should see:** the sign-in form, not the pages. A page that had been open before still
   shows what it had drawn, but nothing new is served and every button on it is refused.

7. On the *other* device (the laptop you were signed in on), reload any owner page.

   **You should see:** the sign-in form there too. Signing out on one device ends the session
   itself, not just the browser that pressed the button.

8. Sign in again, and on the phone press the bar's **Back to the reading pages**.

   **You should see:** `http://ark.lan/` — by the name you came in on, not by a number.

**If step 1 shows a bar with a page missing,** that page is reachable but not on offer; note
which one and check it against the *Addresses* page, which names every door and must name that
one too.

**What this proves that nothing else can:** four figures a person trusts, a bar a thumb can
find its way around, a way back to the reading pages, and a sign-out that ends a session
everywhere — on the device it was pressed on and on the one in the kitchen.

---

## 20. Twenty books, one search box (15 minutes, needs a phone and an SSH session)

**Why it is unproven:** the search page could only ever look in **twelve** of the box's twenty
collections — a ceiling meant as a guard against a pathological card was acting as a page size —
and it printed *Nothing matched “…” yet* about a search it had never finished making. It also
printed that same sentence when the library said nothing at all, which is a fault wearing the
costume of an answer. And a snippet was cut at character 220 whatever fell there, so the box was
seen serving `…do you carry wat` — a good answer that looked like a broken page. Once all twenty
were asked, a fourth fault showed up behind the first: the twenty-four places on the page were
filled from a list sorted **by collection name**, so *water* came back as Amateur Radio, Bicycles,
Cooking, Gardening, Learning Self-Reliance and Medical Sciences and nothing else — no encyclopedia,
dictionary, travel guide or Gutenberg book could appear at all, however good the article was. All
four are fixed (2 Nov 2026, `tests/test_search.py`), and **none of it has been looked at by a
person.**

**What this test is really about:** a reader deciding whether to trust a "no". There are now
three different sentences for three different facts, and only one of them is a real "no".

**Before you start:** a phone on the same Wi-Fi as the box, and an SSH session for steps 3 and 6.

1. On the phone, open `http://ark.lan/search?q=bleach+water`.

   **You should see:** a **green** dot and a count of answers, with **From the library** first
   and the source collection named under each title.

2. Read the snippet under one library result out loud.

   **You should hear:** whole words. If it stops early it ends with ` …`, and the character
   before the space is a full word — never `wat`, `disinf` or a dangling comma.

3. On the box, count the collections the search really asked:

   ```bash
   curl -s 'http://127.0.0.1:8081/catalog/v2/entries?count=-1' | grep -c 'href="/kiwix/content/'
   ```

   **You should see:** `20` (or however many the box holds that day) — and the search above must
   have asked that many, not twelve. The way to tell from the page: the complete "no" in step 4
   says **All *n* collections were asked**, and that *n* must match this number.

4. Search for something that cannot be in any book — `zzqx vrrblt kjjj` is a good one, because
   every word in it is nonsense.

   **You should see:** a **green** dot, *Nothing matched “zzqx vrrblt kjjj” yet*, and the
   sentence **All 20 collections were asked and none of them had it.** That is a real "no" and
   the only wording allowed to say it.

   **Do not use words that exist.** `xylophone quantum badger` reads like gibberish to a person
   and is **not** one to this box: measured on 2 Nov 2026 it comes back with real hits (Wiktionary
   among them), because each collection matches word by word and *badger* and *quantum* are
   entries. A sentence of real words is the wrong tool for testing a "no" — use the one above.

5. Search for one plain word that will hit many books — `water`.

   **You should see:** hits grouped by collection, and **every heading on the card** somewhere in
   the library block: the twenty-four places are shared out one per collection before any
   collection gets a second, so Amateur Radio, Bicycles, Cooking, Gardening and Learning
   Self-Reliance show two and the rest show one. Before 2 Nov 2026 all twenty-four places went to
   the six alphabetically-first collections, so **Wikipedia, Wiktionary, Wikivoyage, WikiMed,
   Ready.gov, iFixit and the Gutenberg shelf could not appear at all** — those are the specific
   sources this step proves.

   Count the names under the titles, not the titles: you should find **nineteen** different
   names for **twenty** collections, because two of the shelves on the card are both called
   *Project Gutenberg Library*. (The box groups by the name it shows you, so those two count as
   one — and no page can tell you which of the two Gutenberg books answered.)

6. Make the library refuse, and watch the page say so rather than lie:

   ```bash
   sudo systemctl stop ark-kiwix
   ```

   Then search for anything on the phone.

   **You should see:** a **red** dot and *The big library could not be looked in just now* — with
   *That is not the same as “nothing found”* under it — and the manuals and bookshelf still
   searched below. **It must not** say *Nothing matched*.

   Then start it again and search once more:

   ```bash
   sudo systemctl start ark-kiwix
   ```

7. Make it slow rather than silent, to see the amber state. Stop it, start it, and search in the
   same second — the first search hits a box that is still loading twenty collections:

   ```bash
   sudo systemctl restart ark-kiwix
   ```

   **You should see:** an **amber** dot and *Nothing found for “…” — but the look-up stopped
   early, so this is not the whole library*, with **Look again** offered. Tapping it a few
   seconds later should give the green state. Amber is allowed to be rare; if you cannot catch
   it, that is a good sign, not a failed test — say so rather than inventing a result.

**If step 4 says anything other than *All n collections were asked*,** the search did not cover
the library and the wording is wrong: note the exact sentence and whether the dot was amber.

**What this proves that nothing else can:** that a "no" from this box is a real no, that a fault
never reads as an answer, and that a reader's eyes are not left in the middle of a word.

---

## 21. A film, on a phone, in a room (25 minutes, needs a phone and one video file)

The shelf's logic is tested; how a real browser plays a real file is not.

1. **Add a film from the File Box.** Open `http://ark.lan/files` on a phone, choose a
   video (an MP4 from the phone's own camera roll is ideal), press *Put it in the box*.
   It should say **"sent for a look"** and open the Films shelf.
2. **Check it is not public yet.** On another device, open `http://ark.lan/media/`. The
   film must **not** be there. The File Box listing must not show it either.
3. **Publish it.** As the owner, open `https://ark.local:8443/admin/media`. The film is
   under *Everything* as *Waiting for a look* with its real length and picture. Press
   **Show on the shelf**.
4. **Watch it.** On the phone, open the Films shelf, tap the card, press play. Check:
   it starts within a few seconds; **seeking to the middle works** (drag the bar);
   fullscreen works; rotating the phone keeps the picture the right way up.
5. **Resume.** Stop the film halfway, close the tab, open the film again. It should
   offer **Start over (resume at …)** and continue where you stopped. Press **Clear
   this list** on the shelf; the *Carry on watching* row should disappear.
6. **A film that needs preparing.** Add an MKV (or an AVI) the same way. The owner's
   page should say *Needs a quick repack* / *Needs converting*. Press **Make a playing
   copy** and wait — write down how long a one-minute clip took. Press **Stop
   preparing** on a second one and check the original is still in the File Box details.
7. **Captions.** Put a `.srt` beside the film in the owner's upload form (same name),
   then check the player's caption menu offers *English*.
8. **Delete one.** Press **Delete for ever** twice — the first press must not delete
   until *I am sure* is ticked. Check the page says how much was removed.
9. **A full card.** (Optional, on a test card.) Fill the card until the owner's page
   says it is inside the reserve. Uploading a film should be refused with a sentence
   naming the shortfall, and the portal should still answer.

**What to write down:** the phone, the browser, the file's codec (the owner's page
shows it), whether it played directly or needed preparing, and how long preparation
took for a known length. That last number is the one nobody can measure on a Mac.

---

## 22. Maps on a real phone, with the Ark gone (40 minutes + one region)

**Why a person must do this:** the automated test runs desktop Chromium with the
test certificate pinned by fingerprint. That proves the application, the storage,
the router and the service worker, but it is not an iPhone, it is not Android's
certificate store, and it is not a home-screen launch. This is the step that turns
"works in Chromium" into "works in a pocket".

**Prepare:** the Ark reachable; a region built and published (the demonstration
region Liechtenstein is fine; the owner's real region is better); a phone you are
willing to change one setting on.

1. **Trust the certificate.** On the ordinary portal, tap *the Ark's certificate*.
   - iPhone/iPad: Settings → Profile Downloaded → Install; then Settings → General →
     About → Certificate Trust Settings → full trust for “Project ARK”.
   - Android: Settings → Security → Encryption & credentials → Install a
     certificate → CA certificate → choose the file.
   Write down the fingerprint the Ark prints (`sudo ark-tls-cert --show`) and compare
   it with what the phone shows before trusting it.
2. Open **https://ark.local:8446/maps/** in the phone's browser. The lock must be a
   real trusted lock, not a warning you tapped through. Add it to the home screen
   (iPhone: Share → Add to Home Screen; Android: menu → Add to Home screen).
3. Open the region, tap **Download for offline use**, and watch it through. Pause it
   once and continue; then let it finish. The badge must say **Ready offline**.
4. **Leave the Ark.** Turn the phone's Wi-Fi off entirely (not just leave the
   building), or put it in aeroplane mode with Wi-Fi off. Wait ten seconds.
5. Open Maps from the **home-screen icon**, not by typing the address. It must open
   the map, not an error page. Pan to a corner you never looked at, zoom in, and
   search for a place you never searched before.
6. Choose a start and a destination you have never used, pick **On foot** and then
   **Car**, and calculate a **new** route each time. Read the directions out loud:
   are they ordered, named, and believable? Print or download the route sheet;
   download the GPX and open it in another program if you have one.
7. Turn Wi-Fi back on, reconnect to the Ark, and open Maps. The page must still open;
   the catalogue may show an update. Nothing should have been lost.
8. Remove the region from the device, then download it again. It must say **Ready
   offline** a second time. If the phone reports low storage, do not fill it to find
   out what happens; use a spare device.
9. Record: phone model, OS version, browser, region and data date, pack size,
   installed size, time to download, time to first map, route/search latency, any
   warning, and whether the home-screen icon survived a phone restart.

**Pass:** every step works with the Ark and the internet both unreachable, the route
is new (not a saved sheet), and the page says **Ready offline** only after the check.

**If it fails:** note whether it is the certificate (no lock / no offline), the
download (badge absent), the home-screen launch (address typed instead), or the
route (a sentence from the page). Those are four different problems; do not report
them as "Maps does not work".

---

## 23. The shelves, and a film that actually plays (20 minutes, needs a phone)

**Why a person must do this:** the automated tests prove the page is built from the
card's own index, that a film shelf is never counted as a searched shelf, and that the
digest of every downloaded file matched the one the mirror published. None of them can
prove that a phone decodes these particular films, that a shelf name means something to
somebody who has never heard of *Stack Exchange*, or that the films are any good.

**Prepare:** the Ark on, three collections from the new shelves (a film shelf, the
home-improvement Q&A, and Low-tech Magazine), and a phone with sound.

1. Open `http://ark.lan/library`. Count the shelves. Do the names tell you where you
   would look for: *a leaking tap*, *a car that will not start*, *keeping food for a
   year*, *what to do about a cut*, *a radio that will not receive*?
2. Tap **Watch and learn**, then the **Urban Prepper** card. It must say on the
   card that these are films and that they play in the browser. Open it.
3. Pick any film. Note its title and length. Tap it.
   - Does the picture appear within a few seconds?
   - Does the sound play, and can you turn it up?
   - Drag the progress bar to the middle. Does it jump there, or restart?
   - Pinch to full screen (iPhone: the full-screen button in Safari's video player;
     Android: the same in Chrome).
   - Turn the phone's Wi-Fi **off while the film plays**. It should keep playing for
     the part already fetched; it is not a stream from the internet.
4. Go back and open **Fix it** → **Home improvement Q&A**. Type `leaking tap` into the
   collection's own search box. Do the answers look like answers to the question?
5. Go back to the portal's top search box and search `leaking tap`. The results must
   include the home-improvement collection. Then search `winter` — the film shelves
   are asked like every other collection, so a video page may appear; open one and
   check that it is a film you can play.
6. Open **Water, power and shelter** → **Low-tech living**. Read one article. Is it the
   magazine, with its pictures and its own dates on the page?
7. Record: phone model, browser, which shelf names landed and which did not, which film,
   time to first picture, whether seeking and full screen worked, whether sound was in
   sync, and any film that failed to play at all (title + what the screen said).

**Pass:** every collection on the page opens, one film plays with sound and seeks, and
the search finds the Q&A archive.

**If it fails:** note whether it is the page (a shelf missing), the collection (a card
that will not open), the film (a spinner, a black screen, or “cannot play this file”),
or the search wording. Those are four different problems; do not report them as
"the library does not work".

---

## What is *not* on this list, on purpose


* The v5 migration has run once, on the live database. It will be proved on a card that already
  holds v5 the first time a v6 exists — a migration that has never run on real old rows is a
  migration nobody has tested. Until then: **take a `.backup` before every deploy that migrates**
  (the rule already lives in `AGENTS.md` §8.4).
* Load, several readers at once, and a full card are measured by readiness and by `du`, not by a
  human. `tests/test_health.py` covers a full card; nothing covers twelve phones at once.
* Anything the box has not built yet (quotas, a trash, an approval step, a backup button, a "check
  now" button) is not a test item — it is absent, and `AGENTS.md` §8.6 says so.
