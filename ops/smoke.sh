#!/bin/sh
set -eu

base_url=${SMOKE_BASE_URL:-http://127.0.0.1:8080}
attempts=${SMOKE_ATTEMPTS:-20}
timeout_seconds=${SMOKE_TIMEOUT_SECONDS:-3}

case "$attempts" in
  ''|*[!0-9]*) echo "SMOKE_ATTEMPTS must be an integer" >&2; exit 2 ;;
esac
case "$timeout_seconds" in
  ''|*[!0-9]*) echo "SMOKE_TIMEOUT_SECONDS must be an integer" >&2; exit 2 ;;
esac
if [ "$attempts" -lt 1 ] || [ "$attempts" -gt 60 ]; then
  echo "SMOKE_ATTEMPTS must be between 1 and 60" >&2
  exit 2
fi
if [ "$timeout_seconds" -lt 1 ] || [ "$timeout_seconds" -gt 30 ]; then
  echo "SMOKE_TIMEOUT_SECONDS must be between 1 and 30" >&2
  exit 2
fi

base_url=${base_url%/}

fetch() {
  path=$1
  remaining=$attempts
  while [ "$remaining" -gt 0 ]; do
    if body=$(curl --fail --silent --show-error --max-time "$timeout_seconds" "$base_url$path" 2>/dev/null); then
      printf '%s' "$body"
      return 0
    fi
    remaining=$((remaining - 1))
    if [ "$remaining" -gt 0 ]; then
      sleep 1
    fi
  done
  echo "Smoke check failed for $path" >&2
  return 1
}

health=$(fetch /healthz)
case "$health" in
  *'"status":"ok"'*) ;;
  *) echo "Health response was invalid" >&2; exit 1 ;;
esac

analysis=$(fetch /api/demo/analysis)
case "$analysis" in
  *'"status":"NOT_READY"'*) ;;
  *) echo "API response was invalid" >&2; exit 1 ;;
esac

page=$(fetch /)
case "$page" in
  *'Release intelligence'*) ;;
  *) echo "Web response was invalid" >&2; exit 1 ;;
esac

echo "Release intelligence smoke checks passed"
