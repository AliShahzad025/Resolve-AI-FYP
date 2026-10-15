

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid() on PG < 13

-- ---------------------------------------------------------------------
-- 1. Enumerated types
-- ---------------------------------------------------------------------
CREATE TYPE connection_status_t   AS ENUM ('disconnected', 'active', 'error');
CREATE TYPE member_role_t         AS ENUM ('owner', 'manager', 'agent');
CREATE TYPE event_source_t        AS ENUM ('shopify', 'whatsapp');
CREATE TYPE processing_status_t   AS ENUM ('pending', 'processed', 'failed');
CREATE TYPE risk_tier_t           AS ENUM ('low', 'medium', 'high');
CREATE TYPE case_status_t         AS ENUM ('open', 'awaiting_customer', 'in_progress', 'escalated', 'resolved');
CREATE TYPE case_route_t          AS ENUM ('automatic_response', 'human_review', 'escalation');
CREATE TYPE message_direction_t   AS ENUM ('incoming', 'outgoing');
CREATE TYPE delivery_status_t     AS ENUM ('received', 'queued', 'submitted', 'delivered', 'read', 'failed');
CREATE TYPE urgency_t             AS ENUM ('low', 'medium', 'high');
CREATE TYPE sentiment_t           AS ENUM ('positive', 'neutral', 'negative');
CREATE TYPE index_status_t        AS ENUM ('pending', 'ready', 'failed');
CREATE TYPE suggestion_status_t   AS ENUM ('pending_review', 'approved', 'rejected', 'expired');
CREATE TYPE notification_status_t AS ENUM ('queued', 'submitted', 'delivered', 'read', 'failed');

-- ---------------------------------------------------------------------
-- 2. Tables
-- ---------------------------------------------------------------------

-- 4.15 Store
CREATE TABLE stores (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                 VARCHAR(200) NOT NULL,
    shopify_domain       VARCHAR(255) NOT NULL UNIQUE,
    shopify_token_ref    VARCHAR(255) NOT NULL,          -- reference to encrypted secret store, never the token
    whatsapp_number_id   VARCHAR(100),
    whatsapp_token_ref   VARCHAR(255),
    connection_status    connection_status_t NOT NULL DEFAULT 'disconnected',
    last_synced_at       TIMESTAMPTZ
);

