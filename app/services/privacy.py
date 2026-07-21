import hashlib
import hmac

from app.core.config import settings


def pseudonymize(value: str | None, *, namespace: str) -> str | None:
    """Return a stable, non-reversible identifier for an audit record."""
    if not value:
        return None
    key = settings.ADMIN_AUDIT_HASH_KEY
    if not key:
        # Never persist raw PII when production configuration is incomplete.
        if settings.is_production:
            return None
        key = "calry-development-audit-key"
    digest = hmac.new(key.encode(), f"{namespace}:{value.casefold()}".encode(), hashlib.sha256).hexdigest()
    return f"{namespace}:{digest[:20]}"
