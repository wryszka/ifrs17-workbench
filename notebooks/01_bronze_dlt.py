# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Bronze — governed front door (Lakeflow Declarative Pipeline)
# MAGIC
# MAGIC Every close feed lands through expectations via Auto Loader. Failures are never silently
# MAGIC lost: drop rules land the offending rows in a **quarantine mirror** with the reason.
# MAGIC The schema-drifted reserving file (demo lever) is caught here — `cf_type` missing and
# MAGIC the renamed columns rescued — and blocks the close gate downstream.

# COMMAND ----------

import dlt
from pyspark.sql import functions as F

CAT = spark.conf.get("source_catalog")
SCH = spark.conf.get("source_schema")
VOL = f"/Volumes/{CAT}/{SCH}/ifrs17_files"
LAND = f"{VOL}/landing"
CKPT = f"{VOL}/checkpoints"

PROPS = {"quality": "bronze", "layer": "bronze", "demo": "ifrs17_workbench"}


def _stream(fmt, path, schema_str, schema_dir, opts=None):
    r = (spark.readStream.format("cloudFiles")
         .option("cloudFiles.format", fmt)
         .option("cloudFiles.schemaLocation", f"{CKPT}/{schema_dir}")
         .option("rescuedDataColumn", "_rescued_data"))
    if fmt == "csv":
        r = r.option("header", "true")
    for k, v in (opts or {}).items():
        r = r.option(k, v)
    return (r.schema(schema_str + ", _rescued_data string").load(path)
            .withColumn("_source_file", F.col("_metadata.file_name"))
            .withColumn("_bronze_ingested_at", F.current_timestamp()))

# COMMAND ----------

# MAGIC %md ## Policy admin (CSV extracts per cohort year)

# COMMAND ----------

POLICY_SCHEMA = ("policy_id string, portfolio_id string, cohort_year int, inception_date date, "
                 "expiry_date date, total_premium double, annual_premium double, installments int, "
                 "sum_insured double, acq_cost double, region string, channel string")


@dlt.table(name="brz_policy_admin",
           comment="Policy admin core extracts — the written book. Invalid rows quarantine.",
           table_properties=PROPS)
@dlt.expect_or_drop("valid_policy_id", "policy_id IS NOT NULL")
@dlt.expect_or_drop("positive_premium", "total_premium > 0")
@dlt.expect("valid_dates", "expiry_date > inception_date")
@dlt.expect("known_portfolio", "portfolio_id IN ('MOT','PROP','LIAB','CLT','DEC','RO')")
def brz_policy_admin():
    return _stream("csv", f"{LAND}/policy_admin/*.csv", POLICY_SCHEMA, "policy_admin")

# COMMAND ----------

# MAGIC %md ## Claims snapshot (Guidewire-shaped JSON lines) + claim transactions

# COMMAND ----------

CLAIMS_SCHEMA = ("publicID string, claimNumber string, policyNumber string, portfolio string, "
                 "lossDate date, reportedDate date, accidentQuarter string, lossCause string, "
                 "catastrophe struct<code:string,name:string>, state string, grossPaidToDate double, "
                 "caseReserve double, region string, _extractedAt date")


@dlt.table(name="brz_claims",
           comment="Claims snapshot (ClaimCenter-shaped JSONL) as at the reporting date.",
           table_properties=PROPS)
@dlt.expect_or_drop("valid_claim_id", "publicID IS NOT NULL")
@dlt.expect("reported_after_loss", "reportedDate >= lossDate")
def brz_claims():
    return _stream("json", f"{LAND}/claims/*.jsonl", CLAIMS_SCHEMA, "claims")


TXN_SCHEMA = ("transaction_id string, claim_id string, payment_date date, transaction_type string, "
              "amount double, portfolio_id string")


@dlt.table(name="brz_claim_transactions",
           comment="Claims finance feed — cash transactions per claim.",
           table_properties=PROPS)
@dlt.expect_or_drop("valid_txn", "transaction_id IS NOT NULL AND amount IS NOT NULL")
@dlt.expect("positive_amount", "amount > 0")
def brz_claim_transactions():
    return _stream("csv", f"{LAND}/claim_transactions/*.csv", TXN_SCHEMA, "claim_transactions")

# COMMAND ----------

# MAGIC %md ## Actuarial projections (reserving-system export) — WITH quarantine mirror
# MAGIC The workbench consumes reserving output; it never computes reserves. A drifted export
# MAGIC (renamed columns, missing `cf_type`) fails the drop rules and quarantines — the Day-3
# MAGIC close-blocked hero.

# COMMAND ----------

PROJ_SCHEMA = ("run_id string, as_of_date date, scope string, portfolio_id string, "
               "cohort_or_accident_year int, group_ref string, projection_month date, "
               "cf_type string, amount double, assumption_set string")


