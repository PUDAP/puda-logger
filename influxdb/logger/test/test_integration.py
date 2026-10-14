"""Opt-in Docker tests; use only isolated containers and temporary test keys."""

import asyncio
import base64
import gzip
import json
import os
import socket
import ssl
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import nats
import pytest
from puda import e2e

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DOCKER_TESTS") != "1", reason="set RUN_DOCKER_TESTS=1"
)


def docker(*args):
    return subprocess.check_output(
        ["docker", *args], text=True, stderr=subprocess.STDOUT
    ).strip()


async def eventually(predicate, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.2)
    raise AssertionError("Timed out waiting for integration condition")


@pytest.mark.parametrize("encrypted", [False, True], ids=["plaintext", "e2ee"])
async def test_container_pipeline(tmp_path, encrypted):
    image = os.getenv("LOGGER_TEST_IMAGE", "puda-influxdb-e2ee-test:local")
    suffix = uuid.uuid4().hex[:10]
    broker, logger = f"puda-test-nats-{suffix}", f"puda-test-logger-{suffix}"
    containers = []
    received = []
    failures = []
    accepting = threading.Event()
    accepting.set()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.headers.get("Transfer-Encoding") == "chunked":
                chunks = []
                while True:
                    size = int(self.rfile.readline().split(b";", 1)[0], 16)
                    if not size:
                        self.rfile.readline()
                        break
                    chunks.append(self.rfile.read(size))
                    self.rfile.read(2)
                body = b"".join(chunks)
            else:
                body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            if accepting.is_set():
                received.append(body.decode())
                self.send_response(204)
            else:
                failures.append(body.decode())
                self.send_response(503)
            self.end_headers()

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    nc = None
    try:
        broker_args = []
        tls_args = []
        logger_args = []
        compose_env = os.environ.copy()
        compose_env["PUDA_NATS_CA"] = ""
        compose_env["PUDA_E2E_KEY_FILE"] = ""
        key_path = tmp_path / "keys" / "e2e.key"
        original_key = b"a" * 32
        if encrypted:
            certs = tmp_path / "certs"
            certs.mkdir()
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-keyout",
                    str(certs / "server.key"),
                    "-out",
                    str(certs / "ca.pem"),
                    "-days",
                    "1",
                    "-subj",
                    "/CN=localhost",
                    "-addext",
                    "subjectAltName=DNS:localhost,IP:127.0.0.1",
                ],
                check=True,
                capture_output=True,
            )
            (certs / "server.key").chmod(0o644)  # Disposable test certificate only.
            key_path.parent.mkdir()
            key_path.write_text(
                "encrypt=" + base64.b64encode(original_key).decode() + "\n"
            )
            broker_args = ["-v", f"{certs}:/certs:ro"]
            tls_args = [
                "--tls",
                "--tlscert",
                "/certs/ca.pem",
                "--tlskey",
                "/certs/server.key",
            ]
            compose_env["PUDA_NATS_CA"] = str(certs / "ca.pem")
            compose_env["PUDA_E2E_KEY_FILE"] = str(key_path)
        # Exercise the real Compose host-path mappings without loading a user's .env.
        rendered = json.loads(
            subprocess.check_output(
                [
                    "docker",
                    "compose",
                    "--env-file",
                    ".env.example",
                    "-f",
                    "compose.yml",
                    "config",
                    "--no-env-resolution",
                    "--format",
                    "json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                env=compose_env,
                text=True,
            )
        )["services"]["influxdb-logger"]
        for name in ("PUDA_NATS_CA", "PUDA_E2E_KEY_FILE"):
            value = rendered["environment"][name]
            assert bool(value) == encrypted
            logger_args += ["-e", f"{name}={value}"]
        for mount in rendered["volumes"]:
            if mount["target"].startswith("/run/puda-"):
                assert mount["bind"]["create_host_path"] is False
                spec = f"type=bind,source={mount['source']},target={mount['target']}"
                if mount.get("read_only"):
                    spec += ",readonly"
                logger_args += ["--mount", spec]
        # Pin the selected host port: Docker reassigns anonymous ports on restart.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            broker_port = sock.getsockname()[1]
        docker(
            "run",
            "-d",
            "--name",
            broker,
            "-p",
            f"127.0.0.1:{broker_port}:4222",
            *broker_args,
            "nats:2.14.5",
            "-js",
            "-sd",
            "/data",
            *tls_args,
        )
        containers.append(broker)
        port = docker("port", broker, "4222/tcp").rsplit(":", 1)[1]
        server = f"{'tls' if encrypted else 'nats'}://127.0.0.1:{port}"
        options = (
            {"tls": ssl.create_default_context(cafile=str(certs / "ca.pem"))}
            if encrypted
            else {}
        )
        nc = await nats.connect(server, **options)
        js = nc.jetstream()
        docker(
            "run",
            "-d",
            "--name",
            logger,
            "--network",
            "host",
            "-e",
            f"NATS_SERVERS={server}",
            "-e",
            f"INFLUXDB_URL=http://127.0.0.1:{http.server_port}",
            "-e",
            "INFLUXDB_TOKEN=test-token",
            *logger_args,
            image,
        )
        containers.append(logger)
        await eventually(lambda: "Subscriber ready" in docker("logs", logger))
        e2e.set_ring(e2e.Ring(encrypt=original_key) if encrypted else None)

        async def publish(name):
            header = {
                "machine_id": "test-machine",
                "timestamp": "2026-07-07T09:41:42Z",
                "username": "ACME\\test user",
            }
            command = {"name": name, "step_number": 1, "version": "1.0"}
            data = {
                "header": header,
                "command": command,
                "response": {"status": "success", "message": 'ok "quoted"'},
            }
            for topic in (
                "cmd.queue",
                "cmd.immediate",
                "cmd.response.queue",
                "cmd.response.immediate",
            ):
                subject = "puda.test-machine." + topic
                payload = e2e.seal(subject, json.dumps(data).encode())
                if "response" in topic:
                    await js.publish(subject, payload)
                else:
                    await nc.publish(subject, payload)
            subject = "puda.test-machine.tlm.health"
            await nc.publish(
                subject,
                e2e.seal(
                    subject,
                    json.dumps(
                        {
                            "timestamp": "2026-07-07T09:41:42.123456789Z",
                            "cpu": 6.0,
                            "sample": name,
                        }
                    ).encode(),
                ),
            )
            await nc.flush()

        await publish("initial")
        await eventually(
            lambda: len([line for body in received for line in body.splitlines()]) >= 5
        )
        lines = [line for body in received for line in body.splitlines()]
        assert sum("cmd_name=initial" in line for line in lines) == 4
        assert any(
            line.startswith("telemetry,") and line.endswith("1783417302123456789")
            for line in lines
        )
        assert not any(
            "Error in plugin" in line for line in docker("logs", logger).splitlines()
        )

        # Invalid JSON and, in E2EE mode, plaintext must not become metrics.
        await nc.publish(
            "puda.test-machine.cmd.queue",
            e2e.seal("puda.test-machine.cmd.queue", b"not-json"),
        )
        await nc.flush()
        await eventually(lambda: "Invalid message" in docker("logs", logger))
        if encrypted:
            await nc.publish("puda.test-machine.cmd.queue", b'{"secret":"do-not-log"}')
            await nc.flush()
            await eventually(lambda: "Cannot decrypt" in docker("logs", logger))
            assert "do-not-log" not in docker("logs", logger)

            # Rotate using the real SDK frame and KV AAD, then verify persistence.
            kv = await js.create_key_value(bucket=e2e.KEYRING_BUCKET)
            next_key = b"b" * 32
            record = {
                "new_key": base64.b64encode(next_key).decode(),
                "old_key": base64.b64encode(original_key).decode(),
                "encrypt_kid": e2e.kid_hex(e2e.kid_for(next_key)),
            }
            sealed = e2e.seal(
                e2e.kv_aad(e2e.KEYRING_BUCKET, e2e.KEYRING_KEY),
                json.dumps(record).encode(),
            )
            await kv.put(e2e.KEYRING_KEY, sealed)
            await eventually(
                lambda: base64.b64encode(next_key).decode() in key_path.read_text()
            )
            e2e.set_ring(e2e.Ring(encrypt=next_key, previous=original_key))
            await publish("rotated")
            await eventually(
                lambda: any("cmd_name=rotated" in body for body in received)
            )
            await kv.delete(e2e.KEYRING_KEY)
            await eventually(lambda: "previous=" not in key_path.read_text())

        # Telegraf buffers while the Influx HTTP endpoint fails and retries afterwards.
        accepting.clear()
        await publish("outage")
        await eventually(lambda: any("cmd_name=outage" in body for body in failures))
        accepting.set()
        await eventually(lambda: any("cmd_name=outage" in body for body in received))

        docker("restart", broker)
        await eventually(lambda: "NATS reconnected" in docker("logs", logger))
        await eventually(lambda: nc.is_connected)
        await publish("reconnected")
        await eventually(
            lambda: any("cmd_name=reconnected" in body for body in received)
        )

        # Kill only this test container's subscriber; execd must start it again.
        docker(
            "exec",
            logger,
            "/opt/puda/bin/python",
            "-c",
            "import os,pathlib,signal; "
            "pids=[p for p in pathlib.Path('/proc').iterdir() if p.name.isdigit()]; "
            "[(os.kill(int(p.name),signal.SIGKILL)) for p in pids "
            "if int(p.name)!=os.getpid() and "
            "b'/opt/puda/subscriber.py' in (p/'cmdline').read_bytes().split(b'\\0')]",
        )
        await eventually(lambda: docker("logs", logger).count("Subscriber ready") >= 2)
        await publish("restarted")
        await eventually(lambda: any("cmd_name=restarted" in body for body in received))
    finally:
        if nc is not None:
            await nc.close()
        for name in reversed(containers):
            logs = docker("logs", name)
            print(f"\n{name}:\n{logs}")
            docker("rm", "-f", name)
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)
        e2e.reset()
