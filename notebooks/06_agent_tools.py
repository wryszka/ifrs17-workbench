# Databricks notebook source
# MAGIC %md
# MAGIC # 06 · Agent tools — UC functions (the deterministic crux)
# MAGIC
# MAGIC Every number an agent can cite comes from these governed SQL functions over the engine
# MAGIC tables. **LLMs narrate, SQL decides.** Bodies are provably one row (aggregate + struct);
# MAGIC rich COMMENTs because the supervisor routes off them (UC fns can't carry tags).
# MAGIC
# MAGIC GOTCHA (inherited): `CREATE OR REPLACE FUNCTION` revokes EXECUTE grants → this notebook
# MAGIC runs at BUILD time only; `99_reset` never recreates functions. Re-deploy the supervisor
# MAGIC (06b) if you rerun this.

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "ifrs17_workbench")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
FQ = f"{catalog}.{schema}"

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE FUNCTION {FQ}.fn_cohort_summary(p_group_id STRING)
RETURNS STRUCT<group_id STRING, portfolio_id STRING, cohort_year INT, bucket STRING, model STRING,
               gwp DOUBLE, policies INT, lrc_carrying DOUBLE, loss_component DOUBLE, csm DOUBLE,
               fcf_remaining_current DOUBLE, risk_adjustment DOUBLE, bs_lrc_total DOUBLE,
               onerous BOOLEAN, headroom DOUBLE, revenue_in_quarter DOUBLE, locked_in_curve STRING>
COMMENT 'IFRS 17 group summary at the current close (from gld_cohort_360): measurement model, §16 bucket, LRC/LC/CSM/RA balances, onerous flag + §57 headroom, revenue. THE first tool to call for any group question. Group ids look like PROP-2026-REM, CLT-2025-NSP, RO-2020-LIC.'
RETURN SELECT struct(any_value(group_id), any_value(portfolio_id), any_value(cohort_year),
                     any_value(profitability_bucket), any_value(measurement_model), any_value(gwp),
                     any_value(policies), any_value(lrc_carrying), any_value(loss_component),
                     any_value(csm), any_value(fcf_remaining_current), any_value(risk_adjustment),
                     any_value(bs_lrc_total), any_value(onerous), any_value(headroom),
                     any_value(revenue_in_quarter), any_value(locked_in_curve_date))
       FROM {FQ}.gld_cohort_360 WHERE group_id = p_group_id
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {FQ}.fn_csm_rollforward(p_group_id STRING, p_period STRING)
RETURNS STRUCT<group_id STRING, close_period STRING, steps MAP<STRING, DOUBLE>>
COMMENT 'The B96-ordered CSM waterfall for a GMM group in a period (e.g. 2026Q2): opening, new_business, interest_accretion (locked-in), experience_adjustments, fcf_changes_future_service (the unlock), fx, csm_release, closing. Only GMM groups (CLT-*, DEC-*) have a CSM — PAA groups NEVER do.'
RETURN SELECT struct(any_value(group_id), any_value(close_period),
                     map_from_entries(collect_list(struct(step, amount))))
       FROM {FQ}.gld_csm_rollforward WHERE group_id = p_group_id AND close_period = p_period
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {FQ}.fn_onerous_test(p_group_id STRING, p_period STRING)
RETURNS STRUCT<group_id STRING, close_period STRING, lrc_carrying DOUBLE, fcf_remaining_current DOUBLE,
               headroom DOUBLE, onerous BOOLEAN, loss_component_closing DOUBLE, trigger STRING>
COMMENT 'The §57 facts-and-circumstances onerous test for a PAA group in a period: GMM-style FCF for remaining coverage (current rates + RA) vs LRC carrying amount; loss component = the excess. Explains WHY a property cohort went onerous at 2026Q2 (flood_freq_property v2).'
RETURN SELECT struct(any_value(o.group_id), any_value(o.close_period), any_value(o.lrc_carrying),
                     any_value(o.fcf_remaining_current), any_value(o.headroom), any_value(o.onerous),
                     any_value(lc.amount), any_value(o.trigger))
       FROM {FQ}.gld_onerous_test o
       LEFT JOIN {FQ}.gld_loss_component lc
         ON lc.group_id = o.group_id AND lc.close_period = o.close_period AND lc.step = 'closing'
       WHERE o.group_id = p_group_id AND o.close_period = p_period
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {FQ}.fn_ave_analysis(p_portfolio STRING, p_period STRING)
RETURNS STRUCT<portfolio_id STRING, close_period STRING, expected_claims_prior_view DOUBLE,
               incurred_measured DOUBLE, experience_variance DOUBLE, past_service_changes DOUBLE,
               assumption_changes_future_service DOUBLE, unwind_ifie DOUBLE, rate_change_ifie DOUBLE,
               note STRING>
