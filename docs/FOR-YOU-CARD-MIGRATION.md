# FOR YOU — the card swap

*Everything here needs a person, two cards and the box. Nothing on this page is
done, and nothing on this page was done by an agent. Tick as you go; write the
answers down, because several of them are the only record that the migration
worked.*

Reference pages: `docs/CARD-MIGRATION.md` (the full procedure) and
`docs/HUMAN-TEST-LIST.md` §28 (the same steps written as a test script).
You do not need both — this page is the short one.

**The software is ready. The card is not migrated.**

---

## Before you touch anything

- [ ] Take a backup from the owner's page and copy it somewhere that is **not**
      the card in the box.
- [ ] On the box, write down the certificate fingerprint:
      `sudo ark-tls-cert --show`
      ├─ Fingerprint before: ______________________________
- [ ] On the box, confirm it is healthy:
      `curl -s http://ark.local/api/ready` → 200, `"blocking": []`
- [ ] Decide the path:
      [ ] **Clone** — keeps the certificate, accounts, Wi-Fi, SSH keys, library
      [ ] **Fresh install** — clean box, everything rebuilt, phones must re-trust

---

## 1. Insert the card

- [ ] New 512 GB card into a USB reader, **only** card plugged into the Mac.
- [ ] `bash scripts/lib-disk-guard.sh`
      ├─ says **512 GB card** and a size between 440 and 580 GB
      ├─ says `Removable   : yes` and `Ejectable   : yes`
      └─ if it refuses: **stop here** and read the sentence it printed. A reader
         that will not report removable media is the one thing this rule trades
         on — note it, do not work around it.

## 2. Final device verification (do not skip)

- [ ] With **no** card in the reader: `bash scripts/lib-disk-guard.sh; echo $?`
      └─ must abort **and** exit non-zero.
- [ ] With **two** external disks attached: it must refuse and name both, never
      choose.
- [ ] If cloning: `bash scripts/clone-card.sh --dry-run`
      └─ names the source `/dev/diskN` as *never written*, and the larger card as
         the destination.

## 3. Migrate

**Clone:**
- [ ] `bash scripts/clone-card.sh`
- [ ] type the destination device name when asked
- [ ] ends with `SAMPLE OK` and `Copy made. The original card is unchanged.`
- [ ] **put the original card in a drawer.** Do not erase it, do not boot it.

**Fresh install:**
- [ ] `bash scripts/make-firstrun.sh` (with `staging/wifi.env` filled in)
- [ ] `bash scripts/01-flash.sh`
- [ ] note: every device that trusted the old certificate must trust the new one.

## 4. Boot the new card

- [ ] new card into the Pi, power on
- [ ] `curl -s http://ark.local/api/ready` → 200
- [ ] **clone only:** `sudo ark-tls-cert --show` — must equal the fingerprint in
      the box above. If it differs, stop: the identity did not survive.
      ├─ Fingerprint after: _______________________________
- [ ] open the portal on a phone that already trusted the certificate
      └─ no certificate warning (clone) / the warning is expected (fresh)

## 5. Expand (clone only)

- [ ] `ark-card-report` → exit 4, *"the card is bigger than its main partition"*
- [ ] `sudo growpart /dev/mmcblk0 2`
- [ ] `sudo resize2fs /dev/mmcblk0p2`
- [ ] `ark-card-report` → exit 0, *"the whole card is in use"*
      ├─ card ______ GB │ partition ______ GB │ filesystem ______ GiB
      ├─ free ______ GiB │ kept back ______ GiB │ usable ______ GiB
      └─ kept back should be about **33 GiB** (7 % of ~476 GiB)

*Not doing this now is a valid choice — the box works on the old size. The extra
room just is not usable until this runs.*

## 6. Prove it still works

- [ ] `/api/ready` 200, `blocking` empty
- [ ] open a collection, a book, a game, a film, a map — one each
- [ ] `ark-card-report` still says the whole card is in use
- [ ] write the result in `docs/IMPLEMENTATION-LOG.md` as a dated checkpoint

## 7. Keep the original card

- [ ] the old card stays as a **cold spare**, unerased
- [ ] do not boot it while the new card is in use — it is the same machine
- [ ] label it with the date of the clone

---

## What is already done, and what is not

**Done, tested on the Mac, committed and pushed:**
capacity classes (128/256/512), fail-closed metadata, the removable-media rule
that keeps a USB backup drive from being mistaken for a card, identity
re-validation immediately before every write, the two-card clone tool, the
read-only `ark-card-report`, and documentation for both migration paths.

**Not done — that is this page:**
no physical 512 GB card has been inserted, cloned, expanded or booted. The guard
is proved against a stand-in `diskutil`; the clone tool has never copied a byte
on hardware; `ark-card-report` has only read a disposable sysfs tree.

**If any step disagrees with this page:** stop, put the original card back, and
record exactly which step said what. The original card is the rollback.
