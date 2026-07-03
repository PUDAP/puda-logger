# influxdb-logger

Streams PUDA NATS machine traffic and writes to InfluxDB.

## Measurements

| Measurement | Populated by | Description |
|---|---|---|
| `machine_status` | `puda.*.tlm.health` | Real-time status timeline per machine |
| `machine_commands` | `puda.*.cmd.queue`, `puda.*.cmd.immediate` (commands), `puda.*.cmd.response.queue`, `puda.*.cmd.response.immediate` (responses) | Command/response log |

Command *requests* are observed via a plain core NATS subscription (safe, since the underlying streams use WorkQueue retention where only the machine's own execution consumer may bind). Command *responses* are consumed via a **durable JetStream consumer** (`influxdb_logger_response_queue` / `influxdb_logger_response_immediate`) bound to the Interest-retention response streams, so a response published while the logger is offline/restarting is redelivered on reconnect instead of being silently lost — this matters most for queue commands, which can take minutes to complete.

## Configuration

| Variable | Default | Description |
|---|---|---|
| `INFLUXDB_URL` | `http://localhost:8181` | InfluxDB host URL |
| `INFLUXDB_TOKEN` | `apiv3_puda` | Auth token |
| `INFLUXDB_DATABASE` | `machines` | Target database |
| `INFLUXDB_WRITE_TIMEOUT_MS` | `5000` | Write timeout in milliseconds |
| `NATS_SERVERS` | `nats://localhost:4222,...` | Comma-separated NATS server URLs |
| `NATS_RECONNECT_WAIT_SECS` | `2` | Seconds between reconnect attempts |
| `HEALTH_STALL_TIMEOUT_SECS` | `90` | Exit if no health write for this long (0 = disabled) |
| `TLM_WRITE_INTERVAL_SECS` | `5` | Telemetry batch flush interval |
| `WRITE_QUEUE_MAXSIZE` | `10000` | Max queued messages before exit |
| `LOG_LEVEL` | `INFO` | Python log level |
| `OFFLINE_TIMEOUT_SECS` | `30` | Seconds of silence before marking a machine offline |

## Running

### Docker Compose

Create `admin-token.json` with your InfluxDB admin token before first start:

```bash
echo '{"token": "apiv3_puda", "name": "_admin", "description": "Preconfigured admin token for influxdb-logger"}' > ../influxdb/admin-token.json
docker compose up -d
```

The logger and InfluxDB will both start. InfluxDB data is persisted in the `influxdb_data` Docker volume. Adjust env vars via a `.env` file or inline overrides.

### Local

```bash
pip install .
python main.py
```