-- 4.16 User
CREATE TABLE users (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name           VARCHAR(200) NOT NULL,
    email          VARCHAR(320) NOT NULL,
    password_hash  VARCHAR(255) NOT NULL,                -- bcrypt/argon2 hash, never plain text
    active         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- case-insensitive unique email
CREATE UNIQUE INDEX ux_users_email_lower ON users (lower(email));

-- 4.17 Membership
CREATE TABLE memberships (
    id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    store_id  UUID NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
    user_id   UUID NOT NULL REFERENCES users(id)  ON DELETE RESTRICT,
    role      member_role_t NOT NULL,
    active    BOOLEAN NOT NULL DEFAULT TRUE
);
-- one active membership per store and user
CREATE UNIQUE INDEX ux_membership_active ON memberships (store_id, user_id) WHERE active;
CREATE INDEX ix_membership_user ON memberships (user_id);

-- Routing settings (Settings > Routing rules, Fig 4.5). One row per store.
CREATE TABLE routing_settings (
    store_id                UUID PRIMARY KEY REFERENCES stores(id) ON DELETE RESTRICT,
    auto_reply_enabled      BOOLEAN NOT NULL DEFAULT FALSE,   -- allow policy-grounded replies for eligible low-urgency cases
    escalate_high_urgency   BOOLEAN NOT NULL DEFAULT TRUE,    -- route high-urgency cases to a human agent
    min_confidence_pct      NUMERIC(5,2) NOT NULL DEFAULT 90
                            CHECK (min_confidence_pct >= 0 AND min_confidence_pct <= 100),
    updated_by              UUID REFERENCES users(id) ON DELETE RESTRICT,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4.18 Customer
CREATE TABLE customers (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    store_id             UUID NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
    shopify_customer_id  VARCHAR(100),
    name                 VARCHAR(200),
    phone                VARCHAR(30),                    -- normalized (E.164)
    email                VARCHAR(320),
    messaging_consent    BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (id, store_id)                               -- target for same-store composite FKs
);
CREATE UNIQUE INDEX ux_customer_shopify ON customers (store_id, shopify_customer_id)
    WHERE shopify_customer_id IS NOT NULL;
CREATE INDEX ix_customer_phone ON customers (store_id, phone);

-- 4.19 Order  ("order" is reserved, so the table is named orders)
CREATE TABLE orders (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    store_id           UUID NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
    customer_id        UUID,
    shopify_order_id   VARCHAR(100) NOT NULL,
    ordered_at         TIMESTAMPTZ NOT NULL,
    total_amount       NUMERIC(12,2) NOT NULL CHECK (total_amount >= 0),
    currency           CHAR(3) NOT NULL DEFAULT 'PKR',
    payment_method     VARCHAR(50),
    items              JSONB NOT NULL DEFAULT '[]'::jsonb,
    location           JSONB,                            -- city/region; excluded from public displays
    outcome_status     VARCHAR(50) NOT NULL,             -- latest synchronized outcome
    delivery_metadata  JSONB,                            -- courier, tracking, timestamps
    UNIQUE (store_id, shopify_order_id),                 -- unique per store, not globally
    UNIQUE (id, store_id),
    FOREIGN KEY (customer_id, store_id) REFERENCES customers (id, store_id) ON DELETE RESTRICT
);
CREATE INDEX ix_orders_store_date    ON orders (store_id, ordered_at DESC);
CREATE INDEX ix_orders_store_outcome ON orders (store_id, outcome_status);
CREATE INDEX ix_orders_customer      ON orders (customer_id);

-- 4.20 SourceEvent
CREATE TABLE source_events (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    store_id           UUID NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
    order_id           UUID,
    provider_event_id  VARCHAR(200) NOT NULL,
    source             event_source_t NOT NULL,
    event_type         VARCHAR(100) NOT NULL,
    payload            JSONB NOT NULL,
    occurred_at        TIMESTAMPTZ NOT NULL,
    processing_status  processing_status_t NOT NULL DEFAULT 'pending',
    attempts           INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    UNIQUE (store_id, source, provider_event_id),        -- idempotent webhook handling (FR-X01)
    FOREIGN KEY (order_id, store_id) REFERENCES orders (id, store_id) ON DELETE RESTRICT
);
CREATE INDEX ix_events_pending ON source_events (store_id, processing_status, occurred_at);

-- 4.21 RiskScore
CREATE TABLE risk_scores (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id           UUID NOT NULL REFERENCES orders(id) ON DELETE RESTRICT,
    score              DOUBLE PRECISION NOT NULL CHECK (score >= 0 AND score <= 1),
    tier               risk_tier_t NOT NULL,
    model_version      VARCHAR(100) NOT NULL,
    features_snapshot  JSONB NOT NULL,
    scored_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid              BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX ix_risk_order_time ON risk_scores (order_id, scored_at DESC);
CREATE INDEX ix_risk_tier       ON risk_scores (tier) WHERE valid;

-- 4.22 ComplaintCase
CREATE TABLE complaint_cases (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_number       BIGINT GENERATED ALWAYS AS IDENTITY UNIQUE,
    case_ref          TEXT GENERATED ALWAYS AS ('CS-' || lpad(case_number::text, 4, '0')) STORED,
    store_id          UUID NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
    customer_id       UUID,
    order_id          UUID,
    assigned_user_id  UUID REFERENCES users(id) ON DELETE RESTRICT,
    status            case_status_t NOT NULL DEFAULT 'open',
    route             case_route_t  NOT NULL DEFAULT 'human_review',
    routing_reason    TEXT,
    human_takeover    BOOLEAN NOT NULL DEFAULT FALSE,
    category_override VARCHAR(100),                      -- staff correction; model output stays separate
    urgency_override  VARCHAR(20),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id, store_id),
    FOREIGN KEY (customer_id, store_id) REFERENCES customers (id, store_id) ON DELETE RESTRICT,
    FOREIGN KEY (order_id,    store_id) REFERENCES orders    (id, store_id) ON DELETE RESTRICT
);
CREATE INDEX ix_cases_store_status ON complaint_cases (store_id, status, created_at DESC);
CREATE INDEX ix_cases_assignee     ON complaint_cases (assigned_user_id) WHERE assigned_user_id IS NOT NULL;
CREATE INDEX ix_cases_customer     ON complaint_cases (customer_id);

-- 4.23 Message
CREATE TABLE messages (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id              UUID NOT NULL REFERENCES complaint_cases(id) ON DELETE RESTRICT,
    provider_message_id  VARCHAR(255),
    sender_user_id       UUID REFERENCES users(id) ON DELETE RESTRICT,
    direction            message_direction_t NOT NULL,
    body                 TEXT NOT NULL,
    sent_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivery_status      delivery_status_t NOT NULL DEFAULT 'received'
);
-- deduplicate provider message identifiers (FR-X04)
CREATE UNIQUE INDEX ux_message_provider ON messages (provider_message_id)
    WHERE provider_message_id IS NOT NULL;
CREATE INDEX ix_messages_case_time ON messages (case_id, sent_at);

-- 4.24 ComplaintAnalysis
CREATE TABLE complaint_analyses (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id      UUID NOT NULL REFERENCES messages(id) ON DELETE RESTRICT,
    category        VARCHAR(100) NOT NULL,
    urgency         urgency_t NOT NULL,
    sentiment       sentiment_t NOT NULL,
    confidence      JSONB NOT NULL DEFAULT '{}'::jsonb,
    model_versions  JSONB NOT NULL DEFAULT '{}'::jsonb,
    analysed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_analysis_message ON complaint_analyses (message_id, analysed_at DESC);

-- 4.25 PolicyDocument
CREATE TABLE policy_documents (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    store_id     UUID NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
    version      INTEGER NOT NULL CHECK (version > 0),
    title        VARCHAR(200) NOT NULL,
    content      TEXT NOT NULL CHECK (length(btrim(content)) > 0),
    index_status index_status_t NOT NULL DEFAULT 'pending',
    active       BOOLEAN NOT NULL DEFAULT FALSE,
    uploaded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (store_id, title, version),
    -- a version may be active only after successful indexing
    CONSTRAINT ck_policy_active_ready CHECK (NOT active OR index_status = 'ready')
);
-- at most one active version per policy document (store + title)
CREATE UNIQUE INDEX ux_policy_one_active ON policy_documents (store_id, title) WHERE active;

-- 4.26 PolicyChunk
CREATE TABLE policy_chunks (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id        UUID NOT NULL REFERENCES policy_documents(id) ON DELETE RESTRICT,
    chunk_index        INTEGER NOT NULL CHECK (chunk_index >= 0),
    content            TEXT NOT NULL,
    qdrant_point_id    VARCHAR(100) NOT NULL UNIQUE,
    embedding_version  VARCHAR(100) NOT NULL,
    UNIQUE (document_id, chunk_index)
);

-- 4.27 ResponseSuggestion
CREATE TABLE response_suggestions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id             UUID NOT NULL REFERENCES complaint_cases(id) ON DELETE RESTRICT,
    draft_text          TEXT NOT NULL,
    policy_document_id  UUID REFERENCES policy_documents(id) ON DELETE RESTRICT,
    evidence_chunk_ids  JSONB NOT NULL DEFAULT '[]'::jsonb,
    generation_model    VARCHAR(100) NOT NULL,
    status              suggestion_status_t NOT NULL DEFAULT 'pending_review',
    reviewed_by         UUID REFERENCES users(id) ON DELETE RESTRICT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_suggestion_case ON response_suggestions (case_id, created_at DESC);

-- 4.28 Notification
CREATE TABLE notifications (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    store_id             UUID NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
    customer_id          UUID NOT NULL,
    order_id             UUID,
    case_id              UUID,
    deduplication_key    VARCHAR(255) NOT NULL UNIQUE,   -- event + recipient; prevents repeated sends
    provider_message_id  VARCHAR(255),
    payload              JSONB NOT NULL,
    status               notification_status_t NOT NULL DEFAULT 'queued',
    attempts             INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    FOREIGN KEY (customer_id, store_id) REFERENCES customers      (id, store_id) ON DELETE RESTRICT,
    FOREIGN KEY (order_id,    store_id) REFERENCES orders         (id, store_id) ON DELETE RESTRICT,
    FOREIGN KEY (case_id,     store_id) REFERENCES complaint_cases(id, store_id) ON DELETE RESTRICT
);
CREATE INDEX ix_notif_store_status ON notifications (store_id, status);

-- 4.29 AuditEntry
CREATE TABLE audit_entries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    store_id        UUID NOT NULL REFERENCES stores(id) ON DELETE RESTRICT,
    actor_user_id   UUID REFERENCES users(id) ON DELETE RESTRICT,   -- NULL for system actions
    entity_type     VARCHAR(50) NOT NULL,
    entity_id       UUID NOT NULL,
    action          VARCHAR(100) NOT NULL,
    change_summary  JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_store_time ON audit_entries (store_id, occurred_at DESC);
CREATE INDEX ix_audit_entity     ON audit_entries (entity_type, entity_id);

-- ---------------------------------------------------------------------
-- 3. Integrity triggers (rules that plain FKs cannot express)
-- ---------------------------------------------------------------------

-- Staff assignment must reference an ACTIVE membership in the SAME store
CREATE OR REPLACE FUNCTION trg_case_assignee_membership() RETURNS trigger AS $$
BEGIN
    IF NEW.assigned_user_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM memberships m
        WHERE m.store_id = NEW.store_id
          AND m.user_id  = NEW.assigned_user_id
          AND m.active
    ) THEN
        RAISE EXCEPTION 'User % has no active membership in store %',
            NEW.assigned_user_id, NEW.store_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER case_assignee_membership
    BEFORE INSERT OR UPDATE OF assigned_user_id, store_id ON complaint_cases
    FOR EACH ROW EXECUTE FUNCTION trg_case_assignee_membership();

-- A suggestion's policy version and reviewer must belong to the case's store
CREATE OR REPLACE FUNCTION trg_suggestion_same_store() RETURNS trigger AS $$
DECLARE
    v_store UUID;
BEGIN
    SELECT store_id INTO v_store FROM complaint_cases WHERE id = NEW.case_id;

    IF NEW.policy_document_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM policy_documents d
        WHERE d.id = NEW.policy_document_id AND d.store_id = v_store
    ) THEN
        RAISE EXCEPTION 'Policy document does not belong to the case store'
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.reviewed_by IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM memberships m
        WHERE m.store_id = v_store AND m.user_id = NEW.reviewed_by AND m.active
    ) THEN
        RAISE EXCEPTION 'Reviewer has no active membership in the case store'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER suggestion_same_store
    BEFORE INSERT OR UPDATE ON response_suggestions
    FOR EACH ROW EXECUTE FUNCTION trg_suggestion_same_store();

