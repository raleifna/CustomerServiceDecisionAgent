-- Adds the missing traceability link back to Olist's real customer_unique_id.
-- 001 minted a CUS-RW-xxxxx id per unique Olist customer but never persisted
-- which real customer_unique_id it came from, so
-- CustomerRepository.get_by_source_unique_id() had nothing to query against.
ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS source_unique_id TEXT;

CREATE INDEX IF NOT EXISTS ix_customers_source_unique_id
    ON customers (source_unique_id);
