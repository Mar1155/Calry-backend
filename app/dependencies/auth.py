from fastapi import Depends, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthException
from app.core.security import verify_firebase_token
from app.dependencies.db import get_db
from app.models.user import User
from app.repositories.user import UserRepository

# Initialize standard Bearer HTTP verification scheme
security = HTTPBearer(auto_error=True)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency to retrieve the authenticated user.

    1. Extracts the Bearer token from the Authorization header.
    2. Validates it via Firebase (supports mock bypasses in development).
    3. Looks up the corresponding user record in the local database.
    4. Auto-creates a new user profile on the fly if this is their first login.
    """
    token = credentials.credentials
    if not token:
        raise AuthException(
            message="Bearer token is missing from the Authorization header.",
            error_code="MISSING_BEARER_TOKEN",
        )

    # Decode and verify the JWT with Firebase Admin SDK
    payload = verify_firebase_token(token)

    firebase_uid = payload.get("uid")
    email = payload.get("email")
    name = payload.get("name")

    if not firebase_uid or not email:
        raise AuthException(
            message="Token payload is missing required Firebase identifiers (uid, email).",
            error_code="MALFORMED_JWT_PAYLOAD",
        )

    user_repo = UserRepository(db)
    user = await user_repo.get_by_firebase_uid(firebase_uid)

    if not user:
        # Check if a user with the same email already exists (e.g. from previous mock runs)
        user = await user_repo.get_by_email(email)
        if user:
            # Link the existing user record to the new Firebase UID
            update_data = {"firebase_uid": firebase_uid}
            if name:
                update_data["name"] = name
            user = await user_repo.update(user, update_data)
        else:
            # AUTO-CREATE: first-time login triggers automated local user registration
            user = User(
                firebase_uid=firebase_uid,
                email=email,
                name=name,
                daily_calorie_goal=2000,  # Standard baseline recommendation
                goal_type="maintain",
            )
            await user_repo.create(user)
            await db.flush()

    return user
