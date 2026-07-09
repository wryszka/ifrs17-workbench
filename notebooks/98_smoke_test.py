# Databricks notebook source
# MAGIC %md
# MAGIC # 98 · Smoke test — the recon identities ARE the QA spec
# MAGIC
# MAGIC One row per step, PASS/FAIL, fails loudly at the end. Covers: the 8 reconciliation
# MAGIC identities an insurance CFO checks with a calculator, the sacred hero invariants,
# MAGIC realism bounds, and the installed-assets checklist (so it doubles as the fresh-deployment
# MAGIC verification). **All identities must PASS before anyone demos.**

# COMMAND ----------

# MAGIC %run ./engine_common

# COMMAND ----------

RESULTS = []
TOL = 0.05


def step(name, ok, detail=""):
    RESULTS.append((len(RESULTS) + 1, name, "PASS" if ok else "FAIL", str(detail)[:400]))
    print(("✅" if ok else "❌"), name, "—", detail)
    return ok


def one(sql_text):
    r = pdf(sql_text)
    return r.iloc[0] if len(r) else None

# COMMAND ----------

# MAGIC %md ## Feeds, gate, book

# COMMAND ----------

r = one(f"SELECT COUNT(*) n FROM {FQ}.gld_feed_sla WHERE status='received'")
step("9 close feeds received", int(r["n"]) == 9, f"{r['n']} feeds")

import os
drifted = [f for f in os.listdir(f"{VOL}/landing/actuarial_projections") if "DRIFTED" in f]
step("no drifted file in landing (default state = green close)", not drifted, drifted or "clean")

r = one(f"SELECT ROUND(SUM(gwp),2) g, SUM(policies) p FROM {FQ}.gld_book_summary")
step("book GWP ties to design (EUR 371.3m)", abs(float(r["g"]) - 371_300_000.0) < 1.0, f"GWP {r['g']}, {r['p']} policies")

# COMMAND ----------

# MAGIC %md ## Identity 1-2 · PAA LRC + CSM roll-forwards foot; expired cohorts run to nil

# COMMAND ----------

r = one(f"""
  WITH t AS (SELECT group_id, close_period,
      SUM(CASE WHEN step='closing' THEN amount END) closing,
      SUM(CASE WHEN step NOT IN ('closing') THEN amount END) sum_of_moves
    FROM {FQ}.gld_lrc_paa_rollforward GROUP BY 1,2)
  SELECT MAX(ABS(closing - sum_of_moves)) worst FROM t""")
step("IDENTITY: PAA LRC roll foots (opening+moves=closing) every group×quarter",
     float(r["worst"]) < TOL, f"worst {r['worst']}")

r = one(f"""SELECT MAX(ABS(amount)) worst FROM {FQ}.gld_lrc_paa_rollforward
            WHERE step='closing' AND close_period='{CLOSE_PERIOD}'
              AND group_id IN ('MOT-2024-REM','PROP-2024-REM','LIAB-2024-REM')""")
step("expired 2024 annual cohorts have run LRC to ~nil", float(r["worst"]) < 1.0, f"worst {r['worst']}")

r = one(f"""
  WITH t AS (SELECT group_id, close_period,
      SUM(CASE WHEN step='closing' THEN amount END) closing,
      SUM(CASE WHEN step != 'closing' THEN amount END) moves
    FROM {FQ}.gld_csm_rollforward GROUP BY 1,2)
  SELECT MAX(ABS(closing - moves)) worst FROM t""")
step("IDENTITY: CSM roll foots (B96 steps sum to closing)", float(r["worst"]) < TOL, f"worst {r['worst']}")

r = one(f"SELECT COUNT(*) n FROM {FQ}.gld_csm_rollforward WHERE group_id LIKE 'MOT%' OR group_id LIKE 'PROP%' OR group_id LIKE 'LIAB%' OR group_id LIKE 'RO%'")
step("NO CSM on PAA or run-off groups — ever", int(r["n"]) == 0, f"{r['n']} rows")

# COMMAND ----------

# MAGIC %md ## Identity 3-4 · LIC + LC + RA roll-forwards foot

# COMMAND ----------

r = one(f"""
  WITH t AS (SELECT portfolio_id, accident_year, close_period,
      SUM(CASE WHEN step='closing' THEN amount END) closing,
      SUM(CASE WHEN step != 'closing' THEN amount END) moves
    FROM {FQ}.gld_lic_rollforward GROUP BY 1,2,3)
  SELECT MAX(ABS(closing - moves)) worst FROM t""")
