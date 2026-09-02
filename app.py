from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, abort, send_file
import os
import re
import time
from werkzeug.utils import secure_filename
from functools import wraps
import uuid
import requests
from datetime import timedelta
from google import genai
from google.genai import types
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_compress import Compress
from supabase import create_client
from authlib.integrations.flask_client import OAuth
from psycopg2.extras import execute_values
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_wtf.csrf import CSRFProtect, generate_csrf
from werkzeug.exceptions import RequestEntityTooLarge
from urllib.parse import urlparse, urljoin

PDF_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "papers")
os.makedirs(PDF_CACHE_DIR, exist_ok=True)

import config
from models import (
    get_db,
    return_db,
    init_db,
    clear_departments_cache,
    get_departments,
    get_department_by_slug,
    get_departments_dict,
    is_valid_department,
    get_department_id_by_name_or_slug,
    auto_remove_department_coming_soon,
    get_subjects,
    get_department_papers,
    get_department_papers_and_subjects,
)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.secret_key = config.SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=(config.FLASK_ENV != "development"),
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    TEMPLATES_AUTO_RELOAD=True,
    SEND_FILE_MAX_AGE_DEFAULT=timedelta(seconds=0) if config.FLASK_ENV == "development" else timedelta(days=7),
    COMPRESS_MIMETYPES=[
        "text/html",
        "text/css",
        "text/xml",
        "application/json",
        "application/javascript",
        "image/svg+xml",
    ],
    COMPRESS_LEVEL=6,
    COMPRESS_MIN_SIZE=500,
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,  # 25 MB
)
Compress(app)

csrf = CSRFProtect(app)


@app.context_processor
def inject_csrf_token():
    # Makes {{ csrf_token() }} available in every template
    return dict(csrf_token=generate_csrf)


@app.errorhandler(RequestEntityTooLarge)
def handle_large_file(e):
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
        return jsonify({"error": "File is too large."}), 413
    flash("File is too large. Maximum size is 25MB.", "error")
    return redirect(request.referrer or url_for("home"))


oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=config.GOOGLE_CLIENT_ID,
    client_secret=config.GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile'
    }
)

@app.after_request
def apply_security_headers(response):
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://www.googletagmanager.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self' https://www.google-analytics.com https://*.supabase.co; "
        "frame-src https://*.supabase.co; "
        "object-src 'none'; "
        "base-uri 'self';"
    )
    return response

client = genai.Client(api_key=config.GEMINI_API_KEY)

def get_user_rate_limit_key():
    """Rate limit per authenticated student account. Falls back to real IP if not logged in."""
    try:
        if session and session.get("user_id"):
            return f"user:{session.get('user_id')}"
    except Exception:
        pass
    return f"ip:{get_remote_address()}"

limiter = Limiter(
    get_user_rate_limit_key,
    app=app,
    default_limits=[],
    storage_uri="memory://"
)
request_log = {}

DEFAULT_DEPARTMENT = config.DEFAULT_DEPARTMENT
ALLOWED_EXTENSIONS = config.ALLOWED_EXTENSIONS

SUPABASE_URL = config.SUPABASE_URL
SUPABASE_KEY = config.SUPABASE_KEY
SUPABASE_BUCKET = config.SUPABASE_BUCKET
supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

STAGING_SUPABASE_URL = config.STAGING_SUPABASE_URL
STAGING_SUPABASE_KEY = config.STAGING_SUPABASE_KEY
STAGING_BUCKET = config.STAGING_BUCKET
staging_supabase = create_client(STAGING_SUPABASE_URL, STAGING_SUPABASE_KEY) if STAGING_SUPABASE_URL and STAGING_SUPABASE_KEY else None

# Initialize database schema and migrations
init_db()



