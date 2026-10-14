from aon.persistence import Database

def make_audit_hook(db: Database, session_id: int, decisions: dict):
    async def hook(event: dict):
        if event.get("type") != "step":
            return
        db.add_action(session_id, event.get("tool", ""), "",
                      "approved" if event.get("approved") else "rejected",
                      "", event.get("output", ""), "", 0)
    return hook
