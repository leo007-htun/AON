import httpx, pytest
from agent.telegram import Telegram, parse_decision

def test_parse_decision():
    assert parse_decision("ok 5") == (5, True)
    assert parse_decision("no 7") == (7, False)
    assert parse_decision("hello") is None

@pytest.mark.asyncio
async def test_notify_sends_to_allowed():
    sent=[]
    def h(req):
        sent.append(dict(httpx.QueryParams(req.url.query)))
        return httpx.Response(200, json={"ok":True})
    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as http:
        await Telegram("tok",[111,222],http=http).notify("hi")
    assert {s["chat_id"] for s in sent} == {"111","222"}

@pytest.mark.asyncio
async def test_poll_ignores_other_chats():
    updates={"result":[
        {"update_id":10,"message":{"from":{"id":111},"text":"ok 1"}},
        {"update_id":11,"message":{"from":{"id":999},"text":"ok 2"}},
    ]}
    def h(req): return httpx.Response(200, json=updates)
    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as http:
        off, texts = await Telegram("tok",[111],http=http).poll_decisions(0)
    assert texts == ["ok 1"] and off == 12
