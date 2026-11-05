#!/usr/bin/env bash
# Pi needs zlib zstd: Node 22.19+ or 23.8+ (23.0-23.7 lack it).
# Run the agent contract suite against echo, pi and deep, each started locally against fake-monarch.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -f .env ]; then set -a; . ./.env; set +a; fi

if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  LLM=1
  echo "contract: ANTHROPIC_API_KEY is set, running llm tests for pi and deep"
else
  LLM=0
  echo "contract: ANTHROPIC_API_KEY is not set, running non-llm tests only"
fi

node_ok() {
  command -v node >/dev/null 2>&1 || return 1
  node -e 'const [a,b]=process.versions.node.split(".").map(Number);process.exit(a>=24||(a===23&&b>=8)||(a===22&&b>=19)?0:1)'
}
if ! node_ok; then
  echo "agent-pi needs Node >= 22.19; run \`mise install\` and use \`mise run contract\` (found: $(node --version 2>/dev/null || echo none))" >&2
  exit 1
fi

TMP="$(mktemp -d)"
PIDS=()
set -m # each background job gets its own process group, so cleanup reaches uv/npm children
cleanup() {
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do kill -- "-$pid" 2>/dev/null || true; done
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do wait "$pid" 2>/dev/null || true; done
  rm -rf "$TMP"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

FAKE=http://127.0.0.1:8190
export MONARCH_MCP_URL="$FAKE/mcp"

start() { # name, command...
  local name=$1; shift
  "$@" >"$TMP/$name.log" 2>&1 &
  PIDS+=("$!")
}

wait_for() { # name, url
  for _ in $(seq 1 120); do
    curl -sS -o /dev/null "$2" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  echo "contract: $1 did not become ready; log:" >&2
  cat "$TMP/$1.log" >&2
  return 1
}

run_suite() { # name, port, mode (all|default)
  local name=$1 port=$2 mode=$3
  echo "== contract: $name =="
  local args=(-v)
  if [ "$mode" = all ]; then
    if [ "$LLM" = 1 ]; then args+=(-m "llm or not llm"); else args+=(-m "not llm"); fi
  fi
  AGENT_URL="http://127.0.0.1:$port" FAKE_MONARCH_URL="$FAKE" uv run pytest contract/tests "${args[@]}"
}

start fake uv run fake-monarch --port 8190
wait_for fake "$FAKE/_mode"

start echo uv run agent-echo
wait_for echo http://127.0.0.1:8201/health
run_suite echo 8201 default

start pi env PI_DATA_DIR="$TMP/pi" npm --prefix agents/pi start
wait_for pi http://127.0.0.1:8202/health
run_suite pi 8202 all

start deep env DEEP_DATA_DIR="$TMP/deep" uv run agent-deep
wait_for deep http://127.0.0.1:8203/health
run_suite deep 8203 all
