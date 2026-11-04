# Project ARK — requirements matrix

**Date:** 3 November 2026
**Branch:** `feature/admin-hardening`
**Suite:** `893 passed, 2 skipped` (`895 collected`, 36 files; `python -m pytest tests --ignore=tests/test_maps_browser.py -o addopts='--strict-markers' -q`, Mac, throwaway databases) — the two skips are QR cross-checks that need `qrcode` / `opencv-python-headless` on the machine; the three Chromium maps tests are excluded by the project's usual command
**Hardware:** **deployed 25 Oct 2026** — `90-verify` green, `/api/ready` 200, the doors, the book
route rules, a bundle and the arcade origin all answered on the Pi (`docs/IMPLEMENTATION-LOG.md`
§15), `ark-arcade --import` answered honestly three times on the box (§18.4), and the name-from-the-
bytes work is deployed too — layout **v5**, the catalogue on the card, and the owner's upload door
proved end to end against a throwaway instance (§19.1). No claim below
should still be read as "a person has used it"; the phone walk-through in
`docs/HUMAN-TEST-LIST.md` has not been done.

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
| The front page of the desk measures rather than decorates: how long the box has been on, how much room is left, how many files a reader can open, how many notes are waiting — and a figure nobody could measure says *not measured yet*, never `0` | `ark_admin._dashboard`/`_stat`, `ark_health.card_room`/`uptime_seconds`/`uptime_words` | `test_admin_dashboard.py::test_the_overview_shows_what_an_owner_opens_it_for`; `::test_the_room_left_is_the_filesystems_own_answer`; `::test_a_page_that_could_not_measure_says_so_and_never_says_zero`; `::test_uptime_is_read_from_what_the_machine_says`; `::test_a_machine_that_will_not_say_how_long_it_has_been_on_is_not_a_zero` |
| Signing out is a deliberate press (`POST` + form token), ends the session on every device at once, and a forged token changes nothing | `ark_admin.logout`/`_sign_out`, `ark_security.drop_session` | `test_admin_dashboard.py::test_signing_out_is_a_post_with_the_form_token`; `::test_signing_out_really_ends_the_session`; `::test_a_sign_out_without_the_form_token_leaves_the_session_alone` |
| Every administrator page is one tap away, the page being read is marked, and the address list names every page on offer | `ark_admin.ADMIN_NAV`/`nav_here`, `pi/api/ark_doors.py` | `test_admin_dashboard.py::test_the_navigation_marks_the_page_being_read`; `::test_a_page_deeper_inside_a_section_belongs_to_that_section`; `::test_every_navigation_item_is_a_page_the_application_really_serves`; `::test_every_page_in_the_navigation_is_also_named_in_the_address_list`; `::test_the_addresses_page_makes_every_reachable_door_a_link`; `test_admin_upload.py::test_every_administrator_page_is_named_in_the_addresses` |
| A public page says where the owner's door is in plain words, and never as a link (that door refuses plain HTTP) | `pi/api/main.py` footer, `pi/portal/index.html` | `test_admin_dashboard.py::test_the_public_pages_say_where_the_owner_pages_are`; `::test_the_front_page_the_box_serves_has_the_same_sentence`; `::test_the_public_footer_still_explains_itself_in_plain_words` |

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

**Status: DONE (code + test).** Restore verifies every byte first, takes the repair turn
and then the exclusive maintenance gate, stops every writer (including the independent
film worker), and starts again only what it stopped and only when the outcome is safe.

| Acceptance sentence | Where | Test |
|---|---|---|
| Every writer is paused before the database is touched | `pi/bin/ark-restore` `quiesce()` | `test_backup.py::test_a_restore_pauses_every_writer_before_touching_the_database` |
| The film worker is stopped even though the API's stop does not own it, and is started again only because it was running | `WRITERS`, `quiesce()`, `resume()` | `test_backup.py::test_a_restore_stops_the_media_worker_as_well_as_the_api`; `::test_a_restore_starts_only_what_it_stopped` |
| A live reader (a real `ark_db` connection in another process) makes the restore refuse, and the next attempt after it is gone succeeds | `ark_maintenance.maintain()` in `restore()` | `test_backup.py::test_a_restore_refuses_while_a_reader_holds_the_records` |
| A failed install is rolled back to the coherent original; only a rollback that itself fails is `unsafe_to_start` | `put_database()` / `ark_db_replace.replace()` `RestoreRefused.safe_to_start` | `test_backup.py::test_put_database_rolls_back_a_failed_install_and_only_calls_an_unresolved_one_unsafe` |
| The candidate is staged and checked on the destination filesystem before the original is touched; a bad candidate leaves no preserved-original folder | `ark_db_replace._stage_candidate()` | `test_db_replace.py::test_a_candidate_that_is_not_a_database_is_refused_before_preservation` |
| The original main file and its `-wal`/`-shm` are preserved as one coherent set; the journal is moved, never deleted | `ark_db_replace._preserve_original()` / `_move_sidecars()` | `test_db_replace.py::test_a_verified_replace_keeps_the_wal_only_rows_in_the_recovery_set`; `::test_a_kill_after_the_journal_moves_is_rolled_back_on_the_next_startup` |
| A killed replacement is resolved at the next startup: abandon, roll back with all WAL rows, or finish the verified install | `ark_db_replace.recover_pending()` | `test_db_replace.py::test_a_kill_before_the_journal_moves_is_set_aside_without_touching_the_records`; `::test_a_kill_after_the_install_is_finished_on_the_next_startup`; `::test_a_kill_after_verification_or_commit_is_rolled_forward` |
| A pending replacement keeps `ark_db` from opening the file even if a service starts before the boot repair | `ark_db.connect()` / `ark_db_replace.pending()` | `test_db_replace.py::test_a_pending_replacement_stops_ark_db_from_opening_the_file` |
| Staging, sync, sidecar, rename and post-install verification failures leave the original coherent or leave a marked blocked state | `ark_db_replace` sequence | `test_db_replace.py::test_a_staging_failure_leaves_the_original_and_journal_untouched`; `::test_a_sidecar_move_failure_puts_the_original_back`; `::test_an_install_rename_failure_puts_the_original_back`; `::test_a_post_install_verification_failure_rolls_back`; `::test_an_unresolvable_failure_leaves_a_blocked_marker_and_services_stopped` |
| Insufficient space and a read-only card are refused before the original is touched | `_check_room()`, maintenance lock | `test_db_replace.py::test_insufficient_space_is_refused_before_a_recovery_folder_is_made`; `::test_a_read_only_destination_is_refused_without_touching_the_database` |
| Recovery evidence is retained until verification and pruned by count and a byte ceiling; a pending folder is never deleted | `ark_db_replace.prune()` | `test_db_replace.py::test_retention_keeps_the_newest_sets_and_never_a_pending_one`; `::test_the_retention_byte_ceiling_never_deletes_the_newest_set`; `::test_recovery_evidence_is_never_swept` |
| A backup is a reader too: it may not copy across a replacement | `ark_bundle.copy_database()` | `test_backup.py::test_a_backup_refuses_while_the_records_are_being_replaced` |
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