-- An outgoing message's sender must be staff of the case's store
CREATE OR REPLACE FUNCTION trg_message_sender_store() RETURNS trigger AS $$
BEGIN
    IF NEW.sender_user_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM complaint_cases c
        JOIN memberships m ON m.store_id = c.store_id
        WHERE c.id = NEW.case_id AND m.user_id = NEW.sender_user_id AND m.active
    ) THEN
        RAISE EXCEPTION 'Sender has no active membership in the case store'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER message_sender_store
    BEFORE INSERT OR UPDATE OF sender_user_id, case_id ON messages
    FOR EACH ROW EXECUTE FUNCTION trg_message_sender_store();

-- Audit history is append-only
CREATE OR REPLACE FUNCTION trg_audit_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_entries is append-only' USING ERRCODE = 'restrict_violation';
END $$ LANGUAGE plpgsql;

CREATE TRIGGER audit_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_entries
    FOR EACH ROW EXECUTE FUNCTION trg_audit_append_only();

-- ---------------------------------------------------------------------
-- 4. Convenience view: latest valid risk score per order (FR-S04)
-- ---------------------------------------------------------------------
CREATE VIEW v_latest_risk_scores AS
SELECT DISTINCT ON (r.order_id)
       r.order_id, r.score, r.tier, r.model_version, r.scored_at
