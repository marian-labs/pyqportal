from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, abort
import os
import psycopg2
import time
from psycopg2 import pool
from supabase import create_client
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import uuid
import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_compress import Compress

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY','asasasa')
Compress(app)
load_dotenv()

@app.after_request
def apply_security_headers(response):

    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

    # Uncomment after confirming HTTPS on Cloudflare
    # response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "

        # JS — self + jsdelivr (marked.js) + Google Analytics + inline scripts
        "script-src 'self' 'unsafe-inline' "
        "https://cdn.jsdelivr.net "
        "https://www.googletagmanager.com; "

        # CSS — self + inline styles (used in login, admin, error pages)
        "style-src 'self' 'unsafe-inline' "
        "https://fonts.googleapis.com; "

        # Fonts — Google Fonts
        "font-src 'self' "
        "https://fonts.gstatic.com; "

        # Images — self + data URIs
        "img-src 'self' data:; "

        # API calls — self + Google Analytics + Supabase storage (proxy-pdf route fetches server-side)
        "connect-src 'self' "
        "https://www.google-analytics.com "
        "https://*.supabase.co; "

        # PDFs open in new tab from Supabase storage
        "frame-src https://*.supabase.co; "

        # No plugins, objects, or base tag hijacking
        "object-src 'none'; "
        "base-uri 'self';"
    )

    return response

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://"
)
request_log = {}

# ---------- Departments (URL slug → display name) ----------
DEPARTMENTS = {
    "bca": "BCA",
    "bba": "BBA",
    "bcom": "BCom",
    "bsc-math": "BSc Mathematics",
    "msc-physics": "MSc Physics",
}

DEFAULT_DEPARTMENT = "bca"

# ---------- Supabase ----------
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------- Staging Supabase (new project) ----------
STAGING_SUPABASE_URL = os.environ.get("STAGING_SUPABASE_URL")
STAGING_SUPABASE_KEY = os.environ.get("STAGING_SUPABASE_KEY")
STAGING_BUCKET = os.environ.get("STAGING_SUPABASE_BUCKET", "pending-uploads")
staging_supabase = create_client(STAGING_SUPABASE_URL, STAGING_SUPABASE_KEY)

# ---------- DB (Connection Pool) ----------
db_pool = pool.SimpleConnectionPool(1, 10, os.environ.get("DATABASE_URL"))


def get_db():
    try:
        conn = db_pool.getconn()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        return conn
    except:
        return psycopg2.connect(os.environ.get("DATABASE_URL"))


def return_db(conn):
    try:
        db_pool.putconn(conn)
    except Exception as e:
        app.logger.warning(f"putconn failed, closing directly: {e}")
        conn.close()


def init_db():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS departments (
                department_id SERIAL PRIMARY KEY,
                slug VARCHAR(50) UNIQUE NOT NULL,
                name VARCHAR(100) NOT NULL,
                code VARCHAR(20),
                icon VARCHAR(50) DEFAULT '📚',
                description TEXT,
                is_active BOOLEAN DEFAULT true,
                is_coming_soon BOOLEAN DEFAULT false,
                display_order INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id SERIAL PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role VARCHAR(20) NOT NULL DEFAULT 'user'
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS subjects (
                subject_id SERIAL PRIMARY KEY,
                subject_name VARCHAR(255) NOT NULL,
                semester INTEGER,
                department VARCHAR(50),
                department_id INTEGER REFERENCES departments(department_id)
            );
        """)
        cur.execute(
            "ALTER TABLE subjects ADD COLUMN IF NOT EXISTS department VARCHAR(50)"
        )
        cur.execute(
            "ALTER TABLE subjects ADD COLUMN IF NOT EXISTS department_id INTEGER REFERENCES departments(department_id)"
        )
        
        # Seed default departments if missing
        default_depts = [
            ("bca", "BCA", "BCA", "💻", "Bachelor of Computer Applications", True, False, 1),
            ("bcom", "BCom", "BCom", "📊", "Bachelor of Commerce", True, True, 2),
            ("bba", "BBA", "BBA", "📈", "Bachelor of Business Administration", True, True, 3),
            ("bsc-math", "BSc Mathematics", "BSc Math", "📐", "BSc Mathematics", True, True, 4),
            ("msc-physics", "MSc Physics", "MSc Phys", "⚛️", "MSc Physics", True, True, 5),
        ]
        for slug, name, code, icon, desc, active, coming_soon, order in default_depts:
            cur.execute("""
                INSERT INTO departments (slug, name, code, icon, description, is_active, is_coming_soon, display_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (slug) DO NOTHING;
            """, (slug, name, code, icon, desc, active, coming_soon, order))

        # Backfill department_id in subjects
        cur.execute("""
            UPDATE subjects s
            SET department_id = d.department_id
            FROM departments d
            WHERE (LOWER(s.department) = LOWER(d.slug) OR LOWER(s.department) = LOWER(d.name) OR LOWER(s.department) = LOWER(d.code))
              AND s.department_id IS NULL;
        """)
        cur.execute("""
            UPDATE subjects
            SET department_id = (SELECT department_id FROM departments WHERE slug = 'bca' LIMIT 1)
            WHERE department_id IS NULL;
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS question_papers (
                paper_id SERIAL PRIMARY KEY,
                subject_id INTEGER REFERENCES subjects(subject_id),
                year INTEGER,
                file_name VARCHAR(255),
                file_path VARCHAR(255),
                exam_type VARCHAR(100),
                upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                file_url TEXT,
                public_id TEXT,
                ai_analysis TEXT,
                file_size BIGINT
            );
        """)
        cur.execute(
            "ALTER TABLE question_papers ADD COLUMN IF NOT EXISTS file_size BIGINT"
        )
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_papers (
                id SERIAL PRIMARY KEY,
                subject_id INTEGER REFERENCES subjects(subject_id),
                year INTEGER,
                exam_type VARCHAR(100),
                file_name VARCHAR(255),
                staging_path VARCHAR(255),
                submitted_by_ip VARCHAR(50),
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status VARCHAR(20) DEFAULT 'pending',
                file_size BIGINT
            );
        """)
        cur.execute("ALTER TABLE pending_papers ADD COLUMN IF NOT EXISTS file_size BIGINT;")
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM users")
        count = cur.fetchone()[0]
        if count == 0:
            default_username = os.environ.get('ADMIN_USER', 'admin')
            default_password = os.environ.get('ADMIN_PASS', 'admin123')
            hash_val = generate_password_hash(default_password)
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s)",
                (default_username, hash_val, 'admin')
            )
            conn.commit()
            print(f"Created default admin: {default_username}")
    finally:
        cur.close()
        return_db(conn)