**Status: DONE (code + test).** One policy (`ark_space`) owns the reserve, every
growth path takes a cross-process reservation before it writes, and a request body
is admitted **before the first byte is read** — declared, chunked, or both. The
whole design, including the inventory of every path and what it cannot do, is
[`docs/STORAGE-ADMISSION.md`](STORAGE-ADMISSION.md).

| Acceptance sentence | Where | Test |
|---|---|---|
| A file over the per-file cap is refused while streaming | `ark_storage.spool()` | `test_storage.py` |
| A write that would leave less than the reserve is refused before the body arrives | `ark_admission` middleware → `ark_space.need()` | `test_storage.py::test_a_full_box_refuses_before_writing`; `test_media.py::test_a_declared_upload_that_cannot_fit_is_refused_before_it_is_written` |
| The reserve is 7 % of the receiving filesystem, or 2 GiB, measured with `f_bavail` | `ark_space.reserve_for()`, `ark_space.facts()` | `test_space.py::test_the_reserve_is_seven_percent_of_the_card_that_holds_the_bytes`; `::test_a_small_card_still_keeps_two_gibibytes`; `::test_free_space_is_what_an_ordinary_account_may_write` |
| The proxy refuses a body no page asked for, and never buffers one before admission | `client_max_body_size 64k` server-wide; `proxy_request_buffering off` on every listener; per-door limits | `test_config.py::test_the_proxy_default_body_is_small_and_buffering_is_off`; `::test_the_file_box_door_allows_a_film_and_the_server_keeps_the_smaller_cap`; `::test_the_save_door_did_not_grow_to_a_game_size` |
| Every address that takes a body has a named door; an unnamed one is 64 KiB | `ark_admission.DOORS`, `PREFIX_DOORS`, `DEFAULT_DOOR` | `test_admission.py::test_an_address_with_no_door_is_small_not_unbounded` |
| A declared body reserves its peak before the first byte is read | `Admission.begin()` | `test_admission.py::test_a_declared_body_is_refused_before_a_byte_is_read` |
| The peak counts the framework copy and the route copy, not the file size | `Door.copies`, `Admission.begin()`, `Receipt.peak()` | `test_admission.py::test_the_peak_counts_the_framework_copy_and_the_route_s_copy` |
| A body with no `Content-Length` is admitted in bounded steps | `Admission.saw()`, `ark_space.first_step()/growth_step()` | `test_admission.py::test_a_chunked_body_is_admitted_in_bounded_steps`; `::test_a_chunked_body_bigger_than_the_card_is_stopped` |
| A header is not proof of size | `Admission.saw()` (`declared + 1 MiB` bound) | covered by the door limit + the chunked tests |
| The route's spool adopts the request's promise instead of a second one | `ark_space.cover()`, `contextvar` through anyio | `test_admission.py::test_the_spool_adopts_the_request_promise_end_to_end`; `::test_a_route_adopts_the_request_s_promise_instead_of_promising_twice` |
| Two real processes cannot be promised the same bytes | `space_reservations`, `BEGIN IMMEDIATE` | `test_admission.py::test_six_processes_cannot_be_promised_the_same_bytes` |
| Bounded concurrency: a fifth large body is told to come back | `BodyAdmissionMiddleware`, `ARK_BODY_SLOTS` | `test_admission.py::test_only_a_bounded_number_of_large_bodies_are_received_at_once` |
| A disconnect or a cancellation releases the promise | middleware `finally` | `test_admission.py::test_a_client_that_walks_away_leaves_no_promise` |
| Bytes already written are not counted twice | `space_reservations.written`, `Receipt.spent()` | `test_space.py::test_bytes_already_written_are_not_counted_twice`; `test_admission.py::test_bytes_written_are_not_promised_twice` |
| `reserved` is a peak, not a sum of stages | `Receipt.peak()` | `test_admission.py::test_a_promise_is_the_peak_on_disk_not_a_sum_of_every_stage` |
| A promise belongs to a process: a live worker keeps it, a dead one loses it | `ark_space.live()`, `pid`, `boot` (migration 8) | `test_admission.py::test_a_live_worker_keeps_its_promise_however_old_the_heartbeat_is`; `::test_a_killed_worker_s_promise_is_released`; `::test_a_promise_from_a_previous_boot_is_released_even_if_its_pid_is_alive`; `::test_a_clock_that_jumped_backwards_does_not_release_a_promise` |
| An unreadable ledger is not an empty one | `LedgerUnreadable`, `committed_for() -> None`, `slack() -> None` | `test_admission.py::test_a_bulk_write_is_refused_when_the_ledger_cannot_be_read`; `::test_a_page_says_not_measured_rather_than_zero` |
| Recovery, deletion and the owner's pages still work when admission refuses | `essential=True`, `ark_space.essential_room()` | `test_admission.py::test_an_essential_write_is_still_admitted_when_the_ledger_is_broken`; `::test_the_recovery_copy_is_kept_even_when_admission_would_refuse`; `::test_deleting_a_file_still_works_when_the_card_is_full` |
| Somebody else filling the card stops the upload | `Admission.refresh()` every 4 MiB | `test_admission.py::test_another_writer_filling_the_card_stops_the_upload` |
| A conversion's output is bounded, not just its input | `ark_media_jobs.output_budget()`, `-fs`, `ark_space.hold()` | `test_media.py::test_a_codec_a_browser_cannot_read_is_converted` |
| Every shell path reserves, not merely checks | `ark-space --reserve/--touch/--release` in `ark-zim-download`, `ark-ai-model-fetch`, `ark-backup`, `ark-import-books` | `test_space.py::test_the_command_says_yes_quietly_and_no_with_a_reason` |
| The owner can see total, free, kept back, promised and usable | `ark_space.account()`, `/admin/health`, `/admin/media` | `test_media.py::test_the_owner_page_lists_films_and_can_publish_one`; `test_health.py` |
| Half-finished copies are swept, resumable ones are not | `ark_self_heal.SWEEP_RULES` | `test_self_heal.py::test_old_temp_files_are_swept_and_fresh_ones_are_left` |
| Per-person quotas | — | **absent by design** (a box in a room, not a service) |

