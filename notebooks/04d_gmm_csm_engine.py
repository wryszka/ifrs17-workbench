# Databricks notebook source
# MAGIC %md
# MAGIC # 04d · GMM / CSM engine — B96-ordered, locked-in accretion, coverage-unit release
# MAGIC
# MAGIC Per GMM group (Commercial long-tail 3y, Construction decennial 10y) per quarter, in the
# MAGIC **B96 order — auditors test this ordering**:
# MAGIC 1. opening → 2. new business (initial recognition of contracts written in the quarter,
# MAGIC pricing basis, locked-in curve) → 3. interest accretion at the **locked-in** rate →
# MAGIC 4. experience adjustments relating to future service → 5. changes in fulfilment cash
# MAGIC flows relating to future service (the unlock — isolated from new business by evaluating
# MAGIC consecutive reserving runs on the same curve and as-at date) → 6. FX (nil — EUR book) →
# MAGIC 7. **release LAST**, on the post-adjustment balance, by coverage units provided ÷
# MAGIC (current + remaining).
# MAGIC
# MAGIC If an unlock exhausted the CSM the excess would flip to a loss component (machinery
# MAGIC present; the Q2 2026 casualty-inflation unlock on CLT-2025 is designed to be visible,
# MAGIC not fatal). The run-off book never enters this engine — it has no CSM by construction.

# COMMAND ----------

# MAGIC %run ./engine_common

# COMMAND ----------

curves = load_curves()
asm = load_assumptions()
pricing_lr = asm["pricing_loss_ratio"][1]
groups = pdf(f"SELECT * FROM {FQ}.gld_contract_groups WHERE measurement_model = 'GMM'").sort_values("group_id")
ra_cov = pdf(f"SELECT portfolio_id, cov FROM {FQ}.ref_ra_params").set_index("portfolio_id")["cov"]
ports_meta = pdf(f"SELECT * FROM {FQ}.ref_portfolio").set_index("portfolio_id")

proj = pdf(f"""
    SELECT run_id, portfolio_id, cohort_or_accident_year cohort_year,
           CAST(projection_month AS STRING) projection_month, cf_type, SUM(amount) amount
    FROM {FQ}.slv_cashflow_projection WHERE scope = 'LRC'
    GROUP BY 1,2,3,4,5""")
proj["m"] = proj["projection_month"].map(datetime.date.fromisoformat)

# expense ratio per portfolio (same governed derivation as the grouping engine)
expense = pdf(f"SELECT portfolio_id, ROUND(SUM(attributable_expense),2) e FROM {FQ}.slv_expense GROUP BY 1") \
    .set_index("portfolio_id")["e"]
gwp_tot = pdf(f"SELECT portfolio_id, SUM(total_premium) g FROM {FQ}.slv_policy GROUP BY 1") \
    .set_index("portfolio_id")["g"]


def fcf_rem_locked(run, port, cy, li_date, asof, raf):
    """FCF for remaining coverage at the locked-in curve: PV(out) + RA - PV(future premiums)."""
    sub = proj[(proj["run_id"] == run) & (proj["portfolio_id"] == port)
               & (proj["cohort_year"] == cy) & (proj["m"] > asof)]
    if sub.empty:
        return 0.0, 0.0
    curve = curves[(li_date, port)]
    pv_out = pv([(r["m"], r["amount"]) for _, r in sub[sub["cf_type"].isin(["claims", "expense"])].iterrows()], curve, asof)
    pv_in = pv([(r["m"], r["amount"]) for _, r in sub[sub["cf_type"] == "premium"].iterrows()], curve, asof)
    ra = round(pv_out * raf, 2)
    return round(pv_out + ra - pv_in, 2), ra

# COMMAND ----------

# MAGIC %md ## New business (per writing quarter) + coverage units — bottom-up from the policy book

# COMMAND ----------

qgrid = " UNION ALL ".join(
    f"SELECT '{lbl}' q, DATE'{datetime.date(int(lbl[:4]), 3*int(lbl[-1])-2, 1)}' qs, DATE'{q_end(lbl)}' qe"
    for lbl in QL)

nb = pdf(f"""
    WITH qs AS ({qgrid}),
    pol AS (SELECT p.*, m.group_id FROM {FQ}.slv_policy p JOIN {FQ}.gld_group_policy_map m USING (policy_id)
            WHERE p.portfolio_id IN ('CLT','DEC'))
    SELECT p.group_id, q.q close_period,
      ROUND(SUM(CASE WHEN p.inception_date BETWEEN q.qs AND q.qe
                 THEN CASE WHEN p.installments = 1 THEN p.total_premium ELSE p.annual_premium END ELSE 0 END), 2) prem_received_nb,
      ROUND(SUM(CASE WHEN p.inception_date BETWEEN q.qs AND q.qe THEN p.acq_cost ELSE 0 END), 2) acq_nb,
      ROUND(SUM(CASE WHEN p.inception_date BETWEEN q.qs AND q.qe AND p.installments > 1 THEN p.annual_premium * 2 ELSE 0 END), 2) fut_prem_nb_face,
      ROUND(SUM(CASE WHEN p.inception_date BETWEEN q.qs AND q.qe THEN p.total_premium ELSE 0 END), 2) written_nb
    FROM pol p CROSS JOIN qs q GROUP BY 1, 2""").set_index(["group_id", "close_period"])

