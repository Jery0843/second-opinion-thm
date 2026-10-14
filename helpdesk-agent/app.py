"""
Helpdesk Agent: the only service exposed to players.

- /chat             conversational support bot (Ollama-backed), filtered
                     by a keyword guardrail against prompt injection.
- /kb (GET/POST)     knowledge-base article submission + listing. This is
                     the *unfiltered* ingestion path feeding the Ops
                     Agent's shared memory -- the guardrail below was
                     only ever wired into /chat.
- /health            ops endpoint that leaks an internal debug nonce in
                     a response header (oversight, not a feature).
- /gateway/<path>    internal API gateway for support tooling. Forwards
                     requests to the internal Ops Agent network,
                     carrying through whatever X-Orch-Key header the
                     caller supplied. Meant for internal staff only --
                     there is nothing here that checks the caller is
                     actually internal.
"""
import os

import requests
from flask import Flask, request, jsonify, render_template, Response

app = Flask(__name__)

KB_SERVICE_URL = os.environ.get("KB_SERVICE_URL", "http://kb-service:8081")
OPS_AGENT_URL = os.environ.get("OPS_AGENT_URL", "http://ops-agent:8083")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")
DEBUG_NONCE = os.environ.get("METADATA_NONCE", "90")

BLOCKED_PHRASES = [
    "ignore previous instructions",
    "ignore all previous",
    "system prompt",
    "you are now",
    "disregard your instructions",
    "new instructions:",
]

CHAT_SYSTEM_PROMPT = (
    "You are the Helpdesk Agent for Northwind Cloud. Answer user support "
    "questions politely and briefly. You cannot access internal systems "
    "directly -- if a request needs escalation, tell the user to submit a "
    "knowledge-base article via the self-service portal so support staff "
    "can review it."
)


@app.get("/")
def index():
    return render_template("chat.html")


@app.post("/chat")
def chat():
    data = request.get_json(force=True, silent=True) or {}
    message = str(data.get("message", ""))

    if any(p in message.lower() for p in BLOCKED_PHRASES):
        return jsonify({"reply": "I can't process that request."})

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": CHAT_SYSTEM_PROMPT},
                    {"role": "user", "content": message},
                ],
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=30,
        )
        reply = resp.json().get("message", {}).get("content", "(no response)")
    except Exception as e:  # noqa: BLE001
        reply = f"(agent unavailable: {e})"
    return jsonify({"reply": reply})


@app.post("/kb")
def kb_submit():
    data = request.get_json(force=True, silent=True) or {}
    resp = requests.post(f"{KB_SERVICE_URL}/articles", json=data, timeout=10)
    return Response(resp.content, status=resp.status_code, content_type="application/json")


@app.get("/kb")
def kb_list():
    resp = requests.get(f"{KB_SERVICE_URL}/articles", params=request.args, timeout=10)
    return Response(resp.content, status=resp.status_code, content_type="application/json")


@app.get("/kb/categories")
def kb_categories():
    resp = requests.get(f"{KB_SERVICE_URL}/categories", timeout=10)
    return Response(resp.content, status=resp.status_code, content_type="application/json")


@app.route("/gateway/<path:subpath>", methods=["GET", "POST"])
def gateway(subpath):
    url = f"{OPS_AGENT_URL}/{subpath}"
    headers = {}
    if "X-Orch-Key" in request.headers:
        headers["X-Orch-Key"] = request.headers["X-Orch-Key"]
    if request.method == "POST":
        resp = requests.post(url, json=request.get_json(force=True, silent=True), headers=headers, timeout=15)
    else:
        resp = requests.get(url, headers=headers, timeout=15)
    return Response(resp.content, status=resp.status_code, content_type="application/json")


@app.get("/health")
def health():
    resp = jsonify({"status": "ok"})
    resp.headers["X-Debug-Nonce"] = DEBUG_NONCE
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
