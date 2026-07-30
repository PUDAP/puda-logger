#!/usr/bin/env python3
"""Deterministic assertions for ALD JetStream ingestion configuration and samples."""

from __future__ import annotations

import json
import re
import shlex
import sys
from datetime import datetime
from pathlib import Path

LOGGER_DIR = Path(__file__).resolve().parents[1]
COMPOSE = LOGGER_DIR / "compose.yml"
TELEGRAF = LOGGER_DIR / "telegraf.conf.template"
TEST_TELEGRAF = LOGGER_DIR / "test" / "telegraf.test.conf"
SAMPLES = LOGGER_DIR / "test" / "publish-samples.sh"
DOCKERFILE = LOGGER_DIR / "Dockerfile"
ENTRYPOINT = LOGGER_DIR / "docker-entrypoint.sh"
HEALTHCHECK = LOGGER_DIR / "healthcheck.sh"
ENV_EXAMPLE = LOGGER_DIR / ".env.example"
NATS_CONFIG = LOGGER_DIR / "nats-config.sh"
README = LOGGER_DIR / "README.md"

MAX_BYTES = "10GB"
BASE_TAGS = {"machine_id", "point_key", "scope", "tube", "category"}
BASE_FIELDS = {
    "schema": "string",
    "event_id": "string",
    "node_id": "string",
    "value_type": "string",
    "value_int": "int",
    "value_float": "float",
    "value_bool": "bool",
    "value_text": "string",
    "quality": "string",
    "unit": "string",
    "source_timestamp": "string",
    "server_timestamp": "string",
    "received_at": "string",
    "catalog_sha256": "string",
}
VALUE_FIELDS = {"value_int", "value_float", "value_bool", "value_text"}
HEALTH_FIELDS = {
    "opcua_connected",
    "stale",
    "configured_points",
    "resolved_points",
    "cached_points",
    "sqlite_queue_depth",
    "sqlite_dropped_items",
    "sqlite_writer_ok",
    "sqlite_writer_error_count",
    "sqlite_writer_last_error_type",
    "outbox_pending",
    "outbox_published",
    "outbox_failed_pending",
    "reconnect_count",
    "publisher_error_count",
    "publisher_last_error",
}
SAMPLE_BASE_KEYS = {
    "schema",
    "event_id",
    "event_time",
    "machine_id",
    "point_key",
    "node_id",
    "scope",
    "tube",
    "category",
    "value_type",
    "quality",
    "unit",
    "source_timestamp",
    "server_timestamp",
    "received_at",
    "catalog_sha256",
}


class Assertions:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, condition: bool, message: str) -> None:
        if not condition:
            self.failures.append(message)

    def finish(self) -> None:
        if self.failures:
            for failure in self.failures:
                print(f"FAIL: {failure}", file=sys.stderr)
            print(f"ALD assertions failed: {len(self.failures)}", file=sys.stderr)
            raise SystemExit(1)
        print("ALD config/sample assertions passed")


def command_block(compose: str, verb: str) -> str:
    match = re.search(
        rf"nats(?:_cli)? stream {verb} ALD_TELEMETRY\s+\\(?P<body>.*?)(?=\n\s*(?:nats(?:_cli)? |else\b|fi\b))",
        compose,
        flags=re.DOTALL,
    )
    return match.group(0) if match else ""


def consumer_block(config: str, measurement: str) -> str:
    for block in re.split(r"(?=\[\[inputs\.nats_consumer\]\])", config):
        if re.search(rf'^\s*name_override\s*=\s*"{re.escape(measurement)}"\s*$', block, re.MULTILINE):
            return block
    return ""


