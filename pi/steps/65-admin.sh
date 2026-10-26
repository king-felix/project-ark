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
PLAYER_SITE=/etc/nginx/sites-available/ark-players

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
ark_copy "$ARK_SRC/nginx/ark-players.conf" "$PLAYER_SITE" 644 root:root
ln -sfn "$PLAYER_SITE" /etc/nginx/sites-enabled/ark-players

if ! nginx -t >/dev/null 2>&1; then
  nginx -t || true
  ark_die "nginx rejected the administrator/player configuration above — nothing was restarted"
fi
ark_service_up nginx.service 20
ark_ok "nginx reloaded with the HTTPS management and player listeners"

# ---- prove the doors --------------------------------------------------------
say() { printf '   %s %-46s %s\n' "$1" "$2" "$3"; }

# Every TLS probe below asks by name and reaches loopback with --resolve: a
# request to an address literal carries no SNI, nginx then serves the default
# server (which refuses the handshake), and curl reports 000 on a healthy door.
# The helper lives in pi/lib/common.sh so 65-admin and 90-verify cannot drift.
tls_code() { # <host:port> <path>
  ark_tls_code "$@"
}

public_admin="$(ark_http_code http://127.0.0.1/admin)"
if [[ "$public_admin" == "404" ]]; then
  say "$(printf '\033[1;32m✓\033[0m')" "public :80 refuses /admin" "$public_admin"
else
  say "$(printf '\033[1;31m✗\033[0m')" "public :80 refuses /admin" "$public_admin (expected 404)"
  ark_die "/admin is reachable on the plain-HTTP portal"
fi

# TLS liveness and an expected unauthenticated answer are two different facts.
# /admin must send a stranger to the sign-in (303) or refuse (401/403); a 200
# here would mean the management pages are readable without signing in.
code="$(tls_code ark.lan:8443 /admin)"
case "$code" in
  303|401|403) say "$(printf '\033[1;32m✓\033[0m')" "management listener :8443 answers, sign-in required" "$code" ;;
  200)         ark_die "https://ark.lan:8443/admin returned 200 without a sign-in" ;;
  *)           ark_die "https://ark.lan:8443/admin returned '$code'" ;;
esac

# The Community page sits behind the same sign-in, and must not appear elsewhere.
code="$(tls_code ark.lan:8443 /admin/community)"
case "$code" in
  303|401|403) say "$(printf '\033[1;32m✓\033[0m')" "Community page behind the sign-in" "$code" ;;
  200)         ark_die "https://ark.lan:8443/admin/community returned 200 without a sign-in" ;;
  *)           ark_die "https://ark.lan:8443/admin/community returned '$code'" ;;
esac

# The Household page is behind the same sign-in as everything else in /admin.
code="$(tls_code ark.lan:8443 /admin/household)"
case "$code" in
  303|401|403) say "$(printf '\033[1;32m✓\033[0m')" "Household page behind the sign-in" "$code" ;;
  200)         ark_die "https://ark.lan:8443/admin/household returned 200 without a sign-in" ;;
  *)           ark_die "https://ark.lan:8443/admin/household returned '$code'" ;;
esac

# The page pack is the one non-/admin path on this origin: a visitor who follows
# the link without a pack on the card gets a readable refusal, never an nginx 404
# page (which is what an unlisted location would give).  Both answers are honest;
# a 500 or a 000 is not.
code="$(tls_code ark.lan:8443 /essentials.zip)"
case "$code" in
  200) say "$(printf '\033[1;32m✓\033[0m')" "page pack offered on :8443" "200" ;;
  404) say "$(printf '\033[1;32m✓\033[0m')" "page pack path answers (no pack made yet)" "404" ;;
  *)   ark_die "https://ark.lan:8443/essentials.zip returned '$code'" ;;
esac

# The loopback setup listener is reached before an account exists, so its
# sign-in page (200) is the expected unauthenticated answer.
code="$(tls_code localhost:8444 /admin)"
case "$code" in
  200|303|401|403) say "$(printf '\033[1;32m✓\033[0m')" "loopback setup listener :8444 answers" "$code" ;;
  *)               ark_die "https://localhost:8444/admin returned '$code'" ;;
esac

# The management origin must not serve the library or shared files.
for path in /kiwix/ /books/ /drop/ /vault/ /; do
  code="$(tls_code ark.lan:8443 "$path")"
  if [[ "$code" == "404" ]]; then
    say "$(printf '\033[1;32m✓\033[0m')" "management origin hides $path" "$code"
  else
    ark_die "the management origin served $path with HTTP $code"
  fi
done

# The listener is HTTPS-only.  A completed 400/497 is nginx doing its job; a
# completed 200 is a management page sent in clear text and must stop the
# install.  curl exits zero for both, which is the defect this check removes.
plain_code="$(ark_http_code http://127.0.0.1:8443/admin)"
plain_verdict="$(ark_plain_http_verdict "$plain_code")"
if ! ark_plain_http_is_rejected "$plain_code"; then
  ark_die "port 8443 answered plain HTTP with $plain_code ($plain_verdict) — it must be TLS only"
fi
say "$(printf '\033[1;32m✓\033[0m')" "port 8443 refuses plain HTTP" "$plain_verdict"

# ---- the player's origin ----------------------------------------------------
code="$(tls_code ark.lan:8445 /arcade/player)"
case "$code" in
  200) say "$(printf '\033[1;32m✓\033[0m')" "player origin :8445 answers" "$code" ;;
  *)   ark_die "https://ark.lan:8445/arcade/player returned '$code'" ;;
esac

code="$(tls_code ark.lan:8445 /admin)"
[[ "$code" == "404" ]] || ark_die "the player origin served /admin with HTTP $code"
say "$(printf '\033[1;32m✓\033[0m')" "player origin hides /admin" "$code"

plain_code="$(ark_http_code http://127.0.0.1:8445/arcade/)"
plain_verdict="$(ark_plain_http_verdict "$plain_code")"
if ! ark_plain_http_is_rejected "$plain_code"; then
  ark_die "port 8445 answered plain HTTP with $plain_code ($plain_verdict) — it must be TLS only"
fi
say "$(printf '\033[1;32m✓\033[0m')" "port 8445 refuses plain HTTP" "$plain_verdict"

# The public portal must send a named player to the secure origin, and a header
# invented by a visitor must not make the public proxy pretend to be it.
public_player="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
  -H 'X-ARK-Player-Origin: 1' http://127.0.0.1/arcade/player || true)"
[[ "$public_player" == "307" ]] \
  || ark_die "public /arcade/player returned '$public_player' (expected a 307 to :8445)"
say "$(printf '\033[1;32m✓\033[0m')" "public shelf sends names to :8445" "$public_player"

public_shelf="$(ark_http_code http://127.0.0.1/arcade/)"
[[ "$public_shelf" == "200" ]] || ark_die "public /arcade/ returned HTTP $public_shelf"
say "$(printf '\033[1;32m✓\033[0m')" "public shelf answers for guests" "$public_shelf"

printf '   %s fingerprint: %s\n' "→" "$(/usr/local/sbin/ark-tls-cert --show)"
ark_ok "phase 6b complete — sign in at https://ark.lan:8443/admin"
