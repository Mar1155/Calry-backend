from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Generic async repository that provides common database CRUD routines.

    Leverages SQLAlchemy 2.0 typing and async patterns.
    """

    def __init__(self, model: type[ModelType], db: AsyncSession):
        self.model = model
        self.db = db

    async def get(self, id: Any) -> ModelType | None:
        """Retrieves a single record by its primary key ID."""
        return await self.db.get(self.model, id)

    async def get_multi(self, skip: int = 0, limit: int = 100) -> Sequence[ModelType]:
        """Fetches multiple records with limit and offset pagination support."""
        stmt = select(self.model).offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def create(self, obj_in: dict[str, Any] | Any) -> ModelType:
        """Adds a new record to the database and flushes it to assign identifiers."""
        if isinstance(obj_in, dict):
            db_obj = self.model(**obj_in)
        else:
            db_obj = obj_in
        self.db.add(db_obj)
        await self.db.flush()
        return db_obj

    async def update(self, db_obj: ModelType, obj_in: dict[str, Any] | Any) -> ModelType:
        """Updates fields of an existing model instance in place and flushes changes."""
        if isinstance(obj_in, dict):
            update_data = obj_in
        else:
            update_data = obj_in.model_dump(exclude_unset=True)

        for field, value in update_data.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)

        self.db.add(db_obj)
        await self.db.flush()
        return db_obj

    async def remove(self, id: Any) -> ModelType | None:
        """Deletes a record by its primary key ID and returns the deleted object if found."""
        obj = await self.db.get(self.model, id)
        if obj:
            await self.db.delete(obj)
            await self.db.flush()
        return obj
