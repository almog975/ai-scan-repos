# infra/

Infra-level assets.

- `db/schema.sql` – target PostgreSQL schema v1 (source-of-truth reference for the data model).
- `db/migrations/001_init.sql` – same schema wrapped in a transaction for raw `psql` bootstrap.

The running review-api still applies its own Alembic migrations (`review-api/alembic/`); converging Alembic onto `infra/db/schema.sql` is a follow-up.
