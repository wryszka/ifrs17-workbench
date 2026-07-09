# Databricks notebook source
# MAGIC %md
# MAGIC # 04f · Statements + disclosures + AoC + crosswalk + Cohort 360
# MAGIC
# MAGIC The §80 statement face, the §100–103 roll-forward (by liability component) and the §104
# MAGIC analysis (by measurement component), the RA roll-forward, the deterministic analysis of
# MAGIC change, the Solvency II crosswalk (differences LABELLED, never naively equal), the onerous
# MAGIC early-warning watch, and the denormalized Cohort 360 serving table.
# MAGIC
# MAGIC The P&L, balance sheet and §101 disclosure are derived **from the subledger postings** —
# MAGIC articulation with the books is guaranteed by construction, not by hope.

# COMMAND ----------

# MAGIC %run ./engine_common

# COMMAND ----------

post = pdf(f"SELECT * FROM {FQ}.gld_subledger_postings")
post["signed_liab"] = post.apply(lambda r: r["amount"] if r["dr_cr"] == "CR" else -r["amount"], axis=1)
post["signed_pl"] = post.apply(lambda r: r["amount"] if r["dr_cr"] == "DR" else -r["amount"], axis=1)

lrc = pdf(f"SELECT * FROM {FQ}.gld_lrc_paa_rollforward")
lc = pdf(f"SELECT * FROM {FQ}.gld_loss_component")
lic = pdf(f"SELECT * FROM {FQ}.gld_lic_rollforward")
csm = pdf(f"SELECT * FROM {FQ}.gld_csm_rollforward")
fcf = pdf(f"SELECT * FROM {FQ}.gld_fcf_summary")
oci = pdf(f"SELECT * FROM {FQ}.gld_discount_impact")
ot = pdf(f"SELECT * FROM {FQ}.gld_onerous_test")
ri = pdf(f"SELECT * FROM {FQ}.gld_ri_held")
rev_gmm = pdf(f"SELECT * FROM {FQ}.gld_revenue_gmm")
cu = pdf(f"SELECT * FROM {FQ}.gld_coverage_units")
groups = pdf(f"SELECT * FROM {FQ}.gld_contract_groups")
curves = load_curves()

# COMMAND ----------

# MAGIC %md ## Insurance revenue decomposition (never premium on the face)

# COMMAND ----------

rev_rows = []
for _, r in lrc[lrc["step"] == "insurance_revenue"].iterrows():
    rev_rows.append(dict(group_id=r["group_id"], portfolio_id=r["portfolio_id"],
                         close_period=r["close_period"], component="paa_earned_premium",
                         amount=round(-r["amount"], 2)))
for _, r in rev_gmm.iterrows():
    rev_rows.append(dict(group_id=r["group_id"], portfolio_id=r["portfolio_id"],
                         close_period=r["close_period"], component=f"gmm_{r['component']}",
                         amount=round(r["amount"], 2)))
rev = pd.DataFrame(rev_rows)
write_engine(rev, "gld_insurance_revenue",
             "group_id string, portfolio_id string, close_period string, component string, amount double",
             "Insurance revenue decomposition: PAA earned premium; GMM expected claims+expenses, RA release, "
             "CSM release. Written premium NEVER appears on the statement face (GWP is a KPI, not revenue).")

# COMMAND ----------

# MAGIC %md ## §80 P&L + balance sheet — straight from the postings

# COMMAND ----------

PL_MAP = [  # (line_no, label, accounts, sign: expense positive as charge)
    (1, "Insurance revenue", ["4000"], -1),
    (2, "Insurance service expenses", ["5000", "5010", "5020", "5030", "5040"], 1),
    (3, "Net income/(expenses) from reinsurance contracts held", ["5100"], 1),
    (4, "Insurance service result", None, None),
    (5, "Insurance finance income/(expenses) — P&L", ["6000"], 1),
    (6, "Other operating expenses", ["7000"], 1),
    (7, "Profit before tax (insurance activities)", None, None),
]
pl_rows = []
for lbl in QL:
    p = post[post["close_period"] == lbl]
    vals = {}
    for no, label, accts, sign in PL_MAP:
        if accts is None:
            continue
        v = round(float(p[p["gl_account"].isin(accts)]["signed_pl"].sum()) * (1 if sign == 1 else -1), 2)
        vals[no] = v
    isr = round(vals[1] - vals[2] - vals[3], 2)
    pbt = round(isr - vals[5] - vals[6], 2)
    for no, label, accts, sign in PL_MAP:
        amount = isr if no == 4 else (pbt if no == 7 else (vals[no] if no == 1 else -vals[no]))
        # presentation: revenue positive, expenses negative, results computed
        if no in (2, 3, 5, 6):
            amount = -vals[no]
        pl_rows.append(dict(close_period=lbl, line_no=no, line_item=label, amount=round(amount, 2)))
