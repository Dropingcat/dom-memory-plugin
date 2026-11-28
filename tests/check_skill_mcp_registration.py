"""Check that ``dom_skill_select`` is registered in the MCP server on every layer.

The real ``mcp`` package is an optional runtime dependency (it is absent on a bare
interpreter), so this test installs a minimal typed stand-in before importing
``mockup.server``.  That is enough to exercise the registration itself: tool table,
signature binding, routing table, visibility for the lite profile, and the closed
public projection of a result.

Run: python -B tests/check_skill_mcp_registration.py
"""
from __future__ import annotations

import json
import sys
import types as pytypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opencode"))

TOOL = "dom_skill_select"


def install_mcp_stub() -> None:
    """Minimal ``mcp`` surface used at import time by mockup.server."""
    mcp = pytypes.ModuleType("mcp")
    types_mod = pytypes.ModuleType("mcp.types")

    class Tool:
        def __init__(self, name=None, description=None, inputSchema=None):
            self.name = name
            self.description = description
            self.inputSchema = inputSchema

    class TextContent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class CallToolResult:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    types_mod.Tool = Tool
    types_mod.TextContent = TextContent
    types_mod.CallToolResult = CallToolResult
    mcp.types = types_mod

    server_pkg = pytypes.ModuleType("mcp.server")
    server_pkg.__path__ = []
    lowlevel = pytypes.ModuleType("mcp.server.lowlevel")

    class Server:
        def __init__(self, *args, **kwargs):
            pass

    class NotificationOptions:
        pass

    lowlevel.Server = Server
    lowlevel.NotificationOptions = NotificationOptions

    models = pytypes.ModuleType("mcp.server.models")

    class InitializationOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    models.InitializationOptions = InitializationOptions

    stdio = pytypes.ModuleType("mcp.server.stdio")

    async def stdio_server():
        return None

    stdio.stdio_server = stdio_server

    for name, module in {
        "mcp": mcp,
        "mcp.types": types_mod,
        "mcp.server": server_pkg,
        "mcp.server.lowlevel": lowlevel,
        "mcp.server.models": models,
        "mcp.server.stdio": stdio,
    }.items():
        sys.modules[name] = module


install_mcp_stub()

from mockup import claim_entry as claim_entry_mod  # noqa: E402
from mockup import public_dto  # noqa: E402
from mockup import server as server_mod  # noqa: E402
from mockup import skill_select  # noqa: E402
from mockup.skill_index import SkillRecord  # noqa: E402


class ReadOnlyService:
    """The handler must not touch the ledger; any attribute access is a failure."""

    profile = "lite"

    def __getattr__(self, name):
        raise AssertionError("dom_skill_select touched service.%s" % name)


def fixtures():
    skills = [
        SkillRecord(
            skill_id="sqlite-migration",
            name="sqlite-migration",
            description="Write schema migrations for the sqlite ledger.",
            categories=("databases",),
            mentions=("sqlite", "migration"),
            verified_provides=("sqlite", "migration"),
            source_path="skills/sqlite-migration/SKILL.md",
        ),
        SkillRecord(
            skill_id="pde-solver",
            name="pde-solver",
            description="Solve partial differential equations.",
            categories=("physics",),
            mentions=("pde",),
            verified_provides=("physics",),
            source_path="skills/pde-solver/SKILL.md",
        ),
    ]
    cards = [{"id": "sqlite-migration", "state": "verified"}]
    return skills, cards