COMMENT 'Deterministic analysis of change for a portfolio (MOT/PROP/LIAB/CLT/DEC/RO) in a period: experience variance (actual vs prior reserving expectation) SPLIT from future-service assumption changes SPLIT from unwind and rate effects. The auditor conversation, computed. The June 2026 flood shows as experience on PROP; the assumption response shows as future-service.'
RETURN SELECT struct(any_value(portfolio_id), any_value(close_period), any_value(expected_claims_prior_view),
                     any_value(incurred_measured), any_value(experience_variance),
                     any_value(past_service_changes), any_value(assumption_changes_future_service),
                     any_value(unwind_ifie), any_value(rate_change_ifie), any_value(note))
       FROM {FQ}.gld_ave_analysis WHERE portfolio_id = p_portfolio AND close_period = p_period
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {FQ}.fn_discount_impact(p_group_id STRING, p_period STRING)
RETURNS STRUCT<group_id STRING, close_period STRING, fcf_locked_in DOUBLE, fcf_current DOUBLE,
               oci_balance DOUBLE, oci_in_period DOUBLE>
COMMENT 'GMM OCI disaggregation for a group in a period: FCF at locked-in vs current rates; the difference sits in OCI. Key expert point: the CSM barely moves when current rates move — accretion is locked-in.'
RETURN SELECT struct(any_value(group_id), any_value(close_period), any_value(fcf_locked_in),
                     any_value(fcf_current), any_value(oci_balance), any_value(oci_in_period))
       FROM {FQ}.gld_discount_impact WHERE group_id = p_group_id AND close_period = p_period
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {FQ}.fn_coverage_units(p_group_id STRING, p_period STRING)
RETURNS STRUCT<group_id STRING, close_period STRING, units_in_period DOUBLE, units_remaining DOUBLE,
               release_fraction DOUBLE, csm_release DOUBLE, basis STRING>
COMMENT 'Coverage units + CSM release mechanics for a GMM group: release = post-adjustment CSM x units provided / (provided + remaining). The basis (sum insured x coverage period) is a versioned assumption in gov_assumption_registry.'
RETURN SELECT struct(any_value(group_id), any_value(close_period), any_value(units_in_period),
                     any_value(units_remaining), any_value(release_fraction), any_value(csm_release),
                     any_value(basis))
       FROM {FQ}.gld_coverage_units WHERE group_id = p_group_id AND close_period = p_period
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {FQ}.fn_recon_check(p_period STRING)
RETURNS STRUCT<close_period STRING, items INT, tied INT, open_items INT,
               largest_residual DOUBLE, journal_applied STRING, detail STRING>
COMMENT 'Subledger vs GL trial-balance reconciliation status for a period: items tied, open residuals, the reclass journal applied. At 2026Q2 the one break (EUR 412,340 attributable claims-ops cost misbooked to other opex) is cleared by MJ-2026Q2-001.'
RETURN SELECT struct(any_value(close_period), CAST(COUNT(*) AS INT),
                     CAST(SUM(CASE WHEN status = 'tied' THEN 1 ELSE 0 END) AS INT),
                     CAST(SUM(CASE WHEN status != 'tied' THEN 1 ELSE 0 END) AS INT),
                     MAX(ABS(residual)), MAX(journal_id),
                     concat_ws('; ', collect_list(concat(recon_item, ': ', status))))
       FROM {FQ}.gld_trial_balance_recon WHERE close_period = p_period
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {FQ}.fn_close_status(p_period STRING)
RETURNS STRUCT<close_period STRING, workstreams ARRAY<STRUCT<working_day INT, workstream STRING,
               status STRING, detail STRING>>, blocked INT>
