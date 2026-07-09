# Databricks notebook source
# MAGIC %md
# MAGIC # 04c · PAA + LIC engine
# MAGIC
# MAGIC **PAA LRC** (Motor / Property / Liability — annual coverage, LRC undiscounted per §56):
# MAGIC roll-forward per group × quarter: opening + premiums received − insurance revenue (earned,
# MAGIC passage of time) − acquisition cash flows + acquisition amortisation = closing.
# MAGIC **No CSM on PAA cohorts — ever.**
# MAGIC
# MAGIC **§57 onerous test, run EVERY quarter** (continuous monitoring, not just at events): compares
# MAGIC the GMM-style FCF for remaining coverage (current rates + RA, from 04b) with the LRC carrying
# MAGIC amount → loss component = max(0, FCF − LRC). The Q2 2026 flood assumption (registry v2)
# MAGIC pushes PROP forward LR to 78% and the test bites on the 2025 + 2026 property cohorts.
# MAGIC
# MAGIC **LIC engine (ALL portfolios incl. GMM lines + run-off)**: discounted at current rates + RA,
# MAGIC with the §103 decomposition — incurred (current service) for the current accident year,
# MAGIC changes relating to past service for prior years, unwind and rate change as IFIE.
# MAGIC
# MAGIC **Reinsurance held**: 30% property quota share → recoverable asset, recoveries on paid, and
# MAGIC the **loss-recovery component** offsetting the gross loss component (simplified, disclosed).

# COMMAND ----------

# MAGIC %run ./engine_common

# COMMAND ----------

curves = load_curves()
groups = pdf(f"SELECT * FROM {FQ}.gld_contract_groups")
paa_groups = groups[groups["measurement_model"] == "PAA"].sort_values("group_id")
ra_cov = pdf(f"SELECT portfolio_id, cov FROM {FQ}.ref_ra_params").set_index("portfolio_id")["cov"]
fcf = pdf(f"SELECT * FROM {FQ}.gld_fcf_summary WHERE basis = 'current'")

# COMMAND ----------

# MAGIC %md ## Premiums received, earned, acquisition — bottom-up from the landed policy book

# COMMAND ----------

qgrid = " UNION ALL ".join(
    f"SELECT '{lbl}' q, DATE'{datetime.date(int(lbl[:4]), 3*int(lbl[-1])-2, 1)}' qs, DATE'{q_end(lbl)}' qe"
    for lbl in QL)

cash = pdf(f"""
    WITH qs AS ({qgrid}),
    pol AS (SELECT p.*, m.group_id FROM {FQ}.slv_policy p JOIN {FQ}.gld_group_policy_map m USING (policy_id))
    SELECT g.group_id, q.q close_period,
      ROUND(SUM(CASE WHEN p.installments = 1 AND p.inception_date BETWEEN q.qs AND q.qe THEN p.total_premium
                     WHEN p.installments > 1 AND p.inception_date BETWEEN q.qs AND q.qe THEN p.annual_premium
                     WHEN p.installments > 1 AND add_months(p.inception_date, 12) BETWEEN q.qs AND q.qe THEN p.annual_premium
                     WHEN p.installments > 1 AND add_months(p.inception_date, 24) BETWEEN q.qs AND q.qe THEN p.annual_premium
                     ELSE 0 END), 2) premiums_received,
      ROUND(SUM(CASE WHEN p.inception_date BETWEEN q.qs AND q.qe THEN p.acq_cost ELSE 0 END), 2) acq_paid,
      ROUND(SUM(p.total_premium *
        GREATEST(0, DATEDIFF(LEAST(p.expiry_date, q.qe), GREATEST(p.inception_date, q.qs)) + CASE WHEN p.expiry_date >= q.qs AND p.inception_date <= q.qe THEN 1 ELSE 0 END)
        / (DATEDIFF(p.expiry_date, p.inception_date) + 1)), 2) earned_premium,
      ROUND(SUM(p.acq_cost *
        GREATEST(0, DATEDIFF(LEAST(p.expiry_date, q.qe), GREATEST(p.inception_date, q.qs)) + CASE WHEN p.expiry_date >= q.qs AND p.inception_date <= q.qe THEN 1 ELSE 0 END)
        / (DATEDIFF(p.expiry_date, p.inception_date) + 1)), 2) acq_amortised
    FROM pol p CROSS JOIN qs q
    JOIN {FQ}.gld_contract_groups g ON g.group_id = p.group_id
    GROUP BY 1, 2""")
