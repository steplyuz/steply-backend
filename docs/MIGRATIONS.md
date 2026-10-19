# Database migrations

Alembic migrations are the source of truth for database schema changes.

```bash
alembic upgrade head
alembic current
alembic check
```

For a new migration, update the SQLAlchemy models first, then generate and review a revision. Never edit an already-applied production migration; add a new revision instead. Back up the database before applying production schema changes.
