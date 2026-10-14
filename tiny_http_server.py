from mcp.server.fastmcp import FastMCP
mcp = FastMCP("tinyhttp", port=9123)

@mcp.tool()
def greet(name: str) -> str:
    """Greet someone."""
    return f"hello {name}"

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