**The honest sentence:** no application write can take the card into the space the
box keeps back, two jobs started together cannot both be promised the same bytes,
and a body that has not been admitted is not read. Two limits stay, and both are
stated on the pages that do the work: an *unrelated* OS or shell write can still
fill the card — the box notices on the next step and stops its own work, but the
reserve is an admission policy, not a partition; and a multi-gigabyte upload peaks
at about twice its size, because the framework's temp copy and the route's copy
exist together, which is why the promise is `2 × the file` rather than the file.

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
| **Add files**: several books and games in one press, each routed to its own existing path (a book to the importer's staging folder, a game through the arcade's publish gate), with a per-file answer and an audit line | **done + tested** (`test_admin_upload.py`, 29 tests); never picked in a browser |
| **Addresses**: every door on the box, generated from one list, with the reader's own host name substituted and held to the application by a test | **done + tested** (`test_admin_upload.py`); never opened in a browser |
| "What is working" health page, readiness reasons, storage breakdown | **done + tested** |
| **Home**: four measured figures (time on, room left, files a reader can open, notes waiting) with *not measured yet* where the box could not look, and the two ways to put something on the box | **done + tested** (`test_admin_dashboard.py`, 19 tests); never opened in a browser |
| **Sign out**: a `POST` behind the form token that ends the session everywhere and lands on the sign-in form even on a second press | **done + tested** (`test_admin_dashboard.py`); never pressed in a browser |
| **Navigation**: one eleven-page bar, the page being read marked, a deeper route belonging to its section, and the same list behind the address map (both directions held by tests) | **done + tested** (`test_admin_dashboard.py`, `test_admin_upload.py`); never drawn in a browser |
| Player names: the on/off switch | **done** |
| Per-player management (reset, delete, disable, quota) | **absent** |
| Service controls (restart, stop, pause/resume) | **absent** — no buttons, on purpose |
| Backup/restore buttons | **absent** — the CLI and recovery sheet only |
| Network page: add a Wi-Fi network, ask for **one** restart on it, take the request back | **done + tested** (`test_network_page.py`, 37 test functions / 41 collected); never opened in a browser. A `--restart` that keeps the request but cannot schedule the reboot says *The request is kept.* (never *Nothing was asked.* above a page offering to take it back), and a refusal made before anything ran still says nothing was asked |
| Network mode controls beyond `ark-mode` on the console | **partial** — the page writes a saved profile and a one-boot request; it cannot switch the radio now, pin a mode, or unpin one (`ark-mode` is still the only thing that acts) |
| Diagnostics: thermal, undervoltage, last restore, failed jobs | **absent** (readiness and storage only) |

The owner can run the shelves, the accounts and the switches. They cannot run the box: no
service restart, no copy, no player reset, and no way to pin or unpin a network from the portal.
**One deliberate exception** (added with the Network page): the box can be asked for a **single**
restart on a Wi-Fi network the owner has just saved, and that request can be taken back before it
happens — because carrying the Ark to another house and getting it onto that house's Wi-Fi is the
whole reason the page exists, and the box has no keyboard. It is one boot's worth, it lapses, it
leaves no pin, and if the network is not there the box broadcasts instead of disappearing.

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
| Network-loss failover every minute, pins respected, never drops a reader | **done + tested against fakes**; never acted on real hardware. `lan` — the word the Network page's one-time restart leaves behind — now gets the same one-check grace `home` gets, so a box just put on a house's Wi-Fi is not one blinked check away from broadcasting (`test_network.py`, 16-case decision table) |
| A box can be moved to another house and put on its Wi-Fi with no keyboard: save the network, ask for one restart, take it back | **done + tested on the Mac** (`test_network_page.py`); the page has never been opened in a browser and no request has ever been made on the card (`docs/HUMAN-TEST-LIST.md` §17) |
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
| R8 | DONE (code + test) | one budget, before receipt, across processes; real 2 GiB press and a real full card are human work |
| R9 | DONE (code + test + proved on the box) | all; search quality unmeasured |
| R10 | DONE (code + test) | all on the Mac; **no model on the box, nothing in a browser, not deployed** |
| R11 | DONE (code + test) | the pass and its settings yes; **never met a real power cut, phase not run on the box** |
| R12 | DONE (code + test) | batch upload + every door named; real 2 GiB press is human work |
| R13 | DONE (code + test) | shelf, ranges, worker; real phone playback is human work |
| R14 | DONE (code + real-browser test + live Pi) | live Pi and desktop Chromium offline journey yes; **no real phone, no owner region, eviction unproven** |
| R15 | DONE (code + test + live Pi) | shelves, films-not-searched, digests yes; real phone playback and a person's judgement of the shelves are human work |
| R16 | DONE (code + test + live check) | capture/pin/retain/validate/GC yes; scratch-root restore on the live card; a person's rehearsal remains FOR YOU |
| A | PARTIAL | accounts/community/games/health yes; players/services/backup UI no |
| B | PARTIAL (early) | guest play + named saves + preferences yes; history/metadata/visuals no |
| C | PARTIAL | stage 3–6 fixes yes; thermal/jobs/search cancellation no |
| D | PARTIAL (code + test) | review, cards, pack and household page yes; translation and one-page print no; light colours and the reading order added 4 Nov |
| R18 | DONE (code + test) | containment, feature states, one pause, durable film publish, Maps app update, reading order, light colours; **no phone, no Pi deploy yet** |

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
3. ~~**R8 one budget**~~ **done** — one ledger (`ark_space`), one admission door
   (`ark_admission`), every writer promises before it writes, and the peak a request
   costs is reserved before its body is read. What remains is human: a real 2 GiB
   press on a phone and a genuinely full card (`docs/HUMAN-TEST-LIST.md`).
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
| A repair takes the repair turnstile and then the exclusive gate; a reader that keeps its handle makes it **defer**, not call the file corrupt | `ark_maintenance.repairing()` / `.maintain()`, `heal_sqlite()` | `test_maintenance.py::test_an_open_reader_blocks_a_repair_and_a_kill_releases_it`; `::test_a_periodic_pass_defers_when_a_reader_holds_the_records` |
| A replacement takes the same turnstile and gate before reading the original; a real second process holding the gate makes it answer `busy` with every byte unchanged | `ark_db_replace.replace()` | `test_db_replace.py::test_replace_refuses_while_another_process_holds_the_gate` |
| Two repairs take turns; their intervals do not overlap, and a killed owner's lock is released by the kernel | `repairing()`, `flock` | `test_maintenance.py::test_two_repairs_take_turns_across_processes`; `::test_a_killed_owner_releases_the_gate_and_the_file_stays` |
| The book engine, which cannot take the gate, is stopped for the repair of either of its databases and started again only if it was running; a stop that fails aborts the repair | `OWNER_UNITS`, `_stop_owner()`, `_start_owner()` | `test_self_heal.py::test_a_repair_stops_and_restarts_the_book_engine`; `::test_a_book_engine_that_was_not_running_is_not_started`; `::test_a_book_engine_that_will_not_stop_stops_the_repair` |
| Maintenance holds new connections at the door for a bounded time, then a page says the box is busy (503), not 500 | `admit()`, `main.py` exception handler | `test_maintenance.py::test_maintenance_stops_new_access_at_the_door`; `::test_a_page_during_maintenance_is_a_503_with_a_sentence` |
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

## R12 — the owner can add a pile of files at once, and every door is written down

**Status: DONE + TESTED inside the box; untouched by a human hand.**

The two ways of getting content onto the card were one file at a time, and the list of
addresses lived in four documents that had each been wrong at least once. `/admin/upload`
adds many files in one press without inventing a second ingest path, and `/admin/doors`
prints the list from the code.