# ---------- Auth helpers ----------


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
                return jsonify({"error": "Please sign in"}), 401
            # Store the destination, flash a message, bounce to home so user sees it
            session["post_login_redirect"] = request.url
            flash("Sign in with your @mariancollege.org account to access this page.", "auth")
            return redirect(url_for("home"))
        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute("SELECT token_version, role FROM users WHERE user_id = %s",
                        (session["user_id"],))
            row = cur.fetchone()
        finally:
            cur.close()
            return_db(conn)
        if not row or row[0] != session.get("token_version"):
            session.clear()
            if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
                return jsonify({"error": "Please sign in"}), 401
            session["post_login_redirect"] = request.url
            flash("Your session has expired. Please sign in again.", "auth")
            return redirect(url_for("home"))
        session["role"] = row[1]  # keep role fresh if ADMIN_EMAILS changed since login
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


def clean_user_name(raw_name, email=""):
    """
    Extracts only the user's name from Google profile info.
    Removes Marian College roll numbers/register numbers (e.g. 24UBC145, 23BCA101, etc.)
    and parenthetical codes, preserving clean student/staff names.
    """
    if not raw_name or not str(raw_name).strip():
        if email and "@" in email:
            local = email.split("@")[0]
            return re.sub(r"[._-]+", " ", local).strip().title()
        return "User"

    name = str(raw_name).strip()

    # 1. Remove parenthetical roll numbers or codes like (24UBC145) or (full name regno)
    name = re.sub(r"\s*\(\s*[^)]+\s*\)\s*", " ", name)

    # 2. Remove standard register numbers (e.g. 24UBC145, 23BCA001, 24PMC102, 22BCM050)
    # Matches 2 digits + 2-5 letters + 1-5 digits
    name = re.sub(r"\b\d{2}[A-Za-z]{2,5}\d{1,5}\b", "", name)

    # 3. Remove standalone numeric roll numbers or admission codes (e.g. " - 12345" or " 12345")
    name = re.sub(r"\s+[-–—/]?\s*\d{3,}\b", "", name)

    # 4. Remove trailing or leading hyphens/slashes/dots left behind
    name = re.sub(r"[\s\-–—/]+$", "", name)
    name = re.sub(r"^[\s\-–—/]+", "", name)

    # 5. Collapse duplicate whitespace
    name = re.sub(r"\s+", " ", name).strip()

    if not name:
        return raw_name.strip() if raw_name else (email.split("@")[0].title() if email else "User")

    return name


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


# ================================================================
# ROUTES
# ================================================================


def is_safe_redirect(target):
    """Return True only if target is a relative or same-host URL."""
    if not target:
        return False
    ref = urlparse(request.host_url)
    test = urlparse(urljoin(request.host_url, target))
    return test.scheme in ("http", "https") and test.netloc == ref.netloc


@app.route("/login")
@limiter.limit("200 per minute")
def login():
    """Login navbar button target — immediately kicks off Google OAuth, no intermediate page."""
    # If there's a ?next= param (e.g. from an old email link), preserve it
    next_url = request.args.get("next")
    if next_url and is_safe_redirect(next_url):
        session["post_login_redirect"] = next_url
    return redirect(url_for("login_google"))


@app.route("/login/google")
@limiter.limit("200 per minute")
def login_google():
    redirect_uri = url_for("google_callback", _external=True)
    return google.authorize_redirect(redirect_uri, hd=config.GOOGLE_ALLOWED_DOMAIN)


