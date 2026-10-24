#!/bin/bash
# =============================================================================
#  PROJECT ARK — one-shot repair: give the `pi` account a real shell.
#  Runs ONCE at boot via systemd.run= (see scripts/fix-pi-shell.sh).
#  Needed because current Pi OS images ship `pi` as a nologin account, so SSH
#  authenticates with the key and then refuses with
#  "This account is currently not available."
# =============================================================================
exec >>/var/log/ark-pifix.log 2>&1
set +e
echo "===== ARK pifix start $(date -u +%Y-%m-%dT%H:%M:%SZ) ====="
echo "before: $(getent passwd pi)"

usermod -s /bin/bash pi
[ -d /home/pi ] || { mkdir -p /home/pi; cp -a /etc/skel/. /home/pi/ 2>/dev/null; }
chown pi /home/pi 2>/dev/null
# The password was written to /etc/ark-pipass by firstrun.sh. If the file is
# missing (old card, or firstrun never ran), do NOT invent one — key auth and
# passwordless sudo already work, and a guessed password is worse than none.
if [ -r /etc/ark-pipass ]; then
  ark_pass="$(cat /etc/ark-pipass)"
  if [ -n "$ark_pass" ]; then
    echo "pi:$ark_pass" | chpasswd
    echo "password: set from /etc/ark-pipass"
  fi
else
  echo "password: /etc/ark-pipass missing — left unchanged (SSH key still works)"
fi

for g in sudo adm dialout cdrom audio video plugdev games users input netdev gpio i2c spi; do
  getent group "$g" >/dev/null 2>&1 && usermod -aG "$g" pi 2>/dev/null
done
printf 'pi ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/010_pi-nopasswd
chmod 440 /etc/sudoers.d/010_pi-nopasswd
chown root:root /etc/sudoers.d/010_pi-nopasswd
visudo -cf /etc/sudoers.d/010_pi-nopasswd >/dev/null 2>&1 || rm -f /etc/sudoers.d/010_pi-nopasswd

# hostname + hosts entry (firstrun tried this, but be certain)
echo ark > /etc/hostname
hostnamectl set-hostname ark 2>/dev/null
if grep -q '^127\.0\.1\.1' /etc/hosts; then
  sed -i -E 's/^127\.0\.1\.1.*/127.0.1.1\tark/' /etc/hosts
else
  printf '127.0.1.1\tark\n' >> /etc/hosts
fi
grep -qE '(^|[[:space:]])ark([[:space:]]|$)' /etc/hosts || printf '127.0.0.1\tark\n' >> /etc/hosts

systemctl enable ssh.service >/dev/null 2>&1
systemctl start  ssh.service >/dev/null 2>&1
rfkill unblock wifi >/dev/null 2>&1

# remove self + the command-line tokens so this never runs twice
for f in /boot/firmware/pifix.sh /boot/pifix.sh; do rm -f "$f" 2>/dev/null; done
for cmd in /boot/cmdline.txt /boot/firmware/cmdline.txt; do
  [ -f "$cmd" ] || continue
  sed -i -E 's/[[:space:]]*systemd\.run=[^[:space:]]*//g; s/[[:space:]]*systemd\.run_success_action=[^[:space:]]*//g; s/[[:space:]]*systemd\.unit=kernel-command-line\.target//g' "$cmd"
  tr -s ' ' < "$cmd" | sed 's/[[:space:]]*$//' > "$cmd.tmp" && printf '\n' >> "$cmd.tmp" && mv -f "$cmd.tmp" "$cmd"
done

echo "after:  $(getent passwd pi)"
sync
echo "===== ARK pifix COMPLETE ====="
exit 0
