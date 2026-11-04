"""Disposable native services: explicit binaries, random loopback ports, no .env."""
from contextlib import contextmanager
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time

from storage import ClickHouse, Config


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def native_services(root, temp):
    clickhouse = Path(os.environ["CLICKHOUSE_TEST_BINARY"]).resolve(strict=True)
    nats = Path(os.environ["NATS_TEST_BINARY"]).resolve(strict=True)
    http, native, broker = free_port(), free_port(), free_port()
    password = secrets.token_hex(32)
    (temp / "users.xml").write_text(f'''<clickhouse>
<profiles><default><max_threads>2</max_threads></default></profiles>
<users><test><password>{password}</password><networks><ip>127.0.0.1</ip></networks><profile>default</profile><quota>default</quota><access_management>1</access_management></test></users>
<quotas><default><interval><duration>3600</duration><queries>0</queries><errors>0</errors><result_rows>0</result_rows><read_rows>0</read_rows><execution_time>0</execution_time></interval></default></quotas>
</clickhouse>''')
    (temp / "config.xml").write_text(f'''<clickhouse>
<logger><level>warning</level><log>{temp}/server.log</log><errorlog>{temp}/error.log</errorlog></logger>
<path>{temp}/data/</path><tmp_path>{temp}/tmp/</tmp_path><user_files_path>{temp}/user_files/</user_files_path>
<users_config>{temp}/users.xml</users_config><listen_host>127.0.0.1</listen_host>
<http_port>{http}</http_port><tcp_port>{native}</tcp_port>
<background_schedule_pool_size>16</background_schedule_pool_size>
<background_message_broker_schedule_pool_size>8</background_message_broker_schedule_pool_size>
<background_distributed_schedule_pool_size>8</background_distributed_schedule_pool_size>
</clickhouse>''')
    processes = []
    try:
        with (temp / "console.log").open("wb") as output:
            processes.append(subprocess.Popen([str(clickhouse), "server", "--config-file=" + str(temp / "config.xml")], stdout=output, stderr=output))
            processes.append(subprocess.Popen([str(nats), "-a", "127.0.0.1", "-p", str(broker), "-js", "-sd", str(temp / "nats")], stdout=output, stderr=output))
        config = Config([f"nats://127.0.0.1:{broker}"], f"http://127.0.0.1:{http}", "test", password, batch_interval=0.05)
        db = ClickHouse(config)
        deadline = time.monotonic() + 60
        while True:
            try:
                version = db.request("SELECT version()").decode().strip()
                break
            except RuntimeError:
                if any(p.poll() is not None for p in processes) or time.monotonic() > deadline:
                    raise RuntimeError("Disposable native server did not start; inspect test console.log") from None
                time.sleep(0.2)
        for path in sorted((root / "server/initdb").glob("*.sql")):
            for sql in path.read_text().split(";"):
                if sql.strip():
                    db.request(sql)
        db.check()
        print("Native ClickHouse", version, ";", subprocess.check_output([str(nats), "--version"], text=True).strip())
        yield config, db
    finally:
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
