-- BackendFork migration 004: normalized employee profiles, departments, facilities,
-- employee-to-facility scopes, equipment location, stronger audit and session lifecycle.
-- Requires schema v3 (schema.sql + migration 003). PostgreSQL 16+.
-- Additive migration. Does not create real employees, facilities, PINs or credentials.

BEGIN;

SELECT pg_advisory_xact_lock(74102852);

DO $preflight$
BEGIN
    IF to_regclass('app.schema_version') IS NULL
       OR NOT EXISTS (SELECT 1 FROM app.schema_version WHERE version = 3) THEN
        RAISE EXCEPTION 'Migration 004 requires app.schema_version version 3. Apply migration 003 first.';
    END IF;
    IF to_regclass('app.accounts') IS NULL
       OR to_regclass('app.roles') IS NULL
       OR to_regclass('app.permissions') IS NULL
       OR to_regclass('app.account_roles') IS NULL
       OR to_regclass('app.sessions') IS NULL
       OR to_regclass('app.audit_log') IS NULL THEN
        RAISE EXCEPTION 'Migration 004 preflight failed: required v3 tables are missing.';
    END IF;
END
$preflight$;

LOCK TABLE app.accounts IN SHARE ROW EXCLUSIVE MODE;

-- Distinguish human employee accounts from machine/service identities. Existing accounts default to employee
-- and must be reconciled with app.employees before they can receive employee permissions.
ALTER TABLE app.accounts
    ADD COLUMN IF NOT EXISTS account_type text NOT NULL DEFAULT 'employee';
DO $account_type_constraint$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='app.accounts'::regclass AND conname='accounts_account_type_check_v4') THEN
        ALTER TABLE app.accounts ADD CONSTRAINT accounts_account_type_check_v4
            CHECK (account_type IN ('employee', 'service', 'system')) NOT VALID;
    END IF;
END
$account_type_constraint$;
CREATE INDEX IF NOT EXISTS accounts_type_active_idx
    ON app.accounts (account_type, is_active);
COMMENT ON COLUMN app.accounts.account_type IS 'employee — требует app.employees профиль; service/system — технические identities без кадрового профиля.';

-- Optional federation mapping for a future corporate OIDC/SAML/AD gateway. Never store provider tokens here.
CREATE TABLE IF NOT EXISTS app.external_identities (
    id uuid PRIMARY KEY,
    account_id text NOT NULL REFERENCES app.accounts(id) ON DELETE RESTRICT,
    provider_key text NOT NULL,
    provider_subject text NOT NULL,
    provider_tenant text,
    email text,
    is_active boolean NOT NULL DEFAULT true,
    linked_at timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz,
    linked_by text REFERENCES app.accounts(id) ON DELETE SET NULL,
    CONSTRAINT external_identities_provider_check CHECK (provider_key ~ '^[a-z][a-z0-9_.-]{1,79}$'),
    CONSTRAINT external_identities_subject_check CHECK (length(btrim(provider_subject)) BETWEEN 1 AND 500),
    CONSTRAINT external_identities_tenant_check CHECK (provider_tenant IS NULL OR length(btrim(provider_tenant)) BETWEEN 1 AND 200)
);
CREATE UNIQUE INDEX IF NOT EXISTS external_identity_provider_subject_uidx
    ON app.external_identities (provider_key, COALESCE(provider_tenant, ''), provider_subject);
CREATE INDEX IF NOT EXISTS external_identity_account_idx
    ON app.external_identities (account_id) WHERE is_active = true;
CREATE OR REPLACE FUNCTION app.prevent_external_identity_hard_delete_v4()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'External identity mappings are historical; set is_active=false instead of deleting.'
        USING ERRCODE = '55000';
END;
$function$;
DROP TRIGGER IF EXISTS trg_external_identity_no_delete_v4 ON app.external_identities;
CREATE TRIGGER trg_external_identity_no_delete_v4 BEFORE DELETE ON app.external_identities
    FOR EACH ROW EXECUTE FUNCTION app.prevent_external_identity_hard_delete_v4();


CREATE OR REPLACE FUNCTION app.prevent_account_hard_delete_v4()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'Accounts are historical identities; deactivate the account instead of deleting it.'
        USING ERRCODE = '55000';
END;
$function$;
DROP TRIGGER IF EXISTS trg_accounts_no_delete_v4 ON app.accounts;
CREATE TRIGGER trg_accounts_no_delete_v4 BEFORE DELETE ON app.accounts
    FOR EACH ROW EXECUTE FUNCTION app.prevent_account_hard_delete_v4();

-- 1. Organizational structure.
CREATE TABLE IF NOT EXISTS app.departments (
    id uuid PRIMARY KEY,
    department_code text NOT NULL,
    name text NOT NULL,
    parent_department_id uuid REFERENCES app.departments(id) ON DELETE RESTRICT,
    description text,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    created_by text REFERENCES app.accounts(id) ON DELETE SET NULL,
    CONSTRAINT departments_code_check CHECK (length(btrim(department_code)) BETWEEN 1 AND 64),
    CONSTRAINT departments_name_check CHECK (length(btrim(name)) BETWEEN 1 AND 200),
    CONSTRAINT departments_no_self_parent_check CHECK (parent_department_id IS NULL OR parent_department_id <> id)
);
CREATE UNIQUE INDEX IF NOT EXISTS departments_code_ci_uidx
    ON app.departments (lower(btrim(department_code)));
CREATE INDEX IF NOT EXISTS departments_parent_idx
    ON app.departments (parent_department_id) WHERE parent_department_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS app.facilities (
    id uuid PRIMARY KEY,
    facility_code text NOT NULL,
    name text NOT NULL,
    facility_type text NOT NULL DEFAULT 'other',
    address text,
    time_zone text NOT NULL DEFAULT 'UTC',
    description text,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    created_by text REFERENCES app.accounts(id) ON DELETE SET NULL,
    CONSTRAINT facilities_code_check CHECK (length(btrim(facility_code)) BETWEEN 1 AND 64),
    CONSTRAINT facilities_name_check CHECK (length(btrim(name)) BETWEEN 1 AND 200),
    CONSTRAINT facilities_type_check CHECK (facility_type IN ('enterprise', 'plant', 'site', 'workshop', 'station', 'other')),
    CONSTRAINT facilities_timezone_check CHECK (length(btrim(time_zone)) BETWEEN 1 AND 100)
);
CREATE UNIQUE INDEX IF NOT EXISTS facilities_code_ci_uidx
    ON app.facilities (lower(btrim(facility_code)));