| Acceptance sentence | What does it | The test that proves it |
|---|---|---|
| One press carries several files and routes each by its kind — a book to the Book Library, a game to the games page | `ark_admin.upload_files`, `ark_ingest.classify`/`kinds` | `test_admin_upload.py::test_one_press_carries_both_kinds_and_keeps_them_apart` |
| A book is written into the folder the book importer already watches, under a hidden `.upload-…part` name first and linked to a readable name only when it is whole | `ark_ingest.spool_book` | `test_admin_upload.py::test_a_book_lands_in_the_importers_own_folder` |
| A game goes through the arcade's own `spool_upload` → `import_owner_file` gate and is on the shelf with its fingerprint | `ark_arcade_install` (unchanged) | `test_admin_upload.py::test_a_game_lands_on_the_shelf_through_the_arcade_gate`; `::test_the_same_cartridge_offered_twice_is_named_not_added_again` |
| A kind the box does not read is refused **by name** and nothing is written | `ark_ingest.classify` returns `""` | `::test_a_kind_the_box_does_not_read_is_refused_by_name` |
| An empty file, a file that is not really a game, more files than one press carries, and a press over its ceiling are each refused and leave no partial file | `spool_book`, `spool_upload`, `MAX_FILES`, `MAX_BATCH` | `::test_an_empty_file_is_refused_and_leaves_nothing_behind`; `::test_a_file_that_is_not_really_a_game_is_refused_and_the_shelf_is_unchanged`; `::test_more_files_than_one_press_carries_is_refused_before_anything_is_written`; `::test_a_press_larger_than_one_upload_may_be_stops_and_leaves_no_partial_file` |
| A name typed into the form cannot reach a folder outside the staging area | `_picked_name`, `ark_storage.display_name`, `os.link` into one folder | `::test_a_name_in_the_form_cannot_reach_a_folder_outside_the_staging_area` |
| A book the library already holds is not added twice — decided by the importer's own record, not by the folder | `ark_ingest.imported`/`read_ledger` | `::test_a_book_the_library_already_holds_is_not_added_twice` |
| "In the library" is said only when the importer has recorded it; otherwise the book stays where it is and the page says so | `ark_ingest.wait_for_library` → `calibre-incoming/.imported.json` | `::test_the_page_says_in_the_library_only_when_the_importer_has_recorded_it`; `::test_a_book_the_importer_has_not_answered_for_is_left_where_it_is` |
| A cut in the middle of a book leaves one hidden temp file, which the repair pass takes away — never a half book | `ark_self_heal.SWEEP_RULES` (`calibre-incoming`, `.*.part`) | `::test_a_half_written_book_is_swept_and_never_counted_as_a_book` |
| The door is behind the sign-in **and** the form token, and writes nothing without them | `_gate_tls`, `_committed` | `::test_the_page_is_behind_the_sign_in`; `::test_the_upload_door_needs_a_sign_in_and_writes_nothing`; `::test_the_upload_door_needs_the_form_token` |
| Reading several files never stalls the event loop (the route is not `async def`) | `ark_admin.upload_files` | `::test_the_upload_route_is_never_async` |
| The book kinds are the importer's own list, the game kinds are the arcade's own consoles, and the caps are the box's own numbers | `ark_ingest.BOOK_EXT`/`GAME_EXT`/`MAX_GAME`/`MIN_FREE_RESERVE` | `::test_every_kind_the_book_importer_reads_is_a_kind_this_page_reads`; `::test_every_console_the_shelf_can_play_is_a_kind_this_page_carries`; `::test_the_caps_are_the_ones_the_rest_of_the_box_already_uses` |
| The proxy allows exactly the batch the application accepts, streams it, and parks it on the card | `pi/nginx/ark-admin.conf` `= /admin/upload` (520 m) | `::test_the_batch_door_allows_the_batch_the_page_accepts_and_no_more` |
| Every administrator page the application serves is named in the address list, and the list prints the host the reader used | `ark_doors.DOORS`, `ark_admin.doors_page` | `::test_every_administrator_page_is_named_in_the_addresses`; `::test_the_address_list_really_describes_every_door`; `::test_the_addresses_page_names_the_doors_in_plain_words`; `::test_the_addresses_page_is_behind_the_sign_in` |
| The boxes' own proof: nginx serves both pages and neither answers without a sign-in | `65-admin` / `90-verify` | the two `tls_code ark.lan:8443 /admin/upload|/admin/doors` cases (proved on the card at deploy, log §34) |

**What is not proved by any test, and cannot be:** a person picking files in a browser.
The behaviour of a phone's file picker, a slow upload over the hotspot, and the moment
`ark-book-import` fires while the request is still open are all one human's thumb away —
`docs/HUMAN-TEST-LIST.md` §18.

---

## R13 — a film is promised only what its own bytes can do

**Status: DONE + tested on the Mac; the browser and phone walk-through is human work
(`docs/HUMAN-TEST-LIST.md`).** The shelf reads every film with `ffprobe`, keeps four
verdicts apart (direct / remux / convert / unsupported), serves bytes in ranges, and
never publishes a visitor's film without the owner.