@app.route("/login/google/callback")
@limiter.limit("200 per minute")
def google_callback():
    try:
        token = google.authorize_access_token()
        user_info = token.get("userinfo")
        if not user_info:
            user_info = google.parse_id_token(token, nonce=None)
    except Exception as e:
        app.logger.exception("OAuth callback token exchange failed: %s", str(e))
        flash("Authentication failed. Please try again.")
        return redirect(url_for("login"))

    email = user_info.get("email", "").strip().lower()
    email_verified = user_info.get("email_verified", False)
    raw_name = user_info.get("name", "")
    name = clean_user_name(raw_name, email)
    google_sub = user_info.get("sub", "")

    # Use config module — single source of truth
    if not email_verified or not email.endswith("@" + config.GOOGLE_ALLOWED_DOMAIN):
        session.clear()
        flash(f"Access is restricted to @{config.GOOGLE_ALLOWED_DOMAIN} accounts only.", "error")
        return redirect(url_for("home"))

    role = "admin" if email in config.ADMIN_EMAILS else "user"

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO users (email, google_sub, name, role, last_login, token_version)
            VALUES (%s, %s, %s, %s, NOW(), 0)
            ON CONFLICT (email) DO UPDATE SET
                google_sub = EXCLUDED.google_sub,
                name = EXCLUDED.name,
                role = EXCLUDED.role,
                last_login = NOW()
            RETURNING user_id, token_version;
        """, (email, google_sub, name, role))
        user_row = cur.fetchone()
        conn.commit()
    except Exception as e:
        conn.rollback()
        app.logger.exception("Failed to upsert user record: %s", str(e))
        flash("An error occurred during sign in. Please try again.", "error")
        return redirect(url_for("home"))
    finally:
        cur.close()
        return_db(conn)

    user_id, token_version = user_row[0], user_row[1]

    # BUG FIX: pop redirect URL BEFORE session.clear() wipes it
    next_url = session.pop("post_login_redirect", None)
    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    session["email"] = email
    session["name"] = name
    session["role"] = role
    session["token_version"] = token_version

    # Defense-in-depth: re-validate the stored redirect on the way out
    if next_url and not is_safe_redirect(next_url):
        next_url = None

    return redirect(next_url or url_for("home"))


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
        departments_dict=get_departments_dict(departments)
    )


@app.route("/upload", methods=["GET", "POST"])
@login_required
@admin_required
def upload_page():
    departments_list = get_departments(active_only=True)
    depts_dict = get_departments_dict(departments_list)
    if request.method == "POST":
        conn = get_db()
        cur = conn.cursor()
        try:
            subject_raw = request.form.get("subject_id")
            if not subject_raw or subject_raw.strip() == "":
                return render_template("upload.html", error="Subject is required", subjects=get_subjects(), departments=depts_dict, departments_list=departments_list)
            try:
                subject_id = int(subject_raw)
            except ValueError:
                return render_template("upload.html", error="Invalid subject", subjects=get_subjects(), departments=depts_dict, departments_list=departments_list)

            cur.execute(
                "SELECT subject_id, subject_name FROM subjects WHERE subject_id = %s",
                (subject_id,)
            )
            row = cur.fetchone()
            if not row:
                return render_template("upload.html", error="Subject not found.", subjects=get_subjects(), departments=depts_dict, departments_list=departments_list)
            subject_id, subject_name = row[0], row[1]

            year = request.form.get("year")
            try:
                if not year or not str(year).strip():
                    raise ValueError("Year is required")
                year_int = int(str(year).split("-", 1)
                               [0].strip()) if "-" in str(year) else int(year)
            except (ValueError, TypeError) as exc:
                return render_template("upload.html", error=f"Invalid year: {exc}", subjects=get_subjects(), departments=depts_dict, departments_list=departments_list)

            file = request.files.get("file")
            if not file or not file.filename:
                return render_template("upload.html", error="No file provided", subjects=get_subjects(), departments=depts_dict, departments_list=departments_list)
            if not allowed_file(file.filename):
                return render_template("upload.html", error="Only PDF files are allowed", subjects=get_subjects(), departments=depts_dict, departments_list=departments_list)

            exam_type = request.form.get(
                "examType") or request.form.get("exam_type") or ""

            file_bytes = file.read()
            # 5 MB server-side cap for PDF uploads
            if len(file_bytes) > 5 * 1024 * 1024:
                return render_template("upload.html", error="File too large. Maximum PDF size is 5 MB.", subjects=get_subjects(), departments=depts_dict, departments_list=departments_list)
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
            auto_remove_department_coming_soon(cur, subject_id)
            conn.commit()
            clear_departments_cache()

            flash("Question paper uploaded successfully.")
            return redirect(url_for("upload_page"))

        except Exception:
            conn.rollback()
            app.logger.exception("Upload failed")
            return render_template("upload.html", error="Upload failed. Please try again.", subjects=get_subjects(), departments=depts_dict, departments_list=departments_list)
        finally:
            cur.close()
            return_db(conn)

    return render_template("upload.html", subjects=get_subjects(), departments=depts_dict, departments_list=departments_list)


@app.route("/papers")
@login_required
def view_papers():
    departments = get_departments(active_only=True)
    return render_template("papers_home.html", departments=departments, departments_dict=get_departments_dict(departments))


@app.route("/papers/<department>")
@login_required
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

    if not dept_obj:
        abort(404)

    department_name = dept_obj["name"]

    try:
        papers, subjects = get_department_papers_and_subjects(department_slug)
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
@login_required
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

        # 2. RATE LIMIT CHECK (ONLY FOR NEW ANALYSIS - per user account)
        user_key = get_user_rate_limit_key()
        now = time.time()

        request_log.setdefault(user_key, [])
        request_log[user_key] = [
            t for t in request_log[user_key] if now - t < 3600]

        if len(request_log[user_key]) >= 5:
            return jsonify({
                "error": "Too many requests. You can analyse only 3 papers per hour. Please try later."
            }), 429

        # 3. CALL GEMINI
        try:
            predictions = analyze_with_gemini(file_url, subject_name)
            request_log[user_key].append(now)
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
@login_required
@limiter.limit("5 per hour", methods=["POST"], key_func=get_user_rate_limit_key)
def user_upload():
    departments_list = get_departments(active_only=True)
    depts_dict = get_departments_dict(departments_list)
    if request.method == "POST":
        subject_raw = request.form.get("subject_id", "").strip()
        year = request.form.get("year", "").strip()
        exam_type = request.form.get("exam_type", "").strip()
        file = request.files.get("file")

        # --- Validate ---
        if not subject_raw or not year or not file or not file.filename:
            return render_template("user_upload.html",
                                   error="All fields are required.",
                                   subjects=get_subjects(), departments=depts_dict, departments_list=departments_list)
        if not allowed_file(file.filename):
            return render_template("user_upload.html",
                                   error="Only PDF files allowed.",
                                   subjects=get_subjects(), departments=depts_dict, departments_list=departments_list)
        try:
            subject_id = int(subject_raw)
            year_int = int(year)
            if year_int < 2000 or year_int > 2100:
                raise ValueError
        except ValueError:
            return render_template("user_upload.html",
                                   error="Invalid subject or year.",
                                   subjects=get_subjects(), departments=depts_dict, departments_list=departments_list)

        # --- Upload to staging bucket ---
        staging_path = f"pending/{subject_id}/{year_int}/{uuid.uuid4()}.pdf"
        try:
            file_bytes = file.read()
            # 5 MB server-side cap for PDF uploads
            if len(file_bytes) > 5 * 1024 * 1024:
                return render_template("user_upload.html",
                                       error="File too large. Maximum PDF size is 5 MB.",
                                       subjects=get_subjects(), departments=depts_dict, departments_list=departments_list)
            staging_supabase.storage.from_(STAGING_BUCKET).upload(
                staging_path,
                file_bytes,
                file_options={"content-type": "application/pdf"}
            )
        except Exception:
            app.logger.exception("Staging upload failed")
            return render_template("user_upload.html",
                                   error="Upload failed. Please try again.",
                                   subjects=get_subjects(), departments=depts_dict, departments_list=departments_list)

        # --- Save pending record ---
        conn = get_db()
        cur = conn.cursor()
        try:
            original_filename = secure_filename(file.filename)
            submitted_by_ip = get_remote_address()
            cur.execute("""
                INSERT INTO pending_papers
                (subject_id, year, exam_type, file_name, staging_path, submitted_by_ip, file_size)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (subject_id, year_int, exam_type, original_filename,
                  staging_path, submitted_by_ip, len(file_bytes)))
            conn.commit()
            return render_template("user_upload.html", success=True,
                                   subjects=get_subjects(), departments=depts_dict, departments_list=departments_list)
        except Exception:
            app.logger.exception("Failed to save pending record")
            conn.rollback()
            try:
                staging_supabase.storage.from_(STAGING_BUCKET).remove([staging_path])
            except Exception:
                pass
            return render_template("user_upload.html",
                                   error="Submission failed. Please try again.",
                                   subjects=get_subjects(), departments=depts_dict, departments_list=departments_list)
        finally:
            cur.close()
            return_db(conn)

    return render_template("user_upload.html", subjects=get_subjects(), departments=depts_dict, departments_list=departments_list)


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
        pending_size_updates = []
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
                                pending_size_updates.append((file_size_bytes, r[0]))
                                break
                except Exception as e:
                    app.logger.warning(f"Could not auto-fill size for pending paper {r[0]}: {e}")

            # Note: create_signed_url is an inherent per-file cost (each file genuinely needs its own signed URL).
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

        if pending_size_updates:
            try:
                cur.executemany("UPDATE pending_papers SET file_size = %s WHERE id = %s", pending_size_updates)
                conn.commit()
            except Exception as e:
                conn.rollback()
                app.logger.warning(f"Failed to batch update pending_papers file_size: {e}")

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
        auto_remove_department_coming_soon(cur, subject_id)
        conn.commit()
        clear_departments_cache()

        # 5. Delete from staging (non-critical)
        try:
            staging_supabase.storage.from_(STAGING_BUCKET).remove([staging_path])
        except Exception:
            pass

        return jsonify({"message": "Approved and published successfully."})

    except Exception:
        conn.rollback()
        app.logger.exception("Approve failed")
        return jsonify({"error": "Internal server error. Please try again."}), 500
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

    except Exception:
        conn.rollback()
        app.logger.exception("Reject pending failed")
        return jsonify({"error": "Internal server error. Please try again."}), 500
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

