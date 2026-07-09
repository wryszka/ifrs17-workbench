"""Agent narration with a cache-first wrapper. Caches LLM NARRATION ONLY (never structured outputs).

USE_CACHE wraps the latency of the FM-backed agent endpoints; structured measurement/roll-forward
panels always read the engine tables / UC functions live. Hero cohorts are pre-warmed by the reset
job. Mirrors underwriting-workbench server/agents.py.
"""
import hashlib, json
from . import config, sql


def _key(endpoint: str, payload: dict) -> str:
    blob = json.dumps({"e": endpoint, "p": payload}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def _ensure_cache():
    sql.query(f"""CREATE TABLE IF NOT EXISTS {config.CACHE_TABLE}
                  (cache_key STRING, endpoint STRING, response STRING, created_ts TIMESTAMP) USING DELTA""")


def _read(key: str):
    row = sql.query_one(f"SELECT response FROM {config.CACHE_TABLE} WHERE cache_key = '{key}' LIMIT 1")
    return row["response"] if row else None


def _write(key: str, endpoint: str, response: str):
    r = sql.esc(response)
    sql.query(f"""MERGE INTO {config.CACHE_TABLE} t USING (SELECT '{key}' k) s ON t.cache_key = s.k
                  WHEN NOT MATCHED THEN INSERT (cache_key, endpoint, response, created_ts)
                  VALUES ('{key}', '{sql.esc(endpoint)}', '{r}', current_timestamp())""")


def _log_activity(group_id, agent, activity, tools, signal, reasoning):
    """Live AI-activity record — every agent interaction lands in the governed audit table."""
    try:
        sql.query(f"""INSERT INTO {config.fqn('gld_ai_activity')} VALUES (
            '{sql.esc(group_id or 'book_level')}', '{sql.esc(agent)}', '{sql.esc(activity)}',
            '{sql.esc(tools)[:900]}', '{sql.esc(signal)}', '{sql.esc(reasoning)[:900]}',
            current_timestamp())""")
    except Exception:
        pass


def ask_agent(question: str, custom_inputs: dict = None, use_cache: bool = None) -> dict:
    """Call the REAL tool-calling supervisor agent (ChatAgent). It autonomously calls the UC-function tools.
    Returns the grounded answer + the list of tools it actually called (proof of real tool use)."""
    if use_cache is None:
        use_cache = config.USE_CACHE
    endpoint = config.resolve_endpoint(config.EP_AGENT_SUBSTR)
    payload = {"messages": [{"role": "user", "content": question}], "custom_inputs": custom_inputs or {}}
    key = _key(endpoint, payload)
    if use_cache:
        try:
            _ensure_cache()
            hit = _read(key)
            if hit is not None:
                d = json.loads(hit)
                _log_activity((custom_inputs or {}).get("group_id"), "supervisor",
                              "supervisor_ask (cache hit)", " → ".join(d.get("tools", [])), "cache_hit",
                              d.get("text", ""))
                return {"text": d.get("text", ""), "tools": d.get("tools", []), "cache": "hit", "endpoint": endpoint}
        except Exception:
            pass
    try:
        import requests
        w = config.get_workspace_client()
        # ChatAgent endpoints accept the chat schema directly on the data-plane invocations path.
        host = config.workspace_host(); hdr = w.config._header_factory()
        if host and not host.startswith("http"):
            host = "https://" + host
        r = requests.post(f"{host}/serving-endpoints/{endpoint}/invocations",
                          headers={**hdr, "Content-Type": "application/json"}, json=payload, timeout=120)
        r.raise_for_status()
        out = r.json()
        msgs = out.get("messages") or []
        text = msgs[-1].get("content", "") if msgs else (out.get("content") or "")
        tools = [t.get("tool") for t in (out.get("custom_outputs") or {}).get("trace", [])]
    except Exception as e:
        return {"text": f"[agent unavailable: {str(e)[:160]}]", "tools": [], "cache": "error", "endpoint": endpoint}
    # Always persist (no TTL): cached mode fills on miss; live mode overwrites — so the cache is only
    # ever "recreated" by a live call or a demo reset, never by expiry.
    try:
        _ensure_cache()
        _write(key, endpoint, json.dumps({"text": text, "tools": tools}))
    except Exception:
        pass
    _log_activity((custom_inputs or {}).get("group_id"), "supervisor", "supervisor_ask (live)",
                  " → ".join(tools), "live", text)
    return {"text": text, "tools": tools, "cache": ("miss" if use_cache else "live"), "endpoint": endpoint}


def narrate(role: str, question: str, data: dict, use_cache: bool = None) -> dict:
    """Call a narrate-only agent endpoint (role = movement_narrator/disclosure_drafter/audit_evidence/cfo_brief)."""
    if use_cache is None:
        use_cache = config.USE_CACHE
    endpoint = config.resolve_endpoint(config.ROLE_SUBSTR.get(role, role))
    payload = {"role": role, "question": question,
               "data_json": json.dumps(data, default=str)}
    key = _key(endpoint, payload)
    if use_cache:
        try:
            _ensure_cache()
            hit = _read(key)
            if hit is not None:
                return {"text": hit, "cache": "hit", "endpoint": endpoint}
        except Exception:
            pass
    try:
        w = config.get_workspace_client()
        resp = w.serving_endpoints.query(name=endpoint, dataframe_records=[payload])
        preds = resp.predictions if hasattr(resp, "predictions") else resp.as_dict().get("predictions")
        text = preds[0] if preds else ""
    except Exception as e:
        return {"text": f"[narration unavailable: {str(e)[:140]}]", "cache": "error", "endpoint": endpoint}
    # Always persist (no TTL): live mode overwrites, cached mode fills on miss.
    try:
        _ensure_cache()
        _write(key, endpoint, text)
    except Exception:
        pass
    _log_activity((data or {}).get("group_id") if isinstance(data, dict) else None,
                  role, "narration (live)", "(narrate-only — structured findings passed in)", "live", text)
    return {"text": text, "cache": ("miss" if use_cache else "live"), "endpoint": endpoint}