CREATE INDEX IF NOT EXISTS facilities_active_idx
    ON app.facilities (name) WHERE is_active = true;

CREATE TABLE IF NOT EXISTS app.department_facilities (
    department_id uuid NOT NULL REFERENCES app.departments(id) ON DELETE RESTRICT,
    facility_id uuid NOT NULL REFERENCES app.facilities(id) ON DELETE RESTRICT,
    assigned_at timestamptz NOT NULL DEFAULT now(),
    assigned_by text REFERENCES app.accounts(id) ON DELETE SET NULL,
    PRIMARY KEY (department_id, facility_id)
);
CREATE INDEX IF NOT EXISTS department_facilities_facility_idx
    ON app.department_facilities (facility_id, department_id);

-- 2. Employee profile is separated from the authentication account.
-- app.accounts.id remains the stable identity referenced by reports.inspections.author_id.
CREATE TABLE IF NOT EXISTS app.employees (
    account_id text PRIMARY KEY REFERENCES app.accounts(id) ON DELETE RESTRICT,
    employee_number text NOT NULL,
    full_name text,
    position_title text,
    department_id uuid REFERENCES app.departments(id) ON DELETE SET NULL,
    supervisor_account_id text REFERENCES app.employees(account_id) ON DELETE SET NULL,
    employment_status text NOT NULL DEFAULT 'active',
    hired_on date,
    terminated_on date,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT employees_number_check CHECK (length(btrim(employee_number)) BETWEEN 1 AND 64),
    CONSTRAINT employees_name_check CHECK (full_name IS NULL OR length(btrim(full_name)) BETWEEN 1 AND 200),
    CONSTRAINT employees_position_check CHECK (position_title IS NULL OR length(btrim(position_title)) BETWEEN 1 AND 150),
    CONSTRAINT employees_status_check CHECK (employment_status IN ('active', 'on_leave', 'suspended', 'terminated')),
    CONSTRAINT employees_terminated_date_check CHECK (employment_status <> 'terminated' OR terminated_on IS NOT NULL),
    CONSTRAINT employees_employment_dates_check CHECK (terminated_on IS NULL OR hired_on IS NULL OR terminated_on >= hired_on),
    CONSTRAINT employees_no_self_supervisor_check CHECK (supervisor_account_id IS NULL OR supervisor_account_id <> account_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS employees_number_ci_uidx
    ON app.employees (lower(btrim(employee_number)));
CREATE INDEX IF NOT EXISTS employees_department_idx
    ON app.employees (department_id) WHERE department_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS employees_supervisor_idx
    ON app.employees (supervisor_account_id) WHERE supervisor_account_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS employees_status_idx
    ON app.employees (employment_status);

-- Backfill only accounts that already have an employee code. Unknown personal/work data is not invented.
INSERT INTO app.employees (
    account_id, employee_number, full_name, employment_status, created_at, updated_at
)
SELECT
    a.id,
    a.employee_code,
    a.full_name,
    CASE WHEN a.is_active THEN 'active' ELSE 'suspended' END,
    a.created_at,
    a.updated_at
FROM app.accounts AS a
WHERE a.employee_code IS NOT NULL
  AND length(btrim(a.employee_code)) BETWEEN 1 AND 64
ON CONFLICT (account_id) DO NOTHING;

COMMENT ON TABLE app.employees IS 'Рабочий профиль сотрудника. app.accounts отвечает только за учётную запись и аутентификацию.';
COMMENT ON COLUMN app.accounts.employee_code IS 'LEGACY MIRROR: источник истины после v4 — app.employees.employee_number. Не изменять напрямую.';
COMMENT ON COLUMN app.accounts.full_name IS 'LEGACY MIRROR: источник истины после v4 — app.employees.full_name. Не изменять напрямую.';
COMMENT ON COLUMN app.employees.account_id IS 'Совпадает с app.accounts.id; сохраняет совместимость с reports.inspections.author_id.';
COMMENT ON COLUMN app.employees.full_name IS 'Nullable только для совместимости при backfill старых аккаунтов; новые профили должны иметь ФИО.';

CREATE OR REPLACE FUNCTION app.sync_employee_legacy_account_fields_v4()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    UPDATE app.accounts
       SET account_type = 'employee',
           employee_code = NEW.employee_number,
           full_name = NEW.full_name
     WHERE id = NEW.account_id;
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_employee_sync_account_v4 ON app.employees;
CREATE TRIGGER trg_employee_sync_account_v4
    AFTER INSERT OR UPDATE OF employee_number, full_name ON app.employees
    FOR EACH ROW EXECUTE FUNCTION app.sync_employee_legacy_account_fields_v4();

CREATE OR REPLACE FUNCTION app.prevent_employee_hard_delete_v4()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'Employee profiles cannot be hard-deleted; set employment_status=terminated.'
        USING ERRCODE = '55000';
END;
$function$;

DROP TRIGGER IF EXISTS trg_employee_no_delete_v4 ON app.employees;
CREATE TRIGGER trg_employee_no_delete_v4
    BEFORE DELETE ON app.employees
    FOR EACH ROW EXECUTE FUNCTION app.prevent_employee_hard_delete_v4();

-- Prevent circular reporting/department trees and hard deletion of master-data identities.
CREATE OR REPLACE FUNCTION app.prevent_department_hierarchy_cycle_v4()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    -- Serialize hierarchy edits so two concurrent updates cannot create a cycle.
    PERFORM pg_advisory_xact_lock(74102853);
    IF NEW.parent_department_id IS NOT NULL AND EXISTS (
        WITH RECURSIVE ancestor_ids(id) AS (
            SELECT NEW.parent_department_id
            UNION
            SELECT d.parent_department_id
              FROM app.departments AS d
              JOIN ancestor_ids AS a ON a.id = d.id
             WHERE d.parent_department_id IS NOT NULL
        )
        SELECT 1 FROM ancestor_ids WHERE id = NEW.id
    ) THEN
        RAISE EXCEPTION 'Department hierarchy cycle is not allowed.' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;
DROP TRIGGER IF EXISTS trg_department_no_cycle_v4 ON app.departments;
CREATE TRIGGER trg_department_no_cycle_v4
    BEFORE INSERT OR UPDATE OF parent_department_id ON app.departments
    FOR EACH ROW EXECUTE FUNCTION app.prevent_department_hierarchy_cycle_v4();

CREATE OR REPLACE FUNCTION app.prevent_employee_supervisor_cycle_v4()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    -- Serialize supervisor-chain edits for the same reason as department hierarchy edits.
    PERFORM pg_advisory_xact_lock(74102854);
    IF NEW.supervisor_account_id IS NOT NULL AND EXISTS (
        WITH RECURSIVE supervisor_chain(account_id, supervisor_account_id) AS (
            SELECT e.account_id, e.supervisor_account_id
              FROM app.employees AS e
             WHERE e.account_id = NEW.supervisor_account_id
            UNION
            SELECT parent.account_id, parent.supervisor_account_id
              FROM app.employees AS parent
              JOIN supervisor_chain AS chain ON parent.account_id = chain.supervisor_account_id
             WHERE chain.supervisor_account_id IS NOT NULL
        )
        SELECT 1 FROM supervisor_chain WHERE account_id = NEW.account_id
    ) THEN
        RAISE EXCEPTION 'Employee supervisor cycle is not allowed.' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$function$;
DROP TRIGGER IF EXISTS trg_employee_supervisor_no_cycle_v4 ON app.employees;
CREATE TRIGGER trg_employee_supervisor_no_cycle_v4
    BEFORE INSERT OR UPDATE OF supervisor_account_id ON app.employees
    FOR EACH ROW EXECUTE FUNCTION app.prevent_employee_supervisor_cycle_v4();

CREATE OR REPLACE FUNCTION app.prepare_employee_status_v4()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF NEW.employment_status = 'terminated' AND NEW.terminated_on IS NULL THEN
        NEW.terminated_on := current_date;
    END IF;
    RETURN NEW;
END;
$function$;
DROP TRIGGER IF EXISTS trg_employee_prepare_status_v4 ON app.employees;
CREATE TRIGGER trg_employee_prepare_status_v4
    BEFORE INSERT OR UPDATE OF employment_status ON app.employees
    FOR EACH ROW EXECUTE FUNCTION app.prepare_employee_status_v4();

CREATE OR REPLACE FUNCTION app.prevent_org_master_hard_delete_v4()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'Master data in % cannot be hard-deleted; set is_active=false.', TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$function$;
DROP TRIGGER IF EXISTS trg_departments_no_delete_v4 ON app.departments;
CREATE TRIGGER trg_departments_no_delete_v4 BEFORE DELETE ON app.departments
    FOR EACH ROW EXECUTE FUNCTION app.prevent_org_master_hard_delete_v4();
DROP TRIGGER IF EXISTS trg_facilities_no_delete_v4 ON app.facilities;
CREATE TRIGGER trg_facilities_no_delete_v4 BEFORE DELETE ON app.facilities
    FOR EACH ROW EXECUTE FUNCTION app.prevent_org_master_hard_delete_v4();

-- 3. Facility access is independent from role permission. A user needs BOTH a permission
-- and access to the facility, unless they have facilities.access_all.
CREATE TABLE IF NOT EXISTS app.account_facility_access (
    account_id text NOT NULL REFERENCES app.accounts(id) ON DELETE RESTRICT,
    facility_id uuid NOT NULL REFERENCES app.facilities(id) ON DELETE RESTRICT,
    is_active boolean NOT NULL DEFAULT true,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_until timestamptz,
    granted_at timestamptz NOT NULL DEFAULT now(),
    granted_by text REFERENCES app.accounts(id) ON DELETE SET NULL,
    grant_reason text,
    revoked_at timestamptz,
    revoked_by text REFERENCES app.accounts(id) ON DELETE SET NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, facility_id),
    CONSTRAINT account_facility_access_dates_check CHECK (valid_until IS NULL OR valid_until > valid_from),
    CONSTRAINT account_facility_access_revocation_check CHECK (
        (is_active = true AND revoked_at IS NULL) OR (is_active = false AND revoked_at IS NOT NULL)
    ),
    CONSTRAINT account_facility_access_revoked_after_grant_check CHECK (
        revoked_at IS NULL OR revoked_at >= granted_at
    ),
    CONSTRAINT account_facility_access_reason_check CHECK (grant_reason IS NULL OR length(btrim(grant_reason)) BETWEEN 1 AND 500)
);
CREATE INDEX IF NOT EXISTS account_facility_access_facility_idx
    ON app.account_facility_access (facility_id, account_id) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS account_facility_access_expiry_idx
    ON app.account_facility_access (valid_until) WHERE is_active = true AND valid_until IS NOT NULL;
COMMENT ON TABLE app.account_facility_access IS 'Текущие назначения доступа к объектам; историю выдачи/отзыва фиксировать в app.audit_log.';

-- 4. Equipment belongs to one physical facility. Existing equipment remains unassigned (NULL)
-- until a verified location is provided; no location is inferred from free-text payload.
ALTER TABLE catalog.equipment
    ADD COLUMN IF NOT EXISTS facility_id uuid;
DO $facility_fk$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conrelid = 'catalog.equipment'::regclass
           AND conname = 'equipment_facility_id_fkey'
    ) THEN
        ALTER TABLE catalog.equipment
            ADD CONSTRAINT equipment_facility_id_fkey
            FOREIGN KEY (facility_id) REFERENCES app.facilities(id) ON DELETE RESTRICT;
    END IF;