def json_entries(block: str, kind: str) -> dict[str, dict[str, str]]:
    entries: dict[str, dict[str, str]] = {}
    pattern = rf"\[\[inputs\.nats_consumer\.json_v2\.{kind}\]\](.*?)(?=\n\s*\[\[|\Z)"
    for entry in re.findall(pattern, block, flags=re.DOTALL):
        path_match = re.search(r'^\s*path\s*=\s*"([^"]+)"', entry, re.MULTILINE)
        if not path_match:
            continue
        path = path_match.group(1)
        attributes: dict[str, str] = {}
        for key in ("rename", "type", "optional"):
            value_match = re.search(rf'^\s*{key}\s*=\s*(?:"([^"]+)"|(true|false))', entry, re.MULTILINE)
            if value_match:
                attributes[key] = value_match.group(1) or value_match.group(2)
        entries[path] = attributes
    return entries


def assert_stream(assertions: Assertions, compose: str) -> None:
    assertions.check("stream info ALD_TELEMETRY" in compose, "ALD stream initialization must check whether the stream already exists")
    for verb in ("add", "update"):
        block = command_block(compose, verb)
        assertions.check(bool(block), f"missing idempotent 'nats stream {verb} ALD_TELEMETRY' command")
        if not block:
            continue
        assertions.check('--subjects "puda.*.tlm.ald.*"' in block, f"ALD stream {verb} must cover puda.*.tlm.ald.*")
        assertions.check("--retention limits" in block, f"ALD stream {verb} must use limits retention")
        if verb == "add":
            assertions.check("--storage file" in block, "new ALD stream must use file storage")
        else:
            assertions.check("--force" in block, "existing ALD stream update must be non-interactive")
        assertions.check("--max-age 7d" in block, f"ALD stream {verb} must set max age to 7d")
        assertions.check(f"--max-bytes {MAX_BYTES}" in block, f"ALD stream {verb} must bound storage at {MAX_BYTES}")


def assert_consumer(
    assertions: Assertions,
    config: str,
    measurement: str,
    subject: str,
    alarm: bool,
) -> None:
    block = consumer_block(config, measurement)
    assertions.check(bool(block), f"missing Telegraf measurement {measurement}")
    if not block:
        return

    assertions.check(f'jetstream_subjects = ["{subject}"]' in block, f"{measurement} must consume {subject}")
    assertions.check(f'jetstream_stream = "ALD_TELEMETRY"' in block, f"{measurement} must bind ALD_TELEMETRY")
    assertions.check('tagexclude = ["host", "subject"]' in block, f"{measurement} must exclude infrastructure-only host/subject tags")
    assertions.check('timestamp_path = "event_time"' in block, f"{measurement} must use event_time as its timestamp")
    assertions.check('timestamp_format = "2006-01-02T15:04:05Z07:00"' in block, f"{measurement} must parse RFC3339 event_time")

    tags = json_entries(block, "tag")
    fields = json_entries(block, "field")
    expected_tags = BASE_TAGS | ({"severity"} if alarm else set())
    assertions.check(set(tags) == expected_tags, f"{measurement} payload tags must be exactly {sorted(expected_tags)}; got {sorted(tags)}")
    assertions.check("node_id" not in tags, f"{measurement} must not use full node_id as a tag")

    expected_fields = dict(BASE_FIELDS)
    if alarm:
        expected_fields.update({"active": "bool", "description": "string"})
    assertions.check(set(fields) == set(expected_fields), f"{measurement} fields must be {sorted(expected_fields)}; got {sorted(fields)}")
    for field, expected_type in expected_fields.items():
        actual = fields.get(field, {})
        assertions.check(actual.get("type") == expected_type, f"{measurement}.{field} must have type {expected_type}")
        if field in VALUE_FIELDS:
            assertions.check(actual.get("optional") == "true", f"{measurement}.{field} must be optional")


