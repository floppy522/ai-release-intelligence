#!/bin/sh
set -u

status=${1:-1}
case "$status" in
  ''|*[!0-9]*)
    printf '%s\n' 'compose cleanup requires a numeric exit status' >&2
    exit 2
    ;;
esac

compose_file=${COMPOSE_FILE:-compose.test.yaml}
if [ "$status" -ne 0 ]; then
  docker compose -f "$compose_file" ps --all || true
  docker compose -f "$compose_file" logs \
    --no-color --timestamps --tail=200 postgres migrate api web || true
fi
docker compose -f "$compose_file" down -v --remove-orphans || true
exit "$status"
