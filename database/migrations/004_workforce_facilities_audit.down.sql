-- DESTRUCTIVE rollback for migration 004.
-- Export employee profiles, facility assignments and audit records before running.
-- This removes v4-created data and fields. It does not roll back migration 003.

BEGIN;

DROP VIEW IF EXISTS app.v_employee_accounts_without_profile;
DROP VIEW IF EXISTS app.v_employee_profiles_incomplete;
DROP VIEW IF EXISTS reports.v_employee_maintenance_activity;
DROP VIEW IF EXISTS reports.v_facility_defect_summary;
DROP VIEW IF EXISTS reports.v_facility_inspection_activity;
DROP VIEW IF EXISTS reports.v_employee_inspection_activity;

DROP FUNCTION IF EXISTS app.account_can_access_equipment_v4(text, uuid);
DROP FUNCTION IF EXISTS app.account_can_access_employee_v4(text, text);
DROP FUNCTION IF EXISTS app.account_can_access_facility_v4(text, uuid);
DROP FUNCTION IF EXISTS app.account_has_permission_v4(text, text);

DROP TRIGGER IF EXISTS trg_inspection_findings_no_delete_v4 ON reports.inspection_findings;
DROP TRIGGER IF EXISTS trg_maintenance_actions_no_delete_v4 ON reports.maintenance_actions;
DROP FUNCTION IF EXISTS reports.prevent_work_record_hard_delete_v4();
DROP TRIGGER IF EXISTS trg_external_identity_no_delete_v4 ON app.external_identities;
DROP FUNCTION IF EXISTS app.prevent_external_identity_hard_delete_v4();
DROP TRIGGER IF EXISTS trg_accounts_no_delete_v4 ON app.accounts;
DROP FUNCTION IF EXISTS app.prevent_account_hard_delete_v4();

DROP TRIGGER IF EXISTS trg_audit_log_no_truncate_v4 ON app.audit_log;
DROP TRIGGER IF EXISTS trg_audit_log_immutable_v4 ON app.audit_log;
DROP FUNCTION IF EXISTS app.prevent_audit_log_mutation_v4();
DROP FUNCTION IF EXISTS app.audit_json_has_sensitive_key_v4(jsonb);

DROP TRIGGER IF EXISTS trg_employee_status_sessions_v4 ON app.employees;
DROP FUNCTION IF EXISTS app.handle_employee_status_change_v4();
DROP TRIGGER IF EXISTS trg_accounts_revoke_sessions_v4 ON app.accounts;
DROP FUNCTION IF EXISTS app.revoke_sessions_for_disabled_account_v4();
DROP TRIGGER IF EXISTS trg_accounts_prepare_pin_rotation_v4 ON app.accounts;
DROP FUNCTION IF EXISTS app.prepare_account_pin_rotation_v4();
DROP TRIGGER IF EXISTS trg_employee_prepare_status_v4 ON app.employees;
DROP FUNCTION IF EXISTS app.prepare_employee_status_v4();
DROP TRIGGER IF EXISTS trg_employee_supervisor_no_cycle_v4 ON app.employees;
DROP FUNCTION IF EXISTS app.prevent_employee_supervisor_cycle_v4();
DROP TRIGGER IF EXISTS trg_department_no_cycle_v4 ON app.departments;
DROP FUNCTION IF EXISTS app.prevent_department_hierarchy_cycle_v4();
DROP TRIGGER IF EXISTS trg_departments_no_delete_v4 ON app.departments;
DROP TRIGGER IF EXISTS trg_facilities_no_delete_v4 ON app.facilities;
DROP FUNCTION IF EXISTS app.prevent_org_master_hard_delete_v4();
DROP TRIGGER IF EXISTS trg_employee_no_delete_v4 ON app.employees;
DROP FUNCTION IF EXISTS app.prevent_employee_hard_delete_v4();
DROP TRIGGER IF EXISTS trg_employee_sync_account_v4 ON app.employees;
DROP FUNCTION IF EXISTS app.sync_employee_legacy_account_fields_v4();

