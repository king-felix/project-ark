#!/usr/bin/env bash
# =============================================================================
#  PROJECT ARK — stop a household's own details reaching git.
#
#  A secret is not the only thing that leaks. A Wi-Fi network's NAME, the box's
#  address on somebody's home LAN and the workstation account in a path are all
#  identifying, and this tree is published. Paperwork therefore says
#  <HOUSEHOLD-SSID>, <HOUSEHOLD-PSK>, <OTHER-NETWORK-SSID>, <OTHER-NETWORK-PSK>,
#  <PROFILE-ID>, <HOME-IP>, <CABLE-IP> and /Users/<ACCOUNT> — see the legend at
#  the top of docs/ADMIN.md for what to put in each one.
#
#  The check is SHAPE-based on purpose: a list of the values would have to live
#  in git, which is the very thing this prevents. It catches the four shapes
#  that have actually leaked here:
#    1. a workstation account in a path        /Users/<name>/...
#    2. a private address that is not the Ark  everything except the hotspot's
#       10.42.0.x, the gateway 192.168.1.1, loopback and the documentation
#       ranges 192.0.2.x / 198.51.100.x / 203.0.113.x
#    3. a passphrase written as a literal      psk=…  passphrase=…  PI_PASSWORD=…
#    4. a network name written as a literal    WIFI_SSID=<name>  AP_SSID=<name>
#
#  Usage:
#    bash scripts/ark-secrets-check.sh                  # every tracked file
#    bash scripts/ark-secrets-check.sh --staged         # what a commit would add
#    bash scripts/ark-secrets-check.sh --install-hook   # .git/hooks/pre-commit
#    bash scripts/ark-secrets-check.sh PATH...          # one file or folder
#    bash scripts/ark-secrets-check.sh --quiet          # summary only
#
#  Exit 0 clean, 1 something found, 2 usage. It never prints the value it found:
#  a guard that quotes a secret into a terminal log has leaked it again.
# =============================================================================
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGED=0
QUIET=0
INSTALL=0
TARGETS=()

say()  { (( QUIET )) || printf '\033[1;36m→\033[0m %s\n' "$*"; }
ok()   { (( QUIET )) || printf '\033[1;32m✓\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; }

# The usage text is read out of this file's own header, from the `#  Usage:`
# marker to the closing `# ====` rule. A hardcoded line range silently cut the
# text in half the first time the header gained a line, and "stop at the next
# bare #" stopped at the blank comment line *before* the exit codes — the one
# sentence a person reads to know what a non-zero answer means.
usage() {
  awk '/^#  Usage:/{on = 1} on && /^# ====/{exit} on' "${BASH_SOURCE[0]}" \
    | sed 's/^#[[:space:]]\{0,2\}//'
}

while (( $# )); do
  case "$1" in
    --staged)       STAGED=1 ;;
    --quiet|-q)     QUIET=1 ;;
    --install-hook) INSTALL=1 ;;
    -h|--help)      usage; exit 0 ;;
    --)             shift; TARGETS+=("$@"); break ;;
    -*)             fail "unknown option: $1"; usage >&2; exit 2 ;;
    *)              TARGETS+=("$1") ;;
  esac
  shift
done

git_repo() { git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; }

if (( INSTALL )); then
  git_repo || { fail "$ROOT is not a git repository"; exit 2; }
  HOOK_DIR="$(git -C "$ROOT" rev-parse --git-path hooks)"
  HOOK="$HOOK_DIR/pre-commit"
  mkdir -p "$HOOK_DIR"
  cat >"$HOOK" <<EOF
#!/bin/sh
# PROJECT ARK — written by scripts/ark-secrets-check.sh --install-hook
# Refuses a commit that carries a household's own details. Delete this file to
# stop the check, or ask the script to write it again.
exec bash "$ROOT/scripts/ark-secrets-check.sh" --staged --quiet
EOF
  chmod +x "$HOOK"
  ok "installed $HOOK"
  exit 0
fi