init_db()


# ---------- Department helpers ----------

def get_departments(active_only=True):
    conn = get_db()
    cur = conn.cursor()
    try:
        query = """
            SELECT d.department_id, d.slug, d.name, d.code, d.icon, d.description, d.is_active, d.is_coming_soon, d.display_order,
                   COUNT(q.paper_id) as paper_count
            FROM departments d
            LEFT JOIN subjects s ON d.department_id = s.department_id OR (s.department_id IS NULL AND (LOWER(s.department) = LOWER(d.slug) OR LOWER(s.department) = LOWER(d.name)))
            LEFT JOIN question_papers q ON s.subject_id = q.subject_id
            WHERE (%s = false OR d.is_active = true)
            GROUP BY d.department_id, d.slug, d.name, d.code, d.icon, d.description, d.is_active, d.is_coming_soon, d.display_order
            ORDER BY d.display_order ASC, d.name ASC;
        """
        cur.execute(query, (active_only,))
        rows = cur.fetchall()
        return [
            {
                "id": r[0],
                "slug": r[1],
                "name": r[2],
                "code": r[3] or r[2],
                "icon": r[4] or "📚",
                "description": r[5] or "",
                "is_active": r[6],
                "is_coming_soon": r[7],
                "display_order": r[8],
                "paper_count": r[9] or 0
            }
            for r in rows
        ]
    except Exception as e:
        app.logger.error(f"Error fetching departments: {e}")
        return []
    finally:
        cur.close()
        return_db(conn)


def get_department_by_slug(slug):
    if not slug:
        return None
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT department_id, slug, name, code, icon, description, is_active, is_coming_soon, display_order
            FROM departments
            WHERE LOWER(slug) = LOWER(%s);
        """, (slug,))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "slug": row[1],
            "name": row[2],
            "code": row[3] or row[2],
            "icon": row[4] or "📚",
            "description": row[5] or "",
            "is_active": row[6],
            "is_coming_soon": row[7],
            "display_order": row[8]
        }
    finally:
        cur.close()
        return_db(conn)


def get_departments_dict():
    depts = get_departments(active_only=False)
    if not depts:
        return DEPARTMENTS
    return {d["slug"]: d["name"] for d in depts}

# ---------- Auth helpers ----------


def get_user_by_username(username):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT user_id, username, password_hash, role FROM users WHERE username = %s",
            (username,)
        )
        return cur.fetchone()
    finally:
        cur.close()
        return_db(conn)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'admin':
            flash('Administrator access required.')
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated

# ---------- Utility ----------


ALLOWED_EXTENSIONS = {"pdf"}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def format_file_size(size_bytes):
    if size_bytes is None:
        return "—"
    try:
        size = float(size_bytes)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                if unit == 'B':
                    return f"{int(size)} B"
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"
    except (ValueError, TypeError):
        return "—"


def compress_scanned_pdf(input_bytes, quality=60, max_dimension=1600):
    """
    Compresses PDFs using PyMuPDF (fitz) by optimizing embedded images in-place.
    Preserves exact page count, original physical page dimensions (A4/Letter), and layout,
    while significantly reducing file size.
    """
    import io

    # 1. Try PyMuPDF (fitz) for in-place stream optimization
    try:
        import fitz
        from PIL import Image

        doc = fitz.open(stream=input_bytes, filetype="pdf")
        processed_xrefs = set()

        for page in doc:
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                if xref in processed_xrefs:
                    continue
                processed_xrefs.add(xref)
                try:
                    base_img = doc.extract_image(xref)
                    if not base_img or not base_img.get("image"):
                        continue
                    pil_img = Image.open(io.BytesIO(base_img["image"]))
                    if pil_img.mode in ("RGBA", "P", "LA"):
                        pil_img = pil_img.convert("RGB")

                    w, h = pil_img.size
                    if max(w, h) > max_dimension:
                        scale = max_dimension / float(max(w, h))
                        w, h = int(w * scale), int(h * scale)
                        pil_img = pil_img.resize((w, h), Image.Resampling.LANCZOS)

                    buf = io.BytesIO()
                    pil_img.save(buf, format="JPEG", quality=quality, optimize=True)
                    doc.update_stream(xref, buf.getvalue())
                except Exception as img_err:
                    app.logger.warning(f"Skipped image xref {xref}: {img_err}")

        out_buf = io.BytesIO()
        doc.save(out_buf, deflate=True, garbage=4)
        compressed = out_buf.getvalue()

        if len(compressed) < len(input_bytes):
            app.logger.info(f"PyMuPDF compressed PDF from {len(input_bytes)} B to {len(compressed)} B")
            return compressed
        return input_bytes
    except Exception as fitz_err:
        app.logger.warning(f"PyMuPDF compression fallback: {fitz_err}")

    # 2. Fallback to pypdf stream compression if fitz fails
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(input_bytes))
        writer = pypdf.PdfWriter()
        writer.append(reader)
        writer.compress_identical_objects()
        for page in writer.pages:
            page.compress_content_streams()
        out_buf = io.BytesIO()
        writer.write(out_buf)
        compressed = out_buf.getvalue()
        if len(compressed) < len(input_bytes):
            return compressed
        return input_bytes
    except Exception as e:
        app.logger.warning(f"PDF fallback compression skipped: {e}")
        return input_bytes


def analyze_with_gemini(pdf_url, subject_name):
    import tempfile
    import os as _os

    r = requests.get(pdf_url, timeout=15)
    r.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
        tmp.write(r.content)
        tmp_path = tmp.name

    prompt = f"""You are an exam preparation assistant for {subject_name}.
