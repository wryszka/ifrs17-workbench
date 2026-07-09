# Databricks notebook source
# MAGIC %md
# MAGIC # 03b · DQ scorecard + ingestion source map
# MAGIC
# MAGIC Parses the pipeline **event log** (published to `medallion_event_log`) into
# MAGIC `gld_dq_scorecard` — one row per expectation with pass/fail counts — and builds
# MAGIC `gld_ingestion_sources`, the source map the app's Ingestion page renders (live sources
# MAGIC carry real row counts; roadmap rows are labelled honestly).

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "ifrs17_workbench")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
FQ = f"{catalog}.{schema}"

from pyspark.sql import functions as F

# COMMAND ----------

# MAGIC %md ## DQ scorecard from the DLT event log

# COMMAND ----------

ev = spark.table(f"{FQ}.medallion_event_log")
EXP_SCHEMA = "array<struct<name:string,dataset:string,passed_records:long,failed_records:long>>"
flow = (ev.filter("event_type = 'flow_progress'")
        .select(F.col("origin.flow_name").alias("dataset"),
                F.explode(F.from_json(F.expr("details:flow_progress:data_quality:expectations"), EXP_SCHEMA)).alias("e"),
                F.col("timestamp")))
DROP_RULES = ("valid_policy_id", "positive_premium", "valid_claim_id", "valid_txn", "cf_type_present",
              "projection_month_present", "valid_amount", "valid_account", "valid_journal", "valid_treaty")
score = (flow.groupBy(F.col("e.dataset").alias("dataset"), F.col("e.name").alias("expectation"))
         .agg(F.max("timestamp").alias("last_seen"),
              F.sum("e.passed_records").alias("passed"),
              F.sum("e.failed_records").alias("failed"))
         .withColumn("pass_pct", F.round(F.col("passed") / F.greatest(F.col("passed") + F.col("failed"), F.lit(1)) * 100, 2))
         .withColumn("action", F.when(F.col("expectation").isin(*DROP_RULES), "drop_to_quarantine")
                     .otherwise("track_and_retain")))
score.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.gld_dq_scorecard")
spark.sql(f"ALTER TABLE {FQ}.gld_dq_scorecard SET TBLPROPERTIES ('layer'='gold','demo'='ifrs17_workbench')")
display(spark.table(f"{FQ}.gld_dq_scorecard").orderBy("dataset", "expectation"))

# COMMAND ----------

# MAGIC %md ## Ingestion source map — every close feed, its system, and its production connector

# COMMAND ----------

def cnt(t):
    try:
        return spark.table(f"{FQ}.{t}").count()
    except Exception:
        return None