write_engine(pd.DataFrame(pl_rows), "gld_pnl_statement",
             "close_period string, line_no int, line_item string, amount double",
             "§80 statement of profit or loss (insurance activities), derived from the subledger postings — "
             "articulation guaranteed. Insurance revenue / ISE / net reinsurance → insurance service result; "
             "then IFIE (P&L leg; the OCI leg is in the balance sheet reserve).")

BS_MAP = [(1, "Cash and equivalents", ["1000"], "asset"),
          (2, "Reinsurance contract held assets", ["2300"], "asset"),
          (3, "LRC excluding loss component", ["2100"], "liability"),
          (4, "Loss component of the LRC", ["2110"], "liability"),
          (5, "Liability for incurred claims", ["2200"], "liability"),
          (6, "Insurance finance reserve — OCI", ["3900"], "equity"),
          (7, "Opening equity adjustment (window start)", ["3000"], "equity"),
          (8, "Retained insurance result", None, "equity")]
bs_rows = []
for lbl in QL:
    upto = post[post["close_period"] <= lbl]
    retained = 0.0
    for no, label, accts, side in BS_MAP:
        if accts is None:
            continue
        if side == "asset":
            v = round(float(-upto[upto["gl_account"].isin(accts)]["signed_liab"].sum()), 2)
        else:
            v = round(float(upto[upto["gl_account"].isin(accts)]["signed_liab"].sum()), 2)
        bs_rows.append(dict(close_period=lbl, line_no=no, line_item=label, side=side, amount=v))
    pl_accts = ["4000", "5000", "5010", "5020", "5030", "5040", "5100", "6000", "7000"]
    retained = round(float(-upto[upto["gl_account"].isin(pl_accts)]["signed_pl"].sum()), 2)
    bs_rows.append(dict(close_period=lbl, line_no=8, line_item="Retained insurance result",
                        side="equity", amount=retained))
bs = pd.DataFrame(bs_rows)
write_engine(bs, "gld_balance_sheet",
             "close_period string, line_no int, line_item string, side string, amount double",
             "Balance sheet (insurance activities) accumulated from the subledger. "
             "Assets = liabilities + equity to the cent — postings are balanced pairs.")
for lbl in [CLOSE_PERIOD]:
    b = bs[bs["close_period"] == lbl]
    a = b[b["side"] == "asset"]["amount"].sum()
    le = b[b["side"] != "asset"]["amount"].sum()
    assert abs(a - le) < 0.05, f"BS does not balance at {lbl}: {a} vs {le}"
print("balance sheet balances ✓")

# COMMAND ----------

# MAGIC %md ## §100–103 roll-forward by liability component — postings-derived, foots to the BS

# COMMAND ----------

D101 = {
    "lrc_excl_lc": ("2100", {"premiums_received": "Premiums received",
                             "insurance_revenue": "Insurance revenue",
                             "acq_cashflows_paid": "Insurance acquisition cash flows",
                             "acq_amortisation": "Amortisation of acquisition cash flows",
                             "oci_remeasurement": "Remeasurement through OCI (current vs locked-in)"}),
    "loss_component": ("2110", {"loss_component_recognised": "Losses on onerous contracts recognised",
                                "loss_component_reversed": "Reversal of losses on onerous contracts"}),
    "lic": ("2200", {"claims_incurred": "Claims and expenses incurred (current + past service)",
                     "ifie_lic": "Insurance finance expenses (unwind + rate changes)",
                     "claims_paid": "Claims paid",
                     "lic_brought_forward": "Balance brought forward at window start"}),
}
d_rows = []
for col, (acct, keys) in D101.items():
    opening = 0.0
    for lbl in QL:
        p = post[(post["close_period"] == lbl) & (post["gl_account"] == acct)]
        d_rows.append(dict(close_period=lbl, component=col, line="Opening balance", amount=round(opening, 2), ord=0))
        moved = 0.0
        for i, (key, label) in enumerate(keys.items()):
            v = round(float(p[p["posting_key"] == key]["signed_liab"].sum()), 2)
            moved += v
            d_rows.append(dict(close_period=lbl, component=col, line=label, amount=v, ord=i + 1))
        other = round(float(p["signed_liab"].sum()) - moved, 2)
        if abs(other) >= 0.01:
            d_rows.append(dict(close_period=lbl, component=col, line="Other movements", amount=other, ord=98))
        closing = round(opening + float(p["signed_liab"].sum()), 2)
        d_rows.append(dict(close_period=lbl, component=col, line="Closing balance", amount=closing, ord=99))
        opening = closing