Analyze this previous year question paper and predict the most likely questions for the next exam.
Respond with:
1. Top 10 predicted questions (numbered)
2. Key topics to focus on (bullet points)
3. Question pattern observations (2-3 lines)"""

    try:
        with open(tmp_path, 'rb') as f:
            uploaded = client.files.upload(
                file=f,
                config=types.UploadFileConfig(mime_type='application/pdf')
            )

        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=[uploaded, prompt]
        )

        try:
            client.files.delete(name=uploaded.name)
        except:
            pass

        return response.text
    finally:
        _os.unlink(tmp_path)


def get_subjects(department=None):
    conn = get_db()
    cur = conn.cursor()
    try:
        if department:
            # department may be a slug (e.g. 'bca') or a name (e.g. 'BCA')
            # Try matching via departments table slug first, then fall back to direct name match
            cur.execute(
                """
                SELECT s.subject_id, s.subject_name, s.semester, s.department
                FROM subjects s
                LEFT JOIN departments d ON s.department_id = d.department_id
                WHERE d.slug = %s OR LOWER(s.department) = LOWER(%s)
                ORDER BY s.semester, s.subject_name
                """,
                (department, department),
            )
        else:
            cur.execute(
                """
                SELECT subject_id, subject_name, semester, department
                FROM subjects
                ORDER BY semester, subject_name
                """
            )
        rows = cur.fetchall()
        return [
            {
                "subject_id": r[0],
                "subject_name": r[1],
                "semester": r[2],
                "department": r[3],
            }
            for r in rows
        ]
    finally:
        cur.close()
        return_db(conn)


def get_department_papers(department_slug_or_name):
    conn = get_db()
    cur = conn.cursor()
    papers = []
    try:
        cur.execute(
            """
            SELECT s.subject_name, s.semester, q.year, q.file_url, q.exam_type, q.paper_id,
                   s.department,
                   CASE WHEN q.ai_analysis IS NOT NULL THEN true ELSE false END as is_analysed
            FROM question_papers q
            JOIN subjects s ON q.subject_id = s.subject_id
            LEFT JOIN departments d ON s.department_id = d.department_id
            WHERE d.slug = %s OR LOWER(s.department) = LOWER(%s)
            ORDER BY s.subject_name ASC, q.year DESC
            """,
            (department_slug_or_name, department_slug_or_name),
        )
        rows = cur.fetchall()

        for subject_name, semester, year, file_url, exam_type, paper_id, dept, is_analysed in rows:
            papers.append({
                "subject": subject_name,
                "year": year,
                "semester": semester,
                "department": dept or "",
                "examType": exam_type or "—",
                "file_url": file_url,
                "download_url": (file_url + "?download=") if file_url else None,
                "paper_id": paper_id,
                "is_analysed": is_analysed,
            })
        return papers
    finally:
        cur.close()
        return_db(conn)

# ================================================================
# ROUTES
# ================================================================


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            error = "Username and password are required"
        else:
            row = get_user_by_username(username)
            if row and check_password_hash(row[2], password):
                session.clear()
                session["user_id"] = row[0]
                session["username"] = row[1]
                session["role"] = row[3]
                next_page = request.args.get("next")
                return redirect(next_page or url_for("home"))
            error = "Invalid username or password"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/")
def home():
    paper_count = 0
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM question_papers")
        paper_count = cur.fetchone()[0] or 0
    except Exception:
        pass
    finally:
        cur.close()
        return_db(conn)

    departments = get_departments(active_only=True)
    bca_paper_count = 0
    for d in departments:
        if d["slug"] == "bca":
            bca_paper_count = d["paper_count"]
            break

    return render_template(
        "index.html",
        paper_count=paper_count,
        bca_paper_count=bca_paper_count,
        departments=departments,
        departments_dict=get_departments_dict()
    )


@app.route("/upload", methods=["GET", "POST"])
@login_required
@admin_required
def upload_page():
    depts_dict = get_departments_dict()
    if request.method == "POST":
        conn = get_db()
        cur = conn.cursor()
        try:
            subject_raw = request.form.get("subject_id")
            if not subject_raw or subject_raw.strip() == "":
                return render_template("upload.html", error="Subject is required", subjects=get_subjects(), departments=depts_dict)
            try:
                subject_id = int(subject_raw)
            except ValueError:
                return render_template("upload.html", error="Invalid subject", subjects=get_subjects(), departments=depts_dict)

            cur.execute(
                "SELECT subject_id, subject_name FROM subjects WHERE subject_id = %s",
                (subject_id,)
            )
            row = cur.fetchone()
            if not row:
                return render_template("upload.html", error="Subject not found.", subjects=get_subjects(), departments=depts_dict)
            subject_id, subject_name = row[0], row[1]

            year = request.form.get("year")
            try:
                if not year or not str(year).strip():
                    raise ValueError("Year is required")
                year_int = int(str(year).split("-", 1)
                               [0].strip()) if "-" in str(year) else int(year)
            except (ValueError, TypeError) as exc:
                return render_template("upload.html", error=f"Invalid year: {exc}", subjects=get_subjects(), departments=depts_dict)

            file = request.files.get("file")
            if not file or not file.filename:
                return render_template("upload.html", error="No file provided", subjects=get_subjects(), departments=depts_dict)
            if not allowed_file(file.filename):
                return render_template("upload.html", error="Only PDF files are allowed", subjects=get_subjects(), departments=depts_dict)

            exam_type = request.form.get(
                "examType") or request.form.get("exam_type") or ""

            file_bytes = file.read()
            unique_name = f"{subject_id}/{year_int}/{uuid.uuid4()}.pdf"
            supabase.storage.from_(SUPABASE_BUCKET).upload(
                unique_name,
                file_bytes,
                file_options={"content-type": "application/pdf"}
            )
            file_url = supabase.storage.from_(
                SUPABASE_BUCKET).get_public_url(unique_name)

            original_filename = secure_filename(file.filename)
            cur.execute(
                """
                INSERT INTO question_papers
                (subject_id, year, file_name, file_url, exam_type, public_id, file_size)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING paper_id
                """,
                (subject_id, year_int, original_filename,
                 file_url, exam_type, unique_name, len(file_bytes)),
            )
            conn.commit()

            flash("Question paper uploaded successfully.")
            return redirect(url_for("upload_page"))

        except Exception as e:
            conn.rollback()
            app.logger.exception("Upload failed")
            return render_template("upload.html", error=str(e), subjects=get_subjects(), departments=depts_dict)
        finally:
            cur.close()
            return_db(conn)

    return render_template("upload.html", subjects=get_subjects(), departments=depts_dict)


@app.route("/papers")
def view_papers():
    departments = get_departments(active_only=True)
    return render_template("papers_home.html", departments=departments, departments_dict=get_departments_dict())


@app.route("/papers/<department>")
def view_papers_by_department(department):
    department_slug = department.lower()
    dept_obj = get_department_by_slug(department_slug)

    if not dept_obj:
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT slug, name FROM departments WHERE LOWER(name) = LOWER(%s) OR LOWER(code) = LOWER(%s)", (department, department))
            row = cur.fetchone()
            if row:
                department_slug = row[0]
                dept_obj = get_department_by_slug(department_slug)
        finally:
            cur.close()
            return_db(conn)

    if not dept_obj and department_slug not in DEPARTMENTS:
        abort(404)

    department_name = dept_obj["name"] if dept_obj else DEPARTMENTS.get(department_slug, department.upper())

    try:
        papers = get_department_papers(department_slug)
        subjects = get_subjects(department_slug)
        years = sorted({p["year"] for p in papers if p.get("year")}, reverse=True) if papers else []
        return render_template(
            "view.html",
            papers=papers,
            subjects=subjects,
            years=years,
            department_slug=department_slug,
            department_name=department_name,
            department_obj=dept_obj,
            departments=get_departments_dict(),
        )
    except Exception:
        app.logger.exception("Failed to load papers")
        return render_template("500.html"), 500
            
@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/analyze/<int:paper_id>")
def analyze_paper(paper_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT q.file_url, q.exam_type, q.year, s.subject_name, q.ai_analysis
            FROM question_papers q
            JOIN subjects s ON q.subject_id = s.subject_id
            WHERE q.paper_id = %s
        """, (paper_id,))
        row = cur.fetchone()

        if not row:
            return jsonify({"error": "Paper not found"}), 404

        file_url, exam_type, year, subject_name, ai_analysis = row

        # 1. RETURN CACHED RESULT (NO LIMIT)
        if ai_analysis:
            return jsonify({
                "subject": subject_name,
                "year": year,
                "exam_type": exam_type,
                "predictions": ai_analysis,
                "cached": True
            })

        # 2. RATE LIMIT CHECK (ONLY FOR NEW ANALYSIS)
        user_ip = request.remote_addr
        now = time.time()

        request_log.setdefault(user_ip, [])
        request_log[user_ip] = [
            t for t in request_log[user_ip] if now - t < 3600]

        if len(request_log[user_ip]) >= 5:
            return jsonify({
                "error": "Too many requests. You can analyse only 3 papers per hour. Please try later."
            }), 429

        # 3. CALL GEMINI
        try:
            predictions = analyze_with_gemini(file_url, subject_name)
            request_log[user_ip].append(now)
        except Exception as e:
            app.logger.exception("Gemini analysis failed: %s", str(e))
            err = str(e).lower()

            if "quota" in err or "rate" in err or "429" in err or "resource_exhausted" in err:
                return jsonify({
                    "error": "Daily analysis limit reached. Please try again tomorrow. 😊"
                }), 429

            if "503" in err or "unavailable" in err or "high demand" in err:
                return jsonify({
                    "error": "AI servers are busy right now. Please try again in a few minutes. 🙏"
                }), 503

            return jsonify({
                "error": "Analysis failed. Please try again later."
            }), 500

        # 4. SAVE RESULT (CACHE)
        try:
            cur.execute(
                "UPDATE question_papers SET ai_analysis = %s WHERE paper_id = %s",
                (predictions, paper_id)
            )
            conn.commit()
        except Exception:
            app.logger.exception("Failed to cache analysis")
            conn.rollback()

        # 5. RETURN RESULT
        return jsonify({
            "subject": subject_name,
            "year": year,
            "exam_type": exam_type,
            "predictions": predictions,
            "cached": False
        })

    except Exception:
        app.logger.exception("analyze_paper failed")
        return jsonify({
            "error": "Analysis failed. Please try again later."
        }), 500

    finally:
        cur.close()
        return_db(conn)

