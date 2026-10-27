"""Tests for Pushover notification support."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import ConnectionError as RequestConnectionError

from confighandler import ConfigHandler
from pushover import PushoverCallbacks, PushoverClient, PushoverConnection
from remote import CompositeCallbacks, create_connection


class MockResponse:
    def __init__(self, status_code=200, text='{"status": 1}'):
        self.status_code = status_code
        self.text = text


def test_pushover_client_unconfigured():
    client = PushoverClient(user_key="", api_token="")
    assert not client.is_configured()
    # Sending without credentials should skip and return None
    assert client.send_message("test message") is None


def test_pushover_client_send_success():
    client = PushoverClient(
        user_key="dummy_user_key_30_chars_test",
        api_token="dummy_token_30_chars_test",
        device="iphone",
        default_priority=0,
        sound="cosmic",
    )
    assert client.is_configured()

    with patch("requests.post") as mock_post:
        mock_post.return_value = MockResponse(200)

        resp = client.send_message(
            message="Test <b>stock</b> alert",
            title="Custom Title",
            priority=1,
        )

        assert resp.status_code == 200
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.pushover.net/1/messages.json"
        data = kwargs["data"]
        assert data["token"] == "dummy_token_30_chars_test"
        assert data["user"] == "dummy_user_key_30_chars_test"
        assert data["message"] == "Test <b>stock</b> alert"
        assert data["title"] == "Custom Title"
        assert data["priority"] == 1
        assert data["device"] == "iphone"
        assert data["sound"] == "cosmic"
        assert data["html"] == 1
        assert kwargs["files"] is None


def test_pushover_client_emergency_priority():
    client = PushoverClient(user_key="user", api_token="token")

    with patch("requests.post") as mock_post:
        mock_post.return_value = MockResponse(200)
        client.send_message("Emergency!", priority=2)
        _, kwargs = mock_post.call_args
        assert kwargs["data"]["priority"] == 2
        assert kwargs["data"]["retry"] == 60
        assert kwargs["data"]["expire"] == 3600


def test_pushover_client_attachment(tmp_path):
    client = PushoverClient(user_key="user", api_token="token")
    test_img = tmp_path / "test.png"
    test_img.write_bytes(b"\x89PNG\r\n\x1a\n")

    with patch("requests.post") as mock_post:
        mock_post.return_value = MockResponse(200)
        client.send_message("Message with image", attachment_path=test_img)
        _, kwargs = mock_post.call_args
        assert kwargs["files"] is not None
        assert "attachment" in kwargs["files"]
        filename, _, content_type = kwargs["files"]["attachment"]
        assert filename == "test.png"
        assert content_type == "image/png"


def test_pushover_client_connection_error_retry():
    client = PushoverClient(user_key="user", api_token="token")

    with patch("requests.post") as mock_post, patch("time.sleep"):
        mock_post.side_effect = [
            RequestConnectionError("Network down"),
            MockResponse(200),
        ]
        resp = client.send_message("Retry test", max_retries=2, retry_delay=0.01)
        assert resp.status_code == 200
        assert mock_post.call_count == 2


@pytest.mark.asyncio
async def test_pushover_client_send_message_async():
    client = PushoverClient(user_key="user", api_token="token")

    with patch("requests.post") as mock_post:
        mock_post.return_value = MockResponse(200)
        resp = await client.send_message_async("Async message", title="Async Title")
        assert resp.status_code == 200
        mock_post.assert_called_once()


@pytest.mark.asyncio
async def test_pushover_callbacks_all_events(tmp_path):
    mock_client = MagicMock(spec=PushoverClient)
    mock_client.send_message_async = MagicMock()

    # Mocking async method return value
    async def mock_async(*args, **kwargs):
        return MockResponse(200)

    mock_client.send_message_async.side_effect = mock_async

    callbacks = PushoverCallbacks(
        pushover_config={
            "user_key": "user",
            "api_token": "token",
            "stock_priority": 1,
        },
        notification_prefs={
            "on_start": True,
            "on_stop": True,
            "on_stock_available": True,
            "on_appointment_available": True,
            "on_newly_available": True,
            "on_auto_report": True,
            "on_proxy_depletion": True,
            "on_long_processing_warning": True,
            "on_connection_error": True,
            "on_error": True,
        },
        client=mock_client,
    )

    await callbacks.on_start()
    assert mock_client.send_message_async.called
    assert "Started" in mock_client.send_message_async.call_args[1]["title"]

    await callbacks.on_stop()
    assert "Stopped" in mock_client.send_message_async.call_args[1]["title"]

    await callbacks.on_stock_available("iPhone 16 Pro available!")
    assert mock_client.send_message_async.call_args[1]["priority"] == 1

    await callbacks.on_appointment_available("Slot open at 2pm")
    assert mock_client.send_message_async.call_args[1]["priority"] == 1

    await callbacks.on_newly_available()
    assert mock_client.send_message_async.call_args[1]["priority"] == 1
    assert "STOCK ALERT" in mock_client.send_message_async.call_args[1]["title"]

    await callbacks.on_auto_report("Status report: 1/3 available")
    assert mock_client.send_message_async.call_args[1]["title"] == "Status Report"

    await callbacks.on_proxy_depletion("No proxies left")
    assert mock_client.send_message_async.call_args[1]["title"] == "Proxy Depleted"

    await callbacks.on_long_processing_warning("Slow processing")
    assert (
        mock_client.send_message_async.call_args[1]["title"]
        == "Warning: Slow Processing"
    )

    await callbacks.on_connection_error("Timeout 504")
    assert mock_client.send_message_async.call_args[1]["title"] == "Connection Error"

    # Test on_error with logfile
    log_file = tmp_path / "test.log"
    log_file.write_text("line1\nline2\nTraceback: something failed\n")
    await callbacks.on_error("Test crash", log_file)
    assert mock_client.send_message_async.call_args[1]["title"] == "Monitor Crash Alert"
    assert (
        "Traceback: something failed" in mock_client.send_message_async.call_args[0][0]
    )


@pytest.mark.asyncio
async def test_pushover_callbacks_preferences_respected():
    mock_client = MagicMock(spec=PushoverClient)
    mock_client.send_message_async = MagicMock()

    callbacks = PushoverCallbacks(
        pushover_config={"user_key": "user", "api_token": "token"},
        notification_prefs={"on_start": False, "on_stock_available": False},
        client=mock_client,
    )

    await callbacks.on_start()
    assert not mock_client.send_message_async.called

    await callbacks.on_stock_available("In stock")
    assert not mock_client.send_message_async.called


@pytest.mark.asyncio
async def test_composite_callbacks():
    cb1 = MagicMock(spec=PushoverCallbacks)
    cb2 = MagicMock(spec=PushoverCallbacks)
    for cb in [cb1, cb2]:

        async def dummy(*a, **k):
            return None

        cb.on_start.side_effect = dummy
        cb.on_stock_available.side_effect = dummy

    composite = CompositeCallbacks([cb1, cb2])
    await composite.on_start()
    assert cb1.on_start.called
    assert cb2.on_start.called

    await composite.on_stock_available("Stock")
    assert cb1.on_stock_available.called
    assert cb2.on_stock_available.called


def test_create_connection_routing():
    mock_config = MagicMock(spec=ConfigHandler)

    # 1. Pushover explicitly requested
    mock_config.get.side_effect = lambda keys: {
        ("notifications",): {"service": "pushover"},
        ("telegram",): {},
        ("pushover",): {"user_key": "u", "api_token": "t"},
        ("general",): {
            "polling_interval_seconds": 60,
            "report_after_n_counts": 5,
            "data_path": "data.csv",
            "log_path": "log.txt",
            "randomize_proxies": False,
        },
        ("search",): {"device_family": "iphone", "models": ["A1"], "zip_code": "94061"},
    }.get(tuple(keys), {})

    with patch("monitor.StoreChecker"), patch("monitor.pd.DataFrame"):
        conn = create_connection(mock_config)
        assert isinstance(conn, PushoverConnection)

    # 2. Auto-detection with only Pushover credentials
    mock_config.get.side_effect = lambda keys: {
        ("notifications",): {"service": "auto"},
        ("telegram",): {"api_id": "", "bot_token": ""},
        ("pushover",): {"user_key": "u", "api_token": "t"},
        ("general",): {
            "polling_interval_seconds": 60,
            "report_after_n_counts": 5,
            "data_path": "data.csv",
            "log_path": "log.txt",
            "randomize_proxies": False,
        },
        ("search",): {"device_family": "iphone", "models": ["A1"], "zip_code": "94061"},
    }.get(tuple(keys), {})

    with patch("monitor.StoreChecker"), patch("monitor.pd.DataFrame"):
        conn = create_connection(mock_config)
        assert isinstance(conn, PushoverConnection)

    # 3. Neither configured raises ValueError
    mock_config.get.side_effect = lambda keys: {
        ("notifications",): {"service": "auto"},
        ("telegram",): {},
        ("pushover",): {},
    }.get(tuple(keys), {})

    with pytest.raises(ValueError, match="No notification credentials"):
        create_connection(mock_config)
