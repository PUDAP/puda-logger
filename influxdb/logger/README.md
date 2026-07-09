# influxdb-logger

Streams PUDA NATS machine traffic to InfluxDB using [Telegraf](https://docs.influxdata.com/telegraf/).

## Measurements

| Measurement | NATS subjects | Description |
|---|---|---|
| `commands` | `puda.*.cmd.*` (requests), `puda.*.cmd.response.*` (responses) | Command/response log |
| `telemetry` | `puda.*.tlm.health` | Machine health metrics (`cpu`, `mem`, `temp`) |

### `commands` fields and tags

| Kind | Name | Source |
|---|---|---|
| tag | `machine_id` | `header.machine_id` (commands); parsed from NATS subject (telemetry) |
| tag | `topic` | parsed from NATS subject (`immediate`, `queue`, `response.immediate`, `response.queue`) |
| tag | `cmd_name` | `command.name` |
| tag | `msg_type` | static (`command` or `response`) |
| tag | `status` | `sent` for requests; `data.response.status` for responses |
| tag | `username` | `header.username` |
| tag | `run_id` | `header.run_id` |
| tag | `subject` | NATS subject (added by Telegraf) |
| field | `user_id` | `header.user_id` |
| field | `step_number` | `command.step_number` |
| field | `cmd_version` | `command.version` |
| field | `response_code` | `data.response.code` (responses only) |
| field | `response_message` | `data.response.message` (responses only) |
| field | `completed_at` | `data.response.completed_at` (responses only) |

`topic` and `cmd_name` are tags (not fields) so points with the same header timestamp within a run do not overwrite each other in InfluxDB.

### `telemetry` fields and tags

| Kind | Name | Source |
|---|---|---|
| tag | `machine_id` | parsed from NATS subject |
| tag | `subject` | NATS subject (added by Telegraf) |
| field | `data` | full raw JSON payload from the machine |

Telemetry schemas differ per machine. The logger stores the entire payload as `data` rather than extracting machine-specific fields.

NATS messages use the raw `puda-comms` wire format (not the JSON envelope the old Python logger constructed internally).

## NATS subscription model

Command **requests** (`puda.*.cmd.*`) use a plain core NATS subscription. This is safe because command streams use WorkQueue retention — only the machine's execution consumer may bind as a JetStream consumer; a passive core subscription observes traffic without competing for deliveries.

Command **responses** (`puda.*.cmd.response.*`) use Telegraf's `jetstream_subjects` (ephemeral JetStream push consumer) on the `RESPONSE_QUEUE` / `RESPONSE_IMMEDIATE` streams (interest retention), with explicit `jetstream_stream` bindings. Requires Telegraf **1.39+**. A `nats-stream-init` compose service creates these streams idempotently on startup.

**Note:** Telegraf's `nats_consumer` plugin does not yet support durable JetStream consumers. Unlike the previous Python logger, responses published while Telegraf is offline/restarting may be missed.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `INFLUXDB_URL` | `http://localhost:8181` | InfluxDB 3 Core URL |
| `INFLUXDB_TOKEN` | — | Auth token |
| `INFLUXDB_DATABASE` | `puda` | Target database (written as `bucket` in `outputs.influxdb_v2`) |
| `INFLUXDB_ORGANIZATION` | `bears` | Organization name for `outputs.influxdb_v2` |
| `NATS_SERVERS` | — | Comma-separated NATS server URLs (`nats-stream-init` uses the first entry) |

## Running

### Docker Compose

```bash
cp .env.example .env   # edit with your values
docker compose up -d --build   # first run, or after Dockerfile/entrypoint changes
```

The `nats-stream-init` service ensures JetStream response streams exist, then starts the Telegraf-based logger.

**Config changes:** `telegraf.conf.template` is mounted into the container. After editing it, restart without rebuilding:

```bash
docker compose restart influxdb-logger
```

### Local (without Docker)

```bash
export NATS_SERVERS=nats://localhost:4222
export INFLUXDB_URL=http://localhost:8181
export INFLUXDB_TOKEN=token
export INFLUXDB_DATABASE=puda
export INFLUXDB_ORGANIZATION=bears
./docker-entrypoint.sh
```

Requires `telegraf` and `envsubst` (from `gettext`) on your PATH.