step("IDENTITY: LIC roll foots (incurred/past-service/IFIE/paid → closing)",
     float(r["worst"]) < TOL, f"worst {r['worst']}")

r = one(f"""
  WITH t AS (SELECT group_id, close_period,
      SUM(CASE WHEN step='closing' THEN amount END) closing,
      SUM(CASE WHEN step != 'closing' THEN amount END) moves
    FROM {FQ}.gld_loss_component GROUP BY 1,2)
  SELECT MAX(ABS(closing - moves)) worst FROM t""")
step("IDENTITY: loss-component roll foots", float(r["worst"]) < TOL, f"worst {r['worst']}")

r = one(f"SELECT MAX(ABS(opening + net_change - closing)) worst FROM {FQ}.gld_ra_rollforward")
step("IDENTITY: RA roll-forward foots (CL-75 disclosed)", float(r["worst"]) < TOL, f"worst {r['worst']}")

# COMMAND ----------

# MAGIC %md ## Identity 5-6 · Postings balance; statements articulate

# COMMAND ----------

r = one(f"""SELECT MAX(ABS(dr - cr)) worst FROM (
    SELECT close_period, SUM(CASE WHEN dr_cr='DR' THEN amount END) dr,
           SUM(CASE WHEN dr_cr='CR' THEN amount END) cr
    FROM {FQ}.gld_subledger_postings GROUP BY 1)""")
step("IDENTITY: subledger balanced (ΣDr = ΣCr every period)", float(r["worst"]) < TOL, f"worst {r['worst']}")

r = one(f"""SELECT ABS(SUM(CASE WHEN side='asset' THEN amount ELSE -amount END)) diff
            FROM {FQ}.gld_balance_sheet WHERE close_period='{CLOSE_PERIOD}'""")
step("IDENTITY: balance sheet balances (assets = liabilities + equity)", float(r["diff"]) < TOL, f"diff {r['diff']}")

r = one(f"""
  WITH d AS (SELECT component, SUM(CASE WHEN line='Closing balance' THEN amount END) closing
             FROM {FQ}.gld_disclosure_lrc_lic WHERE close_period='{CLOSE_PERIOD}' GROUP BY 1),
       b AS (SELECT line_item, amount FROM {FQ}.gld_balance_sheet WHERE close_period='{CLOSE_PERIOD}')
  SELECT MAX(diff) worst FROM (
    SELECT ABS((SELECT closing FROM d WHERE component='lrc_excl_lc') - (SELECT amount FROM b WHERE line_item='LRC excluding loss component')) diff
    UNION ALL SELECT ABS((SELECT closing FROM d WHERE component='loss_component') - (SELECT amount FROM b WHERE line_item='Loss component of the LRC'))
    UNION ALL SELECT ABS((SELECT closing FROM d WHERE component='lic') - (SELECT amount FROM b WHERE line_item='Liability for incurred claims')))""")
step("IDENTITY: §101 disclosure closings = balance sheet, to the cent", float(r["worst"]) < TOL, f"worst {r['worst']}")

r = one(f"""
  SELECT ABS((SELECT SUM(amount) FROM {FQ}.gld_insurance_revenue WHERE close_period='{CLOSE_PERIOD}')
           - (SELECT amount FROM {FQ}.gld_pnl_statement WHERE close_period='{CLOSE_PERIOD}' AND line_item='Insurance revenue')) diff""")
step("IDENTITY: revenue decomposition sums to the §80 face", float(r["diff"]) < TOL, f"diff {r['diff']}")

# COMMAND ----------

# MAGIC %md ## Identity 7-8 · Recon tied post-journal; OCI articulates

# COMMAND ----------

r = one(f"""SELECT SUM(CASE WHEN status='tied' THEN 1 ELSE 0 END) tied, COUNT(*) items,
            MAX(ABS(raw_difference)) biggest_break
            FROM {FQ}.gld_trial_balance_recon WHERE close_period='{CLOSE_PERIOD}'""")
step("IDENTITY: GL recon fully tied after the reclass journal",
     int(r["tied"]) == int(r["items"]), f"{r['tied']}/{r['items']} tied; biggest raw break {r['biggest_break']}")
step("the deliberate break is the designed EUR 412,340.00",
     abs(float(r["biggest_break"]) - 412340.0) < 1.0, r["biggest_break"])

