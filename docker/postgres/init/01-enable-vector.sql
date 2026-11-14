-- Runs once, when the Compose data volume is first initialised.
-- Enables pgvector in the database named by POSTGRES_DB (`niyamlens`).
CREATE EXTENSION IF NOT EXISTS vector;
