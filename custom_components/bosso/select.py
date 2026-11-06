"""Bosso preset select platform.

For each Bosso device we create TWO select entities:
- select.<device>_predefined_preset  (built-in Bosso presets)
- select.<device>_user_preset        (user-defined presets)

The two are intentionally separate dropdowns so users can scan each
catalog without prefixes mixing them. Selecting "None" in either
dropdown is a no-op (HA's select doesn't have a clear concept, so
"None" acts as the unset state).
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PRESET_NONE_LABEL
from .coordinator import BossoCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the preset select entities for each Bosso device."""
    coordinator: BossoCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data:
        return

    entities: list[SelectEntity] = []
    for device_id in coordinator.data:
        entities.append(
            BossoPresetSelect(
                coordinator,
                device_id,
                category="predefined",
                name_suffix="Predefined Preset",
            )
        )
        entities.append(
            BossoPresetSelect(
                coordinator,
                device_id,
                category="user",
                name_suffix="User Preset",
            )
        )

    async_add_entities(entities)


class BossoPresetSelect(CoordinatorEntity[BossoCoordinator], SelectEntity):
    """A dropdown for one preset category (predefined or user) on one device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BossoCoordinator,
        device_id: int,
        category: str,
        name_suffix: str,
    ) -> None:
        """Initialize the select entity.

        Args:
            coordinator: The shared coordinator with preset caches.
            device_id: The Bosso device id this select controls.
            category: 'predefined' or 'user'.
            name_suffix: Display name (e.g. 'Predefined Preset').
        """
        super().__init__(coordinator)
        self._device_id = device_id
        self._category = category
        self._attr_name = name_suffix
        self._attr_unique_id = f"bosso_{device_id}_{category}_preset"
        self._attr_icon = "mdi:palette" if category == "predefined" else "mdi:palette-outline"

        # Bind to the same DeviceInfo as the light entity so HA groups them
        # together under the same device card.
        device = coordinator.data[device_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(device_id))},
            manufacturer="Bosso",
            model=device.get("ver", "Bosso Controller"),
            name=device.get("name") or f"Bosso {device_id}",
            sw_version=device.get("ver"),
            connections={("mac", device["mac"])} if device.get("mac") else set(),
        )

    # ----------------------------------------------------------- Helpers
    @property
    def _name_to_id_map(self) -> dict[str, int]:
        if self._category == "predefined":
            return self.coordinator.predefined_preset_name_to_id
        return self.coordinator.user_preset_name_to_id

    @property
    def _id_to_name_map(self) -> dict[int, str]:
        if self._category == "predefined":
            return self.coordinator.predefined_preset_id_to_name
        return self.coordinator.user_preset_id_to_name

    @property
    def _device(self) -> dict[str, Any]:
        return self.coordinator.data.get(self._device_id, {})

    @property
    def _state(self) -> dict[str, Any]:
        return self._device.get("state") or {}

    # -------------------------------------------------- HA select hooks
    @property
    def options(self) -> list[str]:
        """Dropdown options for this category."""
        if self._category == "predefined":
            return self.coordinator.predefined_preset_options
        return self.coordinator.user_preset_options

    @property
    def current_option(self) -> str | None:
        """Currently selected preset.

        Resolution order:
        1. Locally-remembered "last applied preset" (set right after
           the user picked one via this dropdown). This is the most
           reliable signal because the backend's current_preset field
           may not always reflect post-apply state.
        2. Backend's `state.current_preset` field.
        3. 'None' if neither resolves.

        We only show the preset name if it belongs to *this* category's
        catalog; otherwise we show 'None' so the dropdown doesn't claim
        a preset that lives in the other dropdown.
        """
        # 1. Check local memory first
        last_id = self.coordinator.get_last_applied_preset(self._device_id)
        if last_id is not None:
            name = self._id_to_name_map.get(last_id)
            if name is not None:
                return name
            # Last applied belongs to the OTHER category — show None here
            return PRESET_NONE_LABEL

        # 2. Fall back to backend state
        current_preset_id = self._state.get("current_preset")
        if current_preset_id is None or current_preset_id == 0:
            return PRESET_NONE_LABEL

        name = self._id_to_name_map.get(current_preset_id)
        if name is not None:
            return name

        # Preset id is set but doesn't match this category's catalog
        return PRESET_NONE_LABEL

    @property
    def available(self) -> bool:
        return self._device.get("status") == "online" and super().available

    async def async_select_option(self, option: str) -> None:
        """Apply the selected preset, or do nothing for 'None'."""
        if option == PRESET_NONE_LABEL:
            _LOGGER.debug(
                "User selected 'None' for %s preset on device %d — no-op",
                self._category,
                self._device_id,
            )
            return

        preset_id = self._name_to_id_map.get(option)
        if preset_id is None:
            _LOGGER.warning(
                "Preset '%s' not found in %s catalog for device %d",
                option,
                self._category,
                self._device_id,
            )
            return

        _LOGGER.info(
            "Applying %s preset '%s' (id=%d) to device %d",
            self._category,
            option,
            preset_id,
            self._device_id,
        )
        await self.coordinator.async_apply_preset(self._device_id, preset_id)

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
