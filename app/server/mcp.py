"""MCP server — the IFRS 17 workbench exposed as callable tools.

MCP-first: the app's own endpoint functions are the single implementation; this
exposes them as an MCP tool surface so the app UI, notebooks and external agents
— the Bricksurance control tower included — are all clients of one surface.

Every tool DELEGATES to the app endpoint function (passed in via register), so it
reuses the exact logic AND any server-side gate the function enforces — it cannot
be bypassed here. Reads are idempotent; [action] tools write through the governed
handler (journal posting, sign-off approval, certificate, as-at reproduction).

Transport: JSON-RPC 2.0 over one POST + a GET manifest, mirroring
pricing-workbench-gen2 / reserving-workbench. Auth = whatever the Databricks App
enforces in front of the container.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mcp", tags=["mcp"])

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "bricksurance-ifrs17-workbench", "version": "1.0.0"}


def _mk(name, desc, props=None, required=None):
    return {"name": name, "description": desc,
            "inputSchema": {"type": "object", "properties": props or {}, "required": required or []}}


def _wrap(fn, *args, **kwargs) -> dict:
    """Call an app endpoint function (sync) and normalise to a dict. An
    HTTPException (incl. a 401/403 gate) becomes {"ok": False, "gated": ...}."""
    try:
        r = fn(*args, **kwargs)
    except HTTPException as e:
        gated = e.status_code in (401, 403)
        return {"ok": False, **({"gated": True} if gated else {}), "error": f"{e.status_code}: {e.detail}"}
    except Exception as e:
        logger.warning("mcp ifrs17 delegate %s failed: %s", getattr(fn, "__name__", "?"), str(e)[:200])
        return {"ok": False, "error": str(e)[:200]}
    return r if isinstance(r, dict) else {"ok": True, "data": r}


def _body(a: dict) -> dict:
    """Pass agent args through as the handler's body, dropping transport-control keys."""
    return {k: v for k, v in (a or {}).items() if k not in ("session_id",)}


# --- reads ---
def _t_cockpit(a, app):         return _wrap(app.cockpit)
def _t_ingestion(a, app):       return _wrap(app.ingestion)
def _t_groups(a, app):          return _wrap(app.groups)
def _t_cohort(a, app):          return _wrap(app.cohort, str(a.get("gid") or ""))
def _t_discount(a, app):        return _wrap(app.discount)
def _t_results(a, app):         return _wrap(app.results)
def _t_board_pack(a, app):      return _wrap(app.board_pack)
def _t_recon(a, app):           return _wrap(app.recon)
def _t_signoff(a, app):         return _wrap(app.signoff)
def _t_certificate_get(a, app): return _wrap(app.get_certificate, str(a.get("cert_id") or ""))
def _t_governance(a, app):      return _wrap(app.governance, str(a.get("tab") or ""))
def _t_ai_bench(a, app):        return _wrap(app.ai_bench)
def _t_lever_status(a, app):    return _wrap(app.lever_status, int(a.get("run_id") or 0))

# --- narration / AI ---
def _t_cfo_brief(a, app):       return _wrap(app.cfo_brief, _body(a))
def _t_cohort_narrate(a, app):  return _wrap(app.cohort_narrate, str(a.get("gid") or ""), _body({k: v for k, v in a.items() if k != "gid"}))
def _t_audit_narrate(a, app):   return _wrap(app.audit_narrate, _body(a))
def _t_ai_ask(a, app):          return _wrap(app.ai_ask, _body(a))

