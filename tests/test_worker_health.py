import json
import urllib.error
import urllib.request

import pytest

from app.worker.health import WorkerHealthServer


@pytest.fixture
def health_server() -> WorkerHealthServer:
    server = WorkerHealthServer("127.0.0.1", 0)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def test_worker_health_reports_ready(health_server: WorkerHealthServer) -> None:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{health_server.port}/api/v1/health",
        timeout=2,
    ) as response:
        assert response.status == 200
        assert json.load(response) == {"status": "healthy", "worker": "ready"}


def test_worker_health_rejects_unknown_path(health_server: WorkerHealthServer) -> None:
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(f"http://127.0.0.1:{health_server.port}/", timeout=2)

    assert error.value.code == 404
