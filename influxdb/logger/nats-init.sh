#!/bin/sh
# Ensure response streams (limits retention) and durable push consumers exist.
#
# Limits, not workqueue: CLI, postgres-logger, reductstore-logger, and this
# logger each need their own copy of every response. WorkQueue would deliver
# each message to only one of those consumers.
#
# Durables are created here (not by Telegraf). Telegraf QueueSubscribe uses
# the queue group as the durable name and binds to these consumers; because
# it did not create them, Unsubscribe on shutdown will not delete them, so
# responses published while the logger is down stay in the stream until
# max-age and are replayed on reconnect.
set -e

NATS_URL="$(echo "${NATS_SERVERS:-}" | cut -d',' -f1 | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
if [ -z "$NATS_URL" ]; then NATS_URL=nats://localhost:4222; fi

MAX_PENDING="${JS_MAX_ACK_PENDING:-20000}"
MAX_AGE="${JS_RESPONSE_MAX_AGE:-24h}"

echo "Ensuring JetStream response streams on $NATS_URL (limits, max-age $MAX_AGE, max-ack-pending $MAX_PENDING)"

ensure_stream() {
  stream="$1"
  subjects="$2"
  if nats stream info "$stream" --server "$NATS_URL" >/dev/null 2>&1; then
    echo "Updating stream $stream -> retention=limits max-age=$MAX_AGE"
    nats stream edit "$stream" --server "$NATS_URL" \
      --retention limits \
      --max-age "$MAX_AGE" \
      --force
  else
    echo "Creating stream $stream"
    nats stream add "$stream" --server "$NATS_URL" \
      --subjects "$subjects" \
      --retention limits \
      --max-age "$MAX_AGE" \
      --storage file \
      --defaults
  fi
}

# Durable name and deliver-group must match telegraf.conf.template queue_group.
ensure_consumer() {
  stream="$1"
  consumer="$2"
  filter="$3"
  if nats consumer info "$stream" "$consumer" --server "$NATS_URL" >/dev/null 2>&1; then
    echo "Updating consumer $stream/$consumer max-pending=$MAX_PENDING"
    nats consumer edit "$stream" "$consumer" --server "$NATS_URL" \
      --max-pending "$MAX_PENDING" \
      --wait 60s \
      --force
  else
    echo "Creating durable consumer $stream/$consumer"
    nats consumer add "$stream" "$consumer" --server "$NATS_URL" \
      --filter "$filter" \
      --ack explicit \
      --deliver all \
      --replay instant \
      --deliver-group "$consumer" \
      --max-pending "$MAX_PENDING" \
      --wait 60s \
      --defaults
  fi
}

ensure_stream RESPONSE_QUEUE "puda.*.cmd.response.queue"
ensure_stream RESPONSE_IMMEDIATE "puda.*.cmd.response.immediate"

ensure_consumer RESPONSE_QUEUE puda_logger_cmd_responses_queue "puda.*.cmd.response.queue"
ensure_consumer RESPONSE_IMMEDIATE puda_logger_cmd_responses_immediate "puda.*.cmd.response.immediate"

echo "JetStream response streams and durable consumers ready"
