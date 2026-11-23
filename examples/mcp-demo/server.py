"""A small MCP server with Ritvo added. Fictional tools; nothing here touches a real system.

Adopting Ritvo is the one `protect` line, and `protection.remove()` undoes it.
"""

import os

from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from pydantic import AnyHttpUrl

from ritvo.enforcement.mcp_middleware import protect, token_verifier

CONFIG = os.environ.get("RITVO_CONFIG", "ritvo.yaml")

server = MCPServer(
    "demo-trading",
    # Your server has its own token verifier already; this one is built from the same ritvo.yaml.
    token_verifier=token_verifier(CONFIG),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl("https://idp.example.com"),
        resource_server_url=AnyHttpUrl("http://localhost:8000/mcp"),
        validate_token_resource=False,
    ),
)


@server.tool(name="broker.quote")
def quote(symbol: str) -> dict[str, object]:
    """A price quote. A read, low risk."""
    return {"symbol": symbol, "price": 101.5}


@server.tool(name="broker.order")
def place_order(symbol: str, order_value: int) -> dict[str, object]:
    """Place an order. Moves money, so high risk."""
    return {"symbol": symbol, "order_value": order_value, "status": "filled"}


@server.tool(name="ledger.adjust")
def adjust(account: str, amount: int) -> dict[str, object]:
    """Adjust a ledger balance. Medium risk."""
    return {"account": account, "amount": amount, "status": "adjusted"}


protection = protect(server, CONFIG)  # <- the one line
