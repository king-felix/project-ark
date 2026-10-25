#!/usr/bin/env bash
# =============================================================================
#  FINAL CHECK — prove the box is turnkey, then leave a receipt.
#
#  Nothing here changes state: it only looks.  It writes the result to
#  /srv/ark/ark_status.json so the Mac can read it back over SSH without
#  guessing, and exits non-zero if something a visitor needs is not working.
# =============================================================================
set -Eeuo pipefail
source "${ARK_SRC:-/srv/ark/src}/lib/common.sh"
ark_require_root
# shellcheck disable=SC1090
[[ -r "$ARK_ETC/ark.env" ]] && . "$ARK_ETC/ark.env"

ark_step "FINAL CHECK — every door, every service"

FAIL=0
problems=()

say_ok()   { printf '   \033[1;32m✓\033[0m %-40s %s\n' "$1" "$2"; }
say_bad()  { printf '   \033[1;31m✗\033[0m %-40s %s\n' "$1" "$2"; FAIL=1; problems+=("$1 ($2)"); }
say_soft() { printf '   \033[1;33m·\033[0m %-40s %s\n' "$1" "$2"; }

# ---- services ---------------------------------------------------------------
# ark-kiwix only runs once at least one collection has arrived, so it is checked
# softly: an empty library is a normal state on a metered connection, not a bug.
for u in nginx.service ark-api.service ark-zim-downloader.service ark-boot-guard.service \
         ark-net-watch.timer ark-book-import.path ark-arcade-discover.timer ssh.service \
         NetworkManager.service avahi-daemon.service; do
  en="$(systemctl is-enabled "$u" 2>/dev/null || true)"
  ac="$(systemctl is-active  "$u" 2>/dev/null || true)"
  if [[ "$en" == "enabled" && ( "$ac" == "active" || "$ac" == "waiting" ) ]]; then
    say_ok "$u" "enabled, active"
  elif [[ "$u" == "ark-boot-guard.service" && "$en" == "enabled" && "$ac" == "activating" ]]; then
    say_ok "$u" "enabled, running now"
  else
    say_bad "$u" "enabled=$en active=$ac"
  fi
done

en="$(systemctl is-enabled ark-kiwix.service 2>/dev/null || true)"
ac="$(systemctl is-active  ark-kiwix.service 2>/dev/null || true)"
if [[ "$en" != "enabled" ]]; then
  say_bad "ark-kiwix.service" "not enabled (enabled=$en)"
elif [[ "$ac" == "active" ]]; then
  say_ok "ark-kiwix.service" "enabled, active"
elif compgen -G "$ARK_ROOT/kiwix/*.zim" >/dev/null; then
  say_bad "ark-kiwix.service" "bundles on disk but service is $ac"
else
  say_soft "ark-kiwix.service" "enabled, waiting for the first collection"
fi

# ---- pages a person actually taps -------------------------------------------
check_page() { # <path> <what> [codes]
  local path=$1 what=$2 want=${3:-"200 302"} code
  code="$(ark_http_code "http://127.0.0.1$path")"
  if [[ " $want " == *" $code "* ]]; then
    say_ok "$what" "HTTP $code"
  else
    say_bad "$what" "HTTP $code (expected $want)"
  fi
}
check_page "/"                    "home page"
check_page "/help.html"           "how-to-use page"
check_page "/assets/ark.css"      "stylesheet"
check_page "/search?q=water"      "one search box"
check_page "/board"               "message board"
check_page "/files"               "file box"
check_page "/arcade/"             "games shelf"
check_page "/arcade/add"          "add a game (no sign-in)"
check_page "/arcade/static/ark-arcade.css" "game shelf stylesheet"
check_page "/arcade/emulator/loader.js"    "game player"
check_page "/api/status"          "live status"
check_page "/vault/"              "manuals index"
check_page "/vault/quick-card.html" "printable cards"
check_page "/generate_204"        "captive-portal trigger"
check_page "/kiwix/"              "offline library" "200 302 503"
check_page "/books/"              "book shelf"      "200 302"

