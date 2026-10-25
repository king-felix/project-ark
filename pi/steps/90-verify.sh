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
         ark-book-import.path ssh.service NetworkManager.service avahi-daemon.service; do
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
check_page "/api/status"          "live status"
check_page "/vault/"              "manuals index"
check_page "/vault/quick-card.html" "printable cards"
check_page "/generate_204"        "captive-portal trigger"
check_page "/kiwix/"              "offline library" "200 302 503"
check_page "/books/"              "book shelf"      "200 302"

# ---- the owner's door --------------------------------------------------------
check_page "/admin"               "admin stays off the public portal" "404"

adm_code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 15 \
            https://127.0.0.1:8443/admin -H 'Host: ark.lan' 2>/dev/null || true)"
case "$adm_code" in
  200|303|403) say_ok "management listener :8443" "HTTP $adm_code" ;;
  *)           say_bad "management listener :8443" "HTTP ${adm_code:-none}" ;;
esac

loop_code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 15 \
             https://127.0.0.1:8444/admin -H 'Host: localhost' 2>/dev/null || true)"
case "$loop_code" in
  200|303|403) say_ok "loopback setup listener :8444" "HTTP $loop_code" ;;
  *)           say_bad "loopback setup listener :8444" "HTTP ${loop_code:-none}" ;;
esac

community_code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 15 \
                   https://127.0.0.1:8443/admin/community -H 'Host: ark.lan' 2>/dev/null || true)"
case "$community_code" in
  200|303|403) say_ok "Community page behind the sign-in" "HTTP $community_code" ;;
  *)           say_bad "Community page behind the sign-in" "HTTP ${community_code:-none}" ;;
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
printf '   %s collections: %s file(s), %s GiB · %s title(s) indexed\n' "→" "$zim_count" "$zim_gib" "$lib_titles"
printf '   %s books: %s · guides: %s page(s), %s printable\n' "→" "$books" "$guides" "$pdfs"

# ---- receipt for the Mac ------------------------------------------------------
STATUS="$ARK_ROOT/ark_status.json"
tmp="$(mktemp)"
cat > "$tmp" <<JSON
{
  "checked": "$(ark_ts)",
  "hostname": "$(hostname)",
  "mode": "$mode",
  "ok": $( (( FAIL == 0 )) && echo true || echo false ),
  "problems": "$(IFS='; '; echo "${problems[*]:-}")",
  "collections": {"files": ${zim_count:-0}, "gib": ${zim_gib:-0}, "indexed": ${lib_titles:-0}},
  "books": ${books:-0},
  "guides": {"pages": ${guides:-0}, "pdfs": ${pdfs:-0}},
  "free_gib": ${free_gib:-0},
  "free_pct_used": "$root_use",
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
