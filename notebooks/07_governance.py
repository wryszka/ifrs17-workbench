# Databricks notebook source
# MAGIC %md
# MAGIC # 07 · Governance — evidence, masking, model register, AI activity, lineage
# MAGIC
# MAGIC Reads existing objects and adds governed surfaces — no measurement logic here.
# MAGIC - `gov_data_inventory`: what's collected, per feed, with sensitivity tiers.
# MAGIC - `gov_journal_secure`: UC-enforced masking — the app service principal is deliberately
# MAGIC   OUTSIDE `ifrs17_finance_controllers`, so poster/approver identities are redacted by
# MAGIC   Unity Catalog itself, not by app code.
# MAGIC - `gov_model_register`: PRA SS1/23-shaped register, joined LIVE to the UC model registry.
# MAGIC - `gld_ai_activity`: every agent interaction, logged.
# MAGIC - `gov_close_approvals` / `gov_signoff_certificates`: the sign-off trail (app writes).
# MAGIC - `gov_lineage_view`: real system.access.table_lineage for this schema.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "ifrs17_workbench")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
FQ = f"{catalog}.{schema}"

# COMMAND ----------

# MAGIC %md ## Data inventory — what's collected and how sensitive it is

# COMMAND ----------

INVENTORY = [
    # table, feed, contains, tier, masking
    ("brz_policy_admin", "Policy admin core", "Policy terms, premiums, sums insured", "INTERNAL", "none"),
    ("brz_claims", "Guidewire ClaimCenter", "Claim events, causes, catastrophe codes", "INTERNAL", "none"),
    ("brz_claim_transactions", "Claims finance", "Payment cash flows", "INTERNAL", "none"),
    ("brz_actuarial_cashflows", "Reserving system", "Projected cash flows, assumption sets", "CONFIDENTIAL", "none"),
    ("brz_gl_trial_balance", "SAP GL", "Ledger movements", "CONFIDENTIAL", "none"),
    ("brz_manual_journals", "Financial control", "Journals incl. poster/approver identities", "RESTRICTED",
     "UC view gov_journal_secure masks identities outside ifrs17_finance_controllers"),
    ("gov_assumption_registry", "Workbench-governed", "Assumptions incl. approver identities", "CONFIDENTIAL", "none"),
    ("brz_reinsurance_treaties", "Ceded re", "Treaty terms, counterparties", "CONFIDENTIAL", "none"),
    ("ref_rfr_curve", "EIOPA (public)", "Real published risk-free curves", "PUBLIC", "none"),
    ("gld_subledger_postings", "Engine output", "IFRS 17 subledger", "CONFIDENTIAL", "none"),
    ("gov_signoff_certificates", "Workbench-governed", "Sign-off evidence snapshots", "RESTRICTED", "none"),
]
spark.createDataFrame(INVENTORY, "table_name string, feed string, contains string, sensitivity_tier string, masking string") \
    .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.gov_data_inventory")
spark.sql(f"COMMENT ON TABLE {FQ}.gov_data_inventory IS 'What the close collects, per feed, with sensitivity tiers and the masking control applied.'")

# COMMAND ----------

# MAGIC %md ## UC-enforced masking — the app SP is outside the privileged group BY DESIGN

# COMMAND ----------

GROUP = "ifrs17_finance_controllers"
try:
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    if not any(g.display_name == GROUP for g in w.groups.list(filter=f'displayName eq "{GROUP}"')):
        w.groups.create(display_name=GROUP)
        print(f"created group {GROUP}")
except Exception as e:  # noqa: BLE001
    print(f"group setup note: {e} — view still enforces via is_account_group_member")

spark.sql(f"""
CREATE OR REPLACE VIEW {FQ}.gov_journal_secure
COMMENT 'Manual journals with UC-enforced identity masking: poster/approver visible ONLY to members of
{GROUP}. The workbench app service principal is deliberately outside that group — the redaction you see
in the app is Unity Catalog itself, not app code.'
AS SELECT journal_id, period, gl_account_dr, gl_account_cr, amount_eur, narrative,
          CASE WHEN is_account_group_member('{GROUP}') THEN posted_by ELSE '•••@bricksurance (masked by UC)' END AS posted_by,
          CASE WHEN is_account_group_member('{GROUP}') THEN approved_by ELSE '•••@bricksurance (masked by UC)' END AS approved_by,
          status
FROM {FQ}.slv_manual_journal
""")
print("gov_journal_secure view created")

# COMMAND ----------

# MAGIC %md ## Model register — SS1/23-shaped, joined live to Unity Catalog

# COMMAND ----------

