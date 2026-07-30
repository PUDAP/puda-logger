#!/bin/sh
set -eu

if [ -n "${INFLUXDB_TOKEN:-}" ]; then
  echo "INFLUXDB_TOKEN is forbidden; use INFLUXDB_TOKEN_FILE" >&2
  exit 1
fi
INFLUXDB_TOKEN_FILE="${INFLUXDB_TOKEN_FILE:-/run/secrets/influxdb-token}"
if [ -L "$INFLUXDB_TOKEN_FILE" ]; then
  echo "InfluxDB token file must not be a symlink" >&2
  exit 1
fi
if [ ! -f "$INFLUXDB_TOKEN_FILE" ] || [ ! -r "$INFLUXDB_TOKEN_FILE" ]; then
  echo "InfluxDB token file must be a readable regular file" >&2
  exit 1
fi
token_mode=$(stat -c '%a' "$INFLUXDB_TOKEN_FILE")
case "$token_mode" in
  *00) ;;
  *)
    echo "InfluxDB token file must not be accessible by group or other users" >&2
    exit 1
    ;;
esac
if [ "$(stat -c '%u' "$INFLUXDB_TOKEN_FILE")" != "$(id -u)" ]; then
  echo "InfluxDB token file must be owned by the runtime user" >&2
  exit 1
fi
INFLUXDB_TOKEN=$(cat "$INFLUXDB_TOKEN_FILE")
if [ -z "$INFLUXDB_TOKEN" ]; then
  echo "InfluxDB token file must not be empty" >&2
  exit 1
fi
export INFLUXDB_TOKEN

NATS_CONFIG_LIB="${NATS_CONFIG_LIB:-/usr/local/lib/nats-config.sh}"
if [ ! -r "$NATS_CONFIG_LIB" ]; then
  echo "shared NATS configuration library is not readable" >&2
  exit 1
fi
# shellcheck source=nats-config.sh
. "$NATS_CONFIG_LIB"
nats_validate_config

reject_unsafe_text() {
  name="$1"
  value="$2"
  if [ -z "$value" ] || printf '%s' "$value" | LC_ALL=C grep -q '[[:cntrl:][:space:]"\\]'; then
    echo "$name contains unsupported control, whitespace, quote, or backslash characters" >&2
    exit 1
  fi
}

export INFLUXDB_URL="${INFLUXDB_URL:-http://localhost:8181}"
export INFLUXDB_DATABASE="${INFLUXDB_DATABASE:-machines}"
export INFLUXDB_ORGANIZATION="${INFLUXDB_ORGANIZATION:-bears}"

case "$INFLUXDB_URL" in
  http://*|https://*) ;;
  *)
    echo "INFLUXDB_URL must use http or https" >&2
    exit 1
    ;;
esac
reject_unsafe_text INFLUXDB_URL "$INFLUXDB_URL"
for name in INFLUXDB_TOKEN INFLUXDB_DATABASE INFLUXDB_ORGANIZATION; do
  eval "value=\${$name}"
  if ! printf '%s' "$value" | LC_ALL=C grep -Eq '^[A-Za-z0-9._-]+$'; then
    echo "$name contains unsupported characters" >&2
    exit 1
  fi
done

TELEGRAF_CONFIG_TEMPLATE="${TELEGRAF_CONFIG_TEMPLATE:-/etc/telegraf/telegraf.conf.template}"
TELEGRAF_CONFIG_OUTPUT="${TELEGRAF_CONFIG_OUTPUT:-/tmp/telegraf.conf}"
if [ ! -f "$TELEGRAF_CONFIG_TEMPLATE" ] || [ ! -r "$TELEGRAF_CONFIG_TEMPLATE" ]; then
  echo "Telegraf configuration template is not a readable regular file" >&2
  exit 1
fi

# The rendered file contains the InfluxDB token. Create it atomically with
# owner-only permissions and never print its contents or substituted values.
umask 077
config_tmp="${TELEGRAF_CONFIG_OUTPUT}.tmp.$$"
cleanup_config_tmp() {
  rm -f "$config_tmp"
}
trap cleanup_config_tmp EXIT HUP INT TERM

# envsubst expects the variable names literally in its allowlist.
# shellcheck disable=SC2016
envsubst '${NATS_SERVERS_TOML} ${NATS_CREDS_FILE} ${NATS_SECURE} ${NATS_TLS_CA_FILE} ${NATS_TLS_CERT_FILE} ${NATS_TLS_KEY_FILE} ${INFLUXDB_URL} ${INFLUXDB_TOKEN} ${INFLUXDB_DATABASE} ${INFLUXDB_ORGANIZATION}' \
  < "$TELEGRAF_CONFIG_TEMPLATE" \
  > "$config_tmp"
chmod 600 "$config_tmp"
mv -f "$config_tmp" "$TELEGRAF_CONFIG_OUTPUT"
chmod 600 "$TELEGRAF_CONFIG_OUTPUT"
trap - EXIT HUP INT TERM

exec telegraf --config "$TELEGRAF_CONFIG_OUTPUT"
