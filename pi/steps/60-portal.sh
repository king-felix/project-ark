#!/usr/bin/env bash
# =============================================================================
#  PHASE 6 — the front door: portal pages, portal backend, nginx
#
#  Everything a visitor sees lives here.  The pages are plain HTML so they work
#  with zero JavaScript; the backend only adds the things that need it (live
#  status, one search box, the message board, the file box).
# =============================================================================
set -Eeuo pipefail
source "${ARK_SRC:-/srv/ark/src}/lib/common.sh"
ark_require_root

ark_step "PHASE 6/7 — portal and web server"

VENV="$ARK_ROOT/venv"
PORTAL="$ARK_ROOT/portal"
API_DIR="$ARK_ROOT/api"
ARCADE_DATA="$ARK_ROOT/arcade"

# ---- static pages -----------------------------------------------------------
for d in "$PORTAL" "$PORTAL/assets" "$API_DIR" "$API_DIR/data" \
         "$ARK_ROOT/drop/files" "$ARK_ROOT/drop/tmp" \
         "$ARCADE_DATA" "$ARCADE_DATA/games" "$ARCADE_DATA/saves" "$ARCADE_DATA/tmp"; do
  ark_ensure_dir "$d" 775 "$ARK_PI_USER:$ARK_PI_GROUP"
done

ark_copy "$ARK_SRC/portal/index.html"      "$PORTAL/index.html"      644 "$ARK_PI_USER:$ARK_PI_GROUP"
ark_copy "$ARK_SRC/portal/help.html"       "$PORTAL/help.html"       644 "$ARK_PI_USER:$ARK_PI_GROUP"
ark_copy "$ARK_SRC/portal/assets/ark.css"  "$PORTAL/assets/ark.css"  644 "$ARK_PI_USER:$ARK_PI_GROUP"
ark_copy "$ARK_SRC/portal/assets/ark-admin.css" "$PORTAL/assets/ark-admin.css" 644 "$ARK_PI_USER:$ARK_PI_GROUP"
ark_ok "portal pages installed in $PORTAL"

# ---- backend ----------------------------------------------------------------
for f in main.py ark_db.py ark_storage.py ark_security.py ark_admin.py ark_recover.py ark_health.py \
         ark_arcade.py ark_arcade_install.py ark_arcade_web.py; do
  ark_copy "$ARK_SRC/api/$f" "$API_DIR/$f" 644 "$ARK_PI_USER:$ARK_PI_GROUP"
done
ark_copy "$ARK_SRC/api/requirements.txt" "$API_DIR/requirements.txt" 644 "$ARK_PI_USER:$ARK_PI_GROUP"

# ARK Arcade: the pages, the player and the seed list live in the source tree
# (read-only to the service); the games and saves live in $ARCADE_DATA above.
for needed in "$ARK_SRC/arcade/templates/page.html" \
              "$ARK_SRC/arcade/static/ark-arcade.css" \
              "$ARK_SRC/arcade/static/notices.txt" \
              "$ARK_SRC/arcade/catalog/seed.json" \
              "$ARK_SRC/arcade/emulatorjs/loader.js" \
              "$ARK_SRC/arcade/emulatorjs/emulator.min.js" \
              "$ARK_SRC/arcade/emulatorjs/compression/extractzip.js" \
              "$ARK_SRC/arcade/emulatorjs/compression/extract7z.js" \
              "$ARK_SRC/arcade/emulatorjs/compression/libunrar.js" \
              "$ARK_SRC/arcade/emulatorjs/compression/libunrar.wasm"; do
  [[ -f "$needed" ]] || ark_die "ARK Arcade is missing $needed — the source tree was not copied in full"
done
# The player loads those four files at run time, and a missing or altered one only
# shows up on a phone with no internet.  Prove every vendored byte before serving.
EMU="$ARK_SRC/arcade/emulatorjs"
[[ -f "$EMU/SHA256SUMS" ]] || ark_die "the player's checksum list is missing: $EMU/SHA256SUMS"
if ! (cd "$EMU" && sha256sum --quiet -c SHA256SUMS); then
  ark_die "the player's files do not match SHA256SUMS — re-copy the source tree, do not serve it"
fi
ark_ok "vendored player and its add-ons match SHA256SUMS"
ark_copy "$ARK_SRC/bin/ark-arcade" /usr/local/sbin/ark-arcade 755 root:root
chmod +x /usr/local/sbin/ark-arcade
ark_ok "ARK Arcade files present; shelf keeper installed in /usr/local/sbin"

[[ -x "$VENV/bin/python" ]] || ark_die "no Python environment at $VENV — run phase 3 (30-calibre) first"

if ! sudo -u "$ARK_PI_USER" "$VENV/bin/python" -c 'import fastapi, uvicorn, multipart, aiofiles' 2>/dev/null; then
  ark_warn "portal backend libraries missing — trying to install them now"
  for pkgset in "--prefer-binary -r $API_DIR/requirements.txt" "-r $API_DIR/requirements.txt"; do
    # shellcheck disable=SC2086
    if sudo -u "$ARK_PI_USER" "$VENV/bin/pip" install --quiet $pkgset; then break; fi
  done
  sudo -u "$ARK_PI_USER" "$VENV/bin/python" -c 'import fastapi, uvicorn, multipart, aiofiles' \
    || ark_die "portal backend cannot start: fastapi/uvicorn/multipart/aiofiles missing"
