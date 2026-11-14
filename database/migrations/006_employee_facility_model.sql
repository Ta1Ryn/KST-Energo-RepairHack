-- BackendFork migration 006: harden the two agreed work packages:
-- (1) separate HR employee profiles from login accounts;
-- (2) scope access by departments and hierarchical industrial facilities.
-- Requires schema versions 2..5 (migrations 003, 004 and 005 already applied).
-- PostgreSQL 16+. Additive; does not invent employee, department, facility or equipment data.

BEGIN;

SELECT pg_advisory_xact_lock(74102852);

DO $preflight$
BEGIN
    IF to_regclass('app.schema_version') IS NULL
       OR NOT EXISTS (SELECT 1 FROM app.schema_version WHERE version = 5) THEN
        RAISE EXCEPTION 'Migration 006 requires app.schema_version version 5. Apply migrations 003-005 first.';
    END IF;

    IF to_regclass('app.accounts') IS NULL
       OR to_regclass('app.employees') IS NULL
       OR to_regclass('app.departments') IS NULL
       OR to_regclass('app.facilities') IS NULL
       OR to_regclass('app.department_facilities') IS NULL
       OR to_regclass('app.account_facility_access') IS NULL
       OR to_regclass('app.account_facility_access_events') IS NULL
       OR to_regclass('catalog.equipment') IS NULL THEN
        RAISE EXCEPTION 'Migration 006 preflight failed: required v5 workforce/facility tables are missing.';
    END IF;

    -- Do not silently bless incomplete or contradictory legacy identities.
    IF EXISTS (
        SELECT 1
          FROM app.accounts a
          LEFT JOIN app.employees e ON e.account_id = a.id
         WHERE a.account_type = 'employee' AND e.account_id IS NULL
    ) THEN
        RAISE EXCEPTION 'Migration 006 blocked: employee accounts without app.employees profiles exist. Reconcile them first.';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM app.employees e
          JOIN app.accounts a ON a.id = e.account_id
         WHERE a.account_type <> 'employee'
    ) THEN
        RAISE EXCEPTION 'Migration 006 blocked: app.employees rows are linked to non-employee accounts.';
    END IF;
END
$preflight$;

-- ---------------------------------------------------------------------------
-- Phase 1: enforce the boundary between login identity and HR profile.
-- app.accounts remains the stable authentication/foreign-key identity.
-- app.employees remains the source of truth for employee/organizational data.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION app.assert_employee_account_pair_v6()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    v_account_id text;
    v_account_type text;
    v_account_exists boolean;
    v_has_profile boolean;
BEGIN
    IF TG_TABLE_NAME = 'accounts' THEN
        IF TG_OP = 'DELETE' THEN
            v_account_id := OLD.id;
        ELSE
            v_account_id := NEW.id;
        END IF;
    ELSE
        IF TG_OP = 'DELETE' THEN
            v_account_id := OLD.account_id;
        ELSE
            v_account_id := NEW.account_id;
        END IF;
    END IF;

    SELECT EXISTS (SELECT 1 FROM app.accounts AS a WHERE a.id = v_account_id)
      INTO v_account_exists;

    SELECT a.account_type
      INTO v_account_type
      FROM app.accounts AS a
     WHERE a.id = v_account_id;

    SELECT EXISTS (
        SELECT 1 FROM app.employees AS e WHERE e.account_id = v_account_id
    ) INTO v_has_profile;

    -- If the account no longer exists, FK constraints decide whether its removal is legal.
    IF NOT v_account_exists THEN
        IF v_has_profile THEN
            RAISE EXCEPTION 'Employee profile % has no account.', v_account_id
                USING ERRCODE = '23503';
        END IF;
        RETURN NULL;
    END IF;

    IF (v_account_type = 'employee') IS DISTINCT FROM v_has_profile THEN
        RAISE EXCEPTION 'Account/profile mismatch for account %: account_type=%, employee_profile_exists=%',
            v_account_id, v_account_type, v_has_profile
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$function$;

DROP TRIGGER IF EXISTS trg_accounts_employee_pair_v6 ON app.accounts;
CREATE CONSTRAINT TRIGGER trg_accounts_employee_pair_v6
    AFTER INSERT OR UPDATE OR DELETE ON app.accounts
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION app.assert_employee_account_pair_v6();

DROP TRIGGER IF EXISTS trg_employees_account_pair_v6 ON app.employees;
CREATE CONSTRAINT TRIGGER trg_employees_account_pair_v6
    AFTER INSERT OR UPDATE OR DELETE ON app.employees
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION app.assert_employee_account_pair_v6();

CREATE OR REPLACE FUNCTION app.prevent_employee_account_relink_v6()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.account_id IS DISTINCT FROM OLD.account_id THEN
        RAISE EXCEPTION 'Employee account_id is immutable. Create a new account and use an approved identity-linking process.'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_employee_account_relink_v6 ON app.employees;
CREATE TRIGGER trg_employee_account_relink_v6
    BEFORE UPDATE OF account_id ON app.employees
    FOR EACH ROW EXECUTE FUNCTION app.prevent_employee_account_relink_v6();

