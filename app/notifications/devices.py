import datetime as dt

from sqlalchemy import delete, desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.push_device import PushDevice
from app.models.user import User


class PushDeviceService:
    """Owns the account ↔ device-token mapping."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def register(
        self,
        user: User,
        token: str,
        *,
        platform: str | None = None,
        now: dt.datetime | None = None,
    ) -> PushDevice:
        now = now or dt.datetime.now(dt.UTC)
        device = await self.db.scalar(select(PushDevice).where(PushDevice.token == token))
        if device is None:
            device = PushDevice(user_id=user.id, token=token, platform=platform, created_at=now)
            self.db.add(device)
        elif device.user_id != user.id:
            # Same phone, different account: the previous owner stops receiving.
            await self.db.execute(
                update(User)
                .where(User.id == device.user_id, User.fcm_token == token)
                .values(fcm_token=None)
            )
            device.user_id = user.id
        if platform:
            device.platform = platform
        device.last_seen_at = now
        user.fcm_token = token
        await self.db.flush()
        return device

    async def unregister(self, user: User, token: str) -> None:
        await self.db.execute(
            delete(PushDevice).where(PushDevice.user_id == user.id, PushDevice.token == token)
        )
        if user.fcm_token == token:
            user.fcm_token = await self._newest_token(user.id)
        await self.db.flush()

    async def unregister_all(self, user: User) -> None:
        await self.db.execute(delete(PushDevice).where(PushDevice.user_id == user.id))
        user.fcm_token = None
        await self.db.flush()

    async def tokens(self, user_id: int) -> list[str]:
        return list(
            (
                await self.db.scalars(
                    select(PushDevice.token)
                    .where(PushDevice.user_id == user_id)
                    .order_by(desc(PushDevice.last_seen_at))
                )
            ).all()
        )

    async def has_devices(self, user_id: int) -> bool:
        return (
            await self.db.scalar(select(PushDevice.id).where(PushDevice.user_id == user_id).limit(1))
        ) is not None

    async def invalidate(self, token: str) -> None:
        """Forget a token FCM reported as permanently unusable."""
        await self.db.execute(delete(PushDevice).where(PushDevice.token == token))
        owners = list(
            (await self.db.scalars(select(User).where(User.fcm_token == token))).all()
        )
        for owner in owners:
            owner.fcm_token = await self._newest_token(owner.id)
        await self.db.flush()

    async def _newest_token(self, user_id: int) -> str | None:
        return await self.db.scalar(
            select(PushDevice.token)
            .where(PushDevice.user_id == user_id)
            .order_by(desc(PushDevice.last_seen_at))
            .limit(1)
        )
