#!/bin/sh
# Detects a known Telegraf nats_consumer failure mode: the JetStream push
# consumers backing the "response" inputs (see telegraf.conf.template) can
# silently wedge, pinning num_ack_pending at max_ack_pending forever. When
# that happens NATS stops pushing new response messages and Telegraf never
# recovers on its own or logs an error - the container looks "Up" and quiet
# while responses silently stop being written to InfluxDB.
#
# This script queries each durable consumer directly and reports unhealthy
# if its ack window is fully saturated, which is the signature of the stall.
# Pair with an autoheal-style watcher (see compose.yml) to actually restart
# the container when this happens, since Docker does not do this on its own.
set -eu

: "${NATS_SERVERS:?NATS_SERVERS is required}"

# Must match the queue_group / jetstream_stream values in telegraf.conf.template.
CONSUMERS="RESPONSE_QUEUE:puda_logger_cmd_responses_queue RESPONSE_IMMEDIATE:puda_logger_cmd_responses_immediate"

check_consumer() {
  stream="$1"
  consumer="$2"

  info=$(nats --server "$NATS_SERVERS" consumer info "$stream" "$consumer" --json 2>&1) || {
    echo "healthcheck: failed to query consumer $stream/$consumer: $info" >&2
    return 1
  }

  ack_pending=$(echo "$info" | jq -r '.num_ack_pending // 0')
  max_ack_pending=$(echo "$info" | jq -r '.config.max_ack_pending // 0')

  if [ "$max_ack_pending" -gt 0 ] && [ "$ack_pending" -ge "$max_ack_pending" ]; then
    echo "healthcheck: consumer $stream/$consumer looks stalled (num_ack_pending=$ack_pending >= max_ack_pending=$max_ack_pending)" >&2
    return 1
  fi

  return 0
}

status=0
for pair in $CONSUMERS; do
  stream="${pair%%:*}"
  consumer="${pair#*:}"
  check_consumer "$stream" "$consumer" || status=1
done

exit "$status"
