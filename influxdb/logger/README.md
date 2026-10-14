# influxdb-logger

Streams PUDA NATS machine traffic to InfluxDB using a Python subscriber and
[Telegraf](https://docs.influxdata.com/telegraf/). The subscriber decrypts messages
when configured, maps them to Influx line protocol, and writes to Telegraf's
`inputs.execd` over a local pipe. Telegraf buffers, batches, and writes to InfluxDB.

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
| tag | `status` | `sent` for requests; `response.status` for responses |
| tag | `username` | `header.username` |
| tag | `run_id` | `header.run_id` |
| tag | `subject` | NATS subject (added by the subscriber) |
| field | `user_id` | `header.user_id` |
| field | `step_number` | `command.step_number` |
| field | `cmd_version` | `command.version` |
| field | `response_code` | `response.code` (responses only; stored as a string) |
| field | `response_message` | `response.message` (responses only) |
| field | `completed_at` | `response.completed_at` (responses only) |

`topic` and `cmd_name` are tags (not fields) so points with the same header timestamp within a run do not overwrite each other in InfluxDB.

### `telemetry` fields and tags

| Kind | Name | Source |
|---|---|---|
| tag | `machine_id` | parsed from NATS subject |
| tag | `subject` | NATS subject (added by the subscriber) |
| field | `data` | full raw JSON payload from the machine |

Telemetry schemas differ per machine. The logger stores the entire payload as `data` rather than extracting machine-specific fields.

NATS messages use the raw PUDA SDK wire format, not the envelope constructed by
the old Python logger. Command timestamps come from `header.timestamp`; telemetry
uses `timestamp` when present, otherwise receipt time. Nanoseconds are preserved.

## NATS subscription model

Command **requests** (`puda.*.cmd.queue` and `puda.*.cmd.immediate`) use core NATS
subscriptions. They observe traffic without binding to or acknowledging the
machines' WorkQueue consumers. Health telemetry also uses core NATS.

Command **responses** use ephemeral JetStream consumers on `RESPONSE_QUEUE` and
`RESPONSE_IMMEDIATE` with interest retention. The subscriber creates missing
response streams and validates existing ones without rewriting them. It uses the
same TLS settings for initialization and subscriptions; no separate init service
is needed. Consumers start with new messages only.

Responses are acknowledged only after a metric is written and flushed to stdout.
This confirms pipe handoff, **not an InfluxDB write**. Crashes or exhausted Telegraf
buffers can lose data. Requests and telemetry are live-only; responses published
while the logger is offline/restarting may also be missed. This is not a lossless
audit log. Run one logger instance per destination to avoid duplicate responses.

Decryption failures are logged without payloads or keys. Responses are negatively
acknowledged with a five-second delay (at most five deliveries); malformed JSON or
schema errors terminate delivery. Core messages cannot be replayed. NATS pending
queues and the Python queue are bounded; slow-consumer errors are logged. Telegraf
restarts a failed subscriber after ten seconds.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `INFLUXDB_URL` | `http://localhost:8181` | InfluxDB 3 Core URL |
| `INFLUXDB_TOKEN` | — | Auth token |
| `INFLUXDB_DATABASE` | `puda` | Target database (written as `bucket` in `outputs.influxdb_v2`) |
| `INFLUXDB_ORGANIZATION` | `bears` | Organization name for `outputs.influxdb_v2` |
| `NATS_SERVERS` | — | Comma-separated NATS server URLs |
| `PUDA_NATS_CA` | unset | Optional TLS CA file path; requires the key file variable too |
| `PUDA_E2E_KEY_FILE` | unset | Optional shared PUDA key file path; requires the CA variable too |

There is no E2EE boolean. If both optional variables are unset or empty, use
`nats://` URLs and plaintext payloads. If both are set, use `tls://` URLs and
encrypted payloads. Setting only one, mixing URL schemes, or supplying missing or
invalid files fails startup. Encryption never silently falls back to plaintext.

The logger pins the E2EE-capable PUDA SDK `0.0.19rc1` and reuses its framing,
subject authentication, and keyring functions. It polls `E2E_KEYRING/current`
every two seconds and refreshes again on a decryption failure. The shared key file
must be writable so rotated keys persist across restarts. Its runtime directory
must also be writable for the SDK's `.id` metadata. Give the logger its own copy
of the lab key file.

## Running

### Docker Compose

```bash
cp .env.example .env   # edit with your values
docker compose up -d --build   # first run, or after Dockerfile/entrypoint changes
```

The same Compose file supports both modes. For plaintext, leave both optional
variables unset; no certificate or key files are needed.

For E2EE, set `.env` to your TLS broker URLs and the **local machine paths** of the
broker CA and a copy of the shared lab key:

```dotenv
NATS_SERVERS=tls://your-broker:4222
PUDA_NATS_CA=/path/to/ca.pem
PUDA_E2E_KEY_FILE=/path/to/e2e.key
```

The key file contains `encrypt=<base64-encoded 32-byte key>`. Restrict its permissions
to the service user (for example, `chmod 600 /path/to/e2e.key`). Then run:

```bash
docker compose up -d --build
```

Compose mounts the CA read-only and the key file writable, and automatically
replaces the environment values with the corresponding container paths. Key
rotation updates the local key file in place. The SDK's `.id` metadata stays in
the container; this logger does not use it for registration. Missing source paths
fail instead of being created as directories. For plaintext mode, Compose uses
unused `/dev/null` mounts and passes empty environment values.

**Config changes:** `telegraf.conf.template` is mounted into the container. After editing it, restart without rebuilding:

```bash
docker compose restart influxdb-logger
```

### Local (without Docker)

Install Python 3.11+, `uv`, and Telegraf. Run `uv sync --frozen`, export your
environment variables, and configure Telegraf's `inputs.execd.command` with the
absolute paths to `.venv/bin/python` and `subscriber.py`. Use `signal = "none"`
and `data_format = "influx"`. Render the InfluxDB environment placeholders in the
template with `envsubst`, then run Telegraf with that configuration. For E2EE,
use the same local CA and key file paths as in the Docker setup.

To inspect the subscriber's line protocol directly:

```bash
NATS_SERVERS=nats://localhost:4222 uv run python subscriber.py
```

## Development and tests

```bash
uv sync --frozen
uv run pytest -q
docker build -t puda-influxdb-e2ee-test:local .
RUN_DOCKER_TESTS=1 uv run pytest -q test/test_integration.py
```

Docker tests create isolated NATS and logger containers, disposable TLS/key files,
and a local Influx-compatible HTTP test endpoint. They cover plaintext and E2EE,
field mappings, rotation, NATS reconnection, subscriber restarts, and output outage
recovery. They remove their containers afterwards. Requires Docker and OpenSSL.

Docker and local development both install from `pyproject.toml` and `uv.lock`.
The Docker build uses `uv sync --frozen --no-dev`. After changing dependencies,
update the lockfile:

```bash
uv lock
```