REGISTER = [
    dict(model_key="model_lic_emergence", tier="Tier 3", purpose="Challenger view of claims ultimates from paid development",
         owner="Head of Reserving (accountable), Group Reporting (user)",
         training_data="gld_claims_triangles + slv_claim (synthetic)",
         validation="Backtest vs mature accident quarters; divergence flags on Cohort 360",
         monitoring="Divergence vs reserving view per close; retrain on drift",
         use_constraint="NEVER the booked number — reserving projections are the governed basis"),
    dict(model_key="model_ifrs17_agent", tier="Tier 3", purpose="Narrate-only role agents (AoC, disclosure drafts, evidence, CFO brief)",
         owner="Head of Reporting (accountable)",
         training_data="None (foundation model, prompt-constrained)",
         validation="Structured findings passed in; prohibited from computing; outputs marked draft",
         monitoring="Every interaction logged to gld_ai_activity",
         use_constraint="LLMs narrate, SQL decides; human approves every output that matters"),
    dict(model_key="ifrs17_agent", tier="Tier 3", purpose="Tool-calling supervisor over governed UC functions + Genie",
         owner="Head of Reporting (accountable)",
         training_data="None (foundation model + deterministic UC function tools)",
         validation="Tool-call trace surfaced in-app; answers grounded in engine tables only",
         monitoring="gld_ai_activity + serving endpoint tracing (MLflow)",
         use_constraint="Explains and advises; never books, approves or signs"),
]
try:
    uc_models = {m.name.split(".")[-1]: m for m in
                 __import__("databricks.sdk", fromlist=["WorkspaceClient"]).WorkspaceClient()
                 .registered_models.list(catalog_name=catalog, schema_name=schema)}
except Exception:
    uc_models = {}
rows = []
for r in REGISTER:
    live = uc_models.get(r["model_key"])
    rows.append((r["model_key"], r["tier"], r["purpose"], r["owner"], r["training_data"],
                 r["validation"], r["monitoring"], r["use_constraint"],
                 f"{catalog}.{schema}.{r['model_key']}",
                 str(getattr(live, "created_at", "")) if live else "not yet registered"))
spark.createDataFrame(rows, "model_key string, tier string, purpose string, accountable_owner string, "
                            "training_data string, validation string, monitoring string, use_constraint string, "
                            "uc_name string, uc_created string") \
    .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.gov_model_register")
spark.sql(f"COMMENT ON TABLE {FQ}.gov_model_register IS 'SS1/23-shaped model risk register, joined live to the UC registry: tier, accountable owner, purpose, training data, validation, monitoring, use constraints.'")

# COMMAND ----------

# MAGIC %md ## AI activity log + sign-off trail tables (app writes)

# COMMAND ----------

spark.sql(f"""CREATE TABLE IF NOT EXISTS {FQ}.gld_ai_activity (
    group_id STRING, agent STRING, activity STRING, tools STRING, signal STRING,
    reasoning STRING, ts TIMESTAMP) USING DELTA
    COMMENT 'Every agent interaction: who was asked what, which tools it actually called, and what it said. Regulator-viewable.'""")

spark.sql(f"""CREATE TABLE IF NOT EXISTS {FQ}.gov_close_approvals (
    close_period STRING, workstream STRING, decision STRING, approver STRING, comment STRING,
    approved_at TIMESTAMP) USING DELTA
    COMMENT 'Close approvals per workstream: who approved what, when, with what comment.'""")

spark.sql(f"""CREATE TABLE IF NOT EXISTS {FQ}.gov_signoff_certificates (
    certificate_id STRING, close_period STRING, signed_by STRING, signed_at TIMESTAMP,
    evidence_json STRING, sha256 STRING, pdf_path STRING) USING DELTA
    COMMENT 'Sign-off certificates: the as-at evidence snapshot (table Delta versions, assumption versions, key figures) + SHA-256 + the PDF on the Volume. The auditor reproduces any number from here via time travel.'""")

# COMMAND ----------

# MAGIC %md ## Real lineage — system.access.table_lineage

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE VIEW {FQ}.gov_lineage_view
COMMENT 'REAL Unity Catalog lineage for this schema (system.access.table_lineage): which engine read what to produce what, captured by the platform — not drawn by hand.'
AS SELECT source_table_full_name, target_table_full_name, entity_type, entity_id,
          MAX(event_time) AS last_event
FROM system.access.table_lineage
WHERE target_table_full_name LIKE '{catalog}.{schema}.%'
  AND source_table_full_name IS NOT NULL
GROUP BY 1, 2, 3, 4
""")

print("07 complete — governance surfaces created")
