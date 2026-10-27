"""Opt-in, disposable local Docker test. Never reads .env or lab endpoints."""

import asyncio
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid

import nats
from nats.js.api import RetentionPolicy, StreamConfig
import pytest

from storage import ClickHouse, Config
from subscriber import RESPONSE_CONSUMERS
from test_logger import command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DOCKER_INTEGRATION") != "1",
        reason="set RUN_DOCKER_INTEGRATION=1 for disposable Docker services",
    ),
]
ROOT = Path(__file__).resolve().parents[2]


def docker(*args, **kwargs):
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=120, **kwargs
    )
    if result.returncode:
        # Never include command/env or container inspect output (credentials).
        raise RuntimeError("Docker command failed: " + result.stderr[:500])
    return result.stdout.strip()


@pytest.fixture
def services(tmp_path):
    docker("version", "--format", "{{.Server.Version}}")
    prefix = "puda-ch-test-" + uuid.uuid4().hex[:12]
    network, ch, broker = prefix + "-net", prefix + "-db", prefix + "-nats"
    password = secrets.token_hex(32)
    env = dict(os.environ, CLICKHOUSE_PASSWORD=password)
    image = next(
        line.split("image:", 1)[1].strip()
        for line in (ROOT / "server/compose.yml").read_text().splitlines()
        if "image:" in line
    )
    try:
        docker("network", "create", network)
        docker(
            "run",
            "--detach",
            "--name",
            ch,
            "--network",
            network,
            "--publish",
            "127.0.0.1::8123",
            "--ulimit",
            "nofile=262144:262144",
            "-e",
            "CLICKHOUSE_USER=test",
            "-e",
            "CLICKHOUSE_PASSWORD",
            "-e",
            "CLICKHOUSE_DB=puda",
            "--mount",
            f"type=bind,src={ROOT / 'server/initdb'},dst=/docker-entrypoint-initdb.d,readonly",
            "--mount",
            f"type=bind,src={ROOT / 'server/config.d/resources.xml'},dst=/etc/clickhouse-server/config.d/puda.xml,readonly",
            image,
            env=env,
        )
        docker(
            "run",
            "--detach",
            "--name",
            broker,
            "--network",
            network,
            "--publish",
            "127.0.0.1::4222",
            "nats:2.12-alpine",
            "-js",
        )
        ch_port = int(docker("port", ch, "8123/tcp").rsplit(":", 1)[1])
        nats_port = int(docker("port", broker, "4222/tcp").rsplit(":", 1)[1])
        config = Config(
            [f"nats://127.0.0.1:{nats_port}"],
            f"http://127.0.0.1:{ch_port}",
            "test",
            password,
            batch_interval=0.05,
        )
        client = ClickHouse(config)
        deadline = time.monotonic() + 90
        while True:
            try:
                client.check()
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Disposable ClickHouse did not become ready"
                    ) from None
                time.sleep(0.5)
        yield config, client
    finally:
        # Names are random and belong exclusively to this fixture. -v removes its anonymous volumes.
        for name in (ch, broker):
            subprocess.run(
                ["docker", "rm", "--force", "--volumes", name],
                capture_output=True,
                timeout=30,
            )
        subprocess.run(
            ["docker", "network", "rm", network], capture_output=True, timeout=30
        )


async def eventually(predicate, timeout=20):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("Timed out waiting for isolated integration condition")


