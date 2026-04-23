-- V005: Persistent peer node registry.
--
-- Stores the normalised base URLs of known peer nodes so the registry
-- survives process restarts. url is the primary key — inserts use
-- ON CONFLICT DO NOTHING for idempotency, matching InMemoryNodeRegistry
-- deduplication behaviour.

CREATE TABLE IF NOT EXISTS nodes (
    url TEXT PRIMARY KEY
);

INSERT INTO schema_migrations (version)
VALUES ('V005')
ON CONFLICT (version) DO NOTHING;