END
$facility_fk$;
CREATE INDEX IF NOT EXISTS equipment_facility_idx
    ON catalog.equipment (facility_id) WHERE facility_id IS NOT NULL;
COMMENT ON COLUMN catalog.equipment.facility_id IS 'Физический объект размещения. NULL означает, что расположение ещё не подтверждено.';

-- 4a. Structured findings and maintenance actions provide an auditable link between
-- the engineer, the component, the inspection and the corrective work.
CREATE UNIQUE INDEX IF NOT EXISTS inspection_items_finding_fk_uidx
    ON reports.inspection_items (inspection_id, component_id, installation_id);

CREATE TABLE IF NOT EXISTS reports.inspection_findings (
    id uuid PRIMARY KEY,
    inspection_id uuid NOT NULL,
    component_id uuid NOT NULL,
    installation_id uuid NOT NULL,
    defect_type_code text NOT NULL REFERENCES knowledge.defect_types(code) ON DELETE RESTRICT,
    severity text NOT NULL DEFAULT 'medium',
    status text NOT NULL DEFAULT 'open',
    source text NOT NULL DEFAULT 'manual',
    title text NOT NULL,
    description text,
    detected_by_account_id text NOT NULL REFERENCES app.employees(account_id) ON DELETE RESTRICT,
    assigned_to_account_id text REFERENCES app.employees(account_id) ON DELETE RESTRICT,
    resolved_by_account_id text REFERENCES app.employees(account_id) ON DELETE RESTRICT,
    detected_at timestamptz NOT NULL DEFAULT now(),
    due_at timestamptz,
    resolved_at timestamptz,
    resolution_notes text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT inspection_findings_severity_check CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical')),
    CONSTRAINT inspection_findings_status_check CHECK (status IN ('open', 'assigned', 'in_progress', 'resolved', 'false_positive', 'accepted_risk')),
    CONSTRAINT inspection_findings_source_check CHECK (source IN ('manual', 'visual_ai', 'sensor', 'document', 'other')),
    CONSTRAINT inspection_findings_title_check CHECK (length(btrim(title)) BETWEEN 1 AND 200),
    CONSTRAINT inspection_findings_payload_object_check CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT inspection_findings_resolution_check CHECK (
        (status IN ('resolved', 'false_positive', 'accepted_risk') AND resolved_at IS NOT NULL AND resolved_by_account_id IS NOT NULL)
        OR (status NOT IN ('resolved', 'false_positive', 'accepted_risk') AND resolved_at IS NULL AND resolved_by_account_id IS NULL)
    ),
    FOREIGN KEY (inspection_id, component_id, installation_id)
        REFERENCES reports.inspection_items (inspection_id, component_id, installation_id) ON DELETE RESTRICT,
    FOREIGN KEY (installation_id, component_id)
        REFERENCES catalog.installations (id, component_id) ON DELETE RESTRICT,
    UNIQUE (id, inspection_id, component_id, installation_id)
);
CREATE INDEX IF NOT EXISTS inspection_findings_status_idx
    ON reports.inspection_findings (status, severity, due_at);
