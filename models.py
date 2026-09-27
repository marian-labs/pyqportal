import time
import psycopg2
from psycopg2 import pool
import config

_DEPARTMENTS_CACHE = {}
_DEPARTMENT_PAPERS_CACHE = {}
_CACHE_TTL = 300
_CONN_IDLE_TIMEOUT = 30
_conn_last_used = {}

# DB Connection Pool (Thread-safe for Gunicorn multi-threading & background workers)
try:
    db_pool = pool.ThreadedConnectionPool(1, 10, config.DATABASE_URL)
except Exception:
    db_pool = None


def get_db():
    try:
        conn = db_pool.getconn()
        now = time.time()
        conn_id = id(conn)
        last_used = _conn_last_used.get(conn_id, 0)

        # Health check only if connection has been idle for > 30s or is closed
        if getattr(conn, 'closed', 0) != 0 or (now - last_used) > _CONN_IDLE_TIMEOUT:
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.close()
            except Exception:
                try:
                    db_pool.putconn(conn, close=True)
                except Exception:
                    pass
                conn = psycopg2.connect(config.DATABASE_URL)
                _conn_last_used[id(conn)] = now
                return conn

        _conn_last_used[conn_id] = now
        return conn
    except Exception:
        return psycopg2.connect(config.DATABASE_URL)


def return_db(conn):
    try:
        if conn:
            _conn_last_used[id(conn)] = time.time()
            db_pool.putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


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
                description TEXT,
                is_active BOOLEAN DEFAULT true,
                is_coming_soon BOOLEAN DEFAULT false,
                display_order INTEGER DEFAULT 0,
                stream VARCHAR(50) DEFAULT 'FYUGP',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id SERIAL PRIMARY KEY,
                username VARCHAR(100) UNIQUE,
                password_hash TEXT,
                email VARCHAR(255) UNIQUE,
                google_sub VARCHAR(255) UNIQUE,
                name VARCHAR(255),
                role VARCHAR(20) NOT NULL DEFAULT 'user',
                last_login TIMESTAMP,
                token_version INTEGER DEFAULT 0,
                approved_papers_count INTEGER DEFAULT 0
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS subjects (
                subject_id SERIAL PRIMARY KEY,
                subject_name VARCHAR(255) NOT NULL,
                semester INTEGER,
                department VARCHAR(50),
                department_id INTEGER REFERENCES departments(department_id),
                course_type VARCHAR(20) DEFAULT 'CORE'
            );
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
                file_size BIGINT,
                user_id INTEGER REFERENCES users(user_id)
            );
        """)
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
                file_size BIGINT,
                user_id INTEGER REFERENCES users(user_id)
            );
        """)

        # Alter table if needed for existing DB
        cur.execute("ALTER TABLE subjects ADD COLUMN IF NOT EXISTS course_type VARCHAR(20) DEFAULT 'CORE';")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS approved_papers_count INTEGER DEFAULT 0;")
        cur.execute("ALTER TABLE pending_papers ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(user_id);")
        cur.execute("ALTER TABLE question_papers ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(user_id);")

        # Indexes for query performance optimization
        cur.execute("CREATE INDEX IF NOT EXISTS idx_departments_slug_active ON departments(slug, is_active);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_subjects_department_id ON subjects(department_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_subjects_dept_sem ON subjects(department_id, semester);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_subjects_course_type_sem ON subjects(course_type, semester);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_question_papers_subject_id ON question_papers(subject_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_question_papers_subject_year ON question_papers(subject_id, year DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_question_papers_upload_date ON question_papers(upload_date);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_question_papers_user_id ON question_papers(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pending_papers_status ON pending_papers(status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pending_papers_subject_id ON pending_papers(subject_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pending_papers_user_id ON pending_papers(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pending_papers_status_submitted ON pending_papers(status, submitted_at DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_qp_subj_yr_exam ON question_papers(subject_id, year, exam_type);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_pp_subj_yr_exam ON pending_papers(subject_id, year, exam_type) WHERE status = 'pending';")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_last_login ON users(last_login DESC NULLS LAST, user_id DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_approved_papers ON users(approved_papers_count DESC);")

        conn.commit()
    finally:
        cur.close()
        return_db(conn)


# ---------- Department helpers & models ----------

def clear_departments_cache():
    global _DEPARTMENTS_CACHE, _DEPARTMENT_PAPERS_CACHE
    _DEPARTMENTS_CACHE.clear()
    _DEPARTMENT_PAPERS_CACHE.clear()


def get_departments(active_only=True):
    now = time.time()
    cache_entry = _DEPARTMENTS_CACHE.get(active_only)
    if cache_entry and (now - cache_entry["timestamp"]) < _CACHE_TTL:
        return cache_entry["data"]

    conn = get_db()
    cur = conn.cursor()
    try:
        query = """
            SELECT d.department_id, d.slug, d.name, d.code, d.description, d.is_active, d.is_coming_soon, d.display_order,
                   d.stream, COUNT(DISTINCT q.paper_id) as paper_count
            FROM departments d
            LEFT JOIN (
                SELECT q.paper_id, s.department_id, COALESCE(s.course_type, 'CORE') as course_type, s.semester
                FROM question_papers q
                JOIN subjects s ON q.subject_id = s.subject_id
            ) q ON (
                (q.course_type = 'CORE' AND q.department_id = d.department_id)
                OR (
                    COALESCE(d.stream, 'FYUGP') = 'FYUGP' AND (
                        (q.course_type = 'AEC' AND q.semester IN (1, 2))
                        OR (LOWER(d.slug) NOT IN ('bca', 'bba') AND q.course_type = 'MDC' AND q.semester IN (1, 2))
                        OR (LOWER(d.slug) NOT IN ('bca', 'bba') AND q.course_type = 'VAC' AND q.semester IN (3, 4))
                    )
                )
            )
            WHERE (%s = false OR d.is_active = true)
            GROUP BY d.department_id, d.slug, d.name, d.code, d.description, d.is_active, d.is_coming_soon, d.display_order, d.stream
            ORDER BY d.display_order ASC, d.name ASC;
        """
        cur.execute(query, (active_only,))
        rows = cur.fetchall()
        result = [
            {
                "id": r[0],
                "slug": r[1],
                "name": r[2],
                "code": r[3] or r[2],
                "description": r[4] or "",
                "is_active": r[5],
                "is_coming_soon": r[6],
                "display_order": r[7],
                "stream": r[8] or ("5year" if r[1] == "msc-physics" else "FYUGP"),
                "paper_count": r[9] or 0
            }
            for r in rows
        ]
        _DEPARTMENTS_CACHE[active_only] = {"data": result, "timestamp": now}
        return result
    except Exception:
        return cache_entry["data"] if cache_entry else []
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
            SELECT department_id, slug, name, code, description, is_active, is_coming_soon, display_order, stream
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
            "description": row[4] or "",
            "is_active": row[5],
            "is_coming_soon": row[6],
            "display_order": row[7],
            "stream": row[8] or ("5year" if row[1] == "msc-physics" else "FYUGP")
        }
    finally:
        cur.close()
        return_db(conn)