r = one(f"""
  SELECT ABS((SELECT SUM(oci_in_period) FROM {FQ}.gld_discount_impact)
           - (SELECT SUM(CASE WHEN dr_cr='DR' THEN amount ELSE -amount END)
              FROM {FQ}.gld_subledger_postings WHERE gl_account='3900')) diff""")
step("IDENTITY: OCI movements articulate (discount impact = OCI postings)", float(r["diff"]) < TOL, f"diff {r['diff']}")

# COMMAND ----------

# MAGIC %md ## Sacred heroes

# COMMAND ----------

r = one(f"""SELECT group_id, headroom FROM {FQ}.gld_onerous_test
            WHERE close_period='{CLOSE_PERIOD}' AND group_id='PROP-2026-REM'""")
step("HERO: PROP-2026-REM onerous at 2026Q2 (flood re-basis v2)",
     r is not None and float(r["headroom"]) < -1_000_000, f"headroom {r['headroom'] if r is not None else 'missing'}")

r = one(f"""SELECT headroom FROM {FQ}.gld_onerous_test
            WHERE close_period='{CLOSE_PERIOD}' AND group_id='PROP-2025-REM'""")
step("HERO: PROP-2025-REM onerous too (H2-weighted writings still on risk)",
     r is not None and float(r["headroom"]) < 0, f"headroom {r['headroom'] if r is not None else 'missing'}")

r = one(f"""SELECT SUM(CASE WHEN step='fcf_changes_future_service' THEN amount END) unlock,
                   SUM(CASE WHEN step='closing' THEN amount END) closing
            FROM {FQ}.gld_csm_rollforward WHERE group_id='CLT-2025-NSP' AND close_period='{CLOSE_PERIOD}'""")
step("HERO: CLT-2025 casualty-inflation unlock visible, CSM survives",
     float(r["unlock"]) < -700_000 and float(r["closing"]) > 1_000_000,
     f"unlock {r['unlock']}, closing {r['closing']}")

r = one(f"""SELECT MAX(ABS(amount)) worst FROM {FQ}.gld_csm_rollforward
            WHERE step='fcf_changes_future_service' AND group_id LIKE 'DEC%'""")
step("no phantom unlocks on DEC (no assumption changed there)", float(r["worst"]) < 100_000, f"worst {r['worst']}")

r = one(f"""SELECT SUM(CASE WHEN step='fcf_changes_future_service' THEN amount END) unlock,
                   SUM(CASE WHEN step='closing' THEN amount END) closing
            FROM {FQ}.gld_csm_rollforward WHERE group_id='CLT-2026-NSP' AND close_period='{CLOSE_PERIOD}'""")
step("HERO: CLT-2026 smaller unlock (partially re-priced cohort), CSM survives",
     -900_000 < float(r["unlock"]) < -150_000 and float(r["closing"]) > 800_000,
     f"unlock {r['unlock']}, closing {r['closing']}")

r = one(f"""SELECT amount FROM {FQ}.gld_ri_held
            WHERE close_period='{CLOSE_PERIOD}' AND component='loss_recovery_component'""")
step("HERO: loss-recovery component offsets the gross LC (30% QS)",
     r is not None and float(r["amount"]) > 300_000, f"{r['amount'] if r is not None else 'missing'}")

r = one(f"""SELECT COUNT(*) n FROM {FQ}.gld_onerous_test
            WHERE close_period='{CLOSE_PERIOD}' AND onerous AND group_id LIKE 'MOT%'""")
step("motor thin but NOT onerous", int(r["n"]) == 0, f"{r['n']} onerous motor groups")

# COMMAND ----------

# MAGIC %md ## Realism bounds (the numbers experts subconsciously scan)

# COMMAND ----------

r = one(f"""
  SELECT ROUND(SUM(CASE WHEN l.step IN ('incurred_current_service','past_service_changes') THEN l.amount END)
       / (SELECT -SUM(amount) FROM {FQ}.gld_lrc_paa_rollforward
          WHERE step='insurance_revenue' AND group_id LIKE 'MOT%') * 100, 1) lr
  FROM {FQ}.gld_lic_rollforward l WHERE l.portfolio_id='MOT'""")
step("motor loss ratio in a credible band (60-85%)", 60 <= float(r["lr"]) <= 85, f"LR {r['lr']}%")

