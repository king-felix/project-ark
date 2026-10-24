#!/usr/bin/env bash
# =============================================================================
#  PHASE 7 — the Ark's own Wi-Fi
#
#  Creates the "The-Ark-Offline" access point, makes every hostname resolve to
#  the portal while it is broadcasting, and installs the boot guard that falls
#  back to it whenever no home network is around.
#
#  NOTHING here switches the radio: you are most likely connected over that same
#  Wi-Fi right now.  Use "sudo ark-mode test 180" to try it deliberately.
# =============================================================================
set -Eeuo pipefail
source "${ARK_SRC:-/srv/ark/src}/lib/common.sh"
ark_require_root

ark_step "PHASE 7/7 — broadcast and failsafe"

ENV_FILE="$ARK_ETC/ark.env"
# shellcheck disable=SC1090
. "$ENV_FILE"

HOTSPOT_CONNECTION="${HOTSPOT_CONNECTION:-ark-hotspot}"
WIFI_DEV="${WIFI_DEV:-wlan0}"

ark_apt_install network-manager dnsmasq-base iw

[[ -d "/sys/class/net/$WIFI_DEV" ]] || ark_die "no $WIFI_DEV on this machine — is the Wi-Fi chip present?"
rfkill unblock wifi >/dev/null 2>&1 || true          # a soft block survives reboots
ark_ok "wifi radio present and unblocked ($WIFI_DEV)"

# ---- remember which network we are actually on ------------------------------
# The Pi was provisioned with the home Wi-Fi; its NetworkManager profile has a
# name nobody would guess, so store it for ark-mode to use.
home="$(nmcli -t -f NAME,TYPE connection show --active 2>/dev/null \
        | awk -F: '$2 ~ /wireless/ {print $1; exit}')"
if [[ -n "${home:-}" && "$home" != "${HOME_WIFI_CONNECTION:-}" ]]; then
  if [[ "${HOME_WIFI_CONNECTION:-home-wifi}" == "home-wifi" ]]; then
    sed -i "s|^HOME_WIFI_CONNECTION=.*|HOME_WIFI_CONNECTION=\"$home\"|" "$ENV_FILE"
    HOME_WIFI_CONNECTION="$home"
    ark_ok "home network recorded as \"$home\" in $ENV_FILE"
  else
    ark_info "keeping the home network already configured: $HOME_WIFI_CONNECTION"
  fi
fi

# ---- the access point -------------------------------------------------------
if ! nmcli -t -f NAME connection show 2>/dev/null | grep -Fxq "$HOTSPOT_CONNECTION"; then
  ark_info "creating the hotspot profile \"$HOTSPOT_CONNECTION\""
  nmcli connection add type wifi ifname "$WIFI_DEV" con-name "$HOTSPOT_CONNECTION" \
        ssid "$HOTSPOT_SSID" >/dev/null
fi

# Open network (no password) on purpose: the whole point is that a stranger with
# a dead phone can read the library.  autoconnect stays off — ark-mode decides.
nmcli connection modify "$HOTSPOT_CONNECTION" \
  802-11-wireless.mode      ap \
  802-11-wireless.ssid      "$HOTSPOT_SSID" \
  802-11-wireless.band      bg \
  802-11-wireless.channel   "$HOTSPOT_CHANNEL" \
  802-11-wireless.powersave 2 \
  ipv4.method               shared \
  ipv4.addresses            "$HOTSPOT_ADDRESS/24" \
  ipv4.never-default        yes \
  ipv6.method               ignore \
  connection.autoconnect    no
ark_ok "hotspot profile ready: ssid \"$HOTSPOT_SSID\", $HOTSPOT_ADDRESS/24, channel $HOTSPOT_CHANNEL"

# ---- every name points at the Ark while broadcasting ------------------------
# dnsmasq (started by NetworkManager in "shared" mode) reads this directory, so
# http://anything.at.all on a phone lands on the portal instead of an error.
ark_ensure_dir /etc/NetworkManager/dnsmasq-shared.d 755 root:root
ark_write /etc/NetworkManager/dnsmasq-shared.d/ark-wildcard.conf 644 root:root <<EOF
# Project ARK — while the hotspot is broadcast, resolve every name to the portal.
# Only read by the dnsmasq instance NetworkManager starts for "ipv4.method shared".
address=/#/$HOTSPOT_ADDRESS
EOF

# ---- scripts and the boot guard --------------------------------------------
for b in ark-mode ark-boot-guard; do
  ark_copy "$ARK_SRC/bin/$b" "/usr/local/bin/$b" 755 root:root
done
ark_install_unit ark-boot-guard.service
systemctl enable ark-boot-guard.service >/dev/null 2>&1 \
  || ark_warn "could not enable ark-boot-guard.service"
# Every unit must be enabled AND running, so start it now — but with the network
# step suppressed: "ark-mode auto" must not drop the SSH session we run over.
systemctl set-environment GUARD_SKIP_NETWORK=1
if systemctl start ark-boot-guard.service; then
  systemctl unset-environment GUARD_SKIP_NETWORK
  if [[ "$(systemctl is-active ark-boot-guard.service 2>/dev/null)" == "active" ]]; then
    ark_ok "boot guard enabled + started (network step suppressed for this run)"
  else
    ark_warn "boot guard ran but is not active — journalctl -u ark-boot-guard"
  fi
else
  systemctl unset-environment GUARD_SKIP_NETWORK
  ark_warn "boot guard did not start — journalctl -u ark-boot-guard"
fi

# ---- prove the pieces are in place (without changing the radio) -------------
mode_shown="$(nmcli -g 802-11-wireless.mode connection show "$HOTSPOT_CONNECTION" 2>/dev/null || true)"
method_shown="$(nmcli -g ipv4.method connection show "$HOTSPOT_CONNECTION" 2>/dev/null || true)"
printf '   profile %-12s mode=%s  ipv4=%s\n' "$HOTSPOT_CONNECTION" "${mode_shown:-?}" "${method_shown:-?}"
[[ "${mode_shown:-}" == "ap" ]] || ark_warn "hotspot profile is not in access-point mode"
[[ "${method_shown:-}" == "shared" ]] || ark_warn "hotspot profile is not sharing its connection"
[[ -f /etc/NetworkManager/dnsmasq-shared.d/ark-wildcard.conf ]] || ark_warn "wildcard DNS file is missing"
[[ "$(systemctl is-enabled ark-boot-guard.service 2>/dev/null || true)" == "enabled" ]] \
  || ark_warn "boot guard is not enabled"

ark_info "try it on purpose:  sudo ark-mode test 180   (SSH drops while it runs)"
ark_info "hotspot on/off:     sudo ark-mode hotspot | sudo ark-mode auto"
ark_info "what is happening:  sudo ark-mode status"
ark_ok "phase 7 complete — a cold boot now brings up either the home network or the hotspot"