def _proj_stream():
    return _stream("csv", f"{LAND}/actuarial_projections/*.csv", PROJ_SCHEMA, "actuarial_projections")


@dlt.table(name="brz_actuarial_cashflows",
           comment="Reserving-system cash-flow projections (LIC + LRC scopes, monthly). Drifted exports quarantine.",
           table_properties=PROPS)
@dlt.expect_or_drop("cf_type_present", "cf_type IN ('claims','expense','premium')")
@dlt.expect_or_drop("projection_month_present", "projection_month IS NOT NULL")
@dlt.expect_or_drop("valid_amount", "amount IS NOT NULL AND amount >= 0")
@dlt.expect("valid_scope", "scope IN ('LIC','LRC')")
def brz_actuarial_cashflows():
    return _proj_stream()


@dlt.table(name="brz_quarantine_cashflows",
           comment="Quarantine mirror: projection rows that failed a DROP rule (schema drift — renamed "
                   "columns land in _rescued_data). Nothing is silently lost; the close gate blocks while "
                   "this is non-empty for the current run.",
           table_properties=PROPS)
def brz_quarantine_cashflows():
    return (_proj_stream()
            .filter("cf_type IS NULL OR cf_type NOT IN ('claims','expense','premium') "
                    "OR projection_month IS NULL OR amount IS NULL")
            .withColumn("quarantine_reason", F.lit("schema_drift_or_missing_fields"))
            .withColumn("_quarantined_at", F.current_timestamp()))

# COMMAND ----------

# MAGIC %md ## GL trial balance, reinsurance register, FX, expense allocation

# COMMAND ----------

GL_SCHEMA = "period string, gl_account string, gl_account_name string, cost_centre string, movement_eur double"


@dlt.table(name="brz_gl_trial_balance", comment="SAP-shaped GL trial balance movements per period.",
           table_properties=PROPS)
@dlt.expect_or_drop("valid_account", "gl_account IS NOT NULL")
def brz_gl_trial_balance():
    return _stream("csv", f"{LAND}/gl_trial_balance/gl_trial_balance_*.csv", GL_SCHEMA, "gl_trial_balance")


JRN_SCHEMA = ("journal_id string, period string, gl_account_dr string, gl_account_cr string, "
              "amount_eur double, narrative string, posted_by string, approved_by string, status string")


@dlt.table(name="brz_manual_journals", comment="Manual journal feed (seed + app-posted files).",
           table_properties=PROPS)
@dlt.expect_or_drop("valid_journal", "journal_id IS NOT NULL AND amount_eur IS NOT NULL")
def brz_manual_journals():
    return _stream("csv", f"{LAND}/gl_trial_balance/manual_journals*.csv", JRN_SCHEMA, "manual_journals")


TREATY_SCHEMA = ("treaty_id string, treaty_type string, portfolios string, cession_pct double, "
                 "commission_pct double, inception date, expiry date, counterparty string, description string")


@dlt.table(name="brz_reinsurance_treaties", comment="Ceded reinsurance register.", table_properties=PROPS)
@dlt.expect_or_drop("valid_treaty", "treaty_id IS NOT NULL")
def brz_reinsurance_treaties():
    return _stream("csv", f"{LAND}/reinsurance/*.csv", TREATY_SCHEMA, "reinsurance")


@dlt.table(name="brz_fx_rates", comment="Market FX rates (quarter-end fixes).", table_properties=PROPS)
def brz_fx_rates():
    return _stream("csv", f"{LAND}/fx_rates/*.csv", "rate_date date, pair string, rate double", "fx_rates")


EXP_SCHEMA = "period string, cost_centre string, cost_centre_name string, amount_eur double, classification string"


@dlt.table(name="brz_expense_amounts", comment="Expense amounts per cost centre per quarter (from the finance-planning workbook).",
           table_properties=PROPS)
@dlt.expect_or_drop("valid_amount", "amount_eur IS NOT NULL")
def brz_expense_amounts():
    return _stream("csv", f"{LAND}/expense_allocation/expense_amounts*.csv", EXP_SCHEMA, "expense_amounts")


KEYS_SCHEMA = ("cost_centre string, cost_centre_name string, attributable string, MOT double, PROP double, "
               "LIAB double, CLT double, DEC double, RO double")


@dlt.table(name="brz_expense_keys", comment="Expense allocation keys (cost centre → portfolio), parsed from the workbook.",
           table_properties=PROPS)
def brz_expense_keys():
    return _stream("csv", f"{LAND}/expense_allocation/expense_allocation_keys*.csv", KEYS_SCHEMA, "expense_keys")