CREATE INDEX IF NOT EXISTS inspection_findings_component_idx
    ON reports.inspection_findings (component_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS inspection_findings_assignee_idx
    ON reports.inspection_findings (assigned_to_account_id, status) WHERE assigned_to_account_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS inspection_findings_inspection_idx
    ON reports.inspection_findings (inspection_id, component_id);

CREATE TABLE IF NOT EXISTS reports.maintenance_actions (
    id uuid PRIMARY KEY,
    equipment_id uuid NOT NULL REFERENCES catalog.equipment(id) ON DELETE RESTRICT,
    component_id uuid NOT NULL,
    installation_id uuid NOT NULL,
    inspection_id uuid,
    finding_id uuid,
    action_type text NOT NULL,
    status text NOT NULL DEFAULT 'planned',
    title text NOT NULL,
    description text,
    created_by_account_id text NOT NULL REFERENCES app.accounts(id) ON DELETE RESTRICT,
    assigned_to_account_id text REFERENCES app.employees(account_id) ON DELETE RESTRICT,
    performed_by_account_id text REFERENCES app.employees(account_id) ON DELETE RESTRICT,
    verified_by_account_id text REFERENCES app.employees(account_id) ON DELETE RESTRICT,
    planned_at timestamptz,
    started_at timestamptz,
    completed_at timestamptz,
    verified_at timestamptz,
    new_installation_id uuid,
    result_notes text,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT maintenance_actions_type_check CHECK (action_type IN ('maintenance', 'repair', 'replacement', 'calibration', 'lubrication', 'inspection_followup', 'other')),
    CONSTRAINT maintenance_actions_status_check CHECK (status IN ('planned', 'in_progress', 'completed', 'cancelled')),
    CONSTRAINT maintenance_actions_title_check CHECK (length(btrim(title)) BETWEEN 1 AND 200),
    CONSTRAINT maintenance_actions_payload_object_check CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT maintenance_actions_completed_check CHECK (status <> 'completed' OR (completed_at IS NOT NULL AND performed_by_account_id IS NOT NULL)),
    CONSTRAINT maintenance_actions_finding_requires_inspection_check CHECK (finding_id IS NULL OR inspection_id IS NOT NULL),
    CONSTRAINT maintenance_actions_verified_check CHECK ((verified_at IS NULL AND verified_by_account_id IS NULL) OR (verified_at IS NOT NULL AND verified_by_account_id IS NOT NULL)),
    CONSTRAINT maintenance_actions_replacement_check CHECK (status <> 'completed' OR action_type <> 'replacement' OR new_installation_id IS NOT NULL),
    CONSTRAINT maintenance_actions_new_installation_different_check CHECK (new_installation_id IS NULL OR new_installation_id <> installation_id),
    CONSTRAINT maintenance_actions_dates_check CHECK (
        (started_at IS NULL OR planned_at IS NULL OR started_at >= planned_at)
        AND (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at)
        AND (verified_at IS NULL OR completed_at IS NULL OR verified_at >= completed_at)
    ),
    FOREIGN KEY (component_id, equipment_id)
        REFERENCES catalog.components (id, equipment_id) ON DELETE RESTRICT,
    FOREIGN KEY (installation_id, component_id)
        REFERENCES catalog.installations (id, component_id) ON DELETE RESTRICT,
    FOREIGN KEY (inspection_id, component_id, installation_id)
        REFERENCES reports.inspection_items (inspection_id, component_id, installation_id) ON DELETE RESTRICT,
    FOREIGN KEY (finding_id, inspection_id, component_id, installation_id)
        REFERENCES reports.inspection_findings (id, inspection_id, component_id, installation_id) ON DELETE RESTRICT,
    FOREIGN KEY (new_installation_id, component_id)
        REFERENCES catalog.installations (id, component_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS maintenance_actions_equipment_idx
    ON reports.maintenance_actions (equipment_id, created_at DESC);
CREATE INDEX IF NOT EXISTS maintenance_actions_status_due_idx
    ON reports.maintenance_actions (status, planned_at);
CREATE INDEX IF NOT EXISTS maintenance_actions_assignee_idx
    ON reports.maintenance_actions (assigned_to_account_id, status) WHERE assigned_to_account_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS maintenance_actions_performer_idx
    ON reports.maintenance_actions (performed_by_account_id, completed_at DESC) WHERE performed_by_account_id IS NOT NULL;

CREATE OR REPLACE FUNCTION reports.prevent_work_record_hard_delete_v4()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'Inspection findings and maintenance actions are historical records; change their status instead of deleting.'
        USING ERRCODE = '55000';
END;
$function$;
DROP TRIGGER IF EXISTS trg_inspection_findings_no_delete_v4 ON reports.inspection_findings;
CREATE TRIGGER trg_inspection_findings_no_delete_v4 BEFORE DELETE ON reports.inspection_findings
    FOR EACH ROW EXECUTE FUNCTION reports.prevent_work_record_hard_delete_v4();
DROP TRIGGER IF EXISTS trg_maintenance_actions_no_delete_v4 ON reports.maintenance_actions;
CREATE TRIGGER trg_maintenance_actions_no_delete_v4 BEFORE DELETE ON reports.maintenance_actions
    FOR EACH ROW EXECUTE FUNCTION reports.prevent_work_record_hard_delete_v4();

-- 5. Add explicit audit context and safe change snapshots.
ALTER TABLE app.audit_log
    ADD COLUMN IF NOT EXISTS facility_id uuid REFERENCES app.facilities(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS department_id uuid REFERENCES app.departments(id) ON DELETE RESTRICT,
    ADD COLUMN IF NOT EXISTS outcome text NOT NULL DEFAULT 'success',
    ADD COLUMN IF NOT EXISTS event_source text NOT NULL DEFAULT 'api',
    ADD COLUMN IF NOT EXISTS reason text,
    ADD COLUMN IF NOT EXISTS failure_code text,
    ADD COLUMN IF NOT EXISTS before_data jsonb,
    ADD COLUMN IF NOT EXISTS after_data jsonb;

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
             WHERE lower(item.key) = ANY (ARRAY[
                 'pin', 'pin_hash', 'password', 'password_hash', 'token', 'access_token',
                 'refresh_token', 'authorization', 'cookie', 'secret', 'api_key', 'credential',
                 'totp_secret', 'recovery_code'
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

DO $audit_constraints$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='app.audit_log'::regclass AND conname='audit_outcome_check_v4') THEN
        ALTER TABLE app.audit_log ADD CONSTRAINT audit_outcome_check_v4
            CHECK (outcome IN ('success', 'failure', 'denied')) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='app.audit_log'::regclass AND conname='audit_source_check_v4') THEN
        ALTER TABLE app.audit_log ADD CONSTRAINT audit_source_check_v4
            CHECK (event_source IN ('api', 'admin_ui', 'mobile', 'system', 'cli', 'migration')) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='app.audit_log'::regclass AND conname='audit_before_data_object_check_v4') THEN
        ALTER TABLE app.audit_log ADD CONSTRAINT audit_before_data_object_check_v4
            CHECK (before_data IS NULL OR jsonb_typeof(before_data) = 'object') NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='app.audit_log'::regclass AND conname='audit_after_data_object_check_v4') THEN
        ALTER TABLE app.audit_log ADD CONSTRAINT audit_after_data_object_check_v4
            CHECK (after_data IS NULL OR jsonb_typeof(after_data) = 'object') NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='app.audit_log'::regclass AND conname='audit_no_secret_keys_check_v4') THEN
        ALTER TABLE app.audit_log ADD CONSTRAINT audit_no_secret_keys_check_v4
            CHECK (
                NOT app.audit_json_has_sensitive_key_v4(metadata)
                AND (before_data IS NULL OR NOT app.audit_json_has_sensitive_key_v4(before_data))
                AND (after_data IS NULL OR NOT app.audit_json_has_sensitive_key_v4(after_data))
            ) NOT VALID;
    END IF;
END
$audit_constraints$;

CREATE INDEX IF NOT EXISTS audit_log_facility_idx
    ON app.audit_log (facility_id, occurred_at DESC) WHERE facility_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS audit_log_action_outcome_idx
    ON app.audit_log (action_key, outcome, occurred_at DESC);
COMMENT ON COLUMN app.audit_log.before_data IS 'Только разрешённые несекретные поля до изменения; не сохранять полные строки аккаунтов.';
COMMENT ON COLUMN app.audit_log.after_data IS 'Только разрешённые несекретные поля после изменения; не сохранять PIN/хеши/токены.';
COMMENT ON COLUMN app.audit_log.event_source IS 'api/admin_ui/mobile/system/cli/migration.';

CREATE OR REPLACE FUNCTION app.prevent_audit_log_mutation_v4()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    RAISE EXCEPTION 'app.audit_log is append-only; UPDATE, DELETE and TRUNCATE are forbidden.'
        USING ERRCODE = '55000';
END;
$function$;
DROP TRIGGER IF EXISTS trg_audit_log_immutable_v4 ON app.audit_log;
CREATE TRIGGER trg_audit_log_immutable_v4
    BEFORE UPDATE OR DELETE ON app.audit_log
    FOR EACH ROW EXECUTE FUNCTION app.prevent_audit_log_mutation_v4();
DROP TRIGGER IF EXISTS trg_audit_log_no_truncate_v4 ON app.audit_log;
CREATE TRIGGER trg_audit_log_no_truncate_v4
    BEFORE TRUNCATE ON app.audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION app.prevent_audit_log_mutation_v4();

-- 6. Session metadata and automatic revocation on account/employment disablement.
ALTER TABLE app.sessions
    ADD COLUMN IF NOT EXISTS device_label text,
    ADD COLUMN IF NOT EXISTS auth_method text NOT NULL DEFAULT 'pin',
    ADD COLUMN IF NOT EXISTS revoked_by text REFERENCES app.accounts(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS revoke_reason text;
DO $session_constraints$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='app.sessions'::regclass AND conname='sessions_auth_method_check_v4') THEN
        ALTER TABLE app.sessions ADD CONSTRAINT sessions_auth_method_check_v4
            CHECK (auth_method IN ('pin', 'pin_mfa', 'sso', 'sso_mfa', 'password')) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='app.sessions'::regclass AND conname='sessions_revoke_reason_check_v4') THEN
        ALTER TABLE app.sessions ADD CONSTRAINT sessions_revoke_reason_check_v4
            CHECK (revoke_reason IS NULL OR length(btrim(revoke_reason)) BETWEEN 1 AND 200) NOT VALID;
    END IF;
