# Databricks notebook source
# MAGIC %md
# MAGIC # 06b · IFRS 17 AI supervisor — a REAL tool-calling agent
# MAGIC
# MAGIC A `ChatAgent` with a genuine Claude tool-use loop: the LLM autonomously decides which UC
# MAGIC functions to call (cohort summary, CSM waterfall, onerous test, AoC, discount impact,
# MAGIC coverage units, recon, close status, LIC, reinsurance held, assumption history, Genie),
# MAGIC executes them via the Statement Execution API, and returns a grounded answer **plus the
# MAGIC tool-call trace** the app surfaces as proof of real tool use. It narrates and explains;
# MAGIC it never books, signs or approves — the close is signed by a named human.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "ifrs17_workbench")
dbutils.widgets.text("fm_endpoint", "databricks-claude-sonnet-4-5")
dbutils.widgets.text("warehouse_id", "a3b61648ea4809e3")
dbutils.widgets.text("genie_space_id", "")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
fm_endpoint = dbutils.widgets.get("fm_endpoint")
warehouse_id = dbutils.widgets.get("warehouse_id")
genie_space_id = dbutils.widgets.get("genie_space_id")
FQ = f"{catalog}.{schema}"

import json, os, uuid

import mlflow
from mlflow.pyfunc import ChatAgent
from mlflow.types.agent import ChatAgentMessage, ChatAgentResponse

mlflow.set_registry_uri("databricks-uc")

SYSTEM = (
    "You are the IFRS 17 AI supervisor at Bricksurance SE, a European P&C insurer (fictional; synthetic data). "
    "You help the Head of Reporting, the CFO and auditors understand the quarterly IFRS 17 close. You have "
    "tools that call real Unity Catalog functions over the governed measurement engines — USE THEM to ground "
    "every claim in numbers; never invent or recompute figures. Group ids look like PROP-2026-REM, "
    "CLT-2025-NSP, RO-2020-LIC; portfolios are MOT, PROP, LIAB, CLT, DEC, RO; periods look like 2026Q2. "
    "For 'why did X move' questions call the onerous test / CSM roll-forward / AvE tools, then explain with "
    "the past-service vs future-service split and name the assumption version and approver. TERMINOLOGY "
    "DISCIPLINE (non-negotiable): insurance revenue is never premium; PAA groups have no CSM; groups are "
    "fixed at initial recognition and never re-bucket; reinsurance held is never onerous (it has a "
    "loss-recovery component); the CSM accretes at locked-in rates so current-rate moves land in OCI, not "
    "the CSM. You explain and advise; humans book, approve and sign — never say you have posted or signed "
    "anything.")

TOOLS = {
    "get_cohort_summary": ("Group summary at the current close: model, bucket, LRC/LC/CSM/RA, onerous flag + headroom, revenue. First call for any group question.", ["group_id"]),
    "get_csm_rollforward": ("B96-ordered CSM waterfall for a GMM group in a period (opening → new business → locked-in accretion → future-service changes → release → closing).", ["group_id", "period"]),
    "get_onerous_test": ("§57 onerous test for a PAA group in a period: FCF (current rates + RA) vs LRC carrying; loss component = the excess.", ["group_id", "period"]),
    "get_ave_analysis": ("Analysis of change for a portfolio in a period: experience vs assumption changes vs unwind vs rate effects.", ["portfolio", "period"]),
    "get_discount_impact": ("GMM locked-in vs current FCF and the OCI balance for a group in a period.", ["group_id", "period"]),
    "get_coverage_units": ("Coverage units and CSM release mechanics for a GMM group in a period.", ["group_id", "period"]),
    "get_recon_check": ("Subledger vs GL reconciliation status for a period, including the reclass journal.", ["period"]),
    "get_close_status": ("The Day 1-10 close status board for a period — where the close is, what is blocked.", ["period"]),
    "get_lic_summary": ("LIC position for a portfolio in a period (discounted + RA): closing, incurred, past-service, IFIE, paid.", ["portfolio", "period"]),
    "get_ri_held": ("Reinsurance held position for a period incl. the loss-recovery component offsetting the gross loss component.", ["period"]),
    "get_assumption_history": ("Version history of a governed assumption (value, source study, approver, approval date).", ["assumption_id"]),
    "ask_the_close": ("Ask a natural-language analytics question over the IFRS 17 close marts via AI/BI Genie.", ["question"]),
}

