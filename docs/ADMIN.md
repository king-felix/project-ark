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
| Change the box's settings | no | only the two Community switches so far (§7) |

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

4. Sign in with the temporary account:

   * user: `admin`
   * password: `ChangeMe-ARK!`

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
*recovery-required* and will **not** recreate `admin` / `ChangeMe-ARK!`. Use the
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

## 8. Not in this release yet

Stated plainly, because a missing control is better than a fake one:

* Uploads: 2 GiB per file, one at a time, anonymous. There is **no per-person
  quota**, no approval queue and no quarantine — anything uploaded is
  downloadable at once. Uniqueness and safe naming are done (three files with the
  same name no longer overwrite each other).
* Deleting a shared file is for ever; there is no trash and no undo.
* No health panel, storage breakdown, download pause/resume controls, backup or
  restore screens. The overview page therefore shows only what it can read from
  the database.
* No household page, no "download the essentials" pack, no QR cards, no
  theme/text-size controls yet.
* Emergency content review (first aid, water, electrical) is still pending.

The audit history, the account rules above, the transport boundary and the
Community controls are complete and tested.

---

## 9. Tested / not tested

**Tested** — 84 automated tests, disposable databases, `pytest`:

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
  address.

**Not tested** — no hardware or browser access in this environment:

* anything on the Raspberry Pi: the new nginx listener, the certificate, a real
  browser warning, a phone on the hotspot, the SSH tunnel end to end;
* the loopback binding of `kiwix-serve` as a *running* service (the unit is
  checked as text only);
* `ark-tls-cert` itself (the script passes `bash -n` and uses standard
  `openssl` calls, but it has not been run on the Pi);
* `ark-admin-recover` against the live database — reviewed, not executed there;
* a full 2 GiB upload across the real network (the cap is tested with a smaller
  limit; the wire behaviour is nginx's).

**Not tested** — no hardware or browser access in this environment:

* anything on the Raspberry Pi: the new nginx listener, the certificate, a real
  browser warning, a phone on the hotspot, the SSH tunnel end to end;
* `ark-tls-cert` itself (the script passes `bash -n` and uses standard
  `openssl` calls, but it has not been run on the Pi);
* `ark-admin-recover` against the live database — reviewed, not executed there.

To install and verify this stage on the Pi:

```bash
sudo bash /srv/ark/src/install-ark.sh --only 60-portal,65-admin
```

then work through §2 with a phone.

