"""s19: MCP Plugin — external tools via standard protocol."""
import re
from config import normalize_name


class MCPClient:
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
            return f"MCP error: unknown tool '{tool_name}'"
        try:
            return handler(**args)
        except Exception as e:
            return f"MCP error: {e}"


mcp_clients: dict[str, MCPClient] = {}


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


def run_connect_mcp(name: str) -> str:
    if name in mcp_clients:
        return f"MCP server '{name}' already connected"
    factory = MOCK_SERVERS.get(name)
    if not factory:
        return f"Unknown server '{name}'. Available: {', '.join(MOCK_SERVERS.keys())}"
    client = factory()
    mcp_clients[name] = client
    tool_names = [t["name"] for t in client.tools]
    return f"Connected to '{name}'. Discovered tools: {', '.join(tool_names)}"