FROM   risk_scores r
WHERE  r.valid
ORDER  BY r.order_id, r.scored_at DESC;

COMMIT;

-- =====================================================================
-- Resolve AI  |  TEST DATA (2-3 new rows in every table)
-- Run AFTER the schema exists. Adds data only: no DROP, no DELETE, no UPDATE.
-- Safe to run more than once (ON CONFLICT DO NOTHING). Uses its own IDs,
-- emails and domains, so it does not collide with the earlier sample rows.
--
-- Test stores:  test-store-1.myshopify.com  and  test-store-2.myshopify.com
-- Test logins:  test.owner@resolvetest.com / test.agent@resolvetest.com / test.owner2@resolvetest.com
--               (password_hash is a placeholder - not a real hash; create real users through your backend)
--
-- ID pattern: 10000000-0000-0000-TTTT-00000000000N   (TTTT = table code)
-- =====================================================================

BEGIN;

-- stores (2 new) ------------------------------------------------------
INSERT INTO stores (id, name, shopify_domain, shopify_token_ref, whatsapp_number_id, whatsapp_token_ref, connection_status, last_synced_at) VALUES
 ('10000000-0000-0000-0001-000000000001', 'Test Store One', 'test-store-1.myshopify.com', 'vault://test/store1/shopify', '100000000000001', 'vault://test/store1/whatsapp', 'active', now()),
 ('10000000-0000-0000-0001-000000000002', 'Test Store Two', 'test-store-2.myshopify.com', 'vault://test/store2/shopify', NULL, NULL, 'disconnected', NULL)
ON CONFLICT DO NOTHING;

