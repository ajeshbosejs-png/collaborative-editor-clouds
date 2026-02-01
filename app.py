import os
import json
import jwt
import datetime
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_socketio import SocketIO, emit, join_room
import redis
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
import difflib

# ---------------- CONFIG ---------------- #
SECRET_KEY = "supersecretkey"
JWT_SECRET = "jwtsecretkey"
DOCUMENT_DIR = "documents"
VERSION_DIR = "versions"

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

os.makedirs(DOCUMENT_DIR, exist_ok=True)
os.makedirs(VERSION_DIR, exist_ok=True)

# ---------------- APP INIT ---------------- #
app = Flask(__name__)
app.secret_key = SECRET_KEY
socketio = SocketIO(app)

# ✅ REDIS CONNECTION (RENDER SAFE)
redis_url = os.environ.get("REDIS_URL")

if not redis_url:
    raise ValueError("REDIS_URL not set in environment!")

redis_client = redis.from_url(
    redis_url,
    decode_responses=True,
    ssl_cert_reqs=None
)

active_users = {}
system_logs = []

def get_db():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn

# ---------------- HELPERS ---------------- #
def log_event(event):
    system_logs.append({
        "time": datetime.datetime.now().strftime("%H:%M:%S"),
        "event": event
    })

def create_jwt(username):
    payload = {
        "user": username,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=2)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

# ---------------- ROUTES ---------------- #

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        raw_password = request.form.get("password")

        if len(raw_password) < 8:
            return render_template("register.html", error="Password must be at least 8 characters")

        password = generate_password_hash(raw_password)

        db = get_db()
        try:
            db.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
            db.commit()
            log_event(f"User registered: {username}")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            return render_template("register.html", error="Username already exists")

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

        if not user or not check_password_hash(user["password"], password):
            return render_template("login.html", error="Invalid credentials")

        session["user"] = username
        session["token"] = create_jwt(username)
        return redirect(url_for("dashboard"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect(url_for("login"))

    documents = os.listdir(DOCUMENT_DIR)
    return render_template("dashboard.html", documents=documents)

@app.route("/create", methods=["POST"])
def create_document():
    doc_id = f"doc_{int(datetime.datetime.now().timestamp())}.txt"
    open(os.path.join(DOCUMENT_DIR, doc_id), "w").write("")
    return redirect(url_for("editor", doc_id=doc_id.split(".")[0]))

@app.route("/delete/<filename>", methods=["POST"])
def delete_document(filename):
    path = os.path.join(DOCUMENT_DIR, filename)
    if os.path.exists(path):
        os.remove(path)
    return redirect(url_for("dashboard"))

@app.route("/editor/<doc_id>")
def editor(doc_id):
    if "user" not in session:
        return redirect(url_for("login"))

    content = redis_client.get(doc_id) or ""

    return render_template("editor.html", doc_id=doc_id, user=session["user"], content=content)

@app.route("/save/<doc_id>", methods=["POST"])
def save_document(doc_id):
    content = request.form.get("content", "")

    with open(os.path.join(DOCUMENT_DIR, f"{doc_id}.txt"), "w", encoding="utf-8") as f:
        f.write(content)

    redis_client.set(doc_id, content)
    return "Saved"

@app.route("/history/<doc_id>")
def history(doc_id):
    version_files = []
    for file in sorted(os.listdir(VERSION_DIR), reverse=True):
        if file.startswith(doc_id + "_"):
            version_files.append(file)
    return render_template("history.html", doc_id=doc_id, versions=version_files)

# ---------------- SOCKET.IO ---------------- #

@socketio.on("join")
def on_join(data):
    join_room(data["doc"])

@socketio.on("edit")
def on_edit(data):
    redis_client.set(data["doc"], data["content"])
    emit("update", {"content": data["content"]}, room=data["doc"], include_self=False)

# ---------------- MAIN ---------------- #

if __name__ == "__main__":
    socketio.run(app, debug=True)