@app.route("/paper/<int:paper_id>/view")
@app.route("/paper/<int:paper_id>/download")
@app.route("/download/<int:paper_id>")
@login_required
def serve_paper(paper_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT q.file_url, q.file_name, s.subject_name, q.year
            FROM question_papers q
            JOIN subjects s ON q.subject_id = s.subject_id
            WHERE q.paper_id = %s
            """,
            (paper_id,)
        )
        row = cur.fetchone()
        if not row or not row[0]:
            flash("Question paper not found.", "error")
            return redirect(url_for("home"))

        file_url, file_name, subject_name, year = row[0], row[1], row[2], row[3]
        safe_name = secure_filename(f"{subject_name}_{year}.pdf") or file_name or "paper.pdf"
        is_download = request.path.endswith('/download') or request.args.get('download') is not None

        cache_file = os.path.join(PDF_CACHE_DIR, f"{paper_id}.pdf")

        # 1. If cached on local VPS disk, serve directly (0 KB Supabase egress, ~0.001s response)
        if os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
            return send_file(
                cache_file,
                mimetype='application/pdf',
                as_attachment=is_download,
                download_name=safe_name,
                max_age=86400
            )

        # 2. Cache miss (first view only) - download from Supabase, save to local VPS disk, and serve
        r = requests.get(file_url, timeout=30)
        r.raise_for_status()

        try:
            with open(cache_file, "wb") as f:
                f.write(r.content)
            return send_file(
                cache_file,
                mimetype='application/pdf',
                as_attachment=is_download,
                download_name=safe_name,
                max_age=86400
            )
        except Exception as write_err:
            app.logger.warning("Could not write to local cache: %s", write_err)
            from io import BytesIO
            return send_file(
                BytesIO(r.content),
                mimetype='application/pdf',
                as_attachment=is_download,
                download_name=safe_name,
                max_age=86400
            )

    except Exception as e:
        app.logger.exception("Failed to serve paper %s: %s", paper_id, str(e))
        flash("Could not load paper. Please try again.", "error")
        return redirect(url_for("home"))
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
        departments=get_departments_dict(departments_list),
        departments_list=departments_list
    )


@app.route("/admin/api/departments", methods=["GET"])
@login_required
@admin_required
def admin_get_departments():
    depts = get_departments(active_only=False)
    return jsonify(depts)


@app.route("/admin/api/departments", methods=["POST"])
@login_required
@admin_required
def admin_save_department():
    if request.is_json:
        data = request.get_json() or {}
        dept_id = data.get("id")
        name = (data.get("name") or "").strip()
        slug = (data.get("slug") or "").strip().lower()
        code = (data.get("code") or "").strip() or name
        description = (data.get("description") or "").strip()
        stream = (data.get("stream") or "FYUGP").strip()
        is_coming_soon = bool(data.get("is_coming_soon"))
        is_active = bool(data.get("is_active", True))
    else:
        dept_id = request.form.get("department_id")
        name = request.form.get("name", "").strip()
        slug = request.form.get("slug", "").strip().lower()
        code = request.form.get("code", "").strip() or name
        description = request.form.get("description", "").strip()
        stream = (request.form.get("stream") or "FYUGP").strip()
        is_coming_soon = request.form.get("is_coming_soon") in ("on", "true", "1", "yes")
        is_active = request.form.get("is_active", "on") in ("on", "true", "1", "yes")

    if not name or not slug:
        if not request.is_json:
            flash("Department Name and Slug are required.")
            return redirect(url_for("admin_panel"))
        return jsonify({"error": "Department Name and Slug are required."}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        if dept_id:
            cur.execute("""
                UPDATE departments
                SET name = %s, slug = %s, code = %s, description = %s,
                    is_coming_soon = %s, is_active = %s, stream = %s
                WHERE department_id = %s
                RETURNING department_id
            """, (name, slug, code, description, is_coming_soon, is_active, stream, int(dept_id)))
            row = cur.fetchone()
            if not row:
                if not request.is_json:
                    flash("Department not found.")
                    return redirect(url_for("admin_panel"))
                return jsonify({"error": "Department not found."}), 404
        else:
            cur.execute("""
                INSERT INTO departments (slug, name, code, description, is_coming_soon, is_active, display_order, stream)
                VALUES (%s, %s, %s, %s, %s, %s, (SELECT COALESCE(MAX(display_order), 0) + 1 FROM departments), %s)
                ON CONFLICT (slug) DO UPDATE
                SET name = EXCLUDED.name, code = EXCLUDED.code,
                    description = EXCLUDED.description, is_coming_soon = EXCLUDED.is_coming_soon,
                    is_active = EXCLUDED.is_active, stream = EXCLUDED.stream
                RETURNING department_id;
            """, (slug, name, code, description, is_coming_soon, is_active, stream))
            row = cur.fetchone()

        conn.commit()
        clear_departments_cache()

        if not request.is_json:
            flash(f"Department '{name}' saved successfully!")
            return redirect(url_for("admin_panel"))

        return jsonify({"message": f"Department '{name}' saved successfully!", "department_id": row[0]}), 200
    except Exception:
        conn.rollback()
        app.logger.exception("Failed to save department")
        if not request.is_json:
            flash("Failed to save department. Please try again.")
            return redirect(url_for("admin_panel"))
        return jsonify({"error": "Internal server error. Please try again."}), 500
    finally:
        cur.close()
        return_db(conn)


@app.route("/admin/departments/add", methods=["POST"])
@login_required
@admin_required
def add_department():
    return admin_save_department()


@app.route("/admin/api/departments/<int:dept_id>", methods=["DELETE"])
@login_required
@admin_required
def admin_delete_department(dept_id):
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM subjects WHERE department_id = %s", (dept_id,))
        count = cur.fetchone()[0] or 0
        if count > 0:
            return jsonify({"error": f"Cannot delete department. There are {count} subjects assigned to it."}), 400

        cur.execute("DELETE FROM departments WHERE department_id = %s RETURNING department_id", (dept_id,))
        if not cur.fetchone():
            return jsonify({"error": "Department not found"}), 404
        conn.commit()
        clear_departments_cache()
        return jsonify({"message": "Department deleted successfully"}), 200
    except Exception:
        conn.rollback()
        app.logger.exception("Delete department failed")
        return jsonify({"error": "Internal server error. Please try again."}), 500
    finally:
        cur.close()
        return_db(conn)


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
    data = request.get_json() or {}
    name = (data.get("subject_name") or "").strip()
    semester = data.get("semester")
    department = (data.get("department") or "BCA").strip()

    if not name:
        return jsonify({"error": "Subject name is required"}), 400
    if not is_valid_department(department):
        return jsonify({"error": "Invalid department"}), 400
    if semester is not None:
        try:
            semester = int(semester)
            if not 1 <= semester <= 10:
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({"error": "Semester must be between 1 and 10"}), 400

    dept_id = get_department_id_by_name_or_slug(department)
    if dept_id is None:
        return jsonify({"error": "Invalid department"}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO subjects (subject_name, semester, department, department_id) VALUES (%s, %s, %s, %s) RETURNING subject_id",
            (name, semester, department, dept_id)
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        clear_departments_cache()
        return jsonify({
            "subject_id": new_id,
            "subject_name": name,
            "semester": semester,
            "department": department,
        }), 201
    except Exception:
        conn.rollback()
        app.logger.exception("Create subject failed")
        return jsonify({"error": "Internal server error. Please try again."}), 500
    finally:
        cur.close()
        return_db(conn)


@app.route("/admin/api/subjects/bulk", methods=["POST"])
@login_required
@admin_required
def admin_bulk_create_subjects():
    """Insert multiple subjects sharing the same department and semester."""
    data = request.get_json() or {}
    names = data.get("subject_names", [])
    semester = data.get("semester")
    department = (data.get("department") or "BCA").strip()

    # Validate department against live DB data
    if not is_valid_department(department):
        return jsonify({"error": "Invalid department"}), 400

    dept_id = get_department_id_by_name_or_slug(department)
    if dept_id is None:
        return jsonify({"error": "Invalid department"}), 400

    # Validate semester
    if semester is not None and semester != "":
        try:
            semester = int(semester)
            if not 1 <= semester <= 10:
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({"error": "Semester must be between 1 and 10"}), 400
    else:
        semester = None

    # Clean names — drop empty strings
    clean_names = [n.strip() for n in names if isinstance(n, str) and n.strip()]
    if not clean_names:
        return jsonify({"error": "At least one subject name is required"}), 400

    # Note: Pre-validating inputs avoids DB-level batch rollbacks. A DB-level constraint failure
    # (rare, as subjects table has no unique constraint on subject_name) will roll back the batch transaction,
    # which is acceptable to maintain single round-trip performance.

    conn = get_db()
    cur = conn.cursor()
    try:
        records = [(name, semester, department, dept_id) for name in clean_names]
        query = """
            INSERT INTO subjects (subject_name, semester, department, department_id)
            VALUES %s
            RETURNING subject_name, subject_id
        """
        inserted_rows = execute_values(cur, query, records, fetch=True)
        conn.commit()
        clear_departments_cache()

        # Match results positionally with input records to preserve duplicate subject names correctly
        results = []
        for i, name in enumerate(clean_names):
            if i < len(inserted_rows):
                results.append({"subject_name": name, "subject_id": inserted_rows[i][1], "ok": True})
            else:
                results.append({"subject_name": name, "ok": False, "error": "Insertion failed"})
        return jsonify({"results": results}), 201
    except Exception:
        conn.rollback()
        app.logger.exception("Bulk create subjects failed")
        return jsonify({"error": "Internal server error. Please try again."}), 500
    finally:
        cur.close()
        return_db(conn)


@app.route("/admin/api/subjects/<int:subject_id>", methods=["PUT"])
@login_required
@admin_required
def admin_update_subject(subject_id):
    data = request.get_json() or {}
    name = (data.get("subject_name") or "").strip()
    semester = data.get("semester")
    department = (data.get("department") or "BCA").strip()

    if not name:
        return jsonify({"error": "Subject name is required"}), 400
    if not is_valid_department(department):
        return jsonify({"error": "Invalid department"}), 400
    if semester is not None:
        try:
            semester = int(semester)
            if not 1 <= semester <= 10:
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({"error": "Semester must be between 1 and 10"}), 400

    dept_id = get_department_id_by_name_or_slug(department)
    if dept_id is None:
        return jsonify({"error": "Invalid department"}), 400

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE subjects SET subject_name=%s, semester=%s, department=%s, department_id=%s WHERE subject_id=%s RETURNING subject_id",
            (name, semester, department, dept_id, subject_id)
        )
        if cur.fetchone() is None:
            return jsonify({"error": "Subject not found"}), 404
        conn.commit()
        clear_departments_cache()
        return jsonify({
            "subject_id": subject_id,
            "subject_name": name,
            "semester": semester,
            "department": department,
        })
    except Exception:
        conn.rollback()
        app.logger.exception("Update subject failed")
        return jsonify({"error": "Internal server error. Please try again."}), 500
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
        clear_departments_cache()
        return jsonify({"message": "Deleted"}), 200
    except Exception:
        conn.rollback()
        app.logger.exception("Delete subject failed")
        return jsonify({"error": "Internal server error. Please try again."}), 500
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
        pending_size_updates = []
        for r in rows:
            paper_id, subj_name, sem, yr, ex_type, file_url, up_date, ai_an, pub_id, size = r
            if size is None and file_url:
                try:
                    head_res = requests.head(file_url, timeout=3)
                    if head_res.status_code == 200 and 'Content-Length' in head_res.headers:
                        size = int(head_res.headers['Content-Length'])
                        pending_size_updates.append((size, paper_id))
                except Exception:
                    pass

            results.append({
                "paper_id": paper_id,
                "subject_name": subj_name or "Unknown",
                "semester": sem,
                "year": yr,
                "exam_type": ex_type,
                "file_url": f"/paper/{paper_id}/view",
                "upload_date": up_date.isoformat() if up_date else None,
                "ai_analysis": ai_an,
                "public_id": pub_id,
                "file_size": size,
                "file_size_formatted": format_file_size(size),
            })

        if pending_size_updates:
            try:
                cur.executemany("UPDATE question_papers SET file_size = %s WHERE paper_id = %s", pending_size_updates)
                conn.commit()
            except Exception as e:
                conn.rollback()
                app.logger.warning(f"Failed to batch update question_papers file_size: {e}")

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
        clear_departments_cache()

        # Remove local cached copy if present
        try:
            cached_path = os.path.join(PDF_CACHE_DIR, f"{paper_id}.pdf")
            if os.path.exists(cached_path):
                os.remove(cached_path)
        except Exception:
            pass

        return jsonify({"message": "Deleted"}), 200
    except Exception:
        conn.rollback()
        app.logger.exception("Delete paper failed")
        return jsonify({"error": "Internal server error. Please try again."}), 500
    finally:
        cur.close()
        return_db(conn)


@app.route('/proxy-pdf')
@login_required
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
    app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 8000)),
        debug=os.environ.get('FLASK_DEBUG', 'false').lower() in ('true', '1')
    )