| Acceptance sentence | Where | Test |
|---|---|---|
| An extension is a routing hint, never a promise | `ark_media.probe()`, `plan_for()` | `test_media.py::test_the_extension_only_decides_that_a_file_should_be_looked_at` |
| A renamed archive is refused with a sentence and leaves no file | `ark_media.accept()` | `test_media.py::test_a_broken_file_is_refused_and_leaves_nothing_behind`; `::test_a_renamed_archive_is_not_a_film` |
| A film from the File Box waits for the owner and is not downloadable meanwhile | `main.files_post` → `ark_media.accept(state=pending)`, carrier row `hidden=1` | `test_media.py::test_a_film_shared_in_the_file_box_waits_for_the_owner` |
| An unpublished film answers 404 on every public address | `/media/<id>` and its stream, poster, download, captions | `test_media.py::test_an_unpublished_film_is_not_reachable_by_guessing_its_number` |
| The owner publishes, hides and deletes behind session + token | `/admin/media/{id}/state`, `/delete` | `test_media.py::test_the_owner_page_lists_films_and_can_publish_one`; `::test_the_owner_page_refuses_a_form_without_the_token`; `::test_a_deleted_film_from_the_owner_page_says_what_went` |
| Seeking works: 200, 206, suffix ranges, 416, `HEAD`, `Accept-Ranges` | `ark_media_web.ranged()` | `test_media.py::test_a_published_film_plays_as_it_is_and_in_pieces` |
| A repack serves a real MP4 and releases the upload once it is published | `ark_media_jobs.make_derivative()`, `ark_media.release_original()` | `test_media.py::test_a_film_that_needs_repacking_is_prepared_once_and_the_original_is_released` |
| A codec a browser cannot read is converted to H.264/AAC, bounded by `-fs` | `build_command()`, `output_budget()` | `test_media.py::test_a_codec_a_browser_cannot_read_is_converted` |
| The picture is chosen once, from inside the film, and never blocks a playable film | `ark_media_jobs.make_thumbnail()` | `test_media.py::test_a_film_that_needs_repacking_is_prepared_once_and_the_original_is_released` |
| A film with no picture still has a neutral card | `ark_media_web.fallback_poster()` | `test_media.py::test_the_poster_falls_back_to_a_neutral_card_when_there_is_none` |
| Captions become WebVTT and belong to one film | `ark_media.srt_to_vtt()`, `/media/{id}/captions/{lang}` | `test_media.py::test_srt_becomes_webvtt_without_losing_a_line`; `::test_captions_travel_with_their_film_and_nobody_elses` |
| A job interrupted by a cut is queued again; three honest failures stop it | `ark_media.recover()` | `test_media.py::test_an_interrupted_job_is_put_back_in_the_queue`; `::test_a_job_that_stopped_three_times_is_not_retried_for_ever` |
| Stopping a job keeps the original and kills nothing else | `ark_media.cancel()` | `test_media.py::test_stopping_a_job_is_recorded_and_the_original_is_untouched` |
| A preparation that failed is shown as failed, on the shelf and on its own page — never as *Getting ready* | `ark_media.status_of(job_state=)`, `/media/{id}` | `test_media.py::test_a_preparation_that_failed_is_not_advertised_as_getting_ready`; `::test_a_film_nobody_is_working_on_does_not_say_getting_ready` |
| A poster job cannot mask or mis-attribute a failed preparation | `ark_media.last_job(kind="prepare")`, `listing()` subqueries | `test_media.py::test_a_picture_taken_later_does_not_erase_a_failed_preparation` |
| A film that plays is never labelled a failure by a later failed attempt | `ark_media.status_of()` ordering | `test_media.py::test_a_film_that_plays_is_ready_even_if_a_later_attempt_failed` |
| The conversion clock is measured under the worker's own limits, and a long film still fits inside it | `ark_media_jobs.CONVERT_FACTOR`, `MAX_TIMEOUT` | `test_media.py::test_a_long_film_gets_the_time_the_box_really_needs`; `::test_the_deadline_is_still_a_ceiling_not_a_promise` |
| A playing copy is never larger than 720p, in either orientation, and nothing is upscaled | `ark_media_jobs.PLAY_HEIGHT`, `build_command()` | `test_media.py::test_a_film_above_720p_is_prepared_at_720p_and_a_smaller_one_is_left_alone` |
| The hardware decoder is used only where the device exists, and a refused file falls back to the CPU | `ark_media_jobs.hwaccel_args()`, `make_derivative()` | `test_media.py::test_the_hardware_decoder_is_asked_for_only_when_the_device_is_there`; `::test_a_hardware_decoder_that_refuses_a_file_is_tried_again_on_the_cpu`; `::test_a_conversion_that_failed_halfway_is_not_started_over` |
| The upload is released only when a playing copy is ready, and never for a direct film | `ark_media.release_original()` | `test_media.py::test_an_original_is_not_released_before_a_playing_copy_exists`; `::test_a_film_that_plays_as_it_is_keeps_its_only_copy` |
| A File Box share keeps its bytes: the shelf adds a copy and changes nobody's download | `ark_media.release_original()` (`hidden = 0` refusal) | `test_media.py::test_the_file_box_share_keeps_its_original` |
| The startup/`--trim` sweep releases only ready films, and repeating it changes nothing | `ark_media.release_ready_originals()`, `ark-media-worker --trim` | `test_media.py::test_the_sweep_releases_only_films_that_already_have_their_copy` |
| A released film still plays, and its owner page offers nothing that needs the upload | `ark_media.playable()`, `has_bytes()`, admin guards | `test_media.py::test_preparing_again_a_film_whose_upload_was_released_says_it_is_ready`; `::test_the_owner_page_says_when_the_original_was_removed` |
| Deleting removes picture, copy and bytes, and is idempotent | `ark_media.delete()` | `test_media.py::test_deleting_a_film_removes_its_picture_its_copy_and_its_bytes` |
| A File Box film can be adopted without a second copy | `ark_media.adopt()` | `test_media.py::test_an_owner_can_put_a_film_from_the_file_box_on_the_shelf_without_copying_it` |
| The catalogue searches, sorts and pages; `%` and `_` are characters | `ark_media.listing()` | `test_media.py::test_search_sort_and_pages_never_show_more_than_one_screen`; `::test_a_search_wildcard_is_a_character_not_a_pattern` |
| The proxy does not break seeking; the worker is a background neighbour | `pi/nginx/ark.conf`, `ark-media-worker.service` | `test_config.py::test_the_film_door_passes_ranges_through_and_never_buffers_a_player`; `::test_the_media_worker_is_a_background_neighbour_not_a_second_portal` |

**Not proved here:** how a real phone's browser plays each codec, how a Pi 4 feels while
it converts a feature film, and whether a person finds the shelf pleasant. Those are
`docs/HUMAN-TEST-LIST.md` entries, not code claims.

---

## R14 — maps, search and NEW routes with no Ark and no internet

**Status: DONE + tested on the live Pi and in desktop Chromium; a real iPhone/Android
device and the owner's chosen region are human work (`docs/HUMAN-TEST-LIST.md` §22).** The
application, the graph, the search index and the drawing layers live on the device
after one deliberate download. Reopening is served by the service worker; the router
is a local A* over the pack's graph; the search is a local scan; no request is needed
or made for any of the defining journey.

| Acceptance sentence | Where | Test |
|---|---|---|
| A pack is a finished, self-describing folder; a missing source, date, version or fingerprint is refused before a byte is written | `ark_maps.write_pack()`, `manifest_problems()` | `test_maps.py::test_a_pack_must_say_where_it_came_from`; `::test_pack_round_trip` |
| A one-way street is one-way for a car and still walkable; a car may not use a path | graph v2 `modes`, `direction_modes()`, `access_modes()` | `test_maps.py::test_oneway_is_one_way_for_a_car_and_not_for_feet` |
| Turn restrictions with a via node are carried and checked at the junction | `Graph.add_turn()`, `Router._turnAllowed()` | `test_maps.py::test_turn_restriction_is_in_the_description`; `tests/js/router.test.mjs` |
| A changed byte is caught before publication | `ark_maps.verify_pack()` | `test_maps.py::test_a_changed_byte_is_caught` |
| An archive cannot escape its folder, and an unchecked folder is never offered | `Store._zip_entries()`, `Store.publish()` | `test_maps.py::test_a_zip_cannot_escape_the_incoming_folder`; `::test_an_unchecked_folder_is_not_offered` |
| A pack is immutable; a new version publishes beside the old one; deleting on the Ark does not pretend to reach a phone | `Store._publish_folder()`, `Store.delete()` | `test_maps.py::test_import_publish_and_delete`; admin page copy |
| Download resumes with real ranges: 206, suffix ranges, 416, `HEAD`, ETag | `ark_map_web._parse_range()`, `pack_file()` | `test_maps.py::test_catalogue_and_range_downloads` |
| Admin management is behind TLS, session, CSRF and password on delete; public read is open | `ark_admin.maps_*`, `ark_map_web` | `test_maps.py::test_the_admin_maps_page_is_behind_the_sign_in` |
| The app is served with a worker scope and a CSP that cannot reach `/admin` | `ark_map_web._serve_asset()` | `test_maps.py::test_the_app_is_served_with_a_worker_scope` |
| **The defining journey:** download → make the Ark and internet unreachable → close/reopen → pan → search a new place → calculate a NEW route → directions → route sheet → GPX, with no successful network response | `pi/maps/web/*`, IndexedDB, service worker | `test_maps_browser.py::test_a_downloaded_region_reopens_and_routes_with_no_network` |
| Pause keeps the received pieces; continue finishes; cancel leaves nothing | `app.js` download lifecycle, `store.deletePack()` | `test_maps_browser.py::test_a_paused_download_keeps_its_pieces_and_resumes` |
| A certificate error does **not** enable a service worker; a trusted certificate does | browser behaviour, `help.html` | proved by the probe recorded in `docs/IMPLEMENTATION-LOG.md` §44; the browser test pins the certificate exactly as a device would |
| Space is the one shared policy; an import reserves before extracting and releases however it ends | `ark_map_store.import_zip()`, `ark_space.hold()` | `test_maps.py::test_import_publish_and_delete`; `test_space.py` |

