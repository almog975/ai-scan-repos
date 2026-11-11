# infra/

Infra-level assets.

- `db/schema.sql` – target PostgreSQL schema v1 (source-of-truth reference for the data model).
- `db/migrations/001_init.sql` – same schema wrapped in a transaction for raw `psql` bootstrap.

The running review-api still applies its own Alembic migrations (`services/review-api/alembic/`); converging Alembic onto `infra/bootstrap/db/schema.sql` is a follow-up.

TODO (per plan, not in repo yet): K3s install notes, kube-prometheus-stack values, installer (`infra/bootstrap/installer/aiscan-install.sh`) +
Terraform (VPC/EKS/EFS/ECR), `create-secrets.sh`. Owner: AI Scan Infra.
