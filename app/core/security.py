import logging

import firebase_admin
from firebase_admin import auth, credentials

from app.core.config import settings
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
            cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
            firebase_admin.initialize_app(cred)
            logger.info(f"Firebase Admin SDK successfully initialized from path: {settings.FIREBASE_CREDENTIALS_PATH}")
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
    if settings.is_testing:
        uid = token.replace("mock_token_", "") if token.startswith("mock_token_") else "test_user"
        return {
            "uid": uid,
            "email": f"{uid}@example.com",
            "name": f"Mock User {uid}",
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
