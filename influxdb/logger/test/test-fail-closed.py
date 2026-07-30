#!/usr/bin/env python3
"""Focused fail-closed tests for NATS authentication, health, and config rendering."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

LOGGER_DIR = Path(__file__).resolve().parents[1]
NATS_CONFIG = LOGGER_DIR / "nats-config.sh"
ENTRYPOINT = LOGGER_DIR / "docker-entrypoint.sh"
HEALTHCHECK = LOGGER_DIR / "healthcheck.sh"

NATS_ENV_KEYS = {
    "NATS_SERVERS",
    "NATS_URL",
    "NATS_CREDS",
    "NATS_CREDS_FILE",
    "NATS_CA",
    "NATS_TLS_CA_FILE",
    "NATS_CERT",
    "NATS_TLS_CERT_FILE",
    "NATS_KEY",
    "NATS_TLS_KEY_FILE",
    "NATS_TIMEOUT",
    "NATS_ALLOW_UNAUTHENTICATED_LOOPBACK",
}


def clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in NATS_ENV_KEYS:
        env.pop(key, None)
    env["NATS_CONFIG_LIB"] = str(NATS_CONFIG)
    return env


def write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    path.chmod(0o755)


class NatsConfigurationTests(unittest.TestCase):
    def validate(self, **updates: str) -> subprocess.CompletedProcess[str]:
        env = clean_env()
        env.update(updates)
        return subprocess.run(
            [
                "/bin/sh",
                "-c",
                '. "$NATS_CONFIG_LIB"; nats_validate_config || exit $?; '
                "printf 'secure=%s servers=%s creds=%s\\n' "
                '"$NATS_SECURE" "$NATS_SERVERS_TOML" "$NATS_CREDS_FILE"',
            ],
            cwd=LOGGER_DIR,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_unauthenticated_nats_requires_explicit_test_mode(self) -> None:
        result = self.validate(NATS_SERVERS="nats://127.0.0.1:4222")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("NATS credentials are required", result.stderr)

    def test_unauthenticated_test_mode_rejects_non_loopback_server(self) -> None:
        result = self.validate(
            NATS_SERVERS="nats://nats.internal:4222",
            NATS_ALLOW_UNAUTHENTICATED_LOOPBACK="1",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("loopback", result.stderr)

    def test_explicit_unauthenticated_loopback_test_mode_is_accepted(self) -> None:
        result = self.validate(
            NATS_SERVERS="nats://127.0.0.1:4222,nats://localhost:4223",
            NATS_ALLOW_UNAUTHENTICATED_LOOPBACK="1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('servers=["nats://127.0.0.1:4222","nats://localhost:4223"]', result.stdout)

    def test_credentials_and_tls_files_are_accepted_without_url_userinfo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            files = {}
            for name in ("creds", "ca", "cert", "key"):
                path = root / name
                path.write_text(f"non-empty-{name}\n", encoding="utf-8")
                path.chmod(0o600)
                files[name] = str(path)
            result = self.validate(
                NATS_SERVERS="tls://nats-a.internal:4222,tls://nats-b.internal:4222",
                NATS_CREDS_FILE=files["creds"],
                NATS_TLS_CA_FILE=files["ca"],
                NATS_TLS_CERT_FILE=files["cert"],
                NATS_TLS_KEY_FILE=files["key"],
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("secure=true", result.stdout)
        self.assertNotIn("non-empty-creds", result.stdout + result.stderr)

    def test_credentials_reject_symlink_and_group_or_other_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "creds"
            target.write_text("DO_NOT_LEAK_CREDS\n", encoding="utf-8")
            target.chmod(0o600)
            link = root / "creds-link"
            link.symlink_to(target)
            linked = self.validate(
                NATS_SERVERS="tls://nats.internal:4222",
                NATS_CREDS_FILE=str(link),
            )
            self.assertNotEqual(linked.returncode, 0)
            self.assertIn("symlink", linked.stderr)
            self.assertNotIn("DO_NOT_LEAK_CREDS", linked.stdout + linked.stderr)

            target.chmod(0o644)
            permissive = self.validate(
                NATS_SERVERS="tls://nats.internal:4222",
                NATS_CREDS_FILE=str(target),
            )
            self.assertNotEqual(permissive.returncode, 0)
            self.assertIn("group or other", permissive.stderr)
            self.assertNotIn("DO_NOT_LEAK_CREDS", permissive.stdout + permissive.stderr)

    def test_tls_client_certificate_requires_matching_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            creds = root / "creds"
            cert = root / "cert"
            creds.write_text("creds\n", encoding="utf-8")
            cert.write_text("cert\n", encoding="utf-8")
            creds.chmod(0o600)
            cert.chmod(0o600)
            result = self.validate(
                NATS_SERVERS="tls://nats.internal:4222",
                NATS_CREDS_FILE=str(creds),
                NATS_TLS_CERT_FILE=str(cert),
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("certificate and key", result.stderr)

    def test_url_userinfo_is_rejected_without_echoing_secret(self) -> None:
        secret = "DO_NOT_LEAK"
        result = self.validate(
            NATS_SERVERS=f"nats://user:{secret}@127.0.0.1:4222",
            NATS_ALLOW_UNAUTHENTICATED_LOOPBACK="1",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(secret, result.stdout + result.stderr)


class EntrypointTests(unittest.TestCase):
    def test_generated_config_is_mode_0600_and_secret_is_not_printed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            template = root / "telegraf.conf.template"
            output = root / "telegraf.conf"
            template.write_text(
                'token = "${INFLUXDB_TOKEN}"\nservers = ${NATS_SERVERS_TOML}\n',
                encoding="utf-8",
            )
            write_executable(
                bin_dir / "envsubst",
                """
                #!/usr/bin/env python3
                import os
                import sys
                text = sys.stdin.read()
                for name in (
                    "INFLUXDB_TOKEN",
                    "NATS_SERVERS_TOML",
                    "INFLUXDB_URL",
                    "INFLUXDB_DATABASE",
                    "INFLUXDB_ORGANIZATION",
                    "NATS_CREDS_FILE",
                    "NATS_SECURE",
                    "NATS_TLS_CA_FILE",
                    "NATS_TLS_CERT_FILE",
                    "NATS_TLS_KEY_FILE",
                ):
                    text = text.replace("${" + name + "}", os.environ.get(name, ""))
                sys.stdout.write(text)
                """,
            )
            write_executable(bin_dir / "telegraf", "#!/bin/sh\nexit 0\n")
            secret = "TOP_SECRET_VALUE"
            token_file = root / "influx-token"
            token_file.write_text(secret, encoding="utf-8")
            token_file.chmod(0o600)
            env = clean_env()
            env.update(
                {
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "NATS_SERVERS": "nats://127.0.0.1:4222",
                    "NATS_ALLOW_UNAUTHENTICATED_LOOPBACK": "1",
                    "INFLUXDB_TOKEN_FILE": str(token_file),
                    "TELEGRAF_CONFIG_TEMPLATE": str(template),
                    "TELEGRAF_CONFIG_OUTPUT": str(output),
                }
            )
            result = subprocess.run(
                ["/bin/sh", str(ENTRYPOINT)],
                cwd=LOGGER_DIR,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.is_file())
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertIn(secret, output.read_text(encoding="utf-8"))
            self.assertNotIn(secret, result.stdout + result.stderr)

    def test_entrypoint_rejects_legacy_or_unsafe_influx_token_sources(self) -> None:
        secret = "DO_NOT_LEAK_TOKEN"
        legacy_env = clean_env()
        legacy_env["INFLUXDB_TOKEN"] = secret
        legacy = subprocess.run(
            ["/bin/sh", str(ENTRYPOINT)],
            cwd=LOGGER_DIR,
            env=legacy_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(legacy.returncode, 0)
        self.assertIn("forbidden", legacy.stderr)
        self.assertNotIn(secret, legacy.stdout + legacy.stderr)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "token"
            target.write_text(secret, encoding="utf-8")
            target.chmod(0o600)
            link = root / "token-link"
            link.symlink_to(target)
            for path, expected in ((link, "symlink"), (target, "group or other")):
                if path == target:
                    target.chmod(0o644)
                env = clean_env()
                env["INFLUXDB_TOKEN_FILE"] = str(path)
                result = subprocess.run(
                    ["/bin/sh", str(ENTRYPOINT)],
                    cwd=LOGGER_DIR,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertNotIn(secret, result.stdout + result.stderr)


class HealthcheckTests(unittest.TestCase):
    def run_healthcheck(self, mode: str) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            log_path = root / "calls.jsonl"
            write_executable(
                bin_dir / "nats",
                """
                #!/usr/bin/env python3
                import json
                import os
                import sys
                args = [item for item in sys.argv[1:] if item != "--no-context"]
                with open(os.environ["FAKE_NATS_LOG"], "a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"args": args, "url": os.environ.get("NATS_URL")}) + "\\n")
                mode = os.environ["FAKE_NATS_MODE"]
                if mode == "malformed":
                    print("{MALFORMED-SECRET-STATE")
                    raise SystemExit(0)
                if args[:2] == ["stream", "info"]:
                    stream = args[2]
                    print(json.dumps({"config": {"name": stream}}))
                    raise SystemExit(0)
                if args[:2] == ["consumer", "info"]:
                    stream, consumer = args[2], args[3]
                    subjects = {
                        "puda_logger_cmd_responses_queue": "puda.*.cmd.response.queue",
                        "puda_logger_cmd_responses_immediate": "puda.*.cmd.response.immediate",
                        "puda_logger_ald_points": "puda.*.tlm.ald.point",
                        "puda_logger_ald_alarms": "puda.*.tlm.ald.alarm",
                        "puda_logger_ald_health": "puda.*.tlm.ald.health",
                        "puda_logger_ald_heartbeat": "puda.*.tlm.ald.heartbeat",
                    }
                    state = {
                        "stream_name": stream,
                        "name": consumer,
                        "config": {
                            "name": consumer,
                            "durable_name": consumer,
                            "deliver_group": consumer,
                            "filter_subject": subjects[consumer],
                            "ack_policy": "explicit",
                            "max_ack_pending": 1000,
                        },
                        "num_ack_pending": 0,
                        "push_bound": True,
                    }
                    if mode == "missing-state":
                        state.pop("num_ack_pending")
                    elif mode == "saturated":
                        state["num_ack_pending"] = 1000
                    print(json.dumps(state))
                    raise SystemExit(0)
                raise SystemExit(2)
                """,
            )
            env = clean_env()
            env.update(
                {
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "NATS_SERVERS": "nats://127.0.0.1:1,nats://localhost:4222",
                    "NATS_ALLOW_UNAUTHENTICATED_LOOPBACK": "1",
                    "FAKE_NATS_MODE": mode,
                    "FAKE_NATS_LOG": str(log_path),
                    "HEALTHCHECK_TEST_MODE": "1",
                    "HEALTHCHECK_TELEGRAF_PID": "999999",
                }
            )
            result = subprocess.run(
                ["/bin/sh", str(HEALTHCHECK)],
                cwd=LOGGER_DIR,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            calls = []
            if log_path.exists():
                calls = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            return result, calls

    def test_healthcheck_uses_all_servers_and_checks_all_streams_and_consumers(self) -> None:
        result, calls = self.run_healthcheck("valid")
        self.assertEqual(result.returncode, 0, result.stderr)
        expected_url = "nats://127.0.0.1:1,nats://localhost:4222"
        self.assertEqual(len(calls), 9)
        self.assertTrue(all(call["url"] == expected_url for call in calls))
        commands = [call["args"] for call in calls]
        for stream in ("RESPONSE_QUEUE", "RESPONSE_IMMEDIATE", "ALD_TELEMETRY"):
            self.assertIn(["stream", "info", stream, "--json"], commands)
        for stream, consumer in (
            ("RESPONSE_QUEUE", "puda_logger_cmd_responses_queue"),
            ("RESPONSE_IMMEDIATE", "puda_logger_cmd_responses_immediate"),
            ("ALD_TELEMETRY", "puda_logger_ald_points"),
            ("ALD_TELEMETRY", "puda_logger_ald_alarms"),
            ("ALD_TELEMETRY", "puda_logger_ald_health"),
            ("ALD_TELEMETRY", "puda_logger_ald_heartbeat"),
        ):
            self.assertIn(["consumer", "info", stream, consumer, "--json"], commands)

    def test_healthcheck_fails_closed_on_malformed_json_without_echoing_it(self) -> None:
        result, _ = self.run_healthcheck("malformed")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("malformed or incomplete", result.stderr)
        self.assertNotIn("MALFORMED-SECRET-STATE", result.stdout + result.stderr)

    def test_healthcheck_fails_closed_on_missing_consumer_state(self) -> None:
        result, _ = self.run_healthcheck("missing-state")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("malformed or incomplete", result.stderr)

    def test_healthcheck_fails_closed_on_saturation(self) -> None:
        result, _ = self.run_healthcheck("saturated")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("saturated", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
