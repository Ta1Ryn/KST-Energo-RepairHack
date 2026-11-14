-- Destructive rollback for migration 006.
-- Export app.facilities.parent_facility_id and account_facility_access.include_descendants first.
-- This rollback removes the hierarchy and inheritance semantics; it does not roll back migrations 003-005.

BEGIN;
SELECT pg_advisory_xact_lock(74102852);

-- Restore the original v5 access-event payload before removing include_descendants.
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
        v_reason := NULLIF(btrim(current_setting('app.change_reason', true)), '');
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
        v_reason := NULLIF(btrim(current_setting('app.change_reason', true)), '');
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

-- Restore the original v4 helper behavior before removing the v6 wrappers.
CREATE OR REPLACE FUNCTION app.account_can_access_facility_v4(p_account_id text, p_facility_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, app
AS $function$
    SELECT CASE
        WHEN p_facility_id IS NULL THEN false
        ELSE EXISTS (SELECT 1 FROM app.facilities f WHERE f.id = p_facility_id AND f.is_active = true)
         AND (
             app.account_has_permission_v4(p_account_id, 'facilities.access_all')
             OR EXISTS (
                 SELECT 1
                   FROM app.account_facility_access AS afa
                   JOIN app.accounts AS a ON a.id = afa.account_id
                  WHERE afa.account_id = p_account_id
                    AND afa.facility_id = p_facility_id
                    AND afa.is_active = true
                    AND afa.valid_from <= now()
                    AND (afa.valid_until IS NULL OR afa.valid_until > now())
                    AND a.is_active = true
                    AND a.must_change_pin = false
                    AND (a.locked_until IS NULL OR a.locked_until <= now())
                    AND (a.account_type <> 'employee' OR EXISTS (
                        SELECT 1 FROM app.employees AS active_employee
                         WHERE active_employee.account_id = a.id AND active_employee.employment_status = 'active'
                    ))
                    AND NOT EXISTS (
                        SELECT 1 FROM app.employees AS e
                         WHERE e.account_id = a.id AND e.employment_status <> 'active'
                    )
             )
         )
    END;
$function$;

CREATE OR REPLACE FUNCTION app.account_can_access_equipment_v4(p_account_id text, p_equipment_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, app
AS $function$
    SELECT COALESCE((
        SELECT CASE
            WHEN eq.facility_id IS NULL THEN app.account_has_permission_v4(p_account_id, 'equipment.read_unassigned')
            ELSE app.account_can_access_facility_v4(p_account_id, eq.facility_id)
        END
          FROM catalog.equipment AS eq
         WHERE eq.id = p_equipment_id
    ), false);
$function$;

CREATE OR REPLACE FUNCTION app.account_can_access_employee_v4(p_actor_account_id text, p_employee_account_id text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, app
AS $function$
    SELECT COALESCE((
        SELECT CASE
            WHEN app.account_has_permission_v4(p_actor_account_id, 'employees.read') THEN true
            WHEN p_actor_account_id = p_employee_account_id
                 AND app.account_has_permission_v4(p_actor_account_id, 'employees.read_own') THEN true
            WHEN app.account_has_permission_v4(p_actor_account_id, 'employees.read_assigned')
                 AND EXISTS (
                     SELECT 1
                       FROM app.department_facilities AS df
                       JOIN app.departments AS target_department
                         ON target_department.id = df.department_id AND target_department.is_active = true
                       JOIN app.account_facility_access AS afa
                         ON afa.facility_id = df.facility_id
                       JOIN app.facilities AS f ON f.id = df.facility_id AND f.is_active = true
                      WHERE df.department_id = target.department_id
                        AND afa.account_id = p_actor_account_id
                        AND afa.is_active = true
                        AND afa.valid_from <= now()
                        AND (afa.valid_until IS NULL OR afa.valid_until > now())
                 ) THEN true
            ELSE false
        END
          FROM app.employees AS target
         WHERE target.account_id = p_employee_account_id
    ), false);
$function$;

DROP FUNCTION IF EXISTS app.account_can_access_employee_v6(text, text);
DROP FUNCTION IF EXISTS app.account_can_access_facility_v6(text, uuid);
DROP FUNCTION IF EXISTS app.account_is_eligible_v6(text);

DROP TRIGGER IF EXISTS trg_facility_no_cycle_v6 ON app.facilities;
DROP FUNCTION IF EXISTS app.prevent_facility_hierarchy_cycle_v6();
DROP TRIGGER IF EXISTS trg_employee_account_relink_v6 ON app.employees;
DROP FUNCTION IF EXISTS app.prevent_employee_account_relink_v6();
DROP TRIGGER IF EXISTS trg_employees_account_pair_v6 ON app.employees;
DROP TRIGGER IF EXISTS trg_accounts_employee_pair_v6 ON app.accounts;
DROP FUNCTION IF EXISTS app.assert_employee_account_pair_v6();

ALTER TABLE app.account_facility_access
    DROP COLUMN IF EXISTS include_descendants;
ALTER TABLE app.facilities
    DROP CONSTRAINT IF EXISTS facilities_no_self_parent_v6,
    DROP CONSTRAINT IF EXISTS facilities_parent_facility_id_fkey_v6,
    DROP COLUMN IF EXISTS parent_facility_id;
DROP INDEX IF EXISTS app.facilities_parent_idx_v6;

DELETE FROM app.schema_version WHERE version = 6;

COMMIT;