def load_samples(assertions: Assertions, script: str) -> list[tuple[str, dict[str, object]]]:
    samples: list[tuple[str, dict[str, object]]] = []
    for line_number, line in enumerate(script.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("publish "):
            continue
        try:
            words = shlex.split(stripped)
        except ValueError as exc:
            assertions.check(False, f"sample line {line_number} is not valid shell quoting: {exc}")
            continue
        if len(words) != 3:
            assertions.check(False, f"sample line {line_number} must call publish with subject and one JSON payload")
            continue
        try:
            payload = json.loads(words[2])
        except json.JSONDecodeError as exc:
            assertions.check(False, f"sample line {line_number} contains invalid JSON: {exc}")
            continue
        if words[1].startswith("puda.") and ".tlm.ald." in words[1]:
            samples.append((words[1], payload))
    return samples


def assert_sample_shape(assertions: Assertions, subject: str, payload: dict[str, object], alarm: bool) -> None:
    missing = SAMPLE_BASE_KEYS - set(payload)
    assertions.check(not missing, f"sample {subject} is missing keys {sorted(missing)}")

    value_fields = VALUE_FIELDS & set(payload)
    assertions.check(len(value_fields) == 1, f"sample {subject} must contain exactly one value_* field; got {sorted(value_fields)}")
    raw_value_type = payload.get("value_type")
    value_type = raw_value_type if isinstance(raw_value_type, str) else ""
    expected_value_field = {
        "int": "value_int",
        "float": "value_float",
        "bool": "value_bool",
        "string": "value_text",
    }.get(value_type)
    assertions.check(expected_value_field in value_fields, f"sample {subject} value_type={value_type!r} must match {expected_value_field}")

    if expected_value_field in payload:
        value = payload[expected_value_field]
        expected_python_type = {"int": int, "float": float, "bool": bool, "string": str}.get(value_type)
        type_matches = expected_python_type is not None and type(value) is expected_python_type
        assertions.check(type_matches, f"sample {subject} {expected_value_field} has wrong JSON type")

    event_time = payload.get("event_time")
    try:
        datetime.fromisoformat(str(event_time).replace("Z", "+00:00"))
    except ValueError:
        assertions.check(False, f"sample {subject} has invalid RFC3339 event_time {event_time!r}")

    machine_id = payload.get("machine_id")
    assertions.check(subject.startswith(f"puda.{machine_id}."), f"sample {subject} machine_id does not match its subject")
    catalog_hash = payload.get("catalog_sha256")
    assertions.check(isinstance(catalog_hash, str) and bool(re.fullmatch(r"[0-9a-f]{64}", catalog_hash)), f"sample {subject} must contain a lowercase SHA-256 catalog hash")

    alarm_keys = {"active", "severity", "description"}
    if alarm:
        assertions.check(alarm_keys <= set(payload), f"alarm sample {subject} must contain {sorted(alarm_keys)}")
        assertions.check(type(payload.get("active")) is bool, f"alarm sample {subject} active must be boolean")
    else:
        assertions.check(not (alarm_keys & set(payload)), f"point sample {subject} must not contain alarm-only keys")


def assert_samples(assertions: Assertions, script: str) -> None:
    samples = load_samples(assertions, script)
    points = [(subject, payload) for subject, payload in samples if subject.endswith(".ald.point")]
    alarms = [(subject, payload) for subject, payload in samples if subject.endswith(".ald.alarm")]

    assertions.check(len(points) >= 4, f"need at least four ALD point samples; got {len(points)}")
    assertions.check(len(alarms) >= 2, f"need active and recovered ALD alarm samples; got {len(alarms)}")

    for subject, payload in points:
        assert_sample_shape(assertions, subject, payload, alarm=False)
    for subject, payload in alarms:
        assert_sample_shape(assertions, subject, payload, alarm=True)

    point_types = {payload.get("value_type") for _, payload in points}
    assertions.check(point_types >= {"int", "float", "bool", "string"}, f"ALD point samples must cover int/float/bool/string; got {sorted(str(item) for item in point_types)}")
    alarm_states = {payload.get("active") for _, payload in alarms}
    assertions.check(alarm_states == {False, True}, f"ALD alarm samples must cover active and recovered states; got {alarm_states}")


def assert_processors(assertions: Assertions, config: str) -> None:
    for field in HEALTH_FIELDS:
        assertions.check(
            f'"{field}"' in config,
            f"telemetry processor must expose health field {field}",
        )
    assertions.check(
        'namepass = ["ald_point", "ald_alarm"]' in config,
        "ALD numeric processor must cover point and alarm measurements",
    )
    assertions.check(
        'metric.fields["value_numeric"]' in config,
        "ALD numeric processor must derive value_numeric",
    )


def assert_nats_security(assertions: Assertions, config: str) -> None:
    blocks = [
        block
        for block in re.split(r"(?=\[\[inputs\.nats_consumer\]\])", config)
        if "[[inputs.nats_consumer]]" in block
    ]
    assertions.check(len(blocks) == 8, f"expected eight NATS inputs; got {len(blocks)}")
    for index, block in enumerate(blocks, start=1):
        for fragment in (
            "servers = ${NATS_SERVERS_TOML}",
            'credentials = "${NATS_CREDS_FILE}"',
            "secure = ${NATS_SECURE}",
            'tls_ca = "${NATS_TLS_CA_FILE}"',
            'tls_cert = "${NATS_TLS_CERT_FILE}"',
            'tls_key = "${NATS_TLS_KEY_FILE}"',
            "insecure_skip_verify = false",
        ):
            assertions.check(fragment in block, f"NATS input {index} must include {fragment}")


def assert_fixture_parity(
    assertions: Assertions,
    production_config: str,
    test_config: str,
    script: str,
) -> None:
    assertions.check(
        "set -eu" in script.splitlines()[:8],
        "sample publisher must fail fast with set -eu",
    )
    assertions.check(
        "to $NATS_URL" not in script,
        "sample publisher must not echo a possibly credential-bearing NATS URL",
    )
    for legacy_path in ("data.header.", "data.command.", "data.response."):
        assertions.check(
            legacy_path not in test_config,
            f"test config must mirror raw production wire paths, not {legacy_path}",
        )
    for required_path in ('path = "header.timestamp"', 'path = "header.machine_id"'):
        assertions.check(
            required_path in production_config and required_path in test_config,
            f"production and test configs must both include {required_path}",
        )
    assertions.check(
        'namepass = ["commands"]' in test_config and 'result_key = "topic"' in test_config,
        "test config must derive command topic from the NATS subject like production",
    )
    for regex_line in (
        r"pattern = '^puda\.[^.]+\.cmd\.(.+)$'",
        r"pattern = '^puda\.([^.]+)\.tlm\.(?:health|ald\.(?:health|heartbeat))$'",
    ):
        assertions.check(
            regex_line in production_config and regex_line in test_config,
            f"production/test regex drift: missing exact {regex_line}",
        )

    published: list[tuple[str, dict[str, object]]] = []
    for line_number, line in enumerate(script.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("publish "):
            continue
        try:
            words = shlex.split(stripped)
            payload = json.loads(words[2])
        except (IndexError, ValueError, json.JSONDecodeError) as exc:
            assertions.check(False, f"sample line {line_number} cannot be parsed: {exc}")
            continue
        if isinstance(payload, dict):
            published.append((words[1], payload))

    command_payloads = [
        payload for subject, payload in published if ".cmd." in subject
    ]
    assertions.check(bool(command_payloads), "raw command/response fixtures are required")
    for payload in command_payloads:
        assertions.check(
            "data" not in payload and {"header", "command"} <= set(payload),
            "command fixtures must use top-level header/command raw wire shape",
        )
    health_payloads = [
        payload for subject, payload in published if subject.endswith(".tlm.health")
    ]
    assertions.check(len(health_payloads) == 1, "exactly one health fixture is required")
    if health_payloads:
        missing = HEALTH_FIELDS - set(health_payloads[0])
        assertions.check(
            not missing,
            f"health fixture must cover all extracted health fields; missing {sorted(missing)}",
        )


def assert_deployment_hardening(
    assertions: Assertions,
    compose: str,
    dockerfile: str,
    entrypoint: str,
    healthcheck: str,
    nats_config: str,
    readme: str,
) -> None:
    assertions.check("|| true" not in compose, "stream init must not suppress failures")
    assertions.check("\n    build:" not in compose, "production logger Compose must not build in place")
    assertions.check(
        "${PUDA_LOGGER_IMAGE_REPOSITORY:?" in compose
        and "@${PUDA_LOGGER_IMAGE_DIGEST:?" in compose,
        "logger image must be repository plus mandatory immutable digest",
    )
    assertions.check("jq -e" in compose, "stream init must verify final JSON state")
    assertions.check('((.config.storage | ascii_downcase) == "file")' in compose, "stream init must verify immutable file storage")
    assertions.check("natsio/nats-box:0.19.2@sha256:" in compose, "stream init image must be versioned and digest-pinned")
    assertions.check("/var/run/docker.sock" not in compose, "logger deployment must not mount the Docker socket")
    assertions.check("autoheal:" not in compose, "logger must not depend on a privileged autoheal sidecar")
    for fragment in ("read_only: true", "cap_drop:", "no-new-privileges:true"):
        assertions.check(fragment in compose, f"logger compose must include {fragment}")
    assertions.check(compose.count("read_only: true") >= 2, "stream init and logger must both use a read-only root filesystem")
    assertions.check(compose.count("cap_drop:") >= 2, "stream init and logger must both drop capabilities")
    assertions.check(compose.count("no-new-privileges:true") >= 2, "stream init and logger must both disable privilege escalation")
    assertions.check(compose.count("user:") >= 2, "stream init and logger must both declare a non-root user")
    assertions.check(
        compose.count("${PUDA_LOGGER_RUNTIME_UID:?") == 2
        and compose.count("${PUDA_LOGGER_RUNTIME_GID:?") == 2,
        "Compose must bind both services to the owner of file-backed secrets",
    )
    for secret in ("nats_creds", "nats_tls_ca", "nats_tls_cert", "nats_tls_key"):
        assertions.check(f"{secret}:" in compose, f"Compose must declare {secret}")
        assertions.check(compose.count(f"source: {secret}") == 2, f"{secret} must be mounted into init and logger")
    assertions.check(compose.count("mode: 0400") >= 9, "all credential/token mounts must be owner-read-only")
    init_prefix = compose.split("  influxdb-logger:", maxsplit=1)[0]
    assertions.check("env_file:" not in init_prefix, "stream init must not inherit the full logger .env")
    assertions.check("telegraf:1.39@sha256:" in dockerfile, "Telegraf base image must be versioned and digest-pinned")
    assertions.check("natsio/nats-box:0.19.2@sha256:" in dockerfile, "nats CLI build image must be versioned and digest-pinned")
    assertions.check("USER telegraf" in dockerfile, "final logger image must run as telegraf")
    assertions.check(
        'TELEGRAF_CONFIG_OUTPUT="${TELEGRAF_CONFIG_OUTPUT:-/tmp/telegraf.conf}"' in entrypoint,
        "read-only logger must default rendered configuration to /tmp",
    )
    assertions.check("umask 077" in entrypoint, "generated Telegraf configuration must default to mode 0600")
    assertions.check('chmod 600 "$TELEGRAF_CONFIG_OUTPUT"' in entrypoint, "generated Telegraf configuration must be forced to mode 0600")
    assertions.check("INFLUXDB_TOKEN is forbidden" in entrypoint, "entrypoint must reject a token in process environment")
    assertions.check("INFLUXDB_TOKEN_FILE" in entrypoint and 'if [ -L "$INFLUXDB_TOKEN_FILE" ]' in entrypoint, "entrypoint must require a non-symlink InfluxDB token file")
    assertions.check("INFLUXDB_DATABASE:-machines" in entrypoint, "entrypoint database default must be machines")
    assertions.check("INFLUXDB_DATABASE=machines" in readme, "README local example database must be machines")
    assertions.check("nats_validate_config" in entrypoint, "entrypoint must use the shared fail-closed NATS validator")
    assertions.check(
        "NATS URL userinfo is not supported" in nats_config,
        "NATS validator must reject credential-bearing URLs before logging or CLI use",
    )
    assertions.check("NATS_ALLOW_UNAUTHENTICATED_LOOPBACK" in nats_config, "unauthenticated NATS must require an explicit test-mode switch")
    assertions.check("NATS credentials are required" in nats_config, "production NATS must require a credentials file")
    assertions.check("loopback" in nats_config, "unauthenticated test mode must be restricted to loopback servers")
    for variable in ("NATS_CREDS_FILE", "NATS_TLS_CA_FILE", "NATS_TLS_CERT_FILE", "NATS_TLS_KEY_FILE"):
        assertions.check(variable in nats_config, f"shared NATS validator must support {variable}")
    expected_pairs = {
        "RESPONSE_QUEUE:puda_logger_cmd_responses_queue",
        "RESPONSE_IMMEDIATE:puda_logger_cmd_responses_immediate",
        "ALD_TELEMETRY:puda_logger_ald_points",
        "ALD_TELEMETRY:puda_logger_ald_alarms",
        "ALD_TELEMETRY:puda_logger_ald_health",
        "ALD_TELEMETRY:puda_logger_ald_heartbeat",
    }
    for pair in expected_pairs:
        assertions.check(pair in healthcheck, f"healthcheck must cover {pair}")
    for stream in ("RESPONSE_QUEUE", "RESPONSE_IMMEDIATE", "ALD_TELEMETRY"):
        assertions.check(stream in healthcheck, f"healthcheck must verify stream {stream}")
    assertions.check("nats_cli" in healthcheck, "healthcheck must use shared authenticated multi-server NATS CLI setup")
    assertions.check("jq -e" in healthcheck, "healthcheck JSON parsing must fail closed")
    for state in ("stream_name", "num_ack_pending", "max_ack_pending", "push_bound"):
        assertions.check(state in healthcheck, f"healthcheck must validate required consumer state {state}")
    assertions.check("kill -TERM" in healthcheck, "healthcheck failure must trigger restart-policy recovery")
    assertions.check(
        not re.search(r'(?:echo|printf)[^\n]*\$info[^\n]*>&2', healthcheck),
        "healthcheck must not echo raw NATS errors",
    )


def main() -> None:
    assertions = Assertions()
    compose = COMPOSE.read_text(encoding="utf-8")
    config = TELEGRAF.read_text(encoding="utf-8")
    test_config = TEST_TELEGRAF.read_text(encoding="utf-8")
    samples = SAMPLES.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
    healthcheck = HEALTHCHECK.read_text(encoding="utf-8")
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    nats_config = NATS_CONFIG.read_text(encoding="utf-8") if NATS_CONFIG.exists() else ""
    readme = README.read_text(encoding="utf-8")

    assertions.check(
        "INFLUXDB_DATABASE=machines" in env_example,
        "logger and Grafana must share the canonical machines database",
    )

    assert_stream(assertions, compose)
    assert_consumer(assertions, config, "ald_point", "puda.*.tlm.ald.point", alarm=False)
    assert_consumer(assertions, config, "ald_alarm", "puda.*.tlm.ald.alarm", alarm=True)
    for subject, queue in (
        ("puda.*.tlm.ald.health", "puda_logger_ald_health"),
        ("puda.*.tlm.ald.heartbeat", "puda_logger_ald_heartbeat"),
    ):
        assertions.check(
            f'jetstream_subjects = ["{subject}"]' in config,
            f"telemetry must consume {subject}",
        )
        assertions.check(
            f'queue_group = "{queue}"' in config,
            f"telemetry must use durable queue {queue}",
        )
    assert_consumer(assertions, test_config, "ald_point", "puda.*.tlm.ald.point", alarm=False)
    assert_consumer(assertions, test_config, "ald_alarm", "puda.*.tlm.ald.alarm", alarm=True)
    assert_processors(assertions, config)
    assert_processors(assertions, test_config)
    assert_nats_security(assertions, config)
    assert_samples(assertions, samples)
    assert_fixture_parity(assertions, config, test_config, samples)
    assert_deployment_hardening(
        assertions,
        compose,
        dockerfile,
        entrypoint,
        healthcheck,
        nats_config,
        readme,
    )
    assertions.finish()


if __name__ == "__main__":
    main()