write_engine(pd.DataFrame(d_rows), "gld_disclosure_lrc_lic",
             "close_period string, component string, line string, amount double, ord int",
             "§100-103 insurance contract roll-forward by liability component (LRC excl LC / LC / LIC), "
             "derived from the subledger postings — it foots to the balance sheet to the cent because it IS "
             "the balance sheet, re-presented.")

# COMMAND ----------

# MAGIC %md ## §104 analysis by measurement component (PV of FCF / RA / CSM / LC)

# COMMAND ----------

fcf_cur = fcf[fcf["basis"] == "current"]
lic_close = lic[lic["step"] == "closing"]
c_rows = []
comp_prev = {}
for lbl in QL:
    f = fcf_cur[fcf_cur["close_period"] == lbl]
    licq = lic_close[lic_close["close_period"] == lbl]
    pv_fcf = round(float((f["pv_future_claims"] + f["pv_future_expenses"] - f["pv_future_premiums"]).sum())
                   + float(licq["closing_pv"].fillna(0).sum()), 2)
    ra_bal = round(float(f["risk_adjustment"].sum()) + float(licq["closing_ra"].fillna(0).sum()), 2)
    csm_bal = round(float(csm[(csm["close_period"] == lbl) & (csm["step"] == "closing")]["amount"].sum()), 2)
    lc_bal = round(float(lc[(lc["close_period"] == lbl) & (lc["step"] == "closing")]["amount"].sum()), 2)
    for comp, bal in (("pv_fulfilment_cashflows", pv_fcf), ("risk_adjustment", ra_bal),
                      ("csm", csm_bal), ("loss_component", lc_bal)):
        opening = comp_prev.get(comp, 0.0)
        c_rows.append(dict(close_period=lbl, component=comp, line="Opening balance", amount=opening, ord=0))
        if comp == "csm":
            for i, step in enumerate(["new_business", "interest_accretion", "fcf_changes_future_service", "csm_release"]):
                v = round(float(csm[(csm["close_period"] == lbl) & (csm["step"] == step)]["amount"].sum()), 2)
                c_rows.append(dict(close_period=lbl, component=comp,
                                   line={"new_business": "New business recognised",
                                         "interest_accretion": "Interest accretion (locked-in rate)",
                                         "fcf_changes_future_service": "Changes in FCF relating to future service",
                                         "csm_release": "Release to insurance revenue (coverage units)"}[step],
                                   amount=v, ord=i + 1))
            resid = round(bal - opening - float(csm[(csm["close_period"] == lbl)
                          & (csm["step"].isin(["new_business", "interest_accretion",
                                               "fcf_changes_future_service", "csm_release"]))]["amount"].sum()), 2)
        elif comp == "loss_component":
            rec = round(float(lc[(lc["close_period"] == lbl) & (lc["step"] == "recognised_in_period")]["amount"].sum()), 2)
            rev_ = round(float(lc[(lc["close_period"] == lbl) & (lc["step"] == "reversed_in_period")]["amount"].sum()), 2)
            c_rows.append(dict(close_period=lbl, component=comp, line="Recognised on onerous groups", amount=rec, ord=1))
            c_rows.append(dict(close_period=lbl, component=comp, line="Reversals", amount=rev_, ord=2))
            resid = round(bal - opening - rec - rev_, 2)
        else:
            resid = round(bal - opening, 2)
            c_rows.append(dict(close_period=lbl, component=comp,
                               line="Net movement (new business, experience, unwind, rate changes, release)",
                               amount=resid, ord=1))
            resid = 0.0
        if abs(resid) >= 0.01:
            c_rows.append(dict(close_period=lbl, component=comp, line="Other movements", amount=resid, ord=98))
        c_rows.append(dict(close_period=lbl, component=comp, line="Closing balance", amount=bal, ord=99))
        comp_prev[comp] = bal
