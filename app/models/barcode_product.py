import datetime as dt

from sqlalchemy import JSON, CheckConstraint, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BarcodeProduct(Base):
    __tablename__ = "barcode_products"
    __table_args__ = (
        CheckConstraint("length(trim(code)) >= 8", name="ck_barcode_product_code_min_len"),
        CheckConstraint("length(trim(product_name)) > 0", name="ck_barcode_product_name_not_blank"),
        CheckConstraint("calories_per_100g >= 0", name="ck_barcode_product_nonnegative_density"),
        CheckConstraint("calories_per_100g <= 900", name="ck_barcode_product_density_upper_bound"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Normalized digits-only GTIN / EAN-8 / UPC-A / EAN-13 / GTIN-14
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    product_name_i18n: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    calories_per_100g: Mapped[float] = mapped_column(Float, nullable=False)
    protein_per_100g: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    carbs_per_100g: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    fat_per_100g: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    serving_size_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    serving_weight_grams: Mapped[int | None] = mapped_column(Integer, nullable=True)
    package_weight_grams: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nutriscore_grade: Mapped[str | None] = mapped_column(String(4), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="openfoodfacts", nullable=False)

    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        nullable=False,
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
        nullable=False,
    )
