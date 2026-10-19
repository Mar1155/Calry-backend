from __future__ import annotations

import datetime as dt
import logging
import math
import re
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas.meal_estimate import MealEstimateItem, MealEstimateResult
from app.models.barcode_product import BarcodeProduct
from app.schemas.meal import BarcodeProductResponse

logger = logging.getLogger("app.services.barcode_lookup")

_DIGITS_ONLY_RE = re.compile(r"\D+")
_GRAMS_IN_TEXT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:g|gr|grammi|grams|ml)\b", re.IGNORECASE)
_CACHE_TTL = dt.timedelta(days=30)
_OFF_TIMEOUT_SECONDS = 4.5
_SUPPORTED_LOCALES = ("en", "it", "es", "zh", "ja", "ar")


class InvalidBarcodeFormatError(ValueError):
    """Raised when the scanned or typed code is not a valid 8..14 digit barcode."""


class BarcodeProductNotFoundError(LookupError):
    """Raised when the barcode is not in cache or Open Food Facts (or lacks nutrition)."""


def normalize_barcode(raw_code: str) -> str:
    """Strip non-digits and validate length (EAN-8, UPC-A, EAN-13, GTIN-14)."""
    digits = _DIGITS_ONLY_RE.sub("", (raw_code or "").strip())
    if len(digits) < 8 or len(digits) > 14:
        raise InvalidBarcodeFormatError(f"Invalid barcode length ({len(digits)} digits).")
    if set(digits) == {"0"}:
        raise InvalidBarcodeFormatError("Barcode cannot be all zeros.")
    return digits


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
        if math.isfinite(parsed):
            return parsed
    except (TypeError, ValueError):
        pass
    return None


def _parse_weight_grams(raw_quantity: Any, raw_label: str | None = None) -> int | None:
    qty = _safe_float(raw_quantity)
    if qty is not None and 1 <= qty <= 5000:
        return int(round(qty))
    if raw_label:
        match = _GRAMS_IN_TEXT_RE.search(raw_label)
        if match:
            parsed = _safe_float(match.group(1).replace(",", "."))
            if parsed is not None and 1 <= parsed <= 5000:
                return int(round(parsed))
    return None


def _extract_calories_per_100g(nutriments: dict[str, Any], serving_grams: int | None) -> float | None:
    kcal_100g = _safe_float(nutriments.get("energy-kcal_100g"))
    if kcal_100g is None:
        kcal_100g = _safe_float(nutriments.get("energy-kcal_value"))
    if kcal_100g is None:
        kj_100g = _safe_float(nutriments.get("energy-kj_100g")) or _safe_float(nutriments.get("energy_100g"))
        if kj_100g is not None and kj_100g >= 0:
            kcal_100g = kj_100g / 4.184
    if kcal_100g is None and serving_grams and serving_grams > 0:
        kcal_serving = _safe_float(nutriments.get("energy-kcal_serving"))
        if kcal_serving is not None and kcal_serving >= 0:
            kcal_100g = (kcal_serving * 100.0) / serving_grams

    if kcal_100g is None or kcal_100g < 0:
        return None
    return round(min(900.0, max(0.0, kcal_100g)), 1)


def _extract_macro_per_100g(
    nutriments: dict[str, Any],
    key_prefix: str,
    serving_grams: int | None,
) -> float:
    val_100g = _safe_float(nutriments.get(f"{key_prefix}_100g"))
    if val_100g is None and serving_grams and serving_grams > 0:
        val_serving = _safe_float(nutriments.get(f"{key_prefix}_serving"))
        if val_serving is not None and val_serving >= 0:
            val_100g = (val_serving * 100.0) / serving_grams
    if val_100g is None or val_100g < 0:
        return 0.0
    return round(min(100.0, max(0.0, val_100g)), 1)


def _localized_name_from_product(product: BarcodeProduct, locale: str | None) -> str:
    lang = (locale or "en").split("-")[0].split("_")[0].lower()
    if isinstance(product.product_name_i18n, dict):
        candidate = product.product_name_i18n.get(lang) or product.product_name_i18n.get("en")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return product.product_name


def to_barcode_response(product: BarcodeProduct, locale: str | None = None) -> BarcodeProductResponse:
    localized_name = _localized_name_from_product(product, locale)
    default_grams = (
        product.serving_weight_grams
        or (
            product.package_weight_grams
            if product.package_weight_grams and product.package_weight_grams <= 250
            else None
        )
        or 100
    )
    return BarcodeProductResponse(
        code=product.code,
        product_name=localized_name,
        brand=product.brand,
        image_url=product.image_url,
        calories_per_100g=product.calories_per_100g,
        protein_per_100g=product.protein_per_100g,
        carbs_per_100g=product.carbs_per_100g,
        fat_per_100g=product.fat_per_100g,
        serving_size_label=product.serving_size_label,
        serving_weight_grams=product.serving_weight_grams,
        package_weight_grams=product.package_weight_grams,
        default_weight_grams=default_grams,
        nutriscore_grade=product.nutriscore_grade,
    )


