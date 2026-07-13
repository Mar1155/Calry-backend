from __future__ import annotations

from html import escape

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.core.config import settings
from app.legal.content import LEGAL_DOCUMENTS

router = APIRouter()

_STYLE_URL = "/static/legal/legal.css"
_MONTHS = {
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
    "it": (
        "gennaio",
        "febbraio",
        "marzo",
        "aprile",
        "maggio",
        "giugno",
        "luglio",
        "agosto",
        "settembre",
        "ottobre",
        "novembre",
        "dicembre",
    ),
}


def _language(explicit: str | None, accept_language: str | None) -> str:
    if explicit and explicit.lower() in {"en", "it"}:
        return explicit.lower()
    if accept_language:
        for candidate in accept_language.lower().split(","):
            code = candidate.split(";", 1)[0].strip().split("-", 1)[0]
            if code in {"en", "it"}:
                return code
    return "en"


def _localized_date(language: str) -> str:
    value = settings.LEGAL_EFFECTIVE_DATE
    month = _MONTHS[language][value.month - 1]
    return f"{month} {value.day}, {value.year}" if language == "en" else f"{value.day} {month} {value.year}"


def _contact(language: str) -> str:
    if settings.LEGAL_CONTACT_EMAIL:
        email = escape(settings.LEGAL_CONTACT_EMAIL)
        return f'<a href="mailto:{email}">{email}</a>'
    fallback = (
        "the support contact published on Calry’s official App Store or Google Play listing"
        if language == "en"
        else "il contatto di assistenza pubblicato nella scheda ufficiale di Calry su App Store o Google Play"
    )
    return f"<span>{fallback}</span>"


def _render(document: str, language: str, request: Request) -> HTMLResponse:
    content = LEGAL_DOCUMENTS[(document, language)]
    operator = escape(settings.LEGAL_OPERATOR_NAME)
    body = content["body"].replace("{{OPERATOR_NAME}}", operator).replace("{{CONTACT}}", _contact(language))
    effective_label = "Effective" if language == "en" else "In vigore dal"
    other_language = "it" if language == "en" else "en"
    other_label = "Italiano" if other_language == "it" else "English"
    privacy_label = "Privacy Policy" if language == "en" else "Informativa privacy"
    terms_label = "Terms & Conditions" if language == "en" else "Termini e condizioni"
    canonical_path = "/privacy" if document == "privacy" else "/terms"
    canonical = str(request.base_url).rstrip("/") + canonical_path

    html = f"""<!doctype html>
<html lang="{language}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{escape(content['description'])}">
  <meta name="robots" content="index,follow">
  <link rel="canonical" href="{escape(canonical)}">
  <link rel="alternate" hreflang="en" href="{escape(canonical)}?lang=en">
  <link rel="alternate" hreflang="it" href="{escape(canonical)}?lang=it">
  <link rel="stylesheet" href="{_STYLE_URL}">
  <title>{escape(content['title'])} · Calry</title>
</head>
<body>
  <header class="site-header">
    <a class="brand" href="/" aria-label="Calry home"><span class="brand-mark">C</span><span>Calry</span></a>
    <a class="language" href="{canonical_path}?lang={other_language}" hreflang="{other_language}">{other_label}</a>
  </header>
  <main>
    <div class="hero">
      <p class="eyebrow">Calry · Legal</p>
      <h1>{escape(content['title'])}</h1>
      <p class="lead">{escape(content['lead'])}</p>
      <p class="effective">{effective_label}: {_localized_date(language)}</p>
    </div>
    <article>{body}</article>
  </main>
  <footer>
    <nav aria-label="Legal">
      <a href="/privacy?lang={language}">{privacy_label}</a>
      <a href="/terms?lang={language}">{terms_label}</a>
    </nav>
    <p>© {settings.LEGAL_EFFECTIVE_DATE.year} {operator}. {content['footer']}</p>
  </footer>
</body>
</html>"""
    headers = {
        "Cache-Control": "public, max-age=300",
        "Content-Language": language,
        "Content-Security-Policy": "default-src 'none'; style-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "Vary": "Accept-Language",
    }
    return HTMLResponse(html, headers=headers)


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
@router.get("/privacy-policy", response_class=HTMLResponse, include_in_schema=False)
async def privacy_policy(request: Request, lang: str | None = Query(default=None)) -> HTMLResponse:
    return _render("privacy", _language(lang, request.headers.get("accept-language")), request)


@router.get("/terms", response_class=HTMLResponse, include_in_schema=False)
@router.get("/terms-and-conditions", response_class=HTMLResponse, include_in_schema=False)
async def terms_and_conditions(request: Request, lang: str | None = Query(default=None)) -> HTMLResponse:
    return _render("terms", _language(lang, request.headers.get("accept-language")), request)