# ---- what is ready, and the reason behind each answer -------------------------
# /api/ready answers 200 only when every part a reader needs actually works, and
# 503 with the reasons when one does not.  An empty shelf is reported softly: a
# box that is still filling up is not broken.  Anything that has content but
# cannot serve it is a failure, and that is the whole point of this block.
ready_json="$(curl -sS --max-time 25 http://127.0.0.1/api/ready 2>/dev/null || true)"
if [[ -z "$ready_json" ]]; then
  say_bad "readiness answer" "the portal did not answer /api/ready"
else
  ready_rows="$(printf '%s' "$ready_json" | python3 -c '
import json, sys
report = json.load(sys.stdin)
for s in report.get("sections", []):
    if s.get("ready"):
        flag = "ready"
    elif s.get("content"):
        flag = "broken"
    else:
        flag = "empty"
    reason = " ".join(str(s.get("reason") or "").split())
    label = " ".join(str(s.get("label") or s.get("key") or "").split())
    print("\t".join([flag, label, reason]))
' 2>/dev/null || true)"
  ready_rows="${ready_rows:-}"
  if [[ -z "$ready_rows" ]]; then
    say_bad "readiness answer" "unreadable JSON from /api/ready"
  else
    while IFS=$'\t' read -r flag label reason; do
      [[ -n "$label" ]] || continue
      case "$flag" in
        ready)  say_ok   "$label" "$reason" ;;
        empty)  say_soft "$label" "$reason" ;;
        *)      say_bad  "$label" "$reason" ;;
      esac
    done <<<"$ready_rows"
  fi
fi

# ---- the owner's door --------------------------------------------------------
check_page "/admin"               "admin stays off the public portal" "404"

adm_code="$(ark_tls_code ark.lan:8443 /admin)"
case "$adm_code" in
  200|303|403) say_ok "management listener :8443" "HTTP $adm_code" ;;
  *)           say_bad "management listener :8443" "HTTP ${adm_code:-none}" ;;
esac

loop_code="$(ark_tls_code localhost:8444 /admin)"
case "$loop_code" in
  200|303|403) say_ok "loopback setup listener :8444" "HTTP $loop_code" ;;
  *)           say_bad "loopback setup listener :8444" "HTTP ${loop_code:-none}" ;;
esac

community_code="$(ark_tls_code ark.lan:8443 /admin/community)"
case "$community_code" in
  200|303|403) say_ok "Community page behind the sign-in" "HTTP $community_code" ;;
  *)           say_bad "Community page behind the sign-in" "HTTP ${community_code:-none}" ;;
esac

games_admin_code="$(ark_tls_code ark.lan:8443 /admin/arcade)"
case "$games_admin_code" in
  200|303|403) say_ok "Games page behind the sign-in" "HTTP $games_admin_code" ;;
  *)           say_bad "Games page behind the sign-in" "HTTP ${games_admin_code:-none}" ;;
esac

if [[ -d "$ARK_ROOT/drop/files" && -d "$ARK_ROOT/drop/tmp" ]]; then
  say_ok "File Box folders" "drop/files + drop/tmp, $(find "$ARK_ROOT/drop/files" -maxdepth 1 -type f | wc -l) shared file(s)"
else
  say_bad "File Box folders" "missing drop/files or drop/tmp — re-run phase 20"
fi

if [[ -x /usr/local/sbin/ark-admin-recover ]]; then
  say_ok "recovery command" "installed, mode $(stat -c '%a' /usr/local/sbin/ark-admin-recover)"
else
  say_bad "recovery command" "missing /usr/local/sbin/ark-admin-recover"
fi

# Who can get in — reported honestly, without ever printing a password.
adm_row="$(sqlite3 -readonly "$ARK_ROOT/api/data/ark.db" \
           'SELECT COUNT(*) || "/" || COALESCE(SUM(must_change),0) FROM admins;' 2>/dev/null || true)"
if [[ -z "$adm_row" ]]; then
  say_soft "administrators" "not readable yet (has the admin phase run?)"