write_engine(pd.DataFrame(c_rows), "gld_disclosure_by_component",
             "close_period string, component string, line string, amount double, ord int",
             "§104 analysis by measurement component: PV of fulfilment cash flows, risk adjustment, CSM "
             "(B96 movement lines), loss component. The CSM column carries the full waterfall.")

# COMMAND ----------

# MAGIC %md ## RA roll-forward + AoC (deterministic) + onerous watch

# COMMAND ----------

ra_rows, prev_ra = [], 0.0
for lbl in QL:
    f = fcf_cur[fcf_cur["close_period"] == lbl]
    licq = lic_close[lic_close["close_period"] == lbl]
    bal = round(float(f["risk_adjustment"].sum()) + float(licq["closing_ra"].fillna(0).sum()), 2)
    ra_rows.append(dict(close_period=lbl, opening=prev_ra, net_change=round(bal - prev_ra, 2), closing=bal,
                        confidence_level="75% (lognormal quantile per line, CoV in ref_ra_params)"))
    prev_ra = bal
write_engine(pd.DataFrame(ra_rows), "gld_ra_rollforward",
             "close_period string, opening double, net_change double, closing double, confidence_level string",
             "Risk adjustment roll-forward with the disclosed confidence level (75%). LRC + LIC components.")

proj = pdf(f"""SELECT run_id, portfolio_id, cf_type, CAST(projection_month AS STRING) pm, SUM(amount) amount
               FROM {FQ}.slv_cashflow_projection WHERE scope='LRC' AND cf_type='claims' GROUP BY 1,2,3,4""")
proj["m"] = proj["pm"].map(datetime.date.fromisoformat)
aoc_rows = []
for port in sorted(groups["portfolio_id"].unique()):
    for i, lbl in enumerate(QL):
        prior_run = f"RSV_{QL[i-1]}" if i > 0 else None
        expected = 0.0
        if prior_run:
            sub = proj[(proj["run_id"] == prior_run) & (proj["portfolio_id"] == port)
                       & (proj["m"] > q_end(QL[i - 1])) & (proj["m"] <= q_end(lbl))]
            expected = round(float(sub["amount"].sum()), 2)
        inc = round(float(lic[(lic["portfolio_id"] == port) & (lic["close_period"] == lbl)
                             & (lic["step"] == "incurred_current_service")]["amount"].sum()), 2)
        past = round(float(lic[(lic["portfolio_id"] == port) & (lic["close_period"] == lbl)
                              & (lic["step"] == "past_service_changes")]["amount"].sum()), 2)
        unw = round(float(lic[(lic["portfolio_id"] == port) & (lic["close_period"] == lbl)
                             & (lic["step"] == "unwind_ifie")]["amount"].sum()), 2)
        rate = round(float(lic[(lic["portfolio_id"] == port) & (lic["close_period"] == lbl)
                              & (lic["step"] == "rate_change_ifie")]["amount"].sum()), 2)
        assum = round(float(lc[(lc["portfolio_id"] == port) & (lc["close_period"] == lbl)
                              & (lc["step"] == "recognised_in_period")]["amount"].sum())
                      - float(csm[(csm["portfolio_id"] == port) & (csm["close_period"] == lbl)
                                  & (csm["step"] == "fcf_changes_future_service")]["amount"].sum()), 2)
        if not any([expected, inc, past, unw, rate, assum]):
            continue
        aoc_rows.append(dict(portfolio_id=port, close_period=lbl,
                             expected_claims_prior_view=expected, incurred_measured=inc,
                             experience_variance=round(inc - expected, 2),
                             past_service_changes=past,
                             assumption_changes_future_service=assum,
                             unwind_ifie=unw, rate_change_ifie=rate,
                             note="expected = prior reserving run, undiscounted; incurred = discounted + RA "
                                  "(basis difference labelled on screen). Future-service = LC recognised + CSM unlock."))
write_engine(pd.DataFrame(aoc_rows), "gld_ave_analysis",
             "portfolio_id string, close_period string, expected_claims_prior_view double, "
             "incurred_measured double, experience_variance double, past_service_changes double, "
             "assumption_changes_future_service double, unwind_ifie double, rate_change_ifie double, note string",
             "Deterministic analysis of change: experience (actual vs prior expectation, past service) split "
             "from assumption changes (future service) split from unwind and rate effects. THE auditor "
             "conversation, computed not narrated — LLMs only explain these numbers.")

