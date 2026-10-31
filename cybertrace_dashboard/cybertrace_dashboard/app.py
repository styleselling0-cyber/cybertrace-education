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
DATA_DIR = BASE_DIR / "data"
PHOTO_DIR = DATA_DIR / "photos"
DATA_DIR.mkdir(exist_ok=True)
PHOTO_DIR.mkdir(exist_ok=True)
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


PHILOSOPHY_ARTICLES = [{'slug': 'mengapa-manusia-terus-bertanya', 'category': 'FILSAFAT DASAR', 'title': 'Mengapa Manusia Terus Bertanya tentang Kehidupan?', 'subtitle': 'Filsafat bermula ketika jawaban yang biasa kita terima tidak lagi terasa cukup.', 'author': 'Pengantar Filsafat', 'read': '8 menit', 'image': 'https://commons.wikimedia.org/wiki/Special:FilePath/The_School_of_Athens.jpg', 'alt': 'Lukisan para filsuf dalam tradisi pemikiran Yunani', 'intro': 'Kita hidup di tengah banyak jawaban. Keluarga, sekolah, agama, budaya, dan media memberi kita cara untuk memahami dunia. Namun, pernahkah kita berhenti dan bertanya: mengapa kita mempercayai jawaban-jawaban itu?', 'sections': [('Filsafat dimulai dari rasa heran', 'Dalam tradisi Yunani, filsafat sering dikaitkan dengan rasa takjub dan keingintahuan. Ketika seseorang bertanya tentang keadilan, keberanian, kebahagiaan, atau hakikat kenyataan, ia sedang membuka ruang untuk menyelidiki alasan di balik keyakinannya.'), ('Bukan sekadar mencari jawaban cepat', 'Berfilsafat bukan lomba untuk selalu memiliki jawaban. Ia adalah latihan merumuskan pertanyaan dengan jernih, menguji asumsi, membandingkan alasan, dan bersedia memperbaiki pandangan ketika menemukan alasan yang lebih kuat.'), ('Dari ruang diskusi ke kehidupan sehari-hari', 'Pertanyaan filosofis hadir saat kita menimbang apakah keputusan itu adil, apakah informasi layak dipercaya, atau apa arti hidup yang baik. Filsafat membantu kita tidak hanya bereaksi, tetapi juga memahami dasar dari tindakan kita.')], 'quote': 'Hidup yang tidak diperiksa tidak layak dijalani.', 'think': 'Keyakinan apa yang selama ini kamu terima begitu saja, dan alasan apa yang membuatmu mempertahankannya?'}, {'slug': 'socrates-seni-mengajukan-pertanyaan', 'category': 'TOKOH FILSAFAT', 'title': 'Socrates dan Seni Mengajukan Pertanyaan', 'subtitle': 'Mengapa mengakui bahwa kita belum tahu justru bisa menjadi awal kebijaksanaan?', 'author': 'Jejak Para Filsuf', 'read': '7 menit', 'image': 'https://upload.wikimedia.org/wikipedia/commons/4/4a/Socrates%2C_Plato_and_Aristotle.jpg', 'alt': 'Patung dada para filsuf Yunani', 'intro': 'Socrates dikenang bukan karena meninggalkan banyak tulisan, melainkan karena cara berdialognya. Ia mengajak orang memeriksa arti kata-kata yang mereka gunakan dan menguji apakah keyakinan mereka sungguh memiliki dasar.', 'sections': [('Metode bertanya', 'Dalam dialog Socratic, pertanyaan digunakan untuk memperjelas definisi, menemukan asumsi tersembunyi, dan melihat apakah sebuah jawaban konsisten. Pertanyaan lanjutan bukan dimaksudkan untuk mempermalukan, melainkan memperdalam pemahaman.'), ('Berani mengakui ketidaktahuan', 'Mengakui bahwa kita belum memahami sesuatu membuka kesempatan untuk belajar. Sebaliknya, merasa sudah pasti benar dapat membuat kita berhenti mendengarkan dan memeriksa bukti.'), ('Mempraktikkannya hari ini', 'Saat mendengar klaim, tanyakan: apa maksud istilah utamanya? Apa alasannya? Contoh apa yang mendukungnya? Adakah keadaan yang menjadi pengecualian?')], 'quote': 'Kenali dirimu sendiri.', 'think': 'Jika seseorang berbeda pendapat denganmu, pertanyaan apa yang bisa membantu kalian memahami masalah sebelum berdebat?'}, {'slug': 'plato-dunia-ide-dan-bayang-bayang', 'category': 'TOKOH FILSAFAT', 'title': 'Plato: Apakah yang Kita Lihat Selalu Kenyataan?', 'subtitle': 'Gua Plato mengajak kita memikirkan hubungan antara penampakan, pengetahuan, dan kebenaran.', 'author': 'Jejak Para Filsuf', 'read': '9 menit', 'image': 'https://commons.wikimedia.org/wiki/Special:FilePath/Plato-raphael.jpg', 'alt': 'Plato dalam lukisan The School of Athens', 'intro': 'Bayangkan seseorang yang sejak kecil hanya melihat bayangan di dinding gua. Baginya, bayangan itulah seluruh kenyataan. Apa yang terjadi ketika ia keluar dan melihat dunia yang selama ini tidak ia kenal?', 'sections': [('Perumpamaan gua', 'Dalam Republik, Plato menggunakan gambaran para tahanan di dalam gua untuk membahas keterbatasan pengalaman dan proses pendidikan. Bayangan dapat disangka sebagai kenyataan ketika seseorang belum mengenal sumber yang membentuknya.'), ('Belajar berarti mengubah cara melihat', 'Perjalanan keluar gua tidak nyaman. Pengetahuan menuntut kita meninjau ulang hal-hal yang selama ini dianggap pasti. Karena itu, pendidikan bukan hanya mengisi ingatan, tetapi juga mengubah cara memahami.'), ('Membaca dunia digital', "Informasi yang muncul di layar tidak otomatis menggambarkan keseluruhan kenyataan. Potongan video, judul, dan unggahan dapat menjadi 'bayangan' jika dilepaskan dari konteks.")], 'quote': 'Pendidikan adalah proses mengarahkan jiwa untuk melihat.', 'think': "Apa contoh 'bayangan' di media sosial yang pernah kamu anggap sebagai gambaran utuh suatu peristiwa?"}, {'slug': 'aristoteles-etika-hidup-baik', 'category': 'ETIKA', 'title': 'Aristoteles: Apa Artinya Menjalani Hidup yang Baik?', 'subtitle': 'Kebahagiaan bukan hanya perasaan sesaat, tetapi berkaitan dengan bagaimana manusia menjalani hidup.', 'author': 'Etika & Kehidupan', 'read': '8 menit', 'image': 'https://upload.wikimedia.org/wikipedia/commons/4/4a/Socrates%2C_Plato_and_Aristotle.jpg', 'alt': 'Patung para pemikir Yunani klasik', 'intro': 'Kita sering menyebut bahagia ketika memperoleh sesuatu yang menyenangkan. Aristoteles mengajak kita melihat pertanyaan yang lebih luas: kehidupan seperti apa yang membuat manusia berkembang dan menjalankan potensinya?', 'sections': [('Eudaimonia', 'Istilah eudaimonia sering diterjemahkan sebagai kebahagiaan atau kesejahteraan hidup. Gagasan ini tidak semata-mata menunjuk pada suasana hati, melainkan kehidupan yang dijalani dengan baik melalui aktivitas dan kebajikan.'), ('Kebajikan sebagai kebiasaan', 'Keberanian, kemurahan hati, dan pengendalian diri tumbuh melalui praktik. Seseorang menjadi adil dengan membiasakan tindakan adil, bukan hanya dengan mengetahui definisinya.'), ('Jalan tengah yang bijaksana', 'Aristoteles membahas kebajikan sebagai jalan tengah relatif terhadap keadaan. Keberanian, misalnya, berbeda dari kenekatan maupun pengecut. Menentukan tindakan yang tepat membutuhkan pertimbangan praktis.')], 'quote': 'Kita menjadi adil dengan melakukan tindakan-tindakan adil.', 'think': 'Kebiasaan kecil apa yang dapat kamu latih agar hidupmu lebih selaras dengan nilai yang kamu yakini?'}, {'slug': 'nietzsche-makna-dan-nilai-hidup', 'category': 'PEMIKIRAN MODERN', 'title': 'Nietzsche: Siapa yang Menentukan Nilai dalam Hidup Kita?', 'subtitle': 'Menguji asal-usul nilai, moralitas, dan keberanian membentuk cara hidup.', 'author': 'Pemikiran Modern', 'read': '10 menit', 'image': 'https://upload.wikimedia.org/wikipedia/commons/1/1b/Nietzsche187a.jpg', 'alt': 'Potret Friedrich Nietzsche', 'intro': 'Banyak nilai terasa begitu wajar karena diwariskan dan diulang terus-menerus. Nietzsche mengajak pembacanya melakukan penelusuran kritis: dari mana nilai itu berasal, kepentingan apa yang membentuknya, dan bagaimana nilai tersebut memengaruhi kehidupan?', 'sections': [('Menelusuri asal-usul moral', 'Dalam genealogi moral, Nietzsche tidak hanya bertanya apakah suatu nilai dianggap baik atau buruk. Ia juga menyelidiki sejarah, kondisi, dan dorongan manusia yang membentuk penilaian moral.'), ('Melampaui kebiasaan berpikir', 'Mengkritik nilai bukan berarti menolak semua aturan. Kritik dapat menjadi cara memahami mengapa suatu aturan ada, siapa yang diuntungkan, dan apakah ia masih layak dipertahankan.'), ('Tanggung jawab atas pilihan', 'Pertanyaan tentang nilai membawa kita pada tanggung jawab: apakah kita menjalani hidup secara sadar, atau sekadar mengikuti kebiasaan tanpa pernah memeriksanya?')], 'quote': 'Jadilah siapa dirimu.', 'think': 'Nilai apa yang ingin kamu pertahankan setelah memeriksanya secara sadar, bukan hanya karena diwariskan?'}, {'slug': 'kebebasan-dan-tanggung-jawab', 'category': 'EKSISTENSIALISME', 'title': 'Kebebasan: Apakah Setiap Pilihan Membuat Kita Bertanggung Jawab?', 'subtitle': 'Memilih berarti menghadapi kemungkinan, konsekuensi, dan tanggung jawab.', 'author': 'Eksistensialisme', 'read': '8 menit', 'image': 'https://commons.wikimedia.org/wiki/Special:FilePath/Jean-Paul_Sartre_in_Venice_%28crop%29.jpg', 'alt': 'Potret Jean-Paul Sartre', 'intro': 'Kita sering menginginkan kebebasan, tetapi tidak selalu menyukai konsekuensinya. Pilihan membuka kemungkinan, sekaligus membuat kita perlu mempertimbangkan dampak tindakan terhadap diri sendiri dan orang lain.', 'sections': [('Kebebasan dalam situasi nyata', 'Kebebasan manusia tidak berlangsung dalam ruang kosong. Kita hidup dengan kondisi sosial, sejarah, tubuh, dan keterbatasan tertentu. Namun, di dalam situasi itu tetap ada ruang untuk mengambil sikap.'), ('Pilihan dan tanggung jawab', 'Pilihan bukan hanya soal apa yang kita inginkan. Kita juga perlu mempertimbangkan akibatnya, alasan yang mendasarinya, dan orang-orang yang terdampak.'), ('Hidup secara autentik', 'Pemikiran eksistensialis mengajak manusia menghadapi kenyataan hidup secara sadar, alih-alih terus bersembunyi di balik alasan atau peran yang tidak pernah diperiksa.')], 'quote': 'Manusia dikutuk untuk bebas.', 'think': 'Dalam keputusan yang sedang kamu hadapi, bagian mana yang benar-benar merupakan pilihanmu dan konsekuensi apa yang perlu kamu tanggung?'}]

