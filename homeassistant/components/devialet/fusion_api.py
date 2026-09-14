"""Low-level Devialet IP Control v1 client used by Fusion."""

from __future__ import annotations

from typing import Any

from aiohttp import ClientSession, ClientTimeout

from .fusion_models import (
    DevialetDevice,
    DevialetEndpoint,
    DevialetGroup,
    DevialetSource,
    DevialetSystem,
    parse_device,
    parse_source,
)

DEFAULT_TIMEOUT = ClientTimeout(total=1.5)


class DevialetApiError(Exception):
    """A Devialet HTTP or logical API error."""

    def __init__(self, code: str, message: str | None = None, status: int | None = None) -> None:
        self.code = code
        self.message = message or code
        self.status = status
        super().__init__(f"{code}: {self.message}")


class DevialetIpControlClient:
    """Async client for documented Devialet IP Control endpoints.

    The API path is supplied by mDNS rather than being hard-coded, as required
    by the Devialet R1 specification.
    """

    def __init__(
        self,
        session: ClientSession,
        endpoint: DevialetEndpoint,
        *,
        timeout: ClientTimeout = DEFAULT_TIMEOUT,
    ) -> None:
        self._session = session
        self._endpoint = endpoint
        self._timeout = timeout

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self._endpoint.base_url}/{path.lstrip('/')}"
        headers = {"Content-Type": "application/json"} if method == "POST" else {}
        kwargs: dict[str, Any] = {"headers": headers, "timeout": self._timeout}
        if method == "POST":
            kwargs["json"] = payload or {}
        try:
            async with self._session.request(method, url, **kwargs) as response:
                if response.status >= 400:
                    raise DevialetApiError(
                        f"HTTP_{response.status}",
                        await response.text(),
                        response.status,
                    )
                data = await response.json(content_type=None)
        except DevialetApiError:
            raise
        except Exception as err:
            raise DevialetApiError("UNREACHABLE", str(err)) from err

        if not isinstance(data, dict):
            raise DevialetApiError("INVALID_RESPONSE", "Expected a JSON object")
        error = data.get("error")
        if isinstance(error, dict):
            raise DevialetApiError(
                str(error.get("code", "UNKNOWN")),
                str(error.get("message", "Devialet API error")),
            )
        return data

    async def get_device(self) -> DevialetDevice:
        """Read the dispatcher device identity/topology."""
        data = await self._request("GET", "/devices/current")
        installation_id = data.get("groupId") or data["deviceId"]
        return parse_device(data, self._endpoint, str(installation_id))

    async def get_system(self) -> DevialetSystem | None:
        """Read the dispatcher system, if the dispatcher is a speaker."""
        data = await self._request("GET", "/systems/current")
        if "systemId" not in data:
            return None
        return DevialetSystem(
            installation_id=str(data.get("groupId", data["systemId"])),
            system_id=str(data["systemId"]),
            group_id=str(data.get("groupId", "")),
            name=str(data.get("systemName", "")),
            available_features={str(value) for value in data.get("availableFeatures", [])},
        )

    async def get_sources(self) -> list[DevialetSource]:
        """Read all sources currently available to the dispatcher's group."""
        data = await self._request("GET", "/groups/current/sources")
        return [parse_source(item) for item in data.get("sources", [])]

    async def get_current_source(self) -> dict[str, Any]:
        """Read current source, playback state, metadata and operations."""
        return await self._request("GET", "/groups/current/sources/current")

    async def get_volume(self) -> int:
        """Read current system volume in the documented 0..100 range."""
        data = await self._request("GET", "/systems/current/sources/current/soundControl/volume")
        return max(0, min(100, int(data["volume"])))

    async def set_volume(self, volume: int) -> None:
        """Set system volume and leave confirmation to the reconciler."""
        await self._request(
            "POST",
            "/systems/current/sources/current/soundControl/volume",
            {"volume": max(0, min(100, int(volume)))},
        )

    async def volume_up(self) -> None:
        """Increase system volume by one Devialet step."""
        await self._request("POST", "/systems/current/sources/current/soundControl/volumeUp")

    async def volume_down(self) -> None:
        """Decrease system volume by one Devialet step."""
        await self._request("POST", "/systems/current/sources/current/soundControl/volumeDown")

    async def playback(self, action: str, source_id: str | None = None) -> None:
        """Execute a documented playback operation."""
        if action == "play":
            if not source_id:
                raise ValueError("source_id is required for play")
            path = f"/groups/current/sources/{source_id}/playback/play"
        elif action in {"pause", "mute", "unmute", "next", "previous"}:
            path = f"/groups/current/sources/current/playback/{action}"
        else:
            raise ValueError(f"Unsupported playback action: {action}")
        await self._request("POST", path)

    async def select_source(self, source_id: str) -> None:
        """Start playback from a source, as specified by IP Control."""
        await self.playback("play", source_id)

    async def power_off(self) -> None:
        """Power off the current system."""
        await self._request("POST", "/systems/current/powerOff")

    async def restart(self) -> None:
        """Restart the current system."""
        await self._request("POST", "/systems/current/restart")

    async def set_night_mode(self, enabled: bool) -> None:
        """Set system night mode."""
        await self._request(
            "POST",
            "/systems/current/settings/audio/nightMode",
            {"nightMode": "on" if enabled else "off"},
        )

    async def set_equalizer(self, preset: str) -> None:
        """Set the documented equalizer preset."""
        await self._request(
            "POST",
            "/systems/current/settings/audio/equalizer",
            {"preset": preset},
        )

    async def start_bluetooth_advertising(self) -> None:
        """Start the documented one-minute Bluetooth advertising window."""
        await self._request("POST", "/systems/current/bluetooth/startAdvertising")
