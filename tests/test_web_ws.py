import json, pytest
from pathlib import Path
from fastapi.testclient import TestClient
from agent.config import Config
from agent.persistence import Database
from agent.web import create_app
from agent.engine import Agent
from agent.llm_client import ChatResult, ToolCall
from agent.tools.base import Tool, ToolResult, ToolRegistry

class Mut(Tool):
    name="run_command"; description=""; parameters={}; mutating=True
    async def run(self, args): return ToolResult(ok=True, output=f"ran {args['command']}")
class FakeLLM:
    def __init__(self, s): self.s=list(s); self.i=0
    async def chat(self, m, tools=None):
        r=self.s[min(self.i,len(self.s)-1)]; self.i+=1; return r

def _cfg(tmp): return Config("http://x/v1","gpt-oss",tmp,[],5,10,"127.0.0.1:8700")

def _app(tmp):
    db=Database(str(tmp/"d.db"))
    def make_agent(gate, on_event, model, convo="default"):
        reg=ToolRegistry(); reg.register(Mut())
        llm=FakeLLM([ChatResult("",[ToolCall("1","run_command",{"command":"echo hi"})]),
                     ChatResult("done: ok",[])])
        return Agent(llm, reg, gate, max_steps=5, on_event=on_event)
    return create_app(_cfg(tmp), db, make_agent)

def test_ws_approve_flow(tmp_path):
    c = TestClient(_app(tmp_path))
    with c.websocket_connect("/ws") as ws:
        ws.send_json({"type":"goal","goal":"do it","model":"gpt-oss"})
        pend = ws.receive_json()
        assert pend["type"]=="pending" and pend["tool"]=="run_command"
        ws.send_json({"type":"decision","req_id":pend["req_id"],"approved":True,"edited_args":None})
        seen=[]
        while True:
            m=ws.receive_json(); seen.append(m)
            if m["type"]=="final": break
        kinds=[m["type"] for m in seen]
        assert "step" in kinds and seen[-1]["status"]=="done"
        step=[m for m in seen if m["type"]=="step"][0]
        assert "ran echo hi" in step["output"]

def test_ws_reject_flow(tmp_path):
    c = TestClient(_app(tmp_path))
    with c.websocket_connect("/ws") as ws:
        ws.send_json({"type":"goal","goal":"do it","model":"gpt-oss"})
        pend = ws.receive_json()
        ws.send_json({"type":"decision","req_id":pend["req_id"],"approved":False,"edited_args":None})
        seen=[]
        while True:
            m=ws.receive_json(); seen.append(m)
            if m["type"]=="final": break
        step=[m for m in seen if m["type"]=="step"][0]
        assert "rejected" in step["output"].lower()