# ---------------------------------------------------------------------------
#  What to read
# ---------------------------------------------------------------------------
list_files() {
  local t
  if (( ${#TARGETS[@]} )); then
    for t in "${TARGETS[@]}"; do
      if [[ -d "$t" ]]; then
        find "$t" -type f ! -path '*/.git/*'
      else
        # Every named path was proved to exist before reading began — a check
        # that quietly reads nothing and exits 0 is worse than no check at all.
        printf '%s\n' "$t"
      fi
    done
  elif git_repo; then
    git -C "$ROOT" ls-files -z | tr '\0' '\n' | sed "s|^|$ROOT/|"
  else
    find "$ROOT" -type f ! -path '*/.git/*'
  fi
}


# ---------------------------------------------------------------------------
#  The four shapes. `-I` skips binaries (the vendored player is megabytes of
#  bytes that would match anything), and every grep may answer "nothing"
#  without tripping `set -e`.
# ---------------------------------------------------------------------------
# The value must *look* like a value to be reported: it has to begin with a
# letter or a digit. That is what keeps `ssid="$ssid"`, `ssid=<name>`, a
# `printf 'ssid=%s'` format, a `--ssid=*)` case arm and a backticked mention in
# prose out of the report while a real `WIFI_SSID=` assignment walks straight in.
R_PATH='/Users/[A-Za-z0-9._+-][A-Za-z0-9._+-]*'
R_PASS=$'([Pp][Ss][Kk]|[Pp]assphrase|PI_PASSWORD|WIFI_PSK)[[:space:]]*=[[:space:]]*[[:alnum:]][^[:space:]"\'$<]{7,}'
R_SSID=$'(WIFI_SSID|HOME_WIFI_SSID|AP_SSID|ssid)[0-9_]*[[:space:]]*=[[:space:]]*[[:alnum:]]'

FOUND=0
STRIP="$ROOT/"
report() {  # report FILE LINE what
  FOUND=$((FOUND + 1))
  (( QUIET )) || printf '  \033[1;31m%s:%s\033[0m  %s\n' "${1#"$STRIP"}" "$2" "$3"
}

# A line may opt itself out, loudly and in writing: `# ark-secrets-ok: <why>`.
# A fixture that writes a made-up network into a made-up request file has the
# exact shape of the thing this script exists to catch, so the honest answer is
# a marked line a reviewer reads — never a looser pattern that lets a real name
# through everywhere. The marker is checked on the line's own text, so it can
# never excuse a neighbouring line.
ALLOW_MARK='ark-secrets-ok'
report_hit() {  # report_hit FILE "LINE:TEXT" what
  [[ ${2#*:} == *"$ALLOW_MARK"* ]] && return 0
  report "$1" "${2%%:*}" "$3"
}

ip_is_private() {
  local ip="$1"
  [[ $ip =~ ^10\. ]] && return 0
  [[ $ip =~ ^192\.168\. ]] && return 0
  [[ $ip =~ ^169\.254\. ]] && return 0
  [[ $ip =~ ^172\.(1[6-9]|2[0-9]|3[01])\. ]] && return 0
  return 1
}

ip_is_allowed() {
  local ip="$1"
  [[ $ip =~ ^127\. ]] && return 0               # loopback
  [[ $ip == 0.0.0.0 ]] && return 0              # "every address"
  [[ $ip =~ ^255\. ]] && return 0               # broadcast
  [[ $ip =~ ^10\.42\.0\. ]] && return 0         # the Ark's own hotspot
  [[ $ip == 192.168.1.1 ]] && return 0          # the gateway most home routers answer on
  [[ $ip =~ ^192\.0\.2\. ]] && return 0         # RFC 5737 documentation ranges
  [[ $ip =~ ^198\.51\.100\. ]] && return 0
  [[ $ip =~ ^203\.0\.113\. ]] && return 0
  return 1
}

check_addresses() {  # check_addresses FILE
  local f="$1" hit line ln ip
  while IFS= read -r hit; do
    ln="${hit%%:*}"
    line="${hit#*:}"
    [[ $line == *"$ALLOW_MARK"* ]] && continue
    while IFS= read -r ip; do
      [[ -n $ip ]] || continue
      if ip_is_private "$ip" && ! ip_is_allowed "$ip"; then
        report "$f" "$ln" "a private address — write <HOME-IP> / <CABLE-IP> instead (legend: docs/ADMIN.md)"
      fi
    done < <(printf '%s\n' "$line" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' || true)
  done < <(grep -nIE '([0-9]{1,3}\.){3}[0-9]{1,3}' "$f" 2>/dev/null || true)
}

check_file() {  # check_file FILE
  local f="$1" hit
  [[ -f $f && -r $f ]] || return 0
  # A binary has no line worth reading. `grep -I` answers "no match" for any
  # file holding a NUL byte, which is how the vendored player is skipped.
  grep -qI . "$f" 2>/dev/null || return 0

  while IFS= read -r hit; do
    report_hit "$f" "$hit" "a workstation account in a path — write /Users/<ACCOUNT>"
  done < <(grep -nIE "$R_PATH" "$f" 2>/dev/null || true)

  while IFS= read -r hit; do
    report_hit "$f" "$hit" "a passphrase written as a literal — it belongs in staging/wifi.env (0600, never committed)"
  done < <(grep -nIE "$R_PASS" "$f" 2>/dev/null || true)

  while IFS= read -r hit; do
    report_hit "$f" "$hit" "a network name written as a literal — write <HOUSEHOLD-SSID> / <OTHER-NETWORK-SSID>"
  done < <(grep -nIE "$R_SSID" "$f" 2>/dev/null || true)

  check_addresses "$f"
}

# ---------------------------------------------------------------------------
#  Read them
# ---------------------------------------------------------------------------
CHECKED=0
FILES=()
# A commit carries the INDEX, not the working copy. Reading the disk for a staged
# path was wrong in the one direction that matters: stage a file with a secret in
# it, then tidy the working copy, and the check passed while the commit carried it.
STAGED_TMP=""
cleanup() { [[ -n $STAGED_TMP && -d $STAGED_TMP ]] && rm -rf "$STAGED_TMP"; return 0; }
trap cleanup EXIT

# Every way of finding nothing to read is answered HERE, in the main shell: an
# `exit` inside `list_files` only leaves its subshell (it feeds a process
# substitution), so a typo used to print "no such file" — and `--staged` outside a
# repository used to print "needs a git repository" — and then exit 0 on an empty
# reading. A check that quietly reads nothing is worse than no check at all.
if (( ${#TARGETS[@]} )); then
  for target in "${TARGETS[@]}"; do
    [[ -e $target ]] || { fail "no such file: $target"; exit 2; }
  done
  say "reading ${#TARGETS[@]} named path(s)"
  while IFS= read -r f; do
    if [[ -n $f ]]; then FILES+=("$f"); fi
  done < <(list_files | sort -u)
elif (( STAGED )); then
  git_repo || { fail "--staged needs a git repository"; exit 2; }
  say "reading what a commit would add in $ROOT"
  STAGED_TMP="$(mktemp -d "${TMPDIR:-/tmp}/ark-staged-XXXXXX")"
  STRIP="$STAGED_TMP/"
  # A deletion has no bytes to read; a rename's new side is included. The staged
  # blob is written out under its own path, so the line numbers reported are the
  # file's own and nothing about the report changes shape.
  while IFS= read -r rel; do
    if [[ -z $rel ]]; then continue; fi
    mkdir -p "$STAGED_TMP/$(dirname "$rel")"
    git -C "$ROOT" show ":$rel" >"$STAGED_TMP/$rel" 2>/dev/null || continue
    FILES+=("$STAGED_TMP/$rel")
  done < <(git -C "$ROOT" diff --cached --name-only --diff-filter=ACMR -z | tr '\0' '\n')
else
  say "reading the tracked tree in $ROOT"
  while IFS= read -r f; do
    if [[ -n $f ]]; then FILES+=("$f"); fi
  done < <(list_files | sort -u)
fi

if (( ${#FILES[@]} )); then
  for f in "${FILES[@]}"; do
    CHECKED=$((CHECKED + 1))
    check_file "$f"
  done
fi

if (( FOUND )); then
  fail "$FOUND place(s) across $CHECKED file(s) carry a household's own details"
  printf '  Nothing was changed. Replace each with the placeholder in the legend at\n' >&2
  printf '  the top of docs/ADMIN.md, then run this again.\n' >&2
  exit 1
fi

if (( CHECKED )); then
  ok "clean: $CHECKED file(s) read, no household detail found"
else
  ok "clean: there was nothing to read"
fi

