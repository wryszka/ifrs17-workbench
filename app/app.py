"""IFRS 17 Workbench — thin FastAPI backend. Presentation only: every panel reads a real
engine table / UC function / serving endpoint / Genie. No measurement logic lives here."""
import datetime
import io
import json
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response

from server import agents, config, sql
from server.packs import build_certificate

app = FastAPI(title="IFRS 17 Workbench — Bricksurance SE")

F = config.fqn
PERIOD = "2026Q2"


def _struct(fn, *args):
    arglist = ", ".join(f"'{sql.esc(str(a))}'" for a in args)
    row = sql.query_one(f"SELECT to_json({F(fn)}({arglist})) AS r")
    return json.loads(row["r"]) if row and row.get("r") else {}


def _actor(request: Request) -> str:
    """The identity that actually signs/approves — derived from the Databricks Apps auth headers,
    NEVER from the request body (which is spoofable). Databricks Apps forwards the end user as
    X-Forwarded-Email / X-Forwarded-Preferred-Username / X-Forwarded-User."""
    h = request.headers
    for k in ("x-forwarded-email", "x-forwarded-preferred-username", "x-forwarded-user"):
        v = h.get(k)
        if v:
            return v
    # Fallback: the app service principal (still a real, non-spoofable identity, not a body value).
    try:
        return config.get_workspace_client().current_user.me().user_name or "unknown"
    except Exception:
        return "unknown"


def _close_ready() -> dict:
    """Server-side close-readiness gate. Sign-off and certificates are refused unless the close is
    actually ready: DQ/close gate not blocked, trial-balance recon fully tied, no failed engine runs.
    This is enforced here (not in the UI) so no client can bypass it."""
    q = sql.query_many({
        "blocked": f"SELECT count(*) n FROM {F('gld_close_status')} WHERE close_period='{PERIOD}' AND status IN ('blocked','red')",
        "recon": f"SELECT count(*) items, sum(CASE WHEN status='tied' THEN 1 ELSE 0 END) tied FROM {F('gld_trial_balance_recon')} WHERE close_period='{PERIOD}'",
        "failed_runs": f"""SELECT count(*) n FROM (
            SELECT status, ROW_NUMBER() OVER (PARTITION BY engine ORDER BY finished_at DESC) rn
            FROM {F('gov_run_audit')} WHERE close_period='{PERIOD}'
        ) WHERE rn=1 AND lower(status) NOT IN ('success','succeeded','ok','done')""",
    })
    blocked = int((q["blocked"][0] or {}).get("n", 0) or 0) if q.get("blocked") else 0
    r = q["recon"][0] if q.get("recon") else {}
    items = int(r.get("items", 0) or 0)
    tied = int(r.get("tied", 0) or 0)
    failed = int((q["failed_runs"][0] or {}).get("n", 0) or 0) if q.get("failed_runs") else 0
    reasons = []
    if blocked:
        reasons.append(f"{blocked} close-gate workstream(s) blocked")
    if items == 0 or tied < items:
        reasons.append(f"trial-balance recon not fully tied ({tied}/{items})")
    if failed:
        reasons.append(f"{failed} engine run(s) not successful")
    return {"ready": not reasons, "reasons": reasons}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/preflight")
def preflight():
    """Pre-demo health check — warms the warehouse and reports readiness so a presenter never
    discovers a cold warehouse or a missing asset live. Safe to poll before a session."""
    import time as _t
    out = {}
    t0 = _t.time()
    try:
        sql.query_one("SELECT 1 v")  # forces the warehouse to start via the polling helper
        out["warehouse_ready"] = True
    except Exception as e:
        out["warehouse_ready"] = False
        out["warehouse_error"] = str(e)[:200]
    out["warehouse_ms"] = int((_t.time() - t0) * 1000)
    try:
        out["cache_table"] = bool(sql.query_one(f"SELECT count(*) n FROM {config.CACHE_TABLE}") is not None)
    except Exception:
        out["cache_table"] = False
    try:
        out["supervisor_endpoint"] = config.resolve_endpoint(config.EP_AGENT_SUBSTR)
    except Exception:
        out["supervisor_endpoint"] = None
    try:
        out["close_ready"] = _close_ready()
    except Exception as e:
        out["close_ready"] = {"ready": False, "reasons": [str(e)[:160]]}
    out["ok"] = bool(out.get("warehouse_ready"))
    return out


@app.get("/api/config")
def get_config():
    host = config.workspace_host()
    return {
        "catalog": config.CATALOG, "schema": config.SCHEMA, "entity": "Bricksurance SE",
        "period": PERIOD, "use_cache": config.USE_CACHE, "workspace_host": host,
        "genie_space_id": config.GENIE_SPACE_ID,
        "genie_embed_url": f"{host}/embed/genie/rooms/{config.GENIE_SPACE_ID}" if config.GENIE_SPACE_ID else "",
        "dashboard_embed_url": f"{host}/embed/dashboardsv3/{config.DASHBOARD_ID}" if config.DASHBOARD_ID else "",
        "hub_app_url": config.HUB_APP_URL,
        "supervisor_endpoint": config.resolve_endpoint(config.EP_AGENT_SUBSTR),
    }


