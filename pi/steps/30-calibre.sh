#!/usr/bin/env bash
# PHASE 3 — python environment + Calibre-Web ("Books")
set -Eeuo pipefail
source "${ARK_SRC:-/srv/ark/src}/lib/common.sh"
ark_require_root

ark_step "PHASE 3/7 — book library engine"

VENV="$ARK_ROOT/venv"
LIB="$ARK_ROOT/calibre-library"
CFG="$ARK_ROOT/calibre-config"
APP_DB="$CFG/app.db"
ADMIN_CRED="$ARK_ETC/calibre-admin"

# ---- 1. build toolchain (only needed if a wheel is missing for aarch64) ----
ark_info "ensuring a C toolchain exists for source-built wheels"
ark_apt_install build-essential python3-dev libffi-dev libssl-dev zlib1g-dev \
                libxml2-dev libxslt1-dev pkg-config \
  || ark_warn "some build packages were unavailable — pip may still succeed with wheels"

# ---- 2. virtualenv (python3.11/3.13 depending on the release) --------------
if [[ ! -x "$VENV/bin/python" ]]; then
  ark_info "creating virtualenv at $VENV"
  sudo -u "$ARK_PI_USER" /usr/bin/python3 -m venv "$VENV" \
    || ark_die "python3 -m venv failed (is python3-venv installed?)"
fi
chown -R "$ARK_PI_USER:$ARK_PI_GROUP" "$VENV"
sudo -u "$ARK_PI_USER" "$VENV/bin/python" -c 'import sys; print("   venv python:", sys.version.split()[0])'

ark_pi_pip install --quiet --upgrade pip setuptools wheel

# ---- 3. Calibre-Web + the API stack ----------------------------------------
ark_info "installing Calibre-Web and the API stack (a few minutes on a Pi 4)"
if ! ark_pi_pip install --prefer-binary \
      'calibreweb>=0.6.21' 'fastapi>=0.110' 'uvicorn>=0.29' \
      'python-multipart>=0.0.9' 'aiofiles>=23.2'; then
  ark_warn "binary-only install failed — retrying with source builds (slow, be patient)"
  ark_pi_pip install \
      'calibreweb>=0.6.21' 'fastapi>=0.110' 'uvicorn>=0.29' \
      'python-multipart>=0.0.9' 'aiofiles>=23.2' \
    || ark_die "pip install failed — see the log above"
fi

if ! sudo -u "$ARK_PI_USER" "$VENV/bin/python" -c \
     'import fastapi, uvicorn, multipart, aiofiles' 2>&1 | tail -3; then
  ark_die "portal backend libraries did not import cleanly"
fi

# Calibre-Web moved its application package: <= 0.6.2x exposed top-level `cps`,
# 0.6.27+ exposes `calibreweb.cps` (service runs `python -m calibreweb`). Detect
# whichever this wheel provides instead of hard-coding one of them.
CW_MODULE="$(sudo -u "$ARK_PI_USER" "$VENV/bin/python" - <<'PY'
import importlib.util as u
for name in ("calibreweb.cps", "cps"):
    if u.find_spec(name):
        print(name)
        break
PY
)"
[[ -n "$CW_MODULE" ]] || ark_die "Calibre-Web is installed but neither 'calibreweb.cps' nor 'cps' can be imported"
sudo -u "$ARK_PI_USER" "$VENV/bin/python" -c "import $CW_MODULE" \
  || ark_die "Calibre-Web module '$CW_MODULE' does not import cleanly (see above)"
CW_VER="$(sudo -u "$ARK_PI_USER" "$VENV/bin/python" -c "import $CW_MODULE.constants as c; print(c.STABLE_VERSION)" 2>/dev/null || echo unknown)"
ark_ok "Calibre-Web $CW_VER ($CW_MODULE), FastAPI + uvicorn ready"

# ---- 4. first start: let Calibre-Web build its own settings database -------
ark_install_unit ark-calibre.service
ark_ensure_dir "$CFG" 775 "$ARK_PI_USER:$ARK_PI_GROUP"

if [[ ! -f "$APP_DB" ]]; then
  ark_info "creating $APP_DB (first run of Calibre-Web, ~20 s)"
  systemctl stop ark-calibre.service >/dev/null 2>&1 || true
  set +e
  timeout 45 sudo -u "$ARK_PI_USER" env CACHE_DIRECTORY="$CFG/cache" \
      "$VENV/bin/python" -m calibreweb -p "$APP_DB" -i 127.0.0.1 \
      >"$ARK_LOG_DIR/calibre-bootstrap.log" 2>&1
  set -e
  # the bootstrap run is expected to be killed by `timeout`; that is success
  [[ -f "$APP_DB" ]] || ark_die "Calibre-Web did not create $APP_DB — see $ARK_LOG_DIR/calibre-bootstrap.log"
  ark_ok "settings database created"
else
  ark_info "settings database already exists"
fi
chown "$ARK_PI_USER:$ARK_PI_GROUP" "$APP_DB"

