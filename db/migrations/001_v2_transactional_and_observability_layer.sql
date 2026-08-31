-- ============================================================================
-- V2 schema migration: transactional fact layer, refund/replacement domain
-- tables, and the conversation/agent observability layer.
--
-- Scope decided in review: reuse the existing, already-battle-tested
-- after-sales tables (service_requests / service_request_items /
-- service_request_evidence / decision_records / human_handoffs /
-- return_orders / action_tasks / outbox_events / audit_logs / checkpoints)
-- unchanged -- do NOT rename or fork them. Only add what's genuinely
-- missing: the transaction fact layer (customers/products/orders/
-- order_items extension/payments/shipments/shipment_events), data
-- provenance + import batch tracking, refunds/replacement_orders as the
-- domain source of truth for fulfillment outcomes, risk_assessments so risk
-- signals stop being computed-and-discarded, and conversations/messages/
-- agent_runs/tool_calls for the Planning-Agent/Tool-layer observability
-- chain.
--
-- Compatibility rules followed throughout (do not violate when extending
-- this file):
--   1. New tables are created outright.
--   2. Existing tables only gain nullable / defaulted columns.
--   3. No existing table is renamed.
--   4. No existing column is dropped.
--   5. The 27 hand-built fixture orders and the 34 deterministic eval cases
--      must keep passing unmodified after this migration.
--   6. The 1128-row Olist/Amazon import batch is held to full referential
--      integrity; the legacy fixture data is not (see order_items.order_id
--      note below) until a later migration backfills it.
--   7. Repository callers are migrated module by module, not in one sweep.
--   8. Run `pytest tests -q -k "not postgres and not integration"` (42
--      cases) and `python scripts/evaluation/run_evaluation.py` (34 cases)
--      after each phase.
-- ============================================================================


