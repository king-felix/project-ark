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

# ---- static pages -----------------------------------------------------------
for d in "$PORTAL" "$PORTAL/assets" "$API_DIR" "$API_DIR/data" "$ARK_ROOT/drop/files"; do
  ark_ensure_dir "$d" 775 "$ARK_PI_USER:$ARK_PI_GROUP"
done

ark_copy "$ARK_SRC/portal/index.html"      "$PORTAL/index.html"      644 "$ARK_PI_USER:$ARK_PI_GROUP"
ark_copy "$ARK_SRC/portal/help.html"       "$PORTAL/help.html"       644 "$ARK_PI_USER:$ARK_PI_GROUP"
ark_copy "$ARK_SRC/portal/assets/ark.css"  "$PORTAL/assets/ark.css"  644 "$ARK_PI_USER:$ARK_PI_GROUP"
ark_copy "$ARK_SRC/portal/assets/ark-admin.css" "$PORTAL/assets/ark-admin.css" 644 "$ARK_PI_USER:$ARK_PI_GROUP"
ark_ok "portal pages installed in $PORTAL"

# ---- backend ----------------------------------------------------------------
for f in main.py ark_db.py ark_security.py ark_admin.py ark_recover.py; do
  ark_copy "$ARK_SRC/api/$f" "$API_DIR/$f" 644 "$ARK_PI_USER:$ARK_PI_GROUP"
done
ark_copy "$ARK_SRC/api/requirements.txt" "$API_DIR/requirements.txt" 644 "$ARK_PI_USER:$ARK_PI_GROUP"

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
ark_ok "backend answering on 127.0.0.1:8085 (health + status)"

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
check "http://127.0.0.1/vault/"           "manuals index"
check "http://127.0.0.1/generate_204"     "captive-portal trigger"
# 503 is the honest answer while the very first collection is still arriving
check "http://127.0.0.1/kiwix/"           "offline library" "503"
check "http://127.0.0.1/books/"           "book shelf"      "503"

(( fail == 0 )) || ark_warn "some pages did not answer 200 — see the column on the right"
ark_ok "phase 6 complete — the portal is live at http://ark.lan"