# ---------------------------------------------------------------- close cockpit
@app.get("/api/cockpit")
def cockpit():
    q = sql.query_many({
        "status": f"SELECT * FROM {F('gld_close_status')} WHERE close_period = '{PERIOD}' ORDER BY working_day, workstream",
        "calendar": f"SELECT * FROM {F('ref_close_calendar')} WHERE close_period = '{PERIOD}' ORDER BY working_day",
        "feeds": f"SELECT feed, source_system, files, rows, last_arrival, status FROM {F('gld_feed_sla')} ORDER BY feed",
        "quarantine": f"SELECT _source_file, quarantine_reason, rows, quarantined_at FROM {F('gld_quarantine_summary')} ORDER BY quarantined_at DESC",
        "dq": f"SELECT round(sum(passed)/greatest(sum(passed)+sum(failed),1)*100, 2) pass_pct, count(*) expectations, sum(failed) failed_rows FROM {F('gld_dq_scorecard')}",
        "pnl": f"SELECT line_item, amount FROM {F('gld_pnl_statement')} WHERE close_period = '{PERIOD}' ORDER BY line_no",
        "onerous": f"SELECT group_id, headroom, loss_component FROM (SELECT o.group_id, o.headroom, lc.amount loss_component FROM {F('gld_onerous_test')} o LEFT JOIN {F('gld_loss_component')} lc ON lc.group_id=o.group_id AND lc.close_period=o.close_period AND lc.step='closing' WHERE o.close_period='{PERIOD}' AND o.onerous)",
        "recon": f"SELECT count(*) items, sum(CASE WHEN status='tied' THEN 1 ELSE 0 END) tied FROM {F('gld_trial_balance_recon')} WHERE close_period = '{PERIOD}'",
        "runs": f"SELECT engine, run_id, status, finished_at FROM {F('gov_run_audit')} WHERE close_period='{PERIOD}' ORDER BY finished_at DESC LIMIT 12",
    })
    return {k: (v if k in ("status", "calendar", "feeds", "quarantine", "pnl", "onerous", "runs") else (v[0] if v else {}))
            for k, v in q.items()}


@app.post("/api/ai/brief")
def cfo_brief(body: dict = None):
    body = body or {}
    data = cockpit()
    findings = {"close_status": data["status"], "pnl": data["pnl"], "onerous": data["onerous"],
                "recon": data["recon"]}
    return agents.narrate("cfo_brief", "Write the close brief for the CFO.", findings,
                          use_cache=body.get("cache"))


# ---------------------------------------------------------------- ingestion
@app.get("/api/ingestion")
def ingestion():
    q = sql.query_many({
        "sources": f"SELECT * FROM {F('gld_ingestion_sources')} ORDER BY source_group, source",
        "feeds": f"SELECT * FROM {F('gld_feed_sla')} ORDER BY feed",
        "quarantine": f"SELECT _source_file, quarantine_reason, rows, quarantined_at FROM {F('gld_quarantine_summary')} ORDER BY quarantined_at DESC",
        "quarantine_sample": f"SELECT run_id, scope, portfolio_id, _rescued_data FROM {F('brz_quarantine_cashflows')} LIMIT 5",
        "dq": f"SELECT dataset, expectation, action, passed, failed, pass_pct FROM {F('gld_dq_scorecard')} ORDER BY dataset, expectation",
    })
    return q


# ---------------------------------------------------------------- contract groups
@app.get("/api/groups")
def groups():
    q = sql.query_many({
        "groups": f"SELECT * FROM {F('gld_cohort_360')} ORDER BY onerous DESC, portfolio_id, cohort_year",
        "watch": f"SELECT * FROM {F('gld_onerous_watch')} WHERE portfolio_id IN ('MOT','PROP','LIAB') ORDER BY group_id, close_period",
    })
    return q


