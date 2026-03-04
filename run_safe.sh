#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source ".env"
  set +a
fi

# Clear proxy envs to avoid unstable routing in terminal sessions.
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY

# Keep siliconflow direct to reduce proxy interference.
export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,::1},api.siliconflow.cn,.siliconflow.cn"
export no_proxy="$NO_PROXY"

# Default timeout guard for whole run.
export RUN_TIMEOUT_SECONDS="${RUN_TIMEOUT_SECONDS:-60}"

exec ./.venv/bin/python -m openclaw_mini_lc.cli "$@"
