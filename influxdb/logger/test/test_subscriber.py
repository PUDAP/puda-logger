import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from puda import e2e

from subscriber import Config, Subscriber, duration_seconds, timestamp_ns, to_line


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (
        "NATS_SERVERS",
        "PUDA_NATS_CA",
        "PUDA_E2E_KEY_FILE",
        "JS_MAX_ACK_PENDING",
        "JS_RESPONSE_MAX_AGE",
    ):
        monkeypatch.delenv(name, raising=False)
    e2e.reset()
    yield
    e2e.reset()


@pytest.fixture
def command():
    return {
        "header": {
            "machine_id": "first",
            "timestamp": "2026-07-07T09:41:42Z",
            "username": "user",
            "run_id": "run-1",
            "user_id": "user-1",
        },
        "command": {"name": "home", "step_number": 1, "version": "1.0"},
        "response": {
            "status": "success",
            "code": 200,
            "message": 'done "ok"',
            "completed_at": "2026-07-07T09:41:43Z",
        },
    }


def test_plaintext_config(monkeypatch):
    monkeypatch.setenv("NATS_SERVERS", " nats://one:4222, ,nats://two:4222 ")
    monkeypatch.setenv("PUDA_NATS_CA", " ")
    monkeypatch.setenv("PUDA_E2E_KEY_FILE", "")
    config = Config.from_env()
    assert not config.encrypted
    assert config.servers == ["nats://one:4222", "nats://two:4222"]
    assert config.connect_options == {}
    assert not e2e.enabled()


@pytest.mark.parametrize("variable", ["PUDA_NATS_CA", "PUDA_E2E_KEY_FILE"])
def test_partial_config_rejected(monkeypatch, variable):
    monkeypatch.setenv("NATS_SERVERS", "nats://one:4222")
    monkeypatch.setenv(variable, "/missing")
    with pytest.raises(ValueError, match="Set both"):
        Config.from_env()


@pytest.mark.parametrize(
    "servers", ["", "tls://one:4222", "nats://one:4222,tls://two:4222"]
)
def test_plaintext_invalid_servers(monkeypatch, servers):
    monkeypatch.setenv("NATS_SERVERS", servers)
    with pytest.raises(ValueError):
        Config.from_env()


@pytest.mark.parametrize(
    "key_text", ["", "encrypt=not-a-key", "encrypt=TOP_SECRET_BAD_KEY"]
)
def test_bad_key_rejected_without_leak(monkeypatch, tmp_path, key_text):
    ca, key = tmp_path / "ca.pem", tmp_path / "e2e.key"
    ca.write_text("placeholder")
    key.write_text(key_text)
    monkeypatch.setenv("NATS_SERVERS", "tls://localhost:4222")
    monkeypatch.setenv("PUDA_NATS_CA", str(ca))
    monkeypatch.setenv("PUDA_E2E_KEY_FILE", str(key))
    with pytest.raises(ValueError, match="valid encrypt key") as error:
        Config.from_env()
    assert "TOP_SECRET" not in str(error.value)


def test_e2ee_config_requires_files_and_tls(monkeypatch, tmp_path):
    monkeypatch.setenv("NATS_SERVERS", "tls://localhost:4222")
    monkeypatch.setenv("PUDA_NATS_CA", str(tmp_path / "missing"))
    monkeypatch.setenv("PUDA_E2E_KEY_FILE", str(tmp_path / "missing-key"))
    with pytest.raises(ValueError, match="existing files"):
        Config.from_env()
    monkeypatch.setenv("NATS_SERVERS", "nats://localhost:4222")
    with pytest.raises(ValueError, match="tls://"):
        Config.from_env()


def test_e2ee_valid_key_bad_ca(monkeypatch, tmp_path):
    ca, key = tmp_path / "ca.pem", tmp_path / "e2e.key"
    ca.write_text("invalid certificate")
    key.write_text("encrypt=" + base64.b64encode(b"a" * 32).decode())
    monkeypatch.setenv("NATS_SERVERS", "tls://localhost:4222")
    monkeypatch.setenv("PUDA_NATS_CA", str(ca))
    monkeypatch.setenv("PUDA_E2E_KEY_FILE", str(key))
    with pytest.raises(ValueError, match="TLS CA"):
        Config.from_env()


def test_command_schema(command):
    line = to_line("puda.first.cmd.queue", json.dumps(command).encode())
    assert line == (
        "commands,cmd_name=home,machine_id=first,msg_type=command,"
        "status=sent,subject=puda.first.cmd.queue,topic=queue "
        'cmd_version="1.0",run_id="run-1",step_number=1i,user_id="user-1",username="user" 1783417302000000000'
    )


def test_response_schema(command):
    line = to_line("puda.first.cmd.response.immediate", json.dumps(command).encode())
    assert "msg_type=response" in line
    assert "topic=response.immediate" in line
    assert 'response_code="200"' in line
    assert 'response_message="done \\"ok\\""' in line
    assert "status=success" in line


