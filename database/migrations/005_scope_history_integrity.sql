-- BackendFork migration 005: durable authorization history, equipment location history,
-- stronger audit-key filtering, and cross-record integrity guards.
-- Requires schema versions 2 + 3 + 4. PostgreSQL 16+.
-- Additive: preserves all current IDs, records and v4 APIs. No real users or secrets are seeded.

BEGIN;
SELECT pg_advisory_xact_lock(74102852);

DO $preflight$
BEGIN
    IF to_regclass('app.schema_version') IS NULL
       OR NOT EXISTS (SELECT 1 FROM app.schema_version WHERE version = 4) THEN
        RAISE EXCEPTION 'Migration 005 requires app.schema_version version 4.';
    END IF;
    IF to_regclass('app.account_roles') IS NULL
       OR to_regclass('app.account_facility_access') IS NULL
       OR to_regclass('app.audit_log') IS NULL
       OR to_regclass('catalog.equipment') IS NULL
       OR to_regclass('reports.inspection_items') IS NULL
       OR to_regclass('reports.maintenance_actions') IS NULL THEN
        RAISE EXCEPTION 'Migration 005 preflight failed: required v4 objects are missing.';
    END IF;
END
$preflight$;

-- 1. Preserve grant/revocation metadata and emit a durable event for every access change.
ALTER TABLE app.account_roles
    ADD COLUMN IF NOT EXISTS revoked_at timestamptz,
    ADD COLUMN IF NOT EXISTS revoked_by text REFERENCES app.accounts(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS revoke_reason text;

ALTER TABLE app.account_facility_access
    ADD COLUMN IF NOT EXISTS revoke_reason text;

DO $role_constraints$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='app.account_roles'::regclass AND conname='account_roles_revocation_check_v5') THEN
        ALTER TABLE app.account_roles ADD CONSTRAINT account_roles_revocation_check_v5
            CHECK (
                (revoked_at IS NULL AND revoked_by IS NULL AND revoke_reason IS NULL)
                OR revoked_at IS NOT NULL
            ) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='app.account_roles'::regclass AND conname='account_roles_revoke_reason_check_v5') THEN
        ALTER TABLE app.account_roles ADD CONSTRAINT account_roles_revoke_reason_check_v5
            CHECK (revoke_reason IS NULL OR length(btrim(revoke_reason)) BETWEEN 1 AND 500) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='app.account_facility_access'::regclass AND conname='account_facility_revoke_reason_check_v5') THEN
        ALTER TABLE app.account_facility_access ADD CONSTRAINT account_facility_revoke_reason_check_v5
            CHECK (revoke_reason IS NULL OR length(btrim(revoke_reason)) BETWEEN 1 AND 500) NOT VALID;
    END IF;
END
$role_constraints$;

CREATE INDEX IF NOT EXISTS account_roles_effective_v5_idx
    ON app.account_roles (account_id, role_key, valid_from, valid_until)
    WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS app.account_role_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id text NOT NULL,
    role_key text NOT NULL,
    event_type text NOT NULL,
    actor_account_id text REFERENCES app.accounts(id) ON DELETE SET NULL,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    reason text,
    request_id text,
    before_data jsonb,
    after_data jsonb,
    CONSTRAINT account_role_events_type_check_v5
        CHECK (event_type IN ('baseline', 'granted', 'updated', 'revoked', 'regranted')),
    CONSTRAINT account_role_events_reason_check_v5
        CHECK (reason IS NULL OR length(btrim(reason)) BETWEEN 1 AND 500),
    CONSTRAINT account_role_events_before_object_check_v5
        CHECK (before_data IS NULL OR jsonb_typeof(before_data) = 'object'),
    CONSTRAINT account_role_events_after_object_check_v5
        CHECK (after_data IS NULL OR jsonb_typeof(after_data) = 'object'),
    FOREIGN KEY (account_id, role_key)
        REFERENCES app.account_roles(account_id, role_key) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX IF NOT EXISTS account_role_events_one_baseline_uidx
    ON app.account_role_events (account_id, role_key) WHERE event_type = 'baseline';
