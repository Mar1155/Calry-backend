import re

_VERSION_RE = re.compile(r"^\d{1,6}(\.\d{1,6}){0,3}$")

PLATFORMS = ("ios", "android")


def is_valid_version(value: str | None) -> bool:
    return bool(value) and _VERSION_RE.fullmatch(value.strip()) is not None


def parse_version(value: str) -> tuple[int, ...]:
    """Parse a dotted numeric version. Build/pre-release suffixes (`+42`, `-beta`) are ignored."""
    core = value.strip().split("+", 1)[0].split("-", 1)[0]
    if not _VERSION_RE.fullmatch(core):
        raise ValueError(f"Invalid app version: {value!r}")
    return tuple(int(part) for part in core.split("."))


def compare_versions(left: str, right: str) -> int:
    a, b = parse_version(left), parse_version(right)
    size = max(len(a), len(b))
    a += (0,) * (size - len(a))
    b += (0,) * (size - len(b))
    return (a > b) - (a < b)


def normalize_platform(value: str | None) -> str | None:
    if not value:
        return None
    platform = value.strip().lower()
    return platform if platform in PLATFORMS else None
