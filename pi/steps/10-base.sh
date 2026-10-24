#!/usr/bin/env bash
# PHASE 1 — hardware audit, rootfs growth, base packages, kiwix toolchain
set -Eeuo pipefail
source "${ARK_SRC:-/srv/ark/src}/lib/common.sh"
ark_require_root

ark_step "PHASE 1/7 — hardware audit"

MODEL="$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
ARCH="$(uname -m)"
MEM_MB="$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo)"
OS_NAME="$( . /etc/os-release; printf '%s' "${PRETTY_NAME:-unknown}" )"

printf '   model      : %s\n' "$MODEL"
printf '   arch       : %s\n' "$ARCH"
printf '   kernel     : %s\n' "$(uname -r)"
printf '   os         : %s\n' "$OS_NAME"
printf '   memory     : %s MiB\n' "$MEM_MB"

if [[ "$ARCH" != "aarch64" ]]; then
  ark_die "expected a 64-bit ARM system (aarch64), found $ARCH — wrong image flashed?"
fi
if (( MEM_MB < 3500 )); then
  ark_warn "only ${MEM_MB} MiB RAM — designed for the 8 GB model, continuing anyway"
fi

# ---- root filesystem must use the whole card -------------------------------
ROOT_SRC="$(findmnt -no SOURCE / 2>/dev/null || echo '')"
PK_NAME=""
if [[ -n "$ROOT_SRC" ]]; then
  PK_NAME="$(lsblk -no PKNAME "$ROOT_SRC" 2>/dev/null | head -1 || true)"
fi
CARD_BYTES=0
if [[ -n "$PK_NAME" ]]; then
  CARD_BYTES="$(lsblk -bndo SIZE "/dev/$PK_NAME" 2>/dev/null | head -1 | tr -dc '0-9' || true)"
fi
CARD_BYTES="${CARD_BYTES:-0}"
ROOT_BYTES="$(df -B1 --output=size / | tail -1 | tr -dc '0-9')"
printf '   card       : %s GiB\n' "$(ark_gib "$CARD_BYTES")"
printf '   rootfs     : %s GiB\n' "$(ark_gib "$ROOT_BYTES")"

if (( CARD_BYTES > 0 )); then
  if (( ROOT_BYTES * 10 < CARD_BYTES * 9 )); then
    ark_info "root filesystem does not use the full card — expanding (no reboot needed)"
    if ark_have raspi-config; then
      raspi-config --expand-rootfs >/dev/null 2>&1 || true
      raspi-config nonint do_expand_rootfs >/dev/null 2>&1 || true
    fi
    ROOT_BYTES="$(df -B1 --output=size / | tail -1 | tr -dc '0-9')"
    printf '   rootfs now : %s GiB\n' "$(ark_gib "$ROOT_BYTES")"
  fi
  if (( CARD_BYTES < 100000000000 )); then
    ark_die "card is only $(ark_gib "$CARD_BYTES") GiB — the offline library will not fit"
  fi
fi

# ---- apt -------------------------------------------------------------------
ark_step "PHASE 1/7 — packages"
ark_apt_update

ark_info "applying security upgrades (best effort)"
DEBIAN_FRONTEND=noninteractive apt-get -y \
    -o Dpkg::Options::=--force-confdef \
    -o Dpkg::Options::=--force-confold \
    upgrade || ark_warn "apt upgrade reported a problem — continuing (log above)"

# Base runtime.  `file` is installed on purpose: it drags in the correct
# libmagic1 for the release, which Calibre-Web needs to sniff uploads.
ark_apt_install \
  nginx curl wget ca-certificates jq sqlite3 unzip rsync tmux \
  python3 python3-pip python3-venv python3-dev \
  file avahi-daemon avahi-utils libnss-mdns \
  dnsmasq-base network-manager wpasupplicant rfkill \
  exfatprogs dosfstools

# ImageMagick (required by Calibre-Web for cover processing).  The shared-library
# package name changed between Bookworm and Trixie, so try each in turn.
MAGICK_OK=0
for m in libmagickwand-6.q16-6 libmagickwand-7.q16-10 libmagickwand-dev imagemagick; do
  if DEBIAN_FRONTEND=noninteractive apt-get install -y "$m" >/dev/null 2>&1; then
    ark_ok "image library installed: $m"
    MAGICK_OK=1
    break
  fi
done
if (( MAGICK_OK == 0 )); then
  ark_warn "no ImageMagick found — Calibre-Web will still serve books but cannot resize covers"
fi

# dnsmasq must NEVER run as a standalone unit: NetworkManager owns it in
# hotspot ("ipv4.method shared") mode and a second instance would fight for :53.
if systemctl list-unit-files 2>/dev/null | grep -q '^dnsmasq.service'; then
  systemctl disable --now dnsmasq.service >/dev/null 2>&1 || true
  ark_ok "standalone dnsmasq.service disabled (NetworkManager manages DNS)"
fi

systemctl enable NetworkManager.service >/dev/null 2>&1 || true
systemctl enable avahi-daemon.service >/dev/null 2>&1 || true
systemctl enable ssh.service >/dev/null 2>&1 || ark_warn "could not enable ssh.service"

# ---- kiwix toolchain -------------------------------------------------------
ark_step "PHASE 1/7 — kiwix toolchain"
KIWIX_VER="$(kiwix-serve --version 2>/dev/null | tr -dc '0-9.\n' | head -1 || true)"
ark_info "kiwix-serve from apt: ${KIWIX_VER:-absent}"

if [[ -z "$KIWIX_VER" ]] || [[ "$(printf '%s\n3.0.0\n' "$KIWIX_VER" | sort -V | head -1)" != "3.0.0" ]]; then
  ark_warn "apt kiwix-tools is missing or too old — installing the official aarch64 build"
  TMPD="$(mktemp -d)"
  if curl -fL --retry 3 --max-time 600 \
        -o "$TMPD/kiwix-tools.tar.gz" \
        https://download.kiwix.org/release/kiwix-tools/kiwix-tools_linux-aarch64.tar.gz; then
    tar -xzf "$TMPD/kiwix-tools.tar.gz" -C "$TMPD"
    install -d /usr/local/bin
    find "$TMPD" -maxdepth 2 -type f -name 'kiwix-*' -perm -u+x \
      -exec install -m 755 {} /usr/local/bin/ \;
    rm -rf "$TMPD"
    hash -r
    KIWIX_VER="$(kiwix-serve --version 2>/dev/null | tr -dc '0-9.\n' | head -1 || true)"
    ark_ok "kiwix-serve ${KIWIX_VER:-?} installed to /usr/local/bin"
  else
    rm -rf "$TMPD"
    ark_warn "could not download kiwix-tools — the offline library will be unavailable until it is installed"
  fi
else
  ark_ok "kiwix-serve $KIWIX_VER"
fi

for t in kiwix-serve kiwix-manage kiwix-search wget nginx sqlite3 jq curl; do
  if ark_have "$t"; then
    printf '   \033[1;32m✓\033[0m %-14s %s\n' "$t" "$(command -v "$t")"
  else
    printf '   \033[1;33m!\033[0m %-14s MISSING\n' "$t"
  fi
done

ark_ok "phase 1 complete"
