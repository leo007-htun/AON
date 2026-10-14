import asyncio, sys
sys.path.insert(0, "src")
from agent.mcp_client import McpManager, McpServerCfg

async def main():
    m = McpManager("/tmp/mcp_http_live.json")  # real default connector -> _http_connector for transport=http
    m.add_server(McpServerCfg("tinyhttp", transport="http", url="http://127.0.0.1:9123/mcp"))
    await m.connect_all()
    print("status:", [(s["name"], s["transport"], s["status"], s["tools"]) for s in m.status()])
    if m.tools():
        print("call greet(AON):", await m.call_tool("tinyhttp", "greet", {"name": "AON"}))
    await m.disconnect("tinyhttp")

asyncio.run(main())
