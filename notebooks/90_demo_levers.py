# Databricks notebook source
# MAGIC %md
# MAGIC # 90 · Demo levers — inject / restore the drifted reserving file
# MAGIC
# MAGIC `action=inject`: copies the schema-drifted Q2 2026 reserving export from staging into the
# MAGIC landing area → the next close run quarantines it and the **close gate blocks Day 3**.
# MAGIC `action=restore`: removes the drifted file (the corrected v2 export is already landed) so
# MAGIC the close runs green again. Both are file operations on the Volume — exactly what a real
# MAGIC bad delivery and its fix look like.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "ifrs17_workbench")
dbutils.widgets.dropdown("action", "restore", ["inject", "restore"])
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
action = dbutils.widgets.get("action")
VOL = f"/Volumes/{catalog}/{schema}/ifrs17_files"

import os
import shutil

STAGED = f"{VOL}/staging/rsv_projection_2026Q2_v1_DRIFTED.csv"
LANDED = f"{VOL}/landing/actuarial_projections/rsv_projection_2026Q2_v1_DRIFTED.csv"

if action == "inject":
    shutil.copyfile(STAGED, LANDED)
    print("INJECTED: drifted reserving file landed — the next close run will quarantine it and block Day 3.")
elif action == "restore":
    if os.path.exists(LANDED):
        os.remove(LANDED)
        print("RESTORED: drifted file removed from landing.")
    else:
        print("Nothing to restore — landing is clean.")
    # Quarantine rows are DLT-owned (no DML): the close gate only blocks on quarantined files
    # STILL PRESENT in landing, so removing the file is the fix — rerun ifrs17_quarter_close.
    print("Rerun ifrs17_quarter_close to go green.")