@app.get("/api/cohort/{gid}")
def cohort(gid: str):
    gid = sql.esc(gid)
    q = sql.query_many({
        "summary": f"SELECT * FROM {F('gld_cohort_360')} WHERE group_id = '{gid}'",
        "csm": f"SELECT close_period, step, amount, paragraph FROM {F('gld_csm_rollforward')} WHERE group_id = '{gid}' ORDER BY close_period, CASE step WHEN 'opening' THEN 0 WHEN 'new_business' THEN 1 WHEN 'interest_accretion' THEN 2 WHEN 'experience_adjustments' THEN 3 WHEN 'fcf_changes_future_service' THEN 4 WHEN 'fx' THEN 5 WHEN 'csm_release' THEN 6 ELSE 7 END",
        "lrc": f"SELECT close_period, step, amount FROM {F('gld_lrc_paa_rollforward')} WHERE group_id = '{gid}' ORDER BY close_period",
        "lc": f"SELECT close_period, step, amount FROM {F('gld_loss_component')} WHERE group_id = '{gid}' ORDER BY close_period",
        "onerous": f"SELECT * FROM {F('gld_onerous_test')} WHERE group_id = '{gid}' ORDER BY close_period",
        "units": f"SELECT * FROM {F('gld_coverage_units')} WHERE group_id = '{gid}' ORDER BY close_period",
        "discount": f"SELECT * FROM {F('gld_discount_impact')} WHERE group_id = '{gid}' ORDER BY close_period",
        "fcf": f"SELECT close_period, basis, pv_future_premiums, pv_future_claims, pv_future_expenses, risk_adjustment, fcf_remaining FROM {F('gld_fcf_summary')} WHERE group_id = '{gid}' ORDER BY close_period, basis",
        "ave": f"SELECT * FROM {F('gld_ave_analysis')} WHERE portfolio_id = split('{gid}', '-')[0] ORDER BY close_period",
        "assumptions": f"SELECT * FROM {F('gov_assumption_registry')} WHERE portfolio_id = split('{gid}', '-')[0] OR portfolio_id = 'ALL' ORDER BY assumption_id, version",
        "ri": f"SELECT * FROM {F('gld_ri_held')} WHERE close_period = '{PERIOD}' ORDER BY component",
    })
    port = gid.split("-")[0]
    if port in ("PROP",):
        q["source_claims"] = sql.query(
            f"SELECT claim_id, loss_date, peril, catastrophe_code, status, paid_to_date, case_reserve, region "
            f"FROM {F('slv_claim')} WHERE portfolio_id = '{port}' AND catastrophe_code IS NOT NULL "
            f"ORDER BY paid_to_date + case_reserve DESC LIMIT 12")
    q["lic"] = sql.query(f"SELECT * FROM {F('gld_lic_rollforward')} WHERE portfolio_id = '{sql.esc(port)}' "
                         f"AND close_period = '{PERIOD}' ORDER BY accident_year, step")
    return q


@app.post("/api/cohort/{gid}/narrate")
def cohort_narrate(gid: str, body: dict = None):
    body = body or {}
    findings = {
        "summary": _struct("fn_cohort_summary", gid),
        "onerous_test": _struct("fn_onerous_test", gid, PERIOD),
        "ave": _struct("fn_ave_analysis", gid.split("-")[0], PERIOD),
        "assumption": _struct("fn_assumption_history",
                              "flood_freq_property" if gid.startswith("PROP") else "casualty_inflation_clt"),
    }
    if gid.split("-")[0] in ("CLT", "DEC"):
        findings["csm_waterfall"] = _struct("fn_csm_rollforward", gid, PERIOD)
        findings["coverage_units"] = _struct("fn_coverage_units", gid, PERIOD)
    findings["group_id"] = gid
    return agents.narrate("movement_narrator",
                          body.get("question", f"Explain the {PERIOD} movement for {gid}."),
                          findings, use_cache=body.get("cache"))


# ---------------------------------------------------------------- discount & assumptions
@app.get("/api/discount")
def discount():
    q = sql.query_many({
        "meta": f"SELECT * FROM {F('ref_rfr_meta')} ORDER BY curve_date",
        "spots": f"SELECT curve_date, maturity_years, spot_rate FROM {F('ref_rfr_curve')} WHERE maturity_years <= 30 ORDER BY curve_date, maturity_years",
        "ilp": f"SELECT assumption_id, version, value_json, approved_by, approved_at FROM {F('gov_assumption_registry')} WHERE assumption_id = 'illiquidity_premium'",
        "impact": f"SELECT * FROM {F('gld_discount_impact')} WHERE close_period = '{PERIOD}' ORDER BY group_id",
        "mapping": f"SELECT group_id, locked_in_curve_date FROM {F('gld_contract_groups')} WHERE locked_in_curve_date IS NOT NULL ORDER BY group_id",
    })
    return q