cash_ix = cash.set_index(["group_id", "close_period"])

write_engine(cash, "gld_cash_measures",
             "group_id string, close_period string, premiums_received double, acq_paid double, "
             "earned_premium double, acq_amortised double",
             "Bottom-up cash + earning measures per group × quarter from the landed policy book "
             "(daily pro-rata earning; CLT annual installments within the contract boundary).")

# COMMAND ----------

# MAGIC %md ## PAA LRC roll-forward + §57 onerous test + loss component

# COMMAND ----------

lrc_rows, ot_rows, lc_rows = [], [], []
for _, g in paa_groups.iterrows():
    gid, port = g["group_id"], g["portfolio_id"]
    opening = 0.0
    lc_opening = 0.0
    for lbl in QL:
        if int(lbl[:4]) < int(g["cohort_year"]):
            continue
        c = cash_ix.loc[(gid, lbl)] if (gid, lbl) in cash_ix.index else None
        prem = float(c["premiums_received"]) if c is not None else 0.0
        earned = float(c["earned_premium"]) if c is not None else 0.0
        acq_paid = float(c["acq_paid"]) if c is not None else 0.0
        acq_am = float(c["acq_amortised"]) if c is not None else 0.0
        closing = round(opening + prem - earned - acq_paid + acq_am, 2)
        for step, amt in (("opening", opening), ("premiums_received", prem),
                          ("insurance_revenue", -earned), ("acq_cashflows_paid", -acq_paid),
                          ("acq_amortisation", acq_am), ("closing", closing)):
            lrc_rows.append(dict(group_id=gid, portfolio_id=port, close_period=lbl, step=step, amount=round(amt, 2)))

        # §57 facts-and-circumstances test — every quarter, against the CURRENT-basis FCF from 04b
        f = fcf[(fcf["group_id"] == gid) & (fcf["close_period"] == lbl)]
        fcf_rem = float(f["fcf_remaining"].iloc[0]) if len(f) else 0.0
        headroom = round(closing - fcf_rem, 2)
        lc_target = round(max(0.0, fcf_rem - closing), 2)
        ot_rows.append(dict(group_id=gid, portfolio_id=port, close_period=lbl,
                            lrc_carrying=closing, fcf_remaining_current=fcf_rem, headroom=headroom,
                            onerous=bool(lc_target > 0),
                            trigger="facts_and_circumstances_quarterly_monitoring"))
        recognised = round(max(0.0, lc_target - lc_opening), 2)
        reversed_ = round(max(0.0, lc_opening - lc_target), 2)
        for step, amt in (("opening", lc_opening), ("recognised_in_period", recognised),
                          ("reversed_in_period", -reversed_), ("closing", lc_target)):
            lc_rows.append(dict(group_id=gid, portfolio_id=port, close_period=lbl, step=step, amount=round(amt, 2)))
        opening, lc_opening = closing, lc_target

write_engine(pd.DataFrame(lrc_rows), "gld_lrc_paa_rollforward",
             "group_id string, portfolio_id string, close_period string, step string, amount double",
             "PAA LRC (excluding loss component) roll-forward: opening + premiums received - insurance "
             "revenue (earned) - acquisition cash flows + acquisition amortisation = closing. Undiscounted per §56.")
write_engine(pd.DataFrame(ot_rows), "gld_onerous_test",
             "group_id string, portfolio_id string, close_period string, lrc_carrying double, "
             "fcf_remaining_current double, headroom double, onerous boolean, trigger string",
             "§57 onerous test, run every quarter (continuous monitoring): loss component = max(0, GMM-style "
             "FCF for remaining coverage - PAA LRC carrying amount). Headroom trend = the early-warning watch.")
write_engine(pd.DataFrame(lc_rows), "gld_loss_component",
             "group_id string, portfolio_id string, close_period string, step string, amount double",
             "Loss component roll-forward (within the group — groups are never re-bucketed): opening + "
             "recognised - reversed = closing. Recognition hits insurance service expenses immediately.")

onerous_now = pd.DataFrame(ot_rows)
onerous_now = onerous_now[(onerous_now["close_period"] == CLOSE_PERIOD) & (onerous_now["onerous"])]
print("Onerous at", CLOSE_PERIOD, ":", onerous_now[["group_id", "headroom"]].to_dict("records"))

