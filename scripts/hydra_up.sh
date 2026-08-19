#!/usr/bin/env bash
# Bring HydraDB up inside WSL Ubuntu and keep it there.
#
# WSL terminates a distro once its last process exits, which takes dockerd and
# the graph node with it. This script is idempotent: run it any time, it
# restores the node and prints the address to talk to.
set -u

TOKEN='local-development-token-32-bytes'

wsl -d Ubuntu -u root -e bash -lc '
set -u
# 1. dockerd
if ! docker info >/dev/null 2>&1; then
  nohup dockerd --iptables=false --bridge=none >/var/log/dockerd.log 2>&1 &
  for i in $(seq 1 40); do docker info >/dev/null 2>&1 && break; sleep 1; done
fi

# 2. store (idempotent)
mkdir -p /var/lib/hydradb/store /var/lib/hydradb/cache
[ -f /var/lib/hydradb/auth-token ] || printf "%s\n" "local-development-token-32-bytes" > /var/lib/hydradb/auth-token
chown -R 10001:10001 /var/lib/hydradb

# 3. graph node
if ! docker ps --filter name=hydradb --filter status=running -q | grep -q .; then
  docker rm -f hydradb >/dev/null 2>&1 || true
  docker run -d --name hydradb --network host --restart always \
    -v /var/lib/hydradb:/data \
    -e CLOUD_PROVIDER=local -e LOCAL_PATH=/data/store \
    -e GRAPH_NAMESPACE=default -e GRAPH_ID=default \
    -e GRAPH_CELL_ID=cell-0 -e GRAPH_CELLS=cell-0 -e GRAPH_NODE_ID=node-0 \
    -e GRAPH_BOLT_NODE_ADDRESSES=node-0=127.0.0.1:7687 \
    -e GRAPH_ADVERTISED_BOLT_ADDR=127.0.0.1:7687 \
    -e GRAPH_DATA_CACHE_DIR=/data/cache \
    -e GRAPH_AUTH_TOKEN_FILE=/data/auth-token \
    -e GRAPH_ALLOW_PLAINTEXT=true -e RUST_MIN_STACK=33554432 \
    ghcr.io/hydra-db/hydradb:latest >/dev/null
fi

# 4. readiness
for i in $(seq 1 60); do
  c=$(curl -s -o /dev/null -w "%{http_code}" -m 3 http://127.0.0.1:9090/readyz || true)
  [ "$c" = "200" ] && break
  sleep 2
done

# 5. keepalive: hold the distro open so it is not reaped between calls
pgrep -f "hydradb-keepalive" >/dev/null 2>&1 || \
  nohup bash -c "exec -a hydradb-keepalive sleep infinity" >/dev/null 2>&1 &

echo "STATUS $(docker ps --filter name=hydradb --format "{{.Status}}")"
echo "IP $(hostname -I | awk "{print \$1}")"
'