def get_departments_dict(departments_list=None):
    depts = departments_list if departments_list is not None else get_departments(active_only=False)
    if not depts:
        return {}
    return {d["slug"]: d["name"] for d in depts}


def is_valid_department(name):
    if not name:
        return False
    name_str = str(name).strip()
    if name_str.lower() in ('general', 'common'):
        return True
    dept_dict = get_departments_dict()
    return (
        name_str in dept_dict.values()
        or name_str in dept_dict.keys()
        or name_str.lower() in [k.lower() for k in dept_dict.keys()]
        or name_str.lower() in [v.lower() for v in dept_dict.values()]
    )


def get_department_id_by_name_or_slug(dept_input):
    if not dept_input:
        return None
    dept_input_lower = str(dept_input).strip().lower()
    if dept_input_lower in ('general', 'common'):
        return None
    depts = get_departments(active_only=False)
    for d in depts:
        if d["slug"].lower() == dept_input_lower or d["name"].lower() == dept_input_lower or (d.get("code") and d["code"].lower() == dept_input_lower):
            return d["id"]
    return None


def auto_remove_department_coming_soon(cur, subject_id):
    """If the department of the subject is marked as 'is_coming_soon', automatically remove that flag."""
    if not subject_id:
        return
    try:
        cur.execute("""
            UPDATE departments
            SET is_coming_soon = false
            WHERE department_id = (SELECT department_id FROM subjects WHERE subject_id = %s)
              AND is_coming_soon = true
            RETURNING department_id;
        """, (subject_id,))
        updated = cur.fetchone()
        if updated:
            clear_departments_cache()
    except Exception:
        pass


