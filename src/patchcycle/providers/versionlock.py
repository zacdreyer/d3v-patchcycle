"""Interpret native DNF4/DNF5 versionlock listings without changing locks."""

import fnmatch
import re
from collections.abc import Callable

from patchcycle.errors import PreflightError
from patchcycle.models import UpdateInfo


def _evr(value: str) -> str:
    return value if ":" in value else f"0:{value}"


def locked_names(listing: str) -> list[str]:
    """Name patterns carrying native locks, including locks hidden by check-update."""
    if "Package name:" in listing:
        return [
            line.partition(":")[2].strip()
            for line in listing.splitlines()
            if line.startswith("Package name:")
        ]
    return [
        parts[0]
        for line in listing.splitlines()
        if not line.startswith(("#", "!", "Last metadata"))
        and len(parts := line.strip().rsplit("-", 2)) == 3
    ]


def is_held(update: UpdateInfo, listing: str, compare: Callable[[str, str], int]) -> bool:
    if "Package name:" in listing:
        return _held_v5(update, listing, compare)
    positives: list[str] = []
    candidate = f"{update.name}-{_evr(update.version_to)}.{update.arch}"
    for line in listing.splitlines():
        pattern = line.strip()
        if not pattern or pattern.startswith(("#", "Last metadata")):
            continue
        parts = pattern.lstrip("!").rsplit("-", 2)
        if len(parts) != 3 or not fnmatch.fnmatchcase(update.name, parts[0]):
            continue
        if pattern.startswith("!"):
            if fnmatch.fnmatchcase(candidate, pattern[1:]):
                return True
        else:
            positives.append(pattern)
    return bool(positives) and not any(fnmatch.fnmatchcase(candidate, p) for p in positives)


def _held_v5(update: UpdateInfo, listing: str, compare: Callable[[str, str], int]) -> bool:
    matching: list[list[str]] = []
    for block in listing.split("Package name:")[1:]:
        lines = block.strip().splitlines()
        if lines and fnmatch.fnmatchcase(update.name, lines[0].strip()):
            matching.append(
                [line.strip() for line in lines[1:] if line.strip() and not line.startswith("#")]
            )
    return bool(matching) and not any(
        all(_condition(update, condition, compare) for condition in conditions)
        for conditions in matching
    )


def _condition(update: UpdateInfo, line: str, compare: Callable[[str, str], int]) -> bool:
    match = re.fullmatch(r"(evr|epoch|arch)\s*(!=|<=|>=|=|<|>)\s*(\S+)", line)
    if not match:
        raise PreflightError("unrecognized DNF5 versionlock condition")
    key, operator, value = match.groups()
    actual = {
        "evr": _evr(update.version_to),
        "epoch": _evr(update.version_to).split(":")[0],
        "arch": update.arch,
    }[key]
    if key == "evr":
        value = _evr(value)
    if operator in ("=", "!="):
        equal = fnmatch.fnmatchcase(actual, value)
        return equal if operator == "=" else not equal
    result = compare(actual, value)
    return {"<": result < 0, "<=": result <= 0, ">": result > 0, ">=": result >= 0}[operator]
