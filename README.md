# AI WordPress Agent

A production-minded, modular AI WordPress Agent with a minimal black-and-white animated UI.

The system accepts natural-language website management requests, creates a structured execution plan, validates it, executes modular actions, verifies the outcome, and records every run.

## What Is Included

- Python standard-library API server
- SQLite execution log
- Modular planner, validator, executor, verifier, and action services
- WordPress REST client with safe dry-run fallback
- Anthropic-powered planning and content generation
- Source-grounded drafting from pasted notes, copied content, URLs, markdown, HTML, or JSON
- Optional live research through Tavily for current-topic prompts
- Minimal animated black-and-white frontend
- Smoke tests for planner, validator, and executor behavior

## Quick Start

```powershell
python -m backend.app
```

Then open:

```text
http://localhost:8787
```

## Configuration

Copy `.env.example` to `.env` and fill values as needed.

```text
AWA_DRY_RUN=true
WORDPRESS_BASE_URL=https://example.com
WORDPRESS_USERNAME=admin
WORDPRESS_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-5
ANTHROPIC_FALLBACK_MODELS=claude-opus-4-8,claude-haiku-4-5,claude-fable-5
AWA_ENABLE_LIVE_RESEARCH=false
AWA_WEB_SEARCH_PROVIDER=tavily
TAVILY_API_KEY=
```

When `AWA_DRY_RUN=true`, WordPress actions return realistic simulated responses and no remote mutation is attempted.

## Content Inputs

The agent supports three production paths:

- **Prompt only:** creates the requested post, page, update, rewrite, SEO task, taxonomy task, or WordPress action from the user instruction.
- **Prompt + source material:** treats pasted content as the primary evidence base and writes from it instead of producing a generic article.
- **Prompt + URLs:** scrapes the URL text first, summarizes it, then writes from that material.

For current-topic prompts such as trends, news, innovations, and market updates, enable live research with `AWA_ENABLE_LIVE_RESEARCH=true` and add a Tavily API key. The output review panel shows the research mode and sources reviewed.

## API

### `POST /api/run`

```json
{
  "prompt": "Create a blog post about AI search and optimize SEO",
  "source_material": "Optional source text, URLs, notes, HTML, markdown, or JSON"
}
```

Returns the plan, action results, verification, final response, and execution id.

### `GET /api/runs`

Returns recent execution logs.

### `GET /api/health`

Returns service health and dry-run status.

## Tests

```powershell
python -m unittest discover -s tests
```

## Quality Gate

```powershell
python quality_gate.py
```

The current gate must score `9.5` or higher before live deployment.

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md).

## Architecture

```text
User Prompt
  -> Prompt Normalizer
  -> Planner
  -> JSON Validator
  -> Execution Router
  -> Modular Services
  -> Verification Engine
  -> Final Response + SQLite Log
```

The app is designed so n8n workflows can later call the same HTTP API, or individual modules can be translated into n8n sub-workflows without changing the action schema.
