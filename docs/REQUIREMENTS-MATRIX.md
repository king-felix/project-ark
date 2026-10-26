# Project ARK — requirements matrix

**Date:** 26 October 2026  
**Branch:** `feature/admin-hardening`  
**Suite:** `504 passed, 2 skipped` (24 files; `python -m pytest -o addopts='--strict-markers' -q`, Mac, throwaway databases) — the two skips are QR cross-checks that need `qrcode` / `opencv-python-headless` on the machine  
**Hardware:** **deployed 25 Oct 2026** — `90-verify` green, `/api/ready` 200, the doors, the book
route rules, a bundle and the arcade origin all answered on the Pi (`docs/IMPLEMENTATION-LOG.md`
§15), `ark-arcade --import` answered honestly three times on the box (§18.4), and the name-from-the-
bytes work is deployed too — layout **v5**, the catalogue on the card, and the owner's upload door
proved end to end against a throwaway instance (§19.1). No claim below
should still be read as "a person has used it"; the phone walk-through in
`.clinerules` §8.9 item 2 has not been done.

This is the one page that answers "did we actually build the thing the requirement asks
for?". It is deliberately blunt: a green row means there is code **and** a test that fails
without it, not that somebody remembers writing it. A requirement with any unmet
acceptance sentence is **PARTIAL**, even when most of it ships.

## How to read the status column

| Status | Means |
|---|---|
| **DONE (code + test)** | Every acceptance sentence has code and at least one runnable test. Deployed on the box 25 Oct 2026, but **not yet seen in a phone's browser**. |
| **PARTIAL** | Some acceptance sentences ship and are tested; the rest are listed in *What is still missing*. |
| **NOT STARTED** | No implementation worth the name. Nothing to test. |

A *test* here means a test in this repository that would fail if the behaviour were
removed. A grep for a string in a shell installer counts only where the test also runs
`bash -n` on that file; it is evidence of intent, not of a working box.

---

## R1 — the offline player is genuinely offline

**Status: DONE (code + test).** The player is local and every byte it loads is accounted
for. The one thing not proved is a real archive being decompressed on a real phone.

| Acceptance sentence | Where | Test |
|---|---|---|
| No CDN, remote font, telemetry or external asset reference | `pi/arcade/emulatorjs/`, `pi/arcade/templates/`, `pi/api/ark_arcade_web.py` | `test_config.py::test_every_vendored_player_file_matches_its_checksum`; `test_arcade.py::test_the_game_page_is_entirely_local_and_has_no_guest_save_panel` |
| The compression add-ons the minified loader asks for at run time are present | `pi/arcade/emulatorjs/compression/` | `test_config.py::test_the_player_has_every_add_on_it_loads_at_run_time` |
| Every vendored file matches a recorded digest, and the manifest lists every file | `pi/arcade/emulatorjs/SHA256SUMS` | `test_config.py::test_every_vendored_player_file_matches_its_checksum` |
| The installer refuses to serve an unverified player | `pi/steps/60-portal.sh` | `test_config.py::test_the_installer_proves_the_player_before_serving_it` |
| A game page contains no `http://` or `https://` asset reference | `pi/arcade/templates/page.html`, `pi/api/ark_arcade_web.py` | `test_arcade.py::test_the_game_page_is_entirely_local_and_has_no_guest_save_panel` |
| The box the game is drawn into has a definite height, so the picture is on the screen | `pi/arcade/static/ark-arcade.css` (`#arcade-frame`, `#arcade-stage`) | `test_config.py::test_the_player_box_has_a_height_to_fill` |
| The screen's shape is set on a frame the player cannot overwrite (it writes its own `style`) | `ark_arcade_web.game_page`, `SYSTEMS[*]["aspect"]` | `test_arcade.py::test_the_player_is_sized_on_a_frame_the_emulator_cannot_overwrite`; `test_config.py::test_every_console_has_a_screen_shape` |
| A game file answers the size question (`HEAD`) as well as the download (`GET`) | `ark_arcade_web.game_rom`, `/drop/<name>` | `test_arcade.py::test_a_game_file_answers_a_size_question_as_well_as_a_download`; `test_arcade.py::test_a_hidden_or_absent_game_answers_a_size_question_the_same_way` |
| Every file the player asks for is on the card, including its language file | `pi/arcade/emulatorjs/localization/en-US.json` | `test_config.py::test_every_vendored_player_file_matches_its_checksum` |

The add-ons are the subtle part: `extractzip.js`, `extract7z.js`, `libunrar.js` and
`libunrar.wasm` are fetched only when a zipped game is opened, so their absence used to be
invisible until an offline phone tried to play. They are now vendored **and** in
`SHA256SUMS` **and** checked by phase 60 before nginx starts serving.

**Not proved:** decompressing a zipped ROM in a browser on the Pi's Wi-Fi. The files and
the manifest are right; the run is not.

---

## R2 — the admin desk is remote but private

**Status: DONE (code + test).** HTTPS-only management origin, peer address not a header,
bootstrap claim only over loopback, and a sign-in after the claim. Again: not seen on the
box.

| Acceptance sentence | Where | Test |
|---|---|---|
| Every admin page requires a session, including after a restart | `pi/api/ark_admin.py`, `pi/api/main.py` | `test_accounts.py::test_sessions_expire_on_idle_and_on_restart`; `test_auth.py::test_lan_login_refused_before_the_box_is_claimed` |
| The setup claim is reachable only through a loopback channel, never from the LAN | `ark_admin.setup_channel()` | `test_auth.py::test_bootstrap_can_only_finish_over_the_private_channel`; `test_auth.py::test_forged_forwarding_headers_do_not_claim_the_box` |
| A forged `X-Forwarded-*` or `X-ARK-Setup-Channel` cannot claim the box | `pi/systemd/ark-api.service` (`--no-proxy-headers`), `ark_admin.over_tls()` | `test_auth.py::test_the_api_never_trusts_a_forwarded_address`; `test_forged_forwarding_headers_do_not_claim_the_box` |
| Plain HTTP never reaches a password field | `pi/nginx/ark-admin.conf`, `ark_admin` | `test_auth.py::test_plain_http_never_reaches_a_password_field` |
| The management origin is genuinely TLS-only, and an unauthenticated 200 is a failure | `pi/lib/common.sh`, `pi/steps/65-admin.sh` | `test_tls_probe.py` (9-case verdict table + real sockets + TLS server) |
| Security headers on every admin page, CSRF on every POST | `ark_admin`, templates | `test_auth.py::test_security_headers_on_every_admin_page`; `test_post_without_csrf_changes_nothing` |
| Passwords good, sessions revocable, throttling by account and address | `ark_security.py` | `test_auth.py::test_setup_changes_password_and_revokes_the_old_session`; `test_accounts.py::test_wrong_passwords_are_generic_throttled_and_audited`; `test_accounts.py::test_throttling_counts_the_account_and_the_address_separately`; `test_db.py` password vector |
| No root login, key + password auth unchanged, secrets out of git | `staging/firstrun.sh.tmpl`, `.gitignore` | `test_config.py::test_ssh_stays_key_and_password_without_root_and_secrets_stay_out_of_git` |

The `curl` trap is fixed at its root: `curl` exits zero on a completed HTTP 400, so the old
installer check could pass on a listener that served the page in clear text. The helpers now
read the status code and classify it (`400`/`497`/`000` = rejected; any real status = served),
and the installer fails on a served page. `ark_plain_http_verdict` is driven against real
sockets in `test_tls_probe.py`, not mocked.

**Not proved:** a phone opening `https://ark.lan:8443` through a self-signed certificate,
and the actual router path to reach it from outside the house. The second is outside this
repo; the first is on the walk-through list.

---


## R3 — the reading shelf cannot be edited from the reading pages

