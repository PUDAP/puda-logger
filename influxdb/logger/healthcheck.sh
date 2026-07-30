#!/bin/sh
# Detect missing, malformed, unbound, or saturated required JetStream state. A
# confirmed failure terminates Telegraf (PID 1), allowing the restart policy to
# recover without a privileged Docker-socket sidecar.
set -eu
set -f

NATS_CONFIG_LIB="${NATS_CONFIG_LIB:-/usr/local/lib/nats-config.sh}"
if [ ! -r "$NATS_CONFIG_LIB" ]; then
  echo "healthcheck: shared NATS configuration library is not readable" >&2
  exit 1
fi
# shellcheck source=nats-config.sh
. "$NATS_CONFIG_LIB"
if ! nats_validate_config; then
  echo "healthcheck: NATS configuration validation failed" >&2
  exit 1
fi

# Must match every jetstream_stream / queue_group pair in the Telegraf template.
STREAMS="RESPONSE_QUEUE RESPONSE_IMMEDIATE ALD_TELEMETRY"
CONSUMERS="RESPONSE_QUEUE:puda_logger_cmd_responses_queue:puda.*.cmd.response.queue RESPONSE_IMMEDIATE:puda_logger_cmd_responses_immediate:puda.*.cmd.response.immediate ALD_TELEMETRY:puda_logger_ald_points:puda.*.tlm.ald.point ALD_TELEMETRY:puda_logger_ald_alarms:puda.*.tlm.ald.alarm ALD_TELEMETRY:puda_logger_ald_health:puda.*.tlm.ald.health ALD_TELEMETRY:puda_logger_ald_heartbeat:puda.*.tlm.ald.heartbeat"

check_stream() {
  stream="$1"
  if ! info=$(nats_cli stream info "$stream" --json 2>/dev/null); then
    echo "healthcheck: failed to query required stream $stream" >&2
    return 1
  fi
  if ! printf '%s' "$info" | jq -e --arg stream "$stream" '
    (type == "object") and
    (.config | type == "object") and
    (.config.name | type == "string") and
    (.config.name == $stream)
  ' >/dev/null 2>&1; then
    echo "healthcheck: malformed or incomplete state for required stream $stream" >&2
    return 1
  fi
}

check_consumer() {
  stream="$1"
  consumer="$2"
  subject="$3"

  if ! info=$(nats_cli consumer info "$stream" "$consumer" --json 2>/dev/null); then
    echo "healthcheck: failed to query required consumer $stream/$consumer" >&2
    return 1
  fi

  if ! printf '%s' "$info" | jq -e \
    --arg stream "$stream" \
    --arg consumer "$consumer" \
    --arg subject "$subject" '
      (type == "object") and
      (.stream_name == $stream) and
      (.name == $consumer) and
      (.config | type == "object") and
      (.config.name == $consumer) and
      (.config.durable_name == $consumer) and
      (.config.deliver_group == $consumer) and
      (.config.filter_subject == $subject) and
      ((.config.ack_policy | type) == "string") and
      ((.config.ack_policy | ascii_downcase) == "explicit") and
      ((.config.max_ack_pending | type) == "number") and
      ((.config.max_ack_pending | floor) == .config.max_ack_pending) and
      (.config.max_ack_pending > 0) and
      ((.num_ack_pending | type) == "number") and
      ((.num_ack_pending | floor) == .num_ack_pending) and
      (.num_ack_pending >= 0) and
      (.push_bound == true)
    ' >/dev/null 2>&1; then
    echo "healthcheck: malformed or incomplete state for required consumer $stream/$consumer" >&2
    return 1
  fi

  if ! printf '%s' "$info" | jq -e \
    '.num_ack_pending < .config.max_ack_pending' >/dev/null 2>&1; then
    echo "healthcheck: consumer $stream/$consumer is saturated" >&2
    return 1
  fi
}

status=0
for stream in $STREAMS; do
  check_stream "$stream" || status=1
done
for triple in $CONSUMERS; do
  stream="${triple%%:*}"
  remainder="${triple#*:}"
  consumer="${remainder%%:*}"
  subject="${remainder#*:}"
  check_consumer "$stream" "$consumer" "$subject" || status=1
done

if [ "$status" -ne 0 ]; then
  echo "healthcheck: terminating Telegraf so restart policy can recover" >&2
  telegraf_pid=1
  # Unit tests cannot signal the host's PID 1. This branch is unavailable unless
  # an explicit test-only switch is set; Compose never configures it.
  if [ "${HEALTHCHECK_TEST_MODE:-0}" = "1" ]; then
    telegraf_pid="${HEALTHCHECK_TELEGRAF_PID:-999999}"
  fi
  kill -TERM "$telegraf_pid" 2>/dev/null || true
fi
exit "$status"