@app.post("/api/whatif/rates")
def whatif_rates(body: dict = None):
    """LIVE what-if: shift the current curve by N bps and re-PV the remaining cash flows on the
    warehouse in one query. The CSM does not move — accretion is locked-in (the expert point)."""
    bps = int((body or {}).get("bps", 100))
    shift = bps / 10000.0
    cur = "2026-06-30"
    out = sql.query_one(f"""
        WITH flows AS (
          SELECT p.portfolio_id, p.scope,
                 -- sign by direction: premiums are inflows (reduce the liability), claims/expense are outflows.
                 CASE WHEN p.cf_type = 'premium' THEN -p.amount ELSE p.amount END signed_amount,
                 greatest(1, (year(p.projection_month) - 2026) * 12 + month(p.projection_month) - 6) m
          FROM {F('slv_cashflow_projection')} p
          WHERE p.run_id = 'RSV_{PERIOD}' AND p.projection_month > DATE'{cur}'
        ), c AS (
          SELECT portfolio_id, maturity_month, base_spot, ilp_bps, discount_factor
          FROM {F('ref_discount_curve')} WHERE curve_date = '{cur}' AND portfolio_id != '_BASE'
        )
        SELECT round(sum(CASE WHEN f.scope='LIC' THEN f.signed_amount * c.discount_factor END), 2) lic_pv_now,
               round(sum(CASE WHEN f.scope='LIC' THEN f.signed_amount * pow(1 + c.base_spot + c.ilp_bps/10000.0 + {shift}, -c.maturity_month/12.0) END), 2) lic_pv_shifted,
               round(sum(CASE WHEN f.scope='LRC' THEN f.signed_amount * c.discount_factor END), 2) lrc_pv_now,
               round(sum(CASE WHEN f.scope='LRC' THEN f.signed_amount * pow(1 + c.base_spot + c.ilp_bps/10000.0 + {shift}, -c.maturity_month/12.0) END), 2) lrc_pv_shifted
        FROM flows f JOIN c ON c.portfolio_id = f.portfolio_id AND c.maturity_month = f.m""")
    csm = sql.query_one(f"SELECT round(sum(amount),2) csm FROM {F('gld_csm_rollforward')} "
                        f"WHERE close_period = '{PERIOD}' AND step = 'closing'")
    return {"bps": bps, **(out or {}), "csm_closing_unchanged": (csm or {}).get("csm"),
            "note": "Aggregate net-cash-flow PV sensitivity (premiums signed as inflows). The point of "
                    "this what-if is that the CSM does NOT move — interest accretion is locked at the "
                    "cohort inception curve. Full RA re-measurement and the P&L/OCI disaggregation are "
                    "computed by the engine on a close run, not live here."}


# ---------------------------------------------------------------- results & disclosures
@app.get("/api/results")
def results():
    q = sql.query_many({
        "pnl": f"SELECT * FROM {F('gld_pnl_statement')} ORDER BY close_period, line_no",
        "bs": f"SELECT * FROM {F('gld_balance_sheet')} WHERE close_period = '{PERIOD}' ORDER BY line_no",
        "d101": f"SELECT * FROM {F('gld_disclosure_lrc_lic')} WHERE close_period = '{PERIOD}' ORDER BY component, ord",
        "d104": f"SELECT * FROM {F('gld_disclosure_by_component')} WHERE close_period = '{PERIOD}' ORDER BY component, ord",
        "ra": f"SELECT * FROM {F('gld_ra_rollforward')} ORDER BY close_period",
        "revenue": f"SELECT component, round(sum(amount),2) amount FROM {F('gld_insurance_revenue')} WHERE close_period = '{PERIOD}' GROUP BY component ORDER BY component",
        "ml": f"SELECT * FROM {F('gld_lic_ultimates')} WHERE accident_quarter >= '2025Q3' ORDER BY portfolio_id, accident_quarter",
    })
    return q


@app.post("/api/results/note")
def disclosure_note(body: dict = None):
    body = body or {}
    topic = body.get("topic", "loss_component")
    data = sql.query_many({
        "d101": f"SELECT component, line, amount FROM {F('gld_disclosure_lrc_lic')} WHERE close_period='{PERIOD}' ORDER BY component, ord",
        "onerous": f"SELECT * FROM {F('gld_onerous_test')} WHERE close_period='{PERIOD}' AND onerous",
        "ra": f"SELECT * FROM {F('gld_ra_rollforward')} WHERE close_period='{PERIOD}'",
        "ri": f"SELECT component, amount FROM {F('gld_ri_held')} WHERE close_period='{PERIOD}'",
    })
    return agents.narrate("disclosure_drafter",
                          f"Draft the {topic} note paragraph for the {PERIOD} reporting pack.",
                          data, use_cache=body.get("cache"))


