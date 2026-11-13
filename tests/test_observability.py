import sentry_sdk

from app.core import observability
from app.core.config import settings


def test_init_sentry_is_noop_without_dsn(monkeypatch):
    monkeypatch.setattr(settings, "SENTRY_DSN", None)
    calls = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: calls.append(kw))
    observability.init_sentry("api")
    assert calls == []


def test_before_send_filters_credentials_and_bodies():
    event = {
        "request": {
            "headers": {"Authorization": "Bearer abc", "X-Request-ID": "1", "Cookie": "x"},
            "data": {"meal": "secret"},
        }
    }
    out = observability._before_send(event, {})
    assert out["request"]["headers"]["Authorization"] == "[Filtered]"
    assert out["request"]["headers"]["Cookie"] == "[Filtered]"
    assert out["request"]["headers"]["X-Request-ID"] == "1"
    assert "data" not in out["request"]


def test_init_sentry_configures_service_when_dsn_set(monkeypatch):
    monkeypatch.setattr(settings, "SENTRY_DSN", "https://key@o0.ingest.sentry.io/0")
    captured = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: captured.update(kw))
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda *a: captured.setdefault("tag", a))
    observability.init_sentry("worker")
    assert captured["server_name"] == "worker"
    assert captured["send_default_pii"] is False
    assert captured["tag"] == ("service", "worker")
