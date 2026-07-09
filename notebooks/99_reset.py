# Databricks notebook source
# MAGIC %md
# MAGIC # 99 · Reset prep — clean state before the deterministic rebuild
# MAGIC
# MAGIC Removes any injected drifted file, clears the narration cache, resets app-posted journals.
# MAGIC The reset JOB then re-runs: truth → landing → medallion (full refresh) → quarter close.
# MAGIC **Never recreates UC functions** (EXECUTE grants) and **never retrains** the ML model.
# MAGIC Seed 42 → the heroes come back byte-identical.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "ifrs17_workbench")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
FQ = f"{catalog}.{schema}"
VOL = f"/Volumes/{catalog}/{schema}/ifrs17_files"

import glob
import os

# 1 · remove the drifted file if a demo left it in landing
for f in glob.glob(f"{VOL}/landing/actuarial_projections/*DRIFTED*"):
    os.remove(f)
    print("removed", f)

# 2 · remove app-posted journal files (the seeded reclass journal stays)
for f in glob.glob(f"{VOL}/landing/gl_trial_balance/manual_journals_app_*.csv"):
    os.remove(f)
    print("removed", f)

# 3 · clear the narration cache (refills on first ask / warm)
for t, mode in (("cache_agent_responses", "delete"),):
    try:
        spark.sql(f"DELETE FROM {FQ}.{t}")
        print(f"cleared {t}")
    except Exception as e:  # noqa: BLE001
        print(f"skip {t}: {e}")

# 4 · clear close approvals + certificates from previous demos (evidence PDFs stay on the Volume)
for t in ("gov_close_approvals",):
    try:
        spark.sql(f"DELETE FROM {FQ}.{t}")
        print(f"cleared {t}")
    except Exception as e:  # noqa: BLE001
        print(f"skip {t}: {e}")

print("reset prep done — the job now rebuilds truth → landing → medallion (full refresh) → close")