END
$session_constraints$;
CREATE INDEX IF NOT EXISTS sessions_device_idx
    ON app.sessions (account_id, device_label) WHERE revoked_at IS NULL;
COMMENT ON COLUMN app.sessions.token_hash IS 'Только SHA-256 hex от криптографически случайного opaque-токена; исходный токен хранится у клиента.';

CREATE OR REPLACE FUNCTION app.prepare_account_pin_rotation_v4()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF OLD.pin_hash IS DISTINCT FROM NEW.pin_hash THEN
        NEW.pin_changed_at := now();
        NEW.failed_pin_attempts := 0;
        NEW.locked_until := NULL;
    END IF;
    RETURN NEW;
END;
$function$;
DROP TRIGGER IF EXISTS trg_accounts_prepare_pin_rotation_v4 ON app.accounts;
CREATE TRIGGER trg_accounts_prepare_pin_rotation_v4
    BEFORE UPDATE OF pin_hash ON app.accounts
    FOR EACH ROW EXECUTE FUNCTION app.prepare_account_pin_rotation_v4();

CREATE OR REPLACE FUNCTION app.revoke_sessions_for_disabled_account_v4()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF (OLD.is_active = true AND NEW.is_active = false)
       OR (NEW.locked_until IS NOT NULL AND NEW.locked_until > now()
           AND (OLD.locked_until IS NULL OR OLD.locked_until <= now()))
       OR (OLD.pin_hash IS DISTINCT FROM NEW.pin_hash AND OLD.pin_hash IS NOT NULL)
       OR (OLD.must_change_pin IS DISTINCT FROM NEW.must_change_pin AND NEW.must_change_pin = true) THEN
        UPDATE app.sessions
           SET revoked_at = COALESCE(revoked_at, clock_timestamp()),
               revoke_reason = COALESCE(revoke_reason, CASE
                   WHEN NOT NEW.is_active THEN 'account_deactivated'
                   WHEN NEW.locked_until IS NOT NULL AND NEW.locked_until > now() THEN 'account_locked'
                   WHEN NEW.must_change_pin = true THEN 'pin_change_required'
                   ELSE 'credential_rotated' END)
         WHERE account_id = NEW.id AND revoked_at IS NULL;
    END IF;
    RETURN NEW;
