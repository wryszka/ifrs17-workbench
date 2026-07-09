# Databricks notebook source
# MAGIC %md
# MAGIC # 06a · Role agents — IFRS 17 AI (narrate-only)
# MAGIC
# MAGIC One pyfunc model, four serving endpoints differentiated by `AGENT_ROLE`. Each receives the
# MAGIC structured findings the app computed from the UC functions (`data_json`) and writes prose.
# MAGIC **LLMs narrate, SQL decides**: no role agent ever computes, books or signs anything —
# MAGIC a named human approves every output that matters.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "ifrs17_workbench")
dbutils.widgets.text("fm_endpoint", "databricks-claude-sonnet-4-5")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
FM = dbutils.widgets.get("fm_endpoint")
FQ = f"{catalog}.{schema}"

import mlflow
import pandas as pd
from mlflow.models.signature import infer_signature
from mlflow.models.resources import DatabricksServingEndpoint
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.serving import EndpointCoreConfigInput, ServedEntityInput

mlflow.set_registry_uri("databricks-uc")

# COMMAND ----------

class Ifrs17Agent(mlflow.pyfunc.PythonModel):
    """Narrate-only FM-backed agent. Input columns: role, question, data_json → prose per row."""

    def load_context(self, context):
        import os
        from mlflow.deployments import get_deploy_client
        self.client = get_deploy_client("databricks")
        self.fm = os.environ.get("FM_ENDPOINT", "databricks-claude-sonnet-4-5")
        self.default_role = os.environ.get("AGENT_ROLE", "movement_narrator")
        self.systems = {
            "movement_narrator": (
                "You are the analysis-of-change analyst on the IFRS 17 results desk at Bricksurance SE (a "
                "fictional European P&C insurer; all data synthetic). From the structured findings (CSM "
                "waterfall / onerous test / AvE decomposition — already computed by governed engines), write "
                "3-4 tight sentences explaining WHY the number moved, in IFRS 17 language a Head of Reporting "
                "would use: separate experience (past service) from assumption changes (future service) from "
                "unwind and rate effects; name the assumption version and its approver where present. Never "
                "invent figures, never recompute — narrate what is in the data. Terminology discipline: "
                "insurance revenue is never premium; PAA groups have no CSM; groups never re-bucket."),
            "disclosure_drafter": (
                "You draft disclosure-note commentary for Bricksurance SE's IFRS 17 reporting pack. From the "
                "structured roll-forward and statement figures, draft the requested note paragraph in the "
                "register of a published European insurer annual report: factual, past tense, references to "
                "the movements actually in the data (loss component recognised, CSM release via coverage "
                "units, RA at the 75% confidence level, OCI disaggregation). 60-120 words unless asked "
                "otherwise. End with 'Draft — for reporting team review.' A human approves every note."),
            "audit_evidence": (
                "You are the audit-evidence explainer at Bricksurance SE. From a gov_run_audit record and its "
                "linked evidence (input Delta table versions, assumption versions with approvers, curve dates, "
                "DQ gate verdict), explain in plain English exactly how the number in question was produced "
                "and how an auditor would reproduce it (time-travel to the pinned versions, same engine, same "
                "assumptions). 3-5 sentences. Precise, calm, zero marketing."),
            "cfo_brief": (
                "You are the CFO's chief of staff at Bricksurance SE. From the structured close findings "
                "(close status board, P&L, onerous movements, recon status), write the CLOSE BRIEF: exactly "
                "three bullet points, each one sentence, each naming a number and what it means for sign-off "
                "(e.g. the property loss component and its reinsurance offset, the insurance service result, "
                "anything still blocking). Then one closing line on whether the close is ready to sign. "
                "Crisp, quantified, no preamble. You brief; the CFO decides."),
        }

    def _one(self, role, question, data_json):
        system = self.systems.get(role or self.default_role, self.systems["movement_narrator"])
        user = (f"Request: {question}\n\nStructured findings (already computed by governed Databricks "
                f"engines and UC functions — narrate, never recompute or invent figures):\n{data_json}")
        try:
            resp = self.client.predict(endpoint=self.fm, inputs={
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "max_tokens": 700, "temperature": 0.2})
            return resp["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001 — surface, never crash the endpoint
            return f"[narration unavailable: {str(e)[:120]}]"

    def predict(self, context, model_input):
        return [self._one(r.get("role"), r.get("question", ""), r.get("data_json", ""))
                for _, r in model_input.iterrows()]

# COMMAND ----------

example = pd.DataFrame([{"role": "movement_narrator", "question": "Why did PROP-2026-REM turn onerous?",
                         "data_json": '{"headroom":-1500000.0,"assumption":"flood_freq_property v2"}'}])
sig = infer_signature(example, ["..."])
with mlflow.start_run(run_name="ifrs17_agent"):
    mi = mlflow.pyfunc.log_model(
        artifact_path="model", python_model=Ifrs17Agent(),
        signature=sig, input_example=example,
        pip_requirements=["mlflow", "pandas"],
        resources=[DatabricksServingEndpoint(endpoint_name=FM)],
        registered_model_name=f"{FQ}.model_ifrs17_agent")
ver = mi.registered_model_version
print("agent model v", ver)

# COMMAND ----------

import time

w = WorkspaceClient()


def _wait_not_updating(endpoint, timeout_s=1500):
    """Endpoint create/update is async — a job-task retry can race a half-provisioned endpoint."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            ep = w.serving_endpoints.get(endpoint)
            cu = getattr(ep.state, "config_update", None)
            if cu is None or str(cu).endswith("NOT_UPDATING"):
                return True
        except Exception:  # noqa: BLE001 — endpoint may not exist yet
            return True
        time.sleep(30)
    return False


def deploy_agent(endpoint, role):
    entity = ServedEntityInput(name="agent", entity_name=f"{FQ}.model_ifrs17_agent",
                               entity_version=ver, workload_size="Small", scale_to_zero_enabled=True,
                               environment_vars={"AGENT_ROLE": role, "FM_ENDPOINT": FM})
    existing = [e.name for e in w.serving_endpoints.list()]
    for attempt in range(5):
        try:
            if endpoint in existing:
                _wait_not_updating(endpoint)
                w.serving_endpoints.update_config(name=endpoint, served_entities=[entity])
            else:
                w.serving_endpoints.create(name=endpoint, config=EndpointCoreConfigInput(name=endpoint, served_entities=[entity]))
            print("deploying", endpoint, "(role:", role + ")")
            return
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "currently being updated" in msg or "currently updating" in msg or "RESOURCE_CONFLICT" in msg:
                print(f"  {endpoint}: update in progress (attempt {attempt + 1}) — waiting…")
                time.sleep(90)
                existing = [x.name for x in w.serving_endpoints.list()]
                continue
            raise
    print(f"  {endpoint}: still updating after retries — the in-flight config will serve; rerun to pin v{ver}")


deploy_agent("ifrs17-movement", "movement_narrator")
deploy_agent("ifrs17-disclosure", "disclosure_drafter")
deploy_agent("ifrs17-evidence", "audit_evidence")
deploy_agent("ifrs17-brief", "cfo_brief")
print("06a — 4 role agents deploying (non-blocking)")
