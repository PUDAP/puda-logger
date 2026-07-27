# InfluxDB

InfluxDB 3 Core server (`server/`) and Telegraf logger (`logger/`) for PUDA.

The server boots with a preconfigured admin token from a JSON file mounted at `/run/secrets/admin-token`. See `logger/README.md` for the NATS → InfluxDB logger.

## Creating the admin-token file

In InfluxDB 3, `INFLUXDB3_ADMIN_TOKEN_FILE` expects a JSON-formatted file containing token metadata.

`server/compose.yml` bind-mounts `./admin-token.json` to `/run/secrets/admin-token`. Create that file under `server/` before starting the stack.

### Option A: Create the JSON file manually

Create `server/admin-token.json`:

```json
{
  "token": "apiv3_YOUR_GENERATED_SECRET_TOKEN_HERE",
  "name": "admin",
  "description": "Preconfigured bootstrap admin token"
}
```

**Token string rules:**

- Must begin with the `apiv3_` prefix.
- The remainder should be a cryptographically secure random string (at least 16 bytes / 128 bits of entropy):

  ```bash
  echo "apiv3_$(openssl rand -hex 16)"
  ```
- Restrict file permissions locally:

```bash
chmod 644 server/admin-token.json
```

### Option B: Generate automatically via CLI

If you have the `influxdb3` CLI installed, generate a pre-formatted offline JSON file:

```bash
influxdb3 create token --admin \
  --name "admin" \
  --offline \
  --output-file ./server/admin-token.json
```

## What to set for `INFLUXDB_TOKEN`

The `<token>` value in `INFLUXDB_TOKEN` (or `INFLUXDB3_AUTH_TOKEN` for native InfluxDB 3 CLI tools) must be the exact raw token string defined inside your admin-token JSON file.

**Example:** if your JSON file contains:

```json
"token": "apiv3_x9K8mL2pQ5rT7vW1zY3aB5cD7eF9gH0i"
```

then set:

```bash
INFLUXDB_TOKEN=apiv3_x9K8mL2pQ5rT7vW1zY3aB5cD7eF9gH0i
```

Copy `server/.env.example` → `server/.env` (and `logger/.env.example` → `logger/.env`) and replace `<token>` with that same string. The server healthcheck and the Telegraf logger both use `INFLUXDB_TOKEN`.

## Running the server

```bash
cd server
cp .env.example .env          # set INFLUXDB_TOKEN to match admin-token.json
# create admin-token.json (Option A or B above)
docker compose up -d
```