-- users (3 new) -------------------------------------------------------
INSERT INTO users (id, name, email, password_hash, active) VALUES
 ('10000000-0000-0000-0002-000000000001', 'Test Owner',  'test.owner@resolvetest.com',  'PLACEHOLDER_NOT_A_REAL_HASH', TRUE),
 ('10000000-0000-0000-0002-000000000002', 'Test Agent',  'test.agent@resolvetest.com',  'PLACEHOLDER_NOT_A_REAL_HASH', TRUE),
 ('10000000-0000-0000-0002-000000000003', 'Test Owner 2','test.owner2@resolvetest.com', 'PLACEHOLDER_NOT_A_REAL_HASH', TRUE)
ON CONFLICT DO NOTHING;

-- memberships (4 new) -------------------------------------------------
INSERT INTO memberships (id, store_id, user_id, role, active) VALUES
 ('10000000-0000-0000-0003-000000000001', '10000000-0000-0000-0001-000000000001', '10000000-0000-0000-0002-000000000001', 'owner',   TRUE),
 ('10000000-0000-0000-0003-000000000002', '10000000-0000-0000-0001-000000000001', '10000000-0000-0000-0002-000000000002', 'agent',   TRUE),
 ('10000000-0000-0000-0003-000000000003', '10000000-0000-0000-0001-000000000002', '10000000-0000-0000-0002-000000000003', 'owner',   TRUE),
 ('10000000-0000-0000-0003-000000000004', '10000000-0000-0000-0001-000000000002', '10000000-0000-0000-0002-000000000002', 'manager', TRUE)
ON CONFLICT DO NOTHING;

-- routing_settings (2 new) --------------------------------------------
INSERT INTO routing_settings (store_id, auto_reply_enabled, escalate_high_urgency, min_confidence_pct, updated_by) VALUES
 ('10000000-0000-0000-0001-000000000001', TRUE,  TRUE,  85, '10000000-0000-0000-0002-000000000001'),
 ('10000000-0000-0000-0001-000000000002', FALSE, TRUE,  90, '10000000-0000-0000-0002-000000000003')
ON CONFLICT DO NOTHING;

-- customers (3 new) ---------------------------------------------------
INSERT INTO customers (id, store_id, shopify_customer_id, name, phone, email, messaging_consent) VALUES
 ('10000000-0000-0000-0004-000000000001', '10000000-0000-0000-0001-000000000001', 'shp-cust-9001', 'Test Customer Sana',  '+923001110001', 'sana.test@example.com',  TRUE),
 ('10000000-0000-0000-0004-000000000002', '10000000-0000-0000-0001-000000000001', 'shp-cust-9002', 'Test Customer Bilal', '+923001110002', 'bilal.test@example.com', FALSE),
 ('10000000-0000-0000-0004-000000000003', '10000000-0000-0000-0001-000000000002', 'shp-cust-9003', 'Test Customer Hina',  '+923001110003', 'hina.test@example.com',  TRUE)
ON CONFLICT DO NOTHING;

-- orders (3 new) ------------------------------------------------------
INSERT INTO orders (id, store_id, customer_id, shopify_order_id, ordered_at, total_amount, currency, payment_method, items, location, outcome_status, delivery_metadata) VALUES
 ('10000000-0000-0000-0005-000000000001', '10000000-0000-0000-0001-000000000001', '10000000-0000-0000-0004-000000000001', 'T-2001', now() - interval '5 days', 5200.00, 'PKR', 'cash_on_delivery',
   '[{"name":"Cotton kurta - Blue / L","qty":1,"price":5200}]', '{"city":"Lahore"}',  'completed', '{"courier":"TCS","tracking":"TCS123"}'),
 ('10000000-0000-0000-0005-000000000002', '10000000-0000-0000-0001-000000000001', '10000000-0000-0000-0004-000000000002', 'T-2002', now() - interval '3 days', 8900.50, 'PKR', 'card',
   '[{"name":"Lawn suit - Green","qty":2,"price":4450.25}]', '{"city":"Karachi"}', 'refunded',  '{"courier":"Leopards","tracking":"LEO456"}'),
 ('10000000-0000-0000-0005-000000000003', '10000000-0000-0000-0001-000000000002', '10000000-0000-0000-0004-000000000003', 'T-3001', now() - interval '1 day',  2750.00, 'PKR', 'bank_transfer',
   '[{"name":"Scarf - Maroon","qty":1,"price":2750}]', '{"city":"Islamabad"}', 'processing', '{"courier":"PostEx"}')
ON CONFLICT DO NOTHING;