elif [[ "${adm_row%%/*}" == "0" ]]; then
  say_bad "administrators" "none — recovery required: sudo ark-admin-recover --username admin"
elif [[ "${adm_row##*/}" != "0" ]]; then
  say_soft "administrators" "${adm_row%%/*} account(s), ${adm_row##*/} still using a temporary password"
else
  say_ok "administrators" "${adm_row%%/*} account(s), all with their own password"
fi

# ---- the address a phone will use -------------------------------------------
mode="$(cat "$ARK_ETC/mode" 2>/dev/null || echo unknown)"
printf '   %s network mode: %s\n' "→" "$mode"
if [[ "$mode" == "hotspot" ]]; then
  if ip -4 -o addr show "${WIFI_DEV:-wlan0}" 2>/dev/null | grep -q "${HOTSPOT_ADDRESS:-10.42.0.1}"; then
    say_ok "hotspot address" "${HOTSPOT_ADDRESS:-10.42.0.1} on ${WIFI_DEV:-wlan0}"
  else
    say_bad "hotspot address" "not on ${WIFI_DEV:-wlan0}"
  fi
fi
held="$(cat "$ARK_ETC/mode.pinned" 2>/dev/null || true)"
if [[ -n "$held" ]]; then
  printf '   %s network choice: held on "%s" by the owner (ark-mode auto undoes it)\n' "→" "$held"
fi
# The watchdog's last word, so "it stopped broadcasting" arrives with a reason.
if [[ -s "$ARK_ROOT/logs/net-watch.log" ]]; then
  last_word="$(tail -n 1 "$ARK_ROOT/logs/net-watch.log")"
  last_word="${last_word#*  }"
  say_soft "network watchdog" "last: ${last_word:0:80}"
else
  say_soft "network watchdog" "no decision logged yet (first check 3 min after boot)"
fi

# ---- how much room is left ---------------------------------------------------
free_gib="$(awk -v b="$(ark_free_bytes /)" 'BEGIN{printf "%.1f", b/1073741824}')"
root_use="$(df -h / | tail -1 | awk '{print $5}')"
if awk -v f="$free_gib" 'BEGIN{exit !(f >= 5)}'; then
  say_ok "free space" "${free_gib} GiB (root filesystem ${root_use} used)"
else
  say_bad "free space" "only ${free_gib} GiB left"
fi