COMMENT ON TABLE app.accounts IS 'Authentication identity. Employee HR data belongs in app.employees; accounts.id remains stable for inspection authorship.';
COMMENT ON TABLE app.employees IS 'HR profile, separate from authentication. employee_number, name, position, department and employment status are owned here.';
COMMENT ON COLUMN app.accounts.employee_code IS 'Deprecated compatibility mirror. Source of truth: app.employees.employee_number.';
COMMENT ON COLUMN app.accounts.full_name IS 'Deprecated compatibility mirror. Source of truth: app.employees.full_name.';

-- ---------------------------------------------------------------------------
-- Phase 2: make facilities hierarchical and support explicit inherited scope.
-- A grant on a parent facility only reaches descendants when include_descendants=true.
-- ---------------------------------------------------------------------------

ALTER TABLE app.facilities
    ADD COLUMN IF NOT EXISTS parent_facility_id uuid;

DO $facility_parent_fk$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'app.facilities'::regclass
           AND conname = 'facilities_parent_facility_id_fkey_v6'
    ) THEN
        ALTER TABLE app.facilities
            ADD CONSTRAINT facilities_parent_facility_id_fkey_v6
            FOREIGN KEY (parent_facility_id)
            REFERENCES app.facilities(id) ON DELETE RESTRICT;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'app.facilities'::regclass
           AND conname = 'facilities_no_self_parent_v6'
    ) THEN
        ALTER TABLE app.facilities
            ADD CONSTRAINT facilities_no_self_parent_v6
            CHECK (parent_facility_id IS NULL OR parent_facility_id <> id);
    END IF;
END
$facility_parent_fk$;

CREATE INDEX IF NOT EXISTS facilities_parent_idx_v6
    ON app.facilities (parent_facility_id)
    WHERE parent_facility_id IS NOT NULL;

ALTER TABLE app.account_facility_access
    ADD COLUMN IF NOT EXISTS include_descendants boolean NOT NULL DEFAULT false;

-- Extend the existing v5 access-history event so changes to inheritance are auditable too.
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
            'revoked_at', OLD.revoked_at,
            'include_descendants', OLD.include_descendants
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
        'revoked_at', NEW.revoked_at,
        'include_descendants', NEW.include_descendants
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

COMMENT ON COLUMN app.facilities.parent_facility_id IS 'Parent in the enterprise/site/workshop/station hierarchy. NULL means root facility.';
COMMENT ON COLUMN app.account_facility_access.include_descendants IS 'When true, this grant covers active descendants in the facility tree; false grants access only to this facility.';

CREATE OR REPLACE FUNCTION app.prevent_facility_hierarchy_cycle_v6()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
DECLARE
    v_cycle_found boolean;
BEGIN
    -- Serialize hierarchy edits; otherwise two concurrent re-parent operations could form a cycle.
    PERFORM pg_advisory_xact_lock(74102853);

    IF NEW.parent_facility_id IS NULL THEN
        RETURN NEW;
    END IF;

    IF NEW.parent_facility_id = NEW.id THEN
        RAISE EXCEPTION 'A facility cannot be its own parent.' USING ERRCODE = '23514';
    END IF;

    WITH RECURSIVE ancestors AS (
        SELECT f.id, f.parent_facility_id, ARRAY[f.id]::uuid[] AS path
          FROM app.facilities AS f
         WHERE f.id = NEW.parent_facility_id
        UNION ALL
        SELECT p.id, p.parent_facility_id, a.path || p.id
          FROM ancestors AS a
          JOIN app.facilities AS p ON p.id = a.parent_facility_id
         WHERE NOT p.id = ANY(a.path)
    )
    SELECT EXISTS (
        SELECT 1 FROM ancestors WHERE id = NEW.id
    ) INTO v_cycle_found;

    IF v_cycle_found THEN
        RAISE EXCEPTION 'Facility hierarchy cycle detected for facility %.', NEW.id
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_facility_no_cycle_v6 ON app.facilities;
CREATE TRIGGER trg_facility_no_cycle_v6
    BEFORE INSERT OR UPDATE OF parent_facility_id ON app.facilities
    FOR EACH ROW EXECUTE FUNCTION app.prevent_facility_hierarchy_cycle_v6();

