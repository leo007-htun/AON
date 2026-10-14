import pytest
from agent.tools.base import Tool, ToolResult, ToolRegistry

class Dummy(Tool):
    name = "dummy"; description = "d"; parameters = {"type": "object", "properties": {}}; mutating = False
    async def run(self, args): return ToolResult(ok=True, output="ok")

def test_register_and_get():
    r = ToolRegistry(); t = Dummy(); r.register(t)
    assert r.get("dummy") is t
    assert [x.name for x in r.list()] == ["dummy"]

def test_schemas_openai_shape():
    r = ToolRegistry(); r.register(Dummy())
    s = r.schemas()[0]
    assert s["type"] == "function"
    assert s["function"]["name"] == "dummy"
    assert s["function"]["parameters"]["type"] == "object"

def test_unknown_tool_raises():
    with pytest.raises(KeyError):
        ToolRegistry().get("nope")
