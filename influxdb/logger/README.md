# influxdb-logger

Streams PUDA NATS machine traffic to InfluxDB using [Telegraf](https://docs.influxdata.com/telegraf/).

## Measurements

| Measurement | NATS subjects | Description |
|---|---|---|
| `commands` | `puda.*.cmd.*` (requests), `puda.*.cmd.response.*` (responses) | Command/response log |
| `telemetry` | `puda.*.tlm.health` | Machine health metrics (`cpu`, `mem`, `temp`), stored as a **60s mean** per machine |

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
| field | `response_message` | `response.message`, or `response.data` when message is empty (responses only) |
| field | `completed_at` | `response.completed_at` (responses only) |

`topic` and `cmd_name` are tags (not fields) so points with the same header timestamp within a run do not overwrite each other in InfluxDB.

### `telemetry` fields and tags

Health is published at 1 Hz on NATS. Telegraf keeps that live subscription but **writes one aggregated point per machine per 60s** (`aggregators.mean`, `drop_original = true`). Field names stay `cpu` / `mem` / `temp`; values are the window mean. Grafana queries do not need to change, but resolution is 1 minute and points appear shortly after each window closes.

| Kind | Name | Source |
|---|---|---|
| tag | `machine_id` | parsed from NATS subject |
| tag | `subject` | NATS subject (added by Telegraf) |
| tag | `status` | `status` (string, when present). A tag so it survives mean aggregation; a status change inside the window emits one point per status |
| field | `cpu` | 60s mean of `cpu` (float, when present) |
| field | `mem` | 60s mean of `mem` (float, when present) |
| field | `temp` | 60s mean of `temp` (float, when present; omitted if JSON `null`) |
| field | `connected` | 60s mean of `connected` as `0.0` / `1.0` (fraction of samples that were true) |
| field | `data` | raw JSON, **only** if none of the fields above were present (not aggregated; dropped with the 1 Hz originals) |

Telemetry schemas differ per machine. Shared scalars are extracted so Grafana can chart them without storing a duplicate JSON blob on every 1 Hz health ping. Machine-specific extras (Opentrons `run_data`, balance `latest_mass_g`, camera URLs) are not stored.

NATS messages use the raw `puda-comms` wire format (not the JSON envelope the old Python logger constructed internally).

## NATS subscription model

Command **requests** (`puda.*.cmd.*`) use a plain core NATS subscription. This is safe because command streams use WorkQueue retention — only the machine's execution consumer may bind as a JetStream consumer; a passive core subscription observes traffic without competing for deliveries.

Command **responses** (`puda.*.cmd.response.*`) use Telegraf `jetstream_subjects` bound to **durable push queue consumers** on `RESPONSE_QUEUE` / `RESPONSE_IMMEDIATE`. Those streams use **limits** retention (24h) so messages survive a logger restart. `nats-stream-init` creates the streams and the durables (`puda_logger_cmd_responses_queue` / `puda_logger_cmd_responses_immediate`) before Telegraf starts. Telegraf's `queue_group` is that durable name; because Telegraf did not create the consumer, Unsubscribe on shutdown does not delete it.

Limits, not workqueue: the CLI, postgres-logger, reductstore-logger, and this logger each need a copy of every response. WorkQueue would deliver each message to only one of them.

`JS_MAX_ACK_PENDING` (default 20000) is set on the durable (`max_ack_pending`) and in Telegraf (`max_undelivered_messages`). The old default of 1000 pins the ack window under burst and NATS stops pushing — responses silently stop while command *requests* keep flowing. Healthcheck + autoheal still restart a replica if the window saturates.

Requires Telegraf **1.39+**. After pulling this change, recreate the init job so existing streams/consumers are updated:

```bash
docker compose up -d --force-recreate nats-stream-init
docker compose up -d --force-recreate influxdb-logger
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `INFLUXDB_URL` | `http://localhost:8181` | InfluxDB 3 Core URL |
| `INFLUXDB_TOKEN` | — | Auth token |
| `INFLUXDB_DATABASE` | `puda` | Target database (written as `bucket` in `outputs.influxdb_v2`) |
| `INFLUXDB_ORGANIZATION` | `bears` | Organization name for `outputs.influxdb_v2` |
| `NATS_SERVERS` | — | Comma-separated NATS server URLs (`nats-stream-init` uses the first entry) |
| `LOGGER_REPLICAS` | `1` | Telegraf replicas. Queue groups split work so each NATS message is written once. Start at **1 replica per 10–20k machines**; raise if Telegraf CPU is pegged or logs show `metric buffer overflow` / discarded metrics (`metric_buffer_limit`) |
| `JS_MAX_ACK_PENDING` | `20000` | JetStream `max_ack_pending` and Telegraf `max_undelivered_messages` for response consumers |

## Running

### Docker Compose

```bash
cp .env.example .env   # edit with your values
docker compose up -d --build   # first run, or after Dockerfile/entrypoint changes
```

The `nats-stream-init` service ensures JetStream response streams exist, then starts the Telegraf-based logger. `LOGGER_REPLICAS` in `.env` sets how many logger containers Compose runs (`scale`). All replicas join the same NATS queue groups, so ingest is partitioned and Influx does not get duplicate points.

If you previously ran a container named `influxdb-logger`, remove it before scaling so it does not keep consuming as an extra queue member: `docker rm -f influxdb-logger`.

**Sizing:** start at **1 replica per 10–20k machines**. Tune from Telegraf CPU and buffer drops — if a replica is at ~100% CPU, or logs contain `metric buffer overflow` / discarded metrics (`metric_buffer_limit` is 100000), add a replica. Extra replicas on a saturated host do not help; spread them across hosts (same `.env` queue groups) if one box is out of CPU.

With more than one replica, the 60s telemetry mean is computed on the health pings that replica received. Grafana queries should `mean(cpu)` (and the other scalars) grouped by `machine_id`.

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