# COMMAND ----------

# MAGIC %md ## LIC engine — discounted + RA, §103 decomposition (all portfolios)

# COMMAND ----------

lic_proj = pdf(f"""
    SELECT run_id, portfolio_id, cohort_or_accident_year accident_year,
           CAST(projection_month AS STRING) projection_month, SUM(amount) amount
    FROM {FQ}.slv_cashflow_projection WHERE scope = 'LIC'
    GROUP BY 1, 2, 3, 4""")
lic_proj["m"] = lic_proj["projection_month"].map(datetime.date.fromisoformat)

paid = pdf(f"""
    SELECT p.portfolio_id, CAST(SUBSTRING(c.accident_quarter,1,4) AS INT) accident_year,
           p.payment_quarter close_period, ROUND(SUM(p.amount),2) paid
    FROM {FQ}.slv_claim_payments p JOIN {FQ}.slv_claim c USING (claim_id)
    GROUP BY 1,2,3""").set_index(["portfolio_id", "accident_year", "close_period"])["paid"]


def lic_pv(run, port, ay, basis_date, asof):
    sub = lic_proj[(lic_proj["run_id"] == run) & (lic_proj["portfolio_id"] == port)
                   & (lic_proj["accident_year"] == ay) & (lic_proj["m"] > asof)]
    if sub.empty:
        return 0.0
    return pv([(r["m"], r["amount"]) for _, r in sub.iterrows()], curves[(basis_date, port)], asof)


ports_ay = sorted({(r["portfolio_id"], int(r["accident_year"])) for _, r in lic_proj.iterrows()}
                  | {(p, ay) for (p, ay, _), _ in paid.items()})

lic_rows = []
for port, ay in ports_ay:
    cov = float(ra_cov[port])
    raf = ra_factor(cov)
    opening = 0.0
    for i, lbl in enumerate(QL):
        asof = q_end(lbl)
        run = f"RSV_{lbl}"
        basis = CURRENT_CURVE[lbl]
        pv_rem = lic_pv(run, port, ay, basis, asof)
        closing = round(pv_rem * (1 + raf), 2)
        paid_q = float(paid.get((port, ay, lbl), 0.0))
        if closing == 0 and opening == 0 and paid_q == 0:
            continue
        # unwind at the prior current basis 3-month rate on the opening balance
        prior_basis = CURRENT_CURVE[QL[i - 1]] if i > 0 else basis
        df3 = float(curves[(prior_basis, port)].get(3, 1.0))
        unwind = round(opening * (1.0 / df3 - 1.0), 2)
        # rate-change effect (IFIE): prior run's remaining flows at new vs old basis
        rate_change = 0.0
        if i > 0 and prior_basis != basis:
            prior_run = f"RSV_{QL[i-1]}"
            pv_new = lic_pv(prior_run, port, ay, basis, asof)
            pv_old = lic_pv(prior_run, port, ay, prior_basis, asof)
            rate_change = round((pv_new - pv_old) * (1 + raf), 2)
        movement = round(closing - opening + paid_q - unwind - rate_change, 2)
        is_current_ay = (ay == int(lbl[:4]))
        is_bootstrap = (i == 0 and port == "RO")
        incurred = movement if is_current_ay else 0.0
        past_service = 0.0 if is_current_ay else (0.0 if is_bootstrap else movement)
        brought_forward = movement if is_bootstrap else 0.0
        for step, amt in (("opening", opening),
                          ("brought_forward_window_start", brought_forward),
                          ("incurred_current_service", incurred),
                          ("past_service_changes", past_service),
                          ("unwind_ifie", unwind),
                          ("rate_change_ifie", rate_change),
                          ("claims_paid", -paid_q),
                          ("closing", closing)):
            if amt != 0.0 or step in ("opening", "closing"):
                lic_rows.append(dict(portfolio_id=port, accident_year=ay, close_period=lbl,
                                     step=step, amount=round(amt, 2),
                                     closing_pv=pv_rem if step == "closing" else None,
                                     closing_ra=round(closing - pv_rem, 2) if step == "closing" else None))
        opening = closing

