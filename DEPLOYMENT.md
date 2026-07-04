# Production Deployment Guide

This project is intentionally dependency-light: Python standard library only, SQLite for logs, and static frontend assets.

## Production Safety Defaults

Keep these defaults until you have tested drafts end to end:

```env
AWA_DRY_RUN=true
AWA_AUTO_PUBLISH=false
```

When you are ready for live WordPress writes:

```env
AWA_DRY_RUN=false
AWA_AUTO_PUBLISH=false
```

With this setting, the agent can create real WordPress drafts, but publish requests are downgraded to drafts.

Only enable automatic publishing after you fully trust the workflow:

```env
AWA_AUTO_PUBLISH=true
```

## Required Environment

```env
AWA_HOST=0.0.0.0
AWA_PORT=8787
AWA_DRY_RUN=false
AWA_AUTO_PUBLISH=false
AWA_API_TOKEN=change-this-long-random-token
AWA_ALLOWED_WORDPRESS_HOSTS=marketinsights.business

WORDPRESS_BASE_URL=https://marketinsights.business
WORDPRESS_USERNAME=your-user
WORDPRESS_APP_PASSWORD=your-application-password
WORDPRESS_SEO_PLUGIN=rankmath

ANTHROPIC_API_KEY=your-key
ANTHROPIC_MODEL=your-enabled-model-id
```

## Windows Run

```powershell
python -m backend.app
```

Open:

```text
http://127.0.0.1:8787
```

## Linux Server Run

```bash
python3 -m backend.app
```

Recommended reverse proxy:

- Put Nginx/Caddy in front.
- Serve HTTPS.
- Keep `AWA_API_TOKEN` enabled.
- Do not expose the service without authentication.

## Docker

```bash
docker build -t ai-wordpress-agent .
docker run --env-file .env -p 8787:8787 ai-wordpress-agent
```

## Quality Gate

```bash
python quality_gate.py
```

The system should score at least `9.5` before live deployment.

## Live Checks

Read-only WordPress connectivity:

```bash
python -c "from backend.config import load_settings; from backend.services import WordPressClient; print(WordPressClient(load_settings()).test_connection())"
```

Anthropic connectivity:

```bash
python -c "from backend.config import load_settings; from backend.anthropic_client import AnthropicClient; print(AnthropicClient(load_settings()).complete_text('Reply exactly ok', 'health check', 20))"
```
