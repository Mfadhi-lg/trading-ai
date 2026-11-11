-- Durable identities preserve historical references without recreating queue work.

CREATE TABLE IF NOT EXISTS screener_queue_identities (
    queue_item_id BIGINT PRIMARY KEY,
    identity_basis TEXT NOT NULL CHECK (identity_basis IN ('queue_row', 'historical_reference')),
    evidence_sha256 TEXT,
    CHECK ((identity_basis = 'queue_row' AND evidence_sha256 IS NULL)
        OR (identity_basis = 'historical_reference' AND length(evidence_sha256) = 64 AND evidence_sha256 IS NOT NULL))
);

INSERT INTO screener_queue_identities SELECT queue_item_id,'queue_row',NULL FROM screener_company_queue;

DO $fk$ DECLARE item record; BEGIN
 FOR item IN SELECT conname FROM pg_constraint WHERE conrelid='screener_company_queue_events'::regclass AND confrelid='screener_company_queue'::regclass AND contype='f' LOOP
  EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I','screener_company_queue_events',item.conname);
 END LOOP;
END; $fk$;

ALTER TABLE screener_company_queue_events ADD FOREIGN KEY(queue_item_id) REFERENCES screener_queue_identities(queue_item_id) DEFERRABLE INITIALLY IMMEDIATE;

DO $fk$ DECLARE item record; BEGIN
 FOR item IN SELECT conname FROM pg_constraint WHERE conrelid='exchange_screener_enqueues'::regclass AND confrelid='screener_company_queue'::regclass AND contype='f' LOOP
  EXECUTE format('ALTER TABLE %I DROP CONSTRAINT %I','exchange_screener_enqueues',item.conname);
 END LOOP;
END; $fk$;

ALTER TABLE exchange_screener_enqueues ADD FOREIGN KEY(queue_item_id) REFERENCES screener_queue_identities(queue_item_id) DEFERRABLE INITIALLY IMMEDIATE;

CREATE FUNCTION trg_queue_identity_register() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $trigger$
BEGIN
INSERT INTO screener_queue_identities(queue_item_id,identity_basis,evidence_sha256) VALUES(NEW.queue_item_id,'queue_row',NULL);
IF TG_OP='DELETE' THEN RETURN OLD; END IF;
RETURN NEW;
END;
$trigger$;
CREATE TRIGGER trg_queue_identity_register AFTER INSERT ON screener_company_queue FOR EACH ROW EXECUTE FUNCTION trg_queue_identity_register();

CREATE FUNCTION trg_queue_identity_insert_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $trigger$
BEGIN
IF NEW.identity_basis <> 'queue_row' OR NOT EXISTS(SELECT 1 FROM screener_company_queue WHERE queue_item_id=NEW.queue_item_id) THEN
RAISE EXCEPTION USING MESSAGE='Queue identity registration requires a real queue row', ERRCODE='23514';
END IF;
IF TG_OP='DELETE' THEN RETURN OLD; END IF;
RETURN NEW;
END;
$trigger$;
CREATE TRIGGER trg_queue_identity_insert_guard BEFORE INSERT ON screener_queue_identities FOR EACH ROW EXECUTE FUNCTION trg_queue_identity_insert_guard();

CREATE FUNCTION trg_queue_identity_update_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $trigger$
BEGIN
RAISE EXCEPTION USING MESSAGE='Queue identity history is immutable', ERRCODE='23514';
IF TG_OP='DELETE' THEN RETURN OLD; END IF;
RETURN NEW;
END;
$trigger$;
CREATE TRIGGER trg_queue_identity_update_guard BEFORE UPDATE ON screener_queue_identities FOR EACH ROW EXECUTE FUNCTION trg_queue_identity_update_guard();

CREATE FUNCTION trg_queue_identity_delete_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $trigger$
BEGIN
RAISE EXCEPTION USING MESSAGE='Queue identity history is immutable', ERRCODE='23514';
IF TG_OP='DELETE' THEN RETURN OLD; END IF;
RETURN NEW;
END;
$trigger$;
CREATE TRIGGER trg_queue_identity_delete_guard BEFORE DELETE ON screener_queue_identities FOR EACH ROW EXECUTE FUNCTION trg_queue_identity_delete_guard();

CREATE FUNCTION trg_queue_key_insert_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $trigger$
BEGIN
IF EXISTS(SELECT 1 FROM screener_queue_identities WHERE queue_item_id=NEW.queue_item_id) THEN
RAISE EXCEPTION USING MESSAGE='Queue identity cannot be reused', ERRCODE='23514';
END IF;
IF TG_OP='DELETE' THEN RETURN OLD; END IF;
RETURN NEW;
END;
$trigger$;
CREATE TRIGGER trg_queue_key_insert_guard BEFORE INSERT ON screener_company_queue FOR EACH ROW EXECUTE FUNCTION trg_queue_key_insert_guard();

CREATE FUNCTION trg_queue_key_update_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $trigger$
BEGIN
IF NEW.queue_item_id <> OLD.queue_item_id THEN
RAISE EXCEPTION USING MESSAGE='Queue identity is immutable', ERRCODE='23514';
END IF;
IF TG_OP='DELETE' THEN RETURN OLD; END IF;
RETURN NEW;
END;
$trigger$;
CREATE TRIGGER trg_queue_key_update_guard BEFORE UPDATE OF queue_item_id ON screener_company_queue FOR EACH ROW EXECUTE FUNCTION trg_queue_key_update_guard();