-- ----------------------------------------------------------------------------
-- 0. Data management layer -- created first, everything else FKs into it.
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS import_batches (
    import_batch_id      TEXT PRIMARY KEY,
    pipeline_version      TEXT NOT NULL,
    dataset_anchor_date   DATE NOT NULL,
    source_datasets       JSONB NOT NULL,   -- e.g. ["Olist Brazilian E-Commerce (Kaggle)", "Amazon Reviews 2023 (McAuley Lab)"]
    row_counts            JSONB NOT NULL,   -- e.g. {"orders": 1128, "order_items": 1437, "products": 950, "customers": 1125}
    notes                 TEXT,
    imported_at           TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- entity_type/entity_id/field_name identifies which row+column a claim is
-- about; one row per claim, not one row per entity, so a single order can
-- have its purchase timestamp tagged REAL and its refund_status tagged
-- SYNTHETIC_ENRICHMENT independently.
CREATE TABLE IF NOT EXISTS data_provenance (
    provenance_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type        TEXT NOT NULL,     -- 'order' | 'order_item' | 'product' | 'customer' | 'shipment_event' | ...
    entity_id          TEXT NOT NULL,
    field_name         TEXT,              -- NULL = the claim is about the whole row, not one column
    source_type        TEXT NOT NULL CHECK (source_type IN (
                           'REAL', 'DERIVED', 'SYNTHETIC_ENRICHMENT',
                           'SYNTHETIC_EVENT', 'MODEL_OUTPUT', 'SYSTEM_OUTPUT'
                       )),
    source_dataset     TEXT,              -- e.g. 'olist_orders_dataset.csv', 'meta_Electronics.jsonl'
    source_record_id   TEXT,              -- e.g. the original Olist order_id or Amazon parent_asin
    transformation     TEXT,              -- e.g. 'timeline_shift', 'BRL_to_CNY', 'constrained_random_assignment'
    generation_rule    TEXT,              -- e.g. 'LOGISTICS-GEN-001' for a synthetic event's generating rule
    pipeline_version   TEXT,
    metadata           JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_data_provenance_entity
    ON data_provenance (entity_type, entity_id);


-- ----------------------------------------------------------------------------
-- 1. Transaction fact layer (new)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS customers (
    customer_id            TEXT PRIMARY KEY,
    vip_level              TEXT NOT NULL DEFAULT 'Bronze'
                            CHECK (vip_level IN ('Bronze', 'Silver', 'Gold', 'Platinum')),
    register_date          DATE,          -- NULL where the source dataset has no signup date (Olist customers)
    -- Renamed from an earlier draft's refund_history_count: a canceled
    -- order is NOT evidence a refund happened. This column only counts
    -- cancellations observed in the imported order set (source_type
    -- DERIVED, see data_provenance). The true refund_request_count /
    -- successful_refund_count / refund_amount_total are NEVER stored here
    -- -- compute them live from service_requests + refunds so RISK-XXX
    -- reads a real-time answer, not a stale snapshot.
    canceled_order_count   INTEGER NOT NULL DEFAULT 0 CHECK (canceled_order_count >= 0),
    import_batch_id        TEXT REFERENCES import_batches (import_batch_id),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
    product_id       TEXT PRIMARY KEY,
    category         TEXT NOT NULL,
    price            NUMERIC(12, 2) NOT NULL CHECK (price >= 0),   -- CNY, see data_provenance for the conversion rate used
    is_fragile       BOOLEAN NOT NULL DEFAULT FALSE,
    is_customized    BOOLEAN NOT NULL DEFAULT FALSE,
    import_batch_id  TEXT REFERENCES import_batches (import_batch_id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- order_status carries Olist's real value where the row comes from that
-- import (created/approved/processing/invoiced/shipped/delivered/canceled/
-- unavailable); it is NOT a state machine this schema enforces transitions
-- for -- it is a descriptive fact, matching how the source data reports it.
CREATE TABLE IF NOT EXISTS orders (
    order_id                   TEXT PRIMARY KEY,
    customer_id                TEXT NOT NULL REFERENCES customers (customer_id),
    order_status               TEXT NOT NULL,
    payment_status             TEXT NOT NULL DEFAULT 'Paid',
    -- Synthetic and independent of order_status: cancellation is not proof
    -- of a completed refund. NULL unless order_status is canceled/unavailable.
    refund_status               TEXT CHECK (refund_status IS NULL OR refund_status IN ('处理中', '已完成', '失败')),
    order_date                  DATE NOT NULL,
    shipped_date                DATE,
    delivered_date              DATE,
    estimated_delivery_date     DATE,
    delivery_recipient_type     TEXT,
    amount                      NUMERIC(12, 2) NOT NULL CHECK (amount >= 0),
    rounding_adjustment         NUMERIC(12, 2) NOT NULL DEFAULT 0,
    import_batch_id             TEXT REFERENCES import_batches (import_batch_id),
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_orders_customer_id ON orders (customer_id);

-- order_items ALREADY EXISTS (created by setup_application_schema in
-- postgres_repository.py) and is actively used by the legacy fixture flow
-- via a copy-on-first-touch INSERT in service_request_repository.py. We only
-- extend it -- no rename, no column drop.
--
-- order_items.order_id and .product_id are deliberately left WITHOUT a hard
-- FK to orders/products for now: the 27 legacy fixture orders are not (yet)
-- rows in the new `orders` table, and adding a hard FK today would break
-- that existing INSERT the moment it runs. Referential integrity for the
-- new import batch is enforced by the import script's validation pass
-- instead (see migration notes doc, check #1: "所有订单项都能找到订单和商品").
-- Tightening this to a real FK is a follow-up migration once the legacy 27
-- orders are backfilled into `orders` too.
ALTER TABLE order_items
    ADD COLUMN IF NOT EXISTS category         TEXT,
    ADD COLUMN IF NOT EXISTS import_batch_id   TEXT REFERENCES import_batches (import_batch_id);

CREATE TABLE IF NOT EXISTS payments (
    payment_id            TEXT PRIMARY KEY,
    order_id               TEXT NOT NULL REFERENCES orders (order_id),
    amount                  NUMERIC(12, 2) NOT NULL CHECK (amount >= 0),
    currency                 TEXT NOT NULL DEFAULT 'CNY',
    payment_method            TEXT,
    payment_installments       INTEGER,   -- real Olist field; NULL where not applicable
    paid_at                     DATE,   -- date-precision only, matching the source data (no time-of-day)
    import_batch_id              TEXT REFERENCES import_batches (import_batch_id),
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS payment_installments INTEGER;

-- defensive: if this file was applied before shipped_at/delivered_at/
-- paid_at/event_time were corrected from TIMESTAMPTZ to DATE below, bring
-- an already-created table in line too. Always safe to re-run (no-op once
-- the columns are already DATE).
ALTER TABLE payments ALTER COLUMN paid_at TYPE DATE USING paid_at::date;

CREATE INDEX IF NOT EXISTS ix_payments_order_id ON payments (order_id);

CREATE TABLE IF NOT EXISTS shipments (
    shipment_id                TEXT PRIMARY KEY,
    order_id                    TEXT NOT NULL REFERENCES orders (order_id),
    carrier                     TEXT,
    shipped_at                  DATE,   -- date-precision only, matching the source data (no time-of-day)
    delivered_at                DATE,
    estimated_delivery_date     DATE,
    shipment_status              TEXT NOT NULL DEFAULT 'Pending'
                                 CHECK (shipment_status IN ('Pending', 'InTransit', 'Delivered', 'Overdue', 'Canceled')),
    import_batch_id              TEXT REFERENCES import_batches (import_batch_id),
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE shipments ALTER COLUMN shipped_at TYPE DATE USING shipped_at::date;
ALTER TABLE shipments ALTER COLUMN delivered_at TYPE DATE USING delivered_at::date;

CREATE INDEX IF NOT EXISTS ix_shipments_order_id ON shipments (order_id);

-- Only a REAL or DERIVED event is ever written here -- never an asserted
-- fact Olist doesn't actually give us (see build_olist_sample.py notes: no
-- "stalled 72h" claim without a continuous tracking feed to support it).
CREATE TABLE IF NOT EXISTS shipment_events (
    event_id       TEXT PRIMARY KEY,
    shipment_id     TEXT NOT NULL REFERENCES shipments (shipment_id),
    event_type      TEXT NOT NULL,   -- e.g. '派送延迟' | '超过预计送达时间仍未签收'
    event_time      DATE NOT NULL,   -- date-precision only, matching the source data (no time-of-day)
    resolved        BOOLEAN NOT NULL DEFAULT FALSE,
    source_type     TEXT NOT NULL CHECK (source_type IN ('REAL', 'DERIVED', 'SYNTHETIC_EVENT')),
    derivation      TEXT,            -- human-readable: which real fields this was computed from
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE shipment_events ALTER COLUMN event_time TYPE DATE USING event_time::date;

CREATE INDEX IF NOT EXISTS ix_shipment_events_shipment_id ON shipment_events (shipment_id);


-- ----------------------------------------------------------------------------
-- 2. Runtime layer additions -- refund/replacement domain tables,
--    risk assessment, conversation/agent observability.
--    (service_requests / service_request_items / service_request_evidence /
--    decision_records / human_handoffs / return_orders / action_tasks /
--    outbox_events / audit_logs / checkpoints are unchanged and NOT
--    redefined here.)
-- ----------------------------------------------------------------------------

-- Ownership boundary (do not blur this when wiring the Service layer):
--   action_tasks   -> execution/scheduling status only ("did the task run")
--   refunds        -> the refund DOMAIN's single source of truth
--                      ("did the money actually move, how much, to where")
--   replacement_orders -> the exchange DOMAIN's single source of truth
--   outbox_events  -> reliable event delivery
--   audit_logs     -> who did what, when
-- A decision_record can fan out to at most one action_task per action_type,
-- and an action_task's eventual outcome is recorded on exactly one refunds
-- or replacement_orders row -- never update both action_tasks.workflow_status
-- and refunds.refund_status as if either alone were authoritative.
CREATE TABLE IF NOT EXISTS refunds (
    refund_id                  TEXT PRIMARY KEY,
    request_id                  TEXT NOT NULL REFERENCES service_requests (request_id),
    action_id                   TEXT REFERENCES action_tasks (action_id),
    order_id                    TEXT,
    amount                      NUMERIC(12, 2) NOT NULL CHECK (amount >= 0),
    channel                     TEXT,             -- e.g. '原支付渠道'
    external_transaction_id     TEXT,
    refund_status                TEXT NOT NULL DEFAULT 'Pending'
                                 CHECK (refund_status IN ('Pending', 'Processing', 'Succeeded', 'Failed')),
    failure_reason               TEXT,
    created_at                   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_refunds_request_id ON refunds (request_id);

CREATE TABLE IF NOT EXISTS replacement_orders (
    replacement_order_id        TEXT PRIMARY KEY,
    request_id                   TEXT NOT NULL REFERENCES service_requests (request_id),
    action_id                    TEXT REFERENCES action_tasks (action_id),
    original_order_item_id       TEXT,
    replacement_product_id       TEXT,
    replacement_quantity         INTEGER NOT NULL CHECK (replacement_quantity > 0),
    replacement_status            TEXT NOT NULL DEFAULT 'Pending'
                                  CHECK (replacement_status IN ('Pending', 'Shipped', 'Delivered', 'Failed')),
    external_transaction_id       TEXT,
    created_at                    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                    TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_replacement_orders_request_id ON replacement_orders (request_id);

-- conversations/messages are the product-queryable chat record; LangGraph's
-- own `checkpoints` table remains purely a run-recovery mechanism and must
-- NOT be read by the frontend or any API as the chat history source --
-- checkpoint rows are keyed by LangGraph's internal state shape, not a
-- stable public schema, and are not guaranteed to stay compatible across
-- LangGraph versions.
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id     TEXT PRIMARY KEY,   -- = the LangGraph thread_id, not a separately-minted id
    customer_id          TEXT REFERENCES customers (customer_id),
    started_at            TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_message_at       TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    message_id          TEXT PRIMARY KEY,
    conversation_id       TEXT NOT NULL REFERENCES conversations (conversation_id),
    -- required so a retried graph turn can't write the same customer/agent
    -- message twice
    idempotency_key        TEXT NOT NULL,
    role                    TEXT NOT NULL CHECK (role IN ('customer', 'agent', 'system')),
    content                 TEXT NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (conversation_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_messages_conversation_id ON messages (conversation_id);

-- One row per graph turn. graph_node_path is diagnostic (which nodes
-- actually ran this turn), not a replacement for LangGraph's own state.
CREATE TABLE IF NOT EXISTS agent_runs (
    agent_run_id           TEXT PRIMARY KEY,
    conversation_id          TEXT NOT NULL REFERENCES conversations (conversation_id),
    message_id                TEXT REFERENCES messages (message_id),
    service_request_id         TEXT REFERENCES service_requests (request_id),
    graph_node_path             JSONB,   -- ordered list of node names executed, e.g. ["conversation_router","order_agent",...]
    started_at                   TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at                   TIMESTAMPTZ,
    run_status                    TEXT NOT NULL DEFAULT 'Running'
                                  CHECK (run_status IN ('Running', 'Succeeded', 'Failed')),
    error_summary                  TEXT
);

CREATE INDEX IF NOT EXISTS ix_agent_runs_conversation_id ON agent_runs (conversation_id);
CREATE INDEX IF NOT EXISTS ix_agent_runs_service_request_id ON agent_runs (service_request_id);

-- arguments_redacted/result_redacted: the Tool layer must scrub PII
-- (customer name/address/phone, raw evidence file bytes, etc.) before
-- writing here -- this table is meant to be safe to show a reviewer or
-- include in a bug report; it is not a verbatim request/response log.
CREATE TABLE IF NOT EXISTS tool_calls (
    tool_call_id        TEXT PRIMARY KEY,
    agent_run_id          TEXT NOT NULL REFERENCES agent_runs (agent_run_id),
    tool_name              TEXT NOT NULL,   -- e.g. get_order / get_shipment / search_refund_policy
    arguments_redacted      JSONB,
    result_redacted          JSONB,
    started_at                TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at                TIMESTAMPTZ,
    call_status                 TEXT NOT NULL DEFAULT 'Succeeded'
                                CHECK (call_status IN ('Succeeded', 'Failed')),
    error_summary                TEXT
);

CREATE INDEX IF NOT EXISTS ix_tool_calls_agent_run_id ON tool_calls (agent_run_id);

-- Risk signals stop being "computed in policy_agent.py and discarded the
-- moment the turn ends" -- every assessment is persisted with enough
-- context to answer "what signals, under which policy/model version, led to
-- this risk level" after the fact.
CREATE TABLE IF NOT EXISTS risk_assessments (
    risk_assessment_id     TEXT PRIMARY KEY,
    service_request_id       TEXT NOT NULL REFERENCES service_requests (request_id),
    decision_record_id        TEXT REFERENCES decision_records (decision_id),
    agent_run_id                TEXT REFERENCES agent_runs (agent_run_id),
    risk_level                   TEXT NOT NULL CHECK (risk_level IN ('Low', 'Medium', 'High')),
    risk_score                    DOUBLE PRECISION,
    signals                        JSONB NOT NULL DEFAULT '[]'::jsonb,   -- e.g. ["RISK-001", "RISK-003"]
    policy_version                  TEXT,
    model_version                    TEXT,
    assessment_status                 TEXT NOT NULL DEFAULT 'Completed'
                                      CHECK (assessment_status IN ('Completed', 'Failed')),
    created_at                         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_risk_assessments_service_request_id ON risk_assessments (service_request_id);


-- ----------------------------------------------------------------------------
-- 3. Deliberately NOT done in this migration (recorded so it isn't
--    forgotten, not because it was overlooked):
--    - decision_records.rule_hits stays JSONB; not normalized into a
--      decision_rule_matches table (no analysis need yet that justifies the
--      Decision Engine + test churn).
--    - order_items.order_id / .product_id get no hard FK yet (see note
--      above the ALTER TABLE order_items statement).
--    - The 27 legacy fixture orders are not backfilled into `orders` /
--      `customers` / `products` in this migration; they keep working purely
--      through the existing JSON mock_db fallback + the copy-on-touch
--      order_items insert.
-- ----------------------------------------------------------------------------