-- source_events (3 new) -----------------------------------------------
INSERT INTO source_events (id, store_id, order_id, provider_event_id, source, event_type, payload, occurred_at, processing_status, attempts) VALUES
 ('10000000-0000-0000-0006-000000000001', '10000000-0000-0000-0001-000000000001', '10000000-0000-0000-0005-000000000001', 'evt-test-001', 'shopify',  'orders/create',  '{"id":"T-2001"}',                now() - interval '5 days', 'processed', 1),
 ('10000000-0000-0000-0006-000000000002', '10000000-0000-0000-0001-000000000001', '10000000-0000-0000-0005-000000000002', 'evt-test-002', 'shopify',  'refunds/create', '{"order_id":"T-2002"}',          now() - interval '2 days', 'pending',   0),
 ('10000000-0000-0000-0006-000000000003', '10000000-0000-0000-0001-000000000002', NULL,                                   'evt-test-003', 'whatsapp', 'message',        '{"from":"+923001110003"}',     now() - interval '1 hour', 'failed',    3)
ON CONFLICT DO NOTHING;

-- risk_scores (3 new) -------------------------------------------------
INSERT INTO risk_scores (id, order_id, score, tier, model_version, features_snapshot, scored_at, valid) VALUES
 ('10000000-0000-0000-0007-000000000001', '10000000-0000-0000-0005-000000000001', 0.22, 'low',    'xgboost-test-v1', '{"prior_orders":4,"prior_refunds":0}', now() - interval '5 days', TRUE),
 ('10000000-0000-0000-0007-000000000002', '10000000-0000-0000-0005-000000000002', 0.81, 'high',   'xgboost-test-v1', '{"prior_orders":6,"prior_refunds":3}', now() - interval '3 days', TRUE),
 ('10000000-0000-0000-0007-000000000003', '10000000-0000-0000-0005-000000000003', 0.47, 'medium', 'xgboost-test-v1', '{"prior_orders":0,"prior_refunds":0}', now() - interval '1 day',  TRUE)
ON CONFLICT DO NOTHING;

-- complaint_cases (3 new; case_number / case_ref are auto-generated) ----
INSERT INTO complaint_cases (id, store_id, customer_id, order_id, assigned_user_id, status, route, routing_reason, human_takeover) VALUES
 ('10000000-0000-0000-0008-000000000001', '10000000-0000-0000-0001-000000000001', '10000000-0000-0000-0004-000000000001', '10000000-0000-0000-0005-000000000001', '10000000-0000-0000-0002-000000000002', 'in_progress',       'human_review',       'Low classification confidence', FALSE),
 ('10000000-0000-0000-0008-000000000002', '10000000-0000-0000-0001-000000000001', '10000000-0000-0000-0004-000000000002', '10000000-0000-0000-0005-000000000002', NULL,                                   'open',              'automatic_response', 'Routine refund-status question', FALSE),
 ('10000000-0000-0000-0008-000000000003', '10000000-0000-0000-0001-000000000002', '10000000-0000-0000-0004-000000000003', '10000000-0000-0000-0005-000000000003', '10000000-0000-0000-0002-000000000003', 'escalated',         'escalation',         'High urgency',                  TRUE)
ON CONFLICT DO NOTHING;

-- messages (5 new) ----------------------------------------------------
INSERT INTO messages (id, case_id, provider_message_id, sender_user_id, direction, body, sent_at, delivery_status) VALUES
 ('10000000-0000-0000-0009-000000000001', '10000000-0000-0000-0008-000000000001', 'wamid.TEST-001', NULL,                                   'incoming', 'Mera order aya hai lekin size chota hai. Exchange ho sakta hai?', now() - interval '4 hours', 'received'),
 ('10000000-0000-0000-0009-000000000002', '10000000-0000-0000-0008-000000000001', 'wamid.TEST-002', '10000000-0000-0000-0002-000000000002', 'outgoing', 'Assalam o alaikum! Apna order number share karein, hum check kar lete hain.', now() - interval '3 hours', 'delivered'),
 ('10000000-0000-0000-0009-000000000003', '10000000-0000-0000-0008-000000000002', 'wamid.TEST-003', NULL,                                   'incoming', 'Could you check when my refund will arrive?',                       now() - interval '2 hours', 'received'),
 ('10000000-0000-0000-0009-000000000004', '10000000-0000-0000-0008-000000000003', 'wamid.TEST-004', NULL,                                   'incoming', 'Mera parcel abhi tak nahi pohancha, bohat late ho gaya hai!',      now() - interval '1 hour',  'received'),
 ('10000000-0000-0000-0009-000000000005', '10000000-0000-0000-0008-000000000003', 'wamid.TEST-005', '10000000-0000-0000-0002-000000000003', 'outgoing', 'We are sorry for the delay. We are checking with the courier now.',  now() - interval '30 minutes', 'read')
ON CONFLICT DO NOTHING;