# --- stages / governed actions ---
def _t_whatif_rates(a, app):    return _wrap(app.whatif_rates, _body(a))
def _t_disclosure_note(a, app): return _wrap(app.disclosure_note, _body(a))
def _t_post_journal(a, app):    return _wrap(app.post_journal, _body(a))
def _t_approve_signoff(a, app): return _wrap(app.approve, _body(a))
def _t_certificate(a, app):     return _wrap(app.certificate, _body(a))
def _t_reproduce(a, app):       return _wrap(app.reproduce, _body(a))
def _t_lever(a, app):           return _wrap(app.lever, str(a.get("name") or ""))


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _mk("cockpit", "The IFRS 17 close cockpit — the control-tower view (KPIs, close stage, quality gate, CSM/onerous headlines)."),
    _mk("ingestion", "The nine feeds into the close — freshness, row counts, and the quality gate that blocks a red control."),
    _mk("groups", "The IFRS 17 groups / portfolios of contracts (GMM & PAA), with measurement model and status."),
    _mk("cohort", "One cohort/group in detail — measurement, CSM roll-forward, onerous test.", {"gid": {"type": "string"}}, ["gid"]),
    _mk("discount", "The discount-curve view (EIOPA curves) feeding measurement."),
    _mk("results", "The consolidated IFRS 17 results — subledger totals, CSM, onerous, reconciled to GL."),
    _mk("board_pack", "The results/board pack (§80/§101/§104 disclosures that foot by construction)."),
    _mk("recon", "The subledger↔GL reconciliation."),
    _mk("signoff", "The sign-off state of the close (who has approved what)."),
    _mk("certificate_get", "Fetch a generated sign-off certificate by id.", {"cert_id": {"type": "string"}}, ["cert_id"]),
    _mk("governance", "A governance tab (e.g. audit / models / controls / lineage).", {"tab": {"type": "string"}}, ["tab"]),
    _mk("ai_bench", "The AI benchmark/agent-governance view for the close."),
    _mk("lever_status", "Status of a running what-if lever job.", {"run_id": {"type": "integer"}}, ["run_id"]),
    _mk("cfo_brief", "Generate the CFO brief narrative for the close (grounded in the results)."),
    _mk("cohort_narrate", "Narrate the movements for one cohort in plain language.", {"gid": {"type": "string"}}, ["gid"]),
    _mk("audit_narrate", "Narrate an audit finding / reproduction in plain language."),
    _mk("ai_ask", "Ask the grounded IFRS 17 assistant a question (answers only from the close data).", {"question": {"type": "string"}}, ["question"]),
    _mk("whatif_rates", "Run a discount-rate what-if and see the impact on measurement (does not post)."),
    _mk("disclosure_note", "[action] Draft/attach a disclosure note to the results."),
    _mk("post_journal", "[action] Post the IFRS 17 journal to the subledger (audited)."),
    _mk("approve_signoff", "[gated] Approve a sign-off step for the close (maker/checker)."),
    _mk("certificate", "[action] Generate the sign-off certificate for the approved close."),
    _mk("reproduce", "[action] Reproduce the close as-at a prior point via Delta time travel (auditor replay)."),
    _mk("lever", "[action] Trigger a what-if lever job by name (returns a run_id to poll with lever_status).", {"name": {"type": "string"}}, ["name"]),
]

TOOL_IMPLS = {
    "cockpit": _t_cockpit, "ingestion": _t_ingestion, "groups": _t_groups, "cohort": _t_cohort,
    "discount": _t_discount, "results": _t_results, "board_pack": _t_board_pack, "recon": _t_recon,
    "signoff": _t_signoff, "certificate_get": _t_certificate_get, "governance": _t_governance,
    "ai_bench": _t_ai_bench, "lever_status": _t_lever_status,
    "cfo_brief": _t_cfo_brief, "cohort_narrate": _t_cohort_narrate, "audit_narrate": _t_audit_narrate,
    "ai_ask": _t_ai_ask, "whatif_rates": _t_whatif_rates, "disclosure_note": _t_disclosure_note,
    "post_journal": _t_post_journal, "approve_signoff": _t_approve_signoff, "certificate": _t_certificate,
    "reproduce": _t_reproduce, "lever": _t_lever,
}


def _ok(rpc_id, result):  return {"jsonrpc": "2.0", "id": rpc_id, "result": result}
def _err(rpc_id, code, m): return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": m}}


def register(app_module):
    """Wire the router to the app module so tools can call its endpoint functions."""

    @router.post("")
    async def jsonrpc(request: Request):
        try:
            body = await request.json()
        except Exception:
            return _err(None, -32700, "Parse error: body is not valid JSON")
        rpc_id = body.get("id"); method = body.get("method"); params = body.get("params") or {}

        if method == "initialize":
            return _ok(rpc_id, {
                "protocolVersion": PROTOCOL_VERSION, "serverInfo": SERVER_INFO,
                "capabilities": {"tools": {}},
                "instructions": (
                    "IFRS 17 close workbench for Bricksurance SE. Reads cover the close cockpit, "
                    "feeds, groups/cohorts, discounting, results, reconciliation, sign-off and "
                    "governance. Actions write through the same governed handlers the UI uses — "
                    "posting a journal is audited, sign-off approval is maker/checker, and as-at "
                    "reproduction uses Delta time travel. Never invent a figure.")})
        if method in ("notifications/initialized", "notifications/cancelled"):
            return _ok(rpc_id, {})
        if method == "tools/list":
            return _ok(rpc_id, {"tools": TOOL_SCHEMAS})
        if method == "tools/call":
            name = params.get("name"); args = params.get("arguments") or {}
            impl = TOOL_IMPLS.get(name)
            if impl is None:
                return _err(rpc_id, -32601, f"Unknown tool: {name}")
            try:
                payload = impl(args, app_module)
            except Exception as e:
                logger.exception("mcp tool %s failed", name)
                return _err(rpc_id, -32603, f"Tool execution failed: {str(e)[:200]}")
            return _ok(rpc_id, {
                "content": [{"type": "text", "text": json.dumps(payload, default=str)}],
                "structuredContent": payload,
                "isError": isinstance(payload, dict) and payload.get("ok") is False})
        return _err(rpc_id, -32601, f"Method not found: {method}")

    @router.get("/manifest")
    async def manifest():
        return {"server": SERVER_INFO, "protocol_version": PROTOCOL_VERSION,
                "tools": [{"name": t["name"], "description": t["description"]} for t in TOOL_SCHEMAS]}

    return router
