import os
import json
import sqlite3
import secrets
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, request, redirect, url_for, session, render_template, jsonify, abort, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path("/tmp/cybertrace_data")
PHOTO_DIR = DATA_DIR / "photos"
DATA_DIR.mkdir(exist_ok=True, parents=True)
PHOTO_DIR.mkdir(exist_ok=True, parents=True)
DB_PATH = DATA_DIR / "cybertrace.sqlite3"
from flask import render_template

app = Flask(__name__)
@app.route("/share")
def user_portal():
    share_id = session.get("public_share_id")
    # Recover stale cookies when the browser has an old session but this
    # project folder/database does not contain its matching visitor row.
    if share_id:
        with db() as con:
            existing = con.execute(
                "SELECT 1 FROM visitor_devices WHERE share_id=?", (share_id,)
            ).fetchone()
        if not existing:
            session.pop("public_share_id", None)
            share_id = None
    if not share_id:
        share_id = secrets.token_urlsafe(12)
        session["public_share_id"] = share_id
        now = utc_now()
        # IP is ordinary request metadata; disclose it in the portal notice and admin.
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
        ua = request.user_agent.string[:500]
        with db() as con:
            con.execute("""INSERT OR REPLACE INTO visitor_devices
                (share_id,first_seen,last_seen,ip_address,user_agent,location_consent,camera_consent)
                VALUES(?,?,?,?,?,0,0)""", (share_id, now, now, ip, ua))
        log_event("device_opened_share_link", f"Device opened /share · IP {ip} · session {share_id[:8]}")
    else:
        now = utc_now()
        # A fresh page load starts a new consent interaction. Do not let old
        # consent flags survive a reload while the portal UI says "not requested".
        with db() as con:
            con.execute("""UPDATE visitor_devices
                           SET last_seen=?, location_consent=0, camera_consent=0,
                               stopped_at=?
                           WHERE share_id=?""", (now, now, share_id))
        log_event("portal_reopened", f"Portal reopened; previous sharing stopped · session {share_id[:8]}")
    return render_template("user_portal.html")
# Keep Flask session cookies valid across server restarts. A random key on
# every launch invalidates the browser's signed session cookie.
SECRET_KEY_FILE = DATA_DIR / "flask_secret.key"
configured_secret = os.environ.get("CYBERTRACE_SECRET")
if configured_secret:
    app.secret_key = configured_secret
else:
    try:
        app.secret_key = SECRET_KEY_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        persistent_secret = secrets.token_hex(32)
        SECRET_KEY_FILE.write_text(persistent_secret, encoding="utf-8")
        app.secret_key = persistent_secret
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=8 * 1024 * 1024,
)

# Set CYBERTRACE_PASSWORD before starting. No default password is provided.
PASSWORD_HASH = generate_password_hash(os.environ.get("CYBERTRACE_PASSWORD", ""))
if not os.environ.get("CYBERTRACE_PASSWORD"):
    print("WARNING: Set CYBERTRACE_PASSWORD before using the dashboard.")

ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            accuracy REAL,
            note TEXT,
            share_id TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            detail TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS visitor_devices (
            share_id TEXT PRIMARY KEY,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            ip_address TEXT NOT NULL,
            user_agent TEXT NOT NULL,
            device_name TEXT NOT NULL DEFAULT '',
            location_consent INTEGER NOT NULL DEFAULT 0,
            camera_consent INTEGER NOT NULL DEFAULT 0,
            latitude REAL,
            longitude REAL,
            accuracy REAL,
            location_updated_at TEXT,
            stopped_at TEXT
        )""")
        # Safe additive migrations preserve the existing SQLite database and rows.
        location_columns = {r["name"] for r in con.execute("PRAGMA table_info(locations)").fetchall()}
        if "share_id" not in location_columns:
            con.execute("ALTER TABLE locations ADD COLUMN share_id TEXT")
        device_columns = {r["name"] for r in con.execute("PRAGMA table_info(visitor_devices)").fetchall()}
        if "device_name" not in device_columns:
            con.execute("ALTER TABLE visitor_devices ADD COLUMN device_name TEXT NOT NULL DEFAULT ''")
        con.execute("""CREATE TABLE IF NOT EXISTS rtc_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            share_id TEXT NOT NULL,
            sender TEXT NOT NULL,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