def get_subjects(department=None):
    conn = get_db()
    cur = conn.cursor()
    try:
        if department:
            dept_clean = str(department).strip().lower()
            if dept_clean in ('general', 'common'):
                cur.execute(
                    """
                    SELECT s.subject_id, s.subject_name, s.semester, 'General' as department,
                           COALESCE(s.course_type, 'CORE') as course_type
                    FROM subjects s
                    WHERE s.course_type IN ('AEC', 'MDC', 'VAC')
                    ORDER BY s.semester, s.subject_name
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT s.subject_id, s.subject_name, s.semester,
                           COALESCE(d.name, s.department) as department,
                           COALESCE(s.course_type, 'CORE') as course_type
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
                SELECT s.subject_id, s.subject_name, s.semester,
                       COALESCE(d.name, s.department) as department,
                       COALESCE(s.course_type, 'CORE') as course_type
                FROM subjects s
                LEFT JOIN departments d ON s.department_id = d.department_id
                ORDER BY s.semester, s.subject_name
                """
            )
        rows = cur.fetchall()
        return [
            {
                "subject_id": r[0],
                "subject_name": r[1],
                "semester": r[2],
                "department": r[3],
                "course_type": r[4] or "CORE",
            }
            for r in rows
        ]
    finally:
        cur.close()
        return_db(conn)


def get_department_papers(department_slug_or_name):
    if not department_slug_or_name:
        return []
    key = str(department_slug_or_name).strip().lower()
    now = time.time()
    cache_entry = _DEPARTMENT_PAPERS_CACHE.get(key)
    if cache_entry and (now - cache_entry["timestamp"]) < _CACHE_TTL:
        return cache_entry["papers"]

    dept = get_department_by_slug(key)
    dept_id = dept["id"] if dept else None
    dept_stream = dept["stream"] if dept else ("5year" if key == "msc-physics" else "FYUGP")
    dept_slug = (dept["slug"] if dept else key).lower()

    conn = get_db()
    cur = conn.cursor()
    papers = []
    try:
        cur.execute(
            """
            SELECT s.subject_name, s.semester, q.year, q.file_url, q.exam_type, q.paper_id,
                   COALESCE(s.course_type, 'CORE') as course_type,
                   COALESCE(s.department, 'General') as department,
                   CASE WHEN q.ai_analysis IS NOT NULL THEN true ELSE false END as is_analysed
            FROM question_papers q
            JOIN subjects s ON q.subject_id = s.subject_id
            WHERE (
                (s.department_id = %s AND COALESCE(s.course_type, 'CORE') = 'CORE')
                OR (
                    %s = 'FYUGP' AND (
                        (s.course_type = 'AEC' AND s.semester IN (1, 2))
                        OR (%s NOT IN ('bca', 'bba') AND s.course_type = 'MDC' AND s.semester IN (1, 2))
                        OR (%s NOT IN ('bca', 'bba') AND s.course_type = 'VAC' AND s.semester IN (3, 4))
                    )
                )
            )
            ORDER BY s.subject_name ASC, q.year DESC
            """,
            (dept_id, dept_stream, dept_slug, dept_slug),
        )
        rows = cur.fetchall()

        for subject_name, semester, year, file_url, exam_type, paper_id, course_type, dept_name, is_analysed in rows:
            papers.append({
                "subject": subject_name,
                "year": year,
                "semester": semester,
                "department": dept_name or "",
                "course_type": course_type or "CORE",
                "examType": exam_type or "—",
                "file_url": f"/paper/{paper_id}/view",
                "download_url": f"/paper/{paper_id}/download",
                "paper_id": paper_id,
                "is_analysed": is_analysed,
            })
        _DEPARTMENT_PAPERS_CACHE[key] = {"papers": papers, "timestamp": now}
        return papers
    finally:
        cur.close()
        return_db(conn)


