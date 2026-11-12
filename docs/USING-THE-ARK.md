# The Ark — owner's guide

*Everything the box does, in the order you will actually need it.*

Written 24 October 2026 · device answers to **`ark.lan`**, **`ark.local`**, **`10.42.0.1`**

The Ark is a small computer with no internet connection of its own. It holds a library —
encyclopedias, repair guides, medical references, your own books — and it hands that library
to any phone, tablet or laptop that joins its Wi-Fi. No accounts are needed, no sign-in, no internet
required. (A name is optional, only for keeping game saves.) If the internet is gone, everything
below still works.

---

## The one-page version

| I want to… | Open this | Then |
|---|---|---|
| look something up quickly | `http://ark.lan` | type 1–2 words, tap **Search the library, guides and books** |
| find a film to watch | `http://ark.lan/media/` | the films have their own list — the search box does not cover them |
| read this in daylight | `http://ark.lan` | **Light colours** at the bottom of the page |
| make the box quiet for a while | `https://ark.lan:8443/admin/background` | **Pause background work** (owner only) |
| put the date and time right | `https://ark.lan:8443/admin/clock` | check the suggested time, then **Set the box's clock to this time** (owner only) |
| read an encyclopedia offline | `http://ark.lan/kiwix/` | pick a collection, use the search box inside it |
| read a book | `http://ark.lan/books/` | browse or search, then **Read in browser** |
| fix something today | `http://ark.lan/vault/` | open a manual; print the PDF if you want paper |
| hand someone a file | `http://ark.lan/files` | choose file → **Put it in the box** |
| play a game for a few minutes | `http://ark.lan/arcade/` | tap a game; nothing to install, no name needed |
| see this household's own answers | `http://ark.lan/household` | who lives here, where the water stops, who to ring |
| print a card that gets a phone back | `http://ark.lan/cards` | print it, put it by the router |
| keep the guides when the box is dead | `http://ark.lan/household` | **Take it with you** → the pack downloads as one file |
| keep a game save on the box | `http://ark.lan/arcade/player` | it opens the secure page; make a name, then **Save here** |
| put a book on the shelf | `http://ark.lan/files` | drag the `.epub` in and walk away |
| leave a note for the household | `http://ark.lan/board` | name is optional → **Put it on the board** |
| see what is on the box | `http://ark.lan/api/status` | plain text, safe to read on any phone |
| know whether it actually works | `http://ark.lan/api/ready` | `200` = everything works; `503` = the body says which part and why |
| keep the household's notes and files safe | `ssh pi@ark.local` | `sudo ark-backup --to /srv/ark/backup`, then copy that folder to a stick |

Same box, three addresses. Use whichever your device can resolve.

---

## 0. The front page, and what it is telling you

The line under the heading is the box answering a question nobody asked out loud:
*is any of this working?* It starts by saying **"Checking what is on the box…"** and
then either gives you the figures or says plainly that it could not check.

**If it says the box is not answering its own status check,** the manuals, the
household page, the cards and the Maps page are plain pages and should still open.
The library, the books, the board and the films need the part that is not
answering, so one of those may not. The line links to the honest answer.

**The order of the page is on purpose.** *If something is wrong* comes first,
because when something is wrong you should not have to scroll past the
entertainment to find the first-aid card. *Read, watch, play* is next and is a
real section rather than a footnote: a long evening with no internet is what the
box is for as much as a burst pipe is. *For everybody here* is the board and the
File Box.

**Today / Together.** If the owner pins a message that reads like a plan — *"Film
at eight in the front room"* — it appears near the top of the front page. It is
the same message that is on the board, with the same pin and the same
**Sorted out** button, so there is no second list to keep. When there is no plan,
there is no panel at all. When a plan is more than a day and a half old the panel
says when it was written, because the box cannot know whether the evening
happened.

**Light colours.** At the bottom of every page. Tap it and the same pages come
back white with dark text — for reading in a garden, or on a phone whose screen
turns into a mirror in the sun. Your choice is remembered on that device, and it
works with JavaScript turned off.

**Where your place is kept**, which is four different promises:

| What you did | Where it is kept | What that means |
|---|---|---|
| Watched half a film | **this browser, on this device** | close the tab and it is still there; another phone starts from the beginning |
| Played a game as a guest | nowhere | guest play does not save |
| Played a game under a name | **on the Ark**, under that name | any device that uses the same name sees the save |
| Downloaded a map region | **this browser, on this device** | another phone needs its own copy |
| Wrote a message or shared a file | **on the Ark** | everyone on this Wi-Fi sees it |

