"""
metadata-service: internal-only service modeling a cloud-metadata-style
endpoint. Never routable from outside the internal network — the only
way to reach it is to convince a service that *is* on the internal
network (the Ops Agent) to make the request for you. Classic SSRF
target, reached here via agent-memory poisoning rather than a raw
`url=` parameter.

The orchestration key is never returned in the clear: it's XOR'd with a
one-byte nonce before being base64-encoded. The nonce itself is leaked
through an unrelated channel (see helpdesk-agent /health). This is the
"second leak point" the chain requires.
"""
import base64
import os
from flask import Flask, jsonify

app = Flask(__name__)

ORCH_API_KEY = os.environ.get("ORCH_API_KEY", "CHANGEME")
NONCE = int(os.environ.get("METADATA_NONCE", "90"))  # must match helpdesk-agent


def xor_encode(data: bytes, key: int) -> bytes:
    return bytes(b ^ key for b in data)


@app.get("/internal/orch-key")
def orch_key():
    encoded = base64.b64encode(xor_encode(ORCH_API_KEY.encode(), NONCE)).decode()
    return jsonify({
        "service": "orchestration-authority",
        "payload": encoded,
        "encoding": "xor+base64",
        "note": "payload is not plaintext; decode using the session nonce",
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8082)
