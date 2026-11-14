-- BackendFork: employee accounts, PIN credential metadata, RBAC, sessions and audit log.
-- Migration 003; PostgreSQL 16+; apply to a database created from schema.sql v2.
-- Safe to re-run: additive DDL, conditional constraints/indexes, idempotent reference-data inserts.
-- IMPORTANT: this migration does not create real employee accounts or PINs.

BEGIN;

LOCK TABLE app.accounts IN SHARE ROW EXCLUSIVE MODE;

-- Preserve the existing app.accounts(id, payload) contract and existing inspection FKs.
ALTER TABLE app.accounts
    ADD COLUMN IF NOT EXISTS employee_code text,
    ADD COLUMN IF NOT EXISTS full_name text,
    ADD COLUMN IF NOT EXISTS pin_hash text,
    ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS must_change_pin boolean NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS failed_pin_attempts integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS locked_until timestamptz,
    ADD COLUMN IF NOT EXISTS pin_changed_at timestamptz,
    ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS last_login_at timestamptz,
    ADD COLUMN IF NOT EXISTS created_by text;

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'app.accounts'::regclass
          AND conname = 'accounts_employee_code_check'
    ) THEN
        ALTER TABLE app.accounts
            ADD CONSTRAINT accounts_employee_code_check
            CHECK (employee_code IS NULL OR length(btrim(employee_code)) BETWEEN 1 AND 64);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'app.accounts'::regclass
          AND conname = 'accounts_full_name_check'
    ) THEN
        ALTER TABLE app.accounts
            ADD CONSTRAINT accounts_full_name_check
            CHECK (full_name IS NULL OR length(btrim(full_name)) BETWEEN 1 AND 200);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'app.accounts'::regclass
          AND conname = 'accounts_pin_hash_argon2id_check'
    ) THEN
        ALTER TABLE app.accounts
            ADD CONSTRAINT accounts_pin_hash_argon2id_check
            CHECK (pin_hash IS NULL OR (pin_hash LIKE '$argon2id$%' AND length(pin_hash) <= 512));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'app.accounts'::regclass
          AND conname = 'accounts_failed_pin_attempts_check'
    ) THEN
        ALTER TABLE app.accounts
            ADD CONSTRAINT accounts_failed_pin_attempts_check
            CHECK (failed_pin_attempts >= 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'app.accounts'::regclass
          AND conname = 'accounts_created_by_fkey'
    ) THEN
        ALTER TABLE app.accounts
            ADD CONSTRAINT accounts_created_by_fkey
            FOREIGN KEY (created_by) REFERENCES app.accounts(id) ON DELETE SET NULL;
    END IF;
END
$migration$;

CREATE UNIQUE INDEX IF NOT EXISTS accounts_employee_code_ci_uidx
    ON app.accounts (lower(btrim(employee_code)))
    WHERE employee_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS accounts_active_idx
    ON app.accounts (created_at DESC) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS accounts_locked_until_idx
    ON app.accounts (locked_until) WHERE locked_until IS NOT NULL;

COMMENT ON COLUMN app.accounts.employee_code IS 'Уникальный табельный номер/код сотрудника; обязателен для новых сотрудников на уровне backend.';
COMMENT ON COLUMN app.accounts.pin_hash IS 'Только Argon2id PHC-хеш; исходный PIN никогда не хранится в БД.';
COMMENT ON COLUMN app.accounts.must_change_pin IS 'При true backend требует сменить временный PIN при первом входе.';
COMMENT ON COLUMN app.accounts.failed_pin_attempts IS 'Счётчик неверных попыток; обновлять атомарно в backend.';
COMMENT ON COLUMN app.accounts.locked_until IS 'Временная блокировка входа; backend обязан применять rate limit и проверять срок.';
COMMENT ON COLUMN app.accounts.created_by IS 'ID сотрудника, создавшего аккаунт; NULL допустим для первоначального администратора.';

-- Role catalog.
CREATE TABLE IF NOT EXISTS app.roles (
    role_key text PRIMARY KEY,
    name text NOT NULL,
    description text NOT NULL DEFAULT '',
    is_system boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT roles_key_check CHECK (role_key ~ '^[a-z][a-z0-9_]{1,49}$'),
    CONSTRAINT roles_name_check CHECK (length(btrim(name)) BETWEEN 1 AND 100)
);

CREATE TABLE IF NOT EXISTS app.permissions (
    permission_key text PRIMARY KEY,
    description text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT permissions_key_check CHECK (permission_key ~ '^[a-z][a-z0-9_.]{1,99}$')
);

CREATE TABLE IF NOT EXISTS app.role_permissions (
    role_key text NOT NULL REFERENCES app.roles(role_key) ON DELETE CASCADE,
    permission_key text NOT NULL REFERENCES app.permissions(permission_key) ON DELETE CASCADE,
    granted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (role_key, permission_key)
);