# ================================================================
# USER UPLOAD (STAGING)
# ================================================================

@app.route("/user-upload", methods=["GET", "POST"])
@limiter.limit("5 per hour")
def user_upload():
    depts_dict = get_departments_dict()
    if request.method == "POST":
        subject_raw = request.form.get("subject_id", "").strip()
        year = request.form.get("year", "").strip()
        exam_type = request.form.get("exam_type", "").strip()
        file = request.files.get("file")

        # --- Validate ---
        if not subject_raw or not year or not file or not file.filename:
            return render_template("user_upload.html",
                                   error="All fields are required.",
                                   subjects=get_subjects(), departments=depts_dict)
        if not allowed_file(file.filename):
            return render_template("user_upload.html",
                                   error="Only PDF files allowed.",
                                   subjects=get_subjects(), departments=depts_dict)
        try:
            subject_id = int(subject_raw)
            year_int = int(year)
            if year_int < 2000 or year_int > 2100:
                raise ValueError
        except ValueError:
            return render_template("user_upload.html",
                                   error="Invalid subject or year.",
                                   subjects=get_subjects(), departments=depts_dict)

        # --- Upload to staging bucket ---
        staging_path = f"pending/{subject_id}/{year_int}/{uuid.uuid4()}.pdf"
        try:
            file_bytes = file.read()
            compress_requested = request.form.get("compress_pdf", "true") in ("true", "1", "on", "yes")
            if compress_requested:
                file_bytes = compress_scanned_pdf(file_bytes)

            staging_supabase.storage.from_(STAGING_BUCKET).upload(
                staging_path,
                file_bytes,
                file_options={"content-type": "application/pdf"}
            )
        except Exception:
            app.logger.exception("Staging upload failed")
            return render_template("user_upload.html",
                                   error="Upload failed. Please try again.",
                                   subjects=get_subjects(), departments=depts_dict)

        # --- Save pending record ---
        conn = get_db()
        cur = conn.cursor()
        try:
            original_filename = secure_filename(file.filename)
            cur.execute("""
                INSERT INTO pending_papers
                (subject_id, year, exam_type, file_name, staging_path, submitted_by_ip, file_size)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (subject_id, year_int, exam_type, original_filename,
                  staging_path, request.remote_addr, len(file_bytes)))
            conn.commit()
            return render_template("user_upload.html", success=True,
                                   subjects=get_subjects(), departments=depts_dict)
        except Exception:
            app.logger.exception("Failed to save pending record")
            conn.rollback()
            try:
                staging_supabase.storage.from_(STAGING_BUCKET).remove([staging_path])
            except Exception:
                pass
            return render_template("user_upload.html",
                                   error="Submission failed. Please try again.",
                                   subjects=get_subjects(), departments=depts_dict)
        finally:
            cur.close()
            return_db(conn)

    return render_template("user_upload.html", subjects=get_subjects(), departments=depts_dict)


# ================================================================
# ADMIN — PENDING PAPERS
# ================================================================

@app.route("/admin/api/pending", methods=["GET"])
@login_required
@admin_required
def admin_get_pending():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT p.id, s.subject_name, s.semester, p.year, p.exam_type,
                   p.file_name, p.staging_path, p.submitted_by_ip,
                   p.submitted_at, p.status, p.file_size
            FROM pending_papers p
            LEFT JOIN subjects s ON p.subject_id = s.subject_id
            WHERE p.status = 'pending'
            ORDER BY p.submitted_at DESC
        """)
        rows = cur.fetchall()
        results = []
        for r in rows:
            staging_path = r[6]
            file_size_bytes = r[10]

            # Auto-fill file_size from Supabase storage if missing in DB
            if (file_size_bytes is None or file_size_bytes == 0) and staging_path:
                try:
                    dir_path = os.path.dirname(staging_path)
                    file_name = os.path.basename(staging_path)
                    res = staging_supabase.storage.from_(STAGING_BUCKET).list(dir_path)
                    for item in res:
                        if item.get("name") == file_name:
                            size_val = (item.get("metadata") or {}).get("size") or item.get("size")
                            if size_val:
                                file_size_bytes = int(size_val)
                                cur.execute("UPDATE pending_papers SET file_size = %s WHERE id = %s", (file_size_bytes, r[0]))
                                conn.commit()
                                break
                except Exception as e:
                    app.logger.warning(f"Could not auto-fill size for pending paper {r[0]}: {e}")

            # Generate signed URL valid for 1 hour
            try:
                signed = staging_supabase.storage.from_(STAGING_BUCKET).create_signed_url(
                    staging_path, 3600
                )
                preview_url = signed.get("signedURL") or signed.get("signed_url", "")
            except Exception:
                preview_url = ""

            results.append({
                "id": r[0],
                "subject_name": r[1],
                "semester": r[2],
                "year": r[3],
                "exam_type": r[4],
                "file_name": r[5],
                "staging_path": staging_path,
                "submitted_by_ip": r[7],
                "submitted_at": r[8].isoformat() if r[8] else None,
                "status": r[9],
                "file_size": file_size_bytes,
                "formatted_file_size": format_file_size(file_size_bytes),
                "preview_url": preview_url
            })
        return jsonify(results)
    finally:
        cur.close()
        return_db(conn)


