# Which test file proves which behaviour

*The map that used to live in `AGENTS.md` §8.5, kept out of the standing-rules
file so it can grow without pushing the rules past their size limit. Every count
is the number of tests in that file at the last full run; the suite is
**1017 collected, 1015 passed, 2 skipped** with the three browser-map tests
excluded.*

| Behaviour | Test file |
|---|---|
| `tests/test_recovery.py` | recovery generations: exact-byte restore, replace/delete, concurrency, GC, interrupted publish, no space, retention, migration, validation, explicit-backup race (28) |
| unique stored names, no overwrite, empty refusal, cap, reserve, temp sweep, missing file row | `tests/test_storage.py` (16) |
| pin / sorted out / hide / restore, both switches, re-auth delete, CSRF, allowlist | `tests/test_board.py` (16) |
| one import per sha256, renamed re-drop, `done/` skipped, File Box link kept | `tests/test_import.py` (7) |
| search deadline, pool reuse, 5 report states, `shorten()`, Atom namespaces, `_spread()` across all collections | `tests/test_search.py` (44) |
| nginx caps, book door route inventory, player box height, library index beside bundles, offline unit ordering, on-card body temp paths | `tests/test_config.py` (30) |
| schema version, migration/upgrade path, Pi scrypt vector | `tests/test_db.py` (7) |
| per-section readiness, progress, `/api/ready` 503, liveness-only `/api/health`, storage breakdown, backup copies | `tests/test_health.py` (26) |
| 16-case watchdog decision table, fake `nmcli`/`iw`, pins, counters written temp-then-`mv`, `home-network.sh` | `tests/test_network.py` (47) |
| owner's Network page end to end, stdin password, one-boot `lan-request`, kept-request banner, non-root `status` | `tests/test_network_page.py` (43) |
| batch upload door (`/admin/upload`), book `.part` spooling, game gate, ledger polling, `ark_doors` completeness | `tests/test_admin_upload.py` (29) |
| owner overview 4 live figures, 11-page `ADMIN_NAV`, `POST /admin/logout` CSRF + idempotence, public footer | `tests/test_admin_dashboard.py` (19) |
| backup/restore round trip, damaged copy refused, `.partial` swept, `--force`, ownership adoption, plain recovery sheet | `tests/test_backup.py` (28) |
| arcade shelf, guest vs named saves, `:8445` origin header, export/import, owner Games page, upload door, tap-to-start | `tests/test_arcade.py` (34) |
| licence as provenance, language table, per-console repos, seed/discover, owner import dedup by sha256 | `tests/test_arcade_install.py` (36) |
| public `/arcade/add` door: visitor adds but never replaces, typed name decides, switch | `tests/test_arcade_add.py` (10) |
| ROM header stripping, fingerprinting, offline catalogue matching, filename fallback | `tests/test_romdb.py` (15) |
| power-cut repair, clock guard, `key=value` counters, `_repair_lock()`, `ark-fsck-once`, fstab/cmdline guards | `tests/test_self_heal.py` (83) |
| stage/validate, preserve main+WAL/SHM, journal move, rename, verify, kill boundaries, ENOSPC/read-only, retention | `tests/test_db_replace.py` (29) |
| the reserve policy, `f_bavail`, reservations across processes, `hold()` release, liveness by pid/boot, the `ark-space` shell door | `tests/test_space.py` (13) |
| the SD-card guard: 128/256/512 capacity classes, every independent device fact, fail-closed metadata, ambiguous selection, identity + pre-write revalidation, the two-card cloning pair, and the call sites' ordering | `tests/test_disk_guard.py` (71) |
| the read-only card report: physical/partition/filesystem/free/reserve kept apart, the unexpanded-clone verdict, the None rule, and no write path at all | `tests/test_card_report.py` (15) |
| request-body admission: doors, chunked bodies, the 2× peak, adoption, disconnects, six real processes, an unreadable ledger | `tests/test_admission.py` (28) |
| probe/plan (direct/remux/convert), a renamed archive refused, public film waits for the owner, 404 for unpublished, 206/416/HEAD ranges, repack releases the upload, conversion to H.264, poster + fallback, SRT→VTT, recover/cancel/delete/adopt, rename, picture-at, catalogue search/sort/paging | `tests/test_media.py` (55) |
| `synchronous=FULL` on every connection, content-addressed save blobs, atomic save replacement | `tests/test_durability.py` (15) |
| Ask the Ark: brief, prompt budget, route hints, lease slot + disconnect cancel, `public_ask` switch | `tests/test_ai.py` (45) |
| Map pack format v2 (modes, one-way, turn restrictions, buildings), safe zip import, immutable versions, publish/hide/delete, catalogue, 206/416/HEAD ranges, admin gate, app assets | `tests/test_maps.py` (20) |
| **Defining offline journey** in real Chromium: download, disconnect, reload, pan, new search, NEW route, directions, route sheet, GPX, no network; pause/resume/cancel | `tests/test_maps_browser.py` (2) |
| Library shelves from the catalogue's topic column; every book on one shelf; a film shelf says it is films, not that it is unsearchable; `/library` named by the proxy; every imported module copied; published sha-256 checked before publish | `tests/test_library.py` (29) |
| Stage 7 household answers, 8 emergency topics, inline SVG QR cards, essentials pack builder | `tests/test_household.py` (13), `test_household_page.py` (34), `test_qr.py` (11), `test_essentials.py` (24) |
| claim door, forced password change, throttling, sessions, TLS probe verdicts, secrets guard | `tests/test_auth.py` (19), `test_accounts.py` (14), `test_tls_probe.py` (16), `test_secrets.py` (12), `test_public.py` |

---

To run one file:

```bash
.venv/bin/python -m pytest tests/test_admission.py -o addopts='--strict-markers' -q
```
