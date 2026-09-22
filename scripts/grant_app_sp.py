#!/usr/bin/env python3
"""Grant the app service principal everything it needs — one command after (re)creating the app.

Usage: python3 scripts/grant_app_sp.py [profile] [catalog] [schema] [warehouse_id] [genie_space_id] [dashboard_id]
Auto-discovers the app SP from the app, all ifrs17 serving endpoints, and the close/levers/reset jobs.
Idempotent. Mirrors docs/DEPLOY.md. NOTE: the SP is deliberately NOT added to
ifrs17_finance_controllers — the UC masking demo depends on that.
"""
import sys

from databricks.sdk import WorkspaceClient

prof = sys.argv[1] if len(sys.argv) > 1 else "DEV"
cat = sys.argv[2] if len(sys.argv) > 2 else "lr_dev_aws_us_catalog"
sch = sys.argv[3] if len(sys.argv) > 3 else "ifrs17_workbench"
wh = sys.argv[4] if len(sys.argv) > 4 else "a3b61648ea4809e3"
genie = sys.argv[5] if len(sys.argv) > 5 else ""
dash = sys.argv[6] if len(sys.argv) > 6 else ""

w = WorkspaceClient(profile=prof)
app = w.apps.get("ifrs17-workbench")
sp = app.service_principal_client_id
print(f"app SP: {sp}")


def sql(stmt):
    r = w.statement_execution.execute_statement(statement=stmt, warehouse_id=wh, wait_timeout="50s")
    st = r.status.state.value if r.status else "?"
    print(("✓" if st == "SUCCEEDED" else f"✗ {st}"), stmt[:90])


sql(f"GRANT USE CATALOG ON CATALOG {cat} TO `{sp}`")
# Least privilege: schema-level USE/EXECUTE/MODIFY, but NOT schema-wide SELECT. SELECT is granted per
# object below, EXCLUDING the unmasked base journal slv_manual_journal — the app reads the masked
# gov_journal_secure VIEW instead, and a view runs with its owner's rights, so the app SP never needs
# (and must not have) SELECT on the base. This is the real masking control: without it a schema-wide
# SELECT would let the SP read unmasked poster/approver identities directly.
sql(f"GRANT USE SCHEMA, EXECUTE, MODIFY ON SCHEMA {cat}.{sch} TO `{sp}`")
# Remove any prior schema-wide SELECT (revocable at the level it was granted) so base-table access is
# governed solely by the per-object grants below. Harmless if it was never granted.
sql(f"REVOKE SELECT ON SCHEMA {cat}.{sch} FROM `{sp}`")
sql(f"GRANT READ VOLUME, WRITE VOLUME ON VOLUME {cat}.{sch}.ifrs17_files TO `{sp}`")

EXCLUDE_SELECT = {"slv_manual_journal"}  # unmasked base journal — the SP reads gov_journal_secure instead


def grant_select(name, ttype):
    kw = "VIEW" if ttype == "VIEW" else "TABLE"  # views need ON VIEW; tables/MVs/streaming tables use TABLE
    for k in (kw, "TABLE", "VIEW"):  # be tolerant of object-type keyword differences across UC versions
        try:
            w.statement_execution.execute_statement(
                statement=f"GRANT SELECT ON {k} {cat}.{sch}.{name} TO `{sp}`",
                warehouse_id=wh, wait_timeout="50s")
            return True
        except Exception:
            continue
    return False


objs = w.statement_execution.execute_statement(
    statement=f"SELECT table_name, table_type FROM {cat}.information_schema.tables WHERE table_schema='{sch}'",
    warehouse_id=wh, wait_timeout="50s").result.data_array or []
granted, failed = 0, []
for name, ttype in objs:
    if name in EXCLUDE_SELECT:
        continue
    if grant_select(name, ttype):
        granted += 1
    else:
        failed.append(name)
print(f"✓ per-object SELECT granted on {granted} objects; SELECT WITHHELD on {sorted(EXCLUDE_SELECT)}"
      + (f"; ✗ failed: {failed}" if failed else ""))

eps = [e for e in w.serving_endpoints.list()
       if e.name.startswith("ifrs17-") or (e.name.startswith("agents_") and sch in e.name)]
for e in eps:
    try:
        w.api_client.do("PATCH", f"/api/2.0/permissions/serving-endpoints/{w.serving_endpoints.get(e.name).id}",
                        body={"access_control_list": [{"service_principal_name": sp, "permission_level": "CAN_QUERY"}]})
        print("✓ CAN_QUERY", e.name)
    except Exception as ex:  # noqa: BLE001
        print("✗", e.name, str(ex)[:80])

for sub in ("ifrs17_99_reset", "ifrs17_quarter_close", "ifrs17_demo_levers"):
    job = next((j for j in w.jobs.list(limit=100) if sub in (j.settings.name or "")), None)
    if job:
        w.api_client.do("PATCH", f"/api/2.0/permissions/jobs/{job.job_id}",
                        body={"access_control_list": [{"service_principal_name": sp, "permission_level": "CAN_MANAGE_RUN"}]})
        print(f"✓ CAN_MANAGE_RUN {sub}", job.job_id)

for obj, oid, lvl in (("genie", genie, "CAN_RUN"), ("dashboards", dash, "CAN_READ")):
    if not oid:
        continue
    try:
        w.api_client.do("PATCH", f"/api/2.0/permissions/{obj}/{oid}",
                        body={"access_control_list": [{"service_principal_name": sp, "permission_level": lvl}]})
        print(f"✓ {lvl} {obj} {oid}")
    except Exception as ex:  # noqa: BLE001
        print("✗", obj, str(ex)[:80])
print("done — restart the app once so endpoint-name caches refresh")