def main() -> int:
    # 1. Tool documentation: the TOOL_DOCS entry is what builds LITE_TOOLS/description.
    assert TOOL in claim_entry_mod.TOOL_DOCS, sorted(claim_entry_mod.TOOL_DOCS)
    assert TOOL in claim_entry_mod.LITE_TOOL_NAMES, claim_entry_mod.LITE_TOOL_NAMES

    # 2. The six registration points (TDOM-35): doc table -> visible tool set,
    #    exact signature, routing handler, closed response record, response statuses.
    assert TOOL in server_mod.LITE_TOOL_NAMES, sorted(server_mod.LITE_TOOL_NAMES)
    assert TOOL in server_mod.SIGNATURE, sorted(server_mod.SIGNATURE)
    assert TOOL in server_mod.HANDLERS, sorted(server_mod.HANDLERS)
    assert TOOL in public_dto.LITE_RESPONSES, sorted(public_dto.LITE_RESPONSES)
    assert "SkillSelection" in public_dto.RECORDS, sorted(public_dto.RECORDS)

    tool = next(item for item in server_mod.tools_for("lite") if item.name == TOOL)
    assert tool.inputSchema["type"] == "object", tool.inputSchema
    assert tool.inputSchema["additionalProperties"] is False, tool.inputSchema
    assert tool.inputSchema["required"] == [], tool.inputSchema
    assert tool.inputSchema["properties"]["step"]["type"] == "object", tool.inputSchema
    assert tool.inputSchema["properties"]["queue"]["type"] == "array", tool.inputSchema
    assert tool.description and "skill" in tool.description.lower(), tool.description

    required, optional = server_mod.SIGNATURE[TOOL]
    assert required == {}, required
    assert optional == {"step": dict, "queue": list, "plan_version": int, "index_digest": str}, optional

    # 3. Lite-only: the strict tool table is untouched.
    assert TOOL not in server_mod.STRICT_TOOL_NAMES, "dom_skill_select is a lite tool"
    assert TOOL not in [item.name for item in server_mod.tools_for("strict")]

    # 4. Existing tools are still routable (no registration was displaced).
    lite_names = set(claim_entry_mod.LITE_TOOL_NAMES)
    assert lite_names <= set(server_mod.SIGNATURE), lite_names - set(server_mod.SIGNATURE)
    assert lite_names <= set(server_mod.HANDLERS), lite_names - set(server_mod.HANDLERS)
    assert lite_names <= {item.name for item in server_mod.tools_for("lite")}
    assert server_mod.STRICT_TOOL_NAMES <= set(server_mod.SIGNATURE)
    assert server_mod.STRICT_TOOL_NAMES <= set(server_mod.HANDLERS)

    # 5. Argument binding stays exact: an unknown key is a transport refusal.
    step = {"title": "Add the sqlite migration", "cap": ["sqlite", "migration"],
            "category_hint": "databases"}
    try:
        server_mod.dispatch(ReadOnlyService(), TOOL, {"step": step, "principal": "me"})
    except server_mod.ToolInput as error:
        assert str(error) == "INVALID_ARGUMENTS", error
    else:
        raise AssertionError("an unknown argument must be refused")

    # 5b. BUG-1: step is optional at the transport, so a missing step reaches the
    #     handler, which refuses with a typed status and Russian help instead of
    #     rejecting the call in the schema.
    refusal = server_mod.dispatch(ReadOnlyService(), TOOL, {})
    assert refusal["status"] == "INVALID_ARGUMENTS", refusal
    assert refusal["field"] == "step", refusal
    assert "Тебе НУЖНО" in refusal["help"], refusal
    assert server_mod.handle_dom_skill_select(None)["status"] == "INVALID_ARGUMENTS"
    assert server_mod.handle_dom_skill_select("step")["status"] == "INVALID_ARGUMENTS"

    # 6. Hermetic dispatch: inject the fixture index instead of walking the user's home.
    skills, cards = fixtures()
    original_skills, original_cards = skill_select.collect_skills, skill_select.load_cards
    skill_select.collect_skills = lambda roots=None: list(skills)
    skill_select.load_cards = lambda path=None: list(cards)
    try:
        result = server_mod.dispatch(ReadOnlyService(), TOOL, {"step": step})
        public = public_dto.project_tool(TOOL, result, scope={"main"}, profile="lite")
    finally:
        skill_select.collect_skills, skill_select.load_cards = original_skills, original_cards

    assert result["status"] == "SKILLS_SELECTED", result.get("status")
    assert result["queue"] == ["sqlite-migration"], result["queue"]
    assert len(result["block"]) <= 800, len(result["block"])
    assert public == result, (public, result)
    assert type(public["confidence_bp"]) is int, public["confidence_bp"]
    assert isinstance(public["signals"], dict) and public["signals"], public["signals"]

    # 7. The response is JSON-serializable exactly as the MCP layer sends it.
    encoded = json.dumps(public, ensure_ascii=True, sort_keys=True)
    assert "SKILLS_SELECTED" in encoded and "sqlite-migration" in encoded, encoded

    # 6b. BUG-4 STEP-BOUNDS: an over-long step is refused by the handler before the
    #     pipeline runs, with Russian help; the refusal projects to the DomainError shape.
    original_collect = skill_select.collect_skills

    def pipeline_must_not_run(roots=None):
        raise AssertionError("the pipeline must not run for a refused step")

    for field, payload in (("step.title", {"title": "x" * 501}),
                           ("step.context", {"title": "ok", "context": "x" * 2001})):
        skill_select.collect_skills = pipeline_must_not_run
        try:
            refusal = server_mod.dispatch(ReadOnlyService(), TOOL, {"step": payload})
        finally:
            skill_select.collect_skills = original_collect
        assert refusal["status"] == "INVALID_FIELD", refusal
        assert refusal["field"] == field, refusal
        assert "Тебе НУЖНО" in refusal["help"], refusal
        projected = public_dto.project_tool(TOOL, refusal, scope={"main"}, profile="lite")
        assert projected["status"] == "INVALID_FIELD", projected
        assert projected["field"] == field, projected
        assert projected["help"] == refusal["help"], projected

    # The exact boundary is allowed: 500 title / 2000 context chars reach the pipeline.
    skill_select.collect_skills = lambda roots=None: list(skills)
    skill_select.load_cards = lambda path=None: list(cards)
    try:
        boundary = server_mod.dispatch(ReadOnlyService(), TOOL,
                                       {"step": {"title": "x" * 500, "context": "y" * 2000}})
    finally:
        skill_select.collect_skills, skill_select.load_cards = original_skills, original_cards
    assert boundary["status"] == "SKILLS_SELECTED", boundary.get("status")

    print("PASS skill_mcp_registration: dom_skill_select in TOOL_DOCS/LITE_TOOL_NAMES/"
          "SIGNATURE/HANDLERS/LITE_RESPONSES/RECORDS; lite-only, read-only dispatch, "
          "queue=%s, block=%d chars, step bounds=INVALID_FIELD at 501/2001"
          % (public["queue"], len(public["block"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
