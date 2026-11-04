# Moving the Ark to a bigger card

*From a 128 or 256 GB card to a 512 GB one — by cloning the working card, or by
building the box again on a fresh one.*

This page is for the person doing it, with both cards on the desk. It is
deliberately explicit about the difference between **the card**, **the
partition**, **the filesystem**, **the free space** and **the reserve**, because
after a clone those are four different numbers and only one of them is the one
the box can actually use.

Nothing on this page is run by an agent. Cloning, flashing, partitioning and
resizing all write to physical media and all need a person present. If you want
the short version to tick off with the cards on the desk, that is
[`FOR-YOU-CARD-MIGRATION.md`](FOR-YOU-CARD-MIGRATION.md).

---

## 1. What changes, and what does not

**Changes:** the capacity the box can use. The 7 % reserve is a percentage of
the filesystem that is *actually* receiving the bytes (asked with `statvfs`,
never a nominal card size), so on a 512 GB card it becomes about **33 GiB**
instead of about 16 GiB. Nothing else in the software has a card size in it.

**Does not change:** every path, every service, every collection. The box does
not care how big the card is; it cares how much room the filesystem says it has.

**The one thing that catches people out:** a byte-for-byte clone copies the
*partition table* too. The new 512 GB card therefore boots with a 256 GB
partition and a 256 GB filesystem, and 256 GB of the card is simply not in use.
That is not a fault — it is an unfinished migration (§5).

---

## 2. Which cards the tools will write to

`scripts/lib-disk-guard.sh` will only ever write to a device that is **external**,
**physical**, a **whole disk**, **not internal**, `Ejectable=yes`, reports
`RemovableMedia=yes`, and whose size falls inside one of the supported capacity
classes:

| Class | Accepted size (decimal GB) | Why the band |
|---|---|---|
| 128 GB | 100–150 GB | a real 128 GB card reports ~128.0–128.1 GB |
| 256 GB | 200–300 GB | some 256 GB cards report as low as ~247 GB |
| 512 GB | 440–580 GB | 512 GB nominal, or 512 GiB (549.8 GB) of flash |

The bands are separate on purpose. A 200 GB disk is not "a 256 GB card that came
up short", and a 640 GB drive is not "a 512 GB card that grew". Anything outside
every band is refused, and the refusal prints the classes it would have accepted.

**Capacity alone never decides.** Every other fact is checked independently, and
each one can refuse the device on its own. Missing, contradictory or malformed
metadata is a refusal — not a shrug. A reader that reports nothing at all is
*not* treated as "probably fine".

**`RemovableMedia` is the fact that separates a card from a USB drive.** A
portable SSD — the disk somebody keeps their backup on — reports
`Internal=no`, `WholeDisk=yes`, `VirtualOrPhysical=Physical` and
**`Ejectable=yes`**, because ejecting is how the Finder removes it. An earlier
rule accepted "ejectable *or* removable", which describes every external disk on
the desk rather than a card. Now the media itself must be removable, and a
reader that will not say so is refused with that sentence rather than guessed
at. A 500 GB backup drive sits squarely inside the 512 GB class, so this one
line is the difference between erasing a card and erasing a backup.

**The identity is re-checked immediately before the write.** The guard records
the device name, its exact size and any UUID the reader reports, then runs the
whole check again inside the process that is about to write, and compares. What
that catches: a card swapped for a different size (the size), a reader reseated
and renumbered (the device is no longer the one that was verified, or no longer
external), and a different UUID when the reader publishes one.

**What it cannot catch:** two identical cards in the same reader. Most whole
disks publish no UUID at all — measured on this Mac, a whole disk has none of
`DiskUUID`, `MediaUUID`, `VolumeUUID` — and two 512 GB cards differ in nothing
the metadata shows. That is why `clone-card.sh` makes you type the destination
device name and why the original card is never written.

**Adding a class** is a deliberate edit in two places — `ARK_CARD_BANDS` in
`scripts/lib-disk-guard.sh` and the cases in `tests/test_disk_guard.py`. Never
widen a ceiling: a class is a statement that a card of that size is a card this
box expects, with the tests to back it.

Try it without a card:

```bash
bash scripts/lib-disk-guard.sh      # lists the disks and refuses if there is no card
```

---

## 3. Before you start