CREATE INDEX IF NOT EXISTS account_role_events_account_idx
    ON app.account_role_events (account_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS account_role_events_role_idx
    ON app.account_role_events (role_key, occurred_at DESC);

CREATE TABLE IF NOT EXISTS app.account_facility_access_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id text NOT NULL,
    facility_id uuid NOT NULL,
    event_type text NOT NULL,
    actor_account_id text REFERENCES app.accounts(id) ON DELETE SET NULL,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    reason text,
    request_id text,
    before_data jsonb,
    after_data jsonb,
    CONSTRAINT account_facility_events_type_check_v5
        CHECK (event_type IN ('baseline', 'granted', 'updated', 'revoked', 'regranted')),
    CONSTRAINT account_facility_events_reason_check_v5
        CHECK (reason IS NULL OR length(btrim(reason)) BETWEEN 1 AND 500),
    CONSTRAINT account_facility_events_before_object_check_v5
        CHECK (before_data IS NULL OR jsonb_typeof(before_data) = 'object'),
    CONSTRAINT account_facility_events_after_object_check_v5
        CHECK (after_data IS NULL OR jsonb_typeof(after_data) = 'object'),
    FOREIGN KEY (account_id, facility_id)
        REFERENCES app.account_facility_access(account_id, facility_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX IF NOT EXISTS account_facility_events_one_baseline_uidx
    ON app.account_facility_access_events (account_id, facility_id) WHERE event_type = 'baseline';
CREATE INDEX IF NOT EXISTS account_facility_events_account_idx
    ON app.account_facility_access_events (account_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS account_facility_events_facility_idx
    ON app.account_facility_access_events (facility_id, occurred_at DESC);

-- Backfill one marked baseline row per existing assignment; this is not presented as a complete prior audit history.
INSERT INTO app.account_role_events (
    account_id, role_key, event_type, actor_account_id, occurred_at, reason, after_data
)
SELECT ar.account_id, ar.role_key, 'baseline', NULL, clock_timestamp(),
       'Baseline snapshot imported by migration 005; earlier changes were not recorded.',
       jsonb_build_object('is_primary', ar.is_primary, 'valid_from', ar.valid_from,
                          'valid_until', ar.valid_until, 'revoked_at', ar.revoked_at)
  FROM app.account_roles AS ar
 WHERE NOT EXISTS (SELECT 1 FROM app.account_role_events AS ev
                    WHERE ev.account_id = ar.account_id AND ev.role_key = ar.role_key
                      AND ev.event_type = 'baseline')
ON CONFLICT DO NOTHING;

INSERT INTO app.account_facility_access_events (
    account_id, facility_id, event_type, actor_account_id, occurred_at, reason, after_data
)
SELECT afa.account_id, afa.facility_id, 'baseline', NULL, clock_timestamp(),
       'Baseline snapshot imported by migration 005; earlier changes were not recorded.',
       jsonb_build_object('is_active', afa.is_active, 'valid_from', afa.valid_from,
                          'valid_until', afa.valid_until, 'revoked_at', afa.revoked_at)
  FROM app.account_facility_access AS afa
 WHERE NOT EXISTS (SELECT 1 FROM app.account_facility_access_events AS ev
                    WHERE ev.account_id = afa.account_id AND ev.facility_id = afa.facility_id
                      AND ev.event_type = 'baseline')
ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION app.prevent_access_history_mutation_v5()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'Access history is append-only; UPDATE, DELETE and TRUNCATE are forbidden.'
        USING ERRCODE = '55000';
END;
$function$;

CREATE OR REPLACE FUNCTION app.capture_account_role_event_v5()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    v_event_type text;
    v_actor text;
    v_reason text;
    v_before jsonb;
    v_after jsonb;
BEGIN
    IF TG_OP = 'INSERT' THEN
        v_event_type := 'granted';
        v_actor := NULLIF(btrim(current_setting('app.actor_account_id', true)), '');
        v_reason := COALESCE(NULLIF(btrim(current_setting('app.change_reason', true)), ''),
                             NULLIF(btrim(NEW.grant_reason), ''));
        v_before := NULL;
    ELSE
        IF OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL THEN
            v_event_type := 'revoked';
        ELSIF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS NULL THEN
            v_event_type := 'regranted';
        ELSE
            v_event_type := 'updated';
        END IF;
        v_actor := NULLIF(btrim(current_setting('app.actor_account_id', true)), '');
        v_reason := CASE
            WHEN v_event_type = 'revoked' THEN COALESCE(NULLIF(btrim(current_setting('app.change_reason', true)), ''), NULLIF(btrim(NEW.revoke_reason), ''))
            WHEN v_event_type = 'regranted' THEN COALESCE(NULLIF(btrim(current_setting('app.change_reason', true)), ''), NULLIF(btrim(NEW.grant_reason), ''))
            ELSE COALESCE(NULLIF(btrim(current_setting('app.change_reason', true)), ''),
                          NULLIF(btrim(NEW.revoke_reason), ''), NULLIF(btrim(NEW.grant_reason), ''))
        END;
        v_before := jsonb_build_object(
            'is_primary', OLD.is_primary,
            'valid_from', OLD.valid_from,
            'valid_until', OLD.valid_until,
            'revoked_at', OLD.revoked_at
        );
    END IF;

    IF v_actor IS NULL THEN
        RAISE EXCEPTION 'Set transaction-local app.actor_account_id from the authenticated backend principal before changing roles.'
            USING ERRCODE = '23514';
    END IF;
    IF v_reason IS NULL OR length(v_reason) < 3 THEN
        RAISE EXCEPTION 'Role grant/revocation requires a transaction-local app.change_reason of at least 3 characters.'
            USING ERRCODE = '23514';
    END IF;

    v_after := jsonb_build_object(
        'is_primary', NEW.is_primary,
        'valid_from', NEW.valid_from,
        'valid_until', NEW.valid_until,
        'revoked_at', NEW.revoked_at
    );

    INSERT INTO app.account_role_events (
        account_id, role_key, event_type, actor_account_id, reason, request_id, before_data, after_data
    ) VALUES (
        NEW.account_id, NEW.role_key, v_event_type, v_actor, v_reason,
        NULLIF(current_setting('app.request_id', true), ''), v_before, v_after
    );
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION app.capture_account_facility_event_v5()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    v_event_type text;
    v_actor text;
    v_reason text;
    v_before jsonb;
    v_after jsonb;
BEGIN
    IF TG_OP = 'INSERT' THEN
        v_event_type := 'granted';
        v_actor := NULLIF(btrim(current_setting('app.actor_account_id', true)), '');
        v_reason := COALESCE(NULLIF(btrim(current_setting('app.change_reason', true)), ''),
                             NULLIF(btrim(NEW.grant_reason), ''));
        v_before := NULL;
    ELSE
        IF OLD.is_active = true AND NEW.is_active = false THEN
            v_event_type := 'revoked';
        ELSIF OLD.is_active = false AND NEW.is_active = true THEN
            v_event_type := 'regranted';
        ELSE
            v_event_type := 'updated';
        END IF;
        v_actor := NULLIF(btrim(current_setting('app.actor_account_id', true)), '');
        v_reason := CASE
            WHEN v_event_type = 'revoked' THEN COALESCE(NULLIF(btrim(current_setting('app.change_reason', true)), ''), NULLIF(btrim(NEW.revoke_reason), ''))
            WHEN v_event_type = 'regranted' THEN COALESCE(NULLIF(btrim(current_setting('app.change_reason', true)), ''), NULLIF(btrim(NEW.grant_reason), ''))
            ELSE COALESCE(NULLIF(btrim(current_setting('app.change_reason', true)), ''),
                          NULLIF(btrim(NEW.revoke_reason), ''), NULLIF(btrim(NEW.grant_reason), ''))
        END;
        v_before := jsonb_build_object(
            'is_active', OLD.is_active,
            'valid_from', OLD.valid_from,
            'valid_until', OLD.valid_until,
            'revoked_at', OLD.revoked_at
        );
    END IF;

    IF v_actor IS NULL THEN
        RAISE EXCEPTION 'Set transaction-local app.actor_account_id from the authenticated backend principal before changing facility access.'
            USING ERRCODE = '23514';
    END IF;
    IF v_reason IS NULL OR length(v_reason) < 3 THEN
        RAISE EXCEPTION 'Facility access grant/revocation requires a transaction-local app.change_reason of at least 3 characters.'
            USING ERRCODE = '23514';
    END IF;

    v_after := jsonb_build_object(
        'is_active', NEW.is_active,
        'valid_from', NEW.valid_from,
        'valid_until', NEW.valid_until,
        'revoked_at', NEW.revoked_at
    );

    INSERT INTO app.account_facility_access_events (
        account_id, facility_id, event_type, actor_account_id, reason, request_id, before_data, after_data
    ) VALUES (
        NEW.account_id, NEW.facility_id, v_event_type, v_actor, v_reason,
        NULLIF(current_setting('app.request_id', true), ''), v_before, v_after
    );
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_account_roles_event_v5 ON app.account_roles;
CREATE TRIGGER trg_account_roles_event_v5
    AFTER INSERT OR UPDATE ON app.account_roles
    FOR EACH ROW EXECUTE FUNCTION app.capture_account_role_event_v5();
DROP TRIGGER IF EXISTS trg_account_facility_access_event_v5 ON app.account_facility_access;
CREATE TRIGGER trg_account_facility_access_event_v5
    AFTER INSERT OR UPDATE ON app.account_facility_access
    FOR EACH ROW EXECUTE FUNCTION app.capture_account_facility_event_v5();

DROP TRIGGER IF EXISTS trg_account_role_events_immutable_v5 ON app.account_role_events;
CREATE TRIGGER trg_account_role_events_immutable_v5
    BEFORE UPDATE OR DELETE ON app.account_role_events
    FOR EACH ROW EXECUTE FUNCTION app.prevent_access_history_mutation_v5();
DROP TRIGGER IF EXISTS trg_account_role_events_no_truncate_v5 ON app.account_role_events;
CREATE TRIGGER trg_account_role_events_no_truncate_v5
    BEFORE TRUNCATE ON app.account_role_events
    FOR EACH STATEMENT EXECUTE FUNCTION app.prevent_access_history_mutation_v5();
DROP TRIGGER IF EXISTS trg_account_facility_events_immutable_v5 ON app.account_facility_access_events;
CREATE TRIGGER trg_account_facility_events_immutable_v5
    BEFORE UPDATE OR DELETE ON app.account_facility_access_events
    FOR EACH ROW EXECUTE FUNCTION app.prevent_access_history_mutation_v5();
DROP TRIGGER IF EXISTS trg_account_facility_events_no_truncate_v5 ON app.account_facility_access_events;
CREATE TRIGGER trg_account_facility_events_no_truncate_v5
    BEFORE TRUNCATE ON app.account_facility_access_events
    FOR EACH STATEMENT EXECUTE FUNCTION app.prevent_access_history_mutation_v5();

CREATE OR REPLACE FUNCTION app.prevent_access_assignment_delete_v5()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'Access assignments are historical; revoke them instead of deleting rows.'
        USING ERRCODE = '55000';
END;
$function$;
DROP TRIGGER IF EXISTS trg_account_roles_no_delete_v5 ON app.account_roles;
CREATE TRIGGER trg_account_roles_no_delete_v5 BEFORE DELETE ON app.account_roles
    FOR EACH ROW EXECUTE FUNCTION app.prevent_access_assignment_delete_v5();
DROP TRIGGER IF EXISTS trg_account_facility_access_no_delete_v5 ON app.account_facility_access;
CREATE TRIGGER trg_account_facility_access_no_delete_v5 BEFORE DELETE ON app.account_facility_access
    FOR EACH ROW EXECUTE FUNCTION app.prevent_access_assignment_delete_v5();

-- Update the existing helper in place so the backend contract and function name stay stable.
CREATE OR REPLACE FUNCTION app.account_has_permission_v4(p_account_id text, p_permission_key text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, app
AS $function$
    SELECT EXISTS (
        SELECT 1
          FROM app.accounts AS a
          JOIN app.account_roles AS ar ON ar.account_id = a.id
          JOIN app.role_permissions AS rp ON rp.role_key = ar.role_key
         WHERE a.id = p_account_id
           AND a.is_active = true
           AND a.must_change_pin = false
           AND (a.locked_until IS NULL OR a.locked_until <= now())
           AND ar.revoked_at IS NULL
           AND ar.valid_from <= now()
           AND (ar.valid_until IS NULL OR ar.valid_until > now())
           AND rp.permission_key = p_permission_key
           AND (a.account_type <> 'employee' OR EXISTS (
               SELECT 1 FROM app.employees AS e
                WHERE e.account_id = a.id AND e.employment_status = 'active'
           ))
           AND NOT EXISTS (
               SELECT 1 FROM app.employees AS e
                WHERE e.account_id = a.id AND e.employment_status <> 'active'
           )
    );
$function$;

-- 2. Equipment location history. Every move needs a transaction-local reason.
CREATE TABLE IF NOT EXISTS catalog.equipment_facility_history (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type text NOT NULL DEFAULT 'changed',
    equipment_id uuid NOT NULL REFERENCES catalog.equipment(id) ON DELETE RESTRICT,
    previous_facility_id uuid REFERENCES app.facilities(id) ON DELETE RESTRICT,
    new_facility_id uuid REFERENCES app.facilities(id) ON DELETE RESTRICT,
    changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    changed_by_account_id text REFERENCES app.accounts(id) ON DELETE SET NULL,
    change_reason text NOT NULL,
    request_id text,
    CONSTRAINT equipment_facility_history_event_type_check_v5
        CHECK (event_type IN ('baseline', 'assigned', 'moved', 'unassigned')),
    CONSTRAINT equipment_facility_history_change_check_v5
        CHECK (previous_facility_id IS DISTINCT FROM new_facility_id),
    CONSTRAINT equipment_facility_history_reason_check_v5
        CHECK (length(btrim(change_reason)) BETWEEN 3 AND 500)
);
CREATE UNIQUE INDEX IF NOT EXISTS equipment_facility_history_one_baseline_uidx
    ON catalog.equipment_facility_history (equipment_id) WHERE event_type = 'baseline';
CREATE INDEX IF NOT EXISTS equipment_facility_history_equipment_idx
    ON catalog.equipment_facility_history (equipment_id, changed_at DESC);
CREATE INDEX IF NOT EXISTS equipment_facility_history_facility_idx
    ON catalog.equipment_facility_history (new_facility_id, changed_at DESC)
    WHERE new_facility_id IS NOT NULL;

-- Current locations assigned before v5 get a baseline snapshot, not a fabricated move event.
INSERT INTO catalog.equipment_facility_history (
    event_type, equipment_id, previous_facility_id, new_facility_id, change_reason
)
SELECT 'baseline', eq.id, NULL, eq.facility_id,
       'Baseline snapshot imported by migration 005; earlier location history was unavailable.'
  FROM catalog.equipment AS eq
 WHERE eq.facility_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM catalog.equipment_facility_history AS h
                    WHERE h.equipment_id = eq.id AND h.event_type = 'baseline')
ON CONFLICT DO NOTHING;

CREATE OR REPLACE FUNCTION catalog.validate_equipment_facility_change_v5()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    v_reason text;
    v_actor text;
    v_previous_facility_id uuid;
BEGIN
    IF TG_OP = 'INSERT' THEN
        v_previous_facility_id := NULL;
    ELSE
        v_previous_facility_id := OLD.facility_id;
    END IF;

    IF NEW.facility_id IS DISTINCT FROM v_previous_facility_id
       AND (NEW.facility_id IS NOT NULL OR v_previous_facility_id IS NOT NULL) THEN
        v_actor := NULLIF(btrim(current_setting('app.actor_account_id', true)), '');
        IF v_actor IS NULL THEN
            RAISE EXCEPTION 'Changing catalog.equipment.facility_id requires SET LOCAL app.actor_account_id.'
                USING ERRCODE = '23514';
        END IF;
        v_reason := NULLIF(btrim(current_setting('app.change_reason', true)), '');
        IF v_reason IS NULL OR length(v_reason) < 3 THEN
            RAISE EXCEPTION 'Changing catalog.equipment.facility_id requires SET LOCAL app.change_reason.'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.facility_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM app.facilities WHERE id = NEW.facility_id AND is_active = true
        ) THEN
            RAISE EXCEPTION 'Equipment can only be assigned to an active facility.'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION catalog.capture_equipment_facility_change_v5()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    v_actor text;
    v_reason text;
    v_request_id text;
    v_event_source text;
    v_previous_facility_id uuid;
    v_event_type text;
    v_action_key text;
BEGIN
    IF TG_OP = 'INSERT' THEN
        v_previous_facility_id := NULL;
    ELSE
        v_previous_facility_id := OLD.facility_id;
    END IF;

    IF NEW.facility_id IS NOT DISTINCT FROM v_previous_facility_id THEN
        RETURN NEW;
    END IF;
    IF NEW.facility_id IS NULL AND v_previous_facility_id IS NULL THEN
        RETURN NEW;
    END IF;

    v_actor := NULLIF(btrim(current_setting('app.actor_account_id', true)), '');
    v_reason := NULLIF(btrim(current_setting('app.change_reason', true)), '');
    v_request_id := NULLIF(current_setting('app.request_id', true), '');
    v_event_source := COALESCE(NULLIF(current_setting('app.event_source', true), ''), 'api');

    IF v_previous_facility_id IS NULL THEN
        v_event_type := 'assigned';
        v_action_key := 'equipment.facility.assigned';
    ELSIF NEW.facility_id IS NULL THEN
        v_event_type := 'unassigned';
        v_action_key := 'equipment.facility.unassigned';
    ELSE
        v_event_type := 'moved';
        v_action_key := 'equipment.facility.changed';
    END IF;

    INSERT INTO catalog.equipment_facility_history (
        event_type, equipment_id, previous_facility_id, new_facility_id,
        changed_by_account_id, change_reason, request_id
    ) VALUES (
        v_event_type, NEW.id, v_previous_facility_id, NEW.facility_id,
        v_actor, v_reason, v_request_id
    );

    INSERT INTO app.audit_log (
        actor_id, action_key, resource_type, resource_id, request_id,
        facility_id, outcome, event_source, reason, before_data, after_data
    ) VALUES (
        v_actor, v_action_key, 'equipment', NEW.id::text, v_request_id,
        COALESCE(NEW.facility_id, v_previous_facility_id), 'success', v_event_source, v_reason,
        jsonb_build_object('facility_id', v_previous_facility_id),
        jsonb_build_object('facility_id', NEW.facility_id)
    );
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_equipment_facility_validate_v5 ON catalog.equipment;
CREATE TRIGGER trg_equipment_facility_validate_v5
    BEFORE INSERT OR UPDATE OF facility_id ON catalog.equipment
    FOR EACH ROW EXECUTE FUNCTION catalog.validate_equipment_facility_change_v5();
DROP TRIGGER IF EXISTS trg_equipment_facility_history_v5 ON catalog.equipment;
CREATE TRIGGER trg_equipment_facility_history_v5
    AFTER INSERT OR UPDATE OF facility_id ON catalog.equipment
    FOR EACH ROW EXECUTE FUNCTION catalog.capture_equipment_facility_change_v5();

CREATE OR REPLACE FUNCTION catalog.prevent_equipment_history_mutation_v5()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'Equipment facility history is append-only.' USING ERRCODE = '55000';
END;
$function$;
DROP TRIGGER IF EXISTS trg_equipment_facility_history_immutable_v5 ON catalog.equipment_facility_history;
CREATE TRIGGER trg_equipment_facility_history_immutable_v5
    BEFORE UPDATE OR DELETE ON catalog.equipment_facility_history
    FOR EACH ROW EXECUTE FUNCTION catalog.prevent_equipment_history_mutation_v5();
DROP TRIGGER IF EXISTS trg_equipment_facility_history_no_truncate_v5 ON catalog.equipment_facility_history;
CREATE TRIGGER trg_equipment_facility_history_no_truncate_v5
    BEFORE TRUNCATE ON catalog.equipment_facility_history
    FOR EACH STATEMENT EXECUTE FUNCTION catalog.prevent_equipment_history_mutation_v5();

-- 3. Prevent cross-equipment references. Existing rows are not silently rewritten;
-- verification reports any legacy mismatch, while triggers reject new mismatched writes.
CREATE OR REPLACE FUNCTION reports.validate_inspection_item_equipment_v5()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    v_inspection_equipment_id uuid;
    v_component_equipment_id uuid;
BEGIN
    SELECT equipment_id INTO v_inspection_equipment_id
      FROM reports.inspections WHERE id = NEW.inspection_id;
    SELECT equipment_id INTO v_component_equipment_id
      FROM catalog.components WHERE id = NEW.component_id;

    IF v_inspection_equipment_id IS NULL OR v_component_equipment_id IS NULL THEN
        RAISE EXCEPTION 'Inspection and component must exist before adding an inspection item.'
            USING ERRCODE = '23503';
    END IF;
    IF v_inspection_equipment_id <> v_component_equipment_id THEN
        RAISE EXCEPTION 'Inspection item component must belong to the inspected equipment.'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;
DROP TRIGGER IF EXISTS trg_inspection_item_equipment_consistency_v5 ON reports.inspection_items;
CREATE TRIGGER trg_inspection_item_equipment_consistency_v5
    BEFORE INSERT OR UPDATE OF inspection_id, component_id ON reports.inspection_items
    FOR EACH ROW EXECUTE FUNCTION reports.validate_inspection_item_equipment_v5();

-- A maintenance action must describe the same equipment as its component and optional inspection/finding.
-- Composite foreign keys provide referential integrity; this trigger makes equipment mismatch explicit (23514).
CREATE OR REPLACE FUNCTION reports.validate_maintenance_action_equipment_v5()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    v_component_equipment_id uuid;
    v_inspection_equipment_id uuid;
    v_finding_inspection_id uuid;
    v_finding_component_id uuid;
    v_finding_installation_id uuid;
BEGIN
    SELECT equipment_id INTO v_component_equipment_id
      FROM catalog.components WHERE id = NEW.component_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Maintenance action component does not exist.'
            USING ERRCODE = '23503';
    END IF;
    IF v_component_equipment_id <> NEW.equipment_id THEN
        RAISE EXCEPTION 'Maintenance action equipment must match its component equipment.'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.inspection_id IS NOT NULL THEN
        SELECT equipment_id INTO v_inspection_equipment_id
          FROM reports.inspections WHERE id = NEW.inspection_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Maintenance action inspection does not exist.'
                USING ERRCODE = '23503';
        END IF;
        IF v_inspection_equipment_id <> NEW.equipment_id THEN
            RAISE EXCEPTION 'Maintenance action equipment must match its inspection equipment.'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    IF NEW.finding_id IS NOT NULL THEN
        SELECT inspection_id, component_id, installation_id
          INTO v_finding_inspection_id, v_finding_component_id, v_finding_installation_id
          FROM reports.inspection_findings WHERE id = NEW.finding_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Maintenance action finding does not exist.'
                USING ERRCODE = '23503';
        END IF;
        IF NEW.inspection_id IS DISTINCT FROM v_finding_inspection_id
           OR NEW.component_id IS DISTINCT FROM v_finding_component_id
           OR NEW.installation_id IS DISTINCT FROM v_finding_installation_id THEN
            RAISE EXCEPTION 'Maintenance action must reference the exact inspection, component and installation of its finding.'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$function$;
DROP TRIGGER IF EXISTS trg_maintenance_action_equipment_consistency_v5 ON reports.maintenance_actions;
CREATE TRIGGER trg_maintenance_action_equipment_consistency_v5
    BEFORE INSERT OR UPDATE OF equipment_id, component_id, installation_id, inspection_id, finding_id
    ON reports.maintenance_actions
    FOR EACH ROW EXECUTE FUNCTION reports.validate_maintenance_action_equipment_v5();

CREATE OR REPLACE FUNCTION reports.prevent_inspection_equipment_mismatch_v5()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM reports.inspection_items AS ii
          JOIN catalog.components AS c ON c.id = ii.component_id
         WHERE ii.inspection_id = NEW.id
           AND c.equipment_id <> NEW.equipment_id
    ) THEN
        RAISE EXCEPTION 'Changing inspection equipment would invalidate one or more component links.'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;
DROP TRIGGER IF EXISTS trg_inspection_equipment_parent_guard_v5 ON reports.inspections;
CREATE TRIGGER trg_inspection_equipment_parent_guard_v5
    BEFORE UPDATE OF equipment_id ON reports.inspections
    FOR EACH ROW EXECUTE FUNCTION reports.prevent_inspection_equipment_mismatch_v5();

CREATE OR REPLACE FUNCTION reports.prevent_component_equipment_mismatch_v5()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM reports.inspection_items AS ii
          JOIN reports.inspections AS i ON i.id = ii.inspection_id
         WHERE ii.component_id = OLD.id
           AND i.equipment_id <> NEW.equipment_id
    ) THEN
        RAISE EXCEPTION 'Changing component equipment would invalidate one or more inspection links.'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM reports.maintenance_actions AS ma
         WHERE ma.component_id = OLD.id AND ma.equipment_id <> NEW.equipment_id
    ) THEN
        RAISE EXCEPTION 'Changing component equipment would invalidate maintenance history.'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;