r = one(f"""SELECT ROUND(SUM(CASE WHEN step='closing' THEN amount END)
            / (SELECT SUM(gwp) FROM {FQ}.gld_contract_groups WHERE measurement_model='GMM') * 100, 1) pct
            FROM {FQ}.gld_csm_rollforward WHERE close_period='{CLOSE_PERIOD}'""")
step("CSM modest relative to GMM GWP (5-30%)", 5 <= float(r["pct"]) <= 30, f"CSM/GWP {r['pct']}%")

r = one(f"SELECT COUNT(DISTINCT curve_date) n FROM {FQ}.ref_rfr_curve")
step("5 real EIOPA publications parsed incl. the 2026-06-30 reporting date",
     int(r["n"]) == 5 and one(f"SELECT COUNT(*) n FROM {FQ}.ref_rfr_curve WHERE curve_date='2026-06-30'")["n"] > 0,
     f"{r['n']} curve dates")

# COMMAND ----------

# MAGIC %md ## Installed assets (fresh-deployment checklist)

# COMMAND ----------

TABLES = ["ref_portfolio", "ref_close_calendar", "ref_coa_mapping", "ref_ra_params", "ref_rfr_curve",
          "ref_discount_curve", "slv_policy", "slv_claim", "slv_claim_payments", "slv_cashflow_projection",
          "slv_gl_balance", "slv_expense", "gld_contract_groups", "gld_fcf_summary", "gld_lrc_paa_rollforward",
          "gld_onerous_test", "gld_loss_component", "gld_lic_rollforward", "gld_csm_rollforward",
          "gld_coverage_units", "gld_discount_impact", "gld_ri_held", "gld_subledger_postings",
          "gld_trial_balance_recon", "gld_pnl_statement", "gld_balance_sheet", "gld_disclosure_lrc_lic",
          "gld_disclosure_by_component", "gld_ra_rollforward", "gld_ave_analysis", "gld_onerous_watch",
          "gld_sii_crosswalk", "gld_cohort_360", "gld_close_status", "gld_feed_sla", "gld_dq_scorecard",
          "gld_ingestion_sources", "gov_assumption_registry", "gov_run_audit"]
missing = [t for t in TABLES if not spark.catalog.tableExists(f"{FQ}.{t}")]
step(f"all {len(TABLES)} core tables installed", not missing, missing or "all present")

fns = {r_[0].split(".")[-1] for r_ in spark.sql(f"SHOW USER FUNCTIONS IN {FQ}").collect()}
WANT_FNS = {"fn_cohort_summary", "fn_csm_rollforward", "fn_onerous_test", "fn_ave_analysis",
            "fn_discount_impact", "fn_coverage_units", "fn_recon_check", "fn_close_status",
            "fn_lic_summary", "fn_ri_held", "fn_assumption_history"}
step("11 UC agent-tool functions installed", WANT_FNS <= fns, sorted(WANT_FNS - fns) or "all present")

try:
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    models = {m.name.split(".")[-1] for m in w.registered_models.list(catalog_name=catalog, schema_name=schema)}
    step("UC models registered (lic_emergence, ifrs17_agent, model_ifrs17_agent)",
         {"model_lic_emergence", "model_ifrs17_agent", "ifrs17_agent"} <= models, sorted(models))
    eps = [e.name for e in w.serving_endpoints.list()]
    role_eps = [e for e in eps if e.startswith("ifrs17-")]
    sup = [e for e in eps if e.startswith("agents_") and schema in e]
    step("4 role-agent endpoints + supervisor deployed", len(role_eps) >= 4 and len(sup) >= 1,
         f"roles {role_eps}, supervisor {sup}")
except Exception as e:  # noqa: BLE001
    step("serving endpoint checks", False, str(e)[:200])

r = one(f"SELECT COUNT(DISTINCT engine) n FROM {FQ}.gov_run_audit WHERE close_period='{CLOSE_PERIOD}'")
step("run audit populated by every engine", int(r["n"]) >= 5, f"{r['n']} engines logged")

# COMMAND ----------

df = spark.createDataFrame(RESULTS, "step int, check string, result string, detail string")
df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.smoke_results")
display(df)
fails = [x for x in RESULTS if x[2] == "FAIL"]
assert not fails, f"{len(fails)} SMOKE FAILURES: {[x[1] for x in fails]}"
print(f"ALL {len(RESULTS)} CHECKS PASS ✅ — safe to demo")