def get_department_papers_and_subjects(department_slug):
    if not department_slug:
        return [], []
    key = str(department_slug).strip().lower()
    now = time.time()
    cache_entry = _DEPARTMENT_PAPERS_CACHE.get(key)
    if cache_entry and (now - cache_entry["timestamp"]) < _CACHE_TTL and "subjects" in cache_entry:
        return cache_entry["papers"], cache_entry["subjects"]

    dept = get_department_by_slug(key)
    dept_id = dept["id"] if dept else None
    dept_stream = dept["stream"] if dept else ("5year" if key == "msc-physics" else "FYUGP")
    dept_slug = (dept["slug"] if dept else key).lower()

    conn = get_db()
    cur = conn.cursor()
    papers = []
    subjects = []
    try:
        cur.execute(
            """
            SELECT s.subject_name, s.semester, q.year, q.file_url, q.exam_type, q.paper_id,
                   COALESCE(s.course_type, 'CORE') as course_type,
                   COALESCE(s.department, 'General') as department,
                   CASE WHEN q.ai_analysis IS NOT NULL THEN true ELSE false END as is_analysed
            FROM question_papers q
            JOIN subjects s ON q.subject_id = s.subject_id
            WHERE (
                (s.department_id = %s AND COALESCE(s.course_type, 'CORE') = 'CORE')
                OR (
                    %s = 'FYUGP' AND (
                        (s.course_type = 'AEC' AND s.semester IN (1, 2))
                        OR (%s NOT IN ('bca', 'bba') AND s.course_type = 'MDC' AND s.semester IN (1, 2))
                        OR (%s NOT IN ('bca', 'bba') AND s.course_type = 'VAC' AND s.semester IN (3, 4))
                    )
                )
            )
            ORDER BY s.subject_name ASC, q.year DESC
            """,
            (dept_id, dept_stream, dept_slug, dept_slug),
        )
        rows = cur.fetchall()
        for subject_name, semester, year, file_url, exam_type, paper_id, course_type, dept_name, is_analysed in rows:
            papers.append({
                "subject": subject_name,
                "year": year,
                "semester": semester,
                "department": dept_name or "",
                "course_type": course_type or "CORE",
                "examType": exam_type or "—",
                "file_url": f"/paper/{paper_id}/view",
                "download_url": f"/paper/{paper_id}/download",
                "paper_id": paper_id,
                "is_analysed": is_analysed,
            })

        cur.execute(
            """
            SELECT s.subject_id, s.subject_name, s.semester,
                   COALESCE(s.department, 'General') as department,
                   COALESCE(s.course_type, 'CORE') as course_type
            FROM subjects s
            WHERE (
                (s.department_id = %s AND COALESCE(s.course_type, 'CORE') = 'CORE')
                OR (
                    %s = 'FYUGP' AND (
                        (s.course_type = 'AEC' AND s.semester IN (1, 2))
                        OR (%s NOT IN ('bca', 'bba') AND s.course_type = 'MDC' AND s.semester IN (1, 2))
                        OR (%s NOT IN ('bca', 'bba') AND s.course_type = 'VAC' AND s.semester IN (3, 4))
                    )
                )
            )
            ORDER BY s.semester, s.subject_name
            """,
            (dept_id, dept_stream, dept_slug, dept_slug),
        )
        sub_rows = cur.fetchall()
        subjects = [
            {
                "subject_id": r[0],
                "subject_name": r[1],
                "semester": r[2],
                "department": r[3],
                "course_type": r[4] or "CORE",
            }
            for r in sub_rows
        ]
        _DEPARTMENT_PAPERS_CACHE[key] = {"papers": papers, "subjects": subjects, "timestamp": now}
        return papers, subjects
    finally:
        cur.close()
        return_db(conn)


def check_duplicate_paper(subject_id, year, exam_type, include_pending=True):
    """
    Checks if a paper already exists for the given subject_id, year, and exam_type.
    Rules:
    - Same subject (and thus same sem and department/general), same year, and same exam_type -> DUPLICATE.
    - Same year, different exam_type -> NOT duplicate.
    - Different year, same exam_type -> NOT duplicate.
    Returns:
        dict: {"is_duplicate": bool, "status": "approved" | "pending" | None, "message": str}
    """
    if not subject_id or not year or not exam_type:
        return {"is_duplicate": False, "status": None, "message": ""}

    clean_exam_type = str(exam_type).strip()
    if not clean_exam_type:
        return {"is_duplicate": False, "status": None, "message": ""}

    conn = get_db()
    cur = conn.cursor()
    try:
        # 1. Check published question_papers
        cur.execute(
            """
            SELECT paper_id FROM question_papers
            WHERE subject_id = %s
              AND year = %s
              AND LOWER(TRIM(exam_type)) = LOWER(TRIM(%s))
            LIMIT 1;
            """,
            (subject_id, year, clean_exam_type)
        )
        if cur.fetchone():
            return {
                "is_duplicate": True,
                "status": "approved",
                "message": "Already this paper is uploaded."
            }

        # 2. Check pending_papers (under review)
        if include_pending:
            cur.execute(
                """
                SELECT id FROM pending_papers
                WHERE subject_id = %s
                  AND year = %s
                  AND LOWER(TRIM(exam_type)) = LOWER(TRIM(%s))
                  AND status = 'pending'
                LIMIT 1;
                """,
                (subject_id, year, clean_exam_type)
            )
            if cur.fetchone():
                return {
                    "is_duplicate": True,
                    "status": "pending",
                    "message": "Already this paper is uploaded and pending review."
                }

        return {"is_duplicate": False, "status": None, "message": ""}
    finally:
        cur.close()
        return_db(conn)