**Status: DONE (code + test).** The route table was read out of the installed package
(`calibreweb/cps/*.py`, Calibre-Web 0.6.27, 231 rules, read on the box 2026-10-25, stored as
`/tmp/cw-routes.txt` during the survey and transcribed into `tests/test_config.py`). The public
proxy now answers 404 for management/account routes and GET/HEAD only for the rest; the book
importer is unaffected because it talks to `127.0.0.1:8083` directly.

| Acceptance sentence | Where | Test |
|---|---|---|
| Mutating Calibre-Web routes are not reachable | `pi/nginx/ark.conf` `limit_except GET { deny all; }` on `location /books` | `test_config.py::test_the_book_shelf_refuses_every_method_that_could_change_it` |
| Admin/account routes answer 404 at the proxy | `pi/nginx/ark.conf`, the `location ~ ^/books/(?:admin\|…` door | `test_config.py::test_the_book_shelf_answers_404_for_every_management_route` |
| Reading, search, download, OPDS, covers still work | same file — the door names only what must be refused | `test_config.py::test_every_reading_route_survives_the_door` |
| The one POST that only reads keeps working | the `advsearch` door, its own location | `test_config.py::test_the_one_post_that_only_reads_has_its_own_door` |
| Refusing `/upload` and `/login` cannot break the importer | `pi/bin/ark-import-books` talks to loopback, not the proxy | `test_config.py::test_the_block_cannot_break_the_importer_because_it_never_uses_the_proxy` |
| The installer proves the door on the box | `pi/steps/60-portal.sh` `must_be 404/403 …` proofs | `test_config.py::test_the_installer_proves_the_book_door_on_the_box` |

**How the two halves work.** A route a GET can reach (the whole `/admin` tree, `/login`,
`/register`, `/me`, `/generate_auth_token/<id>`, `/shelf/create`, the admin-only `/ajax`
endpoints, …) is named in the 404 door, because a method rule cannot stop a GET. Everything else
is refused by verb, and every Calibre-Web route that changes anything uses POST, PUT or DELETE.
The shell's reading `/ajax` endpoints (`listbooks`, `book/<uuid>`) are deliberately left out of
the 404 door so the table view keeps paging.

**Not proved:** no browser has loaded the shelf through this door yet — the installer's twelve
proofs (`/books/` 200, `/books/admin` 404, `POST /books/upload` 404, `POST /books/book/1` 403,
`POST /books/delete/1` 404, `/books/advsearch` 200, …) have only been `bash -n`'d, and `nginx -t`
has not seen this file on the box. A Calibre-Web upgrade that renames a route would not be caught
by the suite until the transcribed inventory is re-read; the day that happens, re-run the
enumeration and update both the config and `tests/test_config.py` in one commit.

---

## R4 — restoring records cannot corrupt a live card

**Status: DONE (code + test).** Restore pauses every writer, verifies every byte first, and
restarts what it stopped even when it fails.

| Acceptance sentence | Where | Test |
|---|---|---|
| Every writer is paused before the database is touched | `pi/bin/ark-restore` `quiesce()` | `test_backup.py::test_a_restore_pauses_every_writer_before_touching_the_database` |
| A writer that will not stop aborts before any change | `ark-restore` | `test_backup.py::test_a_writer_that_will_not_stop_aborts_before_any_change` |
| The box comes back up even when the restore raises | `finally:` in `ark-restore` | `test_backup.py::test_a_restore_that_fails_after_pausing_still_starts_the_box_again` |
| A card in a reader can be restored without systemd | `--no-service` | `test_backup.py::test_no_service_is_available_for_a_card_in_a_reader` |
| A forced restore replaces a stale game save | `ark-restore` `--force` | `test_backup.py::test_a_forced_restore_replaces_a_stale_game_save` |
| A damaged bundle is refused before any write | `ark_bundle.verify_bundle()` | `test_backup.py::test_a_damaged_copy_is_refused_rather_than_restored` |
| A path that leaves the box is refused | `ark_bundle.safe_rel()` / `inside()` | `test_backup.py::test_a_manifest_that_points_outside_the_box_is_refused` |

**Not proved:** a restore on the Pi, with real systemd units. The quiesce list is exercised
against a fake `systemctl` seam; the real units have never been stopped by this code.

**Still missing on purpose:** no timer and no button takes or restores a copy. A copy that
stays on the same card is not insurance, and the recovery sheet says so.

---

## R5 — a stranger cannot leave a live executable in the File Box

**Status: PARTIAL.** The dangerous half ships: there is exactly one write path, a stored name,
a spool that is never served, a visibility gate, and safe response headers. The admission
policy half — a fresh box that accepts nothing until the owner says yes, and a quarantine
before publication — does not.

| Acceptance sentence | Where | Test |
|---|---|---|
| Anonymous upload cannot overwrite or collide with another file | `ark_storage.spool()`, `os.link` | `test_storage.py::test_three_same_name_uploads_all_survive`; `test_storage.py::test_spool_never_overwrites_even_with_a_repeated_token` |
| A partial upload is never reachable over HTTP | `/srv/ark/drop/tmp`, no nginx alias | `test_storage.py::test_the_temp_folder_is_never_served_by_the_web_server` |
| Empty file, over-cap and low-disk uploads are refused | `spool()` | `test_storage.py::test_oversized_file_is_refused_and_leaves_no_residue`; `test_storage.py::test_empty_file_is_refused`; `test_storage.py::test_a_full_box_refuses_before_writing` |
| A file with no database row is not downloadable (hidden = no URL) | `main.py` `/drop/` gate | `test_storage.py::test_a_visible_file_downloads_and_a_hidden_one_does_not`; `test_storage.py::test_a_file_on_disk_without_a_visible_row_is_not_reachable` |
| Downloads are served as attachments with `X-Content-Type-Options: nosniff` and a safe filename | `main.py` `_download_header()`, `/drop/` route | `test_storage.py::test_a_visible_file_downloads_and_a_hidden_one_does_not`; `test_storage.py::test_the_download_header_cannot_carry_a_newline` |
| HTML/SVG opened directly cannot script the portal origin | attachment + nosniff today | **partial:** no separate download origin, no CSP sandbox |
| A fresh install accepts no contributions until the owner enables them | settings default **open** (`public_uploads`, `public_board_post` = open when absent) | **absent** |
| Uploads are held for approval before anyone can download them | no `pending` state in `files` | **absent** |
| No per-person quota, no rate limit, no aggregate File Box cap | — | **absent** |
| A hidden file's already-known URL cannot be served from cache | `Cache-Control: max-age=3600` | **partial:** up to one hour |

**What this means in practice:** a stranger on the hotspot can still upload a file, and it is
published immediately. The file is inert at rest and forced to download rather than render,
which removes the drive-by script risk, but it is not an approval gate. Flipping the default
to closed is a behaviour change with a test attached (`the closed state still serves
everything already written`), and it has not been made.

---

## R6 — the HTTPS promises are proven, not assumed

**Status: DONE (code + test).**

| Acceptance sentence | Where | Test |
|---|---|---|
| A TLS-only listener rejects plain HTTP | `ark_http_code` + `ark_plain_http_verdict`, `65-admin.sh` | `test_tls_probe.py` (400, 497, 000 accepted; 200/302/401/403/404/500 refused) |
| A clear-text sign-in page is a failure even if it answers 401 | verdict table | `test_tls_probe.py::test_the_plain_http_verdict_is_a_table_not_a_curl_exit_code` |
| A closed port is "no-response", not a status | `ark_http_code` | `test_tls_probe.py::test_a_closed_port_reports_no_response_not_a_status` |
| A real TLS listener answers HTTPS and still rejects HTTP | real `ssl` server | `test_tls_probe.py::test_a_real_tls_listener_answers_over_https_and_still_rejects_plain_http` |
| The installer uses the verdict, not `curl`'s exit code | `65-admin.sh` | `test_tls_probe.py::test_the_installer_uses_the_verdict_instead_of_curl_exit_status` |
| Both secure origins (management and player) are checked the same way | `65-admin.sh` | same test (`8443` ≥ 2, `8445` present) |
| The shell still parses | `common.sh`, `65-admin.sh` | `test_tls_probe.py::test_every_shell_file_still_parses` |

