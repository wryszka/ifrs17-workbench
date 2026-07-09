# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Silver — conformed close inputs
# MAGIC
# MAGIC One conformed table per feed, IFRS 17 vocabulary, expectations tracked. These are the
# MAGIC exact inputs the measurement engines read — the actuarial↔finance handoff, governed.

# COMMAND ----------

import dlt
from pyspark.sql import functions as F

PROPS = {"quality": "silver", "layer": "silver", "demo": "ifrs17_workbench"}

# COMMAND ----------


@dlt.table(name="slv_policy", comment="Conformed policy book — grain: policy.", table_properties=PROPS)
@dlt.expect("known_cohort", "cohort_year IN (2024, 2025, 2026)")
def slv_policy():
    return (dlt.read("brz_policy_admin")
            .select("policy_id", "portfolio_id", "cohort_year", "inception_date", "expiry_date",
                    "total_premium", "annual_premium", "installments", "sum_insured", "acq_cost",
                    "region", "channel"))


@dlt.table(name="slv_claim", comment="Conformed claims — grain: claim, as at the reporting date.",
           table_properties=PROPS)
@dlt.expect("paid_not_exceeding_ultimate", "paid_to_date <= gross_ultimate * 1.001")
def slv_claim():
    return (dlt.read("brz_claims")
            .select(F.col("publicID").alias("claim_id"),
                    F.col("portfolio").alias("portfolio_id"),
                    F.col("policyNumber").alias("policy_id"),
                    F.col("lossDate").alias("loss_date"),
                    F.col("reportedDate").alias("reported_date"),
                    F.col("accidentQuarter").alias("accident_quarter"),
                    F.col("lossCause").alias("peril"),
                    F.col("catastrophe.code").alias("catastrophe_code"),
                    F.col("state").alias("status"),
                    F.col("grossPaidToDate").alias("paid_to_date"),
                    F.col("caseReserve").alias("case_reserve"),
                    (F.col("grossPaidToDate") + F.col("caseReserve")).alias("gross_ultimate"),
                    "region"))


@dlt.table(name="slv_claim_payments", comment="Conformed claim cash payments — grain: transaction.",
           table_properties=PROPS)
def slv_claim_payments():
    return (dlt.read("brz_claim_transactions")
            .select("transaction_id", "claim_id", "payment_date", "amount", "portfolio_id",
                    F.concat(F.year("payment_date").cast("string"), F.lit("Q"),
                             F.quarter("payment_date").cast("string")).alias("payment_quarter")))


@dlt.table(name="slv_cashflow_projection",
           comment="Conformed reserving projections — grain: run × scope × group × month × cf_type. "
                   "The engines PV these; the workbench never computes reserves itself.",
           table_properties=PROPS)
@dlt.expect("assumption_set_present", "assumption_set IS NOT NULL")
def slv_cashflow_projection():
    return (dlt.read("brz_actuarial_cashflows")
            .select("run_id", "as_of_date", "scope", "portfolio_id", "cohort_or_accident_year",
                    "group_ref", "projection_month", "cf_type", "amount", "assumption_set"))


@dlt.table(name="slv_gl_balance", comment="Conformed GL movements per period × account.", table_properties=PROPS)
def slv_gl_balance():
    return (dlt.read("brz_gl_trial_balance")
            .groupBy("period", "gl_account", "gl_account_name")
            .agg(F.round(F.sum("movement_eur"), 2).alias("movement_eur")))


@dlt.table(name="slv_manual_journal", comment="Manual journals — only APPROVED journals flow to recon.",
           table_properties=PROPS)
def slv_manual_journal():
    return dlt.read("brz_manual_journals").select(
        "journal_id", "period", "gl_account_dr", "gl_account_cr", "amount_eur",
        "narrative", "posted_by", "approved_by", "status")


@dlt.table(name="slv_treaty", comment="Conformed reinsurance held register.", table_properties=PROPS)
def slv_treaty():
    return dlt.read("brz_reinsurance_treaties").select(
        "treaty_id", "treaty_type", "portfolios", "cession_pct", "commission_pct",
        "inception", "expiry", "counterparty", "description")


@dlt.table(name="slv_fx", comment="Quarter-end FX fixes (EUR base).", table_properties=PROPS)
def slv_fx():
    return dlt.read("brz_fx_rates").select("rate_date", "pair", "rate")


@dlt.table(name="slv_expense", comment="Attributable/non-attributable expense per portfolio per quarter "
           "(cost-centre amounts × workbook allocation keys).", table_properties=PROPS)
def slv_expense():
    amounts = dlt.read("brz_expense_amounts")
    keys = dlt.read("brz_expense_keys")
    att = (amounts.filter("classification = 'attributable'")
           .join(keys, "cost_centre")
           .selectExpr("period", "cost_centre",
                       "stack(6, 'MOT', MOT, 'PROP', PROP, 'LIAB', LIAB, 'CLT', CLT, 'DEC', DEC, 'RO', RO) "
                       "as (portfolio_id, alloc_pct)", "amount_eur")
           .groupBy("period", "portfolio_id")
           .agg(F.round(F.sum(F.col("amount_eur") * F.col("alloc_pct")), 2).alias("attributable_expense")))
    non_att = (amounts.filter("classification = 'non_attributable'")
               .groupBy("period").agg(F.round(F.sum("amount_eur"), 2).alias("non_attributable_expense")))
    return att.join(non_att, "period", "left")