@app.route("/admin/api/pending/<int:pending_id>/approve", methods=["POST"])
@login_required
@admin_required
def approve_pending(pending_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT p.subject_id, p.year, p.exam_type, p.file_name, p.staging_path
            FROM pending_papers p
            WHERE p.id = %s AND p.status = 'pending'
        """, (pending_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Pending paper not found"}), 404

        subject_id, year, exam_type, file_name, staging_path = row

        # 1. Download from staging
        file_bytes = staging_supabase.storage.from_(STAGING_BUCKET).download(staging_path)

        # 2. Upload to main bucket
        final_path = f"{subject_id}/{year}/{uuid.uuid4()}.pdf"
        supabase.storage.from_(SUPABASE_BUCKET).upload(
            final_path,
            file_bytes,
            file_options={"content-type": "application/pdf"}
        )
        final_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(final_path)

        # 3. Insert into question_papers
        cur.execute("""
            INSERT INTO question_papers
            (subject_id, year, file_name, file_url, exam_type, public_id, file_size)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (subject_id, year, file_name, final_url, exam_type, final_path, len(file_bytes)))

        # 4. Mark as approved
        cur.execute(
            "UPDATE pending_papers SET status = 'approved' WHERE id = %s",
            (pending_id,)
        )
        conn.commit()

        # 5. Delete from staging (non-critical)
        try:
            staging_supabase.storage.from_(STAGING_BUCKET).remove([staging_path])
        except Exception:
            pass

        return jsonify({"message": "Approved and published successfully."})

    except Exception as e:
        conn.rollback()
        app.logger.exception("Approve failed")
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        return_db(conn)


