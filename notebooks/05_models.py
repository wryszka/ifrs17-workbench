# Databricks notebook source
# MAGIC %md
# MAGIC # 05 · LIC emergence model — the ML challenger view
# MAGIC
# MAGIC A LightGBM model predicting **ultimate claims per portfolio × accident quarter** from paid
# MAGIC development features (paid-to-date, development age, payment velocity, mix). It is a
# MAGIC **challenger to the reserving view, never the booked number**: batch-scored into
# MAGIC `gld_lic_ultimates` and shown on Cohort 360 as "ML ultimate vs reserving ultimate".
# MAGIC The booked LIC always comes from the governed reserving projections. Registered in UC →
# MAGIC appears in the SS1/23-shaped model register. Interactive pages never call the model live.

# COMMAND ----------

# MAGIC %pip install lightgbm --quiet

# COMMAND ----------

dbutils.widgets.text("catalog", "lr_dev_aws_us_catalog")
dbutils.widgets.text("schema", "ifrs17_workbench")
catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
FQ = f"{catalog}.{schema}"

import lightgbm as lgb
import mlflow
import numpy as np
import pandas as pd

mlflow.set_registry_uri("databricks-uc")
mlflow.set_experiment(f"/Shared/ifrs17_workbench/lic_emergence")

# COMMAND ----------

# Training frame: development snapshots per portfolio × accident quarter at each age.
tri = spark.sql(f"""
    SELECT portfolio_id, accident_quarter, dev_quarter, paid_in_quarter
    FROM {FQ}.gld_claims_triangles ORDER BY 1, 2, 3""").toPandas()
snap = spark.sql(f"""
    SELECT portfolio_id, accident_quarter,
           ROUND(SUM(paid_to_date + case_reserve), 2) reserving_ultimate,
           COUNT(*) claim_count
    FROM {FQ}.slv_claim GROUP BY 1, 2""").toPandas()

rows = []
for (port, aq), g in tri.groupby(["portfolio_id", "accident_quarter"]):
    g = g.sort_values("dev_quarter")
    cum = g["paid_in_quarter"].cumsum()
    s = snap[(snap["portfolio_id"] == port) & (snap["accident_quarter"] == aq)]
    if s.empty:
        continue
    ult = float(s["reserving_ultimate"].iloc[0])
    n = int(s["claim_count"].iloc[0])
    for i in range(len(g)):
        age = int(g["dev_quarter"].iloc[i])
        paid = float(cum.iloc[i])
        velocity = float(g["paid_in_quarter"].iloc[i]) / max(paid, 1.0)
        rows.append(dict(portfolio_id=port, accident_quarter=aq, age=age, paid_to_date=paid,
                         velocity=round(velocity, 6), claim_count=n, ultimate=ult))
df = pd.DataFrame(rows)
df = pd.get_dummies(df, columns=["portfolio_id"], prefix="p")
FEATURES = [c for c in df.columns if c not in ("accident_quarter", "ultimate")]
print(f"training frame: {len(df)} snapshots, {len(FEATURES)} features")

# mature accident quarters (age >= 4) train; recent ones are the interesting predictions
train = df[df["age"] >= 2]
X, y = train[FEATURES], train["ultimate"]

with mlflow.start_run(run_name="lic_emergence_lgbm") as run:
    model = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.05, num_leaves=15,
                              min_child_samples=5, random_state=42)
    model.fit(X, y)
    mape = float(np.mean(np.abs(model.predict(X) - y) / np.maximum(y, 1.0)))
    mlflow.log_metric("train_mape", mape)
    sig = mlflow.models.infer_signature(X.head(5), model.predict(X.head(5)))
    mlflow.lightgbm.log_model(model, name="model", signature=sig, input_example=X.head(5),
                              registered_model_name=f"{FQ}.model_lic_emergence")
print(f"train MAPE {mape:.3f}")

from mlflow import MlflowClient
client = MlflowClient()
mv = client.search_model_versions(f"name='{FQ}.model_lic_emergence'")
latest = max(int(m.version) for m in mv)
client.set_registered_model_alias(f"{FQ}.model_lic_emergence", "champion", latest)
print(f"model_lic_emergence v{latest} → @champion")

# COMMAND ----------

# Batch score: latest snapshot per portfolio × accident quarter → gld_lic_ultimates
last_snap = df.sort_values("age").drop_duplicates(
    subset=["accident_quarter"] + [c for c in df.columns if c.startswith("p_")], keep="last")
pred = model.predict(last_snap[FEATURES])
out = last_snap.copy()
out["ml_ultimate"] = np.round(pred, 2)
port_cols = [c for c in out.columns if c.startswith("p_")]
out["portfolio_id"] = out[port_cols].idxmax(axis=1).str.replace("p_", "")
res = out[["portfolio_id", "accident_quarter", "age", "paid_to_date", "ml_ultimate"]].merge(
    snap[["portfolio_id", "accident_quarter", "reserving_ultimate"]], on=["portfolio_id", "accident_quarter"])
res["divergence_pct"] = ((res["ml_ultimate"] - res["reserving_ultimate"])
                         / res["reserving_ultimate"].where(res["reserving_ultimate"] != 0, 1) * 100).round(2)
res["model_version"] = latest
res = res.sort_values(["portfolio_id", "accident_quarter"]).reset_index(drop=True)

spark.createDataFrame(res, "portfolio_id string, accident_quarter string, age int, paid_to_date double, "
                           "ml_ultimate double, reserving_ultimate double, divergence_pct double, model_version int") \
    .write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{FQ}.gld_lic_ultimates")
spark.sql(f"COMMENT ON TABLE {FQ}.gld_lic_ultimates IS 'ML challenger view of ultimates (LightGBM on paid "
          f"development) vs the governed reserving view. NEVER the booked number — a divergence flag for the "
          f"results desk. Batch-scored; no live endpoint on the interactive path.'")
spark.sql(f"ALTER TABLE {FQ}.gld_lic_ultimates SET TBLPROPERTIES ('layer'='gold', 'demo'='ifrs17_workbench')")
print(f"gld_lic_ultimates: {len(res)} rows")
display(res[res["accident_quarter"] >= "2026Q1"])
