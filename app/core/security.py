import logging
from pathlib import Path

import firebase_admin
from firebase_admin import auth, credentials

from app.core.config import BASE_DIR, settings
from app.core.exceptions import AuthException

logger = logging.getLogger("app.security")

_firebase_initialized = False


def init_firebase() -> None:
    """Initializes the Firebase Admin SDK.

    Supports custom credentials file paths or automated environment detection (such as on Railway).
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

    try:
        if settings.FIREBASE_CREDENTIALS_PATH:
            credentials_path = Path(settings.FIREBASE_CREDENTIALS_PATH)
            if not credentials_path.is_absolute():
                credentials_path = BASE_DIR / credentials_path
            if not credentials_path.exists():
                raise FileNotFoundError(f"Firebase credentials file not found: {credentials_path}")

            cred = credentials.Certificate(str(credentials_path))
            firebase_admin.initialize_app(cred)
            logger.info(f"Firebase Admin SDK successfully initialized from path: {credentials_path}")
        else:
            # Initialize with the project ID directly to support verification without service accounts
            firebase_admin.initialize_app(options={"projectId": settings.FIREBASE_PROJECT_ID})
            logger.info(f"Firebase Admin SDK initialized for project ID: {settings.FIREBASE_PROJECT_ID}")
        _firebase_initialized = True
    except Exception as e:
        logger.warning(
            f"Unable to initialize Firebase Admin SDK: {e}. "
            "Using development fallback mock mechanism if not in production."
        )


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

    init_firebase()

    try:
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
