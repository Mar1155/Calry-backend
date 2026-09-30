"""Food region (photo pin) contracts.

``DetectedRegion`` is the client-facing shape: coordinates are normalised to
0-1 of the (EXIF-upright) image, so the app maps them onto whatever frame it
renders without knowing the pixel size the model saw.

Boxes come from the photo estimate itself (one optional ``box_2d`` per item),
so every pin is labelled with an ingredient the estimate actually counted.
Earlier scans used a separate detection model (C28, ``photo_detection`` logs);
its labels drifted from the estimate's ingredients, so it was removed.
"""

from collections.abc import Iterable

from pydantic import BaseModel, Field

MAX_REGIONS = 8


class DetectedRegion(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    ymin: float = Field(ge=0, le=1)
    xmin: float = Field(ge=0, le=1)
    ymax: float = Field(ge=0, le=1)
    xmax: float = Field(ge=0, le=1)


def region_from_box(label: object, box: object) -> DetectedRegion | None:
    """Turn a Gemini-native ``box_2d`` ([ymin, xmin, ymax, xmax], 0-1000) into
    a normalised region, or None when it carries no usable location.

    Never raises: a malformed box just means no pin. Degenerate boxes
    (near-empty, or covering almost the whole frame) are dropped too."""
    label = " ".join(str(label or "").split())[:60]
    if not label or not isinstance(box, list | tuple) or len(box) != 4:
        return None
    if any(isinstance(v, bool) for v in box):
        return None
    try:
        ymin, xmin, ymax, xmax = (min(1000.0, max(0.0, float(v))) / 1000 for v in box)
    except (TypeError, ValueError):
        return None
    if ymin > ymax:
        ymin, ymax = ymax, ymin
    if xmin > xmax:
        xmin, xmax = xmax, xmin
    area = (ymax - ymin) * (xmax - xmin)
    if area < 0.0004 or area > 0.9:
        return None
    return DetectedRegion(
        label=label,
        ymin=round(ymin, 4),
        xmin=round(xmin, 4),
        ymax=round(ymax, 4),
        xmax=round(xmax, 4),
    )


def regions_from_items(items: Iterable[object]) -> list[DetectedRegion]:
    """One region per estimated item that came with a usable box, in item
    order, capped at MAX_REGIONS. Accepts MealEstimateItem objects or
    preview item dicts."""
    regions: list[DetectedRegion] = []
    for item in items:
        if isinstance(item, dict):
            name, box = item.get("name"), item.get("box_2d")
        else:
            name, box = getattr(item, "name", None), getattr(item, "box_2d", None)
        region = region_from_box(name, box)
        if region is not None:
            regions.append(region)
            if len(regions) >= MAX_REGIONS:
                break
    return regions