@app.route("/modul")
def education_modules():
    return render_template("education_modules.html", articles=PHILOSOPHY_ARTICLES)

@app.route("/artikel/<slug>")
def philosophy_article_detail(slug):
    item = next((a for a in PHILOSOPHY_ARTICLES if a["slug"] == slug), None)
    if not item:
        abort(404)
    return render_template("article_detail.html", article=item)

def education_module_data():
    return [
      {"slug":"pengantar-filsafat-bertanya-dengan-bijak","grade":"Pengantar","title":"Pengantar Filsafat: Seni Bertanya dengan Bijak","topic":"Dasar-dasar filsafat","duration":"60–90 menit","description":"Mengenal filsafat sebagai latihan berpikir jernih: menyusun pertanyaan, memeriksa asumsi, dan memberi alasan.",
       "objectives":["Memahami filsafat sebagai kegiatan memeriksa pertanyaan dan alasan, bukan sekadar menghafal nama pemikir.","Membedakan pertanyaan faktual, konseptual, dan normatif.","Menyusun pertanyaan lanjutan yang memperjelas suatu persoalan."],
       "materials":["Jurnal atau catatan pribadi","Satu persoalan sehari-hari yang ingin direnungkan","Bacaan pengantar singkat (opsional)"],
       "steps":["Mulai dari satu pertanyaan sederhana: apa yang membuat sebuah hidup terasa bermakna?","Tuliskan jawaban awal tanpa menghakimi jawaban sendiri.","Periksa istilah penting dalam pertanyaan: apa arti 'bermakna' dalam konteks ini?","Buat tiga pertanyaan lanjutan dan catat alasan di balik setiap jawaban.","Akhiri dengan refleksi: bagian mana dari pandangan awal yang berubah atau menjadi lebih jelas?"],
       "worksheet":["Pilih satu pertanyaan filosofis yang dekat dengan kehidupan Anda.","Definisikan dua istilah kunci dalam pertanyaan tersebut.","Tuliskan satu jawaban sementara, dua alasan pendukung, dan satu keberatan yang mungkin muncul."],
       "quiz":["Apa perbedaan bertanya secara filosofis dengan sekadar meminta informasi?","Mengapa definisi istilah penting dalam diskusi filsafat?"],"answers":["Pertanyaan filosofis memeriksa konsep, asumsi, alasan, dan implikasi; bukan hanya mencari data.","Karena perbedaan makna istilah dapat membuat orang tampak berselisih padahal membicarakan hal yang berbeda."]},
      {"slug":"logika-argumen-dan-kesesatan-berpikir","grade":"Logika","title":"Logika, Argumen, dan Kesesatan Berpikir","topic":"Logika praktis","duration":"90 menit","description":"Mempelajari cara mengenali klaim, premis, kesimpulan, serta kekeliruan penalaran dalam percakapan dan media.",
       "objectives":["Membedakan klaim, premis, dan kesimpulan dalam sebuah argumen.","Menilai apakah alasan benar-benar mendukung kesimpulan.","Mengenali beberapa sesat pikir umum tanpa buru-buru memberi label kepada orang."],
       "materials":["Artikel opini atau percakapan publik","Kertas untuk memetakan argumen","Daftar contoh sesat pikir"],
       "steps":["Ambil satu pernyataan yang sering ditemui dalam percakapan atau media.","Tandai klaim utama, alasan pendukung, dan kesimpulannya.","Tanyakan apakah alasan tersebut relevan dan cukup kuat.","Bandingkan dengan contoh serangan pribadi, generalisasi tergesa-gesa, dan pilihan palsu.","Tulis ulang argumen agar lebih adil dan kuat."],
       "worksheet":["Pilih satu klaim dari artikel atau percakapan.","Buat peta sederhana: premis 1, premis 2, kesimpulan.","Temukan satu kemungkinan kelemahan dan usulkan perbaikan argumen."],
       "quiz":["Apa fungsi premis dalam argumen?","Mengapa menyerang pribadi pembicara tidak otomatis membantah argumennya?"],"answers":["Premis adalah alasan atau pernyataan pendukung yang digunakan untuk menopang kesimpulan.","Karena benar-salahnya argumen ditentukan oleh isi dan hubungan alasannya, bukan semata-mata identitas orang yang menyampaikan."]},
      {"slug":"berpikir-kritis-fakta-opini-bukti","grade":"Berpikir Kritis","title":"Berpikir Kritis: Fakta, Opini, dan Bukti","topic":"Evaluasi informasi","duration":"90 menit","description":"Melatih kebiasaan memeriksa sumber, membedakan fakta dan tafsir, serta menunda kesimpulan ketika bukti belum cukup.",
       "objectives":["Membedakan fakta yang dapat diperiksa, opini, interpretasi, dan dugaan.","Menilai kualitas sumber serta relevansi bukti.","Menyatakan tingkat keyakinan secara proporsional dengan bukti yang tersedia."],
       "materials":["Dua tulisan dengan pandangan berbeda tentang isu yang sama","Akses sumber rujukan (opsional)","Lembar evaluasi bukti"],
       "steps":["Pilih sebuah klaim yang ramai dibicarakan.","Pisahkan bagian yang dapat diverifikasi dari penilaian atau tafsir penulis.","Telusuri siapa pembuat informasi, kapan diterbitkan, dan bukti apa yang disertakan.","Cari sumber pembanding yang independen.","Buat kesimpulan sementara dan tulis informasi apa yang masih dibutuhkan."],
       "worksheet":["Catat satu klaim, sumber asal, tanggal, dan bukti pendukungnya.","Nilai relevansi, kecukupan, dan kemungkinan bias sumber.","Tulis kesimpulan sementara beserta batas kepastian Anda."],
       "quiz":["Apakah banyaknya orang yang membagikan informasi membuktikan kebenarannya?","Apa yang sebaiknya dilakukan jika bukti belum memadai?"],"answers":["Tidak. Popularitas bukan pengganti bukti yang dapat diperiksa.","Menunda kesimpulan, mencari bukti tambahan, atau menyatakan bahwa informasi belum cukup."]},
      {"slug":"etika-kehidupan-dilema-moral","grade":"Etika","title":"Etika Kehidupan: Menimbang Dilema Moral","topic":"Etika terapan","duration":"90–120 menit","description":"Menggunakan beberapa pendekatan etika untuk menimbang keputusan sulit, tanggung jawab, keadilan, dan dampaknya bagi orang lain.",
       "objectives":["Mengidentifikasi pihak yang terdampak oleh suatu keputusan.","Membandingkan pertimbangan akibat, kewajiban, hak, dan karakter.","Menyusun keputusan yang dapat dipertanggungjawabkan beserta alasan dan keterbatasannya."],
       "materials":["Satu studi kasus dilema etis","Kertas pemetaan pihak terdampak","Jurnal refleksi"],
       "steps":["Baca kasus tanpa langsung memilih pihak yang benar.","Identifikasi fakta, pihak terdampak, kepentingan, hak, dan kewajiban.","Tinjau pilihan dari sisi akibat yang mungkin timbul.","Tinjau kembali dari sisi prinsip, keadilan, dan karakter yang ingin dijaga.","Pilih tindakan sementara dan jelaskan alasan, risiko, serta hal yang dapat mengubah keputusan."],
       "worksheet":["Tuliskan dilema dalam satu kalimat netral.","Buat tabel pilihan, pihak terdampak, manfaat, risiko, dan prinsip yang relevan.","Susun keputusan dan satu keberatan terkuat terhadap keputusan Anda."],
       "quiz":["Mengapa niat baik saja belum tentu membuat tindakan menjadi etis?","Apa manfaat melihat dilema dari lebih dari satu pendekatan etika?"],"answers":["Karena tindakan juga perlu dinilai dari akibat, kewajiban, hak pihak lain, dan konteksnya.","Agar pertimbangan lebih menyeluruh dan tidak bergantung pada satu sudut pandang saja."]},
      {"slug":"eksistensialisme-kebebasan-dan-makna","grade":"Eksistensialisme","title":"Eksistensialisme: Kebebasan, Pilihan, dan Makna","topic":"Filsafat eksistensi","duration":"90 menit","description":"Merenungkan kebebasan, tanggung jawab, kecemasan, autentisitas, dan pencarian makna dalam pilihan hidup sehari-hari.",
       "objectives":["Menjelaskan hubungan antara pilihan dan tanggung jawab pribadi.","Membedakan nilai yang sungguh diyakini dengan harapan sosial yang diterima tanpa refleksi.","Merumuskan refleksi pribadi tentang makna tanpa menganggap hanya ada satu jawaban universal."],
       "materials":["Jurnal refleksi","Satu keputusan hidup yang sedang dipertimbangkan","Bacaan pengantar eksistensialisme"],
       "steps":["Tuliskan satu pilihan penting yang pernah atau sedang dihadapi.","Identifikasi pengaruh kebiasaan, keluarga, lingkungan, dan nilai pribadi.","Renungkan apa yang berada dalam kendali dan apa yang tidak.","Tanyakan konsekuensi pilihan tersebut bagi diri sendiri dan orang lain.","Rumuskan tindakan kecil yang selaras dengan nilai yang ingin dijalani."],
       "worksheet":["Lengkapi kalimat: saya menganggap hidup bermakna ketika…","Tuliskan satu nilai yang Anda pilih secara sadar dan alasan memilihnya.","Catat satu tindakan nyata yang dapat mencerminkan nilai tersebut minggu ini."],
       "quiz":["Apakah kebebasan berarti tidak memiliki batas atau tanggung jawab?","Mengapa refleksi atas pilihan dapat membantu hidup lebih autentik?"],"answers":["Tidak. Kebebasan selalu berhubungan dengan kondisi, konsekuensi, dan tanggung jawab.","Karena seseorang dapat memahami alasan tindakannya dan tidak sekadar mengikuti tekanan atau kebiasaan tanpa pemeriksaan."]},
      {"slug":"filsafat-politik-keadilan-dan-masyarakat","grade":"Filsafat Politik","title":"Filsafat Politik: Keadilan dan Kehidupan Bersama","topic":"Keadilan sosial dan politik","duration":"90–120 menit","description":"Mengkaji gagasan keadilan, kebebasan, kesetaraan, kekuasaan, dan tanggung jawab warga dalam kehidupan bermasyarakat.",
       "objectives":["Membedakan kesetaraan, keadilan, kebebasan, dan kepentingan bersama sebagai konsep yang saling berkaitan namun tidak identik.","Menganalisis aturan publik dari sudut pandang pihak yang berbeda.","Menyusun argumen kebijakan yang mempertimbangkan hak, dampak, dan alasan yang dapat diuji."],
       "materials":["Contoh kebijakan publik atau aturan komunitas","Kertas peta pemangku kepentingan","Bacaan pengantar filsafat politik"],
       "steps":["Pilih satu aturan atau kebijakan yang dekat dengan kehidupan sehari-hari.","Tentukan tujuan kebijakan dan kelompok yang memperoleh manfaat atau menanggung beban.","Uji kebijakan melalui pertanyaan tentang kebebasan, kesetaraan, dan keadilan.","Susun argumen pendukung dan kritik terkuat secara adil.","Ajukan revisi kebijakan yang lebih dapat dipertanggungjawabkan."],
       "worksheet":["Pilih satu kebijakan dan tulis masalah yang hendak diselesaikan.","Petakan minimal tiga kelompok yang terdampak.","Buat rekomendasi beserta alasan, konsekuensi, dan kemungkinan keberatan."],
       "quiz":["Apakah perlakuan yang sama selalu menghasilkan keadilan?","Mengapa kebijakan publik perlu dinilai dari dampaknya terhadap kelompok berbeda?"],"answers":["Tidak selalu; kondisi dan kebutuhan yang berbeda dapat membuat perlakuan identik menghasilkan ketidakadilan.","Karena kebijakan membagi manfaat, risiko, hak, dan beban secara berbeda di masyarakat."]}
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