def test_health_scalars_and_nanosecond_time():
    raw = b'{"timestamp":"2026-07-07T09:41:42.123456789Z","cpu":6.0}'
    line = to_line("puda.first.tlm.health", raw)
    assert line.startswith(
        "telemetry,machine_id=first,subject=puda.first.tlm.health cpu=6.0 "
    )
    assert "data=" not in line
    assert line.endswith("1783417302123456789")
    assert timestamp_ns("2026-07-07T17:41:42.123456789+08:00") == 1783417302123456789


def test_escaping_and_null_fields(command):
    command["command"]["name"] = "move = x,y"
    command["header"]["username"] = "ACME\\user"
    command["response"]["message"] = "line1\nline2\\end"
    command["response"]["code"] = None
    line = to_line("puda.first.cmd.response.queue", json.dumps(command).encode())
    assert "cmd_name=move\\ \\=\\ x\\,y" in line
    assert "\n" not in line
    assert "response_code=" not in line
    assert 'username="ACME\\\\user"' in line


def test_health_scalars_status_and_fallback():
    line = to_line(
        "puda.first.tlm.health",
        b'{"cpu":2,"mem":4,"temp":null,"connected":true,"status":"ok"}',
    )
    assert "connected=1.0,cpu=2.0,mem=4.0" in line
    assert "status=ok" in line
    assert "temp=" not in line
    assert "data=" in to_line("puda.first.tlm.health", b'{"custom":42}')


def test_response_data_fallback(command):
    command["response"]["message"] = ""
    command["response"]["data"] = {"result": 42}
    line = to_line("puda.first.cmd.response.queue", json.dumps(command).encode())
    assert 'response_message="{\\"result\\":42}"' in line
    assert "response_data=" not in line


@pytest.mark.parametrize(
    "duration,expected", [("24h", 86400), ("1h30m", 5400), ("0.5s", 0.5)]
)
def test_response_retention_duration(duration, expected):
    assert duration_seconds(duration) == expected


@pytest.mark.parametrize("duration", ["", "0s", "-1h", "2days"])
def test_invalid_response_retention_duration(duration):
    with pytest.raises(ValueError):
        duration_seconds(duration)


@pytest.mark.parametrize("payload", [b"[]", b"{}", b"not json"])
def test_malformed_messages(payload):
    with pytest.raises((ValueError, KeyError)):
        to_line("puda.first.cmd.queue", payload)


def message(payload):
    return SimpleNamespace(
        subject="puda.first.cmd.response.queue",
        data=payload,
        ack=AsyncMock(),
        nak=AsyncMock(),
        term=AsyncMock(),
    )


async def test_plaintext_and_e2ee_identical_output(command):
    subscriber = Subscriber(Config([], False, {}))
    subscriber.emit = Mock()
    payload = json.dumps(command).encode()
    await subscriber.process(message(payload), True)
    plain_line = subscriber.emit.call_args.args[0]
    e2e.set_ring(e2e.Ring(encrypt=b"a" * 32))
    encrypted = message(e2e.seal("puda.first.cmd.response.queue", payload))
    await subscriber.process(encrypted, True)
    assert subscriber.emit.call_args.args[0] == plain_line
    encrypted.ack.assert_awaited_once()


async def test_ack_only_after_stdout(command):
    subscriber = Subscriber(Config([], False, {}))
    msg = message(json.dumps(command).encode())

    def emit(line):
        msg.ack.assert_not_awaited()

    subscriber.emit = emit
    await subscriber.process(msg, True)
    msg.ack.assert_awaited_once()
    subscriber.emit = Mock(side_effect=BrokenPipeError)
    msg.ack.reset_mock()
    with pytest.raises(BrokenPipeError):
        await subscriber.process(msg, True)
    msg.ack.assert_not_awaited()


async def test_core_messages_never_acked(command):
    subscriber = Subscriber(Config([], False, {}))
    subscriber.emit = Mock()
    msg = message(json.dumps(command).encode())
    await subscriber.process(msg)
    msg.ack.assert_not_awaited()


async def test_wrong_key_retries_without_emitting(command):
    e2e.set_ring(e2e.Ring(encrypt=b"a" * 32))
    msg = message(
        e2e.seal("puda.first.cmd.response.queue", json.dumps(command).encode())
    )
    e2e.set_ring(e2e.Ring(encrypt=b"b" * 32))
    subscriber = Subscriber(Config([], True, {}))
    subscriber.sync_keyring = AsyncMock()
    subscriber.emit = Mock()
    await subscriber.process(msg, True)
    subscriber.emit.assert_not_called()
    subscriber.sync_keyring.assert_awaited_once()
    msg.nak.assert_awaited_once_with(delay=5)
    msg.ack.assert_not_awaited()


async def test_plaintext_rejected_when_encrypted(command):
    e2e.set_ring(e2e.Ring(encrypt=b"a" * 32))
    subscriber = Subscriber(Config([], True, {}))
    subscriber.sync_keyring = AsyncMock()
    subscriber.emit = Mock()
    msg = message(json.dumps(command).encode())
    await subscriber.process(msg, True)
    subscriber.emit.assert_not_called()
    msg.nak.assert_awaited_once()


async def test_invalid_json_terminated():
    subscriber = Subscriber(Config([], False, {}))
    msg = message(b"invalid")
    await subscriber.process(msg, True)
    msg.term.assert_awaited_once()
    msg.ack.assert_not_awaited()