@app.route("/admin/api/pending/<int:pending_id>/reject", methods=["POST"])
@login_required
@admin_required
def reject_pending(pending_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT staging_path FROM pending_papers WHERE id = %s AND status = 'pending'",
            (pending_id,)
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404

        # 1. Delete from staging
        try:
            staging_supabase.storage.from_(STAGING_BUCKET).remove([row[0]])
        except Exception:
            pass

        # 2. Mark as rejected
        cur.execute(
            "UPDATE pending_papers SET status = 'rejected' WHERE id = %s",
            (pending_id,)
        )
        conn.commit()
        return jsonify({"message": "Rejected and removed."})

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        return_db(conn)


@app.route("/analyze/<int:paper_id>/refresh")
@login_required
@admin_required
def refresh_analysis(paper_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE question_papers SET ai_analysis = NULL WHERE paper_id = %s",
            (paper_id,)
        )
        conn.commit()
        return jsonify({"message": "Cache cleared. Next analyse will regenerate."})
    finally:
        cur.close()
        return_db(conn)

@app.route("/download/<int:paper_id>")
def download_paper(paper_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT file_url FROM question_papers WHERE paper_id = %s",
            (paper_id,)
        )
        row = cur.fetchone()
        if not row:
            return "Paper not found", 404
        return redirect(row[0])
    finally:
        cur.close()
        return_db(conn)
        
# ================================================================
# ADMIN PANEL
# ================================================================


@app.route("/admin")
@login_required
@admin_required
def admin_panel():
    departments_list = get_departments(active_only=False)
    return render_template(
        "admin.html",
        departments=get_departments_dict(),
        departments_list=departments_list
    )


@app.route("/admin/departments/add", methods=["POST"])
@login_required
@admin_required
def add_department():
    name = request.form.get("name", "").strip()
    slug = request.form.get("slug", "").strip().lower()
    code = request.form.get("code", "").strip() or name
    icon = request.form.get("icon", "").strip() or "📚"
    description = request.form.get("description", "").strip()
    is_coming_soon = request.form.get("is_coming_soon") == "on"

    if not name or not slug:
        flash("Department Name and Slug are required.")
        return redirect(url_for("admin_panel"))

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO departments (slug, name, code, icon, description, is_coming_soon, display_order)
            VALUES (%s, %s, %s, %s, %s, %s, (SELECT COALESCE(MAX(display_order), 0) + 1 FROM departments))
            ON CONFLICT (slug) DO UPDATE
            SET name = EXCLUDED.name, code = EXCLUDED.code, icon = EXCLUDED.icon,
                description = EXCLUDED.description, is_coming_soon = EXCLUDED.is_coming_soon;
        """, (slug, name, code, icon, description, is_coming_soon))
        conn.commit()
        flash(f"Department '{name}' saved successfully!")
    except Exception as e:
        conn.rollback()
        flash(f"Failed to add department: {e}")
    finally:
        cur.close()
        return_db(conn)

    return redirect(url_for("admin_panel"))


@app.route("/admin/api/stats")
@login_required
@admin_required
def admin_stats():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM subjects")
        total_subjects = cur.fetchone()[0] or 0

        cur.execute("SELECT COUNT(*) FROM question_papers")
        total_papers = cur.fetchone()[0] or 0

        cur.execute(
            "SELECT COUNT(*) FROM question_papers WHERE ai_analysis IS NOT NULL")
        papers_with_ai = cur.fetchone()[0] or 0

        cur.execute(
            "SELECT COUNT(*) FROM question_papers WHERE upload_date >= NOW() - INTERVAL '30 days'"
        )
        recent_uploads = cur.fetchone()[0] or 0

        return jsonify({
            "total_subjects": total_subjects,
            "total_papers": total_papers,
            "papers_with_ai": papers_with_ai,
            "recent_uploads": recent_uploads,
        })
    finally:
        cur.close()
        return_db(conn)


@app.route("/admin/api/subjects", methods=["GET"])
@login_required
@admin_required
def admin_get_subjects():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT s.subject_id, s.subject_name, s.semester, s.department,
                   COUNT(q.paper_id) AS paper_count
            FROM subjects s
            LEFT JOIN question_papers q ON q.subject_id = s.subject_id
            GROUP BY s.subject_id, s.subject_name, s.semester, s.department
            ORDER BY s.department NULLS LAST, s.semester NULLS LAST, s.subject_name
        """)
        rows = cur.fetchall()
        return jsonify([
            {
                "subject_id": r[0],
                "subject_name": r[1],
                "semester": r[2],
                "department": r[3],
                "paper_count": r[4],
            }
            for r in rows
        ])
    finally:
        cur.close()
        return_db(conn)


