# influxdb-logger

Streams PUDA NATS machine traffic to InfluxDB using [Telegraf](https://docs.influxdata.com/telegraf/).

## Working prototype (demo only)

Start a local unauthenticated loopback NATS JetStream and locally built logger:

```bash
./demo-up.sh
```

The demo expects the Grafana repository demo InfluxDB at
`http://127.0.0.1:8182` and exposes NATS at `nats://127.0.0.1:4223`. It uses a
known synthetic token and skips production TLS, credentials, and immutable
image requirements. Use `compose.yml`, not `compose.demo.yml`, outside a local
demo.

## Measurements

| Measurement | NATS subjects | Description |
|---|---|---|
| `commands` | `puda.*.cmd.*` (requests), `puda.*.cmd.response.*` (responses) | Command/response log |
| `telemetry` | `puda.*.tlm.health` | Machine health metrics (`cpu`, `mem`, `temp`) |
| `ald_point` | `puda.*.tlm.ald.point` | Typed ALD point values |
| `ald_alarm` | `puda.*.tlm.ald.alarm` | ALD alarm activation and recovery events |

### `commands` fields and tags

| Kind | Name | Source |
|---|---|---|
| tag | `machine_id` | `header.machine_id` (commands); parsed from NATS subject (telemetry) |
| tag | `topic` | parsed from NATS subject (`immediate`, `queue`, `response.immediate`, `response.queue`) |
| tag | `cmd_name` | `command.name` |
| tag | `msg_type` | static (`command` or `response`) |
| tag | `status` | `sent` for requests; `response.status` for responses |
| tag | `username` | `header.username` |
| tag | `run_id` | `header.run_id` |
| tag | `subject` | NATS subject (added by Telegraf) |
| field | `user_id` | `header.user_id` |
| field | `step_number` | `command.step_number` |
| field | `cmd_version` | `command.version` |
| field | `response_code` | `response.code` (responses only) |
| field | `response_message` | `response.message` (responses only) |
| field | `completed_at` | `response.completed_at` (responses only) |

`topic` and `cmd_name` are tags (not fields) so points with the same header timestamp within a run do not overwrite each other in InfluxDB.

### `telemetry` fields and tags

| Kind | Name | Source |
|---|---|---|
| tag | `machine_id` | parsed from NATS subject |
| tag | `subject` | NATS subject (added by Telegraf) |
| field | `data` | full raw JSON payload from the machine |
| optional fields | ALD Edge health | OPC UA state/counts, SQLite queue/drop/writer state, outbox counts, reconnect count, and publisher errors |

Telemetry schemas differ per machine. The logger always stores the entire payload as `data`; for the ALD Edge health contract it additionally extracts the documented operational fields used by the provisioned dashboard.

### `ald_point` and `ald_alarm` fields and tags

`event_time` is parsed as the InfluxDB point timestamp. It is not stored as a separate field.

| Kind | Name | Source / type |
|---|---|---|
| tag | `machine_id` | machine identifier |
| tag | `point_key` | stable catalog point key |
| tag | `scope` | point scope |
| tag | `tube` | tube identifier |
| tag | `category` | point category |
| tag (`ald_alarm` only) | `severity` | alarm severity |
| field | `schema` | payload schema (`string`) |
| field | `event_id` | producer event identifier (`string`) |
| field | `node_id` | full OPC UA NodeId (`string`) |
| field | `value_type` | `int`, `float`, `bool`, or `string` |
| optional field | `value_int` | integer value (`int`) |
| optional field | `value_float` | floating-point value (`float`) |
| optional field | `value_bool` | boolean value (`bool`) |
| optional field | `value_text` | string value (`string`) |
| field | `quality` | OPC UA quality (`string`) |
| field | `unit` | engineering unit (`string`) |
| field | `source_timestamp` | source timestamp (`string`) |
| field | `server_timestamp` | server timestamp (`string`) |
| field | `received_at` | logger receipt timestamp (`string`) |
| field | `catalog_sha256` | catalog content hash (`string`) |
| field (`ald_alarm` only) | `active` | `true` for activation, `false` for recovery (`bool`) |
| field (`ald_alarm` only) | `description` | alarm description (`string`) |

Exactly one of `value_int`, `value_float`, `value_bool`, and `value_text` is expected in each payload, matching `value_type`; all four parser entries are optional so every typed shape is accepted. The full `node_id` is deliberately a field rather than a tag to avoid unbounded series cardinality. `event_id`, `catalog_sha256`, `description`, `unit`, and `quality` are also fields, not tags. The ALD inputs exclude Telegraf's infrastructure-only `subject` and `host` tags, so the ALD tag set is limited to the tags listed above.

NATS messages use the raw `puda-comms` wire format (not the JSON envelope the old Python logger constructed internally).

## NATS subscription model

Command **requests** (`puda.*.cmd.*`) use a plain core NATS subscription. This is safe because command streams use WorkQueue retention — only the machine's execution consumer may bind as a JetStream consumer; a passive core subscription observes traffic without competing for deliveries.

Command **responses** (`puda.*.cmd.response.*`) use Telegraf's `jetstream_subjects` on the `RESPONSE_QUEUE` / `RESPONSE_IMMEDIATE` streams (interest retention), with explicit `jetstream_stream` bindings. Requires Telegraf **1.39+**. Telegraf calls nats.go `QueueSubscribe`; each non-empty `queue_group` is also the concrete durable consumer name. A clean unsubscribe can delete a consumer created by that process, while a crash can leave the durable cursor for the next process to resume. Response availability during a restart therefore depends on consumer provenance and interest-retention state; it is not an exactly-once guarantee.

ALD point and alarm inputs bind explicitly to `ALD_TELEMETRY`, with durable names `puda_logger_ald_points` and `puda_logger_ald_alarms`. The stream uses limits retention with file storage and retains up to 7 days or 10 GiB, whichever limit is reached first. `nats-stream-init` creates or updates all three streams and then verifies subjects, retention, file storage, replicas, and ALD limits from the server's JSON state. It uses the complete `NATS_SERVERS` list so the NATS client can fail over between configured servers. Any mismatch, authentication failure, or malformed state fails startup; the initializer does not suppress stream errors or receive InfluxDB credentials.

The retained ALD stream permits historical delivery and operational replay within the retention window. However, Telegraf still does not expose an application-controlled durable cursor for these inputs. Consumer recreation or an explicit replay can therefore deliver an ALD event more than once. InfluxDB identifies a point by measurement, tag set, and `event_time`; an identical replay at the same identity overwrites/merges that point, but `event_id` is only a field and the logger performs no separate deduplication. Delivery is **not exactly once**, and replays with changed timestamps or tags can create additional points.

**Known failure mode and recovery:** a JetStream push consumer can wedge with `num_ack_pending` pinned at `max_ack_pending`. `healthcheck.sh` queries all three required streams and all six concrete response/ALD consumers using the complete multi-server list. It strictly validates stream/consumer identity, durable and filter configuration, explicit acknowledgement policy, positive integer limits, integer pending counts, and active push binding. Missing, unqueryable, malformed, unbound, or saturated state makes the script terminate Telegraf PID 1; Docker's `restart: unless-stopped` then restarts the container. Raw broker output is never echoed. This avoids a privileged autoheal sidecar and does not mount `/var/run/docker.sock`.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `INFLUXDB_URL` | `http://localhost:8181` | InfluxDB 3 Core URL |
| `INFLUXDB_TOKEN_PATH` | — | Host path to the InfluxDB write-token file mounted owner-read-only; `INFLUXDB_TOKEN` in the process environment is rejected |
| `INFLUXDB_DATABASE` | `machines` | Target database shared with puda-grafana (written as `bucket` in `outputs.influxdb_v2`) |
| `INFLUXDB_ORGANIZATION` | `bears` | Organization name for `outputs.influxdb_v2` |
| `NATS_SERVERS` | — | Comma-separated NATS server URLs. URL userinfo, malformed authorities, and mixed encrypted/plain transports are rejected. |
| `NATS_CREDS_PATH` | — | Host path to a NATS 2 credentials file mounted as a Compose secret |
| `NATS_CREDS_FILE` | `/run/secrets/nats.creds` | Read-only credentials path inside both containers |
| `NATS_TLS_CA_PATH` / `NATS_TLS_CA_FILE` | — / `/run/secrets/nats-ca.pem` | Required production host/container CA bundle paths |
| `NATS_TLS_CERT_PATH` / `NATS_TLS_CERT_FILE` | — / `/run/secrets/nats-client-cert.pem` | Required production host/container client certificate paths |
| `NATS_TLS_KEY_PATH` / `NATS_TLS_KEY_FILE` | — / `/run/secrets/nats-client-key.pem` | Required production host/container client private-key paths |
| `NATS_TIMEOUT` | `5s` | Bounded NATS CLI request timeout |
| `NATS_STREAM_REPLICAS` | `1` | Required JetStream replica count; use `3` on a three-node production cluster |
| `NATS_ALLOW_UNAUTHENTICATED_LOOPBACK` | `0` | Test-only escape hatch; `1` is accepted only when every endpoint is `localhost`, `127.0.0.1`, or `[::1]` |

Production requires a readable, non-empty credentials file. Credentials are never placed in URL userinfo or command arguments. CA/certificate/key contents are referenced by file path, not copied into environment variables or the rendered Telegraf configuration. TLS verification is always enabled (`insecure_skip_verify = false`).

## Running

### Docker Compose (production)

```bash
cp .env.example .env
mkdir -p secrets
# Provision influxdb-write-token, nats.creds, nats-ca.pem,
# nats-client-cert.pem, and nats-client-key.pem.
chmod 0700 secrets
sudo chown "$(id -u):$(id -g)" secrets/*
chmod 0400 secrets/*
for secret in secrets/*; do
  test -f "$secret" && test ! -L "$secret" || exit 1
done
# In .env set PUDA_LOGGER_RUNTIME_UID=$(id -u),
# PUDA_LOGGER_RUNTIME_GID=$(id -g), PUDA_LOGGER_IMAGE_REPOSITORY,
# and its registry-resolved sha256 digest.
docker compose pull
docker compose up -d
```

Compose mounts the InfluxDB write token and each NATS secret separately with
read-only mode `0400`. Both services run as non-root UID/GID 999 with a
read-only root filesystem, all capabilities dropped, and
`no-new-privileges`. The logger renders its token-bearing config atomically
under `/tmp` with mode `0600`; scripts never print the config, credentials,
tokens, or raw broker state.

The `nats-stream-init` service ensures JetStream response streams and the bounded ALD telemetry stream exist, then starts the Telegraf-based logger.

**Config changes:** `telegraf.conf.template` is mounted read-only into the container. After editing it, restart without rebuilding:

```bash
docker compose restart influxdb-logger
```

### Explicit unauthenticated loopback test mode

The production Compose file always mounts credentials and TLS material; it does
not provide an unauthenticated deployment path. For a direct local test process
only, set `NATS_SERVERS=nats://127.0.0.1:4222`,
`NATS_ALLOW_UNAUTHENTICATED_LOOPBACK=1`, and leave all NATS credential/TLS
variables unset. Any non-loopback endpoint still fails closed. Do not use this
mode in production.

### Local (without Docker)

```bash
export NATS_SERVERS=tls://nats-1.example.internal:4222,tls://nats-2.example.internal:4222
export NATS_CREDS_FILE=/secure/path/nats.creds
export NATS_TLS_CA_FILE=/secure/path/nats-ca.pem
export NATS_TLS_CERT_FILE=/secure/path/nats-client-cert.pem
export NATS_TLS_KEY_FILE=/secure/path/nats-client-key.pem
export INFLUXDB_URL=http://localhost:8181
export INFLUXDB_TOKEN_FILE=/secure/path/influxdb-write-token
export INFLUXDB_DATABASE=machines
export INFLUXDB_ORGANIZATION=bears
export NATS_CONFIG_LIB="$PWD/nats-config.sh"
export TELEGRAF_CONFIG_TEMPLATE="$PWD/telegraf.conf.template"
./docker-entrypoint.sh
```

Requires `telegraf`, `nats`, `jq`, and `envsubst` (from `gettext`) on your PATH.

### Deterministic ALD assertions

The ALD stream options, Telegraf mappings, typed sample payloads, and active/recovered alarm examples can be checked without NATS or InfluxDB:

```bash
python3 test/assert-ald-config.py
```
