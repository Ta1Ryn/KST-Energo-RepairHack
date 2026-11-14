-- BackendFork rollback for migration 003.
-- DESTRUCTIVE: this removes employee metadata, role assignments, sessions and audit history created after migration.
-- Export required data first. Run only if no later migration depends on these objects.

BEGIN;

DROP TRIGGER IF EXISTS trg_accounts_updated_at_v3 ON app.accounts;
DROP FUNCTION IF EXISTS app.touch_accounts_updated_at_v3();

DROP TABLE IF EXISTS app.sessions;
DROP TABLE IF EXISTS app.audit_log;
DROP TABLE IF EXISTS app.account_roles;
DROP TABLE IF EXISTS app.role_permissions;
DROP TABLE IF EXISTS app.permissions;
DROP TABLE IF EXISTS app.roles;

ALTER TABLE app.accounts
    DROP CONSTRAINT IF EXISTS accounts_created_by_fkey,
    DROP CONSTRAINT IF EXISTS accounts_failed_pin_attempts_check,
    DROP CONSTRAINT IF EXISTS accounts_pin_hash_argon2id_check,
    DROP CONSTRAINT IF EXISTS accounts_full_name_check,
    DROP CONSTRAINT IF EXISTS accounts_employee_code_check;

DROP INDEX IF EXISTS app.accounts_employee_code_ci_uidx;
DROP INDEX IF EXISTS app.accounts_active_idx;
DROP INDEX IF EXISTS app.accounts_locked_until_idx;

ALTER TABLE app.accounts
    DROP COLUMN IF EXISTS created_by,
    DROP COLUMN IF EXISTS last_login_at,
    DROP COLUMN IF EXISTS updated_at,
    DROP COLUMN IF EXISTS created_at,
    DROP COLUMN IF EXISTS pin_changed_at,
    DROP COLUMN IF EXISTS locked_until,
    DROP COLUMN IF EXISTS failed_pin_attempts,
    DROP COLUMN IF EXISTS must_change_pin,
    DROP COLUMN IF EXISTS is_active,
    DROP COLUMN IF EXISTS pin_hash,
    DROP COLUMN IF EXISTS full_name,
    DROP COLUMN IF EXISTS employee_code;

DELETE FROM app.schema_version WHERE version = 3;

COMMIT;
