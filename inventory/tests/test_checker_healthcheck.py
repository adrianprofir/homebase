import os
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "docker" / "checker-healthcheck.sh"


def run_healthcheck(heartbeat, interval_seconds=20):
    env = {
        **os.environ,
        "HOMEBASE_CHECKER_HEARTBEAT": str(heartbeat),
        "CHECK_INTERVAL_SECONDS": str(interval_seconds),
    }
    return subprocess.run(  # noqa: S603
        ["/bin/sh", str(SCRIPT)],
        env=env,
        check=False,
    ).returncode


def test_fresh_heartbeat_is_healthy(tmp_path):
    heartbeat = tmp_path / "heartbeat"
    heartbeat.touch()
    assert run_healthcheck(heartbeat) == 0


def test_missing_heartbeat_is_unhealthy(tmp_path):
    heartbeat = tmp_path / "missing"
    assert run_healthcheck(heartbeat) == 1


def test_stale_heartbeat_is_unhealthy(tmp_path):
    heartbeat = tmp_path / "heartbeat"
    heartbeat.touch()
    old = time.time() - 7200
    os.utime(heartbeat, (old, old))
    assert run_healthcheck(heartbeat, interval_seconds=20) == 1
