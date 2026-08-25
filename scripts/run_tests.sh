#!/usr/bin/env bash
# Runs the suite with a throwaway MySQL alongside it.
#
#   scripts/run_tests.sh              unit + integration, on the host interpreter
#   scripts/run_tests.sh --docker     same, inside the image (pinned deps)
#   scripts/run_tests.sh tests/unit   any trailing args go to pytest
#
# --docker is the authoritative run: the host interpreter drifts from
# requirements.txt, which is how the pool_pre_ping regression reached production.
set -euo pipefail

CONTAINER=seymour-testdb
PORT=3312
MYSQL_IMAGE=mysql:8.4
DB=seymour_test
PASSWORD=test

IN_DOCKER=0
if [[ "${1:-}" == "--docker" ]]; then
    IN_DOCKER=1
    shift
fi

cd "$(dirname "$0")/.."

if ! docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true; then
    echo "starting $CONTAINER on :$PORT"
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    docker run -d --name "$CONTAINER" \
        -e MYSQL_ROOT_PASSWORD="$PASSWORD" -e MYSQL_DATABASE="$DB" \
        -p "$PORT:3306" \
        --health-cmd="mysqladmin ping -uroot -p$PASSWORD --silent" \
        --health-interval=3s --health-retries=40 \
        "$MYSQL_IMAGE" >/dev/null

    printf 'waiting for mysql'
    for _ in $(seq 1 60); do
        [[ "$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER")" == healthy ]] && break
        printf '.'
        sleep 2
    done
    echo
    [[ "$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER")" == healthy ]] || {
        echo "mysql never became healthy:"; docker logs --tail 20 "$CONTAINER"; exit 1; }
fi

if [[ "$IN_DOCKER" == 1 ]]; then
    docker build --target test -t seymour-backend-test . >/dev/null 2>&1
    # host.docker.internal reaches the mysql container's published port from
    # inside this one without putting them on a shared network.
    exec docker run --rm \
        --add-host=host.docker.internal:host-gateway \
        -e "TEST_DATABASE_URL=mysql+aiomysql://root:$PASSWORD@host.docker.internal:$PORT/$DB" \
        seymour-backend-test pytest "$@"
fi

export TEST_DATABASE_URL="mysql+aiomysql://root:$PASSWORD@127.0.0.1:$PORT/$DB"
exec python -m pytest "$@"