init_db()

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def log_event(event_type, detail=""):
    with db() as con:
        con.execute("INSERT INTO events(created_at,event_type,detail) VALUES(?,?,?)",
                    (utc_now(), event_type, detail[:300]))
        # Buang event lama sungguhan; hanya 20 event terbaru yang disimpan.
        con.execute("""DELETE FROM events WHERE id NOT IN
            (SELECT id FROM events ORDER BY id DESC LIMIT 20)""")

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(self), camera=(self), microphone=()"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/")
def education_home():
    share_id = session.get("public_share_id")
    with db() as con:
        exists = con.execute("SELECT 1 FROM visitor_devices WHERE share_id=?", (share_id,)).fetchone() if share_id else None
    if not exists:
        share_id = secrets.token_urlsafe(12)
        session["public_share_id"] = share_id
        now = utc_now()
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown").split(",")[0].strip()
        ua = request.user_agent.string[:500]
        with db() as con:
            con.execute("""INSERT OR REPLACE INTO visitor_devices
                (share_id,first_seen,last_seen,ip_address,user_agent,device_name,location_consent,camera_consent)
                VALUES(?,?,?,?,?,'',0,0)""", (share_id, now, now, ip, ua))
        log_event("education_home_opened", f"Education home opened · session {share_id[:8]}")
    return render_template("education_home.html")

@app.route("/modul")
def education_modules():
    return render_template("education_modules.html", modules=education_module_data())

@app.route("/modul/<slug>")
def education_module_detail(slug):
    modules = education_module_data()
    item = next((m for m in modules if m["slug"] == slug), None)
    if not item:
        abort(404)
    return render_template("education_detail.html", module=item)