-- complaint_analyses (3 new, one per incoming message) ------------------
INSERT INTO complaint_analyses (id, message_id, category, urgency, sentiment, confidence, model_versions, analysed_at) VALUES
 ('10000000-0000-0000-000a-000000000001', '10000000-0000-0000-0009-000000000001', 'wrong_size',     'medium', 'neutral',  '{"category":0.74,"urgency":0.69}', '{"classifier":"xlmr-test","sentiment":"xlmt-test"}', now() - interval '4 hours'),
 ('10000000-0000-0000-000a-000000000002', '10000000-0000-0000-0009-000000000003', 'refund_inquiry', 'low',    'neutral',  '{"category":0.93,"urgency":0.88}', '{"classifier":"xlmr-test","sentiment":"xlmt-test"}', now() - interval '2 hours'),
 ('10000000-0000-0000-000a-000000000003', '10000000-0000-0000-0009-000000000004', 'delivery_delay', 'high',   'negative', '{"category":0.91,"urgency":0.95}', '{"classifier":"xlmr-test","sentiment":"xlmt-test"}', now() - interval '1 hour')
ON CONFLICT DO NOTHING;

-- policy_documents (3 new; one active version per store + title) --------
INSERT INTO policy_documents (id, store_id, version, title, content, index_status, active) VALUES
 ('10000000-0000-0000-000b-000000000001', '10000000-0000-0000-0001-000000000001', 1, 'Returns & refunds',  'Customers may request a return within 7 days of delivery. Items must be unused and in original packaging.', 'ready', TRUE),
 ('10000000-0000-0000-000b-000000000002', '10000000-0000-0000-0001-000000000001', 1, 'Shipping',           'Orders are delivered in 3-5 working days. Delays caused by the courier are tracked and updated by support.', 'ready', TRUE),
 ('10000000-0000-0000-000b-000000000003', '10000000-0000-0000-0001-000000000002', 1, 'Returns & refunds',  'Refunds are processed within 5-7 working days after the returned item is received and verified.', 'ready', TRUE)
ON CONFLICT DO NOTHING;

-- policy_chunks (3 new) -----------------------------------------------
INSERT INTO policy_chunks (id, document_id, chunk_index, content, qdrant_point_id, embedding_version) VALUES
 ('10000000-0000-0000-000c-000000000001', '10000000-0000-0000-000b-000000000001', 0, 'Customers may request a return within 7 days of delivery.',                 'qdrant-test-0001', 'multilingual-minilm-test'),
 ('10000000-0000-0000-000c-000000000002', '10000000-0000-0000-000b-000000000002', 0, 'Orders are delivered in 3-5 working days.',                                  'qdrant-test-0002', 'multilingual-minilm-test'),
 ('10000000-0000-0000-000c-000000000003', '10000000-0000-0000-000b-000000000003', 0, 'Refunds are processed within 5-7 working days after the item is verified.', 'qdrant-test-0003', 'multilingual-minilm-test')
ON CONFLICT DO NOTHING;

-- response_suggestions (3 new) ------------------------------------------
INSERT INTO response_suggestions (id, case_id, draft_text, policy_document_id, evidence_chunk_ids, generation_model, status, reviewed_by, created_at) VALUES
 ('10000000-0000-0000-000d-000000000001', '10000000-0000-0000-0008-000000000001', 'Aap 7 din ke andar return ya exchange request kar sakte hain. Order number share karein.', '10000000-0000-0000-000b-000000000001', '["10000000-0000-0000-000c-000000000001"]', 'llm-test-v1', 'approved',       '10000000-0000-0000-0002-000000000002', now() - interval '3 hours'),
 ('10000000-0000-0000-000d-000000000002', '10000000-0000-0000-0008-000000000002', 'Your refund will be processed within the timeframe stated in our policy.',                  '10000000-0000-0000-000b-000000000002', '[]',                                         'llm-test-v1', 'pending_review', NULL,                                   now() - interval '2 hours'),
 ('10000000-0000-0000-000d-000000000003', '10000000-0000-0000-0008-000000000003', 'Hum courier se rabta kar rahe hain aur jald update dein ge.',                                  '10000000-0000-0000-000b-000000000003', '["10000000-0000-0000-000c-000000000003"]', 'llm-test-v1', 'rejected',       '10000000-0000-0000-0002-000000000003', now() - interval '1 hour')
ON CONFLICT DO NOTHING;

