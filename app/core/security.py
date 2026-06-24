import json
import logging

import firebase_admin
from firebase_admin import auth, credentials

from app.core.config import settings
from app.core.exceptions import AuthException

logger = logging.getLogger("app.security")

_firebase_initialized = False


def init_firebase() -> None:
    """Initializes the Firebase Admin SDK.

    Uses a Firebase service account JSON from the FIREBASE_CREDENTIALS
    environment variable, which works well with Railway variables.
    """
    global _firebase_initialized
    if _firebase_initialized:
        return

    try:
        # Check if already initialized in active memory
        firebase_admin.get_app()
        _firebase_initialized = True
        logger.info("Firebase Admin SDK already initialized.")
        return
    except ValueError:
        pass

    if not settings.FIREBASE_CREDENTIALS:
        raise RuntimeError("FIREBASE_CREDENTIALS environment variable is required.")

    try:
        service_account_info = json.loads(settings.FIREBASE_CREDENTIALS)
    except json.JSONDecodeError as e:
        raise RuntimeError("FIREBASE_CREDENTIALS must contain valid service account JSON.") from e

    credential_project_id = service_account_info.get("project_id")
    if credential_project_id != settings.FIREBASE_PROJECT_ID:
        raise RuntimeError(
            "Firebase credentials project_id does not match FIREBASE_PROJECT_ID: "
            f"{credential_project_id!r} != {settings.FIREBASE_PROJECT_ID!r}"
        )

    cred = credentials.Certificate(service_account_info)
    firebase_admin.initialize_app(cred, {"projectId": settings.FIREBASE_PROJECT_ID})
    _firebase_initialized = True
    logger.info("Firebase Admin SDK successfully initialized from FIREBASE_CREDENTIALS.")


def verify_firebase_token(token: str) -> dict:
    """Verifies a Firebase ID token (JWT) sent by the Flutter client.

    If in a testing environment (pytest), returns a mock token payload
    to allow robust, local unit tests to execute without hitting Firebase servers.
    """
    if settings.is_testing or (settings.ENVIRONMENT == "development" and token.startswith("mock_")):
        uid = "mock_uid_123"
        email = "google.user@calry.ai" if "google" in token else "developer@calry.ai"
        name = "Google User" if "google" in token else "Calry Developer"
        return {
            "uid": uid,
            "email": email,
            "name": name,
            "email_verified": True,
        }

    try:
        init_firebase()
        # Verify the ID token and return decodable user payload
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except Exception as e:
        logger.error(f"Firebase token verification failed: {e}")
        raise AuthException(
            message="Invalid or expired authentication credentials",
            error_code="INVALID_CREDENTIALS",
            details={"original_error": str(e)},
        )
