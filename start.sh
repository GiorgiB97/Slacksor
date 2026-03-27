#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE=".env"

prompt_value() {
  local prompt_label="$1"
  local current_value="${2:-}"
  local secret_mode="${3:-false}"
  local input_value=""
  PROMPT_RESULT=""

  if [[ -n "$current_value" ]]; then
    if [[ "$secret_mode" == "true" ]]; then
      printf "%s [press Enter to keep current]: " "$prompt_label"
      read -r -s input_value
      printf "\n"
    else
      read -r -p "$prompt_label [$current_value]: " input_value
    fi
    if [[ -z "$input_value" ]]; then
      input_value="$current_value"
    fi
  else
    if [[ "$secret_mode" == "true" ]]; then
      printf "%s: " "$prompt_label"
      read -r -s input_value
      printf "\n"
    else
      read -r -p "$prompt_label: " input_value
    fi
  fi

  input_value="${input_value//$'\r'/}"
  input_value="${input_value//$'\n'/}"
  PROMPT_RESULT="$input_value"
}

load_existing_env() {
  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi
}

write_env_file() {
  local bot_token="$1"
  local app_token="$2"
  local db_path="$3"
  local timeout_seconds="$4"
  local keepalive_seconds="$5"
  local chunk_size="$6"
  local polling_interval="$7"
  local enable_transcript_mirror="$8"
  local enable_cursor_hooks_sync="$9"

  cat > "$ENV_FILE" <<EOF
SLACK_BOT_TOKEN=$bot_token
SLACK_APP_TOKEN=$app_token
SLACKSOR_DB_PATH=$db_path
SLACKSOR_SESSION_TIMEOUT_SECONDS=$timeout_seconds
SLACKSOR_KEEPALIVE_SECONDS=$keepalive_seconds
SLACKSOR_POST_CHUNK_SIZE=$chunk_size
SLACKSOR_POLLING_INTERVAL_SECONDS=$polling_interval
SLACKSOR_ENABLE_IDE_TRANSCRIPT_MIRROR=$enable_transcript_mirror
SLACKSOR_ENABLE_CURSOR_HOOKS_SYNC=$enable_cursor_hooks_sync
EOF
}

ensure_env() {
  load_existing_env

  local bot_token
  local app_token
  local db_path
  local timeout_seconds
  local keepalive_seconds
  local chunk_size
  local polling_interval
  local enable_transcript_mirror
  local enable_cursor_hooks_sync
  local missing_any="false"

  bot_token="${SLACK_BOT_TOKEN:-}"
  app_token="${SLACK_APP_TOKEN:-}"
  db_path="${SLACKSOR_DB_PATH:-}"
  timeout_seconds="${SLACKSOR_SESSION_TIMEOUT_SECONDS:-}"
  keepalive_seconds="${SLACKSOR_KEEPALIVE_SECONDS:-}"
  chunk_size="${SLACKSOR_POST_CHUNK_SIZE:-}"
  polling_interval="${SLACKSOR_POLLING_INTERVAL_SECONDS:-}"
  enable_transcript_mirror="${SLACKSOR_ENABLE_IDE_TRANSCRIPT_MIRROR:-}"
  enable_cursor_hooks_sync="${SLACKSOR_ENABLE_CURSOR_HOOKS_SYNC:-}"

  if [[ -z "$bot_token" ]]; then
    missing_any="true"
    prompt_value "SLACK_BOT_TOKEN (xoxb-...)" "" "true"
    bot_token="$PROMPT_RESULT"
  fi

  if [[ -z "$app_token" ]]; then
    missing_any="true"
    prompt_value "SLACK_APP_TOKEN (xapp-...)" "" "true"
    app_token="$PROMPT_RESULT"
  fi

  if [[ -z "$db_path" ]]; then
    missing_any="true"
    prompt_value "SLACKSOR_DB_PATH" "./slacksor.db"
    db_path="$PROMPT_RESULT"
  fi

  if [[ -z "$timeout_seconds" ]]; then
    missing_any="true"
    prompt_value "SLACKSOR_SESSION_TIMEOUT_SECONDS" "300"
    timeout_seconds="$PROMPT_RESULT"
  fi

  if [[ -z "$keepalive_seconds" ]]; then
    missing_any="true"
    prompt_value "SLACKSOR_KEEPALIVE_SECONDS" "30"
    keepalive_seconds="$PROMPT_RESULT"
  fi

  if [[ -z "$chunk_size" ]]; then
    missing_any="true"
    prompt_value "SLACKSOR_POST_CHUNK_SIZE" "3500"
    chunk_size="$PROMPT_RESULT"
  fi

  if [[ -z "$polling_interval" ]]; then
    missing_any="true"
    prompt_value "SLACKSOR_POLLING_INTERVAL_SECONDS" "1.0"
    polling_interval="$PROMPT_RESULT"
  fi

  if [[ -z "$enable_transcript_mirror" ]]; then
    missing_any="true"
    prompt_value "SLACKSOR_ENABLE_IDE_TRANSCRIPT_MIRROR" "true"
    enable_transcript_mirror="$PROMPT_RESULT"
  fi

  if [[ -z "$enable_cursor_hooks_sync" ]]; then
    missing_any="true"
    prompt_value "SLACKSOR_ENABLE_CURSOR_HOOKS_SYNC" "true"
    enable_cursor_hooks_sync="$PROMPT_RESULT"
  fi

  if [[ "$missing_any" == "false" ]]; then
    echo "All required environment variables already exist in .env. Skipping setup."
    echo
    return
  fi

  if [[ -z "$bot_token" || -z "$app_token" ]]; then
    echo "SLACK_BOT_TOKEN and SLACK_APP_TOKEN are required."
    exit 1
  fi

  write_env_file \
    "$bot_token" \
    "$app_token" \
    "$db_path" \
    "$timeout_seconds" \
    "$keepalive_seconds" \
    "$chunk_size" \
    "$polling_interval" \
    "$enable_transcript_mirror" \
    "$enable_cursor_hooks_sync"

  echo ".env has been updated."
  echo
}

ensure_env

check_cursor_auth() {
  python -c "
import subprocess, sys
try:
    r = subprocess.run(
        ['cursor', 'agent', 'models'],
        capture_output=True, text=True, timeout=15,
    )
    sys.exit(0 if r.returncode == 0 else 1)
except Exception:
    sys.exit(1)
" 2>/dev/null
}

if ! check_cursor_auth; then
  echo ""
  echo "[slacksor] Cursor Agent is not authenticated."
  echo "[slacksor] Launching 'cursor agent' for login..."
  echo "[slacksor] Complete authentication in your browser, then exit cursor agent (q or Ctrl+C)."
  echo ""
  cursor agent || true
  echo ""
  if ! check_cursor_auth; then
    echo "[slacksor] Still not authenticated. Please run 'cursor agent' manually and retry."
    exit 1
  fi
  echo "[slacksor] Authentication successful."
  echo ""
fi

read -r -p "Do you want to run in TUI mode (recommended) ? ('Y'/'y' - TUI, 'N'/'n' - Headless): " RUN_MODE

case "${RUN_MODE}" in
  Y|y)
    echo "Starting slacksor in TUI mode..."
    python src/slacksor.py
    ;;
  N|n)
    echo "Starting slacksor in headless mode..."
    python src/slacksor.py serve
    ;;
  *)
    echo "Invalid input. Starting slacksor in TUI mode by default..."
    python src/slacksor.py
    ;;
esac
