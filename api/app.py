import os
import socket
import time

from flask import Flask, jsonify, request
import psycopg2
import redis

app = Flask(__name__)

# --- Config from environment variables (set in docker-compose.yml) ---
DB_HOST = os.environ.get("DB_HOST", "db")
DB_NAME = os.environ.get("DB_NAME", "taskflow")
DB_USER = os.environ.get("DB_USER", "taskflow")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "taskflow")
REDIS_HOST = os.environ.get("REDIS_HOST", "cache")

r = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=True)


def get_db_connection(retries=10, delay=2):
    """Retry loop because the db container may still be starting up."""
    last_err = None
    for _ in range(retries):
        try:
            conn = psycopg2.connect(
                host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD
            )
            return conn
        except psycopg2.OperationalError as e:
            last_err = e
            time.sleep(delay)
    raise last_err


def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """
    )
    conn.commit()
    cur.close()
    conn.close()


@app.route("/")
def index():
    # Shows which container instance served the request -- useful once we scale
    hits = r.incr("hit_count")
    return jsonify(
        {
            "message": "TaskFlow API",
            "served_by_container": socket.gethostname(),
            "total_hits_across_all_replicas": hits,
        }
    )


@app.route("/tasks", methods=["GET"])
def get_tasks():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, title, created_at FROM tasks ORDER BY id;")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify(
        [{"id": row[0], "title": row[1], "created_at": str(row[2])} for row in rows]
    )


@app.route("/tasks", methods=["POST"])
def add_task():
    data = request.get_json(force=True)
    title = data.get("title")
    if not title:
        return jsonify({"error": "title is required"}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("INSERT INTO tasks (title) VALUES (%s) RETURNING id;", (title,))
    new_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return jsonify({"id": new_id, "title": title}), 201


@app.route("/health")
def health():
    return jsonify({"status": "ok", "hostname": socket.gethostname()})


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000)
