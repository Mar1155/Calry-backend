# Calry AI-First Calorie Tracking Backend

This is the production-ready backend foundation for **Calry**, a modern AI-first calorie tracking mobile app. It is built using a highly optimized, clean, and modular architecture designed for rapid feature iteration and seamless integration with a Flutter frontend.

---

## Technical Stack

*   **Core:** [Python 3.12](https://www.python.org/) / [FastAPI](https://fastapi.tiangolo.com/) (Asynchronous endpoints)
*   **Database:** [PostgreSQL](https://www.postgresql.org/) / [SQLAlchemy 2.0](https://www.sqlalchemy.org/) (Async engine, selectin lazy-loading, connection pooling)
*   **Migrations:** [Alembic](https://alembic.sqlalchemy.org/) (Production-safe, Railway-compatible environment setup)
*   **Serialization & Settings:** [Pydantic V2](https://docs.pydantic.dev/) & `pydantic-settings`
*   **Authentication:** [Firebase Authentication](https://firebase.google.com/) JWT verification (with zero-config local development mock fallback)
*   **Storage:** [Firebase Storage](https://firebase.google.com/) (media URLs)
*   **AI Integration:** Multimodal [Google Gemini 1.5 Flash](https://deepmind.google/technologies/gemini/) & [OpenAI GPT-4o](https://openai.com/) adapters (with deterministic mock engines when API keys are absent)
*   **Tooling:** [Ruff](https://github.com/astral-sh/ruff) (Linter), [Black](https://github.com/psf/black) (Formatter), [Pytest](https://docs.pytest.org/) (Async testing)

---

## Directory Structure

```
├── app/
│   ├── api/
│   │   └── v1/
│   │       └── routes/           # Versioned API routes (meals, users, summaries, etc.)
│   ├── core/                     # Config settings, logging, custom exceptions, security
│   ├── db/                       # SQLAlchemy connection initialization & engine setup
│   ├── dependencies/             # Dependency injections (get_db, get_current_user)
│   ├── models/                   # SQLAlchemy 2.0 models (User, Meal, MealItem, etc.)
│   ├── schemas/                  # Pydantic V2 validation schemas
│   ├── services/                 # Business logic controllers (caloric daily summary syncing)
│   ├── ai/                       # AI provider abstractions, model adapters, orchestrator
│   └── main.py                   # App entrypoint, CORS configuration, exception handlers
├── alembic/                      # Database DDL migration revisions
├── tests/                        # Integration and unit tests using in-memory SQLite
├── requirements.txt              # Runtime dependencies (installed into the image)
├── requirements-dev.txt          # Dev/test dependencies (pytest, ruff, black)
├── Dockerfile                    # Multi-stage, slim, non-root production image
├── railway.json                  # Railway build/deploy config (Dockerfile + healthcheck)
├── start.sh                      # Container entrypoint: migrate, then serve on $PORT
├── pyproject.toml                # Black & Ruff formatting configurations
├── .env.example                  # Environment configuration template
└── README.md                     # This documentation
```

---

## Local Setup

### 1. Clone & Setup Virtual Environment
```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env` and fill in local connection credentials.
```bash
cp .env.example .env
```

### 3. Run Database Migrations
Make sure PostgreSQL is running locally with a database matching your `.env` connection URL, then execute:
```bash
alembic upgrade head
```

### 4. Start the Server Locally
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Open [http://localhost:8000/docs](http://localhost:8000/docs) in your browser to view the interactive OpenAPI documentation!

---

## Running Tests

The test suite runs with **SQLite in-memory** (`sqlite+aiosqlite:///:memory:`). You **do not need a running PostgreSQL server** or external AI/Firebase keys to run the entire test suite.

Run all tests instantly:
```bash
pytest -v
```

---

## Railway Production Deployment

Deployment is containerized and declarative. The repo ships:

* **`Dockerfile`** — multi-stage, slim, non-root Python 3.12 image (runtime deps only).
* **`railway.json`** — builds from the Dockerfile, health-checks `/api/v1/health`, restarts `ON_FAILURE`.
* **`start.sh`** — runs `alembic upgrade head`, then `uvicorn` bound to `$PORT` (migrations apply on every deploy).

### 1. Provision

1. Create a Railway project and add the **PostgreSQL** plugin.
2. Add a service from this repo's `calry_backend` directory. Railway auto-detects `railway.json` + `Dockerfile` — no Nixpacks, no manual build/start command needed.

### 2. Environment variables

Set these in the service's **Variables** tab:

| Variable | Value | Purpose |
| :--- | :--- | :--- |
| `ENVIRONMENT` | `production` | Hides internal error detail; production behavior |
| `LOG_LEVEL` | `info` | Log verbosity |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | Reference the Postgres plugin; app auto-rewrites to the async driver |
| `ALLOWED_ORIGINS` | `https://app.calry.ai` | Explicit CORS origins (enables credentials). Comma-separated; omit/`*` for all |
| `OPENROUTER_API_KEY` | `your-openrouter-key` | Enables AI calorie estimation via OpenRouter |
| `DEFAULT_AI_PROVIDER` | `openrouter` | Default AI engine |
| `FIREBASE_PROJECT_ID` | `calry-62362` | Firebase project for ID-token verification |
| `FIREBASE_CREDENTIALS_PATH` | *(leave unset)* | Optional. Token verification works with the project ID alone; only set if you need full Admin SDK operations (provide creds via a mounted secret, not a committed file) |

> `PORT` is injected by Railway automatically — do **not** set it manually.

### 3. Deploy

Push to the connected branch, or use the CLI:
```bash
railway up
```
Railway builds the image, runs migrations via `start.sh`, and waits for `/api/v1/health` to report healthy before routing traffic.

### ⚠️ Persistent uploads
`/static` is written to the container's **ephemeral** filesystem — files are lost on every redeploy/restart. The Flutter client already uses **Firebase Storage**; route production media uploads there (or a Railway Volume / S3) rather than local disk.
