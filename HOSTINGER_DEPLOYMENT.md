# Production Deployment Guide: Hostinger with Custom Domain

This guide covers deploying the Dockerized Flask application on Hostinger under your **custom domain** with SSL (HTTPS) enabled.

---

## 🏗️ Docker Architecture Overview

- **App Container**: Runs Gunicorn bound to `0.0.0.0:8000` inside Docker.
- **Nginx Reverse Proxy**: Receives public HTTPS traffic on ports `80`/`443` for your custom domain (`yourdomain.com`) and securely proxies it to the Docker container on port `8000`.
- **SSL (Let's Encrypt)**: Automatically encrypts all traffic to your domain.

---

## 🚀 Step-by-Step Production Deployment

### Step 1: DNS Setup (Point Domain to Hostinger)

In your domain provider DNS settings (or Hostinger DNS Zone Editor):
- **A Record**: `@` → `<YOUR_HOSTINGER_SERVER_IP>`
- **A Record / CNAME**: `www` → `<YOUR_HOSTINGER_SERVER_IP>` (or `@`)

---

### Step 2: Upload Application Files

SSH into your Hostinger server and clone/upload your codebase to `/var/www/pyq-hostinger`:

```bash
ssh root@<YOUR_HOSTINGER_SERVER_IP>
mkdir -p /var/www/pyq-hostinger
cd /var/www/pyq-hostinger
```

Upload or clone your project files here (`Dockerfile`, `docker-compose.yml`, `requirements.txt`, `app.py`, etc.).

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
ADMIN_USER=admin
ADMIN_PASS=your_admin_password
```

---

### Step 4: Build and Start Docker Container

Run Docker Compose to start the application in background mode:

```bash
docker compose up -d --build
```

Verify container status:
```bash
docker compose ps
```

---

### Step 5: Configure Nginx & SSL for Your Custom Domain

#### 1. Install Nginx and Certbot (if not installed)
```bash
apt update
apt install -y nginx certbot python3-certbot-nginx
```

#### 2. Create Nginx Site Configuration
Create `/etc/nginx/sites-available/pyq-app`:

```nginx
server {
    server_name yourdomain.com www.yourdomain.com;

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
*(Replace `yourdomain.com` with your actual domain).*

#### 3. Enable Site & Test Nginx
```bash
ln -s /etc/nginx/sites-available/pyq-app /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

#### 4. Issue Free SSL Certificate (HTTPS)
```bash
certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

Certbot will automatically configure HTTPS redirect and renew your SSL certificate.

---

### Option B: Hostinger Docker App Manager (hPanel)

If using Hostinger's managed Docker Application feature:

1. Open **Hostinger hPanel** → **Docker / Web Applications**.
2. Select your repository/directory.
3. Set **Port** to `8000`.
4. Under **Domains**, select your custom domain (`yourdomain.com`) and click **Enable SSL**.
5. Add all environment variables from `.env` in the hPanel env editor.
6. Click **Deploy**.
