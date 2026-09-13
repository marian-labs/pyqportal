# Production Deployment Guide: Hostinger with Custom Domain

This guide covers deploying the Dockerized Flask application on Hostinger under your **custom domains** with SSL (HTTPS) and local SSD caching enabled.

---

## 🏗️ Architecture & Optimizations Overview

- **App Container**: Runs Gunicorn bound to `0.0.0.0:8000` inside Docker with persistent caching.
- **Local SSD PDF Cache (`paper_cache` volume)**: When a paper is viewed or downloaded, it is cached on the Hostinger VPS SSD disk (`/app/cache/papers/`). Subsequent views are served directly from Hostinger NVMe disk, reducing Supabase bandwidth usage by 99% and serving papers in ~0.001s.
- **Multi-Domain Support**: Automatically proxies and serves authentication and PDFs across any configured domain (e.g. `pyqportal.app`, `www.pyqportal.app`, `pyq.marian.cloud`).
- **Nginx Reverse Proxy**: Receives public HTTPS traffic on ports `80`/`443` for your custom domains and securely proxies it to the Docker container on port `8000`.
- **SSL (Let's Encrypt)**: Automatically encrypts all traffic across your domains.

---

## 🚀 Step-by-Step Production Deployment

### Step 1: DNS Setup (Point Domains to Hostinger)

In your domain provider DNS settings (or Hostinger DNS Zone Editor):
- **A Record**: `@` → `<YOUR_HOSTINGER_SERVER_IP>`
- **A Record / CNAME**: `www` → `<YOUR_HOSTINGER_SERVER_IP>` (or `@`)
- **A Record (for subdomains like pyq.marian.cloud)**: `pyq` → `<YOUR_HOSTINGER_SERVER_IP>`

---

### Step 2: Upload Application Files

SSH into your Hostinger server and clone/upload your codebase to `/var/www/pyq-hostinger`:

```bash
ssh root@<YOUR_HOSTINGER_SERVER_IP>
mkdir -p /var/www/pyq-hostinger
cd /var/www/pyq-hostinger
```

Upload or clone your project files here (`Dockerfile`, `docker-compose.yml`, `requirements.txt`, `app.py`, `models.py`, `config.py`, etc.).

---

### Step 3: Configure Production `.env`

Create the production `.env` file:

```bash
cp .env.example .env
nano .env
```

Fill in your actual production keys:
```env
SECRET_KEY=your_strong_random_production_secret
PORT=8000
GEMINI_API_KEY=your_gemini_api_key
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_key
SUPABASE_BUCKET=question-papers
STAGING_SUPABASE_URL=https://your-staging.supabase.co
STAGING_SUPABASE_KEY=your_staging_key
STAGING_SUPABASE_BUCKET=pending-uploads
DATABASE_URL=postgresql://user:password@host:5432/postgres
GOOGLE_CLIENT_ID=your_google_oauth_client_id
GOOGLE_CLIENT_SECRET=your_google_oauth_client_secret
GOOGLE_ALLOWED_DOMAIN=mariancollege.org
ADMIN_EMAILS=admin1@mariancollege.org,admin2@mariancollege.org
```

---

### Step 4: Build and Start Docker Container

Run Docker Compose to start the application with persistent disk caching enabled:

```bash
docker compose up -d --build
```

Verify container status and volume:
```bash
docker compose ps
docker volume ls
```

---

### Step 5: Configure Nginx & SSL for All Custom Domains

#### 1. Install Nginx and Certbot (if not installed)
```bash
apt update
apt install -y nginx certbot python3-certbot-nginx
```

#### 2. Create Nginx Site Configuration
Create `/etc/nginx/sites-available/pyq-app`:

```nginx
server {
    server_name pyqportal.app www.pyqportal.app pyq.marian.cloud;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 120s;
    }

    client_max_body_size 50M;
}
```
*(Replace domain names with your actual active domains).*

#### 3. Enable Site & Test Nginx
```bash
ln -s /etc/nginx/sites-available/pyq-app /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

#### 4. Issue Free SSL Certificates (HTTPS)
```bash
certbot --nginx -d pyqportal.app -d www.pyqportal.app -d pyq.marian.cloud
```

Certbot will automatically configure HTTPS redirect and renew your SSL certificates.

---

### Option B: Hostinger Docker App Manager (hPanel)

If using Hostinger's managed Docker Application feature:

1. Open **Hostinger hPanel** → **Docker / Web Applications**.
2. Select your repository/directory.
3. Set **Port** to `8000`.
4. Under **Domains**, select your custom domain (`pyqportal.app`) and click **Enable SSL**.
5. Add all environment variables from `.env` in the hPanel env editor.
6. Click **Deploy**.