def education_module_data():
    return [
      {"slug":"kelas-1-teknologi-sekitar-kita","grade":"Kelas 1","title":"Teknologi di Sekitar Kita","topic":"Mengenal teknologi","duration":"2 x 35 menit","description":"Mengenali benda teknologi dan manfaatnya dalam kehidupan sehari-hari.",
       "objectives":["Siswa menyebutkan contoh teknologi di rumah dan sekolah.","Siswa menjelaskan kegunaan sederhana dari perangkat teknologi.","Siswa menunjukkan cara menggunakan perangkat dengan hati-hati."],
       "materials":["Kartu gambar benda teknologi","Papan tulis","Contoh perangkat (opsional)"],
       "steps":["Pembuka: guru mengajak siswa menyebutkan alat yang membantu kegiatan sehari-hari.","Eksplorasi: kelompok mencocokkan gambar perangkat dengan kegunaannya.","Diskusi: siswa menjelaskan satu alat yang pernah digunakan.","Penutup: siswa menggambar satu teknologi dan manfaatnya."],
       "worksheet":["Gambar tiga benda teknologi yang kamu kenal.","Pasangkan benda dengan kegunaannya.","Lengkapi kalimat: Teknologi membantu kita untuk ____."],
       "quiz":["Apa kegunaan komputer?","Sebutkan satu aturan saat menggunakan perangkat."],"answers":["Membantu mengolah informasi dan menyelesaikan berbagai tugas.","Gunakan dengan hati-hati dan ikuti arahan guru/orang tua."]},
      {"slug":"kelas-2-perangkat-digital","grade":"Kelas 2","title":"Mengenal Perangkat Digital","topic":"Perangkat dan aplikasi","duration":"2 x 35 menit","description":"Mengenal perangkat digital, bagian dasar, dan aplikasi untuk belajar.",
       "objectives":["Siswa membedakan perangkat digital dan bukan perangkat digital.","Siswa mengenali layar, tombol, papan ketik, dan tetikus.","Siswa memahami aplikasi sebagai program untuk melakukan tugas."],
       "materials":["Kartu perangkat","Komputer/laptop demonstrasi","Lembar kerja"],
       "steps":["Amati beberapa gambar benda.","Guru menunjukkan bagian dasar komputer.","Siswa mengelompokkan contoh perangkat dan kegunaannya.","Refleksi tentang penggunaan perangkat secara seimbang."],
       "worksheet":["Lingkari perangkat digital.","Tarik garis bagian komputer ke namanya.","Tuliskan satu aplikasi yang dapat membantu belajar."],
       "quiz":["Apa fungsi papan ketik?","Apa yang dimaksud aplikasi?"],"answers":["Memasukkan huruf, angka, dan perintah.","Program yang membantu pengguna melakukan tugas tertentu."]},
      {"slug":"kelas-3-internet-informasi","grade":"Kelas 3","title":"Internet dan Mencari Informasi","topic":"Internet dasar","duration":"2 x 35 menit","description":"Memahami internet secara sederhana dan belajar menilai informasi dengan bantuan guru.",
       "objectives":["Siswa menjelaskan internet sebagai jaringan yang menghubungkan perangkat.","Siswa menyebutkan contoh manfaat internet.","Siswa memahami bahwa informasi perlu diperiksa bersama orang dewasa."],
       "materials":["Ilustrasi jaringan","Kartu contoh informasi","Proyektor (opsional)"],
       "steps":["Guru menjelaskan analogi jaringan.","Siswa mengidentifikasi kegiatan yang memerlukan internet.","Kelompok memilah contoh informasi yang jelas sumbernya dan yang belum jelas.","Guru menekankan bertanya kepada orang dewasa tepercaya."],
       "worksheet":["Gambarkan dua manfaat internet.","Tuliskan siapa yang dapat membantu memeriksa informasi.","Beri tanda pada informasi yang mencantumkan sumber."],
       "quiz":["Apakah semua informasi di internet pasti benar?","Apa yang harus dilakukan jika ragu?"],"answers":["Tidak. Informasi perlu diperiksa.","Bertanya kepada guru/orang tua dan memeriksa sumber tepercaya."]},
      {"slug":"kelas-4-aman-di-dunia-digital","grade":"Kelas 4","title":"Aman dan Sopan di Dunia Digital","topic":"Keamanan digital","duration":"2 x 35 menit","description":"Mengenal kata sandi kuat, privasi, dan etika berkomunikasi.",
       "objectives":["Siswa memahami informasi pribadi yang tidak boleh dibagikan sembarangan.","Siswa mengenali ciri kata sandi yang lebih kuat.","Siswa mempraktikkan komunikasi yang sopan."],
       "materials":["Kartu situasi","Contoh kata sandi fiktif","LKPD"],
       "steps":["Diskusikan contoh informasi pribadi.","Bandingkan contoh kata sandi fiktif tanpa memakai kata sandi asli.","Bermain peran merespons pesan dengan sopan.","Buat tiga aturan aman berinternet."],
       "worksheet":["Tandai informasi pribadi: alamat rumah, warna favorit, kata sandi, nama hewan.","Pilih contoh kata sandi fiktif yang lebih kuat.","Tuliskan respons sopan pada situasi diskusi daring."],
       "quiz":["Mengapa kata sandi tidak boleh dibagikan?","Apa tindakan saat menerima pesan mencurigakan?"],"answers":["Agar akun dan informasi tetap terlindungi.","Jangan klik tautan atau membalas; tunjukkan kepada guru/orang tua."]},
      {"slug":"kelas-5-logika-dan-ai","grade":"Kelas 5","title":"Logika, Algoritma, dan Pengenalan AI","topic":"Berpikir komputasional","duration":"3 x 35 menit","description":"Menyusun instruksi berurutan dan mengenal AI melalui contoh sederhana.",
       "objectives":["Siswa menyusun langkah-langkah untuk menyelesaikan tugas.","Siswa menemukan kesalahan urutan instruksi.","Siswa menjelaskan bahwa AI adalah teknologi yang membantu mengenali pola atau menghasilkan keluaran."],
       "materials":["Kartu instruksi","Kertas kotak-kotak","Contoh keluaran AI yang telah ditinjau guru"],
       "steps":["Permainan memberi instruksi arah secara berurutan.","Siswa menyusun algoritma untuk kegiatan sederhana.","Uji instruksi dan perbaiki langkah yang keliru.","Guru mengenalkan AI dan pentingnya memeriksa hasilnya."],
       "worksheet":["Susun langkah membuat teh/susu secara berurutan.","Buat algoritma menuju titik tujuan di grid.","Tuliskan satu hal yang perlu diperiksa dari jawaban AI."],
       "quiz":["Apa itu algoritma?","Apakah hasil AI selalu benar?"],"answers":["Urutan instruksi untuk menyelesaikan tugas.","Tidak; hasilnya perlu diperiksa manusia."]},
      {"slug":"kelas-6-proyek-digital","grade":"Kelas 6","title":"Proyek Digital dan Jejak Digital","topic":"Kewargaan digital","duration":"3 x 35 menit","description":"Merancang karya digital sederhana dan memahami jejak digital serta tanggung jawab saat berbagi.",
       "objectives":["Siswa merencanakan proyek digital sederhana.","Siswa menjelaskan bahwa aktivitas daring dapat meninggalkan jejak digital.","Siswa menerapkan prinsip izin, privasi, dan atribusi."],
       "materials":["Kertas perencanaan","Perangkat sekolah (opsional)","Rubrik proyek"],
       "steps":["Pilih masalah sederhana di lingkungan sekolah.","Rancang poster/presentasi digital dengan informasi yang benar.","Periksa izin penggunaan gambar dan cantumkan sumber.","Presentasikan karya dan refleksikan jejak digital."],
       "worksheet":["Tuliskan tujuan proyek dan audiensnya.","Buat daftar aset yang digunakan dan sumbernya.","Periksa apakah karya memuat data pribadi."],
       "quiz":["Apa yang dimaksud jejak digital?","Mengapa perlu izin saat memakai foto orang lain?"],"answers":["Rekaman aktivitas yang ditinggalkan saat menggunakan layanan digital.","Untuk menghormati privasi dan hak orang tersebut."]}
    ]


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not PASSWORD_HASH:
            return render_template("login.html", error="Set CYBERTRACE_PASSWORD in your terminal first.")
        password = request.form.get("password", "")
        if check_password_hash(PASSWORD_HASH, password):
            session.clear()
            session["authenticated"] = True
            session["csrf"] = secrets.token_urlsafe(24)
            log_event("login", "Dashboard login successful")
            return redirect(url_for("index"))
        log_event("login_failed", "Invalid password")
        return render_template("login.html", error="Password salah.")
    return render_template("login.html", error=None)

