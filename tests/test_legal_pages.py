import pytest
from httpx import AsyncClient


@pytest.mark.parametrize(
    ("path", "heading"),
    [
        ("/privacy?lang=en", "Privacy Policy"),
        ("/privacy-policy?lang=it", "Informativa privacy"),
        ("/terms?lang=en", "Terms &amp; Conditions"),
        ("/terms-and-conditions?lang=it", "Termini e condizioni"),
    ],
)
async def test_public_legal_pages_are_available(client: AsyncClient, path: str, heading: str):
    response = await client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert heading in response.text
    assert response.headers["content-language"] in {"en", "it"}
    assert "Authorization" not in response.request.headers


async def test_legal_page_uses_accept_language(client: AsyncClient):
    response = await client.get("/privacy", headers={"Accept-Language": "it-IT,it;q=0.9,en;q=0.8"})

    assert response.status_code == 200
    assert response.headers["content-language"] == "it"
    assert "Informativa privacy" in response.text


async def test_unknown_language_falls_back_to_english(client: AsyncClient):
    response = await client.get("/terms?lang=fr", headers={"Accept-Language": "fr-FR"})

    assert response.status_code == 200
    assert response.headers["content-language"] == "en"
    assert "Terms &amp; Conditions" in response.text


async def test_legal_stylesheet_is_served(client: AsyncClient):
    response = await client.get("/static/legal/legal.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
