"""Config flow for Bosso Lights — email/password."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BossoApiClient, BossoApiError, BossoAuthError
from .const import CONF_ACCESS_TOKEN, CONF_REFRESH_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class BossoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Bosso config flow."""

    VERSION = 1

    async def _async_validate_credentials(
        self, email: str, password: str
    ) -> tuple[str | None, BossoApiClient | None]:
        """Try to log in. Returns (error_key, client_on_success).

        error_key is one of: 'invalid_auth', 'cannot_connect', 'unknown'.
        """
        client = BossoApiClient(async_get_clientsession(self.hass))
        try:
            await client.async_login(email, password)
        except BossoAuthError:
            return "invalid_auth", None
        except BossoApiError as err:
            _LOGGER.error("Bosso API error during login: %s", err)
            return "cannot_connect", None
        except (asyncio.TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.error("Network error during Bosso login: %s", err)
            return "cannot_connect", None
        except Exception:  # noqa: BLE001
            # We deliberately log full traceback here so unknown bugs are
            # surfaced. The "unknown" error message tells the user something
            # went wrong without scaring them with a stack trace.
            _LOGGER.exception("Unexpected error during Bosso login")
            return "unknown", None
        return None, client

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """First step — ask for email/password and validate."""
        errors: dict[str, str] = {}

        if user_input is not None:
            error_key, client = await self._async_validate_credentials(
                user_input[CONF_EMAIL], user_input[CONF_PASSWORD]
            )
            if error_key:
                errors["base"] = error_key
            else:
                # Email is the unique identifier for this account
                await self.async_set_unique_id(user_input[CONF_EMAIL].lower())
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"Bosso ({user_input[CONF_EMAIL]})",
                    data={
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_ACCESS_TOKEN: client.access_token,
                        CONF_REFRESH_TOKEN: client.refresh_token,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Re-auth flow when refresh token expires."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask user for password again."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        email = entry.data[CONF_EMAIL]

        if user_input is not None:
            error_key, client = await self._async_validate_credentials(
                email, user_input[CONF_PASSWORD]
            )
            if error_key:
                errors["base"] = error_key
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        CONF_ACCESS_TOKEN: client.access_token,
                        CONF_REFRESH_TOKEN: client.refresh_token,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"email": email},
            errors=errors,
        )