@app.route("/logout", methods=["POST"])
@login_required
def logout():
    require_csrf()
    session.clear()
    return redirect(url_for("login"))

def require_csrf():
    token = request.headers.get("X-CSRF-Token") or request.form.get("csrf")
    if not token or not secrets.compare_digest(token, session.get("csrf", "")):
        abort(403)

@app.route("/admin")
@login_required
def index():
    with db() as con:
        latest = con.execute("SELECT * FROM locations ORDER BY id DESC LIMIT 1").fetchone()
        locations = con.execute("SELECT * FROM locations ORDER BY id DESC LIMIT 100").fetchall()
        events = con.execute("SELECT * FROM events ORDER BY id DESC LIMIT 20").fetchall()
    return render_template("index.html", latest=latest, locations=locations, events=events,
                           csrf=session.get("csrf", ""))


@app.route("/api/share/consent", methods=["POST"])
def share_consent():
    payload = request.get_json(silent=True) or {}
    if payload.get("resource") not in ("location", "camera"):
        return jsonify(error="Resource consent tidak valid"), 400
    share_id = session.get("public_share_id")
    if not share_id:
        return jsonify(error="Sesi portal tidak ditemukan; buka ulang halaman berbagi"), 400
    field = "location_consent" if payload["resource"] == "location" else "camera_consent"
    now = utc_now()
    with db() as con:
        cur = con.execute(f"UPDATE visitor_devices SET {field}=1,last_seen=?,stopped_at=NULL WHERE share_id=?",
                          (now, share_id))
        if cur.rowcount != 1:
            return jsonify(error="Sesi portal tidak cocok. Muat ulang halaman dari alamat yang sama."), 409
    log_event("device_consent", f"{payload['resource']} permission approved in portal · session {share_id[:8]}")
    return jsonify(ok=True)



@app.route("/api/share/profile", methods=["POST"])
def share_profile():
    share_id = session.get("public_share_id")
    if not share_id:
        return jsonify(error="Sesi portal tidak ditemukan"), 400
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("device_name", "")).strip()[:60]
    now = utc_now()
    with db() as con:
        con.execute("UPDATE visitor_devices SET device_name=?,last_seen=? WHERE share_id=?",
                    (name, now, share_id))
    return jsonify(ok=True, device_name=name)

