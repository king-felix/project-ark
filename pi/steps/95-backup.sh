#!/usr/bin/env bash
# =============================================================================
#  PHASE 8 — a copy that can be put back, and the page for when it can't
#
#  Installs both ends of stage 6: ark-backup (one dated folder holding the
#  records, the shared files and the import list, with a fingerprint for every
#  byte) and ark-restore (which proves a folder before it touches anything).
#  Also installs the printed recovery sheet — the only instructions that still
#  work when the box does not start.
#
#  Nothing here takes a backup or changes a record: it installs, proves the
#  tools can plan a run, and prints the two commands the owner needs.
# =============================================================================
set -Eeuo pipefail
source "${ARK_SRC:-/srv/ark/src}/lib/common.sh"
ark_require_root

ark_step "PHASE 8 — backups and the recovery sheet"

BACKUP_DIR="$ARK_ROOT/backup"
# 0700 pi:pi on purpose: a bundle holds the accounts table, not just messages.
ark_ensure_dir "$BACKUP_DIR" 700 "$ARK_PI_USER:$ARK_PI_GROUP"

# A bundle written by root (every `sudo ark-backup`) used to come out root:root,
# inside a folder the box's own pages open as pi — so "Latest copy" said none had
# ever been made while a copy sat right there.  The tool now hands each new bundle
# to the folder's owner; this hands over the ones that already exist, so running
# this phase again is the whole repair.
adopted=0
kept=0
while IFS= read -r -d '' bundle; do
  if chown -R "$ARK_PI_USER:$ARK_PI_GROUP" "$bundle" 2>/dev/null; then
    adopted=$((adopted + 1))
  else
    kept=$((kept + 1))
  fi
done < <(find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name 'ark-backup-*' -print0)
# The lock file, if a run has made one: same reason, and it is not fatal either.
chown "$ARK_PI_USER:$ARK_PI_GROUP" "$BACKUP_DIR/.lock" 2>/dev/null || true
if (( kept )); then
  ark_warn "$kept existing copy/copies could not be handed to $ARK_PI_USER, so the owner page will not see them"
fi
if (( adopted )); then
  ark_ok "handed $adopted existing copy/copies to $ARK_PI_USER so the owner page can read them"
fi

# ---- the two tools ----------------------------------------------------------
# ark-backup and ark-restore are thin: the layout, the manifest and the path
# rules live in ark_bundle.py, and both tools import it from their own folder.
# It ships next to them so the two can never disagree about what a bundle is.
for tool in ark_bundle.py ark-backup ark-restore; do
  ark_copy "$ARK_SRC/bin/$tool" "/usr/local/sbin/$tool" 755 root:root
done
ark_ok "installed ark-backup and ark-restore in /usr/local/sbin"

# ---- the page that works when the box does not ------------------------------
ark_copy "$ARK_SRC/RECOVERY-SHEET.txt" "$ARK_ROOT/RECOVERY-SHEET.txt" 644 "$ARK_PI_USER:$ARK_PI_GROUP"
# Also readable on a phone while the box still works: /recovery.txt
ark_ensure_dir "$ARK_ROOT/portal" 755 "$ARK_PI_USER:$ARK_PI_GROUP"
ark_copy "$ARK_SRC/RECOVERY-SHEET.txt" "$ARK_ROOT/portal/recovery.txt" 644 "$ARK_PI_USER:$ARK_PI_GROUP"
ark_ok "recovery sheet installed at $ARK_ROOT/RECOVERY-SHEET.txt and on /recovery.txt"

# ---- prove it plans before it writes ---------------------------------------
# A dry run reads the database and counts the shared files; it writes nothing.
# If it cannot even plan, the owner needs to know now, not on the bad day.
if plan="$(/usr/local/sbin/ark-backup --to "$BACKUP_DIR" --dry-run 2>&1)"; then
  ark_ok "ark-backup can plan a run: $(printf '%s\n' "$plan" | head -n 1 | sed 's/^Would write //')"
elif grep -q "nothing to back up yet" <<<"$plan"; then
  ark_warn "no records on the box yet, so there is nothing to copy — run this phase again later"
else
  ark_warn "ark-backup could not plan a run: $plan"
fi

# ---- what the owner is told -------------------------------------------------
ark_info "make a backup:  sudo ark-backup --to $BACKUP_DIR"
ark_info "see the copies: sudo ark-restore --list"
ark_info "put one back:   sudo ark-restore --from $BACKUP_DIR/ark-backup-<date>-<time>"
ark_info "prove a copy:   sudo ark-backup --verify $BACKUP_DIR/ark-backup-<date>-<time>"
ark_info "print the sheet: $ARK_ROOT/RECOVERY-SHEET.txt  (also http://ark.lan/recovery.txt)"
# No timer on purpose: a nightly backup that quietly fills the card the box
# reads from is a defect, not a feature.  Take one when something happened.
ark_ok "phase 8 complete — the box can now copy itself and be put back"
