# Databricks notebook source
# MAGIC %md
# MAGIC # engine_common — shared helpers for the measurement engines (%run target)
# MAGIC
# MAGIC Determinism rules (hard, see CLAUDE.md): all engine math in driver-side pandas, `round(2)`,
# MAGIC deterministic sort before every write. Engines read landed/silver data + the assumption
# MAGIC registry ONLY — never `gen_*` truth. Every engine run logs to `gov_run_audit` with the
# MAGIC Delta versions of its inputs — the audit trail is a join, not a project.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "ifrs17_workbench")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
FQ = f"{catalog}.{schema}"
VOL = f"/Volumes/{catalog}/{schema}/ifrs17_files"

import datetime
import json
import uuid

import pandas as pd

REPORTING_DATE = datetime.date(2026, 6, 30)
CLOSE_PERIOD = "2026Q2"
QUARTERS = [(2024, 1), (2024, 2), (2024, 3), (2024, 4), (2025, 1), (2025, 2), (2025, 3), (2025, 4), (2026, 1), (2026, 2)]
QL = [f"{y}Q{q}" for y, q in QUARTERS]

# Curve basis mapping — locked-in per cohort inception year-end; "current" per close period is
# the nearest bundled REAL EIOPA publication (disclosed simplification for historical quarters).
LOCKED_IN_CURVE = {2024: "2023-12-31", 2025: "2024-12-31", 2026: "2025-12-31"}
CURRENT_CURVE = {"2024Q1": "2023-12-31", "2024Q2": "2023-12-31", "2024Q3": "2023-12-31", "2024Q4": "2023-12-31",
                 "2025Q1": "2024-12-31", "2025Q2": "2024-12-31", "2025Q3": "2024-12-31", "2025Q4": "2024-12-31",
                 "2026Q1": "2026-03-31", "2026Q2": "2026-06-30"}

Z75 = 0.6744897501960817  # 75th percentile standard normal


def q_end(lbl):
    y, q = int(lbl[:4]), int(lbl[-1])
    nx = datetime.date(y + (1 if q == 4 else 0), 1 if q == 4 else 3 * q + 1, 1)
    return nx - datetime.timedelta(days=1)


def q_index(lbl):
    return QL.index(lbl)


def months_between(d_from, d_to):
    """Whole months from d_from (exclusive month) to d_to's month."""
    return (d_to.year - d_from.year) * 12 + (d_to.month - d_from.month)


def pdf(sql_text):
    return spark.sql(sql_text).toPandas()


def write_engine(df, name, ddl, comment=""):
    """Deterministic write: round floats to 2dp, sort by all columns, overwrite."""
    cols = [c.strip().split(" ")[0] for c in ddl.split(",")]
    df = df.copy()[cols]
    for c in df.columns:
        if df[c].dtype == "float64":
            df[c] = df[c].round(2)
    df = df.sort_values(by=list(df.columns), kind="mergesort").reset_index(drop=True)
    sdf = spark.createDataFrame(df, ddl)
    sdf.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.{name}")
    spark.sql(f"ALTER TABLE {FQ}.{name} SET TBLPROPERTIES ('layer'='gold_engine', 'demo'='ifrs17_workbench')")
    if comment:
        spark.sql(f"COMMENT ON TABLE {FQ}.{name} IS '{comment}'")
    print(f"  {name}: {len(df)} rows")
    return cols


def ra_factor(cov):
    """Confidence-level RA at CL 75%: lognormal quantile uplift on PV outflows."""
    import math
    sigma2 = math.log(1 + cov * cov)
    sigma = math.sqrt(sigma2)
    return math.exp(Z75 * sigma - sigma2 / 2) - 1


def load_assumptions():
    """Active + versioned assumptions as {assumption_id: {version: value_dict}}."""
    a = pdf(f"SELECT assumption_id, version, value_json FROM {FQ}.gov_assumption_registry")
    out = {}
    for _, r in a.iterrows():
        out.setdefault(r["assumption_id"], {})[int(r["version"])] = json.loads(r["value_json"])
    return out