SRC_MAP = [
    # group, source, system, format, cadence, databricks_tool, table, status, note, production_connector
    ("Core systems", "Policy admin extracts", "Policy admin core", "CSV per cohort year", "daily in close",
     "Auto Loader → Lakeflow Declarative Pipelines", "brz_policy_admin", "live",
     "The written book — premiums, coverage dates, acquisition cash",
     "SIMULATED extracts. Production: CDC from the PAS (Guidewire PolicyCenter CDA / SAP FS-PM) via Lakeflow Connect; same bronze contract from there."),
    ("Core systems", "Claims snapshot", "Guidewire ClaimCenter-shaped", "JSON lines", "daily in close",
     "Auto Loader (JSON)", "brz_claims", "live",
     "Open/closed claims, paid + case as at the reporting date",
     "SIMULATED snapshot. Production: Guidewire CDA → Delta (see the sibling Claims Workbench for the full landing)."),
    ("Core systems", "Claim transactions", "Claims finance feed", "CSV per year", "daily in close",
     "Auto Loader (CSV)", "brz_claim_transactions", "live",
     "Cash grain — feeds paid triangles and LIC actuals",
     "SIMULATED. Production: claims finance subledger extract or CDA transaction stream."),
    ("Actuarial", "Reserving projections (LIC + LRC)", "Reserving system (ResQ/Igloo-style export)", "CSV per run", "per close run",
     "Auto Loader + schema-drift quarantine", "brz_actuarial_cashflows", "live",
     "THE actuarial→finance handoff. Drifted exports quarantine and block the close gate",
     "REAL file ingestion incl. drift handling. Production: reserving system export lands on the same Volume — the workbench consumes projections, it never re-computes reserves."),
    ("Actuarial", "Assumption registry", "Workbench-governed", "Delta", "on approval",
     "Governed UC table", "gov_assumption_registry", "live",
     "Versioned, approved assumptions — every engine run records the versions used",
     "REAL governed table. Production: same, with your approval workflow in front (or Excel round-trip via DATABRICKS.SQL)."),
    ("Finance", "GL trial balance", "SAP-shaped", "CSV per period", "Day 1",
     "Auto Loader (CSV)", "brz_gl_trial_balance", "live",
     "Movement per account — the recon target",
     "SIMULATED TB. Production: SAP extract via Lakeflow Connect (JDBC/BW) or file drop."),
    ("Finance", "Manual journals", "Financial control", "CSV feed + in-app posting", "in close",
     "Auto Loader (CSV)", "brz_manual_journals", "live",
     "Approved journals only flow to recon — the Q2 reclass fixes the one deliberate break",
     "REAL feed + app-posted files to the same folder. Production: journal workflow export."),
    ("Finance", "Expense allocation workbook", "Finance planning (Excel)", "XLSX + parsed CSV", "quarterly",
     "Volume drop + parsed feed", "brz_expense_amounts", "live",
     "Cost centre → portfolio allocation keys; attributable vs non-attributable split",
     "REAL xlsx on the Volume with its parsed CSV alongside. Production: connect the workbook (DATABRICKS.SQL round-trip) — don't replace it."),
    ("Reinsurance", "Ceded treaty register", "Ceded-re system", "CSV", "quarterly",
     "Auto Loader (CSV)", "brz_reinsurance_treaties", "live",
     "The property quota share drives the loss-recovery component",
     "SIMULATED register. Production: ceded-re admin system extract."),
    ("Market data", "EIOPA risk-free curves", "EIOPA (REAL publications)", "XLSX (official)", "monthly",
     "Volume drop + engine parse", "ref_rfr_curve", "live",
     "Real RFR_spot_no_VA curves incl. the 2026-06-30 reporting-date publication — see data/eiopa/PROVENANCE.md",
     "REAL EIOPA files, unmodified. Production: the same monthly download lands on the Volume."),
    ("Market data", "FX rates", "Market data feed", "CSV", "quarter-end",
     "Auto Loader (CSV)", "brz_fx_rates", "live",
     "EUR base fixes (book is EUR; multi-currency is roadmap)",
     "SIMULATED fixes. Production: Bloomberg/Refinitiv feed via Lakeflow Connect."),
    ("Roadmap", "IFRS 9 investment result", "Asset platform", "Delta", "quarterly", "—", None, "roadmap",
     "Complete the P&L below the insurance service result"),
    ("Roadmap", "Group consolidation submission", "Group consolidation (Tagetik/HFM-style)", "XBRL/CSV", "Day 10", "—", None, "roadmap",
     "Push the signed close pack downstream"),
    ("Roadmap", "Solvency II TP engine feed", "Capital reporting", "Delta", "quarterly", "—", None, "roadmap",
     "Same FCF dataset, second regime — see the Solvency II workbench"),
]
rows = []
for item in SRC_MAP:
    g, src, sys_, fmt, cad, tool, tbl, st, note = item[:9]
    connector = item[9] if len(item) > 9 else "Roadmap — connector named per source when built."
    rows.append((g, src, sys_, fmt, cad, tool, tbl, st, note, connector,
                 cnt(tbl) if st == "live" and tbl else None))
df = spark.createDataFrame(rows, "source_group string, source string, system string, format string, cadence string, "
                                 "databricks_tool string, table_name string, status string, note string, "
                                 "production_connector string, row_count long")
df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.gld_ingestion_sources")
spark.sql(f"ALTER TABLE {FQ}.gld_ingestion_sources SET TBLPROPERTIES ('layer'='gold','demo'='ifrs17_workbench')")
live_rows = df.filter("status='live'").count()
assert spark.table(f"{FQ}.gld_dq_scorecard").count() >= 8, "expected ≥8 expectations in the scorecard"
print(f"03b complete — {live_rows} live sources mapped, scorecard written")