class BarcodeLookupService:
    """Looks up packaged food nutrition by barcode via local PostgreSQL cache + Open Food Facts v2."""

    def __init__(self, db: AsyncSession, http_client: httpx.AsyncClient | None = None) -> None:
        self._db = db
        self._http_client = http_client

    async def lookup_product(self, raw_code: str, locale: str | None = None) -> BarcodeProduct:
        code = normalize_barcode(raw_code)
        now = dt.datetime.now(dt.UTC)

        # 1. Check PostgreSQL cache (also try leading-zero EAN-13 variant for 12-digit UPC-A)
        candidate_codes = [code]
        if len(code) == 12:
            candidate_codes.append(f"0{code}")
        elif len(code) == 13 and code.startswith("0"):
            candidate_codes.append(code[1:])

        stmt = select(BarcodeProduct).where(BarcodeProduct.code.in_(candidate_codes))
        result = await self._db.execute(stmt)
        cached = result.scalars().first()

        if cached is not None:
            fetched_at = cached.fetched_at
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=dt.UTC)
            if (now - fetched_at) < _CACHE_TTL or cached.source != "openfoodfacts":
                return cached

        # 2. Fetch from Open Food Facts API v2
        off_payload = await self._fetch_open_food_facts(code)
        if off_payload is None:
            if cached is not None:
                # Serve stale cache if upstream is unreachable
                return cached
            raise BarcodeProductNotFoundError(f"Product {code} not found in Open Food Facts.")

        parsed = self._parse_off_product(code, off_payload)
        if parsed is None:
            if cached is not None:
                return cached
            raise BarcodeProductNotFoundError(f"Product {code} has incomplete nutrition data.")

        if cached is not None:
            cached.product_name = parsed["product_name"]
            cached.product_name_i18n = parsed["product_name_i18n"]
            cached.brand = parsed["brand"]
            cached.image_url = parsed["image_url"]
            cached.calories_per_100g = parsed["calories_per_100g"]
            cached.protein_per_100g = parsed["protein_per_100g"]
            cached.carbs_per_100g = parsed["carbs_per_100g"]
            cached.fat_per_100g = parsed["fat_per_100g"]
            cached.serving_size_label = parsed["serving_size_label"]
            cached.serving_weight_grams = parsed["serving_weight_grams"]
            cached.package_weight_grams = parsed["package_weight_grams"]
            cached.nutriscore_grade = parsed["nutriscore_grade"]
            cached.fetched_at = now
            cached.updated_at = now
            await self._db.flush()
            return cached

        record = BarcodeProduct(
            code=code,
            product_name=parsed["product_name"],
            product_name_i18n=parsed["product_name_i18n"],
            brand=parsed["brand"],
            image_url=parsed["image_url"],
            calories_per_100g=parsed["calories_per_100g"],
            protein_per_100g=parsed["protein_per_100g"],
            carbs_per_100g=parsed["carbs_per_100g"],
            fat_per_100g=parsed["fat_per_100g"],
            serving_size_label=parsed["serving_size_label"],
            serving_weight_grams=parsed["serving_weight_grams"],
            package_weight_grams=parsed["package_weight_grams"],
            nutriscore_grade=parsed["nutriscore_grade"],
            source="openfoodfacts",
            fetched_at=now,
            updated_at=now,
        )
        self._db.add(record)
        await self._db.flush()
        return record

    async def _fetch_open_food_facts(self, code: str) -> dict[str, Any] | None:
        url = f"https://world.openfoodfacts.org/api/v2/product/{code}.json"
        params = {
            "fields": (
                "code,product_name,product_name_en,product_name_it,product_name_es,"
                "product_name_zh,product_name_ja,product_name_ar,generic_name,"
                "brands,image_front_small_url,image_front_url,nutriments,"
                "serving_size,serving_quantity,product_quantity,quantity,nutriscore_grade"
            )
        }
        headers = {
            "User-Agent": "CalryApp/1.0 (https://calry.app; support@calry.app)",
            "Accept": "application/json",
        }
        try:
            if self._http_client is not None:
                resp = await self._http_client.get(url, params=params, headers=headers, timeout=_OFF_TIMEOUT_SECONDS)
            else:
                async with httpx.AsyncClient(timeout=_OFF_TIMEOUT_SECONDS) as client:
                    resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict) or data.get("status") != 1:
                return None
            product = data.get("product")
            return product if isinstance(product, dict) else None
        except Exception as exc:
            logger.warning("openfoodfacts_lookup_failed code=%s error=%s", code, exc)
            return None

    def _parse_off_product(self, code: str, raw: dict[str, Any]) -> dict[str, Any] | None:
        nutriments = raw.get("nutriments")
        if not isinstance(nutriments, dict):
            return None

        serving_label_raw = raw.get("serving_size")
        serving_label = str(serving_label_raw).strip()[:100] if serving_label_raw else None
        serving_grams = _parse_weight_grams(raw.get("serving_quantity"), serving_label)
        package_grams = _parse_weight_grams(raw.get("product_quantity"), str(raw.get("quantity") or ""))

        calories_per_100g = _extract_calories_per_100g(nutriments, serving_grams)
        if calories_per_100g is None:
            return None

        protein_100g = _extract_macro_per_100g(nutriments, "proteins", serving_grams)
        carbs_100g = _extract_macro_per_100g(nutriments, "carbohydrates", serving_grams)
        fat_100g = _extract_macro_per_100g(nutriments, "fat", serving_grams)

        i18n_names: dict[str, str] = {}
        for lang in _SUPPORTED_LOCALES:
            val = raw.get(f"product_name_{lang}")
            if isinstance(val, str) and val.strip():
                i18n_names[lang] = val.strip()[:255]

        base_name = (
            str(raw.get("product_name") or "").strip()
            or i18n_names.get("it")
            or i18n_names.get("en")
            or str(raw.get("generic_name") or "").strip()
        )
        brands_raw = str(raw.get("brands") or "").strip()
        brand = brands_raw.split(",")[0].strip()[:255] if brands_raw else None

        if not base_name:
            if brand:
                base_name = brand
            else:
                return None

        image_url = str(raw.get("image_front_url") or raw.get("image_front_small_url") or "").strip() or None
        if image_url and len(image_url) > 1024:
            image_url = None

        nutriscore = str(raw.get("nutriscore_grade") or "").strip().lower()
        if nutriscore not in {"a", "b", "c", "d", "e"}:
            nutriscore = None

        return {
            "code": code,
            "product_name": base_name[:255],
            "product_name_i18n": i18n_names or None,
            "brand": brand,
            "image_url": image_url,
            "calories_per_100g": calories_per_100g,
            "protein_per_100g": protein_100g,
            "carbs_per_100g": carbs_100g,
            "fat_per_100g": fat_100g,
            "serving_size_label": serving_label,
            "serving_weight_grams": serving_grams,
            "package_weight_grams": package_grams,
            "nutriscore_grade": nutriscore,
        }

    def build_meal_estimate(
        self,
        product: BarcodeProduct,
        *,
        weight_grams: int | None = None,
        locale: str | None = None,
    ) -> MealEstimateResult:
        """Constructs a high-confidence MealEstimateResult from a verified barcode product."""
        response_view = to_barcode_response(product, locale)
        chosen_grams = weight_grams if (weight_grams is not None and weight_grams > 0) else response_view.default_weight_grams
        chosen_grams = max(1, min(5000, chosen_grams))

        display_name = response_view.product_name
        if product.brand and product.brand.lower() not in display_name.lower():
            meal_title = f"{display_name} ({product.brand})"[:255]
        else:
            meal_title = display_name[:255]

        if (
            product.serving_weight_grams
            and chosen_grams == product.serving_weight_grams
            and product.serving_size_label
        ):
            qty_label = product.serving_size_label
        else:
            qty_label = f"{chosen_grams} g"

        factor = chosen_grams / 100.0
        est_calories = max(0, int(math.floor(product.calories_per_100g * factor + 0.5)))
        protein_g = round(product.protein_per_100g * factor, 1)
        carbs_g = round(product.carbs_per_100g * factor, 1)
        fat_g = round(product.fat_per_100g * factor, 1)

        item = MealEstimateItem(
            name=display_name[:255],
            quantity_estimate=qty_label[:100],
            weight_grams=chosen_grams,
            calories_per_100g=product.calories_per_100g,
            protein_g=protein_g,
            carbs_g=carbs_g,
            fat_g=fat_g,
            estimated_calories=est_calories,
            image_url=product.image_url,
        )

        return MealEstimateResult(
            meal_name=meal_title,
            estimated_calories=est_calories,
            estimated_min_calories=est_calories,
            estimated_max_calories=est_calories,
            confidence="high",
            confidence_score=0.98,
            meal_category_suggestion=None,
            meal_category_confidence=None,
            source_type="barcode",
            items=[item],
            assumptions=[],
            needs_clarification=False,
            clarifying_question=None,
            model_name="barcode_openfoodfacts_v2",
            prompt_version="barcode_v1",
            total_protein_g=protein_g,
            total_carbs_g=carbs_g,
            total_fat_g=fat_g,
            estimation_reasoning=f"Barcode {product.code} ({int(round(product.calories_per_100g))} kcal/100g)",
        )
