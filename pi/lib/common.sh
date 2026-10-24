#!/usr/bin/env bash
# =============================================================================
#  PROJECT ARK — shared shell library (sourced, never executed directly)
#  Every Pi-side script sources this; it is the single source of truth for
#  paths, logging and idempotent file/service helpers.
# =============================================================================
set -Eeuo pipefail

ARK_ROOT="${ARK_ROOT:-/srv/ark}"
ARK_SRC="${ARK_SRC:-/srv/ark/src}"
ARK_ETC="${ARK_ETC:-/etc/ark}"
ARK_LOG_DIR="$ARK_ROOT/logs"
ARK_PI_USER="${ARK_PI_USER:-pi}"
ARK_PI_GROUP="${ARK_PI_GROUP:-pi}"

# ---- logging ----------------------------------------------------------------
ark_ts()   { date -Is; }
ark_step() { printf '\n\033[1;35m━━━ %s ━━━\033[0m\n' "$*"; }
ark_ok()   { printf '\033[1;32m ✓\033[0m %s\n' "$*"; }
ark_info() { printf '\033[1;36m →\033[0m %s\n' "$*"; }
ark_warn() { printf '\033[1;33m ! %s\033[0m\n' "$*"; }
ark_die()  { printf '\033[1;31m ✗ %s\033[0m\n' "$*" >&2; exit 1; }

ark_require_root() {
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || ark_die "this script must run as root (sudo)"
}

# ---- apt --------------------------------------------------------------------
ark_apt_install() {
  local missing=() p
  for p in "$@"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
  if (( ${#missing[@]} == 0 )); then
    ark_info "already installed: $*"
    return 0
  fi
  ark_info "installing: ${missing[*]}"
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
      -o Dpkg::Options::=--force-confdef \
      -o Dpkg::Options::=--force-confold \
      "${missing[@]}"
}

ark_apt_update() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
}

# ---- filesystem -------------------------------------------------------------
ark_ensure_dir() { # <dir> [mode] [owner:group]
  local d=$1 m=${2:-755} o=${3:-root:root}
  install -d -m "$m" "$d"
  chown "$o" "$d"
}

# ark_write <path> [mode] [owner:group]   (content on stdin)
# Rewrites only when the content differs -> idempotent, avoids needless restarts.
ark_write() {
  local path=$1 mode=${2:-644} owner=${3:-root:root} tmp
  tmp="$(mktemp)"
  cat > "$tmp"
  if [[ -f "$path" ]] && cmp -s "$tmp" "$path"; then
    rm -f "$tmp"
    return 0
  fi
  install -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$tmp" "$path"
  rm -f "$tmp"
  ark_info "wrote $path"
}

# ark_copy <src> <dst> [mode] [owner:group]
ark_copy() {
  local src=$1 dst=$2 mode=${3:-644} owner=${4:-root:root}
  [[ -f "$src" ]] || ark_die "missing source file: $src"
  if [[ -f "$dst" ]] && cmp -s "$src" "$dst"; then return 0; fi
  install -D -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$src" "$dst"
  ark_info "installed $dst"
}

# ---- systemd ----------------------------------------------------------------
ark_install_unit() { # <unit-name.service>
  local u=$1
  [[ -f "$ARK_SRC/systemd/$u" ]] || ark_die "missing unit template: $ARK_SRC/systemd/$u"
  ark_write "/etc/systemd/system/$u" 644 root:root < "$ARK_SRC/systemd/$u"
  systemctl daemon-reload
}

# Enable a unit and (re)start it so new config takes effect.
ark_service_up() { # <unit> [timeout-seconds]
  local u=$1 t=${2:-15} i=0
  systemctl enable "$u" >/dev/null 2>&1 || ark_die "cannot enable $u"
  systemctl restart "$u" || ark_warn "restart of $u returned non-zero (check: journalctl -u $u)"
  while (( i < t * 2 )); do
    systemctl is-active --quiet "$u" && { ark_ok "$u active + enabled"; return 0; }
    sleep 0.5; (( i += 1 )) || true
  done
  journalctl -u "$u" -n 25 --no-pager 2>/dev/null || true
  ark_die "$u failed to become active"
}

ark_service_stop() { systemctl stop "$1" >/dev/null 2>&1 || true; }

# ---- http / disk ------------------------------------------------------------
ark_http_code() {
  local c
  # curl prints 000 itself when it cannot connect; without the || true the old
  # "|| echo 000" appended a second one ("000000") and confused callers.
  c="$(curl -s -o /dev/null -m 8 -w '%{http_code}' "$1" 2>/dev/null)" || true
  printf '%s' "${c:-000}"
}

ark_wait_http() { # <url> <timeout-s> <accepted-codes...>
  local url=$1 t=${2:-120} i=0 code
  local accepted=" ${*:3} "
  (( ${#accepted} > 2 )) || accepted=' 200 302 '
  while (( i < t )); do
    code=$(ark_http_code "$url")
    [[ "$accepted" == *" $code "* ]] && { printf '%s' "$code"; return 0; }
    sleep 2; (( i += 2 )) || true
  done
  printf '%s' "${code:-000}"
  return 1
}

ark_free_bytes() { # bytes available on the filesystem holding <path>
  local b
  b="$(df -B1 --output=avail "$1" 2>/dev/null | tail -1 | tr -dc '0-9')"
  # BSD/macOS df has no --output; -k gives 1 KiB blocks
  [[ -n "$b" ]] || b="$(df -k "$1" 2>/dev/null | tail -1 | awk 'NF>=4 {print $4*1024}')"
  printf '%s' "${b:-0}"
}
ark_gib() { awk -v b="${1:-0}" 'BEGIN{printf "%.1f", b/1073741824}'; }

# ---- misc -------------------------------------------------------------------
ark_pi_pip() { sudo -u "$ARK_PI_USER" "$ARK_ROOT/venv/bin/pip" "$@"; }

ark_have() { command -v "$1" >/dev/null 2>&1; }
