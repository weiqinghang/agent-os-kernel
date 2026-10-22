#!/usr/bin/env bash
# Source box-local secrets without printing them.
# - data/das-local.env: DAS_MCP_URL, DAS_MCP_AUTH_TOKEN, DAS_HEALTH_URL (gitignored)
# - .env: only MODEL_* / XAI_* / OPENAI_* so a live model works without a manual export
# shellcheck shell=bash

load_local_env() {
  local root="$1"
  local das_env="$root/data/das-local.env"
  if [[ -f "$das_env" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$das_env"
    set +a
  fi

  local envfile="$root/.env"
  [[ -f "$envfile" ]] || return 0
  local line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^[[:space:]]*$ ]] && continue
    line="${line#export }"
    case "$line" in
      MODEL_*|XAI_*|OPENAI_*)
        key="${line%%=*}"
        val="${line#*=}"
        val="${val%\"}"
        val="${val#\"}"
        val="${val%\'}"
        val="${val#\'}"
        export "$key=$val"
        ;;
    esac
  done < "$envfile"
}