@app.route("/admin/api/subjects", methods=["POST"])
@login_required
@admin_required
def admin_create_subject():
    data = request.get_json()
    name = (data.get("subject_name") or "").strip()
    semester = data.get("semester")
    department = (data.get("department") or DEPARTMENTS[DEFAULT_DEPARTMENT]).strip()

    if not name:
        return jsonify({"error": "Subject name is required"}), 400
    if department not in DEPARTMENTS.values():
        return jsonify({"error": "Invalid department"}), 400
    if semester is not None:
        try:
            semester = int(semester)
            if not 1 <= semester <= 8:
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({"error": "Semester must be between 1 and 8"}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO subjects (subject_name, semester, department) VALUES (%s, %s, %s) RETURNING subject_id",
            (name, semester, department)
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        return jsonify({
            "subject_id": new_id,
            "subject_name": name,
            "semester": semester,
            "department": department,
        }), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        return_db(conn)


@app.route("/admin/api/subjects/bulk", methods=["POST"])
@login_required
@admin_required
def admin_bulk_create_subjects():
    """Insert multiple subjects sharing the same department and semester."""
    data = request.get_json()
    names = data.get("subject_names", [])
    semester = data.get("semester")
    department = (data.get("department") or DEPARTMENTS[DEFAULT_DEPARTMENT]).strip()

    # Validate department
    if department not in DEPARTMENTS.values():
        return jsonify({"error": "Invalid department"}), 400

    # Validate semester
    if semester is not None and semester != "":
        try:
            semester = int(semester)
            if not 1 <= semester <= 8:
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({"error": "Semester must be between 1 and 8"}), 400
    else:
        semester = None

    # Clean names — drop empty strings
    clean_names = [n.strip() for n in names if isinstance(n, str) and n.strip()]
    if not clean_names:
        return jsonify({"error": "At least one subject name is required"}), 400

    conn = get_db()
    cur = conn.cursor()
    results = []
    try:
        for name in clean_names:
            try:
                cur.execute(
                    "INSERT INTO subjects (subject_name, semester, department) "
                    "VALUES (%s, %s, %s) RETURNING subject_id",
                    (name, semester, department)
                )
                new_id = cur.fetchone()[0]
                results.append({"subject_name": name, "subject_id": new_id, "ok": True})
            except Exception as row_err:
                conn.rollback()
                results.append({"subject_name": name, "ok": False, "error": str(row_err)})
                # Re-open cursor after rollback so remaining rows can continue
                cur.close()
                cur = conn.cursor()
        conn.commit()
        return jsonify({"results": results}), 201
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        return_db(conn)


@app.route("/admin/api/subjects/<int:subject_id>", methods=["PUT"])
@login_required
@admin_required
def admin_update_subject(subject_id):
    data = request.get_json()
    name = (data.get("subject_name") or "").strip()
    semester = data.get("semester")
    department = (data.get("department") or DEPARTMENTS[DEFAULT_DEPARTMENT]).strip()

    if not name:
        return jsonify({"error": "Subject name is required"}), 400
    if department not in DEPARTMENTS.values():
        return jsonify({"error": "Invalid department"}), 400
    if semester is not None:
        try:
            semester = int(semester)
            if not 1 <= semester <= 8:
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({"error": "Semester must be between 1 and 8"}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE subjects SET subject_name=%s, semester=%s, department=%s WHERE subject_id=%s RETURNING subject_id",
            (name, semester, department, subject_id)
        )
        if cur.fetchone() is None:
            return jsonify({"error": "Subject not found"}), 404
        conn.commit()
        return jsonify({
            "subject_id": subject_id,
            "subject_name": name,
            "semester": semester,
            "department": department,
        })
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        return_db(conn)


@app.route("/admin/api/subjects/<int:subject_id>", methods=["DELETE"])
@login_required
@admin_required
def admin_delete_subject(subject_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM subjects WHERE subject_id=%s RETURNING subject_id", (subject_id,))
        if cur.fetchone() is None:
            return jsonify({"error": "Subject not found"}), 404
        conn.commit()
        return jsonify({"message": "Deleted"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        return_db(conn)


@app.route("/admin/api/papers", methods=["GET"])
@login_required
@admin_required
def admin_get_papers():
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT q.paper_id, s.subject_name, s.semester, q.year,
                   q.exam_type, q.file_url, q.upload_date, q.ai_analysis,
                   q.public_id, q.file_size
            FROM question_papers q
            LEFT JOIN subjects s ON q.subject_id = s.subject_id
            ORDER BY q.upload_date DESC NULLS LAST
        """)
        rows = cur.fetchall()
        results = []
        for r in rows:
            paper_id, subj_name, sem, yr, ex_type, file_url, up_date, ai_an, pub_id, size = r
            if size is None and file_url:
                try:
                    head_res = requests.head(file_url, timeout=3)
                    if head_res.status_code == 200 and 'Content-Length' in head_res.headers:
                        size = int(head_res.headers['Content-Length'])
                        cur.execute("UPDATE question_papers SET file_size = %s WHERE paper_id = %s", (size, paper_id))
                        conn.commit()
                except Exception:
                    pass

            results.append({
                "paper_id": paper_id,
                "subject_name": subj_name or "Unknown",
                "semester": sem,
                "year": yr,
                "exam_type": ex_type,
                "file_url": file_url,
                "upload_date": up_date.isoformat() if up_date else None,
                "ai_analysis": ai_an,
                "public_id": pub_id,
                "file_size": size,
                "file_size_formatted": format_file_size(size),
            })
        return jsonify(results)
    finally:
        cur.close()
        return_db(conn)


@app.route("/admin/api/papers/<int:paper_id>", methods=["DELETE"])
@login_required
@admin_required
def admin_delete_paper(paper_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT public_id FROM question_papers WHERE paper_id=%s",
            (paper_id,)
        )
        row = cur.fetchone()
        if row is None:
            return jsonify({"error": "Paper not found"}), 404

        public_id = row[0]

        if public_id:
            try:
                supabase.storage.from_(SUPABASE_BUCKET).remove([public_id])
            except Exception:
                pass

        cur.execute(
            "DELETE FROM question_papers WHERE paper_id=%s", (paper_id,))
        conn.commit()
        return jsonify({"message": "Deleted"}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        return_db(conn)


@app.route("/admin/api/change-password", methods=["POST"])
@login_required
@admin_required
def admin_change_password():
    data = request.get_json()
    current_password = data.get("current_password", "")
    new_password = data.get("new_password", "")

    if not new_password or len(new_password) < 6:
        return jsonify({"error": "New password must be at least 6 characters"}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT user_id, password_hash FROM users WHERE user_id=%s",
            (session["user_id"],)
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "User not found"}), 404

        if not check_password_hash(row[1], current_password):
            return jsonify({"error": "Current password is incorrect"}), 401

        new_hash = generate_password_hash(new_password)
        cur.execute(
            "UPDATE users SET password_hash=%s WHERE user_id=%s",
            (new_hash, row[0])
        )
        conn.commit()
        return jsonify({"message": "Password updated successfully"})
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        cur.close()
        return_db(conn)

# ================================================================
# PDF PROXY  (used by client-side bulk download / JSZip)
# ================================================================

@app.route('/proxy-pdf')
def proxy_pdf():
    """Fetch a Supabase-hosted PDF server-side and stream it to the client.
    This is needed because browsers block cross-origin fetch() to Supabase
    storage from JS (CORS + CSP), but a same-origin request to our own
    Flask server is always allowed.
    """
    url = request.args.get('url', '').strip()
    if not url:
        return 'Missing url parameter', 400

    # Security: only allow Supabase storage URLs
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if not (parsed.scheme in ('http', 'https') and
            parsed.hostname and
            parsed.hostname.endswith('.supabase.co')):
        return 'Forbidden: only Supabase storage URLs are allowed', 403

    try:
        r = requests.get(url, timeout=30, stream=True)
        r.raise_for_status()
    except requests.RequestException as e:
        return f'Upstream fetch failed: {e}', 502

    from flask import Response, stream_with_context
    content_type = r.headers.get('Content-Type', 'application/pdf')
    return Response(
        stream_with_context(r.iter_content(chunk_size=65536)),
        status=r.status_code,
        content_type=content_type,
        headers={
            'Content-Disposition': r.headers.get('Content-Disposition', 'inline'),
        }
    )

# ================================================================
# ERROR HANDLERS
# ================================================================


@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(
        os.environ.get('PORT', 10000)), debug=False)