END;
$function$;
DROP TRIGGER IF EXISTS trg_accounts_revoke_sessions_v4 ON app.accounts;
CREATE TRIGGER trg_accounts_revoke_sessions_v4
    AFTER UPDATE OF is_active, locked_until, pin_hash, must_change_pin ON app.accounts
    FOR EACH ROW EXECUTE FUNCTION app.revoke_sessions_for_disabled_account_v4();

CREATE OR REPLACE FUNCTION app.handle_employee_status_change_v4()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF OLD.employment_status IS DISTINCT FROM NEW.employment_status
       AND NEW.employment_status <> 'active' THEN
        UPDATE app.sessions
           SET revoked_at = COALESCE(revoked_at, clock_timestamp()),
               revoke_reason = COALESCE(revoke_reason, 'employment_status_' || NEW.employment_status)
         WHERE account_id = NEW.account_id AND revoked_at IS NULL;
    END IF;

    IF OLD.employment_status IS DISTINCT FROM NEW.employment_status
       AND NEW.employment_status = 'terminated' THEN
        UPDATE app.accounts SET is_active = false WHERE id = NEW.account_id AND is_active = true;
    END IF;
    RETURN NEW;
END;
$function$;
DROP TRIGGER IF EXISTS trg_employee_status_sessions_v4 ON app.employees;
CREATE TRIGGER trg_employee_status_sessions_v4
    AFTER INSERT OR UPDATE OF employment_status ON app.employees
    FOR EACH ROW EXECUTE FUNCTION app.handle_employee_status_change_v4();

-- 7. Keep updated_at consistent for v4 mutable entities.
CREATE OR REPLACE FUNCTION app.touch_updated_at_v4()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_departments_updated_at_v4 ON app.departments;
CREATE TRIGGER trg_departments_updated_at_v4 BEFORE UPDATE ON app.departments
    FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at_v4();
DROP TRIGGER IF EXISTS trg_facilities_updated_at_v4 ON app.facilities;
CREATE TRIGGER trg_facilities_updated_at_v4 BEFORE UPDATE ON app.facilities
    FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at_v4();
