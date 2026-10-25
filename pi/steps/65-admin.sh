#!/usr/bin/env bash
# =============================================================================
#  PHASE 6b — the owner's door: HTTPS management listener, certificate,
#             and the root-only recovery command.
#
#  Nothing here is public.  :8443 serves only /admin and the stylesheet;
#  :8444 is loopback-only and exists for the first-run SSH tunnel; :80 keeps
#  answering 404 for /admin so a password can never be posted over plain HTTP.
# =============================================================================
set -Eeuo pipefail
source "${ARK_SRC:-/srv/ark/src}/lib/common.sh"
ark_require_root

ark_step "PHASE 6b — administrator access"

API_DIR="$ARK_ROOT/api"
ADMIN_SITE=/etc/nginx/sites-available/ark-admin

# ---- code -------------------------------------------------------------------
for f in ark_db.py ark_security.py ark_admin.py ark_recover.py; do
  ark_copy "$ARK_SRC/api/$f" "$API_DIR/$f" 644 "$ARK_PI_USER:$ARK_PI_GROUP"
done
ark_copy "$ARK_SRC/portal/assets/ark-admin.css" \
  "$ARK_ROOT/portal/assets/ark-admin.css" 644 "$ARK_PI_USER:$ARK_PI_GROUP"

# ---- helpers ----------------------------------------------------------------
ark_copy "$ARK_SRC/bin/ark-tls-cert" /usr/local/sbin/ark-tls-cert 755 root:root
ark_copy "$ARK_SRC/bin/ark-admin-recover" /usr/local/sbin/ark-admin-recover 700 root:root
ark_ok "installed /usr/local/sbin/ark-tls-cert and ark-admin-recover (root only)"

# ---- certificate ------------------------------------------------------------
# Idempotent: it keeps an existing certificate until 30 days before expiry.
ARK_SRC="$ARK_SRC" /usr/local/sbin/ark-tls-cert | sed 's/^/   /'

# ---- nginx ------------------------------------------------------------------
ark_copy "$ARK_SRC/nginx/ark-admin.conf" "$ADMIN_SITE" 644 root:root
ln -sfn "$ADMIN_SITE" /etc/nginx/sites-enabled/ark-admin

if ! nginx -t >/dev/null 2>&1; then
  nginx -t || true
  ark_die "nginx rejected the administrator configuration above — nothing was restarted"
fi
ark_service_up nginx.service 20
ark_ok "nginx reloaded with the HTTPS management listener"

# ---- prove the doors --------------------------------------------------------
say() { printf '   %s %-46s %s\n' "$1" "$2" "$3"; }

public_admin="$(ark_http_code http://127.0.0.1/admin)"
if [[ "$public_admin" == "404" ]]; then
  say "$(printf '\033[1;32m✓\033[0m')" "public :80 refuses /admin" "$public_admin"
else
  say "$(printf '\033[1;31m✗\033[0m')" "public :80 refuses /admin" "$public_admin (expected 404)"
  ark_die "/admin is reachable on the plain-HTTP portal"
fi

code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 20 \
        https://127.0.0.1:8443/admin -H 'Host: ark.lan' || true)"
case "$code" in
  200|303|403) say "$(printf '\033[1;32m✓\033[0m')" "management listener :8443 answers" "$code" ;;
  *)           ark_die "https://127.0.0.1:8443/admin returned '$code'" ;;
esac

# The Community page sits behind the same sign-in, and must not appear elsewhere.
code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 20 \
        https://127.0.0.1:8443/admin/community -H 'Host: ark.lan' || true)"
case "$code" in
  200|303|403) say "$(printf '\033[1;32m✓\033[0m')" "Community page behind the sign-in" "$code" ;;
  *)           ark_die "https://127.0.0.1:8443/admin/community returned '$code'" ;;
esac

code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 20 \
        https://127.0.0.1:8444/admin -H 'Host: localhost' || true)"
case "$code" in
  200|303|403) say "$(printf '\033[1;32m✓\033[0m')" "loopback setup listener :8444 answers" "$code" ;;
  *)           ark_die "https://127.0.0.1:8444/admin returned '$code'" ;;
esac

# The management origin must not serve the library or shared files.
for path in /kiwix/ /books/ /drop/ /vault/ /; do
  code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 10 \
          "https://127.0.0.1:8443$path" -H 'Host: ark.lan' || true)"
  if [[ "$code" == "404" ]]; then
    say "$(printf '\033[1;32m✓\033[0m')" "management origin hides $path" "$code"
  else
    ark_die "the management origin served $path with HTTP $code"
  fi
done

# The listener is HTTPS-only: plain HTTP on 8443 must fail.
if curl -s -o /dev/null --max-time 5 http://127.0.0.1:8443/admin 2>/dev/null; then
  ark_die "port 8443 answered plain HTTP — it must be TLS only"
fi
say "$(printf '\033[1;32m✓\033[0m')" "port 8443 refuses plain HTTP" "closed"

printf '   %s fingerprint: %s\n' "→" "$(/usr/local/sbin/ark-tls-cert --show)"
ark_ok "phase 6b complete — sign in at https://ark.lan:8443/admin"
