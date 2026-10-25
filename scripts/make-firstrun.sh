#!/usr/bin/env bash
# =============================================================================
#  Renders staging/firstrun.sh from the template + wifi.env + the Mac's SSH key.
#  Safe single-quote escaping is done in Python (no sed-injection issues).
#  Usage: bash scripts/make-firstrun.sh
# =============================================================================
set -Eeuo pipefail

ROOT="/Users/basestar/Survival-Pi"
ENV_FILE="$ROOT/staging/wifi.env"
TMPL="$ROOT/staging/firstrun.sh.tmpl"
OUT="$ROOT/staging/firstrun.sh"
PUBKEY_FILE="$HOME/.ssh/id_ed25519.pub"

die() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[[ -f "$ENV_FILE" ]] || die "Missing $ENV_FILE"
[[ -f "$TMPL" ]]     || die "Missing template $TMPL"
[[ -f "$PUBKEY_FILE" ]] || die "Missing $PUBKEY_FILE — run: ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519"

# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

[[ -n "${WIFI_SSID:-}" ]] || die "WIFI_SSID is empty in $ENV_FILE (see the comments in that file)"
WIFI_COUNTRY="${WIFI_COUNTRY:-US}"
[[ ${#WIFI_COUNTRY} -eq 2 ]] || die "WIFI_COUNTRY must be a 2-letter code (got '$WIFI_COUNTRY')"

# The `pi` password. Taken from wifi.env if you set PI_PASSWORD, otherwise a
# random one is generated here — so no default password exists in this repo for
# anyone to look up, and every Ark built from it is different. It is rendered
# into firstrun.sh (chmod 700) and stored in staging/pi-password.txt (chmod 600),
# both gitignored. chpasswd splits on ':', so a colon would silently truncate.
PI_PASSWORD="${PI_PASSWORD:-}"
if [[ -z "$PI_PASSWORD" ]]; then
  PI_PASSWORD="$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 20 || true)"
fi
[[ ${#PI_PASSWORD} -ge 8 ]] || die "PI_PASSWORD must be at least 8 characters"
[[ "$PI_PASSWORD" != *:* && "$PI_PASSWORD" != *$'\n'* ]] || die "PI_PASSWORD must not contain ':' or a newline"


# Extra networks, tried in this order if the primary is out of range.
# Same password unless you set a dedicated *_PASSWORD for that slot.
WIFI_SSID_2="${WIFI_FALLBACK_SSID:-}"
WIFI_PSK_2="${WIFI_FALLBACK_PASSWORD:-${WIFI_PASSWORD:-}}"
WIFI_SSID_3="${WIFI_SSID_3:-}"
WIFI_PSK_3="${WIFI_PASSWORD_3:-${WIFI_PASSWORD:-}}"

# Drop blank and duplicate slots so the Pi never gets two profiles for one SSID.
# (plain if-blocks: `[[ ... ]] && x=` would return 1 and trip `set -e`)
if [[ "$WIFI_SSID_2" == "$WIFI_SSID" ]]; then WIFI_SSID_2=""; fi
if [[ "$WIFI_SSID_3" == "$WIFI_SSID" || "$WIFI_SSID_3" == "$WIFI_SSID_2" ]]; then WIFI_SSID_3=""; fi

# Extra networks that are NOT the home router: another house, a phone hotspot, a
# friend's place. wifi.env carries them as one `id|ssid|password|priority` per
# line (WIFI_EXTRA_PROFILES). Their priorities sit below the home router's
# 20/10/5, so the box still prefers home whenever home is in range — raise one
# here if a particular house should win instead.
#
# The rendered block is substituted verbatim into a double-quoted bash string in
# the template, so a value containing " $ ` or \ would not survive it: refuse it
# now, rather than render a profile that authenticates with the wrong key.
WIFI_EXTRA_PROFILES="${WIFI_EXTRA_PROFILES:-}"
WIFI_EXTRA_RENDERED=""
_seen_ssids=" ${WIFI_SSID} ${WIFI_SSID_2} ${WIFI_SSID_3} "
while IFS='|' read -r ex_id ex_ssid ex_psk ex_prio; do
  if [[ -z "${ex_id}${ex_ssid}${ex_psk}${ex_prio}" ]]; then continue; fi
  if [[ -z "$ex_id" || -z "$ex_ssid" || -z "$ex_prio" ]]; then
    die "WIFI_EXTRA_PROFILES needs id|ssid|password|priority per line (leave the password empty for an open network)"
  fi
  if [[ ! "$ex_id" =~ ^[a-z0-9][a-z0-9_-]*$ ]]; then
    die "WIFI_EXTRA_PROFILES: profile id '$ex_id' must be lowercase letters, digits, '-' or '_' (it becomes a filename)"
  fi
  if [[ ${#ex_ssid} -gt 32 ]]; then
    die "Wi-Fi SSID max length is 32 characters (got '$ex_ssid')"
  fi
  if [[ -n "$ex_psk" && ${#ex_psk} -lt 8 ]]; then
    die "WIFI_EXTRA_PROFILES: the password for '$ex_ssid' is shorter than 8 characters (WPA2 requirement)"
  fi
  if [[ ! "$ex_prio" =~ ^[0-9]{1,2}$ ]]; then
    die "WIFI_EXTRA_PROFILES: priority '$ex_prio' for '$ex_ssid' is not a number"
  fi
  case "$ex_id$ex_ssid$ex_psk" in
    *'"'*|*'$'*|*'`'*|*'\'*)
      die "WIFI_EXTRA_PROFILES: '$ex_ssid' contains a quote, a dollar sign, a backtick or a backslash and cannot be rendered into firstrun.sh" ;;
  esac
  case "$_seen_ssids" in
    *" $ex_ssid "*) die "WIFI_EXTRA_PROFILES: '$ex_ssid' is already one of the home slots above" ;;
  esac
  _seen_ssids="${_seen_ssids}${ex_ssid} "
  WIFI_EXTRA_RENDERED="${WIFI_EXTRA_RENDERED}"$'\n'"${ex_id}|${ex_ssid}|${ex_psk}|${ex_prio}"
done <<< "$WIFI_EXTRA_PROFILES"

for ssid in "$WIFI_SSID" "$WIFI_SSID_2" "$WIFI_SSID_3"; do
  if [[ -z "$ssid" ]]; then continue; fi
  [[ ${#ssid} -le 32 ]] || die "Wi-Fi SSID max length is 32 characters (got '${ssid}', ${#ssid} chars)"
done
for psk in "${WIFI_PASSWORD:-}" "$WIFI_PSK_2" "$WIFI_PSK_3"; do
  [[ -z "$psk" || ${#psk} -ge 8 ]] || die "a Wi-Fi password is shorter than 8 characters (WPA2 requirement), or leave it empty for an open network"
done

export WIFI_SSID WIFI_PASSWORD WIFI_COUNTRY WIFI_SSID_2 WIFI_PSK_2 WIFI_SSID_3 WIFI_PSK_3 WIFI_EXTRA_RENDERED PI_PASSWORD

python3 - "$TMPL" "$OUT" "$(cat "$PUBKEY_FILE")" <<'PY'
import os, sys, pathlib
tmpl, out, key = sys.argv[1:4]
env = os.environ
def q(s):            # bash-safe single-quoted literal
    return s.replace("'", "'\\''")
vals = (
    ("__WIFI_SSID__",    q(env["WIFI_SSID"])),
    ("__WIFI_PSK__",     q(env.get("WIFI_PASSWORD", ""))),
    ("__WIFI_SSID_2__",  q(env.get("WIFI_SSID_2", ""))),
    ("__WIFI_PSK_2__",   q(env.get("WIFI_PSK_2", ""))),
    ("__WIFI_SSID_3__",  q(env.get("WIFI_SSID_3", ""))),
    ("__WIFI_PSK_3__",   q(env.get("WIFI_PSK_3", ""))),
    ("__WIFI_COUNTRY__", q(env["WIFI_COUNTRY"])),
    ("__PI_PASSWORD__",  q(env["PI_PASSWORD"])),
    ("__SSH_PUBKEY__",   q(key.strip())),
)
text = pathlib.Path(tmpl).read_text(encoding="utf-8")
for placeholder, value in vals:
    if placeholder not in text:
        sys.exit(f"template missing {placeholder}")
    text = text.replace(placeholder, value)
# The extra-networks block goes in raw, with no quote escaping: it lands inside a
# double-quoted bash string, so make-firstrun.sh has already refused any value
# that could not survive one.
if "__WIFI_EXTRA_PROFILES__" not in text:
    sys.exit("template missing __WIFI_EXTRA_PROFILES__")
text = text.replace("__WIFI_EXTRA_PROFILES__", env.get("WIFI_EXTRA_RENDERED", ""))
# Check where it landed, not just that it did.  The block is multi-line DATA, so a
# second `__WIFI_EXTRA_PROFILES__` anywhere else (a comment header, say) leaves bare
# `id|ssid|psk|priority` lines in the script: bash treats those as a pipeline of
# commands, and firstrun.sh runs under `set +e`, so the box would boot with two
# "command not found" lines and nobody would notice.  Same for a lost block.
start = text.find('WIFI_PROFILES="')
if start < 0:
    sys.exit("rendered firstrun.sh has no WIFI_PROFILES block")
start += len('WIFI_PROFILES="')
end = text.find('"', start)
if end < 0:
    sys.exit("WIFI_PROFILES is not closed on the rendered script")
entries = [ln for ln in text[start:end].split("\n") if ln.strip()]
extra = [ln for ln in env.get("WIFI_EXTRA_RENDERED", "").splitlines() if ln.strip()]
for entry in entries:
    if len(entry.split("|")) != 4:
        sys.exit("a Wi-Fi line is not id|ssid|password|priority — a '|' in an SSID "
                 "or password cannot be rendered into firstrun.sh")
if len(entries) != 3 + len(extra):
    sys.exit(f"WIFI_PROFILES holds {len(entries)} networks, expected {3 + len(extra)}")
for line in extra:
    if text.count(line) != 1:
        sys.exit(f"the extra network {line.split('|')[0]!r} appears "
                 f"{text.count(line)} times in the render, not once inside WIFI_PROFILES")
# hard-guarantee Unix LF line endings
text = text.replace("\r\n", "\n").replace("\r", "\n")
# NB: Path.write_text(newline=) needs Python 3.10+; macOS ships 3.9.
with open(out, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(text)
print(f"rendered {out} ({len(text)} bytes)")
PY

chmod 700 "$OUT"

# Syntax check (the rendered script targets bash on Debian — same grammar)
bash -n "$OUT" || die "rendered firstrun.sh has a syntax error"

# CRLF guard
if grep -qU $'' "$OUT"; then die "rendered firstrun.sh contains CR characters"; fi

# Every placeholder must be gone
if grep -q '__WIFI\|__SSH\|__PI_PASSWORD' "$OUT"; then die "rendered firstrun.sh still contains placeholders"; fi

# Keep the password where the builder can find it later (never in git)
umask 077
printf '%s\n' "$PI_PASSWORD" >"$ROOT/staging/pi-password.txt"
chmod 600 "$ROOT/staging/pi-password.txt"
umask 022

printf '\033[1;32m✓\033[0m firstrun.sh rendered, +x, LF-only, syntax OK\n'
printf '   Country   : %s\n' "$WIFI_COUNTRY"
printf '   Pi login  : pi / (see staging/pi-password.txt — generated, not in git)\n'
printf '   SSH key   : %s\n' "$(cut -d' ' -f1-3 "$PUBKEY_FILE")"
printf '   Networks  :\n'
i=1
for ssid in "$WIFI_SSID" "$WIFI_SSID_2" "$WIFI_SSID_3"; do
  if [[ -z "$ssid" ]]; then continue; fi
  case "$i" in
    1) psk=${WIFI_PASSWORD:-}; prio=20 ;;
    2) psk=$WIFI_PSK_2;         prio=10 ;;
    *) psk=$WIFI_PSK_3;         prio=5  ;;
  esac
  printf '     %d. %-24s priority %-2s password %s\n' "$i" "\"$ssid\"" "$prio" \
    "$([[ -n "$psk" ]] && echo '(set, hidden)' || echo '(open network)')"
  i=$((i + 1))
done

if [[ -n "$WIFI_EXTRA_RENDERED" ]]; then
  while IFS='|' read -r ex_id ex_ssid ex_psk ex_prio; do
    if [[ -z "$ex_ssid" ]]; then continue; fi
    if [[ -n "$ex_psk" ]]; then pw='(set, hidden)'; else pw='(open network)'; fi
    printf '     %d. %-24s priority %-2s password %s (profile %s)\n' \
      "$i" "\"$ex_ssid\"" "$ex_prio" "$pw" "$ex_id"
    i=$((i + 1))
  done <<< "${WIFI_EXTRA_RENDERED#$'\n'}"
fi
