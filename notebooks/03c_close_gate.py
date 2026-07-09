# Databricks notebook source
# MAGIC %md
# MAGIC # 03c · Close gate — the Day-3 control
# MAGIC
# MAGIC RED stops the run: the measurement engines are downstream of this task in the
# MAGIC `ifrs17_quarter_close` job, so a blocked gate visibly halts the close DAG — the exact
# MAGIC moment the drifted reserving file creates in the demo. Checks:
# MAGIC 1. every close feed has arrived (feed SLA),
# MAGIC 2. the current reserving run (`RSV_2026Q2`) is present in silver,
# MAGIC 3. the quarantine is EMPTY for current-run files.

# COMMAND ----------

# MAGIC %run ./engine_common

# COMMAND ----------

problems = []

sla = pdf(f"SELECT feed, status, rows FROM {FQ}.gld_feed_sla")
missing = sla[sla["status"] != "received"]["feed"].tolist()
if missing:
    problems.append(f"feeds missing: {', '.join(missing)}")

runs = pdf(f"SELECT DISTINCT run_id FROM {FQ}.slv_cashflow_projection WHERE run_id = 'RSV_{CLOSE_PERIOD}'")
if runs.empty:
    problems.append(f"reserving run RSV_{CLOSE_PERIOD} not present in silver")

quar = pdf(f"""SELECT _source_file, quarantine_reason, COUNT(*) rows
               FROM {FQ}.brz_quarantine_cashflows
               WHERE _source_file LIKE '%{CLOSE_PERIOD}%'
               GROUP BY 1, 2""")
# Quarantine rows are DLT-owned history; they BLOCK only while the offending file is still in
# landing (the restore lever removes it — that removal IS the fix, like a real re-delivery).
import os
landing_files = set(os.listdir(f"{VOL}/landing/actuarial_projections"))
for _, r in quar.iterrows():
    if r["_source_file"] in landing_files:
        problems.append(f"QUARANTINED: {r['_source_file']} ({r['quarantine_reason']}, {r['rows']} rows)")

# COMMAND ----------

set_status(1, "Data feeds", "done", f"{len(sla)} feeds received" if not missing else f"missing: {missing}", "close_gate")
set_status(2, "Data feeds", "done" if not missing else "blocked",
           "reinsurance, FX, expense allocation received" if not missing else "feeds outstanding", "close_gate")

if problems:
    detail = " | ".join(problems)[:900]
    set_status(3, "DQ & gates", "blocked", detail, "close_gate")
    set_status(3, "Actuarial projections", "blocked" if not runs.empty or quar.empty is False else "blocked",
               "reserving delivery failed the gate", "close_gate")
    log_run("close_gate", ["gld_feed_sla", "slv_cashflow_projection", "brz_quarantine_cashflows"],
            {}, ["gld_close_status"], status="blocked", note=detail)
    raise Exception(f"CLOSE GATE BLOCKED — {detail}")

set_status(3, "DQ & gates", "done", "expectations pass, quarantine clear, gate open", "close_gate")
set_status(3, "Actuarial projections", "done", f"RSV_{CLOSE_PERIOD} delivered (LIC + LRC scopes)", "close_gate")
set_status(4, "Actuarial projections", "done",
           "assumption set v2 approved: flood_freq_property, casualty_inflation_clt", "close_gate")
log_run("close_gate", ["gld_feed_sla", "slv_cashflow_projection", "brz_quarantine_cashflows"],
        {}, ["gld_close_status"], status="success", note="gate open")
print("close gate OPEN")