DROP TRIGGER IF EXISTS trg_component_equipment_parent_guard_v5 ON catalog.components;
CREATE TRIGGER trg_component_equipment_parent_guard_v5
    BEFORE UPDATE OF equipment_id ON catalog.components
    FOR EACH ROW EXECUTE FUNCTION reports.prevent_component_equipment_mismatch_v5();

-- 4. Strengthen the recursive audit-key denylist: normalize snake_case/camelCase/hyphens.
CREATE OR REPLACE FUNCTION app.audit_json_has_sensitive_key_v4(p_value jsonb)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $function$
    SELECT CASE jsonb_typeof(p_value)
        WHEN 'object' THEN EXISTS (
            SELECT 1
              FROM jsonb_each(p_value) AS item(key, value)
             WHERE regexp_replace(lower(item.key), '[^a-z0-9]', '', 'g') = ANY (ARRAY[
                 'pin', 'pinhash', 'temppin', 'temporarypin',
                 'password', 'passwordhash', 'passwd',
                 'token', 'sessiontoken', 'accesstoken', 'refreshtoken', 'bearertoken',
                 'authorization', 'authorizationheader', 'cookie',
                 'secret', 'clientsecret', 'apisecret', 'apikey', 'credential', 'credentials',
                 'totpsecret', 'mfasecret', 'recoverycode', 'privatekey'
             ]::text[])
                OR app.audit_json_has_sensitive_key_v4(item.value)
        )
        WHEN 'array' THEN EXISTS (
            SELECT 1
              FROM jsonb_array_elements(p_value) AS item(value)
             WHERE app.audit_json_has_sensitive_key_v4(item.value)
        )
        ELSE false
    END;
$function$;

INSERT INTO app.schema_version (version) VALUES (5)
ON CONFLICT (version) DO NOTHING;

COMMIT;
