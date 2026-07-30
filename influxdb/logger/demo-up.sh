#!/usr/bin/env bash
set -euo pipefail

# DEMO ONLY: creates a known synthetic token and unauthenticated loopback NATS.
mkdir -p .demo
chmod 0700 .demo
printf '%s\n' 'puda-demo-token' > .demo/influx-token
chmod 0600 .demo/influx-token

export PUDA_DEMO_UID="${PUDA_DEMO_UID:-$(id -u)}"
export PUDA_DEMO_GID="${PUDA_DEMO_GID:-$(id -g)}"
compose=(docker compose -f compose.demo.yml)
"${compose[@]}" up -d --build

nats_port="${PUDA_DEMO_NATS_PORT:-4223}"
nats_monitor_port="${PUDA_DEMO_NATS_MONITOR_PORT:-8223}"
influx_port="${PUDA_DEMO_INFLUX_PORT:-8182}"
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${nats_monitor_port}/healthz" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

logger_ready=false
for _ in $(seq 1 60); do
  container_id="$("${compose[@]}" ps -q logger)"
  if [[ -n "$container_id" ]] && [[ "$(docker inspect -f '{{.State.Running}}' "$container_id")" == true ]] \
      && docker logs "$container_id" 2>&1 | grep -Fq 'Starting Telegraf'; then
    logger_ready=true
    break
  fi
  sleep 1
done
if [[ "$logger_ready" != true ]]; then
  "${compose[@]}" ps >&2
  "${compose[@]}" logs --no-color logger >&2
  exit 1
fi

curl -fsS "http://127.0.0.1:${influx_port}/health" >/dev/null
machine="ald-logger-demo-check-$$-$(date +%s)"
payload='{"timestamp":"2026-07-29T00:00:00Z","opcua_connected":true}'
"${compose[@]}" run --rm --no-deps --entrypoint nats stream-init \
  pub "puda.${machine}.tlm.ald.health" "$payload" \
  --server "nats://127.0.0.1:${nats_port}" >/dev/null

for _ in $(seq 1 30); do
  response=$(curl -fsS -X POST "http://127.0.0.1:${influx_port}/api/v3/query_sql" \
    -H 'Content-Type: application/json' \
    --data "{\"db\":\"machines\",\"q\":\"SELECT COUNT(*) AS n FROM telemetry WHERE machine_id = '${machine}'\",\"format\":\"json\"}" 2>/dev/null || true)
  rows=$(printf '%s' "$response" | jq -r '.[0].n // 0' 2>/dev/null || printf 0)
  if [[ "$rows" -ge 1 ]]; then
    printf '%s\n' \
      'PUDA logger demo is ready (NATS -> Telegraf -> InfluxDB verified).' \
      "  NATS: nats://127.0.0.1:${nats_port}" \
      "  InfluxDB target: http://127.0.0.1:${influx_port}" \
      '  Stop: docker compose -f compose.demo.yml down'
    exit 0
  fi
  sleep 1
done

"${compose[@]}" logs --no-color logger >&2
echo 'ERROR: logger self-test message did not reach InfluxDB' >&2
exit 1
