"""Storage and content validation for GeoGuessr, independent of Discord UI/game state."""

import json
import logging
import math
import os
from typing import Any

logger = logging.getLogger(__name__)


def load_database(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load the static round database; malformed or absent data is unavailable."""
    database_path = os.fspath(path)
    os.makedirs(os.path.dirname(database_path) or ".", exist_ok=True)
    try:
        with open(database_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        logger.error("GeoGuessr static database is missing path=%s", database_path)
        return {}
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not load GeoGuessr static database path=%s", database_path)
        return {}

    if not isinstance(payload, dict):
        logger.error("GeoGuessr database must be a JSON object path=%s", database_path)
        return {}
    return payload


def load_used_images(path: str | os.PathLike[str]) -> set[str]:
    """Load round-history state; absence means a new history, corruption fails safe."""
    tracker_path = os.fspath(path)
    os.makedirs(os.path.dirname(tracker_path) or ".", exist_ok=True)
    try:
        with open(tracker_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return set()
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not load GeoGuessr used-image tracker; starting with an empty tracker")
        return set()

    if not isinstance(payload, list):
        logger.error("GeoGuessr used-image tracker is not a list; starting with an empty tracker")
        return set()
    return {item for item in payload if isinstance(item, str)}


def save_used_images(used_set: set[str], path: str | os.PathLike[str]) -> None:
    """Atomically replace generated tracker state so a crash cannot truncate it."""
    tracker_path = os.path.abspath(os.fspath(path))
    parent_dir = os.path.dirname(tracker_path)
    os.makedirs(parent_dir, exist_ok=True)
    temporary_path = f"{tracker_path}.tmp"
    try:
        with open(temporary_path, "w", encoding="utf-8") as handle:
            json.dump(sorted(item for item in used_set if isinstance(item, str)), handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, tracker_path)
    except OSError:
        logger.exception("Could not persist GeoGuessr used-image tracker path=%s", tracker_path)
        try:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        except OSError:
            logger.debug("Could not remove incomplete GeoGuessr tracker temp file", exc_info=True)
        raise


def valid_round_keys(database: dict[str, Any], images_dir: str | os.PathLike[str]) -> list[str]:
    """Return records with safe filenames, valid coordinates, and existing images."""
    valid_keys: list[str] = []
    invalid_count = 0
    image_root = os.path.abspath(os.fspath(images_dir))

    for key, record in database.items():
        if not isinstance(key, str) or not isinstance(record, dict):
            invalid_count += 1
            continue

        country_code = record.get("country_code")
        country_ar = record.get("country_ar")
        filename = record.get("filename")
        coords = record.get("coords")
        if (
            not isinstance(country_code, str)
            or len(country_code) != 2
            or not country_code.isalpha()
            or not isinstance(country_ar, str)
            or not country_ar.strip()
            or not isinstance(filename, str)
            or not filename
            or os.path.basename(filename) != filename
            or not isinstance(coords, (list, tuple))
            or len(coords) != 2
        ):
            invalid_count += 1
            continue

        latitude, longitude = coords
        if (
            not isinstance(latitude, (int, float))
            or isinstance(latitude, bool)
            or not math.isfinite(latitude)
            or not -90 <= latitude <= 90
            or not isinstance(longitude, (int, float))
            or isinstance(longitude, bool)
            or not math.isfinite(longitude)
            or not -180 <= longitude <= 180
        ):
            invalid_count += 1
            continue

        image_path = os.path.abspath(os.path.join(image_root, filename))
        try:
            contained = os.path.commonpath((image_root, image_path)) == image_root
        except ValueError:
            contained = False
        if not contained or not os.path.isfile(image_path):
            invalid_count += 1
            continue

        valid_keys.append(key)

    if invalid_count:
        logger.warning("GeoGuessr skipped %s invalid or missing content record(s)", invalid_count)
    return sorted(valid_keys)