@app.route("/api/share/heartbeat", methods=["POST"])
def share_heartbeat():
    share_id = session.get("public_share_id")
    if not share_id:
        return jsonify(error="Sesi portal tidak ditemukan"), 400
    now = utc_now()
    with db() as con:
        con.execute("UPDATE visitor_devices SET last_seen=? WHERE share_id=?", (now, share_id))
    return jsonify(ok=True)

@app.route("/api/share/location", methods=["POST"])
def save_shared_location():
    share_id = session.get("public_share_id")
    if not share_id:
        return jsonify(error="Sesi portal tidak ditemukan"), 400
    with db() as con:
        device = con.execute("SELECT location_consent FROM visitor_devices WHERE share_id=?", (share_id,)).fetchone()
    if not device or not device["location_consent"]:
        return jsonify(error="Persetujuan lokasi belum dicatat"), 403
    payload = request.get_json(silent=True) or {}
    try:
        lat = float(payload["latitude"]); lon = float(payload["longitude"])
        accuracy = float(payload.get("accuracy", payload.get("accuracy_m", 0)))
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Koordinat tidak valid"), 400
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= accuracy <= 10000000):
        return jsonify(error="Koordinat di luar rentang"), 400
    now = utc_now()
    with db() as con:
        con.execute("INSERT INTO locations(created_at,latitude,longitude,accuracy,note,share_id) VALUES(?,?,?,?,?,?)",
                    (now, lat, lon, accuracy, f"Shared portal {share_id[:8]} · consented", share_id))
        con.execute("""UPDATE visitor_devices SET latitude=?,longitude=?,accuracy=?,location_updated_at=?,last_seen=?
                       WHERE share_id=?""", (lat, lon, accuracy, now, now, share_id))
    log_event("shared_location_saved", f"Share session {share_id[:8]} · accuracy {accuracy:.1f} m")
    return jsonify(ok=True, received=True, received_at=now)

@app.route("/api/share/stop", methods=["POST"])
def stop_shared_location():
    share_id = session.get("public_share_id")
    if share_id:
        with db() as con:
            con.execute("UPDATE visitor_devices SET location_consent=0,camera_consent=0,stopped_at=?,last_seen=? WHERE share_id=?",
                        (utc_now(), utc_now(), share_id))
        log_event("device_sharing_stopped", f"User stopped sharing · session {share_id[:8]}")
    return jsonify(ok=True, stopped=True)


def _save_rtc_signal(share_id, sender, kind, payload):
    # Keep signaling data short-lived and bounded; no video is stored by this endpoint.
    with db() as con:
        con.execute("DELETE FROM rtc_signals WHERE created_at < ?",
                    ((datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="seconds"),))
        con.execute("INSERT INTO rtc_signals(share_id,sender,kind,payload,created_at) VALUES(?,?,?,?,?)",
                    (share_id, sender, kind, json.dumps(payload)[:200000], utc_now()))

@app.route("/api/share/rtc", methods=["GET", "POST"])
def share_rtc():
    share_id = session.get("public_share_id")
    if not share_id:
        return jsonify(error="Sesi portal tidak ditemukan"), 400
    with db() as con:
        device = con.execute("SELECT camera_consent FROM visitor_devices WHERE share_id=?", (share_id,)).fetchone()
    if not device or not device["camera_consent"]:
        return jsonify(error="Izin kamera belum tercatat"), 403
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        kind = payload.get("kind")
        if kind not in ("offer", "candidate", "bye"):
            return jsonify(error="Jenis sinyal tidak valid"), 400
        _save_rtc_signal(share_id, "user", kind, payload.get("payload", {}))
        return jsonify(ok=True)
    after = request.args.get("after", "0", type=int)
    with db() as con:
        rows = con.execute("""SELECT id,kind,payload FROM rtc_signals
            WHERE share_id=? AND sender='admin' AND id>? ORDER BY id LIMIT 50""",
            (share_id, after)).fetchall()
    return jsonify([{"id": r["id"], "kind": r["kind"], "payload": json.loads(r["payload"])} for r in rows])

