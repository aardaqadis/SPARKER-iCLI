"""On-demand, keyboard-accessible connection and image-tool mapping settings."""
from __future__ import annotations

import json

from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from .mcp_client import (INPUT_MODES, MCPError, MCPRegistry, OPERATIONS,
                         request_async, sanitize, validate_connection)


class MCPSettingsScreen(ModalScreen):
    DEFAULT_CSS = """
    MCPSettingsScreen { align: center middle; background: $background 65%; }
    MCPSettingsScreen > VerticalScroll { width: 82; height: 92%; max-height: 48;
        background: $panel; border: none; padding: 1 2; }
    MCPSettingsScreen Label { margin-top: 1; }
    MCPSettingsScreen Input, MCPSettingsScreen Select { width: 100%; }
    MCPSettingsScreen Horizontal { height: 3; margin-top: 1; }
    MCPSettingsScreen Button { margin-right: 1; }
    MCPSettingsScreen #mcp-title { color: $accent; text-style: bold; margin-top: 0; }
    MCPSettingsScreen #mcp-status { height: auto; margin-top: 1; }
    MCPSettingsScreen #mcp-schema { height: auto; max-height: 16; overflow-y: auto; }
    """
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.registry = MCPRegistry.load(config)
        self.discovered = {}
        self.discovery_connection = None
        self.saved_name = None
        self.busy = False

    def compose(self):
        with VerticalScroll():
            yield Label("AI / MCP services", id="mcp-title")
            yield Static("Connect your existing MCP service. Save only stores connection details; Discover tools connects when pressed. Image commands send the selected image to that service. Enter environment-variable names for credentials, never token values.", markup=False)
            yield Label("Saved connection")
            yield Select([(name, name) for name in self.registry.servers], prompt="New connection", id="mcp-saved")
            yield Label("Connection name")
            yield Input(placeholder="my-image-service", id="mcp-name")
            yield Label("Transport")
            yield Select([("Remote Streamable HTTP", "http"), ("Local service executable (stdio)", "stdio")], value="http", allow_blank=False, id="mcp-transport")
            yield Label("HTTP endpoint URL")
            yield Input(placeholder="https://your-service.example/mcp", id="mcp-url")
            yield Label("Stdio executable (no shell)")
            yield Input(placeholder="Full executable path, python, node, ...", id="mcp-command")
            yield Label("Stdio arguments (JSON array)")
            yield Input(value="[]", id="mcp-args")
            yield Label("Authentication environment-variable name (optional)")
            yield Input(placeholder="MY_IMAGE_SERVICE_TOKEN", id="mcp-auth-env")
            yield Label("Stdio additional environment-variable names (JSON array)")
            yield Input(value="[]", id="mcp-env-names")
            yield Label("Request timeout (1–600 seconds)")
            yield Input(value="90", id="mcp-timeout")
            with Horizontal():
                yield Button("Save connection", variant="primary", id="mcp-save")
                yield Button("Discover tools", id="mcp-discover")
                yield Button("Remove", id="mcp-remove")
            yield Static("No service connects until you request discovery or run an image command.", id="mcp-status", markup=False)
            yield Label("AI operation")
            yield Select([(name, name) for name in OPERATIONS], value=OPERATIONS[0], allow_blank=False, id="mcp-operation")
            yield Label("Discovered tools")
            yield Select([], prompt="Discover tools first", id="mcp-tools")
            yield Label("Tool name")
            yield Input(placeholder="Exact name exposed by your service", id="mcp-tool")
            yield Label("Image argument field (from the tool schema below)")
            yield Input(placeholder="image_base64", id="mcp-input-key")
            yield Label("Image input representation")
            yield Select([(mode, mode) for mode in INPUT_MODES], value="base64", allow_blank=False, id="mcp-input-mode")
            yield Static("Select a discovered tool to see its argument schema. Other required fields are supplied with --args JSON when running it.", id="mcp-schema", markup=False)
            with Horizontal():
                yield Button("Save mapping", id="mcp-map")
                yield Button("Close", id="mcp-close")

    def on_mount(self):
        self._transport_fields()
        self.query_one("#mcp-name", Input).focus()

    def _value(self, key):
        return self.query_one(f"#mcp-{key}").value

    def connection_values(self):
        transport = self._value("transport")
        try:
            data = {"name": self._value("name").strip(), "transport": transport,
                    "auth_env": self._value("auth-env").strip(), "timeout": float(self._value("timeout"))}
            if transport == "http":
                data["url"] = self._value("url").strip()
            else:
                data.update(command=self._value("command").strip(), args=json.loads(self._value("args")), env_names=json.loads(self._value("env-names")))
        except (ValueError, TypeError):
            raise MCPError("Timeout must be numeric; executable arguments and environment names must be valid JSON arrays.") from None
        return validate_connection(data)

    def _transport_fields(self):
        stdio = self._value("transport") == "stdio"
        self.query_one("#mcp-url", Input).disabled = stdio
        for key in ("command", "args", "env-names"):
            self.query_one(f"#mcp-{key}", Input).disabled = not stdio

    def _refresh_saved(self):
        self.query_one("#mcp-saved", Select).set_options([(name, name) for name in self.registry.servers])

    def _load_mapping(self):
        operation = self._value("operation")
        mapping = self.registry.mappings.get(operation)
        if mapping and mapping["server"] == self._value("name"):
            self.query_one("#mcp-tool", Input).value = mapping["tool"]
            self.query_one("#mcp-input-key", Input).value = mapping["input_key"]
            self.query_one("#mcp-input-mode", Select).value = mapping["input_mode"]

    def on_select_changed(self, event):
        key = event.select.id
        if key == "mcp-transport":
            self._transport_fields()
        elif key == "mcp-saved" and event.value is not Select.BLANK:
            server = self.registry.servers.get(event.value)
            if server:
                for field in ("name", "url", "command", "auth_env", "timeout"):
                    self.query_one("#mcp-" + field.replace("_", "-"), Input).value = str(server.get(field, ""))
                for field in ("args", "env_names"):
                    self.query_one("#mcp-" + field.replace("_", "-"), Input).value = json.dumps(server.get(field, []))
                self.query_one("#mcp-transport", Select).value = server["transport"]
                self.discovered = {}
                self.query_one("#mcp-tools", Select).set_options([])
                self._load_mapping()
        elif key == "mcp-operation":
            self._load_mapping()
        elif key == "mcp-tools" and event.value is not Select.BLANK:
            tool = self.discovered.get(event.value)
            if tool:
                self.query_one("#mcp-tool", Input).value = tool["name"]
                text = str(tool.get("description", "")) + "\n" + json.dumps(tool.get("inputSchema", {}), indent=2)
                self.query_one("#mcp-schema", Static).update(sanitize(text, self.discovery_connection))

    def _status(self, text):
        self.query_one("#mcp-status", Static).update(str(text))

    def on_button_pressed(self, event):
        event.stop()
        action = event.button.id
        if action == "mcp-close":
            self.action_close()
            return
        try:
            if action == "mcp-remove":
                name = self._value("name").strip()
                self.registry = MCPRegistry.load(self.config)
                self.registry.remove(name)
                self._refresh_saved()
                self._status(f"Removed {name} and its tool mappings.")
            elif action == "mcp-save":
                server = self.connection_values()
                self.registry = MCPRegistry.load(self.config)
                self.registry.add(server)
                self.saved_name = server["name"]
                self._refresh_saved()
                self._status(f"Saved {server['name']}. Token values were not stored. Use Discover tools to connect.")
            elif action == "mcp-discover" and not self.busy:
                server = self.connection_values()
                self.run_worker(self._discover(server), group="mcp-discovery", exclusive=True)
            elif action == "mcp-map":
                self.registry = MCPRegistry.load(self.config)
                operation = self._value("operation")
                self.registry.map(operation, {"server": self._value("name").strip(), "tool": self._value("tool").strip(),
                    "input_key": self._value("input-key").strip(), "input_mode": self._value("input-mode")})
                self._status(f"Saved {operation} mapping. Run ai {operation}; supply any other required fields with --args JSON.")
        except (MCPError, OSError) as error:
            self._status(error)

    async def _discover(self, server):
        self.busy = True
        self.query_one("#mcp-discover", Button).disabled = True
        self._status("Connecting and discovering tool schemas…")
        try:
            metadata = await request_async(server)
            if not self.is_mounted:
                return
            self.discovered = {tool["name"]: tool for tool in metadata["tools"]}
            self.discovery_connection = server
            self.query_one("#mcp-tools", Select).set_options([(name, name) for name in self.discovered])
            self._status(f"Connected to {sanitize(metadata['implementation']['name'], server)} · MCP {metadata['protocol']} · {len(self.discovered)} tools. Save connection, then select the tool and image field to map an operation.")
        except MCPError as error:
            if self.is_mounted:
                self._status(error)
        finally:
            self.busy = False
            if self.is_mounted:
                self.query_one("#mcp-discover", Button).disabled = False

    def action_close(self):
        self.workers.cancel_group(self, "mcp-discovery")
        self.dismiss(self.saved_name)
