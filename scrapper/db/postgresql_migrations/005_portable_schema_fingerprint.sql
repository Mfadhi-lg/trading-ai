-- Schema signatures from this migration onward use portable format 2.
-- PostgreSQL allocates new internal RI trigger IDs during logical restore;
-- the verifier pins their semantic FK identity and enforcement state instead.
COMMENT ON TABLE _postgresql_migrations IS
    'Trading AI native schema lineage; schema_sha256 uses portable format 2 from migration 005 onward';