CREATE FUNCTION trg_queue_delete_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $trigger$
BEGIN
RAISE EXCEPTION USING MESSAGE='Queue rows are retained permanently', ERRCODE='23514';
IF TG_OP='DELETE' THEN RETURN OLD; END IF;
RETURN NEW;
END;
$trigger$;
CREATE TRIGGER trg_queue_delete_guard BEFORE DELETE ON screener_company_queue FOR EACH ROW EXECUTE FUNCTION trg_queue_delete_guard();

CREATE FUNCTION trg_queue_natural_update_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $trigger$
BEGIN
IF EXISTS(SELECT 1 FROM screener_company_queue WHERE company_path_normalized=NEW.company_path_normalized AND reporting_scope=NEW.reporting_scope AND result_period=NEW.result_period AND queue_item_id<>OLD.queue_item_id) THEN
RAISE EXCEPTION USING MESSAGE='Queue natural identity conflicts', ERRCODE='23514';
END IF;
IF TG_OP='DELETE' THEN RETURN OLD; END IF;
RETURN NEW;
END;
$trigger$;
CREATE TRIGGER trg_queue_natural_update_guard BEFORE UPDATE OF company_path_normalized,reporting_scope,result_period ON screener_company_queue FOR EACH ROW EXECUTE FUNCTION trg_queue_natural_update_guard();

CREATE FUNCTION trg_queue_claim_insert_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $trigger$
BEGIN
IF NEW.claim_token IS NOT NULL AND EXISTS(SELECT 1 FROM screener_company_queue WHERE claim_token=NEW.claim_token AND queue_item_id<>NEW.queue_item_id) THEN
RAISE EXCEPTION USING MESSAGE='Queue claim token conflicts', ERRCODE='23514';
END IF;
IF TG_OP='DELETE' THEN RETURN OLD; END IF;
RETURN NEW;
END;
$trigger$;
CREATE TRIGGER trg_queue_claim_insert_guard BEFORE INSERT ON screener_company_queue FOR EACH ROW EXECUTE FUNCTION trg_queue_claim_insert_guard();

CREATE FUNCTION trg_queue_claim_update_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $trigger$
BEGIN
IF NEW.claim_token IS NOT NULL AND EXISTS(SELECT 1 FROM screener_company_queue WHERE claim_token=NEW.claim_token AND queue_item_id<>NEW.queue_item_id) THEN
RAISE EXCEPTION USING MESSAGE='Queue claim token conflicts', ERRCODE='23514';
END IF;
IF TG_OP='DELETE' THEN RETURN OLD; END IF;
RETURN NEW;
END;
$trigger$;
CREATE TRIGGER trg_queue_claim_update_guard BEFORE UPDATE OF claim_token ON screener_company_queue FOR EACH ROW EXECUTE FUNCTION trg_queue_claim_update_guard();

CREATE FUNCTION trg_queue_event_insert_parent() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $trigger$
BEGIN
IF NEW.queue_item_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM screener_company_queue WHERE queue_item_id=NEW.queue_item_id) THEN
RAISE EXCEPTION USING MESSAGE='New audit references require a real queue parent', ERRCODE='23514';
END IF;
IF TG_OP='DELETE' THEN RETURN OLD; END IF;
RETURN NEW;
END;
$trigger$;
CREATE TRIGGER trg_queue_event_insert_parent BEFORE INSERT ON screener_company_queue_events FOR EACH ROW EXECUTE FUNCTION trg_queue_event_insert_parent();

CREATE FUNCTION trg_queue_event_update_parent() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $trigger$
BEGIN
IF NEW.queue_item_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM screener_company_queue WHERE queue_item_id=NEW.queue_item_id) AND NEW.queue_item_id IS DISTINCT FROM OLD.queue_item_id THEN
RAISE EXCEPTION USING MESSAGE='New audit references require a real queue parent', ERRCODE='23514';
END IF;
IF TG_OP='DELETE' THEN RETURN OLD; END IF;
RETURN NEW;
END;
$trigger$;
CREATE TRIGGER trg_queue_event_update_parent BEFORE UPDATE OF queue_item_id ON screener_company_queue_events FOR EACH ROW EXECUTE FUNCTION trg_queue_event_update_parent();

CREATE FUNCTION trg_queue_enqueue_insert_parent() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $trigger$
BEGIN
IF NEW.queue_item_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM screener_company_queue WHERE queue_item_id=NEW.queue_item_id) THEN
RAISE EXCEPTION USING MESSAGE='New audit references require a real queue parent', ERRCODE='23514';
END IF;
IF TG_OP='DELETE' THEN RETURN OLD; END IF;
RETURN NEW;
END;
$trigger$;
CREATE TRIGGER trg_queue_enqueue_insert_parent BEFORE INSERT ON exchange_screener_enqueues FOR EACH ROW EXECUTE FUNCTION trg_queue_enqueue_insert_parent();

CREATE FUNCTION trg_queue_enqueue_update_parent() RETURNS trigger
LANGUAGE plpgsql SET search_path FROM CURRENT AS $trigger$
BEGIN
IF NEW.queue_item_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM screener_company_queue WHERE queue_item_id=NEW.queue_item_id) AND NEW.queue_item_id IS DISTINCT FROM OLD.queue_item_id THEN
RAISE EXCEPTION USING MESSAGE='New audit references require a real queue parent', ERRCODE='23514';
END IF;
IF TG_OP='DELETE' THEN RETURN OLD; END IF;
RETURN NEW;
END;
$trigger$;
CREATE TRIGGER trg_queue_enqueue_update_parent BEFORE UPDATE OF queue_item_id ON exchange_screener_enqueues FOR EACH ROW EXECUTE FUNCTION trg_queue_enqueue_update_parent();
