# Hallel Inventory System

Vehicle inventory and sales management for Hallel Import Export. The app includes vehicle receiving, inventory search, sales checkout and Excel import, branch transfers, reports, staff roles, and brand-level sold/remaining-stock cards.

## Project files

- `index.html` — current working interface, including Excel imports and case-insensitive brand grouping.
- `main.py` — FastAPI API and authentication.
- `models.py` — SQLAlchemy models for users, vehicles, sales, and transfers.
- `database.py` — database connection setup.
- `requirements.txt` — Python dependencies.
- `.env.example` — safe configuration template; copy it to `.env`.
- `.gitignore` — prevents local secrets, databases, and caches from being committed.

## Run locally (Windows)

Open a terminal in this folder:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set a unique `SECRET_KEY`, `ADMIN_USERNAME`, and strong `ADMIN_PASSWORD`. For local testing, leave `DATABASE_URL` blank to use SQLite.

Start the application:

```powershell
python -m uvicorn main:app --reload
```

Open `http://127.0.0.1:8000`. API documentation is at `http://127.0.0.1:8000/docs`.

If PowerShell blocks environment activation, run the commands using Command Prompt or invoke `.venv\Scripts\python.exe -m uvicorn main:app --reload` after installing dependencies in that environment.

## Data and deployment safety

- The source package intentionally does **not** include `.env`, `.git`, or `inventory.db`.
- Keep your current `inventory.db` backed up separately. Do not replace it with a new empty database if you need existing records.
- SQLite is suitable for local testing, not as a writable database stored inside a serverless deployment.
- Before production, choose a hosting provider that supports the FastAPI process and configure a persistent hosted database (for example, a supported MySQL or PostgreSQL service) using `DATABASE_URL`.
- Test the migration and verify vehicle, sales, transfer, and user counts before pointing staff to production.
- Keep `.env` out of GitHub. Put production secrets in your hosting provider's environment-variable settings.
- The initial administrator is created by the backend only when `ADMIN_USERNAME` and `ADMIN_PASSWORD` are set on startup and no admin exists. Keep these credentials private and change them before production.
- Configure a strong unique `SECRET_KEY`, HTTPS, database backups, and restricted database credentials before going live.

## Roles

- `admin` — full access and staff account management.
- `stock_manager` — stock receiving/editing and branch transfers.
- `salesperson` — inventory access and sales checkout.
- `management` — inventory, sales history, and reports.

## Before first GitHub push

1. Review the files and confirm no secrets are in the source.
2. Confirm `.env`, `*.db`, `.venv/`, `__pycache__/`, and `.git/` are excluded.
3. Push only the clean source package.
4. Keep a separate encrypted backup of the existing database.
5. Do not deploy to production until the hosting provider confirms FastAPI support and the database migration is tested.