FN_BY_TOOL = {
    "get_cohort_summary": "fn_cohort_summary", "get_csm_rollforward": "fn_csm_rollforward",
    "get_onerous_test": "fn_onerous_test", "get_ave_analysis": "fn_ave_analysis",
    "get_discount_impact": "fn_discount_impact", "get_coverage_units": "fn_coverage_units",
    "get_recon_check": "fn_recon_check", "get_close_status": "fn_close_status",
    "get_lic_summary": "fn_lic_summary", "get_ri_held": "fn_ri_held",
    "get_assumption_history": "fn_assumption_history",
}


def _run_sql(sql):
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.sql import StatementState
    w = WorkspaceClient()
    wid = os.environ.get("AGENT_WAREHOUSE_ID", warehouse_id)
    r = w.statement_execution.execute_statement(statement=sql, warehouse_id=wid, wait_timeout="50s")
    if r.status and r.status.state == StatementState.FAILED:
        raise RuntimeError(r.status.error.message if r.status.error else "SQL failed")
    if not (r.manifest and r.manifest.schema and r.manifest.schema.columns):
        return []
    cols = [c.name for c in r.manifest.schema.columns]
    return [dict(zip(cols, row)) for row in (r.result.data_array or [])] if r.result else []


def _genie_ask(space_id, question):
    from databricks.sdk import WorkspaceClient
    if not space_id or not question:
        return {"note": "Genie space not configured"}
    try:
        w = WorkspaceClient()
        m = w.genie.start_conversation_and_wait(space_id=space_id, content=question)
        out = {"answer": None, "query": None}
        for att in (m.attachments or []):
            if att.text and att.text.content:
                out["answer"] = att.text.content[:1500]
            if att.query and att.query.query:
                out["query"] = att.query.query[:600]
        return out
    except Exception as e:  # noqa: BLE001
        return {"error": f"genie unavailable: {e}"}


def _call_fm(endpoint, messages, tools):
    import requests
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    host = w.config.host.rstrip("/")
    hdr = w.config._header_factory()
    r = requests.post(f"{host}/serving-endpoints/{endpoint}/invocations",
                      headers={**hdr, "Content-Type": "application/json"},
                      json={"messages": messages, "tools": tools, "tool_choice": "auto",
                            "max_tokens": 1400, "temperature": 0.1}, timeout=120)
    r.raise_for_status()
    return r.json()


class Ifrs17Supervisor(ChatAgent):
    def __init__(self, catalog, schema, fm_endpoint, genie_space_id):
        self.fqn = f"{catalog}.{schema}"
        self.fm = fm_endpoint
        self.genie = genie_space_id

    def _scalar(self, fn, args):
        arglist = ", ".join(f"'{str(a).replace(chr(39), '')}'" for a in args)
        rows = _run_sql(f"SELECT to_json({self.fqn}.{fn}({arglist})) AS r")
        return json.loads(rows[0]["r"]) if rows and rows[0].get("r") else {"error": "no row"}

    def _tool(self, name, args):
        a = args or {}
        if name == "ask_the_close":
            return _genie_ask(self.genie, a.get("question", ""))
        fn = FN_BY_TOOL.get(name)
        if not fn:
            return {"error": f"unknown tool {name}"}
        params = TOOLS[name][1]
        return self._scalar(fn, [a.get(p, "") for p in params])

    def predict(self, messages, context=None, custom_inputs=None) -> ChatAgentResponse:
        ci = custom_inputs or {}
        hint = ""
        if ci.get("group_id"):
            hint = (f"\nThe group under review is group_id='{ci['group_id']}'. Pass this id to the group tools. "
                    f"The current close period is 2026Q2.")
        else:
            hint = "\nThe current close period is 2026Q2."
        full = [{"role": "system", "content": SYSTEM + hint}]
        for m in messages:
            full.append({"role": m.role, "content": m.content or ""})
        tools = [{"type": "function", "function": {"name": n, "description": TOOLS[n][0],
                  "parameters": {"type": "object",
                                 "properties": {p: {"type": "string"} for p in TOOLS[n][1]},
                                 "required": TOOLS[n][1]}}} for n in TOOLS]
        trace, final = [], ""
        for _hop in range(8):
            resp = _call_fm(self.fm, full, tools)
            choices = resp.get("choices") or []
            if not choices:
                break
            msg = choices[0].get("message") or {}
            tcs = msg.get("tool_calls") or []
            if tcs:
                full.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tcs})
                for tc in tcs:
                    fnm = (tc.get("function") or {}).get("name")
                    raw = (tc.get("function") or {}).get("arguments") or "{}"
                    try:
                        a = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    except Exception:  # noqa: BLE001
                        a = {}
                    res = self._tool(fnm, a)
                    trace.append({"tool": fnm, "args": a})
                    full.append({"role": "tool", "tool_call_id": tc.get("id") or fnm,
                                 "content": json.dumps(res, default=str)[:8000]})
                continue
            final = msg.get("content") or ""
            break
        return ChatAgentResponse(
            messages=[ChatAgentMessage(role="assistant", content=final, id=str(uuid.uuid4()))],
            custom_outputs={"trace": trace, "model": self.fm})