---

## 1. Getting on

### On your home Wi-Fi
If the Ark is plugged into your normal network, open **`http://ark.local`** on any device
already on that Wi-Fi. (The box broadcasts its own Wi-Fi by default; joining it is the
section below.) A computer on a home network cannot look up a name ending in `.lan` —
that name only works while the Ark is broadcasting, because then the box answers it.

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

### Carrying it to another house

A different house means a different Wi-Fi, and the box has to be told the name and the
password without a keyboard plugged into it. That is what the owner's **Network** page is
for (`https://ark.local:8443/admin/network`, or `https://ark.lan:8443/admin/network` from a
phone on the hotspot):

1. **Add a Wi-Fi network** — the name your phone shows under *Wi-Fi*, and the password, typed
   exactly. Saving it does **not** make the box join it; it only writes it down.
2. Then **Ask for one restart on my network**: pick that network, leave the minutes at 30, and
   press the button. Tick **Restart the box now, without waiting** if you want it to happen
   straight away — the box answers the page first and restarts five seconds later.

The request is used by **one** start and then it is gone: the boot after that broadcasts
`The-Ark-Offline` again. If the network is not there when the box starts, the box broadcasts
instead of sitting there looking for it — that is deliberate, because a box in a cupboard
that has gone looking for a router nobody switched on cannot be found. If you change your
mind before the restart, **Take that request back** on the same page.

A request lapses too: after the number of minutes you chose it is ignored, and the page says
so in a sentence rather than showing a button that would do nothing.

If the box could not restart itself (its own reason appears on the page), the request is
**kept**: the page says **The request is kept.** and the next start — yours, by hand — uses it
and joins that network. Nothing is lost; you only have to do the restarting.

The same two things from a console, if you are already signed in over SSH. The password is
read from **standard input** — never from the command line, which every process on the box can
read and your shell remembers:

```bash
# 1. write the network down (this does not make the box join it)
printf '%s\n' "$WIFI_PASSWORD" | sudo /usr/local/sbin/ark-wifi-add --ssid "Home Wi-Fi"
sudo ls /etc/NetworkManager/system-connections/   # the profile it made, e.g. ark-wifi-Home-Wi-Fi-1a2b3c4d

# 2. ask for one boot on that profile
sudo ark-mode request-lan --minutes 30 --network ark-wifi-Home-Wi-Fi-1a2b3c4d --ssid "Home Wi-Fi"
sudo ark-mode status                              # what is waiting, and for how many more minutes
sudo ark-mode request-lan --cancel                # never mind
```

`ark-mode request-lan` with no `--network` means the standing home network the installer wrote
down (`home-wifi`), which is the right one for the house the box already lives in.

### Make it feel like an app (recommended for phones)
Open `http://ark.lan` in the phone browser → menu → **Add to Home Screen**. You now have an
icon that opens the Ark in one tap, and it still works with no internet.

---

## 2. The front page, tile by tile

The home screen is deliberately boring: five headings, plain words, big buttons.

| Section | Tiles | What it really is |
|---|---|---|
| **Find something** | one search box | searches every collection, every manual and the bookshelf at once |
| **Start here** | Ask the Ark · Offline Library · Books · Manuals & Guides | a helper that answers a question typed in your own words — on the box, with no internet — and the three libraries |
| **When something breaks** | Repair guides · Quick card · This Household · Card to get back on | iFixit-style repair pages, a one-page card to print, the household's own answers, and a printable card that gets a phone back to the box |
| **For everybody here** | Message Board · File Box · Games | notes, file swapping and a small game shelf for anyone on the Wi-Fi |
| **Not sure how this works?** | Help | the same instructions, in even simpler words |

Every tile has a one-line description under it and a **How to use** badge, because the person
who needs this most will not be the person who set it up.

---

## 3. The features, one at a time

### 3.1 Search, and what it covers — `http://ark.lan/search`

**What it is for:** the moment where you do not know *which* library has the answer.

It asks **every** collection on the box at the same time (in parallel, so a slow one never
stalls the page), plus the manuals and the book shelf, and groups what it finds into three
blocks:

- **From the library** — actual encyclopedia articles
- **From the manuals** — your own pages
- **From the bookshelf** — books by title or author