def load_curves():
    """{(curve_date_str, portfolio_id): pd.Series month → discount factor}."""
    c = pdf(f"SELECT CAST(curve_date AS STRING) curve_date, portfolio_id, maturity_month, discount_factor "
            f"FROM {FQ}.ref_discount_curve")
    out = {}
    for (cd, port), g in c.groupby(["curve_date", "portfolio_id"]):
        out[(cd, port)] = g.set_index("maturity_month")["discount_factor"]
    return out


def pv(cashflows, curve, asof):
    """PV of [(date, amount)] at asof using a monthly discount-factor Series (month 0 → 1.0)."""
    total = 0.0
    for d, amt in cashflows:
        m = months_between(asof, d)
        if m <= 0:
            total += amt
        else:
            total += amt * float(curve.get(min(m, int(curve.index.max())), curve.iloc[-1]))
    return round(total, 2)


# ---------- close status ----------

def ensure_close_status():
    spark.sql(f"""CREATE TABLE IF NOT EXISTS {FQ}.gld_close_status (
        close_period STRING, working_day INT, workstream STRING, status STRING,
        detail STRING, updated_by STRING, updated_at TIMESTAMP) USING DELTA""")


def set_status(working_day, workstream, status, detail, run_name):
    ensure_close_status()
    d = detail.replace("'", "''")
    spark.sql(f"""MERGE INTO {FQ}.gld_close_status t
        USING (SELECT '{CLOSE_PERIOD}' cp, {working_day} wd, '{workstream}' ws) s
        ON t.close_period = s.cp AND t.working_day = s.wd AND t.workstream = s.ws
        WHEN MATCHED THEN UPDATE SET status='{status}', detail='{d}', updated_by='{run_name}', updated_at=current_timestamp()
        WHEN NOT MATCHED THEN INSERT (close_period, working_day, workstream, status, detail, updated_by, updated_at)
        VALUES ('{CLOSE_PERIOD}', {working_day}, '{workstream}', '{status}', '{d}', '{run_name}', current_timestamp())""")


# ---------- run audit: the join, not the project ----------

def ensure_run_audit():
    spark.sql(f"""CREATE TABLE IF NOT EXISTS {FQ}.gov_run_audit (
        run_id STRING, engine STRING, close_period STRING, started_at TIMESTAMP, finished_at TIMESTAMP,
        status STRING, input_versions STRING, assumption_versions STRING, output_tables STRING,
        curve_dates STRING, note STRING) USING DELTA""")


def table_version(name):
    try:
        h = spark.sql(f"DESCRIBE HISTORY {FQ}.{name} LIMIT 1").collect()
        return int(h[0]["version"]) if h else -1
    except Exception:
        return -1


def log_run(engine, inputs, assumption_versions, outputs, curve_dates=None, status="success", note=""):
    ensure_run_audit()
    run_id = f"{engine}_{CLOSE_PERIOD}_{uuid.uuid4().hex[:8]}"
    iv = json.dumps({t: table_version(t) for t in inputs}, sort_keys=True).replace("'", "''")
    av = json.dumps(assumption_versions, sort_keys=True).replace("'", "''")
    ot = json.dumps(sorted(outputs)).replace("'", "''")
    cd = json.dumps(curve_dates or {}, sort_keys=True).replace("'", "''")
    nt = note.replace("'", "''")
    spark.sql(f"""INSERT INTO {FQ}.gov_run_audit VALUES (
        '{run_id}', '{engine}', '{CLOSE_PERIOD}', current_timestamp(), current_timestamp(),
        '{status}', '{iv}', '{av}', '{ot}', '{cd}', '{nt}')""")
    print(f"  gov_run_audit ← {run_id} (inputs pinned at Delta versions {iv[:120]}...)")
    return run_id


print(f"engine_common loaded — {FQ}, close {CLOSE_PERIOD}, reporting {REPORTING_DATE}")
