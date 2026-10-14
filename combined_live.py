import subprocess, sys, time, asyncio, os
os.environ["PYTHONPATH"] = "src"
sys.path.insert(0, "src")
print("spawning server...", flush=True)
srv = subprocess.Popen(["/home/ubuntu/app/venv/bin/python", "tiny_http_server.py"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       env=dict(os.environ))
time.sleep(6)
print("server pid", srv.pid, "poll", srv.poll(), flush=True)
from agent.mcp_client import McpManager, McpServerCfg
async def main():
    m = McpManager("/tmp/mcp_http_live.json")
    m.add_server(McpServerCfg("tinyhttp", transport="http", url="http://127.0.0.1:9123/mcp"))
    print("connecting...", flush=True)
    await asyncio.wait_for(m.connect_all(), timeout=25)
    print("status:", [(s["name"], s["transport"], s["status"], s["tools"]) for s in m.status()], flush=True)
    if m.tools():
        print("call greet(AON):", await asyncio.wait_for(m.call_tool("tinyhttp", "greet", {"name": "AON"}), timeout=15), flush=True)
    await m.disconnect("tinyhttp")
try:
    asyncio.run(main())
except Exception as e:
    print("ERROR:", type(e).__name__, e, flush=True)
finally:
    srv.terminate()
print("done", flush=True)