**What it does _not_ cover:** films and games. Each of those has its own list
(`/media/` and `/arcade/`), and the button under the box says so rather than
pretending to search everything. There is no full-text index of a film or a game to
search — a name or a picture is how you find those.

**How to use it:** one or two words. `bleach water`, `burn`, `radiator leak`. Short and
specific beats a sentence. Tap any result to read it right there.

**Why it can say three different things when it finds nothing.** The page used to say
*Nothing matched* whatever had happened, which was sometimes untrue — so there are now three
separate answers, and the dot beside each one is a different colour:

| What the page says | The dot | What it really means |
|---|---|---|
| *Nothing matched “…” yet* — **All 20 collections were asked and none of them had it** | green | a real, complete "no". Try a shorter word, or a different word for the same thing |
| *Nothing found — but the look-up stopped early, so this is not the whole library* | amber | some collections were still thinking when the page had to draw. **A second try often gets further** — tap **Look again** |
| *The big library could not be looked in just now* | red | none of the collections answered at all. This is a fault, **not** a "no" — the manuals and the bookshelf were still searched, and they are listed below if they had anything. Tap **Try again** in a moment |

The page waits eight seconds for the collections. On this box twenty collections answer in
about four and a half, so the amber and red states should be rare — when you see one, the
honest thing is to try again once before believing the answer.

**A snippet is a beginning, not a summary.** The line under each result stops at the end of a
word or a sentence and says so with `…`, so it never leaves you reading half a word.

**If it still finds nothing,** narrower search boxes inside each collection often find more
than the global one.

### 3.2 Offline library — `http://ark.lan/library`

**What it is:** whole websites and whole video channels, frozen and stored on the box. They
open in the browser and behave like the real sites — search, links, pictures, video — with no
internet.

**Two doors.** `/library` is the one to start with: it files every collection on a shelf by what
it is *for*, in plain words, and says how big each one is. `/kiwix/` is the full browser behind
it — every cover, every collection, an alphabetical list and a language filter.

**The shelves** (27 collections, 12 Nov 2026):

* **Fix it** — iFixit repair guides, home-improvement Q&A, woodworking Q&A, electronics Q&A.
* **Vehicles** — motor-vehicle maintenance and repair Q&A (cars, trucks, motorcycles).
* **Food and garden** — cooking Q&A, gardening Q&A, Gutenberg's farming shelf.
* **Health and first aid** — WikiMed, WikEM, medical-sciences Q&A, Gutenberg's medicine shelf.
* **Water, power and shelter** — Ready.gov, the self-reliance library, Low-tech Magazine.
* **Prepared and planning** — TruePrepper's written guides.
* **Watch and learn** — the Urban Prepper and Canadian Prepper film collections.
* **Outdoors and getting about** — outdoors and camping Q&A, bicycle repair Q&A, Wikivoyage.
* **Keep in touch** — amateur-radio Q&A.
* **Learn and look things up** — Wikipedia, Wiktionary, Wikibooks, Wikiversity, Wikiquote,
Gutenberg.

**The film shelves** are *video*, not pages: a search finds the films' own pages by their titles
and descriptions, but not what is said on screen, because nothing has turned the speech into
words. Open the shelf and pick a film from its list. Tap a film to watch it in the browser; it
plays from the box, with no internet and no app.

**How to use it:** open a shelf → open a collection → type in its own search box → read (or tap
a film to watch). The arrow at the top returns you to the shelf of collections.

**How current it is:** each collection is a copy taken on a date — the day it was frozen, not
the day you open it. A collection built in August 2026 holds the films that channel had put out
by then; a 2025 build of a magazine holds the articles up to 2025. The card gives the size, and
the collection's own pages carry the dates of the articles.

**Reading on a phone with no network:** works — that is the entire point. The collection lives
on the box, the box broadcasts Wi-Fi, the phone reads it.

**About the “Download” button** inside each collection: it hands you the whole collection as a
single file. That is only useful if you already run a matching offline-reader app on that
device; the supported path is to read in the browser, which needs nothing installed. A film
shelf is many gigabytes — do not start that download on a phone by accident.

### 3.3 Books — `http://ark.lan/books/`

**What it is:** your own bookshelf, served from a real Calibre library. Series, tags, covers,
reading order and author pages all survive, because they all live in the library's database.

**How to use it:**