@app.get("/api/results/pack")
def board_pack():
    w = config.get_workspace_client()
    path = f"/Volumes/{config.CATALOG}/{config.SCHEMA}/ifrs17_files/packs/ifrs17_board_pack_{PERIOD}.xlsx"
    resp = w.files.download(path)
    data = resp.contents.read()
    return Response(content=data,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=ifrs17_board_pack_{PERIOD}.xlsx"})


# ---------------------------------------------------------------- recon & journals
@app.get("/api/recon")
def recon():
    q = sql.query_many({
        "recon": f"SELECT * FROM {F('gld_trial_balance_recon')} WHERE close_period = '{PERIOD}' ORDER BY recon_item",
        "history": f"SELECT close_period, sum(CASE WHEN status='tied' THEN 1 ELSE 0 END) tied, count(*) items FROM {F('gld_trial_balance_recon')} GROUP BY close_period ORDER BY close_period",
        "journals": f"SELECT * FROM {F('gov_journal_secure')} ORDER BY period DESC",
        "crosswalk": f"SELECT * FROM {F('gld_sii_crosswalk')} ORDER BY portfolio_id, item",
    })
    return q


@app.post("/api/journals")
def post_journal(body: dict):
    """Lands a journal file in the same governed feed folder — it flows through the pipeline
    like any other journal (bronze expectations, silver, recon) on the next close run."""
    # Validate before landing so a malformed journal is rejected with a clear reason (rather than
    # silently quarantining downstream in DLT with no user feedback).
    dr = str(body.get("gl_account_dr", "")).strip()
    cr = str(body.get("gl_account_cr", "")).strip()
    try:
        amt = float(body.get("amount_eur", 0))
    except (TypeError, ValueError):
        return {"ok": False, "reason": "amount_eur must be a number"}
    if not dr or not cr:
        return {"ok": False, "reason": "gl_account_dr and gl_account_cr are both required"}
    if dr == cr:
        return {"ok": False, "reason": "debit and credit accounts must differ"}
    if amt == 0:
        return {"ok": False, "reason": "amount_eur must be non-zero"}
    known = sql.query_one(f"SELECT count(*) n FROM {F('ref_coa_mapping')} "
                          f"WHERE gl_account IN ('{sql.esc(dr)}', '{sql.esc(cr)}')")
    if int((known or {}).get("n", 0) or 0) < 2:
        return {"ok": False, "reason": f"unknown GL account(s): dr={dr}, cr={cr} must exist in the chart of accounts"}
    jid = f"MJ-{PERIOD}-APP-{uuid.uuid4().hex[:6].upper()}"
    row = (f"{jid},{sql.esc(body.get('period', PERIOD))},{sql.esc(body.get('gl_account_dr', ''))},"
           f"{sql.esc(body.get('gl_account_cr', ''))},{float(body.get('amount_eur', 0))},"
           f"\"{sql.esc(body.get('narrative', ''))}\",workbench.app@bricksurance.example,pending,pending")
    content = ("journal_id,period,gl_account_dr,gl_account_cr,amount_eur,narrative,posted_by,approved_by,status\n"
               + row + "\n")
    w = config.get_workspace_client()
    path = f"/Volumes/{config.CATALOG}/{config.SCHEMA}/ifrs17_files/landing/gl_trial_balance/manual_journals_app_{jid}.csv"
    w.files.upload(path, io.BytesIO(content.encode()), overwrite=True)
    return {"journal_id": jid, "landed": path,
            "note": "flows through bronze→silver→recon on the next close run; approval required before it adjusts"}


# ---------------------------------------------------------------- sign-off + auditor mode
@app.get("/api/signoff")
def signoff():
    q = sql.query_many({
        "approvals": f"SELECT * FROM {F('gov_close_approvals')} WHERE close_period = '{PERIOD}' ORDER BY approved_at DESC",
        "certificates": f"SELECT certificate_id, close_period, signed_by, signed_at, sha256, pdf_path FROM {F('gov_signoff_certificates')} ORDER BY signed_at DESC",
        "status": f"SELECT * FROM {F('gld_close_status')} WHERE close_period = '{PERIOD}' ORDER BY working_day",
        "runs": f"SELECT * FROM {F('gov_run_audit')} WHERE close_period = '{PERIOD}' ORDER BY finished_at DESC",
    })
    return q


ALLOWED_WORKSTREAMS = {"Data & DQ", "Measurement", "Reconciliation", "Disclosures", "CFO sign-off", "Close"}
ALLOWED_DECISIONS = {"approved", "rejected"}


@app.post("/api/signoff/approve")
def approve(body: dict, request: Request):
    ws = body.get("workstream", "Close")
    dec = body.get("decision", "approved")
    if ws not in ALLOWED_WORKSTREAMS:
        return {"ok": False, "reason": f"unknown workstream '{ws}'"}
    if dec not in ALLOWED_DECISIONS:
        return {"ok": False, "reason": f"unknown decision '{dec}'"}
    # Identity is the authenticated user, never a body field.
    who = _actor(request)
    com = body.get("comment", "")
    # Server-side gate: the CFO sign-off is refused unless the close is actually ready, and unless
    # the prerequisite workstreams have themselves been approved. No client can bypass this.
    if dec == "approved" and ws == "CFO sign-off":
        ready = _close_ready()
        if not ready["ready"]:
            return {"ok": False, "reason": "close not ready for sign-off: " + "; ".join(ready["reasons"])}
        prereq = sql.query_one(f"""SELECT count(DISTINCT workstream) n FROM {F('gov_close_approvals')}
            WHERE close_period='{PERIOD}' AND decision='approved'
              AND workstream IN ('Data & DQ','Measurement','Reconciliation','Disclosures')""")
        if int((prereq or {}).get("n", 0) or 0) < 4:
            return {"ok": False, "reason": "prerequisite workstream approvals incomplete (need Data & DQ, Measurement, Reconciliation, Disclosures)"}
    sql.query(f"INSERT INTO {F('gov_close_approvals')} VALUES ('{PERIOD}', '{sql.esc(ws)}', '{sql.esc(dec)}', '{sql.esc(who)}', '{sql.esc(com)}', current_timestamp())")
    if dec == "approved" and ws == "CFO sign-off":
        sql.query(f"""MERGE INTO {F('gld_close_status')} t USING (SELECT '{PERIOD}' cp, 9 wd, 'Sign-off' ws) s
                      ON t.close_period=s.cp AND t.working_day=s.wd AND t.workstream=s.ws
                      WHEN MATCHED THEN UPDATE SET status='done', detail='signed by {sql.esc(who)}', updated_at=current_timestamp()""")
    return {"ok": True, "approver": who}


@app.post("/api/signoff/certificate")
def certificate(body: dict = None, request: Request = None):
    body = body or {}
    ready = _close_ready()
    if not ready["ready"]:
        return {"error": "close not ready — certificate refused", "reasons": ready["reasons"]}
    # Signer is the authenticated user, never a body field.
    signed_by = _actor(request) if request is not None else "unknown"
    figures = sql.query_many({
        "pnl": f"SELECT line_item, amount FROM {F('gld_pnl_statement')} WHERE close_period='{PERIOD}' ORDER BY line_no",
        "lc": f"SELECT round(sum(amount),2) v FROM {F('gld_loss_component')} WHERE close_period='{PERIOD}' AND step='closing'",
        "csm": f"SELECT round(sum(amount),2) v FROM {F('gld_csm_rollforward')} WHERE close_period='{PERIOD}' AND step='closing'",
        "runs": f"SELECT run_id, engine, input_versions, assumption_versions, curve_dates FROM {F('gov_run_audit')} WHERE close_period='{PERIOD}' ORDER BY finished_at DESC LIMIT 10",
        "approvals": f"SELECT workstream, decision, approver, cast(approved_at as string) approved_at FROM {F('gov_close_approvals')} WHERE close_period='{PERIOD}'",
        "assumptions": f"SELECT assumption_id, version, approved_by, cast(approved_at as string) approved_at FROM {F('gov_assumption_registry')} WHERE status='active'",
    })
    key_figures = {r["line_item"]: r["amount"] for r in figures["pnl"]}
    key_figures["loss_component_closing"] = (figures["lc"][0] or {}).get("v") if figures["lc"] else None
    key_figures["csm_closing"] = (figures["csm"][0] or {}).get("v") if figures["csm"] else None
    cert_id = f"CERT-{PERIOD}-{uuid.uuid4().hex[:6].upper()}"
    evidence = {"signed_at": datetime.datetime.utcnow().isoformat() + "Z", "key_figures": key_figures,
                "runs": figures["runs"], "approvals": figures["approvals"], "assumptions": figures["assumptions"]}
    pdf_bytes, sha = build_certificate(cert_id, PERIOD, signed_by, evidence)
    w = config.get_workspace_client()
    path = f"/Volumes/{config.CATALOG}/{config.SCHEMA}/ifrs17_files/packs/{cert_id}.pdf"
    w.files.upload(path, io.BytesIO(pdf_bytes), overwrite=True)
    # Escape BACKSLASHES before single quotes: the evidence contains nested JSON (input_versions) with
    # \" sequences, and a Databricks SQL string literal processes backslash escapes — without this the
    # \" collapse to " and the stored evidence_json becomes invalid JSON (breaks reproduce()).
    ev_sql = json.dumps(evidence, default=str).replace("\\", "\\\\").replace("'", "''")
    sql.query(f"""INSERT INTO {F('gov_signoff_certificates')} VALUES ('{cert_id}', '{PERIOD}',
                  '{sql.esc(signed_by)}', current_timestamp(), '{ev_sql}',
                  '{sha}', '{path}')""")
    return {"certificate_id": cert_id, "sha256": sha, "pdf_path": path}


@app.get("/api/certificate/{cert_id}")
def get_certificate(cert_id: str):
    row = sql.query_one(f"SELECT pdf_path FROM {F('gov_signoff_certificates')} WHERE certificate_id = '{sql.esc(cert_id)}'")
    if not row:
        return {"error": "not found"}
    w = config.get_workspace_client()
    data = w.files.download(row["pdf_path"]).contents.read()
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename={cert_id}.pdf"})


def _num(x):
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return None


@app.post("/api/audit/reproduce")
def reproduce(body: dict = None):
    """Auditor mode: take a SIGNED number from a specific certificate and re-read it from the Delta
    history AS AT the moment it was signed (TIMESTAMP AS OF signed_at) — proving the signed figure is
    reproducible, and separately showing whether the book has moved since. This is the honest claim:
    it reproduces the signed OUTPUT, it does not re-run the engines (that's the close rerun lever)."""
    body = body or {}
    metric = body.get("metric", "loss_component_closing")
    # Pick the certificate to reproduce against: an explicit id, else the latest signed one.
    if body.get("certificate_id"):
        cert = sql.query_one(f"""SELECT certificate_id, cast(signed_at as string) signed_at, signed_by, evidence_json
                                 FROM {F('gov_signoff_certificates')}
                                 WHERE certificate_id='{sql.esc(body['certificate_id'])}'""")
    else:
        cert = sql.query_one(f"""SELECT certificate_id, cast(signed_at as string) signed_at, signed_by, evidence_json
                                 FROM {F('gov_signoff_certificates')} WHERE close_period='{PERIOD}'
                                 ORDER BY signed_at DESC LIMIT 1""")
    table, expr = {
        "loss_component_closing": ("gld_loss_component", "round(sum(amount),2) v FROM {t} WHERE close_period='%s' AND step='closing'" % PERIOD),
        "csm_closing": ("gld_csm_rollforward", "round(sum(amount),2) v FROM {t} WHERE close_period='%s' AND step='closing'" % PERIOD),
        "insurance_revenue": ("gld_insurance_revenue", "round(sum(amount),2) v FROM {t} WHERE close_period='%s'" % PERIOD),
        "lic_closing": ("gld_lic_rollforward", "round(sum(amount),2) v FROM {t} WHERE close_period='%s' AND step='closing'" % PERIOD),
    }.get(metric, ("gld_loss_component", "round(sum(amount),2) v FROM {t} WHERE close_period='%s' AND step='closing'" % PERIOD))

    if not cert:
        return {"metric": metric, "table": table, "error": "no signed certificate found — sign the close first",
                "match": None}

    # The figure the CFO actually signed (from the certificate's frozen evidence).
    signed_value = None
    try:
        signed_value = _num((json.loads(cert["evidence_json"]).get("key_figures") or {}).get(metric))
    except Exception:
        signed_value = None
    signed_at = cert["signed_at"]

    live = _num((sql.query_one(f"SELECT {expr.format(t=F(table))}") or {}).get("v"))
    # Pin to the table VERSION that was current WHEN the certificate was signed — i.e. the latest commit
    # at or before signed_at. (Sign-off always follows the close write, so TIMESTAMP AS OF signed_at would
    # be *after* the latest commit and fail; the version-at-sign-off is the correct, stable anchor.)
    pinned, pinned_version, pinned_error = None, None, None
    try:
        vrow = sql.query_one(f"SELECT max(version) v FROM (DESCRIBE HISTORY {F(table)}) "
                             f"WHERE timestamp <= timestamp('{sql.esc(signed_at)}')")
        pinned_version = (vrow or {}).get("v")
        if pinned_version is None:
            pinned_error = "no table version exists at or before the sign-off time (history trimmed)."
        else:
            pinned = _num((sql.query_one(
                f"SELECT {expr.format(t=F(table) + f' VERSION AS OF {int(pinned_version)}')}") or {}).get("v"))
    except Exception as e:  # e.g. that version's files are past Delta retention
        pinned_error = ("cannot re-read the sign-off version — it is beyond this table's Delta retention "
                        f"window; sign a fresh certificate to show reproduction. [{str(e)[:100]}]")

    # The producing run whose input/assumption versions are stamped in the audit log.
    run = sql.query_one(f"""SELECT run_id, engine, input_versions, assumption_versions, curve_dates,
                                   cast(finished_at as string) finished_at
                            FROM {F('gov_run_audit')} WHERE close_period='{PERIOD}'
                              AND finished_at <= timestamp('{sql.esc(signed_at)}')
                              AND engine IN ('paa_lic_engine','gmm_csm_engine','disclosure_engine')
                            ORDER BY finished_at DESC LIMIT 1""")

    reference = signed_value if signed_value is not None else pinned
    return {"metric": metric, "table": table, "certificate_id": cert["certificate_id"],
            "signed_by": cert.get("signed_by"), "signed_at": signed_at,
            "signed_value": signed_value, "value_at_signing": pinned, "pinned_version": pinned_version,
            "live_value": live,
            "reproduced": (pinned is not None and reference is not None and pinned == reference),
            "match": (pinned is not None and signed_value is not None and pinned == signed_value),
            "book_moved_since_signing": (signed_value is not None and live != signed_value),
            "pinned_error": pinned_error, "producing_run": run,
            "note": "Reproduced by re-reading the Delta history AS AT the sign-off timestamp and comparing "
                    "to the figure frozen in the certificate. If the book has since changed, the live value "
                    "differs while the signed number still reproduces from history — the audit trail is a join."}


@app.post("/api/audit/narrate")
def audit_narrate(body: dict = None):
    body = body or {}
    ev = reproduce(body)
    return agents.narrate("audit_evidence", "Explain to an auditor how this number is reproduced.",
                          ev, use_cache=body.get("cache"))


@app.post("/api/prewarm")
def prewarm(body: dict = None):
    """Populate the narration cache for the hero beats so the FIRST live narration in the room is
    instant. Reset clears the cache; warm afterwards. CHUNKED — one beat per call — because warming
    all beats in a single request exceeds the Databricks Apps gateway timeout. POST {} to list the
    beats, then POST {"beat": <name>} for each (a tiny client loop, or the presenter clicks the heroes)."""
    body = body or {}
    beats = {
        "cfo_brief": lambda: cfo_brief({"cache": True}),
        "cohort_prop_2026": lambda: cohort_narrate("PROP-2026-REM", {"cache": True}),
        "cohort_clt_2025": lambda: cohort_narrate("CLT-2025-NSP", {"cache": True}),
        "audit_loss_component": lambda: audit_narrate({"metric": "loss_component_closing", "cache": True}),
        "disclosure_loss_component": lambda: disclosure_note({"topic": "loss_component", "cache": True}),
    }
    beat = body.get("beat")
    if not beat:
        return {"beats": list(beats), "note": "POST {\"beat\": <name>} per beat (chunked under the gateway timeout)"}
    fn = beats.get(beat)
    if not fn:
        return {"ok": False, "reason": f"unknown beat '{beat}'", "beats": list(beats)}
    try:
        fn()
        return {"ok": True, "warmed": beat}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "beat": beat, "error": str(e)[:120]}


