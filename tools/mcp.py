"""MCP Plugin — external tools via stdio transport (JSON-RPC).

Real MCP servers are spawned as subprocesses and communicated with via
newline-delimited JSON-RPC over stdin/stdout. Mock servers remain for
testing and backward compat.
"""
import contextlib as _contextlib
import json as _json
import subprocess as _subprocess
import threading as _threading
import time as _time


class MCPClient:
    """Base client — holds tool definitions and handlers for a server."""

    def __init__(self, name: str):
        self.name = name
        self.tools: list[dict] = []
        self._handlers: dict[str, callable] = {}

    def register(self, tool_defs: list[dict], handlers: dict[str, callable]):
        self.tools = tool_defs
        self._handlers = handlers

    def call_tool(self, tool_name: str, args: dict) -> str:
        handler = self._handlers.get(tool_name)
        if not handler:
            return f"MCP error: unknown tool '{tool_name}' in server '{self.name}'"
        try:
            return handler(**args)
        except Exception as e:
            return f"MCP error: {e}"


# -- global registry -----------------------------------------------------------
mcp_clients: dict[str, MCPClient] = {}


# -- stdio transport -----------------------------------------------------------

class StdioMCPClient(MCPClient):
    """MCP client that communicates with a subprocess via stdio JSON-RPC."""

    def __init__(self, name: str, command: str):
        super().__init__(name)
        self._command = command
        self._proc: _subprocess.Popen | None = None
        self._request_id = 0
        self._lock = _threading.Lock()
        self._initialize()

    def _initialize(self):
        """Spawn the server process and perform the MCP handshake."""
        self._proc = _subprocess.Popen(
            self._command, shell=True,
            stdin=_subprocess.PIPE, stdout=_subprocess.PIPE,
            stderr=_subprocess.PIPE, text=True,
        )
        # Handshake: initialize → tools/list → register
        try:
            init_resp = self._rpc("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "51agent", "version": "0.1.0"},
            })
            if "error" in init_resp:
                raise RuntimeError(f"MCP initialize failed: {init_resp['error']}")

            # Send initialized notification (no response expected)
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

            # Discover tools
            tools_resp = self._rpc("tools/list", {})
            server_tools = tools_resp.get("result", {}).get("tools", [])
            tool_defs = []
            handlers = {}
            for tool in server_tools:
                tool_defs.append({
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("inputSchema", {"type": "object"}),
                })
                # Each tool gets a handler that calls tools/call via RPC
                tool_name = tool["name"]
                handlers[tool_name] = self._make_tool_handler(tool_name)
            self.register(tool_defs, handlers)
        except Exception:
            self._cleanup()
            raise

    def _make_tool_handler(self, tool_name: str):
        """Return a callable that invokes the MCP tool via JSON-RPC."""
        def handler(**kwargs):
            resp = self._rpc("tools/call", {"name": tool_name, "arguments": kwargs})
            if "error" in resp:
                return f"MCP tool error: {resp['error']}"
            result = resp.get("result", {})
            content = result.get("content", [])
            # Extract text from content blocks
            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif isinstance(block, str):
                    texts.append(block)
            return "\n".join(texts) if texts else _json.dumps(result)
        return handler

    def _send(self, msg: dict) -> None:
        """Send a JSON-RPC message to the server process."""
        if not self._proc or self._proc.stdin.closed:
            raise RuntimeError(f"MCP server '{self.name}' is not running")
        line = _json.dumps(msg, ensure_ascii=False)
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def _recv(self) -> dict:
        """Read a single JSON-RPC response from the server process."""
        if not self._proc or self._proc.stdout.closed:
            raise RuntimeError(f"MCP server '{self.name}' is not running")
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError(f"MCP server '{self.name}' closed stdout unexpectedly")
        return _json.loads(line)

    def _rpc(self, method: str, params: dict, timeout: float = 30) -> dict:
        """Send a JSON-RPC request and wait for the response."""
        with self._lock:
            self._request_id += 1
            req_id = self._request_id
            self._send({
                "jsonrpc": "2.0", "id": req_id,
                "method": method, "params": params,
            })
            # Read responses until we get the matching id
            deadline = _time.time() + timeout
            while _time.time() < deadline:
                resp = self._recv()
                if resp.get("id") == req_id:
                    return resp
                # Could be a notification — ignore and continue
            raise TimeoutError(f"MCP RPC '{method}' timed out after {timeout}s")

    def _cleanup(self):
        """Terminate the server process."""
        if self._proc:
            try:
                self._proc.stdin.close()
                self._proc.stdout.close()
                self._proc.stderr.close()
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                with _contextlib.suppress(Exception):
                    self._proc.kill()
            self._proc = None

    def disconnect(self):
        self._cleanup()


# -- mock servers (for testing / backward compat) ------------------------------

def _make_docs_server() -> MCPClient:
    c = MCPClient("docs")
    c.register(
        [{"name": "search", "description": "Search documentation (readOnly)",
          "input_schema": {"type": "object",
            "properties": {"query": {"type": "string"}, "max_results": {"type": "integer"}},
            "required": ["query"]}}],
        {"search": lambda query, max_results=5:
            f"[Docs search for '{query}'] Found {max_results} results. (mock)"})
    return c


def _make_deploy_server() -> MCPClient:
    c = MCPClient("deploy")
    c.register(
        [{"name": "trigger", "description": "Trigger a deployment (destructive)",
          "input_schema": {"type": "object",
            "properties": {"env": {"type": "string"}, "version": {"type": "string"}},
            "required": ["env"]}}],
        {"trigger": lambda env, version="latest":
            f"[Deploy] Triggered deployment to {env} with version {version}. (mock)"})
    return c


MOCK_SERVERS = {"docs": _make_docs_server, "deploy": _make_deploy_server}


# -- connect tool --------------------------------------------------------------

def run_connect_mcp(name: str, command: str = "") -> str:
    """Connect to an MCP server.

    If `command` is provided, spawn it as a subprocess (stdio transport).
    Otherwise, check mock servers for backward compat.
    """
    if name in mcp_clients:
        return f"MCP server '{name}' already connected"

    if command:
        try:
            client = StdioMCPClient(name, command)
        except Exception as e:
            return f"MCP connection failed: {e}"
        mcp_clients[name] = client
        tool_names = [t["name"] for t in client.tools]
        return f"Connected to '{name}' via stdio. Discovered tools: {', '.join(tool_names)}"

    factory = MOCK_SERVERS.get(name)
    if factory:
        client = factory()
        mcp_clients[name] = client
        tool_names = [t["name"] for t in client.tools]
        return f"Connected to '{name}' (mock). Discovered tools: {', '.join(tool_names)}"

    return f"Unknown server '{name}'. Pass a command to spawn, or use mock: {', '.join(MOCK_SERVERS.keys())}"