1. Open `http://ark.lan/books/`.
2. Browse by *Newest*, *Authors*, *Series*, *Tags*, or use the search box at the top.
3. Open a book → **Read in browser** (there is a reader built in — it works on phones and
   needs no app), or **Download** the EPUB/PDF to a tablet, Kindle or USB stick.

**There is nothing to sign in to, and nothing to change.** The shelf is read-only from the
portal: no account, no password, no "add book" button, no settings. The only way books arrive is
section 4 (the File Box, or adopting a whole library from a computer), which is deliberate — a
reader cannot rename, move or delete anything by accident, and nobody can be locked out.

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

**Every name in this list really opens.** If the file behind a name has gone (someone tidied the
folder, or the notes were put back on a fresh card without the files), the name is not shown to
you — a link that leads nowhere is worse than a file that is missing. The owner sees the leftover
name on the **What is working** page, as *“Nothing here can be opened”*, and removes it from the
**Shared files** list there.

**The clever part:** if the file you drop in is a book — `.epub`, `.pdf`, `.mobi`, `.azw3`,
`.fb2`, `.djvu`, `.cbz` and friends — it is picked up within seconds and filed onto the book
shelf automatically. No command, no second step. The copy in the File Box stays put so the link
you were given keeps working. See section 4.

If the owner has paused sharing, the upload button is replaced by a sentence saying so, and every
file already in the box still downloads normally. The owner can also hide a file or, in the last
resort, remove it for good. The list shows the newest 50 files; when there are more, it says so
and the count in the heading is the real total.

### 3.7 Games — `http://ark.lan/arcade/`

Tap **Games** on the front page, then tap a game. It opens with one big **START GAME**
button: give it a tap and the picture appears. (The tap is not decoration — a phone
will not start a game until it has been touched, and without one the game runs with a
blank white screen, which is exactly how it looked before 25 Oct 2026.) The arrow keys
work, and on a phone an on-screen pad appears. Everything runs on the box, so a dead
internet changes nothing.

About two dozen small games come with the box (Game Boy, Game Boy Color, Game Boy
Advance and NES). More are added by whoever looks after the box — from the private
**Games** page behind their own sign-in, or at the box itself. A game added that way is
often named by the box from the file itself rather than from the file's name, so a
carelessly named file still appears with the right title, year and maker. The File Box is
for documents, not games. Each game's card says what it is and who made it, and the licence
the source states is shown on the card and in the notes beside the shelf — it is a
label, not a restriction this box applies.

**A game of your own.** If you have a game file on your phone or on a card in your
pocket, tap **Add a game** (at the top of any games page, or the button under the shelf).
You do not need a name, a password or anybody's permission: choose the file, tap **Look
at this file**, and the box says what it thinks the game is — *“This looks like "Pocket
Quest" (Game Boy).”* — with the title, console, year and maker already filled in. Check
the words, change anything you like, then tap **Add this game**. It appears on the shelf
straight away and you can play it. **Throw it away** changes nothing.

What this box will not do: it will not add a file it cannot play, and it will not let a
game of yours take the place of a game that is already there — if you give it a name that
is already on the shelf it asks you to choose another one. Giving it the same game twice
answers *“that game is already on the shelf”* instead of adding a second copy. One file at
a time, and no bigger than 64 MB. Whoever looks after the box can switch this door off;
when it is off the page says so and every game still plays.

**Saves.** Playing as a guest, your save stays in that one browser on that one device —
clearing the browser's data removes it. If you want a save to follow you to another
device, tap **My saves**, make a name and a password of at least 12 characters, then open
a game and use **Save here**. That page is `https://ark.lan:8445/arcade/player` (the box sends
you there by itself); the name is only for games and cannot change anything on the box.
Three saves per game are kept, and **Download all my saves** gives you one zip to keep
somewhere else. **Put these saves back** takes that zip on another Ark. If a save was
changed on another device while a game was open, the box refuses the older one and asks
you to reload — it never lets an old tab overwrite a newer game. The box also keeps two
small recovery copies of its own records; while one is kept, the save bytes it names are
kept too, so replacing a save today does not make yesterday's copy a lie. Those copies
never include films or the offline library.

### 3.8 Is it happy? — `http://ark.lan/api/status`

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

### 3.9 The household page, the cards and the pack

Three things that are about the day something goes wrong rather than about reading.

**`http://ark.lan/household`** — the page somebody opens when they are frightened. It has
three parts, and each one is honest about what it does not know:

- **What this household wrote down** — who lives here, where the water and the power stop,
  where the first-aid kit is, who to ring. Only a person can fill this in (the owner does,
  on their own page); until somebody does, the page says *"Nobody has written this page
  yet"* rather than showing empty lines.
- **When something goes wrong** — the eight emergency subjects the box carries (breathing and
  bleeding, drinking water, food that is still good, fire, power, toilets with no water,
  cold and shelter, small repairs), each with the page that covers it and **how long since
  somebody last read it**. A subject with no page says so; a page
  nobody has read says *"Nobody has read this yet."*; a date the box cannot read is treated
  as overdue rather than as fresh. Nothing here is invented, and nothing is a percentage.
- **Take it with you** — one download, `/essentials.zip`: the small pages and the repair
  guides rewritten so their links work from a folder, the quick card, the recovery sheet and a
  `MANIFEST.txt` naming every file with its fingerprint. Unzip it on any computer with no
  network and open `START-HERE`. If no pack has been made, the page says so (and the link
  answers a friendly "there is no copy to download yet", never a broken download). The three
  wall cards are **not** inside the pack — they are on `/cards`, to print and put up now,
  because that is what they are for.

**`http://ark.lan/cards`** — three addresses as squares a phone camera can read, with the
address written underneath each one: `http://ark.lan` (the box's own Wi-Fi),
`http://ark.local` (a home network) and `http://10.42.0.1` (the number that always works
while the box is broadcasting). The squares are drawn by the box itself — no image files,
no fonts, no network — so a printed card keeps working when the box does not. Print one and
keep it beside the router.

**The pack from a console** (no page needed — run it as `pi`, it does not need root):

```bash
/usr/local/sbin/ark-essentials --build     # make /srv/ark/essentials/ark-essentials.zip
/usr/local/sbin/ark-essentials --status    # is there one, how big, how old, how many pages
/usr/local/sbin/ark-essentials --list      # what is inside it
/usr/local/sbin/ark-essentials --verify    # read every fingerprint back
```

The owner's **Household** page has the nine answers, the switch, the review list and a
**Make a new copy now** button, plus a **Download this copy** link. The switch hides
`/household` and `/cards` from visitors — hiding them leaves the pack downloadable and every
other page untouched.

### 3.10 Ask a question in your own words — `http://ark.lan/ask`

**What it is for:** the moment when you do not know what to search for, or which words
the book would use.

Type the question the way you would say it out loud — *"how do I make water safe to
drink"*, *"what do I do about a deep cut"*, *"the fridge stopped and the food has been
warm since this morning"* — and the answer is written on this box. Nothing leaves it,
nothing is downloaded, and no answer is stored.

**Read the sentence at the bottom of every answer first.** It says the helper can be
wrong and the guides are the truth, because that is exactly what it means: this is a
small model talking, not a doctor. Where the page can point at a real guide, it does —
tap through and read the page itself.

**How to use it:** one question at a time, in a full sentence, and give it the detail a
person would need (who, how long, how bad). **Give it a minute or two** — the box has four
small cores and an answer takes 100 seconds or so here; you will see the words appear as
they are written, and the page waits for it (the box gives one question three minutes
before it gives up). **Questions other people ask** under the box are ready-made examples
to tap.

**If it says the helper is not fitted:** the box has no model in it yet, and that is a
completely normal state — the owner puts one on the card when they want to
(`sudo ark-ai-model-fetch --status` says what is missing). Every other page works
without it.

**If it says somebody else is asking:** the box answers one question at a time, because it
has no cores to spare. It tells you how many seconds to wait — wait that long and ask again.
Nothing is lost, and everything else on the box keeps working while you wait.

**If the owner has switched it off**, the page says so in one sentence and everything
else on the box is untouched.

### 3.11 Films — `http://ark.lan/media/`

**What it is for:** a film the household keeps on the box, watched on whatever phone
or laptop is in the room — with no account, no streaming service and no internet.

The shelf shows a picture for each film, its length and whether it is ready. Type in
the search box to find one, tap a chip to show only what is ready (or only what is
still being prepared), and use **Order** to sort. Everything works on a phone, with
one thumb, and nothing starts playing on its own.

**Watching:** tap a film, then press play. The player is your browser's own, so
seeking, volume, fullscreen and captions all behave the way they do everywhere else.
Two extras are on the page:

- **Speed** — 0.75× to 2×.
- **Small window** — picture-in-picture, where the browser allows it.

**If the owner has paused background work,** every film that is already prepared
still plays; only a film that is still being prepared waits. The shelf says which
is which. Nothing about watching changes.

**It remembers where you stopped, on this device.** Close the tab, come back, and
the film offers *Start over (resume at 12:34)*. That position lives in **this
browser's** storage — it is not on the box and nobody else sees it. The **Carry on
watching** row on the shelf is built from the same local list, and **Clear this list**
under it removes it.

**If a film says *Needs preparing***: the box has the file, but it is in a shape this
browser cannot play. The owner's page can make a playing copy — that is minutes for
a repack and can be **hours** for a full conversion (about three hours for a
90-minute 1080p film), done in the background while everything else keeps working.
The upload is only removed once that copy is ready, and from then on the copy is
the film. **If it says *Could not be prepared***, the reason is on the owner's page.

**Captions** appear in the player's caption menu when the owner has added them
(a `.vtt` or `.srt` file with the film). **Download the original** is there while
the box still holds the upload — the file exactly as it arrived, at the size it was
uploaded. Once a film has been made ready to play, the box removes the larger upload
to save room; the film itself stays. The copy the player uses is made for a phone
screen: **720p at most**, so the shelf holds many more films than the full-size
files alone would allow.

**How to add a film:** put it in the File Box (`http://ark.lan/files`, up to 8 GB).
It is *not* published straight away: it waits on the owner's Media page until they
have looked at it, and until then it answers 404 everywhere a reader could guess.
That is on purpose — a film is the one upload that can fill the card, and the one a
stranger is most likely to try.

**If the shelf is empty:** nothing has been published yet. The owner adds films from
`/admin/media`.

### 3.12 Maps — a region to carry with you

**What it is for:** leaving the Ark behind and still having a map, a place search
and **new** directions. The region is downloaded once, while you are on the Ark's
Wi-Fi, and lives on your phone. After that nothing needs the Ark or the internet.

**The one-time setup on each phone or laptop.** A page can only reopen without the
Ark when it is served over a secure connection the device trusts. The Ark makes its
own certificate; the front page offers it as **the Ark's certificate**.

1. On the front page, tap **the Ark's certificate** and install it.
   * **iPhone/iPad:** Settings → Profile Downloaded → Install; then Settings →
     General → About → Certificate Trust Settings, and switch on full trust for
     “Project ARK”.
   * **Android:** Settings → Security → Encryption & credentials → Install a
     certificate → CA certificate, and choose the downloaded file.
   * **Laptop:** import it into the operating system's trusted certificates.
2. Open **https://ark.local:8446/maps/** — the address must start with `https` and
   end with `:8446`. The ordinary `http://ark.local/maps` link sends you there.
3. Add the page to the home screen (iPhone: Share → Add to Home Screen; Android:
   Chrome menu → Add to Home screen) or bookmark it.

If you cannot do the certificate step, Maps still works while the Ark is reachable;
it just cannot promise to reopen with the Ark gone. **Clicking through a certificate
warning is not enough** — the browser will not keep the page offline that way.

**Downloading a region.** Open Maps, choose the region the owner prepared, and tap
**Download for offline use**. The page shows the size, the room this device reports,
and the progress. You can pause and continue, even after closing the page. The
badge **Ready offline** appears only after every part has arrived and been checked.
**Opening the page once does not download a region**, and a map you looked at once
is not a region either.

**Using it away from the Ark.** Open the home-screen icon or bookmark. Pan and zoom
anywhere inside the region, including places you never looked at before. Type a
place name (or coordinates) into **Start** and **Destination**, or tap **Pick on
map**. Choose **On foot**, **Bicycle** or **Car**, then **Show route**. The route is
drawn on the map, and the directions below it are ordered and readable. The dashed
line on the map is the edge of the downloaded region; outside it the page says so
instead of showing a blank square.

**Taking it with you on paper.** **Print route sheet** prints a map and the
directions; **Download route sheet** saves the same page as a file; **Download
GPX** saves the route for another program. A saved sheet is a paper fallback, not a
substitute for calculating a new route offline — Maps does that itself.

**Privacy.** Searches, saved places, your last view and route history stay on the
device. They are not sent to the Ark. **Saved places** never leave the device, and
**Remove from this device** removes only that device's copy of a region.

**What it cannot know.** Search finds named places and points of interest, not street
addresses (most map extracts have none). Times use the speed of each kind of road,
not live traffic, closures or weather. One-way streets and access rules are
respected, but turn restrictions that cover a whole stretch, and restrictions that
depend on a time of day, are not carried. A mapped water source is not a promise
that it is drinkable, and a mapped clinic is not a promise that it is open.

**If something goes wrong.** **Check this copy** reads every part and compares it
with its fingerprint. If a part is missing or damaged, remove the copy and download
the region again while the Ark is reachable. If the badge does not appear, read the
sentence on the page — it says whether the download, the page itself, or the
certificate is the thing still missing.

---

## 4. Getting books onto it


### 4.1 One or two books, from any device — 30 seconds

No software, no commands, no login.

1. On the PC (or phone) open **`http://ark.lan/files`**.
2. Drag the `.epub` / `.pdf` / `.mobi` into the **Choose a file** box.
3. Tap **Put it in the box**.

That is it. Within a few seconds the book appears at **`http://ark.lan/books/`** with its title,
author and cover. Nothing else to do.

**A whole pile of books, or a pile of games, at once.** `https://ark.local:8443/admin/upload`
(the owner's **Add files** page, behind the sign-in) takes up to twenty files in one
press: the books go to the Book Library, the games go onto the games page, and a list
under the button says what happened to each one. Anything the box cannot read is
refused **by name** and nothing is written. If a book says *"still being added"*, give
it a minute and open `/books/` — that sentence is the box being honest, not a failure.

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
- **A book you add is never taken out of the box, and a File Box link never breaks.** A file you dropped in
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

- It never fills the card: **7 % of the filesystem (or 2 GiB) stays free**, always — about 16 GiB
  on a 256 GB card and about 33 GiB on a 512 GB one. Anything that would breach that is
  skipped, with a line in the log saying why.
- It compares the file on disk against the catalogue, so a half-finished download is re-fetched,
  not counted as installed.
- A bundle the reader cannot actually open is re-fetched rather than trusted — that check caught
  two short files on this build already.
- One downloader at a time (a lock file), so two runs cannot corrupt each other.
- Safe to unplug: the downloader continues where it stopped.
- `full` also *lists* Wikipedia with photos (118.7 GiB). It needs about 131 GiB free, so the
  reserve rule skips it and says so in the log — on a 256 GB card it can only live there alone;
  on a 512 GB card it fits beside the rest of `core`.


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
8. `sudo ark-self-heal --dry-run` — says what the last power cut left behind **and what would be
   done about it**, changing nothing. The box already does this itself at every boot (before any
   page can read the clock) and again at 04:17; this is how you read what it found. *No pass
   recorded yet* is normal on a new box.


For whoever looks after the box, one more: `https://ark.local:8443/admin` shows a
sign-in page (and `https://ark.lan:8443/admin` does the same from a phone joined to
The-Ark-Offline). Once you are in, the first page says how long the box has been
on, how much room is left on the card, how many files anyone can open and how many
messages are on the board — each measured at that moment — and its navigation ends
in **Sign out**. That is the owner's door — it changes the machine, it is never
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
| `ark.lan` does not load | the name only resolves while the box is broadcasting (then the box answers every name) or on a network whose own DNS knows it; a home computer or phone cannot look up a name ending in `.lan` | use `http://ark.local` on a home network, or `http://10.42.0.1` on The-Ark-Offline |
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
| Out of space | something large added by hand | **What is working** shows which folder holds what; `sudo du -sh /srv/ark/*` for the detail; `ark-card-report` shows the card and what is kept back; the downloader keeps 7 % free on its own |
| Forgot the address | — | `ark.lan`, `ark.local`, `10.42.0.1` — all the same box |
| The date or time is wrong (“3 hours ago” about something made yesterday, or a date in the future) | the Pi has no clock battery, so after a power cut it woke up in the past — or a wrong date was written once and every file since agrees with it | nothing, usually: the box sets the clock from the newest file on the card before any page opens. If it is still wrong, open `https://ark.lan:8443/admin/clock` and set it from your phone (it shows the change first). A console can do the same: `sudo ark-set-clock --show`, then `sudo ark-set-clock --utc "2026-11-04 10:00"` |
| A page mentions a repair, or a file was “set aside” | the box found a half-written file after a cut and kept it rather than using it | nothing; `cat /srv/ark/logs/self-heal.log` reads like a sentence. A *repair* is not a fault — only the line under `problems` needs a person |
| The box ran on after a cut with an empty shelf, or a page said the records could not be opened | the database would not open | it keeps the damaged file and puts back the newest recovery copy whose database and files agree; if none does, **What is working** names it. A bundle is still the cure for a lost card: `sudo ark-backup --to /srv/ark/backup` |

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

# the game shelf runs as pi and needs no sudo (/usr/local/sbin is not on pi's PATH):
/usr/local/sbin/ark-arcade --status    # games on the shelf, player names, saves
/usr/local/sbin/ark-arcade --seed      # put the pinned first games back
/usr/local/sbin/ark-arcade --import F --slug S --title T --system gb  # a game of your own
/usr/local/sbin/ark-arcade --discover --auto  # what the weekly check runs
/usr/local/sbin/ark-arcade --list      # suggestions waiting, and why refused
sudo ark-set-clock --show              # what time the box believes, and in which timezone (no root needed)
sudo ark-set-clock --utc "2026-11-04 10:00"   # set it from a console; --dry-run checks without changing
sudo ark-set-clock --unix 1793786400   # or one instant, in seconds since 1970
sudo ark-tls-cert --show               # the owner's-door certificate fingerprint
sudo ark-admin-recover --list          # locked out? console only, never the web

sudo ark-backup --to /srv/ark/backup   # notes, files, accounts, player saves
sudo ark-backup --list                 # the copies already there, with ages
sudo ark-backup --verify PATH          # re-read every byte of one copy
sudo ark-restore --list                # what could be put back
sudo ark-restore --from PATH           # put one back (asks before replacing)

sudo ark-self-heal --dry-run           # what the last power cut left, and what would be done
sudo ark-self-heal --quiet             # only what was actually repaired (what boot runs)
cat /srv/ark/logs/self-heal.log        # the same, as a sentence per line

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
  household emergency, and it also means the board and the File Box are not private. There are
  two kinds of sign-in, and neither is needed to read: the owner's door on port 8443, which changes
  the machine itself and is never served on the portal address; and an optional **player name** for
  keeping game saves, on its own secure origin on port 8445. A visitor never sees either unless they
  go looking, and nothing a reader does asks them who they are. The owner can pause new notes and
  new uploads with one switch, and can hide or remove something that should not be there — but never
  learns who wrote it.
- **It does not need JavaScript** for anything that matters: search, reading, uploading and
  posting are plain HTML forms — and so is the owner's door. The game player is the one
  exception; the shelf list around it still reads without JavaScript.
- **Its copy is on the same card.** `sudo ark-backup` copies the notes, the shared files, the
  accounts, the book-import list and every named player's game saves — but it writes them where
  you tell it, so a copy left on the
  card dies with the card. Copy that folder to a stick or a computer, and keep the originals of
  your books on the PC as well. The day the card does not boot, the page to follow is
  `/srv/ark/RECOVERY-SHEET.txt` (print it now: `http://ark.lan/recovery.txt`). There is no
  automatic backup timer, on purpose.
- **Films are not in a backup.** `sudo ark-backup` copies the notes, the shared files,
  the accounts and the game saves; the collections, the book shelf, the games and the
  films are deliberately left out — there are tens of gigabytes of them and a bundle is
  not a film library. A restore brings the shelf's records back and the box says which
  films it no longer has. Keep a copy of a film that matters somewhere else.
- **Not built yet** (candidates, not promises): a planting calendar, an offline map pack, a photo
  archive of the house and its shut-off valves.
- **The helper answers, it does not search.** Ask the Ark is a small model on the card that writes
  an answer in its own words. It is not a search engine, it does not read the household's answers,
  it does not remember what you asked last time, and it is allowed to be wrong — the page says so
  under every answer. When it names a guide, read the guide. It also does not work out of the box:
  the box ships with no model (a model is ~1 GB and the owner chooses it), and until one is on the
  card the page says so plainly and every other page works as usual.

---

## 10. Notes for whoever maintains it

- Everything lives under `/srv/ark` — `kiwix/` (collections), `calibre-library/` (books),
  `vault/` (manuals and PDFs), `drop/files/` (File Box, the only folder the web server is allowed
  to serve), `drop/tmp/` (half-arrived uploads, never served), `calibre-incoming/` (staging, with
  `.imported.json` and `done/`), `ai/` (the helper's model and its page of facts — no model by
  default), `portal/` (front page), `logs/`.
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