**Not proved here:** a real iPhone or Android phone (Add to Home Screen, certificate
installation, memory and battery); the owner's actual region choice; browser eviction
under real quota pressure (refusal at a tiny quota is tested, eviction is not); a pack
larger than the demonstration; and voice/continuous navigation, which is not
implemented and is not promised.  The live Pi run used the real certificate pinned by
SPKI over its Wi-Fi address and is recorded in `docs/IMPLEMENTATION-LOG.md` §44.

---

## R15 — the library a person can actually navigate, and a download that is the file it claims

**Status: DONE + tested on the Mac, deployed and measured on the live Pi on 3 November
2026; the phone walk-through is human work (`docs/HUMAN-TEST-LIST.md` §23).** The card
holds 27 collections in two kinds — pages and films — and neither the shelf page nor the
search box is allowed to be vague about which is which.

| Acceptance sentence | Where | Test |
|---|---|---|
| Every collection on the card appears on exactly one shelf, and none is lost | `ark_library.shelves()` | `test_library.py::test_every_book_lands_on_exactly_one_shelf` |
| The filing comes from the catalogue, not from a second list in code | `catalog.tsv` column 7 → `ark_library.read_catalogue()` | `test_library.py::test_every_catalogue_row_is_complete_and_filed_on_a_real_shelf`; `::test_the_requested_collections_are_in_the_catalogue_on_the_right_shelves` |
| A bundle with no catalogue row is shown, not hidden | `ark_library.OTHER` | `test_library.py::test_every_book_lands_on_exactly_one_shelf`; `::test_a_missing_catalogue_is_an_empty_map_not_a_crash` |
| An empty box gets a friendly page, not an error | `main.library` | `test_library.py::test_an_empty_library_is_a_friendly_page_not_an_error` |
| A film shelf is offered as something to watch | `main.FILM_SHELF_NOTE` | `test_library.py::test_the_shelf_page_shows_every_collection_and_links_to_it` |
| The file's own flags never decide what a reader is told: `_ftindex:no` is carried by iFixit, which **is** searchable (22,722 hits for "screen" in 0.22 s on the live box, 3 Nov 2026) | `ark_library.FILM_TOPIC` (the shelf decides) | `test_library.py::test_a_picture_guide_is_not_sold_as_a_film_shelf` |
| The page uses plain words only | `ark_library.TOPICS` | `test_library.py::test_no_shelf_label_uses_a_word_the_reader_does_not_know`; `::test_the_shelf_page_uses_the_plain_words_and_escapes_a_title` |
| The proxy really carries `/library` (an unnamed route is the front page with a 200) | `pi/nginx/ark.conf` on-the-fly location | `test_library.py::test_every_page_the_backend_builds_is_named_by_the_proxy`; `::test_the_library_page_is_not_reachable_without_the_proxy_naming_it` |
| A new backend module that the installer never copies is a failing test, not an inactive service | `60-portal.sh` file list | `test_library.py::test_every_backend_module_the_portal_imports_is_installed` |
| The published digest is checked before a download gets its name | `ark-zim-checksum`, `ark-zim-download` `fetch_one()` | `test_library.py::test_the_downloader_checks_the_digest_before_it_publishes`; `::test_the_metalink_digest_is_found_and_the_file_matches_it` |
| A different file of the right length is refused; an unpublished digest is *unknown*, not *wrong* | exit codes 1 / 3 | `test_library.py::test_a_file_that_is_not_the_published_one_is_refused`; `::test_no_published_digest_is_unknown_not_wrong` |

**Not proved here:** that a real phone plays these films smoothly, that a person finds the
shelves well named, or that a 2 GB video shelf is pleasant to browse on a small screen.
Those are `docs/HUMAN-TEST-LIST.md` §23, not code claims.

---

## R16 — a recovery copy agrees with the files its records name

**Status: DONE (code + test + live check).** The daily pass used to keep the database and nothing
else; the live box now keeps two complete generations and a scratch-root restore on
the real card returned the exact old save bytes (`docs/IMPLEMENTATION-LOG.md` §53.6). A player's save is a row **and** a content-addressed blob; replacing a save
deleted the old blob, so an older copy restored a row whose bytes were gone.

| Acceptance sentence | Where | Test |
|---|---|---|
| A retained recovery copy names every save, shared file and film file its records expect, with size and digest where affordable | `ark_recovery.references()`, `capture()` | `test_recovery.py::test_a_recovery_copy_restores_the_exact_bytes_its_row_named`; `::test_a_small_shared_file_is_pinned_and_not_copied`; `::test_a_film_is_recorded_but_not_pinned_or_copied` |
| Saves (and small shared files) are pinned in place; a replacement or delete cannot take away a blob a retained copy needs | `ark_arcade._forget()`/`_prune_blobs()`, `ark_recovery.protects()` | `::test_replacing_a_save_keeps_its_blob_while_a_copy_names_it`; `::test_deleting_a_save_leaves_its_bytes_while_a_copy_names_them`; `::test_a_save_never_waits_for_a_copy_and_never_loses_its_bytes` |
| Capture and GC take turns on one lock, so a blob cannot vanish between identification and protection | `ark_recovery.capture_lock()`/`deletion_guard()`/`gc()` | `::test_a_capture_in_progress_makes_collection_wait_instead_of_guessing`; `::test_saves_and_copies_at_once_never_leave_a_copy_naming_a_gone_blob` |
| A copy is published only after its database and manifest are durable and checked | `ark_recovery.capture()` stage → fsync → rename | `::test_an_interrupted_copy_is_never_published_and_is_cleared`; `::test_a_copy_that_fails_before_publication_leaves_no_generation` |
| Retention is bounded, and the last verified copy is never spent to fit a new one | `KEEP = 2`, `_prune()`, `_space_allows()` | `::test_a_copy_that_cannot_fit_leaves_the_last_verified_copy_alone`; `::test_retention_removes_an_old_copy_only_after_a_newer_one_is_whole` |
| Validation reads referenced files, not only `quick_check` | `ark_recovery.validate()` | `::test_validation_notices_a_missing_blob_and_a_changed_one` |
| An older database-only `.good.bak` is adopted once and its missing files are named | `ark_recovery.adopt_legacy()`, `ark_self_heal.snapshot()` | `::test_an_old_database_only_copy_is_adopted_and_its_missing_files_named` |
| An explicit backup is coherent too: the DB copy and the files beside it cannot be split by a save replacement | `ark_bundle.capture_lock()`, `ark-backup.take()` | `::test_a_backup_cannot_be_split_by_a_save_landing_during_the_copy` |
| Deleting a pinned shared file retires the copy that needs it; films/maps/collections are never duplicated | `ark_recovery.discard()`, `EXCLUDED` | `::test_deleting_a_pinned_shared_file_retires_the_copy_that_needs_it`; `::test_a_recovery_copy_does_not_copy_the_library` |

