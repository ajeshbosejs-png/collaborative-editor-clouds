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

# ✅ ABSOLUTE PATH FIX (for Render)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DOCUMENT_DIR = os.path.join(BASE_DIR, "documents")
VERSION_DIR = os.path.join(BASE_DIR, "versions")

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"

os.makedirs(DOCUMENT_DIR, exist_ok=True)
os.makedirs(VERSION_DIR, exist_ok=True)

# ---------------- APP INIT ---------------- #

app = Flask(__name__)
app.secret_key = SECRET_KEY
socketio = SocketIO(app)

# ✅ REDIS CLOUD FIX
redis_url = os.environ.get("REDIS_URL")

if redis_url:
    redis_client = redis.from_url(
        redis_url,
        decode_responses=True,
        ssl_cert_reqs=None
    )
else:
    redis_client = redis.Redis(
        host="localhost",
        port=6379,
        decode_responses=True
    )

active_users = {}
system_logs = []

def get_db():
    conn = sqlite3.connect(os.path.join(BASE_DIR, "database.db"))
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

# -------- REGISTER -------- #

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        raw_password = request.form.get("password")

        if len(raw_password) < 8:
            return render_template("register.html", error="Password must be at least 8 characters")

        password = generate_password_hash(raw_password)

        db = get_db()
        try:
            db.execute(
                "INSERT INTO users (username,password) VALUES (?,?)",
                (username,password)
            )
            db.commit()
            log_event(f"User registered: {username}")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            return render_template("register.html", error="Username already exists")

    return render_template("register.html")

# -------- LOGIN -------- #

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE username=?",
            (username,)
        ).fetchone()

        if not user or not check_password_hash(user["password"], password):
            return render_template("login.html", error="Invalid username or password")

        session["user"] = username
        session["token"] = create_jwt(username)
        log_event(f"{username} logged in")
        return redirect(url_for("dashboard"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

# -------- DASHBOARD -------- #

@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect(url_for("login"))

    documents = os.listdir(DOCUMENT_DIR)
    return render_template("dashboard.html", documents=documents)

@app.route("/create", methods=["POST"])
def create_document():
    doc_id = f"doc_{int(datetime.datetime.now().timestamp())}.txt"
    open(os.path.join(DOCUMENT_DIR, doc_id),"w").write("")
    log_event(f"Document created: {doc_id}")
    return redirect(url_for("editor", doc_id=doc_id.split(".")[0]))

@app.route("/delete/<filename>", methods=["POST"])
def delete_document(filename):
    path = os.path.join(DOCUMENT_DIR, filename)
    if os.path.exists(path):
        os.remove(path)
        log_event(f"Document deleted: {filename}")
    return redirect(url_for("dashboard"))

# -------- EDITOR -------- #

@app.route("/editor/<doc_id>")
def editor(doc_id):
    if "user" not in session:
        return redirect(url_for("login"))

    redis_content = redis_client.get(doc_id)

    if redis_content:
        content = redis_content
    else:
        filename = os.path.join(DOCUMENT_DIR, f"{doc_id}.txt")
        if os.path.exists(filename):
            with open(filename,"r") as f:
                content = f.read()
        else:
            content = ""

    return render_template(
        "editor.html",
        doc_id=doc_id,
        user=session["user"],
        content=content
    )

@app.route("/save/<doc_id>", methods=["POST"])
def save_document(doc_id):
    content = request.form.get("content","")

    filename = os.path.join(DOCUMENT_DIR, f"{doc_id}.txt")
    with open(filename,"w",encoding="utf-8") as f:
        f.write(content)

    timestamp = int(datetime.datetime.now().timestamp())
    version_file = os.path.join(VERSION_DIR, f"{doc_id}_{timestamp}.txt")
    with open(version_file,"w",encoding="utf-8") as vf:
        vf.write(content)

    redis_client.set(doc_id, content)
    log_event(f"Document saved: {doc_id} by {session.get('user')}")

    return "Saved"

# -------- HISTORY -------- #

@app.route("/history/<doc_id>")
def history(doc_id):
    version_files = []
    for file in sorted(os.listdir(VERSION_DIR), reverse=True):
        if file.startswith(doc_id+"_"):
            timestamp = file.replace(doc_id+"_","").replace(".txt","")
            dt = datetime.datetime.fromtimestamp(int(timestamp))
            version_files.append({
                "filename": file,
                "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S")
            })

    return render_template(
        "history.html",
        doc_id=doc_id,
        versions=version_files
    )

# -------- COMPARE -------- #

@app.route("/history/<doc_id>/compare")
def compare_versions(doc_id):
    file1 = request.args.get("file1")
    file2 = request.args.get("file2")

    path1 = os.path.join(VERSION_DIR,file1)
    path2 = os.path.join(VERSION_DIR,file2)

    if not os.path.exists(path1) or not os.path.exists(path2):
        return "Version file not found",404

    with open(path1,"r",encoding="utf-8") as f:
        text1 = f.readlines()
    with open(path2,"r",encoding="utf-8") as f:
        text2 = f.readlines()

    diff = difflib.HtmlDiff().make_file(text1,text2)
    return diff

# ---------------- SOCKET.IO ---------------- #

@socketio.on("join")
def on_join(data):
    join_room(data["doc"])
    active_users.setdefault(data["doc"],set()).add(data["user"])
    emit("users",list(active_users[data["doc"]]),room=data["doc"])

@socketio.on("edit")
def on_edit(data):
    redis_client.set(data["doc"],data["content"])
    emit("update",{"content":data["content"]},room=data["doc"],include_self=False)

@socketio.on("disconnect")
def on_disconnect():
    for doc in active_users:
        active_users[doc].discard(session.get("user"))
        emit("users",list(active_users[doc]),room=doc)

# ---------------- MAIN ---------------- #

if __name__ == "__main__":
    socketio.run(app, debug=True)
