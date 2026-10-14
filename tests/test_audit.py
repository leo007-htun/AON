import pytest
from aon.persistence import Database
from aon.audit import make_audit_hook

@pytest.mark.asyncio
async def test_audit_records_steps(tmp_path):
    db = Database(str(tmp_path/"d.db"))
    sid = db.create_session("t","/ws","gpt-oss")
    hook = make_audit_hook(db, sid, {})
    await hook({"type":"step","tool":"run_command","output":"ran echo","approved":True})
    await hook({"type":"final","status":"done","final":"ok"})
    acts = db.get_actions(sid)
    assert len(acts)==1 and acts[0]["tool"]=="run_command" and acts[0]["decision"]=="approved"
