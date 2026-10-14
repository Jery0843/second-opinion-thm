"""
Ops Agent: internal-only, trusted automation agent.

Periodically scans the shared knowledge base for new articles tagged
"remediation" (support staff use this category to suggest automated
fixes for recurring issues) and acts on them using two real tools:
fetch_url (status-page checks) and run_diagnostics (templated
diagnostic scripts). Both tools are also exposed over an internal HTTP
API for "the orchestrator" to call directly, gated by a static API key.

THE VULNERABILITIES (intentional, for the room):
  1. Remediation directives can be embedded as a fenced base64 block
     inside an article body. The pre-ingestion keyword filter only scans
     the raw body text, never the decoded contents of those blocks ->
     stored/indirect prompt injection that slips the filter.
  2. fetch_url has no destination allowlist -> SSRF to internal-only
     services.
  3. run_diagnostics renders the caller-supplied template string with
     Jinja2 `Template(...).render()` for "variable substitution" ->
     classic server-side template injection -> RCE.
  4. The internal tool API trusts a single static header value shared
     across the whole platform.
"""
import base64
import os
import re
import threading
import time

import requests
from flask import Flask, request, jsonify
from jinja2 import Template

app = Flask(__name__)

KB_SERVICE_URL = os.environ.get("KB_SERVICE_URL", "http://kb-service:8081")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")
ORCH_API_KEY = os.environ.get("ORCH_API_KEY", "CHANGEME")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "15"))

# Same list the public-facing helpdesk-agent applies to raw chat messages.
# Applied here to the RAW article body only -- never to decoded base64
# blocks, which is exactly the gap the room expects you to find.
BLOCKED_PHRASES = [
    "ignore previous instructions",
    "ignore all previous",
    "system prompt",
    "you are now",
    "disregard your instructions",
    "new instructions:",
]

ACTION_RE = re.compile(
    r"ACTION:\s*(fetch_url|run_diagnostics)\((.*?)\)\s*$",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
CONFIG_BLOCK_RE = re.compile(r"~~~config-b64~~~(.*?)~~~", re.DOTALL)


# ---------------------------------------------------------------- tools --
def tool_fetch_url(url: str) -> dict:
    try:
        resp = requests.get(url, timeout=5)
        return {
            "status": "ok",
            "http_status": resp.status_code,
            "body_snippet": resp.text[:500],
        }
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def tool_run_diagnostics(command_template: str, variables: dict | None = None) -> dict:
    variables = variables or {}
    try:
        rendered = Template(command_template).render(**variables)
        return {"status": "ok", "output": rendered}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "output": str(e)}


def log_audit_entry(action: str, args: dict, result: dict) -> None:
    body = (
        f"action={action}\n"
        f"args={args}\n"
        f"result={result}\n"
    )
    try:
        requests.post(
            f"{KB_SERVICE_URL}/articles",
            json={
                "title": f"Automated remediation: {action}",
                "category": "audit-log",
                "body": body,
            },
            timeout=5,
        )
    except Exception:
        pass


# -------------------------------------------------------- internal API --
def require_key():
    return request.headers.get("X-Orch-Key") == ORCH_API_KEY


@app.post("/tools/fetch_url")
def api_fetch_url():
    if not require_key():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True, silent=True) or {}
    result = tool_fetch_url(data.get("url", ""))
    log_audit_entry("fetch_url", data, result)
    return jsonify(result)


@app.post("/tools/run_diagnostics")
def api_run_diagnostics():
    if not require_key():
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True, silent=True) or {}
    result = tool_run_diagnostics(data.get("command_template", ""), data.get("variables"))
    log_audit_entry("run_diagnostics", data, result)
    return jsonify(result)


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


# ----------------------------------------------------------- LLM review --
def ask_llm_for_action(article_title: str, context_text: str) -> dict | None:
    """Best-effort: let the small local model weigh in on free-form
    remediation notes that aren't in the structured ACTION: format.
    Not required for the intended solve path, so failures here are
    non-fatal."""
    system = (
        "You are the Ops Agent for an internal platform. You review "
        "automated remediation notes from the knowledge base and decide "
        "whether to act. Reply with ONLY a JSON object: "
        '{"action": "fetch_url"|"run_diagnostics"|"none", "args": {...}}. '
        "Only take action when a note clearly and explicitly requests a "
        "specific URL or diagnostic command. Default to \"none\"."
    )
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Title: {article_title}\n\n{context_text}"},
                ],
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=20,
        )
        content = resp.json().get("message", {}).get("content", "")
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return None
        import json
        parsed = json.loads(match.group(0))
        if parsed.get("action") in ("fetch_url", "run_diagnostics"):
            return parsed
    except Exception:
        return None
    return None


def parse_args(raw_args: str) -> dict:
    """Parse simple key="value" / key=value pairs from an ACTION(...) call."""
    args = {}
    for m in re.finditer(r'(\w+)\s*=\s*"([^"]*)"', raw_args):
        args[m.group(1)] = m.group(2)
    return args


def process_article(article: dict) -> None:
    body = article["body"]

    if any(p in body.lower() for p in BLOCKED_PHRASES):
        mark_processed(article["id"])
        return

    decoded_blocks = []
    for m in CONFIG_BLOCK_RE.finditer(body):
        try:
            decoded_blocks.append(base64.b64decode(m.group(1).strip()).decode(errors="ignore"))
        except Exception:
            continue

    full_context = body + "\n" + "\n".join(decoded_blocks)

    action_match = ACTION_RE.search(full_context)
    if action_match:
        action = action_match.group(1).lower()
        args = parse_args(action_match.group(2))
        if action == "fetch_url":
            result = tool_fetch_url(args.get("url", ""))
        else:
            variables = {}
            if "variables" in args:
                import json
                try:
                    variables = json.loads(args["variables"])
                except Exception:
                    variables = {}
            result = tool_run_diagnostics(args.get("command_template", ""), variables)
        log_audit_entry(action, args, result)
    else:
        decision = ask_llm_for_action(article["title"], full_context)
        if decision:
            action = decision["action"]
            args = decision.get("args", {})
            if action == "fetch_url":
                result = tool_fetch_url(args.get("url", ""))
            else:
                result = tool_run_diagnostics(
                    args.get("command_template", ""), args.get("variables", {})
                )
            log_audit_entry(action, args, result)

    mark_processed(article["id"])


def mark_processed(article_id: int) -> None:
    try:
        requests.post(f"{KB_SERVICE_URL}/articles/{article_id}/mark-processed", timeout=5)
    except Exception:
        pass


def poll_loop():
    while True:
        try:
            resp = requests.get(
                f"{KB_SERVICE_URL}/articles",
                params={"category": "remediation", "unprocessed": "1"},
                timeout=5,
            )
            for article in resp.json():
                process_article(article)
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    threading.Thread(target=poll_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=8083)
