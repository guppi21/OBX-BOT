# OBX Discord Economy Platform (Phase 1: OBX Core)

Phase 1 provides the high-integrity financial core and ledger engine for the OBX Discord economy platform. It manages user identity, non-negative integer wallet balances, immutable audit ledgers, row-level concurrency locking, idempotency guarantees, a reconciliation audit engine, internal FastAPI REST services, and developer CLI tools.

---

## 🏗️ Architecture & Monorepo Structure

```
obx-ecosystem/
├── apps/
│   └── obx_core/
│       ├── api/
│       │   ├── routes/               # REST Endpoints: health, users, wallets
│       │   ├── schemas/              # Pydantic schemas & DTOs
│       │   └── error_handlers.py     # Domain exception handlers
│       ├── services/
│       │   ├── wallet_service.py     # Atomic balance operations & row locking
│       │   └── reconciliation.py     # Ledger mathematical verification engine
│       ├── cli.py                    # Typer & Rich admin CLI tool
│       └── main.py                   # FastAPI application factory
├── packages/
│   ├── database/
│   │   ├── models/                   # SQLAlchemy models (User, Wallet, LedgerEntry)
│   │   ├── migrations/               # Alembic migrations
│   │   ├── session.py                # Engine & session management
│   │   ├── base.py                   # Declarative base & naming conventions
│   │   └── alembic.ini               # Alembic configuration
│   └── shared/
│       ├── config.py                 # Pydantic BaseSettings (.env loading)
│       ├── enums.py                  # TransactionType & ReferenceType enums
│       ├── exceptions.py             # Custom OBX domain exceptions
│       └── logging.py                # Structured logging
├── tests/                            # Automated test suite (44 tests)
├── docker-compose.yml                # PostgreSQL 16 local development container
├── pyproject.toml                    # Project package metadata & tool configuration
├── .env.example                      # Template environment variables
└── README.md                         # Documentation
```

---

## 🔒 Financial Integrity Guarantees

1. **Integer Units Only**: Balances use 64-bit BigInteger units to prevent floating-point rounding errors.
2. **Atomic Operations**: All credit, debit, lock, and release actions occur inside strict database transactions.
3. **Row-Level Concurrency Locking**: Uses `SELECT ... FOR UPDATE` (or immediate transaction locks) to prevent race conditions and lost updates under high concurrency.
4. **Strict Database Constraints**:
   - `CHECK (available_balance >= 0)`
   - `CHECK (locked_balance >= 0)`
   - `CHECK (amount > 0)`
   - `UNIQUE (discord_user_id)`
   - `UNIQUE (idempotency_key)`
5. **Idempotency**: Every balance operation requires a unique `idempotency_key`. Replaying the same key safely returns the existing transaction without duplicating token movements.
6. **Immutable Ledger**: Every OBX movement records an immutable `LedgerEntry` containing the user ID, amount, transaction type, reference type, reference ID, and timestamp.
7. **Reconciliation Engine**: Mathematical audit engine verifying that:
   $$\text{Available Balance} = \sum \text{CREDIT} - \sum \text{DEBIT} - \sum \text{LOCK} + \sum \text{RELEASE} + \sum \text{REFUND}$$
   $$\text{Locked Balance} = \sum \text{LOCK} - \sum \text{RELEASE} - \sum \text{SETTLEMENT}$$

---

## 🚀 Getting Started

### 1. Environment Configuration

Copy the example environment file:
```bash
cp .env.example .env
```

Default variables in `.env`:
```ini
DATABASE_URL=postgresql+psycopg2://postgres:postgres@localhost:5432/obx_economy
ENVIRONMENT=development
LOG_LEVEL=INFO
API_HOST=0.0.0.0
API_PORT=8000
```

### 2. Start PostgreSQL (Docker Compose)

```bash
docker-compose up -d
```

### 3. Apply Database Migrations

```bash
alembic -c packages/database/alembic.ini upgrade head
```

---

## 🌐 Running the FastAPI Server

Start the internal API server:
```bash
python3 apps/obx_core/main.py
```
Or with Uvicorn:
```bash
uvicorn apps.obx_core.main:app --host 0.0.0.0 --port 8000 --reload
```

Interactive API documentation will be available at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

### API Endpoints

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/health` | Health check & DB connection status |
| `POST` | `/users` | Get or create user & wallet |
| `GET` | `/users/{discord_user_id}/balance` | Query available, locked, and total balance |
| `GET` | `/users/{discord_user_id}/transactions` | Paginated transaction history |
| `POST` | `/wallets/credit` | Credit funds idempotently |
| `POST` | `/wallets/debit` | Debit funds idempotently |
| `POST` | `/wallets/lock` | Lock funds (move available $\to$ locked) |
| `POST` | `/wallets/release` | Release funds (move locked $\to$ available) |

---

## 🛠️ Admin & Developer CLI

The CLI provides administrative commands to test, inspect, and reconcile wallets.

```bash
# Create a user
python3 apps/obx_core/cli.py create-user 123456789012345678

# Credit funds
python3 apps/obx_core/cli.py credit 123456789012345678 1000 --ref-type admin_grant --desc "Initial bonus"

# Check wallet balance
python3 apps/obx_core/cli.py balance 123456789012345678

# Debit funds
python3 apps/obx_core/cli.py debit 123456789012345678 250 --desc "Store item purchase"

# View ledger transaction history
python3 apps/obx_core/cli.py transactions 123456789012345678 --limit 10

# Run wallet reconciliation audit
python3 apps/obx_core/cli.py reconcile
python3 apps/obx_core/cli.py reconcile --user 123456789012345678
```

---

## 🧪 Running the Test Suite

Run the full automated test suite with pytest:
```bash
pytest -v
```

### Test Coverage Highlights
- **User & Wallet Constraints**: Unique constraints, negative balance prevention, check constraint enforcement.
- **Balance Operations**: Credit, debit, lock, and release validations.
- **Idempotency**: Repeated calls with same key, conflict detection on altered payloads.
- **Concurrency**: High-concurrency multi-threaded balance mutations with zero race conditions.
- **Reconciliation Engine**: Automatic detection of tampered balances against ledger history.
- **FastAPI Endpoints**: REST status codes, payload validations, error handlers (200, 400, 404, 409, 422).
- **CLI Suite**: Typer CliRunner testing all administrative operations.
