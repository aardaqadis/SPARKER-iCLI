"""Settings prompt persistence, credential names and explicit discovery actions."""
import json

import pytest
from textual.app import App
from textual.widgets import Button, Input, Select, Static

from termatelier.config import RuntimeConfig
from termatelier.mcp_client import MCPRegistry
from termatelier.mcp_settings import MCPSettingsScreen


class SettingsApp(App):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.returned = None

    def compose(self):
        yield Static("Painter")

    def on_mount(self):
        self.push_screen(MCPSettingsScreen(self.config), self.finished)

    def finished(self, value):
        self.returned = value


async def click_visible(pilot, screen, selector):
    screen.query_one(selector).scroll_visible(animate=False)
    await pilot.pause()
    screen.query_one(selector, Button).press()
    await pilot.pause()


async def test_settings_open_and_save_do_not_contact_service(tmp_path, monkeypatch):
    connected = []
    async def forbidden(*args, **kwargs):
        connected.append(args)
        raise AssertionError("Opening or saving must not connect")
    monkeypatch.setattr("termatelier.mcp_settings.request_async", forbidden)
    monkeypatch.setenv("SPARKER_TEST_TOKEN", "never-store-this-secret")
    config = RuntimeConfig.load(path=tmp_path / "settings.json")
    app = SettingsApp(config)
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, MCPSettingsScreen)
        assert not (tmp_path / "mcp-servers.json").exists()
        screen.query_one("#mcp-name", Input).value = "my-service"
        screen.query_one("#mcp-url", Input).value = "https://example.invalid/mcp"
        screen.query_one("#mcp-auth-env", Input).value = "SPARKER_TEST_TOKEN"
        await click_visible(pilot, screen, "#mcp-save")
        assert MCPRegistry.load(config).servers["my-service"]["auth_env"] == "SPARKER_TEST_TOKEN"
        assert "never-store-this-secret" not in (tmp_path / "mcp-servers.json").read_text()
        assert not connected
        screen.query_one("#mcp-tool", Input).value = "remove_background"
        screen.query_one("#mcp-input-key", Input).value = "image_data"
        screen.query_one("#mcp-input-mode", Select).value = "base64"
        await click_visible(pilot, screen, "#mcp-map")
        assert MCPRegistry.load(config).mappings["background-remove"]["tool"] == "remove_background"
        await pilot.press("escape")
        assert app.returned == "my-service"


async def test_settings_reject_token_value_and_invalid_command_arrays(tmp_path):
    config = RuntimeConfig.load(path=tmp_path / "settings.json")
    app = SettingsApp(config)
    async with app.run_test(size=(100,50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#mcp-name", Input).value = "service"
        screen.query_one("#mcp-url", Input).value = "https://example.invalid/mcp"
        screen.query_one("#mcp-auth-env", Input).value = "token value pasted by mistake!"
        await click_visible(pilot, screen, "#mcp-save")
        assert not (tmp_path / "mcp-servers.json").exists()
        assert "NAME" in str(screen.query_one("#mcp-status", Static).content)
        screen.query_one("#mcp-auth-env", Input).value = ""
        screen.query_one("#mcp-transport", Select).value = "stdio"
        await pilot.pause()
        assert screen.query_one("#mcp-url", Input).disabled
        assert not screen.query_one("#mcp-command", Input).disabled
        screen.query_one("#mcp-command", Input).value = "python"
        screen.query_one("#mcp-args", Input).value = '"-m server"'
        await click_visible(pilot, screen, "#mcp-save")
        assert not (tmp_path / "mcp-servers.json").exists()
        assert "JSON array" in str(screen.query_one("#mcp-status", Static).content)


async def test_settings_loads_named_connections_and_mappings(tmp_path):
    config = RuntimeConfig.load(path=tmp_path / "settings.json")
    registry = MCPRegistry.load(config)
    registry.add({"name": "saved", "transport": "stdio", "command": "node", "args": ["service.js"], "auth_env": "MY_TOKEN", "timeout": 42})
    registry.map("denoise", {"server": "saved", "tool": "denoise_pixels", "input_key": "image", "input_mode": "data-uri"})
    app = SettingsApp(config)
    async with app.run_test(size=(100,50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#mcp-saved", Select).value = "saved"
        screen.query_one("#mcp-operation", Select).value = "denoise"
        await pilot.pause()
        assert screen.query_one("#mcp-command", Input).value == "node"
        assert json.loads(screen.query_one("#mcp-args", Input).value) == ["service.js"]
        assert screen.query_one("#mcp-timeout", Input).value == "42.0"
        assert screen.query_one("#mcp-tool", Input).value == "denoise_pixels"
        assert screen.query_one("#mcp-input-mode", Select).value == "data-uri"
        await click_visible(pilot, screen, "#mcp-remove")
        assert not MCPRegistry.load(config).servers
        assert not MCPRegistry.load(config).mappings


async def test_settings_discovery_shows_schema_without_saving(tmp_path, monkeypatch):
    calls = []
    async def discovery(connection):
        calls.append(connection)
        return {"implementation": {"name": "Configured Image Service"}, "protocol": "2025-11-25",
                "tools": [{"name": "remove_background", "description": "Remove image background",
                           "inputSchema": {"type":"object", "required":["image_base64"], "properties":{"image_base64":{"type":"string"}}}}]}
    monkeypatch.setattr("termatelier.mcp_settings.request_async", discovery)
    config = RuntimeConfig.load(path=tmp_path / "settings.json")
    app = SettingsApp(config)
    async with app.run_test(size=(100,50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.query_one("#mcp-name", Input).value = "test-service"
        screen.query_one("#mcp-url", Input).value = "https://example.invalid/mcp"
        await click_visible(pilot, screen, "#mcp-discover")
        assert calls and not (tmp_path / "mcp-servers.json").exists()
        assert "remove_background" in screen.discovered
        screen.query_one("#mcp-tools", Select).value = "remove_background"
        await pilot.pause()
        assert screen.query_one("#mcp-tool", Input).value == "remove_background"
        assert "image_base64" in str(screen.query_one("#mcp-schema", Static).content)
