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
*   **Background Jobs:** Celery + Redis for resilient long-running photo analysis
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

For asynchronous photo analysis, also run Redis and the Celery worker:
```bash
redis-server
./start_worker.sh
```

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

1. Create a Railway project and add **PostgreSQL** and **Redis**.
2. Add an API service from this repo's `calry_backend` directory. Railway auto-detects `railway.json` + `Dockerfile`. Optional: set its service healthcheck to `/api/v1/health` in the Railway UI.
3. Add a second service from the same directory for the worker. It can use the same `railway.json`; set `CALRY_PROCESS=worker` on that service.

### 2. Environment variables

Set these in the service's **Variables** tab:

| Variable | Value | Purpose |
| :--- | :--- | :--- |
| `ENVIRONMENT` | `production` | Hides internal error detail; production behavior |
| `CALRY_PROCESS` | `api` for API, `worker` for worker | Selects Uvicorn or Celery inside `start.sh` |
| `LOG_LEVEL` | `info` | Log verbosity |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | Reference the Postgres plugin; app auto-rewrites to the async driver |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | Celery broker/result backend for background analysis |
| `STORAGE_BACKEND` | `s3` | Stores uploads outside the Railway container |
| `S3_BUCKET` / `S3_REGION` | your bucket settings | S3-compatible storage target |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | credentials | S3 upload credentials |
| `S3_PUBLIC_URL_BASE` | CDN/custom domain URL | Public base used in returned media URLs |
| `S3_PUBLIC_READ` | `true` | Uploads new media with `public-read` ACL when bucket ACLs are enabled |
| `ALLOWED_ORIGINS` | `https://app.calry.ai` | Explicit CORS origins (enables credentials). Comma-separated; omit/`*` for all |
| `OPENROUTER_API_KEY` | `your-openrouter-key` | Enables AI calorie estimation via OpenRouter |
| `DEFAULT_AI_PROVIDER` | `openrouter` | Default AI engine |
| `FIREBASE_PROJECT_ID` | `calry-62362` | Firebase project for ID-token verification |
| `FIREBASE_CREDENTIALS` | full service account JSON | Firebase Admin credentials. Paste the complete JSON from Firebase Console → Project settings → Service accounts |

> `PORT` is injected by Railway automatically — do **not** set it manually.

For AWS S3 buckets with **Block Public Access** or **Object Ownership: Bucket owner enforced**,
`public-read` ACLs may be ignored or rejected. In that case keep `S3_PUBLIC_READ=false`
and add a bucket policy for the upload prefix instead:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicReadUploads",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::calry-bucket/uploads/*"
    }
  ]
}
```

### 3. Deploy

Push to the connected branch, or use the CLI:
```bash
railway up
```
Railway builds the image and runs `start.sh`. API services apply migrations and start Uvicorn; worker services with `CALRY_PROCESS=worker` start Celery and skip migrations. Do not configure an HTTP healthcheck on the worker.

### Persistent uploads
Use `STORAGE_BACKEND=s3` in production. `/static` local uploads remain for development only and are lost on redeploy/restart.
