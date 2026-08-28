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

Command **responses** (`puda.*.cmd.response.*`) use Telegraf's `jetstream_subjects` (ephemeral JetStream push consumer) on the `RESPONSE_QUEUE` / `RESPONSE_IMMEDIATE` streams (interest retention), with explicit `jetstream_stream` bindings. Requires Telegraf **1.39+**. A `nats-stream-init` compose service creates these streams idempotently on startup.

**Note:** Telegraf's `nats_consumer` plugin does not yet support durable JetStream consumers. Unlike the previous Python logger, responses published while Telegraf is offline/restarting may be missed.

**Known failure mode:** the response consumers can silently wedge - `num_ack_pending` gets pinned at `max_ack_pending` (default 1000), NATS stops pushing new response messages to Telegraf, and nothing is logged to indicate why. The container keeps running and command *requests* (plain core NATS subscription) keep flowing normally, so this is easy to miss: `commands` measurement rows with `msg_type="response"` simply stop appearing. Restarting the container clears it. A `HEALTHCHECK` (`healthcheck.sh`) detects this by checking whether either durable consumer's ack window is fully saturated, and the `autoheal` sidecar in `compose.yml` restarts the container automatically when that happens.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `INFLUXDB_URL` | `http://localhost:8181` | InfluxDB 3 Core URL |
| `INFLUXDB_TOKEN` | — | Auth token |
| `INFLUXDB_DATABASE` | `puda` | Target database (written as `bucket` in `outputs.influxdb_v2`) |
| `INFLUXDB_ORGANIZATION` | `bears` | Organization name for `outputs.influxdb_v2` |
| `NATS_SERVERS` | — | Comma-separated NATS server URLs (`nats-stream-init` uses the first entry) |
| `LOGGER_REPLICAS` | `1` | Telegraf replicas. Queue groups split work so each NATS message is written once. Start at **1 replica per 10–20k machines**; raise if Telegraf CPU is pegged or logs show `metric buffer overflow` / discarded metrics (`metric_buffer_limit`) |

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