# ---------------------------------------------------------------- governance
@app.get("/api/governance/{tab}")
def governance(tab: str):
    queries = {
        "inventory": f"SELECT * FROM {F('gov_data_inventory')} ORDER BY sensitivity_tier, table_name",
        "runs": f"SELECT * FROM {F('gov_run_audit')} ORDER BY finished_at DESC LIMIT 50",
        "models": f"SELECT * FROM {F('gov_model_register')} ORDER BY model_key",
        "activity": f"SELECT * FROM {F('gld_ai_activity')} ORDER BY ts DESC LIMIT 50",
        "lineage": f"SELECT * FROM {F('gov_lineage_view')} ORDER BY last_event DESC LIMIT 100",
        "masking": f"SELECT journal_id, period, amount_eur, narrative, posted_by, approved_by, status FROM {F('gov_journal_secure')}",
        "assumptions": f"SELECT * FROM {F('gov_assumption_registry')} ORDER BY assumption_id, version",
    }
    if tab not in queries:
        return {"error": "unknown tab"}
    return {"rows": sql.query(queries[tab])}


# ---------------------------------------------------------------- IFRS 17 AI
@app.post("/api/ai/ask")
def ai_ask(body: dict):
    return agents.ask_agent(body.get("question", ""),
                            custom_inputs={"group_id": body.get("group_id")} if body.get("group_id") else {},
                            use_cache=body.get("cache"))


