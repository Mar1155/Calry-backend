import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_healthcheck_endpoint(client: AsyncClient) -> None:
    """Verifies that the /health endpoint is operational and returns healthy statuses."""
    response = await client.get("/api/v1/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
