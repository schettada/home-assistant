"""Bosso light platform — one HA light per Bosso device.

V2 features:
- on/off
- brightness
- RGB color
- color temperature
- effects (mapped to Bosso fx IDs)
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CCT_MAX_KELVIN,
    CCT_MIN_KELVIN,
    DOMAIN,
    EFFECT_ID_SOLID,
    EFFECT_NAME_SOLID,
)
from .coordinator import BossoCoordinator

_LOGGER = logging.getLogger(__name__)


def _kelvin_to_cct(kelvin: int) -> int:
    """Convert HA Kelvin to Bosso cct (0-255)."""
    kelvin = max(CCT_MIN_KELVIN, min(CCT_MAX_KELVIN, kelvin))
    ratio = (kelvin - CCT_MIN_KELVIN) / (CCT_MAX_KELVIN - CCT_MIN_KELVIN)
    return int(ratio * 255)


def _cct_to_kelvin(cct: int) -> int:
    """Convert Bosso cct (0-255) to HA Kelvin."""
    cct = max(0, min(255, cct))
    ratio = cct / 255
    return int(CCT_MIN_KELVIN + ratio * (CCT_MAX_KELVIN - CCT_MIN_KELVIN))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Bosso lights from a config entry."""
    coordinator: BossoCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data:
        # First refresh produced no devices — nothing to set up.
        # The coordinator will pick up devices on the next successful poll
        # and HA will call async_setup_entry again on integration reload.
        return
    entities = [
        BossoLight(coordinator, device_id) for device_id in coordinator.data
    ]
    async_add_entities(entities)


class BossoLight(CoordinatorEntity[BossoCoordinator], LightEntity):
    """A single Bosso device exposed as a light entity."""

    _attr_has_entity_name = True
    _attr_name = None  # use device name as entity name
    _attr_supported_color_modes = {ColorMode.RGB, ColorMode.COLOR_TEMP}
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_min_color_temp_kelvin = CCT_MIN_KELVIN
    _attr_max_color_temp_kelvin = CCT_MAX_KELVIN

    def __init__(self, coordinator: BossoCoordinator, device_id: int) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"bosso_{device_id}"

        device = coordinator.data[device_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(device_id))},
            manufacturer="Bosso",
            model=device.get("ver", "Bosso Controller"),
            name=device.get("name") or f"Bosso {device_id}",
            sw_version=device.get("ver"),
            connections={("mac", device["mac"])} if device.get("mac") else set(),
        )

    # ------------------------------------------------------------- Helpers
    @property
    def _device(self) -> dict[str, Any]:
        return self.coordinator.data.get(self._device_id, {})

    @property
    def _state(self) -> dict[str, Any]:
        return self._device.get("state") or {}

    # ----------------------------------------------------- Properties read
    @property
    def available(self) -> bool:
        return self._device.get("status") == "online" and super().available

    @property
    def is_on(self) -> bool | None:
        return self._state.get("on")

    @property
    def brightness(self) -> int | None:
        # Bosso `bri` is already 0-255, same as HA — no conversion.
        return self._state.get("bri")

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Pick the first color slot from `col` and drop the W channel."""
        col = self._state.get("col")
        if not col or not isinstance(col, list) or not col[0]:
            return None
        first = col[0]
        if len(first) < 3:
            return None
        return (first[0], first[1], first[2])

    @property
    def color_temp_kelvin(self) -> int | None:
        if not self._state.get("is_cct_enabled"):
            return None
        cct = self._state.get("cct")
        return _cct_to_kelvin(cct) if cct is not None else None

    @property
    def color_mode(self) -> ColorMode | None:
        if self._state.get("is_cct_enabled"):
            return ColorMode.COLOR_TEMP
        return ColorMode.RGB

    @property
    def effect_list(self) -> list[str] | None:
        """Return the list of available effect names."""
        return self.coordinator.effect_list

    @property
    def effect(self) -> str | None:
        """Return the current effect name (or 'Solid' if no effect active)."""
        fx_id = self._state.get("fx", EFFECT_ID_SOLID)
        return self.coordinator.effect_id_to_name.get(fx_id, EFFECT_NAME_SOLID)

    # -------------------------------------------------- Commands → backend
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, optionally setting brightness / color / CCT / effect.

        Strategy:
        - On/off and brightness go to PATCH /state/ (simple, fast)
        - Color, CCT, and effect go to POST /apply-to-home/ (rich payload)
        - If both are needed, send PATCH first then apply-to-home
        """
        # ---- Step 1: PATCH state for on/off + brightness ----------------
        state_patch: dict[str, Any] = {"on": True}
        if ATTR_BRIGHTNESS in kwargs:
            state_patch["bri"] = kwargs[ATTR_BRIGHTNESS]
        await self.coordinator.async_send_state(self._device_id, state_patch)

        # ---- Step 2: apply-to-home for color / CCT / effect -------------
        if (
            ATTR_RGB_COLOR in kwargs
            or ATTR_COLOR_TEMP_KELVIN in kwargs
            or ATTR_EFFECT in kwargs
        ):
            payload = self._build_apply_to_home_payload(kwargs)
            await self.coordinator.async_apply_to_home(payload)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_send_state(self._device_id, {"on": False})

    def _build_apply_to_home_payload(
        self, kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        """Construct the payload for /controller/apply-to-home/.

        Confirmed working backend payload shape:
            {
              "device": {"device": 997},
              "cct": 255,
              "col": [[255,0,0,0], [0,0,0,0], [0,0,0,0]],
              "pal": 0,
              "fx": 0
            }
        """
        current_state = self._state

        # ---- Active color for col[0] ------------------------------------
        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            active_color = [r, g, b, 0]
        else:
            current_col = current_state.get("col") or [[255, 255, 255, 0]]
            active_color = current_col[0] if current_col else [255, 255, 255, 0]
            while len(active_color) < 4:
                active_color.append(0)
            active_color = active_color[:4]
            active_color[3] = 0

        col = [active_color, [0, 0, 0, 0], [0, 0, 0, 0]]

        # ---- CCT --------------------------------------------------------
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            cct = _kelvin_to_cct(kwargs[ATTR_COLOR_TEMP_KELVIN])
        else:
            cct = current_state.get("cct", 255)

        # ---- Effect (fx) ------------------------------------------------
        if ATTR_EFFECT in kwargs:
            effect_name = kwargs[ATTR_EFFECT]
            fx = self.coordinator.effect_name_to_id.get(
                effect_name, EFFECT_ID_SOLID
            )
        else:
            # If user is changing color/CCT but not effect, switch to Solid
            # (otherwise the active effect would override the new color).
            # This matches how most lighting apps behave.
            if ATTR_RGB_COLOR in kwargs or ATTR_COLOR_TEMP_KELVIN in kwargs:
                fx = EFFECT_ID_SOLID
            else:
                fx = current_state.get("fx", EFFECT_ID_SOLID)

        return {
            "device": {"device": self._device_id},
            "cct": cct,
            "col": col,
            "pal": current_state.get("pal", 0),
            "fx": fx,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Re-render entity when coordinator updates."""
        self.async_write_ha_state()