CREATE OR REPLACE FUNCTION app.account_is_eligible_v6(p_account_id text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, app
AS $function$
    SELECT EXISTS (
        SELECT 1
          FROM app.accounts AS a
         WHERE a.id = p_account_id
           AND a.is_active = true
           AND a.must_change_pin = false
           AND (a.locked_until IS NULL OR a.locked_until <= now())
           AND (
                a.account_type <> 'employee'
                OR EXISTS (
                    SELECT 1 FROM app.employees AS e
                     WHERE e.account_id = a.id
                       AND e.employment_status = 'active'
                )
           )
           AND NOT EXISTS (
                SELECT 1 FROM app.employees AS e
                 WHERE e.account_id = a.id
                   AND e.employment_status <> 'active'
           )
    );
$function$;

CREATE OR REPLACE FUNCTION app.account_can_access_facility_v6(p_account_id text, p_facility_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, app
AS $function$
    WITH RECURSIVE facility_ancestors AS (
        SELECT f.id, f.parent_facility_id, f.is_active, 0 AS depth,
               ARRAY[f.id]::uuid[] AS path
          FROM app.facilities AS f
         WHERE f.id = p_facility_id
        UNION ALL
        SELECT parent.id, parent.parent_facility_id, parent.is_active,
               child.depth + 1, child.path || parent.id
          FROM facility_ancestors AS child
          JOIN app.facilities AS parent ON parent.id = child.parent_facility_id
         WHERE NOT parent.id = ANY(child.path)
    )
    SELECT CASE
        WHEN p_account_id IS NULL OR p_facility_id IS NULL THEN false
        WHEN NOT app.account_is_eligible_v6(p_account_id) THEN false
        WHEN NOT EXISTS (SELECT 1 FROM facility_ancestors WHERE depth = 0) THEN false
        WHEN NOT COALESCE((SELECT bool_and(is_active) FROM facility_ancestors), false) THEN false
        WHEN app.account_has_permission_v4(p_account_id, 'facilities.access_all') THEN true
        ELSE EXISTS (
            SELECT 1
              FROM facility_ancestors AS ancestor
              JOIN app.account_facility_access AS access_grant
                ON access_grant.facility_id = ancestor.id
             WHERE access_grant.account_id = p_account_id
               AND access_grant.is_active = true
               AND access_grant.valid_from <= now()
               AND (access_grant.valid_until IS NULL OR access_grant.valid_until > now())
               AND access_grant.revoked_at IS NULL
               AND (ancestor.depth = 0 OR access_grant.include_descendants = true)
        )
    END;
$function$;

-- Keep the v4 helper signatures as compatibility wrappers for existing backend code.
CREATE OR REPLACE FUNCTION app.account_can_access_facility_v4(p_account_id text, p_facility_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, app
AS $function$
    SELECT app.account_can_access_facility_v6(p_account_id, p_facility_id);
$function$;

CREATE OR REPLACE FUNCTION app.account_can_access_equipment_v4(p_account_id text, p_equipment_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, app, catalog
AS $function$
    SELECT COALESCE((
        SELECT CASE
            WHEN eq.facility_id IS NULL
                THEN app.account_has_permission_v4(p_account_id, 'equipment.read_unassigned')
            ELSE app.account_can_access_facility_v6(p_account_id, eq.facility_id)
        END
          FROM catalog.equipment AS eq
         WHERE eq.id = p_equipment_id
    ), false);
$function$;

CREATE OR REPLACE FUNCTION app.account_can_access_employee_v6(p_actor_account_id text, p_employee_account_id text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, app
AS $function$
    WITH RECURSIVE target_department_tree AS (
        SELECT d.id, d.parent_department_id, d.is_active AS active_path,
               ARRAY[d.id]::uuid[] AS path
          FROM app.employees AS target_employee
          JOIN app.departments AS d ON d.id = target_employee.department_id
         WHERE target_employee.account_id = p_employee_account_id
        UNION ALL
        SELECT parent.id, parent.parent_department_id,
               child.active_path AND parent.is_active AS active_path,
               child.path || parent.id
          FROM target_department_tree AS child
          JOIN app.departments AS parent ON parent.id = child.parent_department_id
         WHERE NOT parent.id = ANY(child.path)
    )
    SELECT CASE
        WHEN p_actor_account_id IS NULL OR p_employee_account_id IS NULL THEN false
        WHEN app.account_has_permission_v4(p_actor_account_id, 'employees.read') THEN true
        WHEN p_actor_account_id = p_employee_account_id
             AND app.account_has_permission_v4(p_actor_account_id, 'employees.read_own') THEN true
        WHEN app.account_has_permission_v4(p_actor_account_id, 'employees.read_assigned')
             AND EXISTS (
                 SELECT 1
                   FROM target_department_tree AS department_scope
                   JOIN app.department_facilities AS df
                     ON df.department_id = department_scope.id
                   JOIN app.facilities AS f
                     ON f.id = df.facility_id AND f.is_active = true
                  WHERE department_scope.active_path = true
                    AND app.account_can_access_facility_v6(p_actor_account_id, df.facility_id)
             ) THEN true
        ELSE false
    END;
$function$;

CREATE OR REPLACE FUNCTION app.account_can_access_employee_v4(p_actor_account_id text, p_employee_account_id text)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, app
AS $function$
    SELECT app.account_can_access_employee_v6(p_actor_account_id, p_employee_account_id);
$function$;

COMMENT ON FUNCTION app.account_can_access_facility_v6(text, uuid) IS 'Facility scope check. A parent grant is inherited only when include_descendants=true; inactive nodes block inherited access.';
COMMENT ON FUNCTION app.account_can_access_employee_v6(text, text) IS 'Employee directory scope follows the target department and its active parent departments, then checks facility access.';
COMMENT ON FUNCTION app.account_can_access_equipment_v4(text, uuid) IS 'Compatibility helper: requires confirmed facility scope, or equipment.read_unassigned for equipment without a facility.';

INSERT INTO app.schema_version(version) VALUES (6) ON CONFLICT (version) DO NOTHING;

COMMIT;