**Not proved:** nginx on the Pi actually answering 400 to plain HTTP on `:8443`/`:8445`.
The helpers and the installer branch are tested against real sockets; the shipped config has
only been syntax-checked.

---

## R7 — adding games is a careful owner action, not a browser link

**Status: PARTIAL.** The gates are real and tested, including the fixes made this session.
The catalogue is still one upstream service (`hh3.gbdev.io`) with three per-console
repositories behind it, and SNES/Genesis have no entries at all; the owner's queue is
not a durable background system.

### What ships and is tested

| Acceptance sentence | Where | Test |
|---|---|---|
| A licence is recorded as provenance and never refuses a game (owner's decision, 25 Oct 2026) | `license_problem()`, `rights_facts()` | `test_arcade_install.py::test_a_licence_is_recorded_and_never_refuses_a_game`; `test_arcade_install.py::test_a_candidate_without_a_licence_is_not_held_for_review` |
| Refuses a game the source *says* is in another language (wordless and unrecorded are allowed) | `language_facts()`, `_language_values()` | `test_arcade_install.py::test_the_language_table_allows_english_wordless_and_unrecorded`; `test_arcade_install.py::test_multilingual_english_survives_the_trip_through_a_catalogue_entry` |
| Holds a candidate the source contradicts, or says is not English, for review | `review_reason()`, `candidate_status()` | `test_arcade_install.py::test_a_candidate_without_a_licence_is_not_held_for_review`; `test_arcade_install.py::test_an_owner_file_is_accepted_without_a_licence_but_held_back_in_another_language` |
| Rebuilds the download address from a validated slug + bare filename | `ark_arcade_install` candidate model | download gate tests |
| Source prefix allowlist, redirects refused, size cap, ROM shape | `_download_url`, `publish_candidate` | `test_arcade_install.py` |
| A candidate is temp-written, fsynced, hashed, then `os.replace`d | `publish_candidate` | publish tests |
| Writes file + rows in one transaction; unlinks the new file if the DB refuses | `publish_candidate`, `ark_db.tx(conn)` | transaction test |
| Leaves the shelf exactly as it was on failure | publish tests | `test_arcade_install.py` |
| Owner import copies a local file with recorded origin, never trusts a browser path | `import_owner_file()`, `_copy_to_temp()`, `OWNER_PROVIDER` | `test_arcade_install.py::test_an_owner_file_goes_through_the_same_gate_and_is_marked_owner_supplied` |
| The owner can add one file from the owner's page: the bytes land in the arcade's own waiting folder under a name this box chose, nothing is published until the owner confirms, and the waiting copy is deleted on refusal or on a duplicate | `ark_admin.arcade_upload`, `/admin/arcade/upload/add`, `/upload/discard`, `ark_arcade_install.spool_upload`/`discard_upload` | `test_arcade.py::test_the_owner_can_add_a_file_and_watches_it_become_a_game`; `test_arcade.py::test_the_same_cartridge_offered_twice_is_named_not_added_again`; `test_arcade.py::test_a_file_the_box_cannot_play_is_refused_and_changes_nothing`; `test_arcade.py::test_an_upload_without_a_sign_in_writes_nothing` |
| Anybody on the box's Wi-Fi can add a game of their own from the games page, with no name, account or sign-in — through the same waiting folder and the same publisher — and can never replace a game that is already on the shelf; the owner's switch closes the door without stopping play; the name a person types is the name the game is given | `ark_arcade_web.add_page`/`add_upload`/`add_confirm`/`add_discard`, `ark_arcade.uploads_open` (§ switch `public_arcade_upload`), `pi/nginx/ark.conf` + `ark-players.conf` `/arcade/add` (68 MB, streamed) | `test_arcade_add.py` (10 tests: the open door, never a replacement, the same bytes twice, a file the box cannot play, an empty file, a token that is not ours, changing your mind, the door closed, the owner's switch, the typed name decides); `test_config.py::test_a_game_a_visitor_picks_has_a_door_of_its_own_on_both_origins`; `test_config.py::test_the_save_door_did_not_grow_to_a_game_size` |
| A game actually appears in the browser after one tap (not a white box) | `ark_arcade_web.game_page` — no `EJS_startOnLoaded`, the player's own START GAME button, and a frame shaped to the console | `test_arcade.py::test_the_game_waits_for_a_real_tap_before_it_starts`; `test_arcade.py::test_the_player_is_sized_on_a_frame_the_emulator_cannot_overwrite`; proved in Chromium against the shipped page (log §20) |
| A game file is named from its own contents (header, stripped-header fingerprints) against an offline catalogue, and the answer pre-fills the title, console, year, maker and description | `ark_romdb.identify_path`/`match`, `ark_rommeta.prefill`/`apply` | `test_romdb.py::test_a_game_is_named_from_its_bytes_even_when_the_file_name_says_nothing`; `test_romdb.py::test_the_fingerprint_kept_describes_the_cartridge_not_the_wrapper`; `test_romdb.py::test_the_strongest_fingerprint_is_the_one_used_and_kept`; `test_romdb.py::test_a_recognised_file_is_stored_with_what_the_catalogue_said` |
| Recognition never refuses a file: no catalogue, no record, or a record with no licence still adds the game with the name from its file name, and a word the owner typed is never replaced | `ark_rommeta.sentence`/`apply`, `_ROM_KEEP` | `test_romdb.py::test_a_file_nobody_listed_is_still_usable_and_claims_nothing`; `test_romdb.py::test_a_box_with_no_catalogue_still_names_every_game_from_its_file_name`; `test_romdb.py::test_a_word_the_owner_typed_is_never_replaced` |
| A file given twice is not put on the shelf twice: the same file says "unchanged", the same game under another name is refused with the name it already has, new bytes under the same name are revision 2 | `game_by_sha()`, `owner_candidate()` (records the file's own SHA-256) | `test_arcade_install.py::test_the_same_file_given_twice_is_not_put_on_the_shelf_twice`; `test_arcade_install.py::test_the_same_game_under_a_second_name_is_refused_with_the_name_it_has`; `test_arcade_install.py::test_a_changed_file_under_the_same_name_is_still_a_new_revision` |
| What the owner typed about a game (`--description`, `--notices`) reaches the game's page as text | `ark-arcade --import`, the game page | `test_arcade.py::test_what_a_person_wrote_about_a_game_reaches_the_page_as_text`; `test_arcade_install.py::test_a_game_the_owner_describes_carries_its_words_onto_the_shelf` |
| A full card is refused before a byte is written | `_ensure_room()`, `InstallRefused` | full-card refusal test |
| A mid-copy `ENOSPC` cleans up the temp file | `_copy_to_temp` | mid-copy test |
| Auto-install only when the owner's switch is on | `arcade_auto_install` setting | `test_arcade_install.py::test_automatic_installing_is_off_unless_the_owner_says_yes` |
| The weekly check records suggestions, it does not install by itself | `ark-arcade-discover.timer`, `--discover --auto` | `test_arcade_install.py::test_discovery_asks_for_the_game_file_when_search_has_no_licence` |

Every row above is a test. The duplicate answers and the owner's words were also
**proved on the box** on 25 Oct 2026 (`docs/IMPLEMENTATION-LOG.md` §18.4): the identical
file given again answered *"already on the shelf"* with one file on the card, the same
bytes under a second name were refused with the name that game already has, and a freshly
imported game's page rendered its own description and its credits with the HTML escaped.

The naming and upload rows are **deployed and proved on the box** (`docs/IMPLEMENTATION-LOG.md`
§19.1): the live database is at layout **v5**, `/srv/ark/romdb.sqlite` holds 56,448 names, a
carried `apotris.gba` is named *Apotris (Game Boy Advance)* while `2048gb.gb` is honestly
*"nobody has listed this file"*, and the whole owner door — sign in → **A game you already
have** → *"This looks like "Apotris" (Game Boy Advance)."* → confirm → one file, one row, two
audit lines, the game on `/arcade/` — ran against a throwaway instance on the box with the real
catalogue and a real 4 MB file (29/29 checks). **A person has still not picked a file in a
browser**, and no game has been added to the live shelf this way.

The multilingual fix matters because a real catalogue entry says `["en", "ja"]`, while a
stored candidate carries `"en, ja"`. Both now resolve to English; a game in `ja` alone is
still refused, and `"no-text"` is no longer mistaken for a language called `no`.

### What is still missing

| Acceptance sentence | State |
|---|---|
| More than one legal source; per-console adapters | **GBA and NES done** — `gbadev-org/games` and `nesdev-org/homebrew-db`, each with its own allowlisted prefix; **SNES and Genesis still absent** (no catalogue entries: hand-carried with `--import`) |
| A durable background job queue with progress, cancellation and retry | **absent** — a run is a timer/CLI invocation, and a killed run leaves no resumable job |
| A populated shelf: target titles and a size budget actually reached | **done** — 23 seeded games (10 GB, 6 GBC, 6 GBA, 1 NES), installed on the box on 25 Oct 2026 and served over `/arcade/`; measured 46.8 MB |
| Compatibility/boot check before publication | **absent** — a file that is the right shape can still fail to boot |
| Archive handling (zip/7z) for owner imports | **absent** — the install path accepts a bare ROM file |
| A place to drop a game file, or a button on the owner's page that adds one | **both are done, and the games page has a door of its own** — **Add a game** at `http://ark.lan/arcade/add` takes one file with no sign-in and a visitor may add but never replace (`test_arcade_add.py`, ten tests), and the **A game you already have** section of the owner's Games page takes one file, shows what the box recognised and publishes only on confirmation (`test_arcade.py`, four tests); the bytes never leave the arcade's own waiting folder, and no watch unit looks at a game folder. Still **absent**: a drop folder the File Box or a stick feeds, zip/7z, cover art, and any per-person quota on the public door (the owner's switch closes it instead) |
| External source verification against official hashes/schemas | **absent** — the live source has never been asked for a file from the box |
| Thumbnails: permission recorded, image fetched or uploaded by the owner, served locally | **absent** — no thumbnail column, route or UI |
| A source-specific rate limit and on-disk metadata cache | **absent** |
| A visible per-candidate decision history for the owner | **partial** — licence/language/review reasons are stored and shown; there is no timeline |

**This is why R7 is PARTIAL:** the safety gate is done, the inventory is not.

---

## R8 — uploads and installs cannot fill the card or starve each other

**Status: PARTIAL.** Per-file refusal and a reserve exist on the File Box and on game
install. One coordinated budget across every writer does not, and there is no admission
control before a request body is ingested.

| Acceptance sentence | Where | Test |
|---|---|---|
| A file over the per-file cap is refused while streaming | `ark_storage.spool()` | `test_storage.py` |
| A write that would leave less than the reserve is refused | `spool()` 512 MiB reserve | `test_storage.py` |
| nginx and the app agree on the maximum body | `client_max_body_size 2064m` ↔ `MAX_FILE` | `test_config.py::test_nginx_allows_the_app_limit_plus_framing_only` |
| A game install checks free space before writing | `_ensure_room()` | `test_arcade_install.py::test_a_full_card_is_refused_before_a_game_is_fetched` |
| A game download that hits `ENOSPC` leaves no temp file | `_copy_to_temp` | `test_arcade_install.py::test_a_card_that_fills_while_copying_leaves_nothing_behind` |
| A named player's save is bounded in size | save layer limits | `test_arcade.py::test_a_save_that_is_too_large_is_refused_before_it_is_written` |
| **One** reservation across File Box + games + saves, enforced before any write | each writer measures its own free space | **absent** |
| Request body refused before it is ingested when the card is nearly full | nginx buffers/streams to a temp location first | **absent** |
| Aggregate caps/quotas for File Box, games and saves | — | **absent** |
| A cross-process lock so two installs cannot both pass the check | no shared lock | **absent** |
| A concurrency test (two writers, one card) | — | **absent** |
| A request body waits **on the card**, not in the 256 MB tmpfs | `client_body_temp_path /srv/ark/tmp/nginx` (all three vhosts), `TMPDIR=/srv/ark/tmp` in `ark-api.service` | `test_config.py::test_a_body_on_its_way_to_disk_waits_on_the_card`; `test_config.py::test_the_app_spills_an_upload_onto_the_card_too` |
| The folder a body waits in exists before nginx is asked to start | `20-storage.sh`, `60-portal.sh`, `65-admin.sh` (`$ARK_ROOT/tmp`, `775`, the `pi` account) | `test_config.py::test_a_body_on_its_way_to_disk_waits_on_the_card` |
| Retention/cleanup of half-arrived bodies | `drop/tmp` and `arcade/tmp` swept by pattern and age; **`/srv/ark/tmp` deliberately not swept** | `test_self_heal.py::test_old_temp_files_are_swept_and_fresh_ones_are_left` |

**The honest sentence:** no single upload or install can silently fill the card. Two of them
at the same time, on different paths, still can — each sees enough room and each takes it.
That is the gap this requirement names, and it is not closed.

---


## A — owner controls through the portal

**Status: PARTIAL.**

| Control | State |
|---|---|
| First-run claim, forced password change, long-password policy | **done + tested** |
| Sign-in, sessions, revoke, throttle, CSRF, security headers | **done + tested** |
| Account management, last-admin protection, audit, enable/disable/reset | **done + tested** |
| Community switches, board moderation, password-confirmed delete | **done + tested** |
| Games page: shelf, licences, hide/show, suggestions, both arcade switches | **done + tested** |
| "What is working" health page, readiness reasons, storage breakdown | **done + tested** |
| Player names: the on/off switch | **done** |
| Per-player management (reset, delete, disable, quota) | **absent** |
| Service controls (restart, stop, pause/resume) | **absent** — no buttons, on purpose |
| Backup/restore buttons | **absent** — the CLI and recovery sheet only |
| Network mode controls beyond `ark-mode` on the console | **absent** |
| Diagnostics: thermal, undervoltage, last restore, failed jobs | **absent** (readiness and storage only) |

The owner can run the shelves, the accounts and the switches. They cannot run the box: no
restart, no copy, no network change, no player reset from the portal. Those are deliberate
absences today, not half-built screens.

---

## B — player experience

**Status: PARTIAL (early).**

| Acceptance sentence | State |
|---|---|
| A guest can open a game and play with no name and no internet | **done + tested** (local asset render) |
| Guest saves live in the browser and never reach the server | **done + tested** |
| A named player has server saves, isolated from other names | **done + tested** |
| Three manual state slots per game, export/import one zip | **done + tested** |
| Revision guard (409), owner-only read (404), page token (403), atomic writes | **done + tested** |
| Favourite games kept at the top; opening stamps `last_played` | **done + tested** (this session) |
| A player switch (autosave / favourite) works without JavaScript | **done + tested** |
| Autosave every 60 s to slot 0, with `X-ARK-Revision` | **implemented**, JS presence asserted; **not run in a browser** |
| Normal in-game SRAM sync for every core, including after a crash | **partial** — manual states + one autosave slot |
| Continue Playing / recently played row | **absent** — `last_played` is stored but not used to order the shelf |
| Rotating autosaves and a visible save history | **absent** — one autosave slot |
| A conflict/overwrite prompt a person can understand | **partial** — 409 + page message, no merge/choose UI |
| Game metadata: ROM, core, controls, compatibility warning | **absent** |
| Thumbnails on the shelf | **absent** |
| Player password recovery, save quota, save trash, owner reset | **absent** |

The preferences work is real, but it is not "the player experience" on its own. A player can
favourite a game and keep one autosave; they cannot see a history, recover a lost save, or
find their recent games without searching.

---

## C — operational reliability

**Status: PARTIAL.** Much of this was delivered in stages 3–6 and is tested; the gaps are the
ones named in `docs/IMPLEMENTATION-LOG.md` §8.5 and §8.9.

| Requirement | State |
|---|---|
| One readiness authority with a reason and an action; `/api/ready` 200/503; liveness demoted | **done + tested** |
| Measured download progress (rate, remaining, ETA, stalled), never a guessed `0` | **done + tested** |
| Storage breakdown + card facts; unmeasurable is `None`, never `0` | **done + tested** |
| Network-loss failover every minute, pins respected, never drops a reader | **done + tested against fakes**; never acted on real hardware |
| Backup bundle, verify, restore with quiesce/resume, recovery sheet | **done + tested on the Mac**; never on the Pi |
| Upload/import/board/search safety fixes | **done + tested** |
| A page offers only files it can really open: `/files` and the owner page count openable files, and a leftover record is reported as *"Nothing here can be opened"*, never counted as content | **done + tested + deployed** (`test_storage.py`, `test_health.py`); `/files` on the box now reads *"The box is empty"* beside the ghost record, and `/api/ready` answers 503 `blocking: ["File Box"]` until the owner deletes it — the sentence has not been read on a screen yet |
| A copy taken with `sudo` is handed to the folder's owner so the box's own pages can read it; a copy they may not open is reported as exactly that, never as "no copy" | **done + tested + deployed** (`test_backup.py`, `test_health.py`, `90-verify.sh`); the live bundle went from `root:root`/`PAGE-CANNOT-READ` to `pi:pi`/**Latest copy** reading it, with the verifier asking as `pi` |
| Thermal/undervoltage/failed-job signals in health | **absent** — readiness covers content, service, storage, copy and network, not the board's temperature |
| True end-to-end search deadline that cancels late workers | **partial** — pool of 6, late answer discarded but the thread keeps its slot |
| Download integrity and indexability checked before a title is marked ready | **partial** — shape and hash at install, no boot/index check |
| A durable background queue with resume after reboot | **partial** — ZIM downloads resume; game discovery/install does not queue |
| Dependency pinning and a tested upgrade path | **partial** — schema migrations are numbered and tested; application dependencies are not fully pinned |
| Installer idempotence and a single verification gate | **done + tested** (`90-verify.sh`, installer tests), but the stages have never run on hardware |

---

## D — household layer

**Status: PARTIAL — deployed 26 Oct 2026, unproven in a person's hands.** The emergency pages
are reviewed, the cards and the pack exist and the household page is real, and each one is proved
by a test that fails without it; `/household`, `/cards`, the owner's **This household** page and
`/essentials.zip` all answer from the box's own nginx (`docs/IMPLEMENTATION-LOG.md` §24). What is
missing is the rest of the house: there is no accessibility audit of the *library* pages (Kiwix's
own reader is third-party markup), no household page in any language but English, no phone has
scanned a printed card, nothing has been printed, the pack has not been opened with the box off,
and no person has filled in the nine answers.

| Acceptance sentence | Where | Test |
|---|---|---|
| Every emergency subject the box claims to cover names the page that carries it, and a subject whose page is missing is **listed as missing**, never assumed | `ark_review.TOPICS`, `ark_review.summary()` | `test_household_page.py::test_a_page_on_the_card_is_listed_and_a_missing_one_is_named`; `::test_every_topic_names_a_page_that_is_really_on_the_card` |
| A topic nobody has read says so; an unreadable date counts as **due**, never as fresh | `ark_review.summary()`, `ark_review.days_since()` | `test_household_page.py::test_a_topic_nobody_has_read_says_so_and_is_due`; `::test_a_date_nobody_can_read_counts_as_due_never_as_today` |
| Reading a page is recorded with who and when, and can be taken back without touching another topic | `/admin/household/read/<topic>`, `/forget/<topic>` | `test_household_page.py::test_reading_a_page_today_records_who_and_what_needed_changing`; `::test_taking_a_look_back_leaves_every_other_topic_alone`; `::test_taking_a_look_back_is_recorded_and_audited` |
| The eight topics are the eight pages on the card — a topic may not name a file that does not exist | `ark_review.TOPICS`, `pi/vault/` | `test_household_page.py::test_every_topic_names_a_page_that_is_really_on_the_card` |
| The household's own answers are the household's words: trimmed, never shortened, refused with a sentence when too long | `ark_household.save()`, `TooLong`, `_tidy` | `test_household.py::test_an_answer_too_long_is_refused_with_the_limit_named_never_cut`; `::test_an_answer_is_trimmed_and_control_characters_are_dropped`; `test_household_page.py::test_an_answer_too_long_is_refused_with_the_limit_named` |
| A corrupt or missing record is an **empty page**, never a 500 | `ark_household.load()`, `main.household_page()` | `test_household.py::test_a_row_nobody_can_read_is_an_empty_page_not_a_broken_one`; `test_household_page.py::test_a_corrupt_row_is_an_empty_record_and_survives_being_written_again` |
| Nine questions, `0 of 9 answered` on a fresh box, and the count moving once written | `ark_household.ENTRIES`, `progress()`; `/admin/household` | `test_household_page.py::test_the_owners_page_says_nobody_has_read_anything_yet`; `::test_saving_an_answer_puts_it_on_both_pages_and_in_the_history` |
| `/household` is public, plain words, no sign-in, and says only what the household told it | `main.household_page()` | `test_household_page.py::test_none_of_this_needs_a_password_from_a_reader`; `::test_the_household_page_is_reachable_from_every_page_and_says_nothing_it_was_not_told`; `::test_the_page_shows_what_a_person_typed_escaped_and_in_its_own_order` |
| Three wall cards — the box's own Wi-Fi name, the home-network name, the number that always works — drawn **by the box** with no image file, no font and no network | `ark_cards.cards()`, `ark_qr.py`, `/cards` | `test_household_page.py::test_the_cards_are_inline_pictures_with_the_address_written_out`; `::test_a_card_needs_no_file_a_font_a_script_or_a_network`; `test_qr.py::test_only_a_short_plain_address_may_be_printed`; `test_household.py::test_the_cards_name_a_way_in_and_when_to_use_it` |
| The squares decode back to the addresses they claim | `ark_qr.encode()` | `test_qr.py::test_the_shape_is_a_qr_symbol_and_not_just_a_square_of_noise`; `::test_both_copies_of_the_format_block_agree_with_the_mask_that_was_used`; `::test_the_picture_has_the_white_margin_the_scanner_needs` — plus two cross-checks against other encoders/decoders (`::test_every_module_matches_an_independent_encoder` needs `qrcode`, `::test_a_real_decoder_reads_the_text_back` needs `opencv-python-headless`), both skipped when the package is absent |
| The pack holds the manuals and guides with links that still work from a folder, the quick card, the recovery sheet, its own front page, and a `MANIFEST.txt` with a sha256 per member (the three wall cards are **not** in it — `/cards` is where they are printed) | `ark_essentials.build()/verify()` | `test_essentials.py::test_a_fresh_pack_is_whole_and_says_what_is_in_it`; `::test_a_link_on_the_box_is_made_relative_at_every_depth`; `::test_the_pack_carries_its_own_front_page_and_a_page_that_says_how_to_use_it`; `::test_nothing_in_the_pack_needs_the_network` |
| A damaged pack is refused, never offered: `.partial` → rename, verified before it is published | `ark_essentials.verify()`, `pack_path()` | `test_essentials.py::test_a_pack_that_is_not_a_pack_is_refused_and_never_crashes`; `::test_a_member_that_changed_is_caught_by_its_fingerprint`; `test_household_page.py::test_a_damaged_pack_is_not_offered` |
| `/essentials.zip` is a fixed name from a folder the box owns; no part of the request chooses a path | `main.essentials_download()` | `test_household_page.py::test_a_pack_that_was_made_is_offered_as_one_file_with_no_path_from_the_request`; `::test_a_reader_may_ask_how_big_it_is_before_downloading_it` |
| No pack yet is a sentence, never a 500 and never an empty file | `main.essentials_download()` | `test_household_page.py::test_a_pack_that_was_never_made_is_a_sentence_not_a_broken_link`; `test_essentials.py::test_there_is_nothing_to_pack_and_it_says_so` |
| A page the proxy does not name answers **with the front page** — `/household`, `/cards` and `/essentials.zip` are named in the `:80` block, the front page offers both doors, and the installer asks what the body says | `pi/nginx/ark.conf`, `60-portal.sh`, `90-verify.sh` | `test_config.py::test_every_page_the_backend_builds_is_named_in_the_proxy_block` |
| Nothing on the household path can make readiness look broken: unread topics and an absent pack are **soft** facts | `90-verify.sh` (`say_soft`), `ark_health` unchanged | `test_household_page.py::test_the_status_counts_what_is_there_and_never_decides_readiness` |
| The portal stays dark, mobile-first, 16 px or better, 44 px targets, and loads nothing from outside the box | `pi/portal/index.html`, `help.html`, `assets/ark.css` | `test_config.py::test_the_portal_stays_dark_large_and_self_contained` |
| A screen reader gets a name for every square | `<svg role="img" aria-label=…>`, `aria-hidden` on the decorative parts | `test_household_page.py::test_a_square_is_a_named_picture_so_a_screen_reader_can_read_it`; `test_qr.py::test_the_picture_describes_itself_and_points_at_nothing_else` |

**What is still missing:** an accessibility pass over the *library* and *book shelf* pages
(third-party markup: Kiwix's reader and Calibre-Web's templates are not ours to rewrite);
translation of any of this; a printable one-page household sheet that combines the answers
and the cards on one sheet of paper (the cards and the answers are separate pages today);
and the deploy itself — `90-verify` has not yet run the three new checks on the Pi, and no
household member has opened `/household` on a phone.

---


## Scoreboard

| ID | Status | Rough share of its acceptance sentences met |
|---|---|---|
| R1 | DONE (code + test) | all; hardware run unproven |
| R2 | DONE (code + test) | all; on-box TLS unproven |
| R3 | DONE (code + test) | all; on-box door unproven |
| R4 | DONE (code + test) | all; on-box restore unproven |
| R5 | PARTIAL | write safety + headers yes; admission policy no |
| R6 | DONE (code + test) | all; on-box nginx unproven |
| R7 | PARTIAL | gate yes; inventory/queue/metadata no |
| R8 | PARTIAL | per-writer refusal yes; one shared budget no |
| R9 | DONE (code + test + proved on the box) | all; search quality unmeasured |
| R10 | DONE (code + test) | all on the Mac; **no model on the box, nothing in a browser, not deployed** |
| R11 | DONE (code + test) | the pass and its settings yes; **never met a real power cut, phase not run on the box** |
| A | PARTIAL | accounts/community/games/health yes; players/services/backup UI no |
| B | PARTIAL (early) | guest play + named saves + preferences yes; history/metadata/visuals no |
| C | PARTIAL | stage 3–6 fixes yes; thermal/jobs/search cancellation no |
| D | PARTIAL (code + test) | review, cards, pack and household page yes; library accessibility, translation and one-page print no |

**What "DONE (code + test)" deliberately does not mean:** deployed, seen on a phone, or
measured on the card. Every DONE row above still needs its line in the §8.9 walk-through
before a tag. The suite is evidence that the code does what the tests say; it is not
evidence about the box.

## The next five moves, in order

1. **Deploy and walk through stages 2–6 + arcade** (`docs/IMPLEMENTATION-LOG.md` §8.9).
   Nothing below is worth more than this, because every DONE row is unproven on hardware.
   The deploy has happened (§8.12a); what remains is the phone walk-through and a
   `--only 60-portal,95-backup` redeploy for the two page-truth fixes found afterwards
   (§16.2–16.3 of `docs/IMPLEMENTATION-LOG.md`).
2. **R5 admission policy** — default both public switches to closed on a fresh box, add a
   `pending` state and an owner approval step, keep reading/downloading open, and write the
   test that proves a closed box still serves everything already published. This is a small
   change with a large safety return.
3. **R8 one budget** — a single `ark_storage.reserve()` consulted by the File Box, the game
   installer and the save writer, with a cross-process lock and a two-writer test. Without
   it, the per-writer checks are honest but not sufficient.
4. **R7 thumbnails + a second source** — add a thumbnail column, a local route and an owner
   upload/fetch step (the Games page does not offer one today), then add one non-Game-Boy
   source so the shelf is not a single point of failure. Only after that is a population run
   meaningful.
5. **Keep R3 honest** — the 404 door and its reading list are transcribed from Calibre-Web
   0.6.27. Re-enumerate `calibreweb/cps/*.py` and update the config, the test and this table in
   one commit the day the package version changes, and add a reading route to the installer's
   proofs whenever the shelf page starts using a new one.

## R9 — a search finds what is on the card

**Status: DONE (code + test + proved on the box, 25 Oct 2026).** The portal asks each
collection by the name that collection answers to, and it asks the library at the
address the library is actually mounted on. Both were wrong, and both failed silently:
search answered "nothing matched" for every word while `/kiwix/` served every book.

| Acceptance sentence | Where | Test |
|---|---|---|
| A collection is asked for by its ZIM's file name, not its UUID | `ark_health.library_books()` (`stem`), `main._handle()` | `test_health.py::test_a_book_is_asked_for_by_the_name_of_its_file`; `test_search.py::test_a_collection_is_asked_for_by_the_name_of_its_file` |
| An index that cannot name a file says so instead of guessing the short name | `ark_health._book_stem()` | `test_health.py::test_a_book_whose_path_is_unusable_says_so_instead_of_guessing` |
| A book we could not name is still asked (it must not vanish, or crash the page) | `main._handle()` | `test_search.py::test_a_book_without_a_file_name_still_gets_asked` |
| A result names the collection that owns it, by handle or by id | `main.search_library()` | `test_search.py::test_a_collection_is_asked_for_by_the_name_of_its_file` |
| The catalogue is built beside the bundles it describes, so its paths exist | `pi/bin/ark-kiwix-library` | `test_config.py::test_the_library_catalogue_is_built_beside_the_bundles_it_describes` |

The endpoint is part of the contract: the library is mounted at `/kiwix` (nginx proxies
it there), so its search is `/kiwix/search` — a request to bare `/search` returns the
library's 404 page, whose body says "Content not found" and reads exactly like a failed
lookup. `ark_health.kiwix_base()` carries the prefix; probes built by hand must too.

**Not proved:** search quality (which articles are ranked first) and behaviour with a
collection whose full-text index is missing. Both are the library's business, not the
portal's, and neither is measured here.

---

## R10 — a question is answered on the box, and the box is honest when it cannot

**Status: DONE on the Mac (code + tests, 26 Oct 2026); NOT deployed, and never seen in a
browser.** `/ask` answers a question typed in a person's own words, from a small model
that lives on the card: no internet, no account, nothing stored. The requirement that
matters most is the negative one — **a helper that is not fitted must never look like a
helper that is thinking**, and the rest of the box must not care either way.

| Acceptance sentence | Where | Test |
|---|---|---|
| With no engine and no model the page says so in one sentence and still offers the form | `main.ask_page()`, `ark_ai.not_ready_sentence()` | `test_ai.py::test_a_helper_that_is_not_fitted_says_so_and_offers_the_form` |
| A question with nothing behind it is not answered with invented words | `ark_ai.ask()` | `test_ai.py::test_a_question_with_no_helper_behind_it_is_not_answered` |
| An engine that is fitted but not answering says *that*, instead of *not fitted* | `ark_ai.answering()`, `not_ready_reason()` | `test_ai.py::test_a_fitted_helper_that_is_not_answering_says_that_instead` |
| The question is escaped wherever it is echoed back | `main.ask_page()` (`esc()` on the cleaned question and on every note), `main._ask_paragraphs()` | `test_ai.py::test_a_question_is_escaped_wherever_it_is_echoed` |
| A question that is too long is refused **with the limit named**, never silently cut | `ark_ai.clean_question()` | `test_ai.py::test_a_question_that_is_too_long_is_refused_with_the_limit_named`; `::test_the_cleaner_trims_and_names_the_limit_rather_than_cutting` |
| An answer is escaped and broken into paragraphs, so a model's line breaks cannot become markup | `main._ask_paragraphs()` | `test_ai.py::test_an_answer_is_escaped_and_broken_into_paragraphs` |
| A question about water is sent to the guides and a question about a book to the library | `ark_ai.route()`, `context_for()` | `test_ai.py::test_a_water_question_is_sent_to_the_guides_and_a_book_question_to_the_library` |
| The model is given the card's own pages — and **never** the household's answers, the board or the File Box | `ark_ai.messages()`, `context_for()` | `test_ai.py::test_the_helper_reads_the_pages_and_never_the_households_own_answers`; `::test_the_brief_never_lends_the_model_the_households_own_words` |
| The brief is written by rename and a dry run writes nothing | `ark_ai.build_brief()`, `pi/bin/ark-build-ai-brief` | `test_ai.py::test_the_brief_is_published_by_rename_and_a_dry_run_writes_nothing` |
| An answer arrives a piece at a time, and the stream says why when there is no answer | `main.ask_stream()`, `ark_ai.ask_stream()` | `test_ai.py::test_the_answer_streams_to_the_page_a_piece_at_a_time`; `::test_the_stream_says_why_there_is_no_answer_rather_than_answering_nothing` |
| One answer is written at a time; the second person is told so — **with the real wait in seconds** — and sees no half-written sentence | `ark_ai.begin()`, `end()`, `busy_for()`, `BUSY_SENTENCE`, `busy_sentence()` | `test_ai.py::test_one_answer_is_written_at_a_time`; `::test_the_wait_is_named_in_seconds_and_not_just_in_minutes`; `::test_the_slot_is_a_lease_that_names_who_holds_it` |
| A slot left behind by a question nobody is reading is taken back, and a question whose reader has gone stops the engine and frees the slot at once | `ark_ai.begin()` (takeover), `cancel()`, `_live`, `main.ask_stream()` | `test_ai.py::test_a_slot_left_behind_is_taken_back_rather_than_refusing_for_ever`; `::test_the_engine_is_unhooked_from_the_moment_the_reader_goes`; `::test_the_page_gives_the_slot_back_the_moment_its_reader_goes`; `::test_nothing_is_left_hooked_or_held_after_an_answer` |
| The page never sends a second question behind its own first | `main.ask_page()` (the page's own script) | `test_ai.py::test_the_page_asks_one_question_and_never_a_second_behind_the_first` |
| An engine that dies mid-answer keeps the words already written, and the page still answers 200 | `ark_ai.ask_stream()` | `test_ai.py::test_an_engine_that_dies_mid_answer_keeps_what_it_wrote` |
| A truncated answer is one honest sentence, not a broken page | `main.ask_page()` | `test_ai.py::test_a_truncated_answer_is_one_honest_sentence_and_not_a_broken_page` |
| The switch closes the page **and** the address the page streams from | `main.ask_page()`, `main.ask_stream()`, `public_ask` | `test_ai.py::test_the_switch_closes_both_doors_and_not_only_the_page` |
| The helper is reported on the owner's page and **never** blocks readiness | `ark_health.ask_section()`, `BLOCKING_SECTIONS` | `test_ai.py::test_the_helper_is_reported_on_the_owner_page_and_never_blocks_readiness` |
| A model is copied in from a stick, a wrong fingerprint keeps **nothing**, and a half-arrived file is invisible | `pi/bin/ark-ai-model-fetch` | `test_ai.py::test_a_model_is_copied_in_from_a_stick_and_nothing_is_left_half_arrived`; `::test_a_fingerprint_that_does_not_match_keeps_nothing_at_all`; `::test_the_fingerprint_is_compared_without_caring_about_case`; `::test_a_download_still_arriving_is_invisible_to_the_helper` |
| The engine is started on the loopback address, with one slot, from the newest model | `pi/bin/ark-ai-serve` | `test_ai.py::test_the_engine_is_started_on_the_loopback_address_with_one_slot` |
| A box with no engine stops instead of restarting for ever; a box with no model waits and starts by itself when words arrive | `condition`s in `ark-ai.service`, `ark-ai-serve` | `test_ai.py::test_the_service_refuses_to_run_with_no_engine_instead_of_restarting_for_ever`; `::test_the_helper_waits_for_words_and_starts_by_itself_when_they_arrive` |
| A fresh box with no model is still a working box: the phase finishes, the unit is enabled | `pi/steps/58-ai.sh`, `ark-ai.service` | `test_ai.py::test_the_phase_installs_the_helper_and_the_brief_that_feeds_it`; `::test_the_unit_stops_cleanly_on_a_box_with_no_engine_and_cannot_grow_without_limit` |

**Not proved, and must be said out loud:** no model has been fitted on the box, so the
page has never answered a real question there; `/ask` has never been drawn by a browser,
human or headless; the stream has never crossed nginx on the box (only the backend test
client); and the switch has never been flipped on a live installation. Section 14 of
`docs/HUMAN-TEST-LIST.md` covers what a person must do.

---

---

## R11 — a power cut is not a reboot

**Status: DONE (code + test); the phase has not been run on the box and the pass has
never met a real power cut.** A Pi in a house has its plug pulled. Phase `15-resilience`
installs one tool that runs **before anything serves a page** (`Before=ark-api.service`)
and once a day, and the settings that make the next cut cheaper. The requirement that
matters most is the negative one: **a repair tool must never be able to lose a file that
was not already broken.**

| Acceptance sentence | Where | Test |
|---|---|---|
| A clock in the past is put forward to the newest thing on the card, and a file written a moment ago is not evidence | `ark_self_heal.fix_clock()`, `CLOCK_SLACK` | `test_self_heal.py::test_a_file_from_the_future_means_the_clock_is_behind`; `::test_a_file_written_a_moment_ago_is_not_evidence`; `::test_the_clock_is_put_forward_to_the_newest_thing_on_the_card` |
| The time is remembered across a cold boot, and a clock that agrees says nothing alarming | `fix_clock()` (fake-hwclock), `ark-self-heal --quiet` | `::test_the_clock_is_remembered_for_the_next_cold_boot`; `::test_a_clock_that_agrees_with_the_card_says_nothing_alarming` |
| A small file that will not read is set aside, and the copy from before is put back only if **it** reads | `heal_json()` | `::test_a_broken_file_is_set_aside_and_the_copy_from_before_put_back`; `::test_a_broken_file_with_no_copy_is_set_aside_never_deleted`; `::test_a_good_copy_that_is_itself_broken_is_never_put_back` |
| A file that is simply absent is not a problem; one that vanished is put back from its copy | `heal_json()` | `::test_a_file_that_is_simply_not_there_is_not_a_problem`; `::test_a_file_that_vanished_is_put_back_from_its_copy` |
| The watchdog's counters are checked by **their own** shape, not as JSON, and are never filed back under JSON | `STATE_KEYS`, `state_problem()`, `heal_state()`, `JSON_TARGETS` | `::test_healthy_counters_are_left_alone`; `::test_damaged_counters_are_set_aside_and_the_watchdog_starts_again`; `::test_counters_that_are_not_numbers_are_damage`; `::test_missing_counters_are_not_a_fault` |
| The counters are written in one move, so a cut cannot leave half a file | `write_state()` in `pi/bin/ark-net-watch` | `test_network.py::test_the_watchdog_writes_its_counters_in_one_move` |
| Only the newest three broken copies are kept, and a dot-temp file from a cut is swept | `CORRUPT_KEEP`, `sweep()` | `::test_only_the_newest_three_broken_copies_are_kept`; `::test_a_half_written_counter_file_is_swept_and_the_real_one_is_not` |
| A database SQLite cannot open is replaced only by a copy that passes the same check, and one with no good copy is left exactly where it is | `heal_sqlite()` | `::test_a_database_that_cannot_be_read_is_put_back_from_its_good_copy`; `::test_a_broken_database_with_no_copy_is_left_exactly_where_it_is` |
| A database whose `-shm` is gone after a cut is healthy, and a WAL missing its commits is never copied | `heal_sqlite()` | `::test_a_database_whose_shared_memory_is_gone_after_a_cut_is_healthy`; `::test_a_database_is_not_copied_into_a_wal_missing_its_commits` |
| Old temp files and half-built copies are swept; fresh ones, a finished copy and a database journal are not | `sweep()` | `::test_old_temp_files_are_swept_and_fresh_ones_are_left`; `::test_a_half_built_copy_is_swept_but_a_finished_one_is_not`; `::test_the_database_journal_is_never_swept` |
| A catalogue pointing at books that are not there is rebuilt; a sound one is left alone; no builder is reported, never guessed | `heal_library()` | `::test_a_catalogue_that_points_at_missing_files_is_rebuilt`; `::test_a_catalogue_that_cannot_be_rebuilt_is_reported_and_not_broken` |
| The sweep is judged by the clock **after** it is fixed | `run()` ordering | `::test_the_sweep_is_judged_by_the_clock_after_it_is_fixed` |
| A repair counts as repaired; only what is left for a person is a problem, and only that exits 1 | `run()` (`healed` / `problems`), `ark-self-heal` | `::test_the_command_exits_one_when_something_is_left_to_look_at`; `::test_a_quiet_pass_on_a_healthy_box_says_nothing`; `::test_a_pass_repairs_everything_and_answers_a_report` |
| A dry run changes nothing at all, at the module and at the command | `dry_run` everywhere | `::test_a_dry_run_changes_nothing_at_all`; `::test_a_dry_run_at_the_command_leaves_no_trace`; `::test_a_dry_run_sweeps_nothing` |
| The boot pass runs before the portal and the daily pass is armed | `ark-self-heal.service` / `.timer`, phase `15-resilience` | `::test_the_phase_is_installed_before_anything_that_serves_a_page`; `::test_the_phase_enables_both_units_and_proves_itself` |
| The phase sets the things that make a cut cheap, and can never leave the box unbootable | `15-resilience.sh` (`fstab`/`cmdline.txt` edits, one-line guard) | `::test_the_phase_sets_the_things_that_make_a_cut_cheap`; `::test_the_phase_can_never_leave_the_box_unbootable` |
| The verifier reads the record the pass leaves, keeps the receipt from before, and never fails on an absent pass | `90-verify.sh` | `::test_the_verifier_reads_the_record_the_boot_pass_leaves_behind`; `::test_the_verifier_keeps_the_receipt_from_before` |
| The forced check belongs to **one** boot, and is taken back off the boot line by that boot | `pi/bin/ark-fsck-once`, `pi/systemd/ark-fsck-once.service` | `::test_the_forced_check_is_withdrawn_from_the_boot_line_once`; `::test_a_later_boot_has_nothing_to_withdraw_and_changes_nothing`; `::test_a_dry_run_withdraws_nothing` |
| The withdrawal can never make the box unbootable: two lines, a lost `root=`, or anything not the old line minus two words is refused | `ark-fsck-once` guard | `::test_a_cmdline_that_is_already_two_lines_is_left_alone`; `::test_a_line_that_would_lose_root_is_never_written` |
| The phase arranges the withdrawal, enables it, and deliberately does not start it | `15-resilience.sh`, `ark-fsck-once.service` | `::test_the_unit_runs_the_withdrawal_on_the_boot_that_used_the_check`; `::test_the_phase_installs_the_withdrawal_and_does_not_run_it_now` |
| The gate reports the check as withdrawn / waiting / absent, and a box that has not rebooted is **waiting**, not broken | `90-verify.sh`, `"card_check"` in the receipt | `::test_the_verifier_reports_the_forced_check_without_failing_a_box_that_waiting` |
| A dry run writes nothing at all — no journal mode, no copy, no sweep | `heal_sqlite()` (`dry_run` first) | `::test_a_dry_run_writes_nothing_at_all`; `::test_a_dry_run_on_a_broken_database_reports_and_restores_nothing` |
| A pass that meets a live writer diagnoses and never repairs under it | `writer_busy()` | `::test_a_real_writer_stops_the_pass_touching_its_database`; `::test_a_broken_database_the_box_is_writing_to_is_left_where_it_is`; `::test_the_pass_reports_a_database_it_could_not_repair_because_it_was_in_use` |
| A file stamped **years** ahead is not trusted with the clock | `clock_too_far()`, `CLOCK_MAX_AHEAD` | `::test_a_file_from_the_far_future_is_not_trusted_with_the_clock` |
| A confirmed commit is on the card, and no module may weaken the connection the box writes with | `ark_db.connect()` (`synchronous=FULL`) | `test_durability.py::test_every_connection_writes_fully_synced`; `::test_no_module_weakens_the_connection_the_box_writes_with`; `::test_a_commit_the_box_confirmed_is_readable_from_a_connection_that_was_not_there` |
| A save a person confirmed is whole before any record names it, and a cut between bytes and row leaves the last good save readable | save layer (`blobs/<slot>-<sha256>.bin`, `.save-*.part`, fsync + folder fsync) | `test_durability.py::test_a_save_is_staged_beside_itself_and_its_folder_is_synced`; `::test_a_write_that_fails_leaves_nothing_that_looks_like_a_save`; `::test_a_save_that_could_not_be_recorded_leaves_the_last_one_readable`; `::test_the_save_a_new_one_replaces_is_forgotten_only_after_the_record_moved` |
| A record whose bytes have gone answers nothing rather than a lie, and cannot name a file outside its own folder | save layer | `test_durability.py::test_a_record_whose_bytes_have_gone_answers_nothing_rather_than_a_lie`; `::test_a_record_cannot_name_a_file_outside_its_own_folder` |
| Nothing a visitor waits for waits for the internet; a unit that really fetches still does | `ark-api.service`, `ark-kiwix.service`, `ark-calibre.service` (`After=local-fs.target`) vs `ark-zim-downloader`, `ark-arcade-discover` | `test_config.py::test_nothing_a_visitor_waits_for_waits_for_the_internet`; `::test_only_the_fetching_units_wait_for_the_internet` |

**Not proved, and must be said out loud:** the phase has not been run on the box, no
reboot has been taken with it installed, `fsck.mode=force` has never checked that card,
`ark-fsck-once` has never withdrawn a line from a real `/boot/firmware/cmdline.txt`, the
hardware watchdog has never reset anything, and **no power cut has been simulated on
hardware** — every test drives a throwaway folder tree on the Mac. The durable commit
(`synchronous=FULL`) and the blob-per-save layout are proved against real SQLite files
and a real folder, not against a plug being pulled mid-write.
`docs/HUMAN-TEST-LIST.md` **§15** is the cut-the-power walk-through (pull the plug, wait,
read `logs/self-heal.json`, then confirm the forced check is gone and the second boot is
fast) and **§16** is the boot with nothing to connect to.

---

## Keeping this page honest

* Change a status **in the same commit** as the behaviour and its test.
* A new test that exercises a helper is not proof the shipped route calls it; say which
  route is proved.
* Never move a row from PARTIAL to DONE because the code "looks right". The missing
  sentence has to be in the table, or it is not missing.
* If a row says "done + tested on the Mac", it is still not done on the box.