@app.route("/api/admin/rtc/<share_id>", methods=["GET", "POST"])
@login_required
def admin_rtc(share_id):
    with db() as con:
        device = con.execute("SELECT camera_consent FROM visitor_devices WHERE share_id=?", (share_id,)).fetchone()
    if not device:
        return jsonify(error="Perangkat tidak ditemukan"), 404
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        kind = payload.get("kind")
        if kind not in ("answer", "candidate", "bye"):
            return jsonify(error="Jenis sinyal tidak valid"), 400
        _save_rtc_signal(share_id, "admin", kind, payload.get("payload", {}))
        return jsonify(ok=True)
    after = request.args.get("after", "0", type=int)
    with db() as con:
        rows = con.execute("""SELECT id,kind,payload FROM rtc_signals
            WHERE share_id=? AND sender='user' AND id>? ORDER BY id LIMIT 50""",
            (share_id, after)).fetchall()
    return jsonify([{"id": r["id"], "kind": r["kind"], "payload": json.loads(r["payload"])} for r in rows])

@app.route("/api/admin/devices", methods=["GET"])
@login_required
def admin_devices():
    now = datetime.now(timezone.utc)
    # Hapus sesi perangkat lama/offline agar daftar utama tidak menumpuk.
    # Sesi yang berhenti atau tidak heartbeat selama 2 menit dianggap usang.
    cutoff = (now - timedelta(seconds=120)).isoformat(timespec="seconds")
    with db() as con:
        con.execute("DELETE FROM visitor_devices WHERE last_seen < ?", (cutoff,))
        rows = con.execute("""SELECT share_id,first_seen,last_seen,ip_address,user_agent,device_name,
            location_consent,camera_consent,latitude,longitude,accuracy,location_updated_at,stopped_at
            FROM visitor_devices ORDER BY last_seen DESC LIMIT 100""").fetchall()
    results = []
    for row in rows:
        d = dict(row)
        try:
            age = (now - datetime.fromisoformat(d["last_seen"])).total_seconds()
        except (TypeError, ValueError):
            age = 999999
        d["online"] = age <= 45
        d["seconds_since_seen"] = max(0, int(age))
        d["device_label"] = d.get("device_name") or "Perangkat tanpa nama"
        ua = d["user_agent"] or ""
        d["browser_guess"] = "Edge" if "Edg/" in ua else "Chrome" if "Chrome/" in ua else "Firefox" if "Firefox/" in ua else "Safari" if "Safari/" in ua and "Chrome/" not in ua else "Browser tidak diketahui"
        d["os_guess"] = "Windows" if "Windows" in ua else "Android" if "Android" in ua else "iOS/iPadOS" if ("iPhone" in ua or "iPad" in ua) else "macOS" if "Mac OS" in ua else "Linux" if "Linux" in ua else "OS tidak diketahui"
        results.append(d)
    return jsonify(results)

@app.route("/api/admin/device-activity", methods=["GET"])
@login_required
def admin_device_activity():
    share_id = request.args.get("share_id", "").strip()
    if not share_id:
        return jsonify(error="share_id diperlukan"), 400
    short_id = share_id[:8]
    with db() as con:
        rows = con.execute("""SELECT created_at,event_type,detail FROM events
            WHERE detail LIKE ? ORDER BY id DESC LIMIT 20""",
            (f"%{short_id}%",)).fetchall()
    return jsonify([dict(row) for row in rows])


@app.route("/api/admin/location-history", methods=["GET"])
@login_required
def admin_location_history():
    share_id = request.args.get("share_id", "").strip()
    if not share_id:
        return jsonify(error="share_id diperlukan"), 400
    with db() as con:
        rows = con.execute("""SELECT id,created_at,latitude,longitude,accuracy,note
            FROM locations WHERE share_id=? ORDER BY id DESC LIMIT 100""", (share_id,)).fetchall()
    return jsonify([dict(row) for row in rows])

@app.route("/api/admin/events", methods=["GET"])
@login_required
def admin_events():
    # Hapus event lama dari database, bukan hanya menyembunyikannya di UI.
    with db() as con:
        con.execute("""DELETE FROM events WHERE id NOT IN
            (SELECT id FROM events ORDER BY id DESC LIMIT 20)""")
        rows = con.execute("""SELECT id,created_at,event_type,detail FROM events
            ORDER BY id DESC LIMIT 6""").fetchall()
    return jsonify([dict(row) for row in rows])