units = pdf(f"""
    WITH qs AS ({qgrid}),
    pol AS (SELECT p.*, m.group_id FROM {FQ}.slv_policy p JOIN {FQ}.gld_group_policy_map m USING (policy_id)
            WHERE p.portfolio_id IN ('CLT','DEC'))
    SELECT p.group_id, q.q close_period,
      ROUND(SUM(p.sum_insured *
        GREATEST(0, DATEDIFF(LEAST(p.expiry_date, q.qe), GREATEST(p.inception_date, q.qs)) + 1) / 365.25) / 1e6, 6) units_in_q,
      ROUND(SUM(p.sum_insured * GREATEST(0, DATEDIFF(p.expiry_date, q.qe)) / 365.25) / 1e6, 6) units_remaining
    FROM pol p CROSS JOIN qs q GROUP BY 1, 2""").set_index(["group_id", "close_period"])

# COMMAND ----------

# MAGIC %md ## The roll-forward

# COMMAND ----------

csm_rows, cu_rows, rev_rows = [], [], []
for _, g in groups.iterrows():
    gid, port, cy = g["group_id"], g["portfolio_id"], int(g["cohort_year"])
    li_date = g["locked_in_curve_date"]
    raf = ra_factor(float(ra_cov[port]))
    exp_ratio = round(float(expense.get(port, 0.0)) / float(gwp_tot.get(port, 1.0)), 4)
    cov_m = int(ports_meta.loc[port, "coverage_months"])
    df3 = float(curves[(li_date, port)].get(3, 1.0))
    q_rate = 1.0 / df3 - 1.0
    opening, lc = 0.0, 0.0
    prev_fcf_rem = None
    tranches = []  # (written_amount, quarter_index) — for the coverage-weighted NB share
    for i, lbl in enumerate(QL):
        if int(lbl[:4]) < cy:
            continue
        asof = q_end(lbl)
        run = f"RSV_{lbl}"

        # --- 2 · new business at initial recognition (pricing basis, locked-in curve) ---
        n = nb.loc[(gid, lbl)] if (gid, lbl) in nb.index else None
        nb_csm, nb_fcf_future = 0.0, 0.0
        if n is not None and float(n["written_nb"]) > 0:
            written = float(n["written_nb"])
            prem_rec = float(n["prem_received_nb"])
            acq = float(n["acq_nb"])
            curve = curves[(li_date, port)]
            # expected claims+expenses spread monthly over coverage from mid-quarter inception
            monthly_out = written * (pricing_lr[port] + exp_ratio) / cov_m
            out_flows = [(datetime.date(asof.year + (asof.month - 1 + m) // 12, (asof.month - 1 + m) % 12 + 1, 1), monthly_out)
                         for m in range(1, cov_m + 1)]
            pv_out = pv(out_flows, curve, asof)
            ra_nb = round(pv_out * raf, 2)
            fut_prem = float(n["fut_prem_nb_face"])
            inst_flows = ([(datetime.date(asof.year + 1, asof.month, 1), fut_prem / 2),
                           (datetime.date(asof.year + 2, asof.month, 1), fut_prem / 2)] if fut_prem > 0 else [])
            pv_fut_prem = pv(inst_flows, curve, asof)
            nb_fcf_at_init = round(pv_out + ra_nb - pv_fut_prem - (prem_rec - acq), 2)
            nb_csm = round(max(0.0, -nb_fcf_at_init), 2)
            nb_fcf_future = round(pv_out + ra_nb - pv_fut_prem, 2)  # the part that lives in FCF remaining

        # --- 3 · accretion at locked-in ---
        accretion = round(opening * q_rate, 2)

        # --- 5 · future-service unlock: run-on-run PV change for EXISTING contracts only.
        # The new-business share of the current FCF is allocated coverage-weighted from the
        # projections themselves (written × remaining coverage), so no pricing-basis mismatch
        # can masquerade as an assumption change. Disclosed simplification.
        if n is not None and float(n["written_nb"]) > 0:
            tranches.append([float(n["written_nb"]), i])
        weights = [wr * max(0, cov_m - 3 * (i - qi)) for wr, qi in tranches]
        nb_w = weights[-1] if (n is not None and float(n["written_nb"]) > 0 and weights) else 0.0
        nb_share = (nb_w / sum(weights)) if sum(weights) > 0 else 0.0
        fcf_now, ra_now = fcf_rem_locked(run, port, cy, li_date, asof, raf)
        unlock = 0.0
        if prev_fcf_rem is not None:
            prior_run = f"RSV_{QL[i-1]}"
            fcf_prior_reval, _ = fcf_rem_locked(prior_run, port, cy, li_date, asof, raf)
            unlock = round(fcf_now * (1.0 - nb_share) - fcf_prior_reval, 2)
        prev_fcf_rem = fcf_now

        # --- 7 · release last, on the post-adjustment balance ---
        base = round(opening + nb_csm + accretion - unlock, 2)
        lc_add = 0.0
        if base < 0:  # unlock exhausted the CSM → loss component (not expected on this book)
            lc_add, base = -base, 0.0
        u = units.loc[(gid, lbl)] if (gid, lbl) in units.index else None
        u_q = float(u["units_in_q"]) if u is not None else 0.0
        u_rem = float(u["units_remaining"]) if u is not None else 0.0
        rel_frac = (u_q / (u_q + u_rem)) if (u_q + u_rem) > 0 else 0.0
        release = round(base * rel_frac, 2)
        closing = round(base - release, 2)

        for step, amt in (("opening", opening), ("new_business", nb_csm),
                          ("interest_accretion", accretion), ("experience_adjustments", 0.0),
                          ("fcf_changes_future_service", -unlock), ("fx", 0.0),
                          ("csm_release", -release), ("closing", closing)):
            csm_rows.append(dict(group_id=gid, portfolio_id=port, close_period=lbl, step=step,
                                 amount=round(amt, 2), paragraph={"opening": "B96", "new_business": "B96(a)",
                                 "interest_accretion": "B96(b)", "experience_adjustments": "B96(d)",
                                 "fcf_changes_future_service": "B96(c)", "fx": "B96", "csm_release": "B119",
                                 "closing": "B96"}[step]))
        cu_rows.append(dict(group_id=gid, portfolio_id=port, close_period=lbl,
                            units_in_period=u_q, units_remaining=u_rem,
                            release_fraction=round(rel_frac, 6), csm_release=release,
                            basis="sum insured x coverage period (EUR m-years), undiscounted (disclosed)"))

        # revenue components (GMM): expected current-period claims+expenses (prior run view),
        # RA release, CSM release, acquisition amortisation
        prior_run = f"RSV_{QL[i-1]}" if i > 0 else run
        sub_rev = proj[(proj["run_id"] == prior_run) & (proj["portfolio_id"] == port)
                       & (proj["cohort_year"] == cy) & (proj["m"] <= asof)
                       & (proj["cf_type"].isin(["claims", "expense"]))]
        if i > 0:
            sub_rev = sub_rev[sub_rev["m"] > q_end(QL[i - 1])]
        exp_q = sub_rev["amount"].sum()
        ra_release = round(exp_q * raf, 2)
        for comp, amt in (("expected_claims_expenses", round(float(exp_q), 2)),
                          ("ra_release", ra_release), ("csm_release", release)):
            rev_rows.append(dict(group_id=gid, portfolio_id=port, close_period=lbl,
                                 component=comp, amount=amt))
        opening, lc = closing, round(lc + lc_add, 2)

write_engine(pd.DataFrame(csm_rows), "gld_csm_rollforward",
             "group_id string, portfolio_id string, close_period string, step string, amount double, paragraph string",
             "CSM roll-forward, strict B96 ordering with paragraph references: opening, new business, "
             "interest accretion at the LOCKED-IN rate, experience adjustments, FCF changes relating to "
             "future service (unlock), FX, release LAST via coverage units. CSM is insensitive to "
             "current-rate moves — accretion is locked-in.")
write_engine(pd.DataFrame(cu_rows), "gld_coverage_units",
             "group_id string, portfolio_id string, close_period string, units_in_period double, "
             "units_remaining double, release_fraction double, csm_release double, basis string",
             "Coverage units per GMM group: sum insured x coverage provided (EUR m-years). Release = "
             "post-adjustment CSM x units provided / (provided + remaining). Basis is a versioned assumption.")
write_engine(pd.DataFrame(rev_rows), "gld_revenue_gmm",
             "group_id string, portfolio_id string, close_period string, component string, amount double",
             "GMM insurance revenue components: expected claims+expenses (prior-run view), RA release, "
             "CSM release. Never premium.")

clt25 = pd.DataFrame(csm_rows)
clt25 = clt25[(clt25["group_id"] == "CLT-2025-NSP") & (clt25["close_period"] == CLOSE_PERIOD)]
print("CLT-2025 CSM waterfall at", CLOSE_PERIOD)
print(clt25[["step", "amount"]].to_string(index=False))

log_run("gmm_csm_engine",
        ["slv_policy", "slv_cashflow_projection", "slv_expense", "gld_contract_groups",
         "ref_discount_curve", "ref_ra_params", "gov_assumption_registry"],
        {"pricing_loss_ratio": 1, "casualty_inflation_clt": 2, "coverage_unit_basis": 1,
         "risk_adjustment_cl": 1, "illiquidity_premium": 1},
        ["gld_csm_rollforward", "gld_coverage_units", "gld_revenue_gmm"],
        curve_dates={"locked_in": LOCKED_IN_CURVE},
        note="B96 order enforced; unlock isolated run-on-run at locked-in basis")
print("04d complete")