CREATE TABLE IF NOT EXISTS app.account_roles (
    account_id text NOT NULL REFERENCES app.accounts(id) ON DELETE CASCADE,
    role_key text NOT NULL REFERENCES app.roles(role_key) ON DELETE RESTRICT,
    is_primary boolean NOT NULL DEFAULT false,
    granted_by text REFERENCES app.accounts(id) ON DELETE SET NULL,
    granted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, role_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS account_roles_one_primary_uidx
    ON app.account_roles (account_id) WHERE is_primary = true;
CREATE INDEX IF NOT EXISTS account_roles_role_idx
    ON app.account_roles (role_key, account_id);

-- Revocable server-side sessions. Store only a SHA-256 hex digest of a random token, never the token itself.
-- If the backend chooses stateless JWTs, it may not use this table; agree on one session strategy before wiring auth.
CREATE TABLE IF NOT EXISTS app.sessions (
    id uuid PRIMARY KEY,
    account_id text NOT NULL REFERENCES app.accounts(id) ON DELETE CASCADE,
    token_hash text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    last_seen_at timestamptz,
    revoked_at timestamptz,
    ip_address inet,
    user_agent text,
    CONSTRAINT sessions_token_hash_check CHECK (length(token_hash) = 64 AND token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT sessions_expiry_check CHECK (expires_at > created_at)
);
CREATE INDEX IF NOT EXISTS sessions_account_active_idx
    ON app.sessions (account_id, expires_at DESC)
    WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS sessions_expiry_idx
    ON app.sessions (expires_at)
    WHERE revoked_at IS NULL;

-- Audit trail. Application code should INSERT only; do not put PINs, hashes, bearer tokens or secrets in metadata.
CREATE TABLE IF NOT EXISTS app.audit_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    actor_id text REFERENCES app.accounts(id) ON DELETE SET NULL,
    action_key text NOT NULL,
    target_account_id text REFERENCES app.accounts(id) ON DELETE SET NULL,
    resource_type text,
    resource_id text,
    request_id text,
    ip_address inet,
    user_agent text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT audit_action_key_check CHECK (length(btrim(action_key)) BETWEEN 1 AND 120),
    CONSTRAINT audit_metadata_object_check CHECK (jsonb_typeof(metadata) = 'object')
);
CREATE INDEX IF NOT EXISTS audit_log_occurred_idx
    ON app.audit_log (occurred_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_actor_idx
    ON app.audit_log (actor_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_target_idx
    ON app.audit_log (target_account_id, occurred_at DESC);

-- Keep updated_at accurate even if a backend update forgets to set it explicitly.
CREATE OR REPLACE FUNCTION app.touch_accounts_updated_at_v3()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$function$;

DROP TRIGGER IF EXISTS trg_accounts_updated_at_v3 ON app.accounts;
CREATE TRIGGER trg_accounts_updated_at_v3
    BEFORE UPDATE ON app.accounts
    FOR EACH ROW
    EXECUTE FUNCTION app.touch_accounts_updated_at_v3();

-- Built-in roles; ON CONFLICT DO NOTHING intentionally preserves any administrator-edited descriptions.
INSERT INTO app.roles (role_key, name, description, is_system) VALUES
    ('admin', 'Администратор', 'Управление сотрудниками, ролями и всеми данными системы.', true),
    ('engineer', 'Инженер', 'Работа с оборудованием и создание/ведение собственных осмотров.', true),
    ('supervisor', 'Руководитель', 'Просмотр оборудования, всех осмотров и отчётов.', true)
ON CONFLICT (role_key) DO NOTHING;

INSERT INTO app.permissions (permission_key, description) VALUES
    ('equipment.read', 'Просмотр оборудования.'),
    ('components.read', 'Просмотр узлов и комплектующих.'),
    ('inspections.create', 'Создание осмотров.'),
    ('inspections.read_own', 'Просмотр собственных осмотров.'),
    ('inspections.update_own', 'Изменение собственных осмотров в разрешённых статусах.'),
    ('inspections.read_all', 'Просмотр осмотров всех сотрудников.'),
    ('reports.read', 'Просмотр сводных отчётов.'),
    ('accounts.read', 'Просмотр учётных записей сотрудников.'),
    ('accounts.manage', 'Создание, изменение и деактивация сотрудников.'),
    ('accounts.reset_pin', 'Сброс и перевыпуск PIN сотрудника.'),
    ('roles.manage', 'Назначение ролей и управление разрешениями.'),
    ('audit.read', 'Просмотр журнала действий.')
ON CONFLICT (permission_key) DO NOTHING;

INSERT INTO app.role_permissions (role_key, permission_key)
SELECT 'admin', permission_key FROM app.permissions
ON CONFLICT DO NOTHING;

INSERT INTO app.role_permissions (role_key, permission_key) VALUES
    ('engineer', 'equipment.read'),
    ('engineer', 'components.read'),
    ('engineer', 'inspections.create'),
    ('engineer', 'inspections.read_own'),
    ('engineer', 'inspections.update_own'),
    ('supervisor', 'equipment.read'),
    ('supervisor', 'components.read'),
    ('supervisor', 'inspections.read_all'),
    ('supervisor', 'reports.read')
ON CONFLICT DO NOTHING;

INSERT INTO app.schema_version (version) VALUES (3)
ON CONFLICT (version) DO NOTHING;

COMMIT;