@pytest.mark.asyncio
@pytest.mark.parametrize("retention", [RetentionPolicy.LIMITS, RetentionPolicy.INTEREST])
async def test_real_nats_clickhouse_delivery_failure_and_isolation(services, tmp_path, retention):
    config, db = services
    # Prove the HTTP endpoint cannot execute queries without authentication.
    from urllib.error import HTTPError
    from urllib.request import ProxyHandler, Request, build_opener

    with pytest.raises(HTTPError) as denied:
        build_opener(ProxyHandler({})).open(
            Request(config.url, data=b"SELECT 1"), timeout=5
        )
    assert denied.value.code in (401, 403)
    nc = await nats.connect(servers=config.servers)
    js = nc.jetstream()
    await js.add_stream(
        config=StreamConfig(
            name="MACHINE_COMMANDS",
            subjects=["puda.*.cmd.queue"],
            retention=RetentionPolicy.WORK_QUEUE,
        )
    )
    machine = await js.pull_subscribe(
        "puda.*.cmd.queue", durable="machine", stream="MACHINE_COMMANDS"
    )
    # A separate backend should receive every same core message, not compete with ClickHouse.
    other = []

    async def observe(msg):
        other.append(msg.subject)

    for subject in [
        "puda.*.cmd.queue",
        "puda.*.cmd.immediate",
        "puda.*.tlm.health",
        "puda.*.tlm.stream.>",
    ]:
        await nc.subscribe(subject, queue="puda_logger_telemetry", cb=observe)
    # Preserve an existing response stream's retention configuration.
    for stream, subject in [
        ("RESPONSE_QUEUE", "puda.*.cmd.response.queue"),
        ("RESPONSE_IMMEDIATE", "puda.*.cmd.response.immediate"),
    ]:
        await js.add_stream(
            config=StreamConfig(name=stream, subjects=[subject], max_age=7200, retention=retention)
        )
    influx = await js.pull_subscribe(
        "puda.*.cmd.response.queue",
        durable="puda_logger_cmd_responses_queue",
        stream="RESPONSE_QUEUE",
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("CLICKHOUSE_", "NATS_", "PUDA_", "JS_", "BATCH_"))
    }
    env.update(
        NATS_SERVERS=",".join(config.servers),
        CLICKHOUSE_URL=config.url,
        CLICKHOUSE_USER=config.user,
        CLICKHOUSE_PASSWORD=config.password,
        BATCH_INTERVAL="0.05",
    )
    log = tmp_path / "logger.log"
    with log.open("wb") as output:
        proc = subprocess.Popen(
            [sys.executable, "subscriber.py"],
            cwd=ROOT / "logger",
            env=env,
            stdout=output,
            stderr=output,
        )

    async def query(sql):
        return (await asyncio.to_thread(db.request, sql)).decode().strip()

    async def ready():
        assert proc.poll() is None, "Logger exited before readiness"
        return "ClickHouse logger ready" in log.read_text()

    try:
        await eventually(ready)
        assert (await js.stream_info("RESPONSE_QUEUE")).config.max_age == 7200
        raw = json.dumps(command(), indent=2).encode()
        subjects = [
            "cmd.queue",
            "cmd.immediate",
            "cmd.response.queue",
            "cmd.response.immediate",
            "tlm.health",
            "tlm.stream.position",
        ]
        for suffix in subjects:
            data = (
                raw
                if suffix.startswith("cmd")
                else b'{"timestamp":"2026-07-07T09:41:42.123456789Z","cpu":3.5,"x":1}'
            )
            await nc.publish("puda.robot." + suffix, data)
        await nc.flush()

        async def six_rows():
            return await query("SELECT count() FROM puda.events") == "6"

        await eventually(six_rows)
        assert len(other) == 4
        requests = await machine.fetch(1, timeout=3)
        assert requests[0].data == raw
        await requests[0].ack()
        responses = await influx.fetch(1, timeout=3)
        assert responses[0].data == raw
        await responses[0].ack()
        stored = json.loads(
            await query(
                "SELECT event_ns, toUnixTimestamp64Nano(event_time) AS ns, raw_json FROM puda.events WHERE msg_type='response' LIMIT 1 FORMAT JSONEachRow"
            )
        )
        assert int(stored["event_ns"]) == 1783417302123456789
        assert int(stored["ns"]) == 1783417302123456789
        assert stored["raw_json"] == raw.decode()
        assert await query("SELECT uniqExact(msg_type) FROM puda.events") == "4"
        # Real database insert failure must leave a response pending, then redeliver after repair.
        await query("RENAME TABLE puda.events TO puda.events_off")
        outage = command()
        outage["header"]["run_id"] = "outage"
        await nc.publish("puda.robot.cmd.response.queue", json.dumps(outage).encode())
        await nc.flush()

        async def unacked():
            info = await js.consumer_info(
                "RESPONSE_QUEUE", RESPONSE_CONSUMERS["RESPONSE_QUEUE"]
            )
            return info.num_ack_pending > 0

        await eventually(unacked)
        assert (
            await query("SELECT count() FROM puda.events_off WHERE run_id='outage'")
            == "0"
        )
        await query("RENAME TABLE puda.events_off TO puda.events")

        async def replayed():
            return (
                await query("SELECT count() FROM puda.events WHERE run_id='outage'")
                == "1"
            )

        await eventually(replayed)
        await nc.publish("puda.robot.cmd.response.queue", b'{"invalid-secret":')
        await nc.flush()

        async def settled():
            info = await js.consumer_info(
                "RESPONSE_QUEUE", RESPONSE_CONSUMERS["RESPONSE_QUEUE"]
            )
            return info.num_ack_pending == 0 and info.num_pending == 0

        await eventually(settled)
        assert await query("SELECT count() FROM puda.events") == "7"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        await nc.close()
    assert proc.returncode == 0
    assert (
        config.password not in log.read_text()
        and "invalid-secret" not in log.read_text()
    )
