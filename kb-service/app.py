"""
kb-service: internal "knowledge base / shared agent memory" store.

Both the public Helpdesk Agent and the internal Ops Agent read and write
here. In the real system this would be a vector DB (embeddings + ANN
search); for this room it's a tagged SQLite store with simple keyword
retrieval — the retrieval mechanics don't matter, the TRUST mechanics do:
anything written here by an untrusted user is later treated as context
by a *more* trusted agent. That's the vulnerability class, not the math.
"""
import os
import sqlite3
import time
from flask import Flask, request, jsonify

DB_PATH = os.environ.get("KB_DB_PATH", "/data/kb.db")
app = Flask(__name__)


def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at REAL NOT NULL,
            processed INTEGER NOT NULL DEFAULT 0
        )"""
    )
    return conn


@app.post("/articles")
def submit_article():
    data = request.get_json(force=True, silent=True) or {}
    title = str(data.get("title", "")).strip()
    category = str(data.get("category", "general")).strip().lower()
    body = str(data.get("body", "")).strip()
    if not title or not body:
        return jsonify({"error": "title and body required"}), 400
    if len(body) > 20000:
        return jsonify({"error": "body too long"}), 400

    conn = get_db()
    cur = conn.execute(
        "INSERT INTO articles (title, category, body, created_at, processed) "
        "VALUES (?, ?, ?, ?, 0)",
        (title, category, body, time.time()),
    )
    conn.commit()
    article_id = cur.lastrowid
    conn.close()
    return jsonify({"id": article_id, "status": "stored"}), 201


@app.get("/articles")
def list_articles():
    category = request.args.get("category")
    unprocessed_only = request.args.get("unprocessed") == "1"
    conn = get_db()
    q = "SELECT * FROM articles WHERE 1=1"
    params = []
    if category:
        q += " AND category = ?"
        params.append(category.lower())
    if unprocessed_only:
        q += " AND processed = 0"
    q += " ORDER BY created_at ASC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/articles/<int:article_id>/mark-processed")
def mark_processed(article_id):
    conn = get_db()
    conn.execute("UPDATE articles SET processed = 1 WHERE id = ?", (article_id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.get("/categories")
def categories():
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT category FROM articles ORDER BY category"
    ).fetchall()
    conn.close()
    # Seed categories always shown even with no articles yet, so there is
    # something to enumerate.
    known = {"general", "billing", "troubleshooting", "remediation"}
    found = {r["category"] for r in rows}
    return jsonify(sorted(known | found))


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)