**Not proved here:** a restore of a generation against the **live** records (the
live box is only inspected; a real-device rehearsal is `docs/HUMAN-TEST-LIST.md`
§26), a physical power interruption during capture, and a phone. The film, map
and encyclopedia libraries are deliberately excluded from automatic copies; a
film whose bytes are gone is reported by `validate()`, never silently promised.

---

## R17 — the box can move to a bigger card, and nothing writes without proof

**Status: DONE (code + test on the Mac); NO PHYSICAL CARD YET.** The guard, the
clone tool and the read-only report are complete and tested against a stand-in
`diskutil` and a disposable sysfs tree. No 512 GB card has been cloned, expanded
or booted, and `docs/HUMAN-TEST-LIST.md` §28 is the walk-through that would make
this a hardware fact.

| Acceptance sentence | Where | Test |
|---|---|---|
| 128, 256 and 512 GB cards are accepted; 64 GB, 640 GB, 1 TB and 2 TB devices are refused with the supported classes named | `ARK_CARD_BANDS` in `scripts/lib-disk-guard.sh` | `test_disk_guard.py::test_a_supported_card_class_is_accepted`; `::test_an_unsupported_size_is_refused_and_says_what_is_supported`; `::test_the_old_270_gb_ceiling_is_gone_but_a_ceiling_still_exists`; `::test_the_capacity_class_is_a_pure_function` |
| Capacity never decides alone: external, physical, whole-disk, non-internal, ejectable, **removable media** and the forbidden identifiers are each checked independently | `ark_validate_sd_device()` | `::test_an_internal_disk_is_refused_even_when_listed_as_external`; `::test_the_internal_ssd_is_refused_by_name`; `::test_a_synthesized_container_is_refused`; `::test_a_partition_is_not_a_card`; `::test_a_usb_ssd_is_refused_however_big_it_is`; `::test_removable_media_that_is_not_ejectable_is_contradictory`; `::test_a_real_card_is_removable_media_and_ejectable` |
| A refusal is never reported as success — through a function gate or from the standalone script | `ark_guard_or_exit()`, the `__main__` block | `::test_a_refusal_propagates_through_a_gate`; `::test_guard_or_exit_prints_the_device_on_success`; `::test_the_standalone_guard_exits_non_zero_when_it_refuses` |
| A generated privileged helper expands nothing at generation time and is valid bash | the heredocs in `01-flash.sh` and `clone-card.sh` | `::test_the_generated_helper_is_valid_bash_and_expands_nothing_early` |
| Missing, contradictory or malformed metadata fails closed | `ark_validate_sd_device()`, `ark_device_facts()` (sentinel, exit 3) | `::test_a_missing_required_field_fails_closed`; `::test_a_malformed_size_fails_closed`; `::test_a_numeric_but_absurd_size_is_refused_as_an_unsupported_class`; `::test_an_uninterpretable_virtualorphysical_is_refused`; `::test_an_unreadable_plist_fails_closed` |
| Selection is unambiguous: zero candidates and two candidates both abort, and no device is chosen | `ark_assert_sd_card()` | `::test_no_card_at_all_aborts_loudly`; `::test_two_cards_are_ambiguous_and_neither_is_chosen` |
| The device identity is re-checked in the writing process, immediately before the write, and a changed device aborts | `ark_card_identity()`, `ark_revalidate_sd_card()`; `01-flash.sh` helper, `fix-pi-shell.sh` | `::test_revalidation_passes_when_nothing_changed`; `::test_revalidation_refuses_a_card_swapped_for_a_different_size`; `::test_revalidation_catches_a_same_size_swap_by_uuid`; `::test_revalidation_refuses_a_device_that_is_no_longer_external`; `::test_the_flash_helper_revalidates_before_it_hands_the_card_to_the_imager`; `::test_fixing_a_shell_on_a_card_revalidates_before_it_writes` |
| Cloning needs two cards in different classes, refuses to run unattended, refuses a card paired with a backup drive, and never writes to the source | `ark_assert_sd_card_pair()`, `scripts/clone-card.sh` | `::test_the_pair_resolver_picks_the_larger_card_as_destination`; `::test_two_cards_of_the_same_class_are_refused`; `::test_the_pair_resolver_needs_exactly_two`; `::test_the_pair_refuses_a_card_and_a_backup_drive`; `::test_the_clone_tool_refuses_to_run_without_a_person`; `::test_the_clone_tool_revalidates_both_cards_inside_the_helper` |
| The post-migration check separates the physical card, the partition, the filesystem, the free space and the reserve, and says when a clone has not been expanded | `pi/bin/ark-card-report` | `test_card_report.py::test_a_cloned_512_gb_card_that_was_never_expanded_is_named_as_such`; `::test_the_filesystem_figure_is_measured_not_copied_from_the_partition`; `::test_json_carries_every_number_for_a_program` |
| An unmeasured size is never rendered as 0 | `summarise()`, `_size()` | `::test_an_unmeasured_layer_is_not_rendered_as_zero`; `::test_an_unreadable_machine_exits_five_and_says_not_measured` |
| The reserve is the one policy, not a second 7 % | `ark_space.reserve_for()` via `ark-card-report` | `::test_the_report_uses_the_one_reserve_policy`; `::test_the_reserve_is_subtracted_from_free_space_and_never_goes_negative` |
| The check is read-only and never runs the expansion | `pi/bin/ark-card-report` | `::test_the_report_can_never_write_or_resize_anything` |

**Not proved here:** any physical card; a real `diskutil` answer from a USB SD
reader (the fixtures are realistic but synthetic); the clone tool end to end; and
a boot from a cloned 512 GB card. See `docs/HUMAN-TEST-LIST.md` §28.

---

## R18 — living through an outage: contained failures, honest features, one pause

**Status: DONE (code + test on the Mac).** No Pi deploy at the time of writing;
the deploy evidence is in `docs/IMPLEMENTATION-LOG.md` §57.

The reviewed failures, each with the test that now holds it shut:

| Acceptance sentence | Where | Test |
|---|---|---|
| A damaged optional media folder does not stop the shared API starting | `ark_media.ensure_dirs()` / `ark_storage.ensure_dirs()` / `ark_arcade.ensure_dirs()` return their failures instead of raising; `main.init_db()` ignores them | `test_containment.py::test_the_api_starts_with_a_damaged_media_folder`; `::test_the_api_starts_with_a_damaged_shared_folder` |
| A missing video tool and an absent helper cost their own feature and nothing else | `ark_media.tools_present()`, `ark_ai.status()` | `::test_a_missing_video_tool_does_not_stop_the_portal`; `::test_an_unavailable_helper_does_not_stop_the_portal` |
| A malformed offline catalogue is a sentence, not a crash | `ark_health.games_section()` | `::test_a_broken_optional_catalogue_is_a_sentence_not_a_crash` |
| Direct playback survives a broken poster folder | `ark_media.playable()` / `ark_media_web` | `::test_direct_playback_survives_a_broken_poster_folder` |
| A silent child is stopped at its deadline (the reviewed 0.4 s / 0.05 s defect) | `ark_media_jobs._run()` — `select` loop | `test_subprocess_bounds.py::test_a_silent_child_is_stopped_at_its_deadline` |
| A partial line cannot hide the deadline | same | `::test_a_partial_line_does_not_hide_the_deadline` |
| Continuous output cannot outrun the deadline, and progress still arrives | same | `::test_continuous_output_cannot_outrun_the_deadline`; `::test_progress_lines_are_still_streamed` |
| A tool that floods stderr grows nothing without bound and still names the fault | `_Tail`, `STDERR_TAIL_BYTES` | `::test_stderr_growth_is_bounded_and_still_names_the_fault` |
| Cancellation is independent of output, and the child is reaped | `CANCEL_CHECK_SECONDS`, `finally` kill+wait | `::test_a_cancelled_job_stops_a_silent_child` |
| Films, Games and Maps are reported, and none of them can make the core box unready | `ark_health.FEATURE_SECTIONS`, `report()` | `test_pause.py::test_pausing_never_makes_the_core_box_unready`; `test_health.py` (existing sections) |
| Seven states, not one boolean | `ark_health.STATES`, `_section(state=…)` | `test_pause.py::test_a_paused_worker_is_a_paused_shelf_not_a_broken_one`; `test_homepage.py` |
| Film playback is separated from conversion availability | `films_section()` | `test_pause.py::test_a_paused_worker_is_a_paused_shelf_not_a_broken_one` |
| The map shelf is separated from whether a phone kept a copy | `maps_section()` (`device_copy` fact) | `test_containment.py`; `test_pause.py` |
| Game assets are checked for player files, cores and ROMs | `games_section()` | `test_containment.py::test_a_broken_optional_catalogue_is_a_sentence_not_a_crash` |
| Liveness is cheap and never invokes readiness | `ark_health.liveness()`, `/api/health` | `test_health.py::test_health_is_liveness_only_and_says_so`; `test_concurrency.py::test_liveness_stays_cheap_while_other_readers_work` |
| A probe that raises costs one red row, not the page | `_build()` isolation | `test_health.py`; `test_pause.py` |
| The front page never claims "Ready" before anything checked, and says what still works when the API is gone | `pi/portal/index.html` | `test_homepage.py::test_the_static_page_never_claims_to_be_ready_by_itself`; `::test_the_static_page_says_what_still_works_when_the_backend_is_gone` |
| The boot guard's restart decision follows the backend's liveness door, not the static page, and writes the readiness verdict down without acting on it | `pi/bin/ark-boot-guard` | read in review; no automated test (shell). `90-verify` reports features as optional |
| The existing readiness contract is unchanged for existing readers, and says what it now scopes | `report()` — `ok`/`blocking`/`ready`/`total` + `scope`/`unavailable_features` | `test_health.py::test_ready_answers_503_with_a_reason_for_every_part` |
| One pause, in one file, that survives a restart and can be read without Python | `ark_pause` | `test_pause.py::test_the_pause_survives_a_restart`; `::test_the_pause_file_can_be_read_without_python`; `::test_an_unreadable_marker_path_fails_closed` |
| A pause defers work rather than cancelling it, and an encode already running is allowed to finish | `ark_media_jobs.work_once()`, `make_derivative()` | `::test_the_worker_claims_nothing_while_paused`; `::test_a_job_already_running_is_allowed_to_finish` |
| Integrity and recovery keep running while paused | `ark_media_jobs.startup()` | `::test_the_intact_recovery_pass_still_runs_at_startup` |
| Pause and resume are authenticated, CSRF-checked, audited and idempotent | `/admin/background/{pause,resume,stop}` | `::test_the_owner_can_pause_and_resume_through_the_page`; `::test_pausing_needs_the_form_token_and_a_session`; `::test_a_pause_is_recorded_in_the_history`; `::test_pausing_twice_is_idempotent` |
| Stop is bounded to the job the records say is running, and keeps the upload | `background_stop()`, `ark_media.cancel()` | `::test_stopping_the_running_film_names_it_and_keeps_the_upload`; `::test_stop_cannot_name_a_film_that_is_not_running` |
| A dropped book is not silently eaten by the pause | `ark_pause.kick_path()`, `ark-book-import.path` | `::test_resuming_leaves_a_trigger_for_a_book_dropped_while_paused` |
| A paused helper refuses a question and says the rest still works | `ark_ai.NOT_READY["paused"]` | `::test_a_paused_helper_says_so_and_still_leaves_the_box_whole` |
| Every reader page keeps working while paused | `main` | `::test_the_public_pages_keep_working_while_paused` |
| A film's upload leaves only after the copy, its folder and the record are durable | `ark_media_jobs.make_derivative()` | `test_film_durability.py::test_the_publication_order_syncs_before_it_releases` |
| A short or mismatched copy never releases the upload | `ark_media.release_original()` | `::test_an_original_is_kept_when_the_copy_is_short`; `::test_an_original_is_kept_when_the_record_and_the_file_disagree`; `::test_the_sweep_uses_the_same_rule` |
| An installed Maps page takes a newer application, keeps its pack and its saved places, and still routes with nothing reachable | `ark_map_web.app_release()`, `sw.js`, `app.js` | `test_maps_browser.py::test_an_installed_page_takes_a_newer_application_and_keeps_its_pack` |
| No personal attribution in the record; regional-map facts kept | `docs/IMPLEMENTATION-LOG.md` §50.1 | reviewed text |
| Search is labelled by its real coverage, with links to the films and games lists | `main.search()`, `pi/portal/index.html` | `test_search.py::test_the_public_search_page_answers_with_nothing_installed`; `test_homepage.py::test_the_search_button_names_what_it_actually_searches` |
| Where progress is kept is explained, and no cross-device history is implied | `pi/portal/index.html`, `ark_media_web`, `ark_arcade_web` | `test_homepage.py::test_the_page_explains_where_progress_is_kept` |
| A pinned note is the front page's plan; hidden and sorted ones are not; an old one says its age | `main.today()`, `pi/portal/index.html` | `test_homepage.py` (six `today` tests) |
| A light, high-contrast presentation exists and works without JavaScript | `/theme/{light,dark}`, `ark.css`, `pi/portal/index.html` | `::test_the_theme_route_sets_a_cookie_and_returns_the_reader`; `::test_the_theme_route_refuses_to_send_anybody_off_the_box`; `::test_a_page_renders_in_the_chosen_colours` |
| Many readers, seeking, search and a background job: no 5xx, targets met | `main`, `ark_media_jobs` | `test_concurrency.py` (seven tests) |
| A game's page says JavaScript is required and names what does not need it | `ark_arcade_web.game_page()` | read in review |

**Not proved here:** a real phone at all (see `docs/HUMAN-TEST-LIST.md` §29); a
real full card; a physical power cut; the Pi's absolute numbers (the targets are
in §56 of the log and the Pi measurement is there too).

---

## Keeping this page honest

* Change a status **in the same commit** as the behaviour and its test.
* A new test that exercises a helper is not proof the shipped route calls it; say which
  route is proved.
* Never move a row from PARTIAL to DONE because the code "looks right". The missing
  sentence has to be in the table, or it is not missing.
* If a row says "done + tested on the Mac", it is still not done on the box.

