# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Gold marts (declarative) — triangles, feed SLA, book summary
# MAGIC
# MAGIC Pure transforms of silver stay in the pipeline. The measurement engines (04*) are batch
# MAGIC notebooks and write a DISJOINT table set — sequential roll-forward math never lives in DLT.

# COMMAND ----------

import dlt
from pyspark.sql import functions as F

PROPS = {"quality": "gold", "layer": "gold", "demo": "ifrs17_workbench"}

# COMMAND ----------


@dlt.table(name="gld_claims_triangles",
           comment="Paid development triangles — portfolio × accident quarter × development quarter.",
           table_properties=PROPS)
def gld_claims_triangles():
    pays = dlt.read("slv_claim_payments").alias("p")
    clm = dlt.read("slv_claim").select("claim_id", "accident_quarter").alias("c")
    j = pays.join(clm, "claim_id")
    ay = F.substring("accident_quarter", 1, 4).cast("int")
    aq = F.substring("accident_quarter", 6, 1).cast("int")
    dev = (F.year("payment_date") - ay) * 4 + (F.quarter("payment_date") - aq)
    return (j.groupBy("portfolio_id", "accident_quarter", dev.alias("dev_quarter"))
            .agg(F.round(F.sum("amount"), 2).alias("paid_in_quarter"),
                 F.countDistinct("claim_id").alias("claims_paying")))


@dlt.table(name="gld_feed_sla",
           comment="Feed arrival board — every close feed: files, rows, latest arrival. The Close "
                   "Cockpit reads this against the Day 1-10 calendar.",
           table_properties=PROPS)
def gld_feed_sla():
    feeds = [
        ("policy_admin", "brz_policy_admin", "Policy admin core"),
        ("claims_snapshot", "brz_claims", "Guidewire ClaimCenter"),
        ("claim_transactions", "brz_claim_transactions", "Claims finance"),
        ("actuarial_projections", "brz_actuarial_cashflows", "Reserving system"),
        ("gl_trial_balance", "brz_gl_trial_balance", "SAP GL"),
        ("manual_journals", "brz_manual_journals", "Financial control"),
        ("reinsurance", "brz_reinsurance_treaties", "Ceded re register"),
        ("fx_rates", "brz_fx_rates", "Market data"),
        ("expense_allocation", "brz_expense_amounts", "Finance planning workbook"),
    ]
    dfs = []
    for feed, tbl, system in feeds:
        dfs.append(dlt.read(tbl).agg(
            F.lit(feed).alias("feed"), F.lit(system).alias("source_system"),
            F.countDistinct("_source_file").alias("files"),
            F.count(F.lit(1)).alias("rows"),
            F.max("_bronze_ingested_at").alias("last_arrival")))
    out = dfs[0]
    for d in dfs[1:]:
        out = out.unionByName(d)
    return out.withColumn("status", F.when(F.col("rows") > 0, F.lit("received")).otherwise(F.lit("missing")))


@dlt.table(name="gld_book_summary",
           comment="Written book summary — portfolio × cohort: policies, GWP, sum insured, acquisition cash.",
           table_properties=PROPS)
def gld_book_summary():
    return (dlt.read("slv_policy")
            .groupBy("portfolio_id", "cohort_year")
            .agg(F.count(F.lit(1)).alias("policies"),
                 F.round(F.sum("total_premium"), 2).alias("gwp"),
                 F.round(F.sum("sum_insured"), 0).alias("sum_insured"),
                 F.round(F.sum("acq_cost"), 2).alias("acquisition_cash"),
                 F.min("inception_date").alias("first_inception"),
                 F.max("expiry_date").alias("last_expiry")))


@dlt.table(name="gld_quarantine_summary",
           comment="Open quarantine counts per feed — the close gate blocks while the current run has rows here.",
           table_properties=PROPS)
def gld_quarantine_summary():
    q = dlt.read("brz_quarantine_cashflows")
    return (q.groupBy("_source_file", "quarantine_reason")
            .agg(F.count(F.lit(1)).alias("rows"), F.max("_quarantined_at").alias("quarantined_at")))
