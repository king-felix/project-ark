# Project ARK — requirements matrix

**Date:** 25 October 2026  
**Branch:** `feature/admin-hardening`  
**Suite:** `380 passed, 2 skipped` (21 files; `python -m pytest -o addopts='--strict-markers' -q`, Mac, throwaway databases) — the two skips are QR cross-checks that need `qrcode` / `opencv-python-headless` on the machine  
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
| Retention/cleanup and a configurable temp location | fixed paths, startup sweep only | **partial** |

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

**Status: PARTIAL (code + test), not deployed.** The emergency pages are reviewed, the
cards and the pack exist and the household page is real, and each one is proved by a test
that fails without it. What is missing is the rest of the house: there is no accessibility
audit of the *library* pages (Kiwix's own reader is third-party markup), and no household
page in any language but English.

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
| The pack holds the manuals and guides with links that still work from a folder, its own front page, and a `MANIFEST.txt` with a sha256 per member | `ark_essentials.build()/verify()` | `test_essentials.py::test_a_fresh_pack_is_whole_and_says_what_is_in_it`; `::test_a_link_on_the_box_is_made_relative_at_every_depth`; `::test_the_pack_carries_its_own_front_page_and_a_page_that_says_how_to_use_it`; `::test_nothing_in_the_pack_needs_the_network` |
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

## Keeping this page honest

* Change a status **in the same commit** as the behaviour and its test.
* A new test that exercises a helper is not proof the shipped route calls it; say which
  route is proved.
* Never move a row from PARTIAL to DONE because the code "looks right". The missing
  sentence has to be in the table, or it is not missing.
* If a row says "done + tested on the Mac", it is still not done on the box.

