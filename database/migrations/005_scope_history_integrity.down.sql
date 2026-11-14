-- DESTRUCTIVE rollback for migration 005.
-- Export event/history tables before running. Removes v5 history rows and guards.
-- Does not roll back 003 or 004 and does not restore the old audit denylist implementation.

BEGIN;
SELECT pg_advisory_xact_lock(74102852);

DO $preflight$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM app.schema_version WHERE version = 5) THEN
        RAISE EXCEPTION 'Migration 005 is not recorded as installed.';
    END IF;
END
$preflight$;

DROP TRIGGER IF EXISTS trg_component_equipment_parent_guard_v5 ON catalog.components;
DROP FUNCTION IF EXISTS reports.prevent_component_equipment_mismatch_v5();
DROP TRIGGER IF EXISTS trg_inspection_equipment_parent_guard_v5 ON reports.inspections;
DROP FUNCTION IF EXISTS reports.prevent_inspection_equipment_mismatch_v5();
DROP TRIGGER IF EXISTS trg_maintenance_action_equipment_consistency_v5 ON reports.maintenance_actions;
DROP FUNCTION IF EXISTS reports.validate_maintenance_action_equipment_v5();
DROP TRIGGER IF EXISTS trg_inspection_item_equipment_consistency_v5 ON reports.inspection_items;
DROP FUNCTION IF EXISTS reports.validate_inspection_item_equipment_v5();

DROP TRIGGER IF EXISTS trg_equipment_facility_history_no_truncate_v5 ON catalog.equipment_facility_history;
DROP TRIGGER IF EXISTS trg_equipment_facility_history_immutable_v5 ON catalog.equipment_facility_history;
DROP FUNCTION IF EXISTS catalog.prevent_equipment_history_mutation_v5();
DROP TRIGGER IF EXISTS trg_equipment_facility_history_v5 ON catalog.equipment;
DROP TRIGGER IF EXISTS trg_equipment_facility_validate_v5 ON catalog.equipment;
DROP FUNCTION IF EXISTS catalog.capture_equipment_facility_change_v5();
DROP FUNCTION IF EXISTS catalog.validate_equipment_facility_change_v5();
DROP TABLE IF EXISTS catalog.equipment_facility_history;

DROP TRIGGER IF EXISTS trg_account_facility_access_no_delete_v5 ON app.account_facility_access;
DROP TRIGGER IF EXISTS trg_account_roles_no_delete_v5 ON app.account_roles;
DROP FUNCTION IF EXISTS app.prevent_access_assignment_delete_v5();
DROP TRIGGER IF EXISTS trg_account_facility_events_no_truncate_v5 ON app.account_facility_access_events;
DROP TRIGGER IF EXISTS trg_account_facility_events_immutable_v5 ON app.account_facility_access_events;
DROP TRIGGER IF EXISTS trg_account_role_events_no_truncate_v5 ON app.account_role_events;
DROP TRIGGER IF EXISTS trg_account_role_events_immutable_v5 ON app.account_role_events;
DROP FUNCTION IF EXISTS app.prevent_access_history_mutation_v5();
DROP TRIGGER IF EXISTS trg_account_facility_access_event_v5 ON app.account_facility_access;
DROP TRIGGER IF EXISTS trg_account_roles_event_v5 ON app.account_roles;
DROP FUNCTION IF EXISTS app.capture_account_facility_event_v5();
DROP FUNCTION IF EXISTS app.capture_account_role_event_v5();
DROP TABLE IF EXISTS app.account_facility_access_events;
DROP TABLE IF EXISTS app.account_role_events;
DROP INDEX IF EXISTS app.account_roles_effective_v5_idx;
DROP INDEX IF EXISTS app.account_role_events_one_baseline_uidx;
DROP INDEX IF EXISTS app.account_facility_events_one_baseline_uidx;
DROP INDEX IF EXISTS catalog.equipment_facility_history_one_baseline_uidx;
ALTER TABLE app.account_roles
    DROP CONSTRAINT IF EXISTS account_roles_revocation_check_v5,
    DROP CONSTRAINT IF EXISTS account_roles_revoke_reason_check_v5;
ALTER TABLE app.account_roles
    DROP COLUMN IF EXISTS revoked_at,
    DROP COLUMN IF EXISTS revoked_by,
    DROP COLUMN IF EXISTS revoke_reason;
ALTER TABLE app.account_facility_access
    DROP CONSTRAINT IF EXISTS account_facility_revoke_reason_check_v5;
ALTER TABLE app.account_facility_access DROP COLUMN IF EXISTS revoke_reason;

-- Restore the v4 permission helper after removing the v5 revoked_at field.
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

-- Keep the stronger audit denylist function on rollback: it is stricter and backward-compatible.
DELETE FROM app.schema_version WHERE version = 5;
COMMIT;
