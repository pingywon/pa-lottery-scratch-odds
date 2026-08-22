#!/usr/bin/env bash
# Deploy PA Lottery Scratch Odds as a container with its own LAN IP.
#
#   ./deploy/run.sh [tag]        # default: latest
#
# The container gets 192.168.13.16 on the real LAN via macvlan, so it answers
# on port 80 without competing for ports on the host.
set -euo pipefail

IMAGE="${IMAGE:-pingywon/pa-lottery-scratch-odds}"
TAG="${1:-latest}"
NAME="${NAME:-pa-lottery-scratch-odds}"

LAN_IP="${LAN_IP:-192.168.13.16}"
LAN_SUBNET="${LAN_SUBNET:-192.168.13.0/24}"
LAN_GATEWAY="${LAN_GATEWAY:-192.168.13.1}"
LAN_PARENT="${LAN_PARENT:-ens3}"
NET="${NET:-lan-macvlan}"

# Host directory holding data.json + images/, kept current by the host's
# scrape/watchdog systemd units. Mounted so the container never serves the
# stale snapshot baked into the image.
DATA_HOST_DIR="${DATA_HOST_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

if ! docker network inspect "$NET" >/dev/null 2>&1; then
    echo "==> creating macvlan network $NET on $LAN_PARENT"
    docker network create -d macvlan \
        --subnet="$LAN_SUBNET" \
        --gateway="$LAN_GATEWAY" \
        -o parent="$LAN_PARENT" \
        "$NET"
fi

echo "==> replacing container $NAME with $IMAGE:$TAG at $LAN_IP"
docker rm -f "$NAME" >/dev/null 2>&1 || true

# Run as the invoking user so a scrape triggered from inside the container
# writes host files the host's own systemd units can still overwrite.
# Containers set ip_unprivileged_port_start=0, so non-root can still bind :80.
docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    --network "$NET" \
    --ip "$LAN_IP" \
    --user "$(id -u):$(id -g)" \
    -e DATA_DIR=/data \
    -v "$DATA_HOST_DIR:/data" \
    "$IMAGE:$TAG"

echo "==> waiting for health"
for _ in $(seq 1 30); do
    status=$(docker inspect --format '{{.State.Health.Status}}' "$NAME" 2>/dev/null || echo starting)
    [ "$status" = healthy ] && { echo "==> healthy: http://$LAN_IP/"; exit 0; }
    [ "$status" = unhealthy ] && { docker logs --tail 40 "$NAME"; echo "==> UNHEALTHY"; exit 1; }
    sleep 2
done
echo "==> timed out waiting for health"
docker logs --tail 40 "$NAME"
exit 1
