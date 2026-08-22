#!/usr/bin/env bash
# Bring up the host-side macvlan shim and route this box's own macvlan
# containers through it.
#
# A macvlan child cannot talk to its own parent interface, so the host cannot
# reach containers it is itself hosting. A second macvlan child (the "shim")
# plus a /32 route per container IP fixes that.
#
# Routing the whole subnet through the shim would shadow the host's real LAN
# route and cut it off from everything else -- only ever add /32s.
set -uo pipefail

IFACE="${IFACE:-macvlan-shim}"
PARENT="${PARENT:-ens3}"
SHIM_IP="${SHIM_IP:-192.168.13.250}"
NET="${NET:-lan-macvlan}"

# Static fallback so boot works even if Docker has not started containers yet.
STATIC_ROUTES="${MACVLAN_SHIM_ROUTES:-}"

ip link del "$IFACE" 2>/dev/null || true
ip link add "$IFACE" link "$PARENT" type macvlan mode bridge
ip addr add "$SHIM_IP/32" dev "$IFACE"
ip link set "$IFACE" up

targets="$STATIC_ROUTES"
if command -v docker >/dev/null 2>&1; then
    discovered=$(docker network inspect "$NET" \
        --format '{{range .Containers}}{{.IPv4Address}} {{end}}' 2>/dev/null \
        | tr ' ' '\n' | cut -d/ -f1 | grep -E '^[0-9.]+$' || true)
    targets="$targets $discovered"
fi

for ip in $(echo "$targets" | tr ' ' '\n' | sort -u); do
    [ -n "$ip" ] || continue
    ip route replace "$ip/32" dev "$IFACE" && echo "routed $ip via $IFACE"
done