DROP TRIGGER IF EXISTS trg_departments_updated_at_v4 ON app.departments;
DROP TRIGGER IF EXISTS trg_facilities_updated_at_v4 ON app.facilities;
DROP TRIGGER IF EXISTS trg_employees_updated_at_v4 ON app.employees;
DROP TRIGGER IF EXISTS trg_account_facility_access_updated_at_v4 ON app.account_facility_access;
DROP TRIGGER IF EXISTS trg_inspection_findings_updated_at_v4 ON reports.inspection_findings;
DROP TRIGGER IF EXISTS trg_maintenance_actions_updated_at_v4 ON reports.maintenance_actions;
DROP FUNCTION IF EXISTS app.touch_updated_at_v4();

DROP VIEW IF EXISTS app.v_employee_profiles_incomplete;

ALTER TABLE app.audit_log
    DROP CONSTRAINT IF EXISTS audit_outcome_check_v4,
    DROP CONSTRAINT IF EXISTS audit_source_check_v4,
    DROP CONSTRAINT IF EXISTS audit_before_data_object_check_v4,
    DROP CONSTRAINT IF EXISTS audit_after_data_object_check_v4,
    DROP CONSTRAINT IF EXISTS audit_no_secret_keys_check_v4;
ALTER TABLE app.audit_log
    DROP COLUMN IF EXISTS facility_id,
    DROP COLUMN IF EXISTS department_id,
    DROP COLUMN IF EXISTS outcome,
    DROP COLUMN IF EXISTS event_source,
    DROP COLUMN IF EXISTS reason,
    DROP COLUMN IF EXISTS failure_code,
    DROP COLUMN IF EXISTS before_data,
    DROP COLUMN IF EXISTS after_data;
DROP INDEX IF EXISTS app.audit_log_facility_idx;
DROP INDEX IF EXISTS app.audit_log_action_outcome_idx;

ALTER TABLE app.sessions
    DROP CONSTRAINT IF EXISTS sessions_auth_method_check_v4,
    DROP CONSTRAINT IF EXISTS sessions_revoke_reason_check_v4;
ALTER TABLE app.sessions
    DROP COLUMN IF EXISTS device_label,
    DROP COLUMN IF EXISTS auth_method,
    DROP COLUMN IF EXISTS revoked_by,
    DROP COLUMN IF EXISTS revoke_reason;
DROP INDEX IF EXISTS app.sessions_device_idx;

ALTER TABLE app.account_roles
    DROP CONSTRAINT IF EXISTS account_roles_validity_check_v4,
    DROP CONSTRAINT IF EXISTS account_roles_reason_check_v4;
ALTER TABLE app.account_roles
    DROP COLUMN IF EXISTS valid_from,
    DROP COLUMN IF EXISTS valid_until,
    DROP COLUMN IF EXISTS grant_reason;

ALTER TABLE catalog.equipment
    DROP CONSTRAINT IF EXISTS equipment_facility_id_fkey,
    DROP COLUMN IF EXISTS facility_id;
DROP INDEX IF EXISTS catalog.equipment_facility_idx;

DROP TABLE IF EXISTS app.external_identities;
DROP TABLE IF EXISTS reports.maintenance_actions;
DROP TABLE IF EXISTS reports.inspection_findings;
DROP INDEX IF EXISTS reports.inspection_items_finding_fk_uidx;
DROP TABLE IF EXISTS app.account_facility_access;
DROP TABLE IF EXISTS app.department_facilities;
DROP TABLE IF EXISTS app.employees;
DROP TABLE IF EXISTS app.facilities;
DROP TABLE IF EXISTS app.departments;

ALTER TABLE app.accounts DROP CONSTRAINT IF EXISTS accounts_account_type_check_v4;
DROP INDEX IF EXISTS app.accounts_type_active_idx;
ALTER TABLE app.accounts DROP COLUMN IF EXISTS account_type;

-- v4 permission catalogue rows/grants are intentionally retained on rollback to avoid deleting
-- permission keys that may have been customized or referenced by operational role configuration.

DELETE FROM app.schema_version WHERE version = 4;

COMMIT;