watch = ot.copy()
watch["headroom_pct"] = (watch["headroom"] / watch["lrc_carrying"].where(watch["lrc_carrying"] != 0, 1)).round(4)
watch["watch_status"] = watch.apply(
    lambda r: "BREACH — loss component" if r["onerous"]
    else ("WATCH — headroom < 15%" if r["headroom_pct"] < 0.15 else "OK"), axis=1)
write_engine(watch[["group_id", "portfolio_id", "close_period", "lrc_carrying", "fcf_remaining_current",
                    "headroom", "headroom_pct", "onerous", "watch_status"]],
             "gld_onerous_watch",
             "group_id string, portfolio_id string, close_period string, lrc_carrying double, "
             "fcf_remaining_current double, headroom double, headroom_pct double, onerous boolean, watch_status string",
             "Onerous early-warning: §57 headroom per group per quarter — monitoring between closes is a "
             "standing obligation, not a year-end event.")

# COMMAND ----------

# MAGIC %md ## Solvency II crosswalk — one FCF dataset, two regimes, differences labelled

# COMMAND ----------

lic_proj = pdf(f"""SELECT run_id, portfolio_id, cohort_or_accident_year ay,
                          CAST(projection_month AS STRING) pm, SUM(amount) amount
                   FROM {FQ}.slv_cashflow_projection WHERE scope='LIC' AND run_id='RSV_{CLOSE_PERIOD}'
                   GROUP BY 1,2,3,4""")
lic_proj["m"] = lic_proj["pm"].map(datetime.date.fromisoformat)
asof = q_end(CLOSE_PERIOD)
cw_rows = []
for port in sorted(groups["portfolio_id"].unique()):
    flows = [(r["m"], r["amount"]) for _, r in lic_proj[(lic_proj["portfolio_id"] == port)
                                                        & (lic_proj["m"] > asof)].iterrows()]
    if not flows and port != "RO":
        f = fcf_cur[(fcf_cur["close_period"] == CLOSE_PERIOD) & (fcf_cur["portfolio_id"] == port)]
        if f.empty:
            continue
    pv_ilp = pv(flows, curves[(CURRENT_CURVE[CLOSE_PERIOD], port)], asof) if flows else 0.0
    pv_base = pv(flows, curves[(CURRENT_CURVE[CLOSE_PERIOD], "_BASE")], asof) if flows else 0.0
    f = fcf_cur[(fcf_cur["close_period"] == CLOSE_PERIOD) & (fcf_cur["portfolio_id"] == port)]
    lrc_fcf = round(float((f["pv_future_claims"] + f["pv_future_expenses"] - f["pv_future_premiums"]).sum()), 2)
    ra_ifrs = round(float(f["risk_adjustment"].sum())
                    + float(lic_close[(lic_close["close_period"] == CLOSE_PERIOD)
                                      & (lic_close["portfolio_id"] == port)]["closing_ra"].fillna(0).sum()), 2)
    for item, amount, note in (
        ("ifrs17_pv_fcf_current", round(pv_ilp + lrc_fcf, 2),
         "IFRS 17 PV of fulfilment cash flows, current rates (EIOPA + ILP)"),
        ("ilp_effect", round(pv_base - pv_ilp, 2),
         "COMPUTED: removing the illiquidity premium (SII discounts at pure EIOPA RFR) — LIC scope"),
        ("risk_adjustment_ifrs17", ra_ifrs,
         "IFRS 17 RA (CL-75, entity view of non-financial risk)"),
        ("risk_margin_sii_note", 0.0,
         "SII risk margin = 6% cost-of-capital on projected SCR — a DIFFERENT construct; not computed here (labelled)"),
        ("boundary_note", 0.0,
         "Contract boundary differs: SII premium provisions include bound-not-incepted; IFRS 17 LRC follows §34 (labelled)"),
    ):
        cw_rows.append(dict(portfolio_id=port, close_period=CLOSE_PERIOD, item=item, amount=amount, note=note))
write_engine(pd.DataFrame(cw_rows), "gld_sii_crosswalk",
             "portfolio_id string, close_period string, item string, amount double, note string",
             "One FCF dataset, two regimes. The ILP effect is COMPUTED (real curves both sides); the risk "
             "margin and boundary differences are labelled, never faked as equal. See the Solvency II workbench "
             "for the QRT side.")

# COMMAND ----------

# MAGIC %md ## Cohort 360 — the denormalized serving row per group

# COMMAND ----------

