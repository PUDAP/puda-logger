#!/bin/sh
set -e

if [ -z "$NATS_SERVERS" ]; then
  echo "NATS_SERVERS is required" >&2
  exit 1
fi

servers=""
IFS=','
for server in $NATS_SERVERS; do
  server=$(echo "$server" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  if [ -n "$server" ]; then
    servers="${servers}\"${server}\","
  fi
done
unset IFS

if [ -z "$servers" ]; then
  echo "NATS_SERVERS must contain at least one server URL" >&2
  exit 1
fi

export NATS_SERVERS="[${servers%,}]"
export INFLUXDB_URL="${INFLUXDB_URL:-http://localhost:8181}"
export INFLUXDB_TOKEN="${INFLUXDB_TOKEN:-token}"
export INFLUXDB_DATABASE="${INFLUXDB_DATABASE:-puda}"
export INFLUXDB_ORGANIZATION="${INFLUXDB_ORGANIZATION:-bears}"

envsubst '${NATS_SERVERS} ${INFLUXDB_URL} ${INFLUXDB_TOKEN} ${INFLUXDB_DATABASE} ${INFLUXDB_ORGANIZATION}' \
  < /etc/telegraf/telegraf.conf.template \
  > /etc/telegraf/telegraf.conf

exec telegraf --config /etc/telegraf/telegraf.conf