@app.get("/api/ai/bench")
def ai_bench():
    eps = {role: config.resolve_endpoint(substr) for role, substr in config.ROLE_SUBSTR.items()}
    eps["supervisor"] = config.resolve_endpoint(config.EP_AGENT_SUBSTR)
    activity = sql.query(f"SELECT agent, count(*) calls, max(ts) last_used FROM {F('gld_ai_activity')} GROUP BY agent ORDER BY calls DESC")
    return {"endpoints": eps, "activity": activity}


# ---------------------------------------------------------------- levers + cache
def _run_job_by_substr(substr, params=None):
    w = config.get_workspace_client()
    for j in w.jobs.list(name=None, expand_tasks=False, limit=100):
        if substr in (j.settings.name or ""):
            r = w.jobs.run_now(job_id=j.job_id, job_parameters=params or {})
            return {"job": j.settings.name, "run_id": r.run_id}
    return {"error": f"job matching '{substr}' not found"}


@app.post("/api/levers/{name}")
def lever(name: str):
    if name == "inject_bad_feed":
        return _run_job_by_substr(config.LEVERS_JOB_SUBSTR, {"action": "inject"})
    if name == "restore_feed":
        return _run_job_by_substr(config.LEVERS_JOB_SUBSTR, {"action": "restore"})
    if name == "rerun_close":
        return _run_job_by_substr(config.CLOSE_JOB_SUBSTR)
    if name == "reset_demo":
        return _run_job_by_substr(config.RESET_JOB_SUBSTR)
    return {"error": "unknown lever"}