1. **Do not flash, format or repartition anything yet.** Read §4 or §5 first.
2. **Take a backup from the owner's page** and copy it somewhere that is not the
   card. A clone is not a backup: it copies a mistake as faithfully as it copies
   a library.
3. **Write down the certificate fingerprint** on the box, so you can prove the
   identity survived (a clone) or know it will change (a fresh install):

   ```bash
   sudo ark-tls-cert --show
   ```

4. **Have both cards in USB readers** if you are cloning, with no other external
   drive plugged into the Mac.
5. The Mac must stay awake for the copy. Plug it in.

---

## 4. Path A — a fresh installation on the new card

Use this when you want a clean box, or when the old card is suspect.

```bash
# 1. the one secret a backup cannot hold — the household Wi-Fi
#    (see docs/REBUILD-FROM-BACKUP.txt §1a for the exact file)
bash scripts/make-firstrun.sh

# 2. flash the new card (the guard refuses anything that is not a supported card)
bash scripts/01-flash.sh

# 3. card into the Pi, power on, wait
bash scripts/02-wait-for-pi.sh

# 4. install, either empty or from a backup bundle
bash scripts/03-provision.sh
```

A fresh install **expands the root filesystem to the whole card by itself**:
Raspberry Pi OS does it on first boot, and `pi/steps/10-base.sh` measures the
card and the root filesystem and expands again if the root filesystem is still
less than 90 % of the card. Nothing manual is needed.

What a fresh install does **not** carry over — this is the whole difference
between the two paths:

- **the certificate identity.** `sudo ark-tls-cert` makes a *new* self-signed
  certificate. Its fingerprint is different, so **every phone, tablet and
  computer that trusted the old one must trust this one**: open
  `http://ark.local/certificate.crt`, compare the fingerprint with
  `sudo ark-tls-cert --show` on the box, and add it on that device.
- **the owner's account and password** — claim the door again through the tunnel
  (`docs/ADMIN.md` §2), or restore a backup bundle.
- **the household Wi-Fi**, which only exists in `staging/wifi.env` on the Mac and
  in the old card's NetworkManager profiles.
- **the SSH host keys**, so `ssh` will warn that the host key changed:
  `ssh-keygen -R ark.local` on the Mac.
- **the collections, books, games, films and map packs**, which have to come
  from a backup bundle or be downloaded again. Map packs are **not** in
  `ark-backup`; copy `/srv/ark/maps/` off the old card by hand if you want them.

---

## 5. Path B — cloning the working card (keeps everything)

This is the migration that keeps the box's identity: the same certificate (so
nothing has to be re-trusted), the same owner account, the same saved Wi-Fi, the
same SSH host keys, every collection, book, game, film and map.

### 5.1 Copy the card

```bash
bash scripts/clone-card.sh --dry-run     # says what it would do; writes nothing
bash scripts/clone-card.sh               # the real copy
```

The tool:

- needs **exactly two** external cards, each passing the full guard, in
  **different** capacity classes — that is how it knows which is which;
- refuses to run without a person at a keyboard;
- makes you **type the destination device name** before erasing it;
- revalidates **both** cards inside the privileged helper, immediately before
  the first byte is written;
- never writes to the source card;
- compares the first and last 8 MiB after the copy, and stops if they differ.

It takes roughly 30–90 minutes for 256 GB.

> **Two boxes, one identity.** A clone is the same machine: same hostname, same
> SSH keys, same certificate, same database. Never have the original card and
> the clone running at the same time — mDNS, the hostname and the owner's
> account all assume one box. Take the original card out of the Pi and put it
> somewhere safe, and leave it there.

### 5.2 First boot of the new card

Put the new card in the Pi and power on. The box comes up exactly as it was: same
name, same networks, same content. `http://ark.local/api/ready` should answer
200.

**The card is bigger than the filesystem at this point.** That is expected — see
§6.

### 5.3 Keep the original

The original card is now a **cold spare**: a complete, working copy of the box at
the moment of the clone. Keep it. Do not erase it and do not boot it again while
the new card is in use — anything written on the new card since the clone does
not exist on the original.

---

## 6. Making the rest of a cloned card usable

After a clone the three sizes disagree:

```
physical card   : 512 GB      <- what you bought
main partition  : 256 GB      <- what the copied partition table says
filesystem      : 256 GB      <- what ext4 believes
```

The 256 GB that is missing is not lost; it has simply never been allocated. Use
`ark-card-report` to see this in numbers (§7), then expand:

```bash
# on the box, as a person, deliberately
sudo growpart /dev/mmcblk0 2
sudo resize2fs /dev/mmcblk0p2
ark-card-report                     # should now say "the whole card is in use"
```

`sudo raspi-config nonint do_expand_rootfs` does the same two steps if you prefer.

- `dev/mmcblk0` is the card and `2` is the root partition on a Raspberry Pi.
  Confirm with `lsblk` before typing it.
- `resize2fs` grows an ext4 filesystem **online**; no reboot and no unmount.
- Do this while nothing is copying to the card and nothing is being written by a
  film conversion or a collection download.
- **Never run this unattended, and never on a card you have not booted and
  proved.** The original card is your undo button; do not spend it on an
  unproven copy.

If you would rather not do it by hand, `bash scripts/03-provision.sh` runs phase
10, which measures the card against the root filesystem and expands when they
disagree. That is the same operation, with the same effect.

---

## 7. Checking it afterwards — read-only

`ark-card-report` writes nothing, needs no root, and separates the five numbers
that all get called "the size of the card":

```bash
ark-card-report
ark-card-report --json        # the same numbers for a script
```

```
the card, as this machine sees it
  physical card   :     512.1 GB  (476.9 GiB)   mmcblk0
  main partition  :     512.0 GB  (476.9 GiB)   mmcblk0p2
  filesystem      :                 (476.7 GiB)
  free for files  :                 (300.1 GiB)
  kept back (7 %) :                  (33.4 GiB)
  usable by Ark   :                 (266.7 GiB)

  verdict         : the whole card is in use
```

What each line means:

- **physical card** — the block device the machine sees. This is what you bought.
- **main partition** — the slice the root filesystem lives in. After an
  unexpanded clone this is still the old size.
- **filesystem** — what ext4 believes it has. After a partition grows, this can
  still be the old size until `resize2fs` runs.
- **free for files** — what an ordinary account may write right now
  (`f_bavail`), not the free figure that includes the slice ext4 keeps for root.
- **kept back** — the reserve every write path asks `ark_space` for:
  **7 % of the filesystem, or 2 GiB, whichever is larger.**
- **usable by Ark** — free minus the reserve. This is the number that decides
  whether another collection fits.

Exit codes: `0` the whole card is in use; `4` it is not (the card, the partition
and the filesystem disagree); `5` something could not be measured.

An unmeasured number is printed as **"not measured yet"**, never as `0`.

---

## 8. What a clone keeps, and what a fresh install must be given again

| Thing | Clone | Fresh install |
|---|---|---|
| Certificate and its fingerprint | **kept** — devices keep trusting it | new one; every device must trust it again |
| Owner's account, password, sessions | kept | claim the door again, or restore a backup |
| SSH host keys | kept (no warning) | new (run `ssh-keygen -R ark.local`) |
| Household Wi-Fi profiles | kept | from `staging/wifi.env` only |
| `/etc/ark/ark.env` (boot mode, reserve %) | kept | defaults, unless a backup is restored |
| Collections, books, games, films | kept | re-download or restore |
| Map packs | kept | **not in `ark-backup`** — copy by hand |
| Partitions and filesystem size | **old size — expand (§6)** | expanded automatically |

The thing that most often goes wrong in a fresh install is the certificate: the
box looks broken on a phone because the phone does not trust the new certificate,
and the maps service worker will not install until it does. It is not broken —
it is a new identity. Compare fingerprints, do not click through.

---

## 9. What never to do

- **Never flash or partition the card that is working.** Copy from it; do not
  write to it.
- **Never run the clone or the expansion unattended.** Both need a person.
- **Never boot the original and the clone at the same time.**
- **Never widen the guard's size ceiling** to make a device pass. Add a class,
  with a test, or use a different card.
- **Never treat "the box booted" as proof the migration worked.** Prove it with
  `ark-card-report` (sizes), `curl http://ark.local/api/ready` (the box) and the
  certificate fingerprint (identity).

---

## 10. Rolling back

Put the original card back in the Pi and power on. The box returns to exactly
what it was at the moment of the clone. Anything written on the new card since
then — a book, a game save, a film — does not exist on the original.

That is why the original stays untouched until the new card has booted, answered
`/api/ready`, and shown the right sizes in `ark-card-report`.