write_engine(pd.DataFrame(lic_rows), "gld_lic_rollforward",
             "portfolio_id string, accident_year int, close_period string, step string, amount double, "
             "closing_pv double, closing_ra double",
             "LIC roll-forward per portfolio × accident year, discounted at current rates + RA (CL-75). "
             "§103 decomposition: incurred (current accident year) vs changes relating to past service; "
             "unwind + rate change = insurance finance expenses. PAA LIC is discounted — long-settlement "
             "lines never sit undiscounted.")

# COMMAND ----------

# MAGIC %md ## Reinsurance held — quota share asset + loss-recovery component

# COMMAND ----------

treaty = pdf(f"SELECT * FROM {FQ}.slv_treaty WHERE treaty_type = 'quota_share'").iloc[0]
CESSION, COMMISSION = float(treaty["cession_pct"]), float(treaty["commission_pct"])
lic_df = pd.DataFrame(lic_rows)
lc_df = pd.DataFrame(lc_rows)

ri_rows = []
for lbl in QL:
    if lbl < "2025Q1":
        continue
    # ceded premium on PROP receipts (2025+2026 cohorts are within the treaty period)
    prem_c = cash[(cash["close_period"] == lbl) & (cash["group_id"].str.startswith("PROP-202"))
                  & (~cash["group_id"].str.startswith("PROP-2024"))]["premiums_received"].sum()
    ceded = round(prem_c * CESSION, 2)
    commission = round(ceded * COMMISSION, 2)
    # recoverable on LIC: covered accident years 2025+
    lic_close = lic_df[(lic_df["portfolio_id"] == "PROP") & (lic_df["close_period"] == lbl)
                       & (lic_df["step"] == "closing") & (lic_df["accident_year"] >= 2025)]["amount"].sum()
    recoverable = round(lic_close * CESSION, 2)
    paid_prop = -lic_df[(lic_df["portfolio_id"] == "PROP") & (lic_df["close_period"] == lbl)
                        & (lic_df["step"] == "claims_paid") & (lic_df["accident_year"] >= 2025)]["amount"].sum()
    recoveries = round(paid_prop * CESSION, 2)
    # loss-recovery component: offsets the gross LC on covered groups
    lc_close = lc_df[(lc_df["close_period"] == lbl) & (lc_df["step"] == "closing")
                     & (lc_df["group_id"].isin(["PROP-2025-REM", "PROP-2026-REM"]))]["amount"].sum()
    lrc_comp = round(lc_close * CESSION, 2)
    for component, amount, note in (
            ("premium_ceded", -ceded, f"{CESSION:.0%} QS on property receipts"),
            ("commission_income", commission, f"ceding commission {COMMISSION:.0%}"),
            ("recoverable_on_lic", recoverable, "share of discounted LIC, covered accident years"),
            ("recoveries_on_paid", recoveries, "cash recoveries on paid claims"),
            ("loss_recovery_component", lrc_comp,
             "offsets the gross loss component on covered onerous groups (§66A-B simplified)")):
        ri_rows.append(dict(close_period=lbl, treaty_id=treaty["treaty_id"], component=component,
                            amount=amount, note=note))

write_engine(pd.DataFrame(ri_rows), "gld_ri_held",
             "close_period string, treaty_id string, component string, amount double, note string",
             "Reinsurance held (30% property quota share), simplified and disclosed: ceded premium, "
             "commission, recoverable on LIC, recoveries on paid, and the LOSS-RECOVERY component — "
             "reinsurance held is never onerous; it offsets the gross loss component. The cat XL is "
             "data-only (June 2026 floods sit below its attachment).")

log_run("paa_lic_engine",
        ["slv_policy", "slv_claim", "slv_claim_payments", "slv_cashflow_projection", "slv_treaty",
         "gld_fcf_summary", "gld_contract_groups", "ref_discount_curve", "ref_ra_params"],
        {"flood_freq_property": 2, "casualty_inflation_clt": 2, "risk_adjustment_cl": 1, "illiquidity_premium": 1},
        ["gld_cash_measures", "gld_lrc_paa_rollforward", "gld_onerous_test", "gld_loss_component",
         "gld_lic_rollforward", "gld_ri_held"],
        curve_dates={"current": CURRENT_CURVE[CLOSE_PERIOD]},
        note=f"onerous at {CLOSE_PERIOD}: {sorted(onerous_now['group_id'])}")
print("04c complete")
