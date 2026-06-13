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
├── requirements.txt              # Production and development pip dependencies
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

This backend is designed for instant, out-of-the-box deployment to **Railway**.

### 1. Production Startup Command
In your Railway dashboard, set the Startup Command to:
```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```
*(Railway dynamically binds the `$PORT` environment variable.)*

### 2. Database Migration Hook
Set your build or deployment command to apply migrations before startup:
```bash
alembic upgrade head
```

### 3. Production Environment Checklist
Configure these variables in your Railway Project Settings:

| Environment Variable | Recommended Value | Purpose |
| :--- | :--- | :--- |
| `ENVIRONMENT` | `production` | Enables production security and log formats |
| `LOG_LEVEL` | `info` | Silences debugging logs |
| `DATABASE_URL` | `postgresql://...` | Injected automatically by Railway's PostgreSQL plugin |
| `GEMINI_API_KEY` | `your-gemini-key` | Enables Google Gemini 1.5 calorie estimation |
| `OPENAI_API_KEY` | `your-openai-key` | Enables OpenAI GPT-4o calorie estimation |
| `DEFAULT_AI_PROVIDER` | `gemini` | Choose default AI engine (`gemini` or `openai`) |
| `FIREBASE_CREDENTIALS_PATH` | *(Blank or path to private json)* | For authenticating Flutter users via real Firebase JWT |
