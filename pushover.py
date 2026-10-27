"""Module for sending notifications via Pushover."""

import asyncio
import mimetypes
import os
from pathlib import Path
from typing import Optional, Union

import requests
from requests.exceptions import ConnectionError as RequestConnectionError

from confighandler import ConfigHandler
from interface import CallbacksAbstract
from monitor import Monitor
from utils import get_ip, reboot_pi

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


class PushoverClient:
    """Client for the Pushover REST API."""

    def __init__(
        self,
        user_key: str,
        api_token: str,
        device: Optional[str] = None,
        default_priority: int = 0,
        sound: Optional[str] = None,
        api_url: str = PUSHOVER_API_URL,
    ) -> None:
        self.user_key = user_key
        self.api_token = api_token
        self.device = device
        self.default_priority = default_priority
        self.sound = sound
        self.api_url = api_url

    def is_configured(self) -> bool:
        """Check if user_key and api_token are non-empty."""
        return bool(self.user_key and self.api_token)

    def send_message(
        self,
        message: str,
        title: Optional[str] = "Apple Stock Notifier",
        priority: Optional[int] = None,
        sound: Optional[str] = None,
        device: Optional[str] = None,
        attachment_path: Optional[Union[str, Path]] = None,
        html: bool = True,
        retry_count: int = 0,
        max_retries: int = 5,
        retry_delay: float = 10.0,
    ) -> requests.Response:
        """Send a message via Pushover with retry on connection error."""
        if not self.is_configured():
            print(
                "Pushover is not configured (missing user_key or api_token). Skipping notification."
            )
            return None

        effective_priority = self.default_priority if priority is None else priority
        effective_sound = self.sound if sound is None else sound
        effective_device = self.device if device is None else device

        payload = {
            "token": self.api_token,
            "user": self.user_key,
            "message": message,
        }

        if title:
            payload["title"] = title
        if effective_priority is not None:
            payload["priority"] = effective_priority
            # Pushover emergency priority (2) requires retry and expire parameters
            if effective_priority == 2:
                payload.setdefault("retry", 60)
                payload.setdefault("expire", 3600)
        if effective_sound:
            payload["sound"] = effective_sound
        if effective_device:
            payload["device"] = effective_device
        if html:
            payload["html"] = 1

        files = None
        opened_file = None
        if attachment_path:
            p = Path(attachment_path)
            if p.is_file():
                mime_type, _ = mimetypes.guess_type(str(p))
                opened_file = open(p, "rb")
                files = {
                    "attachment": (
                        p.name,
                        opened_file,
                        mime_type or "application/octet-stream",
                    )
                }
            else:
                print(f"Pushover attachment not found at {attachment_path}")

        try:
            response = requests.post(
                self.api_url, data=payload, files=files, timeout=15
            )
            if response.status_code != 200:
                print(
                    f"Pushover notification returned status {response.status_code}: {response.text}"
                )
            return response
        except RequestConnectionError as error:
            if retry_count >= max_retries:
                print(f"Pushover retried {retry_count} times, auto-rebooting now...")
                reboot_pi()
            print(
                f"ConnectionError on sending Pushover message, waiting {retry_delay} seconds to try again. Error: {error}"
            )
            import time

            time.sleep(retry_delay)
            return self.send_message(
                message=message,
                title=title,
                priority=priority,
                sound=sound,
                device=device,
                attachment_path=attachment_path,
                html=html,
                retry_count=retry_count + 1,
                max_retries=max_retries,
                retry_delay=retry_delay,
            )
        finally:
            if opened_file:
                opened_file.close()

    async def send_message_async(
        self,
        message: str,
        title: Optional[str] = "Apple Stock Notifier",
        priority: Optional[int] = None,
        sound: Optional[str] = None,
        device: Optional[str] = None,
        attachment_path: Optional[Union[str, Path]] = None,
        html: bool = True,
    ) -> requests.Response:
        """Asynchronously send a message in a background thread."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.send_message(
                message=message,
                title=title,
                priority=priority,
                sound=sound,
                device=device,
                attachment_path=attachment_path,
                html=html,
            ),
        )


class PushoverCallbacks(CallbacksAbstract):
    """Callbacks implementation for sending notifications via Pushover."""

    def __init__(
        self,
        pushover_config: Optional[dict] = None,
        notification_prefs: Optional[dict] = None,
        client: Optional[PushoverClient] = None,
    ) -> None:
        pushover_config = pushover_config or {}
        self.notification_prefs = notification_prefs or {}

        if client is not None:
            self.client = client
        else:
            self.client = PushoverClient(
                user_key=pushover_config.get("user_key", ""),
                api_token=pushover_config.get("api_token", ""),
                device=pushover_config.get("device") or None,
                default_priority=pushover_config.get("priority", 0),
                sound=pushover_config.get("sound") or None,
            )

        self.stock_priority = pushover_config.get("stock_priority", 1)
        super().__init__()

    async def on_start(self):
        if self.notification_prefs.get("on_start", True):
            message = f"New monitoring session!\nIP address: {get_ip()}"
            print(message)
            await self.client.send_message_async(
                message, title="Apple Stock Notifier - Started"
            )

    async def on_stop(self):
        if self.notification_prefs.get("on_stop", True):
            await self.client.send_message_async(
                "This bot is done scouting the shelves, goodbye!",
                title="Apple Stock Notifier - Stopped",
            )

    async def on_stock_available(self, message):
        if self.notification_prefs.get("on_stock_available", True):
            await self.client.send_message_async(
                message,
                title="Stock Available! 🍏",
                priority=self.stock_priority,
            )

    async def on_appointment_available(self, message):
        if self.notification_prefs.get("on_appointment_available", True):
            await self.client.send_message_async(
                message,
                title="Appointment Available! 📅",
                priority=self.stock_priority,
            )

    async def on_newly_available(self):
        if self.notification_prefs.get("on_newly_available", True):
            await self.client.send_message_async(
                "🚨 <b>AVAILABLE!</b> A coveted Apple device is now available in selected stores! Check the store page immediately.",
                title="🚨 STOCK ALERT! 🚨",
                priority=self.stock_priority,
            )

    async def on_auto_report(self, report: str):
        if self.notification_prefs.get("on_auto_report", True):
            await self.client.send_message_async(report, title="Status Report")

    async def on_proxy_depletion(self, message: str):
        if self.notification_prefs.get("on_proxy_depletion", True):
            await self.client.send_message_async(message, title="Proxy Depleted")

    async def on_long_processing_warning(self, warning: str):
        if self.notification_prefs.get("on_long_processing_warning", True):
            await self.client.send_message_async(
                warning, title="Warning: Slow Processing"
            )

    async def on_connection_error(self, error: str):
        if self.notification_prefs.get("on_connection_error", True):
            await self.client.send_message_async(str(error), title="Connection Error")

    async def on_error(self, error: str, logfile_path: Path):
        if self.notification_prefs.get("on_error", True):
            msg = f"<b>Oops!</b> Something went wrong, the monitor <i>crashed</i>.\nReason: {error}"
            # Pushover accepts image attachments; if a log file exists, include recent lines
            if logfile_path is not None and Path(logfile_path).exists():
                try:
                    with open(logfile_path, "r") as fp:
                        lines = fp.readlines()
                    tail = "".join(lines[-15:])
                    if tail:
                        msg += f"\n\n<b>Recent log:</b>\n<code>{tail[:1000]}</code>"
                except Exception as log_err:
                    print(
                        f"Could not read logfile for Pushover notification: {log_err}"
                    )
            await self.client.send_message_async(
                msg, title="Monitor Crash Alert", priority=1
            )


class PushoverConnection:
    """Class for sending notifications via Pushover without Telegram."""

    def __init__(self, configurationhandler: ConfigHandler) -> None:
        self.configurationhandler = configurationhandler
        pushover_config = configurationhandler.get(["pushover"]) or {}
        notification_prefs = configurationhandler.get(["notifications"]) or {}
        self.callbacks = PushoverCallbacks(pushover_config, notification_prefs)
        self.monitor = Monitor(self.callbacks)

    def start(self):
        """Start the monitoring process with Pushover notifications."""
        print("Starting Apple Store Stock Notifier with Pushover notifications...")
        loop = asyncio.get_event_loop()
        try:
            loop.run_until_complete(self.monitor.start_monitoring())
        except KeyboardInterrupt:
            print("Interrupt received, stopping monitor...")
            loop.run_until_complete(self.monitor.stop_monitoring())


if __name__ == "__main__":
    connection = PushoverConnection(ConfigHandler())
    connection.start()