# ---- what is inside ----------------------------------------------------------
zim_count="$(compgen -G "$ARK_ROOT/kiwix/*.zim" | wc -l | tr -d ' ')"
zim_gib="$(awk -v b="$(du -sb "$ARK_ROOT/kiwix" 2>/dev/null | cut -f1)" 'BEGIN{printf "%.1f", b/1073741824}')"
lib_titles="$(grep -c '<book ' "$ARK_ROOT/kiwix/library.xml" 2>/dev/null || true)"
lib_titles="${lib_titles:-0}"
books="$( [[ -s "$ARK_ROOT/calibre-library/metadata.db" ]] && sqlite3 -readonly "$ARK_ROOT/calibre-library/metadata.db" 'SELECT COUNT(*) FROM books;' 2>/dev/null || echo 0)"
guides="$(find "$ARK_ROOT/vault" -name '*.html' ! -name 'index.html' | wc -l | tr -d ' ')"
pdfs="$(find "$ARK_ROOT/vault/pdf" -name '*.pdf' 2>/dev/null | wc -l | tr -d ' ')"
games="$(sqlite3 -readonly "$ARK_ROOT/api/data/ark.db" \
  'SELECT COUNT(*) FROM games WHERE published = 1 AND hidden = 0;' 2>/dev/null || echo 0)"
player_names="$(sqlite3 -readonly "$ARK_ROOT/api/data/ark.db" \
  'SELECT COUNT(*) FROM players;' 2>/dev/null || echo 0)"
player_saves="$(sqlite3 -readonly "$ARK_ROOT/api/data/ark.db" \
  'SELECT COUNT(*) FROM player_saves;' 2>/dev/null || echo 0)"
printf '   %s collections: %s file(s), %s GiB · %s title(s) indexed\n' "→" "$zim_count" "$zim_gib" "$lib_titles"
printf '   %s books: %s · guides: %s page(s), %s printable\n' "→" "$books" "$guides" "$pdfs"
printf '   %s games: %s on the shelf · %s player name(s), %s save(s)\n' "→" "$games" "$player_names" "$player_saves"

# The player's name/save origin is TLS-only and separate from the management one.
player_code="$(ark_tls_code ark.lan:8445 /arcade/player)"
if [[ "$player_code" == "200" ]]; then
  say_ok "player sign-in origin" "https://ark.lan:8445 answers"
else
  say_bad "player sign-in origin" "HTTP ${player_code:-none} (expected 200; run phase 65-admin)"
fi

arcade_last="$(sqlite3 -readonly "$ARK_ROOT/api/data/ark.db" \
  "SELECT started_at || ' · ' || detail FROM acquisition_jobs ORDER BY id DESC LIMIT 1;" \
  2>/dev/null || true)"
if [[ -n "$arcade_last" ]]; then
  say_soft "last game check" "${arcade_last:0:100}"
else
  say_soft "last game check" "none yet — the first is 15 minutes after boot"
fi

# ---- recognising a game from the bytes inside it -----------------------------
# The name database is knowledge, not a service: a card without it plays every
# game and names each one from its file name.  So nothing here is a failure —
# but the reader itself is proved on a made-up cartridge, which needs no
# catalogue and no internet, so "it would recognise a real game" is a checked
# fact rather than a hope.
romdb_rows=""                     # unknown, never 0 (§8.7)
if [[ -r "$ARK_ROOT/romdb.sqlite" ]]; then
  romdb_rows="$(sqlite3 -readonly "$ARK_ROOT/romdb.sqlite" \
    "SELECT value FROM meta WHERE key = 'rows';" 2>/dev/null || true)"
fi
if [[ ! -f "$ARK_ROOT/romdb.sqlite" ]]; then
  say_soft "game names" "no name database yet — every game is named from its file name"
elif ! runuser -u "$ARK_PI_USER" -- test -r "$ARK_ROOT/romdb.sqlite" 2>/dev/null; then
  say_soft "game names" "the pages cannot open it — hand it to $ARK_PI_USER (chown $ARK_PI_USER: $ARK_ROOT/romdb.sqlite)"
elif [[ -n "$romdb_rows" ]]; then
  say_ok "game names" "$romdb_rows name(s) on this box"
else
  say_soft "game names" "present (how many names it holds could not be read)"
fi
if [[ -x /usr/local/sbin/ark-romdb-build ]]; then
  if runuser -u "$ARK_PI_USER" -- /usr/local/sbin/ark-romdb-build --self-test --quiet \
       >/dev/null 2>&1; then
    say_ok "game name reader" "a made-up cartridge was recognised from its own bytes"
  else
    say_soft "game name reader" \
      "the self-test did not pass — run: /usr/local/sbin/ark-romdb-build --self-test"
  fi
else
  say_soft "game name reader" "not installed — run phase 60-portal"
fi

# ---- the copy that makes a dead card survivable ------------------------------
# Nothing here is visitor-facing, so a missing or stale copy is reported softly:
# it is not a reason to call the box broken while somebody is reading it.
if [[ -x /usr/local/sbin/ark-backup && -x /usr/local/sbin/ark-restore ]]; then
  say_ok "backup tools" "ark-backup + ark-restore installed"
else
  say_soft "backup tools" "not installed — run phase 95-backup"
fi
backup_copies="$(find "$ARK_ROOT/backup" -mindepth 1 -maxdepth 1 -type d \
                   -name 'ark-backup-*' ! -name '*.partial' 2>/dev/null | wc -l | tr -d ' ')"
backup_newest="" ; backup_made="" ; backup_age_h=""
if [[ "${backup_copies:-0}" == "0" ]]; then
  say_soft "latest copy" "none yet — sudo ark-backup --to $ARK_ROOT/backup"
else
  backup_newest="$(ls -dt "$ARK_ROOT"/backup/ark-backup-*/ 2>/dev/null | head -n 1)"
  backup_newest="${backup_newest%/}"
  backup_made="$(sed -n 's/.*"created": "\([^"]*\)".*/\1/p' \
                   "$backup_newest/manifest.json" 2>/dev/null | head -n 1)"
  if [[ -n "$backup_made" ]]; then
    made_s="$(date -d "$backup_made" +%s 2>/dev/null || true)"
    if [[ -n "$made_s" ]]; then
      backup_age_h="$(( ( $(date +%s) - made_s ) / 3600 ))"
    fi
  fi
  if [[ -z "${backup_age_h:-}" ]]; then
    say_soft "latest copy" "$(basename "$backup_newest") (age not readable)"
  elif (( backup_age_h > 168 )); then
    say_soft "latest copy" "$(basename "$backup_newest") — $(( backup_age_h / 24 )) days old"
  else
    say_ok "latest copy" "$(basename "$backup_newest") — ${backup_age_h}h old"
  fi
  say_soft "copies kept" "$backup_copies in $ARK_ROOT/backup"
  # This report runs as root, so it can open a copy the box's own pages cannot.
  # That is exactly how a root:root bundle passed this gate while "What is
  # working" said no copy had ever been made.  Ask as the account the pages use.
  if ! runuser -u "$ARK_PI_USER" -- test -r "$backup_newest/manifest.json" 2>/dev/null; then
    say_soft "copy readable" \
      "the pages cannot open $(basename "$backup_newest") — re-run phase 95-backup to hand it over"
  fi
fi
if [[ -f "$ARK_ROOT/RECOVERY-SHEET.txt" ]]; then
  say_ok "recovery sheet" "printed copy at $ARK_ROOT/RECOVERY-SHEET.txt"
else
  say_soft "recovery sheet" "missing — run phase 95-backup, then print it"
fi

# ---- receipt for the Mac ------------------------------------------------------
ready_line="$(printf '%s' "${ready_json:-}" | python3 -c 'import json,sys; r=json.load(sys.stdin); print(json.dumps({"ok": r.get("ok"), "blocking": r.get("blocking") or [], "checked": r.get("checked")}))' 2>/dev/null || true)"
STATUS="$ARK_ROOT/ark_status.json"
tmp="$(mktemp)"
cat > "$tmp" <<JSON
{
  "checked": "$(ark_ts)",
  "hostname": "$(hostname)",
  "mode": "$mode",
  "ok": $( (( FAIL == 0 )) && echo true || echo false ),
  "problems": "$(IFS='; '; echo "${problems[*]:-}")",
  "ready": ${ready_line:-null},
  "collections": {"files": ${zim_count:-0}, "gib": ${zim_gib:-0}, "indexed": ${lib_titles:-0}},
  "books": ${books:-0},
  "guides": {"pages": ${guides:-0}, "pdfs": ${pdfs:-0}},
  "arcade": {"games": ${games:-0}, "players": ${player_names:-0}, "saves": ${player_saves:-0},
             "player_origin": "${player_code:-none}"},
  "game_names": {"rows": ${romdb_rows:-null}},
  "free_gib": ${free_gib:-0},
  "free_pct_used": "$root_use",
  "backup": {"copies": ${backup_copies:-0}, "newest": "$backup_newest", "made": "$backup_made"},
  "addresses": ["http://ark.lan", "http://ark.local", "http://${HOTSPOT_ADDRESS:-10.42.0.1}"]
}
JSON
install -m 644 "$tmp" "$STATUS" 2>/dev/null || cp -f "$tmp" "$STATUS"
chown "$ARK_PI_USER:$ARK_PI_GROUP" "$STATUS" 2>/dev/null || true
rm -f "$tmp"

if (( FAIL == 0 )); then
  ark_ok "everything a visitor touches is working"
  ark_ok "receipt written to $STATUS"
  exit 0
fi
ark_warn "some checks failed: ${problems[*]}"
ark_warn "receipt written to $STATUS — fixes are in /srv/ark/logs/install.log"
exit 1
