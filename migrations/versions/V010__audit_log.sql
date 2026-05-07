-- V010: Admin audit log.
--
-- Phase I.2 introduces RBAC enforcement; every admin action that mutates a
-- user, role, or permission writes a row here. The log is append-only by
-- convention (no UPDATE / DELETE clauses in `domain/audit.py`).
--
-- `details JSONB` carries the action-specific payload (which role was
-- granted, which permission was assigned, etc.) so the schema does not
-- explode with one column per action type.

CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL    PRIMARY KEY,
    actor_id    VARCHAR(64)  NOT NULL,
    action      VARCHAR(64)  NOT NULL,
    target_id   VARCHAR(64),
    details     JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Most queries filter by actor or target ("show me everything alice did" /
-- "show every action against bob"). An index on `created_at DESC` keeps
-- the typical "last N rows" query cheap.
CREATE INDEX IF NOT EXISTS idx_audit_log_actor      ON audit_log (actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_target     ON audit_log (target_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_action     ON audit_log (action);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log (created_at DESC);

INSERT INTO schema_migrations (version)
VALUES ('V010')
ON CONFLICT (version) DO NOTHING;