-- notifications (3 new) -------------------------------------------------
INSERT INTO notifications (id, store_id, customer_id, order_id, case_id, deduplication_key, provider_message_id, payload, status, attempts) VALUES
 ('10000000-0000-0000-000e-000000000001', '10000000-0000-0000-0001-000000000001', '10000000-0000-0000-0004-000000000001', '10000000-0000-0000-0005-000000000001', NULL,                                   'test-order-T-2001-delivered-c1', 'wamid.NOTIF-001', '{"template":"order_delivered"}', 'delivered', 1),
 ('10000000-0000-0000-000e-000000000002', '10000000-0000-0000-0001-000000000001', '10000000-0000-0000-0004-000000000002', '10000000-0000-0000-0005-000000000002', '10000000-0000-0000-0008-000000000002', 'test-refund-T-2002-update-c2',    NULL,               '{"template":"refund_update"}',   'queued',    0),
 ('10000000-0000-0000-000e-000000000003', '10000000-0000-0000-0001-000000000002', '10000000-0000-0000-0004-000000000003', NULL,                                   '10000000-0000-0000-0008-000000000003', 'test-case-escalated-c3',         'wamid.NOTIF-003', '{"template":"case_update"}',     'failed',    2)
ON CONFLICT DO NOTHING;

-- audit_entries (3 new) -------------------------------------------------
INSERT INTO audit_entries (id, store_id, actor_user_id, entity_type, entity_id, action, change_summary, occurred_at) VALUES
 ('10000000-0000-0000-000f-000000000001', '10000000-0000-0000-0001-000000000001', '10000000-0000-0000-0002-000000000001', 'policy',     '10000000-0000-0000-000b-000000000001', 'policy_activated', '{"version":1}',                                now() - interval '6 days'),
 ('10000000-0000-0000-000f-000000000002', '10000000-0000-0000-0001-000000000001', '10000000-0000-0000-0002-000000000002', 'case',       '10000000-0000-0000-0008-000000000001', 'reply_approved',   '{"before":"draft","after":"sent"}',        now() - interval '3 hours'),
 ('10000000-0000-0000-000f-000000000003', '10000000-0000-0000-0001-000000000002', NULL,                                   'case',       '10000000-0000-0000-0008-000000000003', 'auto_escalated',   '{"reason":"high urgency"}',                now() - interval '1 hour')
ON CONFLICT DO NOTHING;

COMMIT;

-- Quick check (optional): row count per table
-- SELECT 'stores' t, count(*) FROM stores UNION ALL SELECT 'users', count(*) FROM users
-- UNION ALL SELECT 'orders', count(*) FROM orders UNION ALL SELECT 'complaint_cases', count(*) FROM complaint_cases;
SELECT count(*) FROM stores;                 -- expect 3
SELECT count(*) FROM users;                  -- expect 6
SELECT count(*) FROM complaint_cases;        -- expect 4
SELECT case_ref, status FROM complaint_cases ORDER BY case_number;

SELECT 'stores' AS tbl, count(*) FROM stores
UNION ALL SELECT 'users', count(*) FROM users
UNION ALL SELECT 'memberships', count(*) FROM memberships
UNION ALL SELECT 'orders', count(*) FROM orders
UNION ALL SELECT 'complaint_cases', count(*) FROM complaint_cases
UNION ALL SELECT 'messages', count(*) FROM messages
UNION ALL SELECT 'notifications', count(*) FROM notifications;
SELECT current_user;

ALTER USER postgres WITH PASSWORD '1234567890';
SELECT current_database();

SELECT current_database(), current_user, inet_server_addr(), inet_server_port();

SELECT version_num FROM alembic_version;
SELECT to_regclass('public.auth_sessions') AS sessions_table;

SELECT column_name, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name = 'stores'
  AND column_name IN ('shopify_domain', 'shopify_token_ref')
ORDER BY column_name;

SELECT
    u.name AS user_name,
    u.email,
    u.active AS user_active,
    s.name AS store_name,
    m.role,
    m.active AS membership_active
FROM users AS u
JOIN memberships AS m ON m.user_id = u.id
JOIN stores AS s ON s.id = m.store_id
WHERE lower(u.email) = lower('owner@example.com');

SELECT
    u.email,
    s.name AS store_name,
    a.created_at,
    a.expires_at,
    a.revoked_at
FROM auth_sessions AS a
JOIN users AS u ON u.id = a.user_id
JOIN stores AS s ON s.id = a.store_id
WHERE lower(u.email) = lower('ahmed@gmail.com')
ORDER BY a.created_at DESC
LIMIT 5;

SELECT
    u.name AS user_name,
    u.email,
    u.active AS user_active,
    s.name AS store_name,
    m.role,
    m.active AS membership_active
FROM users AS u
JOIN memberships AS m ON m.user_id = u.id
JOIN stores AS s ON s.id = m.store_id
WHERE lower(u.email) = lower('hassan@gmail.com');
