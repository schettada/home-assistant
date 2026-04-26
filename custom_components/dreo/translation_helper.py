"""Helper to load translated entity names from the translations JSON files."""
import json
import os

_cache: dict[str, dict] = {}

def _load(lang: str) -> dict:
    if lang not in _cache:
        path = os.path.join(os.path.dirname(__file__), "translations", f"{lang}.json")
        fallback = os.path.join(os.path.dirname(__file__), "translations", "en.json")
        try:
            with open(path, encoding="utf-8") as f:
                _cache[lang] = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            try:
                with open(fallback, encoding="utf-8") as f:
                    _cache[lang] = json.load(f)
            except Exception:
                _cache[lang] = {}
    return _cache[lang]


def translated_name(lang: str, platform: str, translation_key: str, fallback: str) -> str:
    """Return the translated entity name, falling back to the English key."""
    data = _load(lang)
    name = (
        data
        .get("entity", {})
        .get(platform, {})
        .get(translation_key, {})
        .get("name")
    )
    if not name:
        # Try English fallback
        data_en = _load("en")
        name = (
            data_en
            .get("entity", {})
            .get(platform, {})
            .get(translation_key, {})
            .get("name")
        )
    return name or fallback


def translated_device_name(lang: str, device_name: str) -> str:
    """Return the translated device type name (e.g. 'Humidifier' → 'Umidificatore')."""
    data = _load(lang)
    return data.get("device_type", {}).get(device_name, device_name)