DROP TRIGGER IF EXISTS trg_employees_updated_at_v4 ON app.employees;
CREATE TRIGGER trg_employees_updated_at_v4 BEFORE UPDATE ON app.employees
    FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at_v4();
DROP TRIGGER IF EXISTS trg_account_facility_access_updated_at_v4 ON app.account_facility_access;
CREATE TRIGGER trg_account_facility_access_updated_at_v4 BEFORE UPDATE ON app.account_facility_access
    FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at_v4();
DROP TRIGGER IF EXISTS trg_inspection_findings_updated_at_v4 ON reports.inspection_findings;
CREATE TRIGGER trg_inspection_findings_updated_at_v4 BEFORE UPDATE ON reports.inspection_findings
    FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at_v4();
DROP TRIGGER IF EXISTS trg_maintenance_actions_updated_at_v4 ON reports.maintenance_actions;
CREATE TRIGGER trg_maintenance_actions_updated_at_v4 BEFORE UPDATE ON reports.maintenance_actions
    FOR EACH ROW EXECUTE FUNCTION app.touch_updated_at_v4();

-- 8. Expand permission vocabulary. Existing custom permissions/role grants are preserved.
INSERT INTO app.permissions (permission_key, description) VALUES
    ('employees.read', 'Просмотр всех рабочих профилей сотрудников.'),
    ('employees.read_own', 'Просмотр собственного рабочего профиля.'),
    ('employees.read_assigned', 'Просмотр сотрудников подразделений, связанных с назначенными площадками.'),
    ('employees.manage', 'Создание и изменение рабочих профилей сотрудников.'),
    ('findings.create', 'Регистрация замечаний и признаков дефектов при осмотре.'),
    ('findings.read_own', 'Просмотр замечаний, зарегистрированных сотрудником.'),
    ('findings.read_all', 'Просмотр замечаний в пределах доступных площадок.'),
    ('findings.manage', 'Назначение, обработка и закрытие замечаний.'),
    ('maintenance.create', 'Создание задач по обслуживанию и ремонту.'),
    ('maintenance.read_own', 'Просмотр собственных работ по обслуживанию.'),
    ('maintenance.read_all', 'Просмотр работ по обслуживанию в доступных площадках.'),
    ('maintenance.manage', 'Назначение и управление работами по обслуживанию.'),
    ('departments.read', 'Просмотр организационной структуры.'),
    ('departments.manage', 'Создание и изменение подразделений.'),
    ('facilities.read_assigned', 'Просмотр объектов, к которым назначен доступ.'),
    ('facilities.access_all', 'Доступ ко всем объектам, включая объекты без индивидуального назначения.'),
    ('facilities.manage', 'Создание и изменение объектов.'),
    ('facilities.assign_accounts', 'Выдача и отзыв доступа сотрудников к объектам.'),
    ('equipment.assign_facility', 'Назначение оборудования объекту размещения.'),
    ('equipment.read_unassigned', 'Просмотр оборудования, для которого объект ещё не назначен.'),
    ('sessions.revoke', 'Принудительное завершение сессий сотрудников.'),
    ('audit.export', 'Экспорт журнала действий.')
ON CONFLICT (permission_key) DO NOTHING;

INSERT INTO app.role_permissions (role_key, permission_key)
SELECT 'admin', permission_key
  FROM app.permissions
 WHERE permission_key IN (
    'employees.read', 'employees.read_own', 'employees.read_assigned', 'employees.manage',
    'findings.create', 'findings.read_own', 'findings.read_all', 'findings.manage',
    'maintenance.create', 'maintenance.read_own', 'maintenance.read_all', 'maintenance.manage',
    'departments.read', 'departments.manage',
    'facilities.read_assigned', 'facilities.access_all', 'facilities.manage',
    'facilities.assign_accounts', 'equipment.assign_facility', 'equipment.read_unassigned',
    'sessions.revoke', 'audit.export'
 )
ON CONFLICT DO NOTHING;

INSERT INTO app.role_permissions (role_key, permission_key) VALUES
    ('engineer', 'facilities.read_assigned'),
    ('engineer', 'employees.read_own'),
    ('engineer', 'findings.create'),
    ('engineer', 'findings.read_own'),
    ('engineer', 'maintenance.create'),
    ('engineer', 'maintenance.read_own'),
    ('supervisor', 'facilities.read_assigned'),
    ('supervisor', 'employees.read_assigned'),
    ('supervisor', 'findings.read_all'),
    ('supervisor', 'findings.manage'),
    ('supervisor', 'maintenance.read_all'),
    ('supervisor', 'maintenance.manage')
ON CONFLICT DO NOTHING;

-- 9. Extend current role assignments with optional validity and rationale.
ALTER TABLE app.account_roles
    ADD COLUMN IF NOT EXISTS valid_from timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS valid_until timestamptz,
    ADD COLUMN IF NOT EXISTS grant_reason text;
DO $role_assignment_constraints$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='app.account_roles'::regclass AND conname='account_roles_validity_check_v4') THEN
        ALTER TABLE app.account_roles ADD CONSTRAINT account_roles_validity_check_v4
            CHECK (valid_until IS NULL OR valid_until > valid_from) NOT VALID;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='app.account_roles'::regclass AND conname='account_roles_reason_check_v4') THEN
        ALTER TABLE app.account_roles ADD CONSTRAINT account_roles_reason_check_v4
            CHECK (grant_reason IS NULL OR length(btrim(grant_reason)) BETWEEN 1 AND 500) NOT VALID;
    END IF;
END
$role_assignment_constraints$;

-- 10. Authorization helpers for backend use. These are checks, not a replacement for API auth.
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

COMMENT ON FUNCTION app.account_has_permission_v4(text, text) IS 'Проверяет активность учётной записи, статус сотрудника, блокировку и эффективное назначение разрешения.';
COMMENT ON FUNCTION app.account_can_access_facility_v4(text, uuid) IS 'Проверяет доступ к действующему объекту. Backend дополнительно проверяет требуемое permission.';
COMMENT ON FUNCTION app.account_can_access_equipment_v4(text, uuid) IS 'Проверяет scope доступа к объекту оборудования; NULL facility требует equipment.read_unassigned.';
COMMENT ON FUNCTION app.account_can_access_employee_v4(text, text) IS 'Проверяет просмотр сотрудника по глобальному, собственному или назначенному подразделению scope.';