c360 = []
for _, g in groups.iterrows():
    gid, port = g["group_id"], g["portfolio_id"]
    lbl = CLOSE_PERIOD
    lrc_cl = float(lrc[(lrc["group_id"] == gid) & (lrc["close_period"] == lbl) & (lrc["step"] == "closing")]["amount"].sum())
    lc_cl = float(lc[(lc["group_id"] == gid) & (lc["close_period"] == lbl) & (lc["step"] == "closing")]["amount"].sum())
    csm_cl = float(csm[(csm["group_id"] == gid) & (csm["close_period"] == lbl) & (csm["step"] == "closing")]["amount"].sum())
    f = fcf_cur[(fcf_cur["group_id"] == gid) & (fcf_cur["close_period"] == lbl)]
    fcf_c = float(f["fcf_remaining"].iloc[0]) if len(f) else 0.0
    ra_c = float(f["risk_adjustment"].iloc[0]) if len(f) else 0.0
    o = ot[(ot["group_id"] == gid) & (ot["close_period"] == lbl)]
    headroom = float(o["headroom"].iloc[0]) if len(o) else None
    onerous = bool(o["onerous"].iloc[0]) if len(o) else False
    u = cu[(cu["group_id"] == gid) & (cu["close_period"] == lbl)]
    units_rem = float(u["units_remaining"].iloc[0]) if len(u) else None
    revq = float(rev[(rev["group_id"] == gid) & (rev["close_period"] == lbl)]["amount"].sum())
    model = g["measurement_model"]
    bs_lrc = round((fcf_c + csm_cl) if model == "GMM" else lrc_cl + lc_cl, 2)
    c360.append(dict(group_id=gid, portfolio_id=port, cohort_year=int(g["cohort_year"]),
                     profitability_bucket=g["profitability_bucket"], measurement_model=model,
                     close_period=lbl, gwp=float(g["gwp"]), policies=int(g["policies"]),
                     lrc_carrying=round(lrc_cl, 2), loss_component=round(lc_cl, 2), csm=round(csm_cl, 2),
                     fcf_remaining_current=round(fcf_c, 2), risk_adjustment=round(ra_c, 2),
                     bs_lrc_total=bs_lrc, onerous=onerous, headroom=headroom,
                     units_remaining=units_rem, revenue_in_quarter=round(revq, 2),
                     locked_in_curve_date=g["locked_in_curve_date"],
                     assumption_set="flood_freq_property v2, casualty_inflation_clt v2 (2026Q2 run)" ))
write_engine(pd.DataFrame(c360), "gld_cohort_360",
             "group_id string, portfolio_id string, cohort_year int, profitability_bucket string, "
             "measurement_model string, close_period string, gwp double, policies int, lrc_carrying double, "
             "loss_component double, csm double, fcf_remaining_current double, risk_adjustment double, "
             "bs_lrc_total double, onerous boolean, headroom double, units_remaining double, "
             "revenue_in_quarter double, locked_in_curve_date string, assumption_set string",
             "One denormalized row per group at the current close — the app serving table for the Contract "
             "Groups desk and Cohort 360.")

set_status(7, "Disclosures", "done", "statements + §101/§104 + RA roll produced; AoC computed", "disclosures")
set_status(7, "Solvency II", "done", "crosswalk produced from the same FCF dataset", "disclosures")
set_status(8, "Review & AoC", "done", "deterministic AoC ready for review", "disclosures")
set_status(9, "Sign-off", "in_progress", "awaiting CFO sign-off in the workbench", "disclosures")
set_status(10, "Group submission", "in_progress", "board pack available after sign-off", "disclosures")

log_run("disclosure_engine",
        ["gld_subledger_postings", "gld_lrc_paa_rollforward", "gld_loss_component", "gld_lic_rollforward",
         "gld_csm_rollforward", "gld_fcf_summary", "gld_onerous_test", "gld_ri_held", "gld_revenue_gmm",
         "slv_cashflow_projection"],
        {"flood_freq_property": 2, "casualty_inflation_clt": 2},
        ["gld_insurance_revenue", "gld_pnl_statement", "gld_balance_sheet", "gld_disclosure_lrc_lic",
         "gld_disclosure_by_component", "gld_ra_rollforward", "gld_ave_analysis", "gld_onerous_watch",
         "gld_sii_crosswalk", "gld_cohort_360"],
        note="statements derived from postings — articulation by construction")
print("04f complete")
