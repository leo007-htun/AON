from aon.tools.base import Tool, ToolResult
from aon.geo import normalize_map, build_map


class ShowMapTool(Tool):
    name = "show_map"
    description = ("Show a map to the user and get real coordinates: mark one place as a target, several "
                   "places as an area, or a route from places[0] to places[1].")
    parameters = {"type": "object", "properties": {
        "places": {"type": "array", "items": {"type": "string"}, "description": "Place names, e.g. ['Albert Dock, Liverpool']"},
        "mode": {"type": "string", "enum": ["target", "area", "route"]},
        "travel": {"type": "string", "enum": ["drive", "walk", "cycle", "flight"]},
        "view": {"type": "string", "enum": ["auto", "2d", "3d", "globe"]}},
        "required": ["places"]}
    mutating = False

    def __init__(self, geo):
        self.geo = geo

    async def run(self, args: dict) -> ToolResult:
        places = args.get("places") or []
        mode = args.get("mode") or ("target" if len(places) == 1 else "area")
        spec = normalize_map({**args, "mode": mode, "places": places})
        if spec is None:
            return ToolResult(ok=False, output="", error="give at least one place name")
        mr = await build_map(spec, self.geo)
        text = " ".join([mr.context] + mr.notices + ([mr.ask] if mr.ask else [])).strip()
        if mr.event is None:
            return ToolResult(ok=False, output="", error=text or "no places found")
        return ToolResult(ok=True, output=text, event=mr.event)