-- 11. Admin reporting views; backend must still require employees.read/reports.read and facility scope.
CREATE OR REPLACE VIEW reports.v_employee_inspection_activity
WITH (security_invoker = true)
AS
SELECT
    e.account_id,
    e.employee_number,
    e.full_name,
    e.position_title,
    e.employment_status,
    d.id AS department_id,
    d.name AS department_name,
    eq.facility_id,
    f.name AS facility_name,
    a.is_active AS account_is_active,
    count(i.id) AS inspections_total,
    count(i.id) FILTER (WHERE i.performed_at >= now() - interval '30 days') AS inspections_last_30_days,
    max(i.performed_at) AS last_inspection_at
FROM app.employees AS e
JOIN app.accounts AS a ON a.id = e.account_id
LEFT JOIN app.departments AS d ON d.id = e.department_id
LEFT JOIN reports.inspections AS i ON i.author_id = e.account_id
LEFT JOIN catalog.equipment AS eq ON eq.id = i.equipment_id
LEFT JOIN app.facilities AS f ON f.id = eq.facility_id
GROUP BY e.account_id, e.employee_number, e.full_name, e.position_title,
         e.employment_status, d.id, d.name, eq.facility_id, f.name, a.is_active;

CREATE OR REPLACE VIEW reports.v_facility_inspection_activity
WITH (security_invoker = true)
AS
SELECT
    f.id AS facility_id,
    f.facility_code,
    f.name AS facility_name,
    count(DISTINCT eq.id) AS equipment_count,
    count(DISTINCT i.id) AS inspections_total,
    count(DISTINCT i.id) FILTER (WHERE i.performed_at >= now() - interval '30 days') AS inspections_last_30_days,
    max(i.performed_at) AS last_inspection_at
FROM app.facilities AS f
LEFT JOIN catalog.equipment AS eq ON eq.facility_id = f.id
LEFT JOIN reports.inspections AS i ON i.equipment_id = eq.id
GROUP BY f.id, f.facility_code, f.name;

CREATE OR REPLACE VIEW reports.v_facility_defect_summary
WITH (security_invoker = true)
AS
SELECT
    eq.facility_id,
    f.name AS facility_name,
    finding.defect_type_code,
    dt.name AS defect_type_name,
    finding.severity,
    finding.status,
    count(*) AS findings_count
FROM reports.inspection_findings AS finding
JOIN reports.inspections AS inspection ON inspection.id = finding.inspection_id
JOIN catalog.equipment AS eq ON eq.id = inspection.equipment_id
LEFT JOIN app.facilities AS f ON f.id = eq.facility_id
JOIN knowledge.defect_types AS dt ON dt.code = finding.defect_type_code
GROUP BY eq.facility_id, f.name, finding.defect_type_code, dt.name, finding.severity, finding.status;

CREATE OR REPLACE VIEW reports.v_employee_maintenance_activity
WITH (security_invoker = true)
AS
SELECT
    action.performed_by_account_id AS account_id,
    employee.employee_number,
    employee.full_name,
    eq.facility_id,
    f.name AS facility_name,
    count(*) FILTER (WHERE action.performed_by_account_id IS NOT NULL) AS actions_performed,
    count(*) FILTER (WHERE action.status = 'completed') AS actions_completed,
    max(action.completed_at) AS last_completed_at
FROM reports.maintenance_actions AS action
JOIN app.employees AS employee ON employee.account_id = action.performed_by_account_id
JOIN catalog.equipment AS eq ON eq.id = action.equipment_id
LEFT JOIN app.facilities AS f ON f.id = eq.facility_id
GROUP BY action.performed_by_account_id, employee.employee_number, employee.full_name, eq.facility_id, f.name;

CREATE OR REPLACE VIEW app.v_employee_profiles_incomplete
WITH (security_invoker = true)
AS
SELECT e.account_id, e.employee_number,
       (e.full_name IS NULL OR length(btrim(e.full_name)) = 0) AS missing_full_name,
       (e.department_id IS NULL) AS missing_department,
       (e.position_title IS NULL OR length(btrim(e.position_title)) = 0) AS missing_position
  FROM app.employees AS e
 WHERE e.full_name IS NULL
    OR length(btrim(e.full_name)) = 0
    OR e.department_id IS NULL
    OR e.position_title IS NULL
    OR length(btrim(e.position_title)) = 0;

CREATE OR REPLACE VIEW app.v_employee_accounts_without_profile
WITH (security_invoker = true)
AS
SELECT a.id AS account_id, a.employee_code, a.full_name, a.is_active, a.created_at
  FROM app.accounts AS a
 WHERE a.account_type = 'employee'
   AND NOT EXISTS (SELECT 1 FROM app.employees AS e WHERE e.account_id = a.id);

COMMENT ON VIEW reports.v_employee_inspection_activity IS 'Агрегаты осмотров по сотрудникам; доступ контролируется backend-разрешениями и объектным scope.';
COMMENT ON VIEW reports.v_facility_inspection_activity IS 'Агрегаты оборудования и осмотров по объектам; не включает незакреплённое оборудование.';
COMMENT ON VIEW reports.v_facility_defect_summary IS 'Число замечаний по типу, серьёзности и статусу на площадке.';
COMMENT ON VIEW reports.v_employee_maintenance_activity IS 'Число выполненных работ по сотруднику и площадке.';
COMMENT ON VIEW app.v_employee_profiles_incomplete IS 'Контроль полноты кадровых профилей после миграции; не содержит PIN и токены.';
COMMENT ON VIEW app.v_employee_accounts_without_profile IS 'Employee-аккаунты без кадровой карточки; требуется ручное сопоставление или смена account_type для технической учётной записи.';

INSERT INTO app.schema_version (version) VALUES (4)
ON CONFLICT (version) DO NOTHING;

COMMIT;
