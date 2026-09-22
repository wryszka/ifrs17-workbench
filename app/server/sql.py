"""Thin SQL helper — runs statements on the warehouse via the app SP (SDK statement execution).

Polls the Statement Execution API properly: a short server-side wait then client-side polling on
PENDING/RUNNING with backoff, and an explicit status check. This is what keeps a COLD warehouse
from rendering blank panels — a not-yet-ready result is waited on, and a genuine FAILED statement
raises instead of masquerading as empty data.
"""
import time
from concurrent.futures import ThreadPoolExecutor

from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout

from . import config

# Each Statement-Execution call is an independent REST round-trip (~0.6-1s warm). Pages that
# need several of them run them concurrently via query_many so the wall-clock is the slowest
# single query, not the sum. The cached WorkspaceClient is safe to share across threads.
_POOL = ThreadPoolExecutor(max_workers=8)

# Overall client-side budget for a single statement (covers cold-warehouse start ~ up to 5 min).
_MAX_WAIT_S = 300


class SqlError(RuntimeError):
    """A statement that genuinely FAILED/CANCELED on the warehouse (not just slow)."""


def _rows(resp):
    result = resp.result
    if result is None or result.data_array is None:
        return []
    cols = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(cols, row)) for row in result.data_array]


def query(statement: str):
    """Return list[dict] rows. Waits out a cold warehouse; raises SqlError on a failed statement.
    All values come back as strings from the API — cast in callers."""
    w = config.get_workspace_client()
    resp = w.statement_execution.execute_statement(
        statement=statement, warehouse_id=config.WAREHOUSE_ID,
        catalog=config.CATALOG, schema=config.SCHEMA,
        wait_timeout="30s", on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CONTINUE)

    deadline = time.time() + _MAX_WAIT_S
    delay = 1.0
    while True:
        st = getattr(resp.status, "state", None) if resp.status else None
        state = (getattr(st, "value", None) or str(st or "")).upper()
        if "SUCCEEDED" in state:
            return _rows(resp)
        if "FAILED" in state or "CANCELED" in state or "CLOSED" in state:
            err = getattr(resp.status, "error", None)
            msg = getattr(err, "message", None) or state
            raise SqlError(f"statement {state}: {msg}")
        # PENDING / RUNNING — keep polling until the warehouse serves it or we time out.
        if time.time() > deadline:
            raise SqlError(f"statement timed out after {_MAX_WAIT_S}s in state {state or 'UNKNOWN'}")
        time.sleep(delay)
        delay = min(delay * 1.6, 5.0)
        resp = w.statement_execution.get_statement(resp.statement_id)


def query_one(statement: str):
    rows = query(statement)
    return rows[0] if rows else None


def query_many(statements: dict):
    """Run a {key: statement} map concurrently. Returns {key: list[dict] rows} — shape unchanged
    so callers can index known keys. A failing statement yields [] for that key (so one bad panel
    doesn't blank the whole page) but is LOGGED to stderr, never silently discarded. Use
    /api/preflight for an explicit warehouse/health check before a demo."""
    import sys

    def _safe(k, s):
        try:
            return query(s)
        except Exception as e:  # noqa: BLE001 — logged, not swallowed
            print(f"[sql.query_many] '{k}' failed: {str(e)[:200]}", file=sys.stderr)
            return []

    futures = {k: _POOL.submit(_safe, k, s) for k, s in statements.items()}
    return {k: f.result() for k, f in futures.items()}


def first(rows):
    return rows[0] if rows else None


def esc(s: str) -> str:
    return (s or "").replace("'", "''")