# ---- 5. a REAL calibre library ---------------------------------------------
# Calibre-Web cannot open a missing or bogus metadata.db, and any `sqlite3
# /path/metadata.db 'SELECT ...'` on a missing file happily CREATES a 0-byte
# one. That phantom file then makes every shelf request return HTTP 500
# ("'NoneType' object has no attribute 'query'"). Validate, then repair.
lib_ok=""
if [[ -s "$LIB/metadata.db" ]] && \
   [[ "$(sqlite3 -readonly "$LIB/metadata.db" \
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='books';" 2>/dev/null)" == "1" ]]; then
  lib_ok=1
fi
if [[ -z "$lib_ok" ]]; then
  ark_info "no usable calibre database in $LIB — creating an empty one"
  rm -f "$LIB/metadata.db"
  if ! command -v calibredb >/dev/null 2>&1; then
    ark_info "installing calibre (gives us calibredb + the genuine library schema)"
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends calibre \
      || ark_die "could not install calibre"
  fi
  sudo -u "$ARK_PI_USER" env QT_QPA_PLATFORM=offscreen calibre-debug -c \
    "from calibre.library import db; db('$LIB').close()" >/dev/null 2>&1 || true
  [[ -s "$LIB/metadata.db" ]] || ark_die "calibre did not create $LIB/metadata.db"
  chown "$ARK_PI_USER:$ARK_PI_GROUP" "$LIB/metadata.db"
  ark_ok "genuine calibre library created ($(sqlite3 -readonly "$LIB/metadata.db" 'SELECT COUNT(*) FROM books;') books)"
fi

# ---- 6. configure the library ---------------------------------------------
ark_info "writing library settings (anonymous browse + download + upload + reader)"
CALIBRE_DIR="$LIB"

sqlite3 "$APP_DB" <<SQL
UPDATE settings SET
    config_calibre_dir              = '$CALIBRE_DIR',
    config_port                     = 8083,
    config_external_port            = 8083,
    config_calibre_web_title        = 'THE ARK — Book Library',
    config_theme                    = 1,
    config_anonbrowse               = 1,
    config_uploading                = 1,
    config_public_reg               = 0,
    config_remote_login             = 0,
    config_kobo_sync                = 0,
    config_use_google_drive         = 0,
    config_books_per_page           = 30,
    config_random_books             = 4,
    config_authors_max              = 0,
    config_default_role             = 270,
    config_calibre_split            = 0,
    config_unicode_filename         = 0,
    config_allow_reverse_proxy_header_login = 0,
    config_log_level                = 30
WHERE id = 1;

-- Guest (anonymous) gets: download(2) + upload(4) + anonymous(32) + reader(256)
UPDATE user SET role = role | 2 | 4 | 32 | 256 WHERE (role & 32) != 0;
SQL

ROLE_AFTER="$(sqlite3 "$APP_DB" "SELECT role FROM user WHERE (role & 32) != 0 LIMIT 1;")"
if [[ -z "$ROLE_AFTER" ]]; then
  ark_warn "no anonymous user row found — creating a Guest account"
  sqlite3 "$APP_DB" "INSERT INTO user (name, email, role, password, locale, sidebar_view, default_language, denied_tags, allowed_tags, denied_column_value, allowed_column_value, kobo_only_shelves_sync) VALUES ('Guest','no@email',294,'','en',1,'all','','','','',0);"
  ROLE_AFTER="$(sqlite3 "$APP_DB" "SELECT role FROM user WHERE name='Guest' LIMIT 1;")"
fi
ark_ok "anonymous role bits = $ROLE_AFTER (expect 294 = download+upload+reader+anonymous)"

# admin password for the local importer (Calibre-Web ships admin/admin123)
if [[ ! -f "$ADMIN_CRED" ]]; then
  ark_write "$ADMIN_CRED" 600 root:root <<'CRED'
# Consumed by ark-import-books. If you change the password in the Book Library
# UI (admin -> edit user), update this line to match.
admin:admin123
CRED
fi

# ---- 6. serve it -----------------------------------------------------------
ark_service_up ark-calibre.service 40
CODE="$(ark_wait_http http://127.0.0.1:8083/ 60 200 302)"
if [[ "$CODE" == "200" || "$CODE" == "302" ]]; then
  ark_ok "Calibre-Web answering on 127.0.0.1:8083 (HTTP $CODE)"
else
  journalctl -u ark-calibre.service -n 30 --no-pager || true
  ark_die "Calibre-Web did not answer (last HTTP code $CODE)"
fi

# ---- 7. the importer -------------------------------------------------------
ark_copy "$ARK_SRC/bin/ark-import-books" /usr/local/bin/ark-import-books 755 root:root
ark_copy "$ARK_SRC/bin/ark-import-calibre-library" \
  /usr/local/bin/ark-import-calibre-library 755 root:root
chmod 755 /usr/local/bin/ark-import-books /usr/local/bin/ark-import-calibre-library
ark_ok "importer installed: /usr/local/bin/ark-import-books"

# Drop a book in the File Box (or in calibre-incoming) and it lands on the shelf
# by itself — no command to remember, which is what a non-technical user needs.
ark_install_unit ark-book-import.service
ark_install_unit ark-book-import.path
systemctl enable ark-book-import.path >/dev/null 2>&1 \
  || ark_warn "could not enable ark-book-import.path"
systemctl start ark-book-import.path >/dev/null 2>&1 || true
if [[ "$(systemctl is-active ark-book-import.path 2>/dev/null)" == "active" ]]; then
  ark_ok "new books are picked up automatically (watch: /srv/ark/drop/files)"
else
  ark_warn "auto-import watcher is not running — books still import via ark-import-books"
fi
ark_ok "whole-library import available: sudo ark-import-calibre-library <folder>"

BOOKS="$(sqlite3 -readonly "$LIB/metadata.db" 'SELECT COUNT(*) FROM books;' 2>/dev/null || echo 0)"
ark_ok "phase 3 complete — ${BOOKS} book(s) in the library"

