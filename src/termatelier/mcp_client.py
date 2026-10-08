"""Optional MCP SDK connections, persisted without credentials or model code.

Each request owns and closes its transport. Stdio starts an explicitly configured
executable with an argument list; it never invokes a shell or interprets tool text.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
from itertools import islice
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from urllib.parse import urlsplit

MAX_REGISTRY_BYTES = 262144
MAX_MESSAGE_BYTES = 32 * 1024 * 1024
MAX_SCHEMA_BYTES = 262144
MAX_TOOLS = 512
OPERATIONS = ("background-remove", "upscale", "denoise", "inpaint", "generate", "restore", "colorize")
INPUT_MODES = ("base64", "data-uri", "path", "url", "none")


class MCPError(ValueError):
    """A validated connection, protocol, schema or returned-image problem."""


def _string(value, label, maximum=2048, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()) or any(ord(c) < 32 for c in value):
        raise MCPError(f"{label} must be a single-line string of at most {maximum} characters.")
    return value


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise MCPError("Connection names use 1–64 letters, numbers, dots, underscores or hyphens.")
    return value


def endpoint(value):
    value = _string(value, "MCP URL")
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError()
        _ = parts.port
    except ValueError:
        raise MCPError("Use an HTTP(S) MCP endpoint without credentials, query tokens or fragments. Authentication uses an environment-variable name.") from None
    return value


def validate_connection(data):
    if not isinstance(data, dict) or set(data) - {"name", "transport", "url", "command", "args", "auth_env", "env_names", "timeout"}:
        raise MCPError("Unknown MCP connection fields.")
    name = _name(data.get("name"))
    transport = data.get("transport", "http")
    if transport not in ("http", "stdio"):
        raise MCPError("MCP transport must be http or stdio.")
    timeout = data.get("timeout", 90)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 600:
        raise MCPError("MCP timeout must be 1–600 seconds.")
    result = {"name": name, "transport": transport, "timeout": float(timeout)}
    env_name = data.get("auth_env", "")
    if not isinstance(env_name, str) or (env_name and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", env_name)):
        raise MCPError("Authentication must be an environment-variable NAME, never a token value.")
    result["auth_env"] = env_name
    if transport == "http":
        if data.get("command") or data.get("args") or data.get("env_names"):
            raise MCPError("Executable arguments and forwarded environment names are for stdio connections.")
        result["url"] = endpoint(data.get("url"))
    else:
        if data.get("url"):
            raise MCPError("Stdio connections use an executable, not a URL.")
        result["command"] = _string(data.get("command"), "MCP executable")
        args = data.get("args", [])
        if not isinstance(args, list) or len(args) > 64:
            raise MCPError("MCP executable arguments must be a JSON array of at most 64 strings.")
        result["args"] = [_string(item, "Executable argument", 8192, empty=True) for item in args]
        names = data.get("env_names", [])
        if not isinstance(names, list) or len(names) > 32 or any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", item) for item in names):
            raise MCPError("Forwarded environment names must be a JSON array of at most 32 variable names.")
        result["env_names"] = list(dict.fromkeys(names))
    return result


def _json(value, limit, label):
    try:
        encoded = json.dumps(value, ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise MCPError(f"{label} must be finite JSON data.") from None
    if len(encoded) > limit:
        raise MCPError(f"{label} exceeds its {limit // 1024} KiB limit.")
    return encoded


class MCPRegistry:
    def __init__(self, path, servers=None, mappings=None):
        self.path = Path(path).expanduser().resolve()
        self.servers = servers or {}
        self.mappings = mappings or {}

    @classmethod
    def load(cls, config):
        path = Path(config.path).with_name("mcp-servers.json")
        if not path.exists():
            return cls(path)
        try:
            if path.stat().st_size > MAX_REGISTRY_BYTES:
                raise MCPError("MCP settings file is too large.")
            data = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
            if not isinstance(data, dict) or data.get("format") != "sparker-mcp" or data.get("version") != 1:
                raise MCPError("Unsupported MCP settings format.")
            servers = data.get("servers", [])
            mappings = data.get("mappings", {})
            if not isinstance(servers, list) or len(servers) > 32 or not isinstance(mappings, dict):
                raise MCPError("Invalid MCP settings structure.")
            result = cls(path)
            for item in servers:
                server = validate_connection(item)
                if server["name"] in result.servers:
                    raise MCPError("Duplicate MCP connection name.")
                result.servers[server["name"]] = server
            for operation, mapping in mappings.items():
                result.mappings[operation] = result.validate_mapping(operation, mapping)
            return result
        except (OSError, ValueError, UnicodeError, TypeError, RecursionError) as error:
            if isinstance(error, MCPError):
                raise
            raise MCPError("Could not read MCP settings. Fix or remove mcp-servers.json beside the preferences file.") from None

    def validate_mapping(self, operation, mapping):
        if operation not in OPERATIONS or not isinstance(mapping, dict) or set(mapping) - {"server", "tool", "input_key", "input_mode"}:
            raise MCPError("Choose a supported AI operation and mapping fields.")
        server = _name(mapping.get("server"))
        if server not in self.servers:
            raise MCPError("Add the MCP connection before mapping a tool.")
        tool = _string(mapping.get("tool"), "MCP tool name", 256)
        mode = mapping.get("input_mode", "base64")
        if mode not in INPUT_MODES:
            raise MCPError("Input mode must be base64, data-uri, path, url or none.")
        key = _string(mapping.get("input_key", ""), "Image input field", 256, empty=mode == "none")
        if mode == "path" and self.servers[server]["transport"] != "stdio":
            raise MCPError("A remote service cannot read local file paths. Use base64, data-uri or an explicit public URL.")
        return {"server": server, "tool": tool, "input_key": key, "input_mode": mode}

    def save(self):
        encoded = _json({"format": "sparker-mcp", "version": 1, "servers": list(self.servers.values()), "mappings": self.mappings}, MAX_REGISTRY_BYTES, "MCP settings")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="mcp-settings-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(encoded + "\n")
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def add(self, connection):
        connection = validate_connection(connection)
        if connection["name"] not in self.servers and len(self.servers) >= 32:
            raise MCPError("At most 32 MCP connections are supported.")
        self.servers[connection["name"]] = connection
        self.save()
        return connection

    def remove(self, name):
        if name not in self.servers:
            raise MCPError("Unknown MCP connection.")
        del self.servers[name]
        self.mappings = {key: value for key, value in self.mappings.items() if value["server"] != name}
        self.save()

    def map(self, operation, mapping):
        self.mappings[operation] = self.validate_mapping(operation, mapping)
        self.save()

    def server(self, name=None):
        if name is None and len(self.servers) == 1:
            return next(iter(self.servers.values()))
        if name not in self.servers:
            raise MCPError("Choose --server NAME, or open ai settings to add your existing MCP service.")
        return self.servers[name]


def _secrets(connection):
    names = list(connection.get("env_names", [])) + [connection.get("auth_env", "")]
    return [os.environ[name] for name in names if name and os.environ.get(name)]


def sanitize(value, connection=None):
    """Keep service text inert, compact and free of configured token values."""
    secrets = _secrets(connection or {})
    def clean(item):
        if isinstance(item, str):
            for secret in secrets:
                item = item.replace(secret, "[redacted]")
            item = re.sub(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", "[image data]", item)
            item = re.sub(r"([?&](?:token|key|api_key|access_token)=)[^&\s]+", r"\1[redacted]", item, flags=re.I)
            return "".join(c for c in item if c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) <= 159))[:8192]
        if isinstance(item, list):
            return [clean(child) for child in item[:512]]
        if isinstance(item, dict):
            return {clean(str(key)): ("[image data]" if key in ("image_base64", "base64", "data") and isinstance(child, str) and len(child) > 256 else clean(child)) for key, child in list(item.items())[:512]}
        return item
    return clean(value)


def sdk_available():
    try:
        import mcp  # noqa: F401
        import jsonschema  # noqa: F401
        return True
    except ImportError:
        return False


def _require_sdk():
    if not sdk_available():
        raise MCPError('MCP support is optional. Install SPARKER with the mcp extra: python -m pip install "sparker-icli[mcp]". In a source checkout use python -m pip install ".[mcp]" in its virtual environment.')


def validate_arguments(tool, arguments):
    _require_sdk()
    if not isinstance(arguments, dict):
        raise MCPError("Tool arguments must be a JSON object.")
    _json(arguments, MAX_MESSAGE_BYTES, "Tool arguments")
    schema = tool.get("inputSchema", {})
    _json(schema, MAX_SCHEMA_BYTES, "Tool schema")
    # MCP schemas are supplied by the service. Never resolve external $refs.
    def check_refs(item, depth=0):
        if depth > 64:
            raise MCPError("Tool schema is nested too deeply.")
        if isinstance(item, dict):
            if "$ref" in item and (not isinstance(item["$ref"], str) or not item["$ref"].startswith("#/")):
                raise MCPError("External schema references are not supported; the service must return a self-contained schema.")
            for value in item.values():
                check_refs(value, depth + 1)
        elif isinstance(item, list):
            for value in item:
                check_refs(value, depth + 1)
    check_refs(schema)
    import jsonschema
    try:
        validator = jsonschema.validators.validator_for(schema)
        validator.check_schema(schema)
        errors = list(islice(validator(schema).iter_errors(arguments), 10))
        if errors:
            missing = [error.validator_value for error in errors if error.validator == "required"]
            path = ".".join(map(str, errors[0].absolute_path)) or "arguments"
            detail = " Required fields: " + ", ".join(dict.fromkeys(str(key) for keys in missing for key in keys)) if missing else ""
            raise MCPError(f"Arguments do not match {tool['name']}'s input schema at {path}.{detail} Use mcp tools SERVER --json to inspect the schema, then supply --args JSON.")
    except (jsonschema.SchemaError, RecursionError):
        raise MCPError("The service supplied an invalid or recursively unsupported input schema.") from None


async def request_async(connection, tool_name=None, arguments=None, prepare=None):
    """Initialize, discover and optionally invoke a schema-validated tool."""
    connection = validate_connection(connection)
    _require_sdk()
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client
    import httpx
    timeout = connection["timeout"]
    env_name = connection.get("auth_env")
    if env_name and not os.environ.get(env_name):
        raise MCPError(f"Environment variable {env_name} is not set. Set it outside SPARKER; token values are never stored.")

    class BoundedStream(httpx.AsyncByteStream):
        def __init__(self, wrapped):
            self.wrapped = wrapped
        async def __aiter__(self):
            count = 0
            async for chunk in self.wrapped:
                count += len(chunk)
                if count > MAX_MESSAGE_BYTES:
                    raise MCPError("MCP response exceeds the 32 MiB message limit.")
                yield chunk
        async def aclose(self):
            await self.wrapped.aclose()

    async def bounded_response(response):
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise MCPError("MCP service returned compressed transport data despite requesting identity encoding; return uncompressed JSON/SSE for bounded image transfers.")
        length = response.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > MAX_MESSAGE_BYTES:
            raise MCPError("MCP response exceeds the 32 MiB message limit.")
        response.stream = BoundedStream(response.stream)

    try:
        async with asyncio.timeout(timeout):
            async with AsyncExitStack() as stack:
                if connection["transport"] == "stdio":
                    env_names = connection.get("env_names", []) + ([env_name] if env_name else [])
                    env = {key: os.environ[key] for key in env_names if key in os.environ}
                    errlog = stack.enter_context(open(os.devnull, "w", encoding="utf-8"))
                    read, write = await stack.enter_async_context(stdio_client(StdioServerParameters(command=connection["command"], args=connection["args"], env=env), errlog=errlog))
                else:
                    headers = {"Accept-Encoding": "identity"}
                    if env_name:
                        headers["Authorization"] = "Bearer " + os.environ[env_name]
                    client = await stack.enter_async_context(httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=False, event_hooks={"response": [bounded_response]}))
                    read, write, _ = await stack.enter_async_context(streamable_http_client(connection["url"], http_client=client))
                session = await stack.enter_async_context(ClientSession(read, write, read_timeout_seconds=timedelta(seconds=timeout)))
                initialized = await session.initialize()
                tools, seen, cursor = [], set(), None
                for _ in range(32):
                    page = await session.list_tools(cursor=cursor)
                    for item in page.tools:
                        value = item.model_dump(mode="json", by_alias=True, exclude_none=True)
                        _json(value, MAX_SCHEMA_BYTES, "Tool metadata")
                        if any(len(secret) >= 8 and secret in value["name"] for secret in _secrets(connection)):
                            raise MCPError("The service returned a tool name containing private credentials; discovery was rejected.")
                        if value["name"] in seen:
                            raise MCPError("Service returned duplicate tool names.")
                        seen.add(value["name"])
                        tools.append(value)
                        if len(tools) > MAX_TOOLS:
                            raise MCPError("Service exposes more than 512 tools; use a smaller image-tool service.")
                    cursor = page.nextCursor
                    if not cursor:
                        break
                else:
                    raise MCPError("MCP tool pagination did not finish.")
                metadata = {"server": connection["name"], "protocol": initialized.protocolVersion,
                            "implementation": initialized.serverInfo.model_dump(mode="json"), "tools": tools}
                if tool_name is None:
                    return metadata
                tool = next((item for item in tools if item["name"] == tool_name), None)
                if tool is None:
                    raise MCPError("Tool is not exposed by this MCP service. Use mcp tools SERVER to discover available names.")
                args = prepare(tool) if prepare else arguments
                validate_arguments(tool, args)
                result = await session.call_tool(tool_name, args, read_timeout_seconds=timedelta(seconds=timeout))
                result = result.model_dump(mode="json", by_alias=True, exclude_none=True)
                _json(result, MAX_MESSAGE_BYTES, "Tool response")
                if result.get("isError"):
                    raise MCPError("The MCP tool reported an error: " + str(sanitize(result.get("content", []), connection))[:2048])
                return {"server": connection["name"], "tool": tool_name, "result": result}
    except MCPError as error:
        raise MCPError(sanitize(str(error), connection)) from None
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        # SDK task groups often wrap exceptions. Never echo traceback arguments,
        # HTTP headers, auth-bearing URLs or service-owned executable output.
        def leaves(item):
            return [leaf for child in item.exceptions for leaf in leaves(child)] if isinstance(item, BaseExceptionGroup) else [item]
        nested = leaves(error)
        reason = next((str(item) for item in nested if isinstance(item, MCPError)), None)
        if reason:
            raise MCPError(sanitize(reason, connection)) from None
        if any(isinstance(item, (TimeoutError, httpx.TimeoutException)) for item in nested):
            raise MCPError(f"MCP request timed out after {timeout:g} seconds; the connection was closed.") from None
        raise MCPError(f"MCP connection failed ({type(nested[0]).__name__}). Check the endpoint/executable, authentication environment and service status.") from None


def request(connection, tool_name=None, arguments=None, prepare=None):
    """Synchronous adapter also usable from an already-running Textual loop."""
    def run():
        return asyncio.run(request_async(connection, tool_name, arguments, prepare))
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return run()
    # UI callers normally run editing commands in their own worker. This fallback
    # avoids nested asyncio loops; caller controls whether to yield its UI.
    result, failures = [], []
    def worker():
        try:
            result.append(run())
        except BaseException as error:
            failures.append(error)
    thread = threading.Thread(target=worker, name="sparker-mcp-request", daemon=True)
    thread.start()
    thread.join()
    if failures:
        raise failures[0]
    return result[0]
