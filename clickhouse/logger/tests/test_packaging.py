"""Guard the single-source, locked uv container build."""

from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_container_uses_locked_uv_project():
    assert (ROOT / "uv.lock").is_file(), "Generate the uv lockfile"
    assert not (ROOT / "requirements.txt").exists(), "Use pyproject.toml only"
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "uv sync --locked --no-dev --no-install-project" in dockerfile
    assert "COPY pyproject.toml uv.lock" in dockerfile
    assert "pip install" not in dockerfile
    assert 'USER 10001:10001' in dockerfile
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    names = {package["name"] for package in lock["package"]}
    assert {"puda", "nats-py"} <= names
