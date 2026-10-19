# Deployment

## API

1. Create and activate a Python 3.12 virtual environment.
2. Install dependencies from `requirements.txt`.
3. Configure `.env` with strong production secrets, an explicit `TRUSTED_HOSTS` allowlist, HTTPS origins, and a private persistent SQLite path.
4. Run `alembic upgrade head` before starting the service. Production must use `AUTO_CREATE_SCHEMA=false`.
5. Start the API with `uvicorn app.main:app --host 0.0.0.0 --port 8000`. Put it behind a TLS-terminating reverse proxy.
6. Run the Telegram bot separately with `python bot.py` when needed.

Do not expose the database file, `.env`, uploads, or secrets through a public static directory.