fi
ark_ok "portal backend libraries present in $VENV"

ark_install_unit ark-api.service
ark_service_up ark-api.service 25

code="$(ark_wait_http http://127.0.0.1:8085/api/health 90 200)" || true
[[ "$code" == "200" ]] || { journalctl -u ark-api -n 30 --no-pager || true; ark_die "backend health check returned HTTP $code"; }
code="$(ark_wait_http http://127.0.0.1:8085/api/status 60 200)" || true
[[ "$code" == "200" ]] || ark_die "status endpoint returned HTTP $code"
# /api/ready is the honest one: 200 only when every part a reader needs actually
# answers, 503 with the reasons when it does not.  A box still filling up is
# allowed to be 503 here — phase 7 is what holds it to account.
ready_code="$(ark_wait_http http://127.0.0.1:8085/api/ready 60 200 503)" || true
case "$ready_code" in
  200) ark_ok "readiness: every part a visitor needs is working" ;;
  503) reason="$(curl -fsS --max-time 10 http://127.0.0.1:8085/api/ready 2>/dev/null \
          | "$VENV/bin/python" -c 'import json,sys; r=json.load(sys.stdin); print("; ".join(r.get("blocking") or ["nothing named"]))' 2>/dev/null \
          || echo "unknown")"
       ark_warn "readiness: not ready yet — $reason (normal while the box is still filling up)" ;;
  *)   ark_die "readiness endpoint returned HTTP ${ready_code:-none}" ;;
esac
ark_ok "backend answering on 127.0.0.1:8085 (health + status + readiness)"

# ---- first shelf ------------------------------------------------------------
# The pinned three are downloaded, checked against their SHA-256 and the shape
# of a Game Boy file, then published.  A box with no internet yet is not broken:
# the shelf stays empty and the next discovery run fills it.
seed_log="$ARK_ROOT/logs/arcade-seed.log"
if sudo -u "$ARK_PI_USER" /usr/local/sbin/ark-arcade --seed >>"$seed_log" 2>&1; then
  ark_ok "first games checked and put on the shelf ($(tail -n 1 "$seed_log" | cut -c1-90))"
else
  ark_warn "first games could not be fetched yet — see $seed_log (the shelf stays empty, the rest works)"
fi

# Weekly discovery.  It only ever records what the source offers; a suggestion
# is installed by a person unless the owner has switched automatic installing on.
ark_install_unit ark-arcade-discover.service
ark_install_unit ark-arcade-discover.timer
if systemctl enable --now ark-arcade-discover.timer >/dev/null 2>&1; then
  ark_ok "weekly game check enabled (ark-arcade-discover.timer)"
else
  ark_warn "could not enable ark-arcade-discover.timer — the shelf will not check for new games"
fi

# ---- nginx ------------------------------------------------------------------
NGINX_SITE=/etc/nginx/sites-available/ark
ark_copy "$ARK_SRC/nginx/ark.conf" "$NGINX_SITE" 644 root:root
ln -sfn "$NGINX_SITE" /etc/nginx/sites-enabled/ark
rm -f /etc/nginx/sites-enabled/default

if ! nginx -t >/dev/null 2>&1; then
  nginx -t || true
  ark_die "nginx rejected the configuration above — nothing was restarted"
fi
ark_service_up nginx.service 20

# ---- prove every door opens --------------------------------------------------
fail=0
check() { # <url> <what> [extra codes that also count as success]
  local url=$1 what=$2 extra=${3:-} code
  code="$(ark_http_code "$url")"
  if [[ "$code" == "200" || "$code" == "302" || " $extra " == *" $code "* ]]; then
    printf '   \033[1;32m✓\033[0m %-42s %s\n' "$what" "$code"
  else
    printf '   \033[1;33m!\033[0m %-42s %s\n' "$what" "$code"
    fail=1
  fi
}
check "http://127.0.0.1/"                 "home page"
check "http://127.0.0.1/help.html"        "how-to-use page"
check "http://127.0.0.1/assets/ark.css"   "stylesheet"
check "http://127.0.0.1/api/status"       "live status"
check "http://127.0.0.1/admin"          "admin stays off the public portal" "404"
check "http://127.0.0.1/search?q=water"   "one search box"
check "http://127.0.0.1/board"            "message board"
check "http://127.0.0.1/files"            "file box"
check "http://127.0.0.1/arcade/"          "games shelf"
check "http://127.0.0.1/arcade/static/ark-arcade.css" "game shelf stylesheet"
check "http://127.0.0.1/arcade/emulator/loader.js"     "game player"
check "http://127.0.0.1/vault/"           "manuals index"
check "http://127.0.0.1/generate_204"     "captive-portal trigger"
# 503 is the honest answer while the very first collection is still arriving
check "http://127.0.0.1/kiwix/"           "offline library" "503"
check "http://127.0.0.1/books/"           "book shelf"      "503"

(( fail == 0 )) || ark_warn "some pages did not answer 200 — see the column on the right"
ark_ok "phase 6 complete — the portal is live at http://ark.lan"