@app.get("/api/levers/run/{run_id}")
def lever_status(run_id: int):
    w = config.get_workspace_client()
    r = w.jobs.get_run(run_id=run_id)
    state = r.state.life_cycle_state.value if r.state and r.state.life_cycle_state else "UNKNOWN"
    result = r.state.result_state.value if r.state and r.state.result_state else None
    tasks = [{"task": t.task_key,
              "state": (t.state.life_cycle_state.value if t.state and t.state.life_cycle_state else "?"),
              "result": (t.state.result_state.value if t.state and t.state.result_state else None)}
             for t in (r.tasks or [])]
    dur = None
    if r.start_time:
        end = r.end_time or int(datetime.datetime.now().timestamp() * 1000)
        dur = round((end - r.start_time) / 1000)
    return {"state": state, "result": result, "tasks": tasks, "duration_seconds": dur,
            "run_page_url": r.run_page_url}


@app.post("/api/cache/toggle")
def cache_toggle(body: dict = None):
    config.USE_CACHE = bool((body or {}).get("on", not config.USE_CACHE))
    return {"use_cache": config.USE_CACHE}


# ---------------------------------------------------------------- SPA
import os

DIST = os.path.join(os.path.dirname(__file__), "dist")


@app.get("/")
def index():
    return FileResponse(os.path.join(DIST, "index.html"))


# --- MCP server: the full workbench surface as governed tools (control-tower client) ---
import sys as _sys
from server import mcp as _mcp
app.include_router(_mcp.register(_sys.modules[__name__]))