@app.route("/api/location", methods=["POST"])
@login_required
def save_location():
    require_csrf()
    payload = request.get_json(silent=True) or {}
    try:
        lat = float(payload["latitude"])
        lon = float(payload["longitude"])
        accuracy = float(payload.get("accuracy", 0))
    except (KeyError, TypeError, ValueError):
        return jsonify(error="Koordinat tidak valid"), 400
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= accuracy <= 10_000_000):
        return jsonify(error="Koordinat di luar rentang"), 400
    note = str(payload.get("note", "Browser geolocation"))[:120]
    with db() as con:
        con.execute("INSERT INTO locations(created_at,latitude,longitude,accuracy,note) VALUES(?,?,?,?,?)",
                    (utc_now(), lat, lon, accuracy, note))
    log_event("location_saved", f"Accuracy: {accuracy:.1f} m")
    return jsonify(ok=True)

@app.route("/api/share/snapshot", methods=["POST"])
def share_camera_snapshot():
    share_id = session.get("public_share_id")
    if not share_id:
        return jsonify(error="Sesi portal tidak ditemukan"), 400
    with db() as con:
        device = con.execute("SELECT camera_consent FROM visitor_devices WHERE share_id=?", (share_id,)).fetchone()
    if not device or not device["camera_consent"]:
        return jsonify(error="Izin kamera belum diberikan di portal"), 403
    image = request.files.get("image")
    if not image:
        return jsonify(error="Tidak ada gambar"), 400
    # Only accept JPEG from this one-shot capture endpoint.
    if image.mimetype != "image/jpeg":
        return jsonify(error="Foto harus berformat JPEG"), 400
    filename = secure_filename(f"snapshot-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}.jpg")
    image.save(PHOTO_DIR / filename)
    log_event("shared_camera_snapshot_saved", f"JPG received from consented device · session {share_id[:8]} · {filename}")
    return jsonify(ok=True, filename=filename, received_at=utc_now())

@app.route("/api/admin/snapshots", methods=["GET"])
@login_required
def admin_snapshots():
    items = []
    for path in sorted(PHOTO_DIR.glob("snapshot-*"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
        if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            items.append({"filename":path.name,"url":url_for("photos", filename=path.name),
                          "modified":datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")})
    return jsonify(items)

@app.route("/api/admin/snapshots/<path:filename>", methods=["DELETE"])
@login_required
def delete_admin_snapshot(filename):
    require_csrf()
    # Hanya file snapshot yang dikelola aplikasi; cegah path traversal.
    safe_name = secure_filename(filename)
    if safe_name != filename or not safe_name.startswith("snapshot-"):
        return jsonify(error="Nama foto tidak valid"), 400
    path = (PHOTO_DIR / safe_name).resolve()
    if path.parent != PHOTO_DIR.resolve() or not path.is_file():
        return jsonify(error="Foto tidak ditemukan"), 404
    path.unlink()
    log_event("camera_snapshot_deleted", safe_name)
    return jsonify(ok=True, filename=safe_name)

@app.route("/api/camera-snapshot", methods=["POST"])
@login_required
def camera_snapshot():
    require_csrf()
    if "image" not in request.files:
        return jsonify(error="Tidak ada gambar"), 400
    image = request.files["image"]
    ext = ALLOWED_IMAGE_TYPES.get(image.mimetype)
    if not ext:
        return jsonify(error="Format harus JPEG, PNG, atau WebP"), 400
    filename = secure_filename(f"snapshot-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}{ext}")
    image.save(PHOTO_DIR / filename)
    log_event("camera_snapshot_saved", filename)
    return jsonify(ok=True, filename=filename)

@app.route("/photos/<path:filename>")
@login_required
def photos(filename):
    return send_from_directory(PHOTO_DIR, filename, as_attachment=False)

@app.route("/api/locations", methods=["GET"])
@login_required
def get_locations():
    with db() as con:
        rows = con.execute("SELECT id,created_at,latitude,longitude,accuracy,note FROM locations ORDER BY id DESC LIMIT 100").fetchall()
    return jsonify([dict(row) for row in rows])

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        ssl_context=("cert.pem", "key.pem"),
        debug=False
    )
