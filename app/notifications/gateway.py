from firebase_admin import messaging

from app.core.security import init_firebase


class PushDeliveryError(RuntimeError):
    def __init__(self, code: str, *, transient: bool, invalidate_token: bool = False):
        super().__init__(code)
        self.code = code
        self.transient = transient
        self.invalidate_token = invalidate_token


_TRANSIENT_ERRORS = frozenset(
    {
        "AbortedError",
        "CancelledError",
        "UnavailableError",
        "ResourceExhaustedError",
        "QuotaExceededError",
        "InternalError",
        "DeadlineExceededError",
    }
)
_INVALID_TOKEN_ERRORS = frozenset({"UnregisteredError", "SenderIdMismatchError"})


class FirebasePushGateway:
    async def send(
        self,
        *,
        token: str,
        title: str,
        body: str,
        data: dict[str, str],
    ) -> str:
        try:
            init_firebase()
            return messaging.send(
                messaging.Message(
                    token=token,
                    notification=messaging.Notification(title=title, body=body),
                    data=data,
                    android=messaging.AndroidConfig(priority="normal"),
                    apns=messaging.APNSConfig(
                        headers={"apns-priority": "5"},
                        payload=messaging.APNSPayload(
                            aps=messaging.Aps(sound="default", content_available=False)
                        ),
                    ),
                )
            )
        except Exception as exc:
            name = type(exc).__name__
            raise PushDeliveryError(
                name,
                transient=name in _TRANSIENT_ERRORS,
                invalidate_token=name in _INVALID_TOKEN_ERRORS,
            ) from exc
