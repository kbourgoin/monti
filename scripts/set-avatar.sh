#!/usr/bin/env bash
# Set Monti's Signal profile picture (and re-send the name, which the profile API requires).
# Usage: mise run set-avatar [image]   (default: assets/monti-avatar.png; square PNG/JPEG, ~512px)
set -euo pipefail
cd "$(dirname "$0")/.."
img="${1:-assets/monti-avatar.png}"
[ -f "$img" ] || { echo "no such image: $img" >&2; exit 1; }
docker compose exec -T gateway python -c '
import base64, os, sys, httpx
r = httpx.put(
    "http://signal-cli:8080/v1/profiles/" + os.environ["BOT_NUMBER"],
    json={"name": "Monti", "base64_avatar": base64.b64encode(sys.stdin.buffer.read()).decode()},
    timeout=60,
)
print("profile updated" if r.is_success else f"failed: {r.status_code} {r.text}")
sys.exit(0 if r.is_success else 1)
' < "$img"
