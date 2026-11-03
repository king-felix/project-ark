#!/usr/bin/env bash
# =============================================================================
#  PHASE 6.6 — Maps: the application, the shelf, and the secure public door
#
#  The application is static files and the shelf is a folder tree; no new
#  service, no new account, no new port on the internet.  The one thing that
#  must be right is the origin: a page that reopens offline needs HTTPS the
#  device trusts, so this phase adds :8446 with the Ark's own certificate and
#  serves nothing there but /maps/.
#
#  A box with no map packs is a working box.  The Maps page says no region is
#  ready, and every other reader's page is untouched.
# =============================================================================
set -Eeuo pipefail
source "${ARK_SRC:-/srv/ark/src}/lib/common.sh"
ark_require_root

ark_step "PHASE 6.6 — maps"

MAPS="$ARK_ROOT/maps"

# ---- folders ---------------------------------------------------------------
for d in "$MAPS" "$MAPS/packs" "$MAPS/incoming" "$MAPS/tmp" "$MAPS/web"; do
  ark_ensure_dir "$d" 775 "$ARK_PI_USER:$ARK_PI_GROUP"
done
ark_ok "map shelf ready under $MAPS"

# ---- the application -------------------------------------------------------
# --delete is safe here: this folder is the application, not data.  The packs
# live beside it and are never touched by an install.
if [[ -d "$ARK_SRC/maps/web" ]]; then
  rsync -a --delete --chown="$ARK_PI_USER:$ARK_PI_GROUP" --chmod=D755,F644 \
        "$ARK_SRC/maps/web/" "$MAPS/web/"
  ark_ok "maps application installed ($(find "$MAPS/web" -type f | wc -l) files)"
else
  ark_die "the maps application is missing from $ARK_SRC/maps/web"
fi

# ---- the public secure door ------------------------------------------------
if [[ ! -r /etc/ssl/ark/ark-admin.crt || ! -r /etc/ssl/ark/ark-admin.key ]]; then
  ark_info "no certificate yet — creating one so the maps door can start"
  bash "$ARK_SRC/bin/ark-tls-cert" || ark_die "the maps listener needs the Ark certificate"
fi
# The API runs as pi and cannot traverse /etc/ssl/ark (0700 root).  Publish only
# the public half where it can read it; the private key stays 0600 root.
install -m 644 -o "$ARK_PI_USER" -g "$ARK_PI_GROUP" \
  /etc/ssl/ark/ark-admin.crt "$MAPS/ark-certificate.crt"
ark_ok "the public certificate is readable by the maps application"
MAPS_SITE=/etc/nginx/sites-available/ark-maps
ark_copy "$ARK_SRC/nginx/ark-maps.conf" "$MAPS_SITE" 644 root:root
ln -sfn "$MAPS_SITE" /etc/nginx/sites-enabled/ark-maps
if ! nginx -t >/dev/null 2>&1; then
  nginx -t || true
  ark_die "nginx rejected the maps configuration above — nothing was restarted"
fi
ark_service_up nginx.service 20
ark_ok "nginx reloaded with the maps listener on :8446"

# ---- the operator's door ---------------------------------------------------
ark_copy "$ARK_SRC/bin/ark-maps" /usr/local/sbin/ark-maps 755 root:root
ark_ok "map shelf helper installed: /usr/local/sbin/ark-maps (run as $ARK_PI_USER)"

# ---- prove it --------------------------------------------------------------
INDEX_CODE="$(ark_tls_code ark.local:8446 /maps/ || true)"
CATALOG_CODE="$(ark_tls_code ark.local:8446 /maps/api/catalog || true)"
SW_HEAD="$(curl -sk --resolve ark.local:8446:127.0.0.1 -D - -o /dev/null \
  https://ark.local:8446/maps/sw.js 2>/dev/null | tr -d '\r' | grep -i '^service-worker-allowed' || true)"
CERT_CODE="$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: ark.local' \
  http://127.0.0.1/certificate.crt || true)"
ADMIN_CODE="$(ark_tls_code ark.local:8443 /admin/maps || true)"

if [[ "$INDEX_CODE" == "200" && "$CATALOG_CODE" == "200" ]]; then
  ark_ok "the maps application answers on https://ark.local:8446/maps/ (200)"
else
  ark_warn "the maps application answered index=$INDEX_CODE catalog=$CATALOG_CODE"
fi
if [[ "$SW_HEAD" == *"/maps/"* ]]; then
  ark_ok "the service worker is allowed to hold the /maps/ scope"
else
  ark_warn "the service worker header is missing or wrong: ${SW_HEAD:-none}"
fi
if [[ "$CERT_CODE" == "200" ]]; then
  ark_ok "the public certificate is offered on the ordinary portal"
else
  ark_warn "the certificate download answered $CERT_CODE"
fi
if [[ "$ADMIN_CODE" == "303" ]]; then
  ark_ok "the owner's maps page asks for a sign-in (303) on the management door"
else
  ark_warn "the owner's maps page answered $ADMIN_CODE on :8443"
fi

PACKS="$(sudo -u "$ARK_PI_USER" /usr/local/sbin/ark-maps --list 2>/dev/null | head -n 5 || true)"
if [[ -n "$PACKS" ]]; then
  ark_info "map shelf contents:"
  printf '%s\n' "$PACKS"
else
  ark_info "no map packs on the card yet — the Maps page will say so, which is the truth"
fi