COMMENT 'The Day 1-10 close status board for a period: every workstream with its status (done / in_progress / blocked) and detail. Answers "where is the close?" and "what is blocking Day 3?".'
RETURN SELECT struct(any_value(close_period),
                     array_sort(collect_list(struct(working_day, workstream, status, detail))),
                     CAST(SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS INT))
       FROM {FQ}.gld_close_status WHERE close_period = p_period
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {FQ}.fn_lic_summary(p_portfolio STRING, p_period STRING)
RETURNS STRUCT<portfolio_id STRING, close_period STRING, lic_closing DOUBLE, lic_pv DOUBLE,
               lic_ra DOUBLE, claims_paid DOUBLE, incurred_current DOUBLE, past_service DOUBLE,
               ifie DOUBLE>
COMMENT 'LIC position for a portfolio in a period, discounted + RA: closing balance (PV + RA split), paid, incurred (current service), past-service changes, insurance finance expenses (unwind + rate). Works for the run-off book (RO) too — its whole story is LIC.'
RETURN SELECT struct(any_value(portfolio_id), any_value(close_period),
                     SUM(CASE WHEN step = 'closing' THEN amount ELSE 0 END),
                     SUM(CASE WHEN step = 'closing' THEN COALESCE(closing_pv, 0) ELSE 0 END),
                     SUM(CASE WHEN step = 'closing' THEN COALESCE(closing_ra, 0) ELSE 0 END),
                     SUM(CASE WHEN step = 'claims_paid' THEN -amount ELSE 0 END),
                     SUM(CASE WHEN step = 'incurred_current_service' THEN amount ELSE 0 END),
                     SUM(CASE WHEN step = 'past_service_changes' THEN amount ELSE 0 END),
                     SUM(CASE WHEN step IN ('unwind_ifie', 'rate_change_ifie') THEN amount ELSE 0 END))
       FROM {FQ}.gld_lic_rollforward WHERE portfolio_id = p_portfolio AND close_period = p_period
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {FQ}.fn_ri_held(p_period STRING)
RETURNS STRUCT<close_period STRING, components MAP<STRING, DOUBLE>, note STRING>
COMMENT 'Reinsurance held position for a period: ceded premium, commission, recoverable on LIC, recoveries on paid, and the LOSS-RECOVERY component (offsets the gross loss component on covered onerous property groups — reinsurance held is never onerous). The cat XL is data-only: the June 2026 floods sit below its attachment.'
RETURN SELECT struct(any_value(close_period),
                     map_from_entries(collect_list(struct(component, amount))),
                     'QS 30% on property; simplified held measurement, disclosed')
       FROM {FQ}.gld_ri_held WHERE close_period = p_period
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {FQ}.fn_assumption_history(p_assumption STRING)
RETURNS STRUCT<assumption_id STRING, versions ARRAY<STRUCT<version INT, value_json STRING,
               effective_from_run STRING, source STRING, approved_by STRING, approved_at DATE,
               status STRING>>>
COMMENT 'Version history of a governed assumption (e.g. flood_freq_property, casualty_inflation_clt, illiquidity_premium): every version with value, source study, approver and approval date. The drill target for "why did this number move".'
RETURN SELECT struct(any_value(assumption_id),
                     array_sort(collect_list(struct(version, value_json, effective_from_run, source,
                                                    approved_by, approved_at, status))))
       FROM {FQ}.gov_assumption_registry WHERE assumption_id = p_assumption
""")

TEST_ARGS = {
    "fn_cohort_summary": "'PROP-2026-REM'",
    "fn_csm_rollforward": "'CLT-2025-NSP', '2026Q2'",
    "fn_onerous_test": "'PROP-2026-REM', '2026Q2'",
    "fn_ave_analysis": "'PROP', '2026Q2'",
    "fn_discount_impact": "'CLT-2025-NSP', '2026Q2'",
    "fn_coverage_units": "'CLT-2025-NSP', '2026Q2'",
    "fn_recon_check": "'2026Q2'",
    "fn_close_status": "'2026Q2'",
    "fn_lic_summary": "'PROP', '2026Q2'",
    "fn_ri_held": "'2026Q2'",
    "fn_assumption_history": "'flood_freq_property'",
}
for f, args in TEST_ARGS.items():
    r = spark.sql(f"SELECT to_json({FQ}.{f}({args})) r").collect()
    assert r and r[0]["r"], f"{f} returned nothing"
    print(f"{f} ✓  {r[0]['r'][:110]}")
print("06 complete — 11 UC function tools")