# COMMAND ----------

# Local smoke before logging — proves the tool loop calls real UC functions.
_local = Ifrs17Supervisor(catalog, schema, fm_endpoint, genie_space_id)
_r = _local.predict([ChatAgentMessage(role="user",
                                      content="Why did the 2026 property cohort turn onerous this quarter, and what offsets it?",
                                      id="u1")],
                    custom_inputs={"group_id": "PROP-2026-REM"})
print("LOCAL:", _r.messages[0].content[:600])
print("TOOLS CALLED:", [t["tool"] for t in (_r.custom_outputs or {}).get("trace", [])])
assert (_r.custom_outputs or {}).get("trace"), "supervisor must call real tools"

# COMMAND ----------

# MAGIC %md ## Log + register + deploy

# COMMAND ----------

from mlflow.models.resources import (DatabricksFunction, DatabricksGenieSpace,
                                     DatabricksServingEndpoint, DatabricksSQLWarehouse, DatabricksTable)

FNS = list(FN_BY_TOOL.values())
resources = [DatabricksServingEndpoint(endpoint_name=fm_endpoint),
             DatabricksSQLWarehouse(warehouse_id=warehouse_id)]
resources += [DatabricksFunction(function_name=f"{FQ}.{fn}") for fn in FNS]
for t in ["gld_cohort_360", "gld_csm_rollforward", "gld_onerous_test", "gld_loss_component",
          "gld_ave_analysis", "gld_discount_impact", "gld_coverage_units", "gld_trial_balance_recon",
          "gld_close_status", "gld_lic_rollforward", "gld_ri_held", "gov_assumption_registry"]:
    resources.append(DatabricksTable(table_name=f"{FQ}.{t}"))
if genie_space_id:
    resources.append(DatabricksGenieSpace(genie_space_id=genie_space_id))

agent_uc_name = f"{FQ}.ifrs17_agent"
input_example = {"messages": [{"role": "user", "content": "Why did the property cohort turn onerous?"}],
                 "custom_inputs": {"group_id": "PROP-2026-REM"}}
with mlflow.start_run(run_name="ifrs17_supervisor_agent"):
    mi = mlflow.pyfunc.log_model(
        artifact_path="agent",
        python_model=Ifrs17Supervisor(catalog, schema, fm_endpoint, genie_space_id),
        resources=resources, input_example=input_example,
        registered_model_name=agent_uc_name,
        pip_requirements=["mlflow", "databricks-sdk>=0.30.0", "requests"])
    print("logged:", mi.model_uri)

from mlflow.tracking import MlflowClient

mc = MlflowClient(registry_uri="databricks-uc")
version = max(int(v.version) for v in mc.search_model_versions(f"name='{agent_uc_name}'"))

import time

from databricks import agents

dep = None
for attempt in range(8):
    try:
        dep = agents.deploy(model_name=agent_uc_name, model_version=version, scale_to_zero=True,
                            environment_vars={"AGENT_WAREHOUSE_ID": warehouse_id},
                            tags={"project": "ifrs17_workbench", "layer": "agent"})
        break
    except Exception as e:  # noqa: BLE001 — endpoint provisioning is async; retries race it
        if "currently updating" in str(e) or "currently being updated" in str(e):
            print(f"endpoint updating (attempt {attempt + 1}) — waiting 120s…")
            time.sleep(120)
            continue
        raise
assert dep is not None, "agents.deploy did not succeed after retries"
ep_name = getattr(dep, "endpoint_name", None) or getattr(dep, "endpoint", None)
print("agents.deploy →", ep_name)
dbutils.notebook.exit(json.dumps({"endpoint_name": str(ep_name), "version": version}))
