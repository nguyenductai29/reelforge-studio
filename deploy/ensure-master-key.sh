#!/usr/bin/env bash
# Make sure this server has a usable master key before anything restarts. Called by deploy.sh; safe to run
# by hand. The key is never printed.
#
#   1. A usable key already (the key file, or a legacy key the services still load): continue.
#   2. No key file, but the legacy key is available (REELFORGE_TOKEN_ENCRYPTION_KEY in the environment or in
#      /etc/reelforge/runtime.env): copy that same key into the key file. Nothing becomes unreadable.
#   3. Otherwise check PostgreSQL for encrypted data (OAuth tokens, payment and provider secrets):
#      - encrypted data exists: STOP. A new key would make it unreadable; restore the old key file.
#      - the database cannot be read: STOP. Never generate a key without knowing.
#      - none (a new installation): python -m app.master_key init.
#   4. Verify with python -m app.master_key status.
#   5. Continue (exit 0). Any STOP exits 1.
#
# Environment: PYTHON (default python), REELFORGE_MASTER_KEY_FILE (default /etc/reelforge/master.key),
# REELFORGE_LEGACY_ENV_FILE (default /etc/reelforge/runtime.env).
set -euo pipefail

PYTHON="${PYTHON:-python}"
KEY_FILE="${REELFORGE_MASTER_KEY_FILE:-/etc/reelforge/master.key}"
LEGACY_FILE="${REELFORGE_LEGACY_ENV_FILE:-/etc/reelforge/runtime.env}"

# The services read the optional legacy runtime file through systemd (EnvironmentFile=-). Read it the same way,
# so a key that still lives there is found, and never replaced by a new one.
if [ -z "${REELFORGE_ENV_FILE:-}" ] && [ -r "$LEGACY_FILE" ]; then
  export REELFORGE_ENV_FILE="$LEGACY_FILE"
fi

stop() {
  echo >&2
  printf 'STOP: %s\n' "$@" >&2
  exit 1
}

# Write the key file with `python -m app.master_key init` (it copies a legacy key when one is set, generates one
# otherwise, and never overwrites). /etc/reelforge belongs to root: the key is then created as this (service)
# account in a private staging directory and installed with sudo, owned by this account, readable by it only.
# The directory the key goes in, or its nearest existing ancestor: can this account create the file there?
can_create() {
  local dir="$1"
  while [ ! -e "$dir" ]; do dir="$(dirname "$dir")"; done
  [ -d "$dir" ] && [ -w "$dir" ]
}

create_key() {
  [ -e "$KEY_FILE" ] && stop "$KEY_FILE exists but holds no usable key; it is never overwritten. Fix or move it by hand."
  local key_dir
  key_dir="$(dirname "$KEY_FILE")"
  if can_create "$key_dir"; then
    REELFORGE_MASTER_KEY_FILE="$KEY_FILE" "$PYTHON" -m app.master_key init --path "$KEY_FILE" \
      || stop "python -m app.master_key init refused; see the message above."
  else
    local staging
    staging="$(mktemp -d)"
    # Expanded now: the staging copy of the key is removed however the script ends.
    trap "rm -rf '$staging'" EXIT
    REELFORGE_MASTER_KEY_FILE="$staging/master.key" "$PYTHON" -m app.master_key init --path "$staging/master.key" \
      || stop "python -m app.master_key init refused; see the message above."
    [ -d "$key_dir" ] || sudo install -d -m 750 -o root -g "$(id -gn)" "$key_dir"
    sudo install -m 600 -o "$(id -un)" -g "$(id -gn)" "$staging/master.key" "$KEY_FILE"
  fi
}

# 1. Already usable?
if "$PYTHON" -m app.master_key status; then
  exit 0
fi

# 2. The legacy key is still around (only a key file the services expect is missing): move it into the file.
legacy_key=no
if [ -n "${REELFORGE_TOKEN_ENCRYPTION_KEY:-}" ]; then
  legacy_key=yes
elif [ -n "${REELFORGE_ENV_FILE:-}" ] && [ -r "$REELFORGE_ENV_FILE" ] \
    && grep -Eq '^[[:space:]]*REELFORGE_TOKEN_ENCRYPTION_KEY=[^[:space:]]' "$REELFORGE_ENV_FILE"; then
  legacy_key=yes
fi
if [ "$legacy_key" = yes ]; then
  echo
  echo "== The legacy REELFORGE_TOKEN_ENCRYPTION_KEY is available: moving it into $KEY_FILE (the same key) =="
  create_key
else
  # 3. No key anywhere: is there encrypted data a new key would lose?
  echo
  echo "== No usable master key: checking PostgreSQL for encrypted data =="
  set +e
  "$PYTHON" -m app.master_key encrypted
  found=$?
  set -e
  if [ "$found" -eq 1 ]; then
    stop "the database already holds encrypted data (OAuth tokens, payment or provider secrets)." \
         "A new key would make it unreadable. Restore the original key file from your backup to:" \
         "  $KEY_FILE   (chmod 600, owned by the account the services run as)" \
         "then run ./deploy.sh again."
  elif [ "$found" -ne 0 ]; then
    stop "the database could not be checked, so no key is generated." \
         "Fix instance/bootstrap.json or the database connection, then run ./deploy.sh again."
  fi
  echo
  echo "== New installation: creating the master key at $KEY_FILE =="
  create_key
fi

# 4. Verify.
echo
echo "== Verifying the master key =="
REELFORGE_MASTER_KEY_FILE="$KEY_FILE" "$PYTHON" -m app.master_key status \
  || stop "the new key file is not usable; nothing was restarted."

# 5. Continue.
echo
echo "IMPORTANT: back up $KEY_FILE now, off this server and separately from the database backups."
echo "Without it, every stored secret is lost. If it is not at the default path /etc/reelforge/master.key,"
echo "set Environment=REELFORGE_MASTER_KEY_FILE=$KEY_FILE in the systemd units."
