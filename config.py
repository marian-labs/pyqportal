import os
from dotenv import load_dotenv

load_dotenv()

# Flask Configuration
SECRET_KEY = os.environ.get("SECRET_KEY", "dev_secret_key_change_in_production")
PORT = int(os.environ.get("PORT", 8000))
DATABASE_URL = os.environ.get("DATABASE_URL")
FLASK_ENV = os.environ.get("FLASK_ENV", "production")

# Google OAuth Configuration
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_ALLOWED_DOMAIN = os.environ.get("GOOGLE_ALLOWED_DOMAIN", "mariancollege.org").strip().lower()
ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ADMIN_EMAILS", "").split(",")
    if e.strip()
}

# Supabase Storage Configuration (Main)
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "question-papers")

# Supabase Storage Configuration (Staging)
STAGING_SUPABASE_URL = os.environ.get("STAGING_SUPABASE_URL")
STAGING_SUPABASE_KEY = os.environ.get("STAGING_SUPABASE_KEY")
STAGING_BUCKET = os.environ.get("STAGING_SUPABASE_BUCKET", "pending-uploads")

# Google Gemini AI API Key
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Default department slug
DEFAULT_DEPARTMENT = "bca"

# Allowed upload extensions
ALLOWED_EXTENSIONS = {"pdf"}
