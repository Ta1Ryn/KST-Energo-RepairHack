-- v7, PostgreSQL 16+. Run with ops/migrate.py, never replay old migrations.
-- Forward-only: new personnel IDs/access history cannot be removed losslessly.
BEGIN;
SELECT pg_advisory_xact_lock(74102852);
DO $preflight$
BEGIN
    IF (SELECT array_agg(version ORDER BY version) FROM app.schema_version)
       IS DISTINCT FROM ARRAY[2,3,4,5,6] THEN
        RAISE EXCEPTION '007 requires exactly schema versions 2..6';
    END IF;
END $preflight$;
LOCK TABLE app.accounts, app.employees, app.account_facility_access,
    app.facilities, app.departments, catalog.equipment IN ACCESS EXCLUSIVE MODE;

-- Independent personnel identity; retain all account IDs and account-based work FKs.
ALTER TABLE app.employees ADD COLUMN id uuid NOT NULL DEFAULT gen_random_uuid();
CREATE TEMP TABLE employee_fk_rebuild ON COMMIT DROP AS
SELECT conrelid::regclass AS relation, conname, pg_get_constraintdef(oid) AS definition
FROM pg_constraint WHERE confrelid = 'app.employees'::regclass AND contype = 'f';
DO $keys$
DECLARE r record;
BEGIN
    FOR r IN SELECT * FROM employee_fk_rebuild LOOP
        EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.relation, r.conname);
    END LOOP;
    ALTER TABLE app.employees DROP CONSTRAINT employees_pkey;
    ALTER TABLE app.employees ALTER COLUMN account_id DROP NOT NULL;
    ALTER TABLE app.employees ADD PRIMARY KEY (id);
    ALTER TABLE app.employees ADD CONSTRAINT employees_account_id_key UNIQUE (account_id);
    FOR r IN SELECT * FROM employee_fk_rebuild LOOP
        EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I %s', r.relation, r.conname, r.definition);
    END LOOP;
END $keys$;
ALTER TABLE app.employees
    ADD COLUMN supervisor_id uuid REFERENCES app.employees(id) ON DELETE RESTRICT;
UPDATE app.employees e SET supervisor_id = s.id
FROM app.employees s WHERE s.account_id = e.supervisor_account_id;
ALTER TABLE app.employees ADD CONSTRAINT employees_no_self_supervisor_v7
    CHECK (supervisor_id IS NULL OR supervisor_id <> id);
CREATE INDEX employees_supervisor_id_idx ON app.employees(supervisor_id)
    WHERE supervisor_id IS NOT NULL;
CREATE INDEX inspections_author_idx ON reports.inspections(author_id, performed_at DESC);
DROP TRIGGER trg_employee_supervisor_no_cycle_v4 ON app.employees;
DROP TRIGGER trg_employee_account_relink_v6 ON app.employees;
DROP TRIGGER trg_employee_sync_account_v4 ON app.employees;
DROP TRIGGER trg_employee_prepare_status_v4 ON app.employees;

CREATE FUNCTION app.prepare_employee_v7() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, app AS $$
DECLARE s app.employees;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.id IS DISTINCT FROM OLD.id OR
           (OLD.account_id IS NOT NULL AND NEW.account_id IS DISTINCT FROM OLD.account_id) THEN
            RAISE EXCEPTION 'Personnel ID and an established account link are immutable' USING ERRCODE='55000';
        END IF;
    ELSE
        IF NEW.full_name IS NULL THEN
            RAISE EXCEPTION 'New personnel require a supplied full_name' USING ERRCODE='23514';
        END IF;
    END IF;
    IF NEW.account_id IS NOT NULL THEN
        PERFORM 1 FROM app.accounts WHERE id=NEW.account_id AND account_type='employee' FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'An employee can link only to an existing employee account' USING ERRCODE='23514';
        END IF;
    END IF;
    -- Accept either supervisor spelling, but keep one canonical personnel link.
    IF TG_OP='UPDATE' THEN
        IF NEW.supervisor_account_id IS DISTINCT FROM OLD.supervisor_account_id
           AND NEW.supervisor_id IS NOT DISTINCT FROM OLD.supervisor_id THEN
            SELECT id INTO NEW.supervisor_id FROM app.employees WHERE account_id=NEW.supervisor_account_id;
            IF NEW.supervisor_account_id IS NOT NULL AND NEW.supervisor_id IS NULL THEN
                RAISE EXCEPTION 'Supervisor account has no employee' USING ERRCODE='23503';
            END IF;
        END IF;
    ELSIF NEW.supervisor_id IS NULL AND NEW.supervisor_account_id IS NOT NULL THEN
        SELECT id INTO NEW.supervisor_id FROM app.employees WHERE account_id=NEW.supervisor_account_id;
        IF NEW.supervisor_id IS NULL THEN
            RAISE EXCEPTION 'Supervisor account has no employee' USING ERRCODE='23503';
        END IF;
    END IF;
    IF NEW.supervisor_id IS NOT NULL THEN
        SELECT * INTO s FROM app.employees WHERE id=NEW.supervisor_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Supervisor employee does not exist' USING ERRCODE='23503';
        END IF;
        IF NEW.supervisor_account_id IS NOT NULL AND NEW.supervisor_account_id IS DISTINCT FROM s.account_id
           AND (TG_OP='INSERT' OR NEW.supervisor_account_id IS DISTINCT FROM OLD.supervisor_account_id) THEN
            RAISE EXCEPTION 'Conflicting supervisor identities' USING ERRCODE='23514';
        END IF;
        NEW.supervisor_account_id := s.account_id;
    ELSE
        NEW.supervisor_account_id := NULL;
    END IF;
    -- Do not infer employment dates from today's migration/status-change date.
    IF NEW.employment_status='terminated' AND NEW.terminated_on IS NULL THEN
        RAISE EXCEPTION 'Supply the actual termination date' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_employee_prepare_v7 BEFORE INSERT OR UPDATE ON app.employees
FOR EACH ROW EXECUTE FUNCTION app.prepare_employee_v7();

CREATE OR REPLACE FUNCTION app.sync_employee_legacy_account_fields_v4() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,app AS $$
BEGIN
    UPDATE app.accounts SET employee_code=NEW.employee_number, full_name=NEW.full_name
    WHERE id=NEW.account_id;
    -- If a previously offline manager gets an account, update compatibility mirrors.
    UPDATE app.employees SET supervisor_account_id=NEW.account_id
    WHERE supervisor_id=NEW.id AND supervisor_account_id IS DISTINCT FROM NEW.account_id;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_employee_sync_account_v4 AFTER INSERT OR UPDATE OF account_id,employee_number,full_name
ON app.employees FOR EACH ROW EXECUTE FUNCTION app.sync_employee_legacy_account_fields_v4();

CREATE OR REPLACE FUNCTION app.assert_employee_account_pair_v6() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,app AS $$
DECLARE aid text; kind text; profile boolean;
BEGIN
    IF TG_TABLE_NAME='accounts' THEN aid := NEW.id; ELSE aid := NEW.account_id; END IF;
    IF aid IS NULL THEN RETURN NULL; END IF;
    SELECT account_type INTO kind FROM app.accounts WHERE id=aid FOR UPDATE;
    SELECT EXISTS(SELECT 1 FROM app.employees WHERE account_id=aid) INTO profile;
    IF (kind='employee') IS DISTINCT FROM profile THEN
        RAISE EXCEPTION 'Account/personnel type mismatch' USING ERRCODE='23514';
    END IF;
    RETURN NULL;
END $$;

-- Serialize before the statement, then check the final state, not a BEFORE-row snapshot.
-- REPEATABLE READ is rejected for tree edits: a stale snapshot could miss another tree edit.
CREATE FUNCTION app.serialize_tree_v7() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,app AS $$
BEGIN
    IF current_setting('transaction_isolation') NOT IN ('read committed','serializable') THEN
        RAISE EXCEPTION 'Tree edits require READ COMMITTED or SERIALIZABLE' USING ERRCODE='25000';
    END IF;
    PERFORM pg_advisory_xact_lock(74102853);
    RETURN NULL;
END $$;
CREATE FUNCTION app.assert_tree_v7() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,app AS $$
DECLARE bad boolean; parent_column text;
BEGIN
    parent_column := TG_ARGV[0];
    EXECUTE format('WITH RECURSIVE edges(id,parent_id) AS (
        SELECT id,%1$I FROM app.%2$I WHERE id=$1 UNION
        SELECT p.id,p.%1$I FROM edges e JOIN app.%2$I p ON p.id=e.parent_id
    ) SELECT EXISTS(SELECT 1 FROM edges WHERE parent_id=$1)',parent_column,TG_TABLE_NAME)
    INTO bad USING NEW.id;
    IF bad THEN RAISE EXCEPTION 'Hierarchy cycle is forbidden' USING ERRCODE='23514'; END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER trg_facility_tree_lock_v7 BEFORE INSERT OR UPDATE ON app.facilities
FOR EACH STATEMENT EXECUTE FUNCTION app.serialize_tree_v7();
CREATE CONSTRAINT TRIGGER trg_facility_tree_final_v7 AFTER INSERT OR UPDATE ON app.facilities
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.assert_tree_v7('parent_facility_id');
CREATE TRIGGER trg_department_tree_lock_v7 BEFORE INSERT OR UPDATE ON app.departments
FOR EACH STATEMENT EXECUTE FUNCTION app.serialize_tree_v7();
CREATE CONSTRAINT TRIGGER trg_department_tree_final_v7 AFTER INSERT OR UPDATE ON app.departments
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.assert_tree_v7('parent_department_id');
CREATE TRIGGER trg_employee_tree_lock_v7 BEFORE INSERT OR UPDATE ON app.employees
FOR EACH STATEMENT EXECUTE FUNCTION app.serialize_tree_v7();
CREATE CONSTRAINT TRIGGER trg_employee_tree_final_v7 AFTER INSERT OR UPDATE ON app.employees
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION app.assert_tree_v7('supervisor_id');

CREATE FUNCTION app.protect_stable_id_v7() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id THEN
        RAISE EXCEPTION 'Identity ID is immutable' USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_account_stable_id_v7 BEFORE UPDATE OF id ON app.accounts
FOR EACH ROW EXECUTE FUNCTION app.protect_stable_id_v7();
CREATE TRIGGER trg_facility_stable_id_v7 BEFORE UPDATE OF id ON app.facilities
FOR EACH ROW EXECUTE FUNCTION app.protect_stable_id_v7();
CREATE TRIGGER trg_department_stable_id_v7 BEFORE UPDATE OF id ON app.departments
FOR EACH ROW EXECUTE FUNCTION app.protect_stable_id_v7();
CREATE TRIGGER trg_equipment_stable_id_v7 BEFORE UPDATE OF id ON catalog.equipment
FOR EACH ROW EXECUTE FUNCTION app.protect_stable_id_v7();

-- Extensible object types; enterprises/sites/workshops are nodes of the same tree.
CREATE TABLE app.facility_types (
    type_key text PRIMARY KEY CHECK(type_key ~ '^[a-z][a-z0-9_]{1,49}$'),
    name text NOT NULL CHECK(length(btrim(name)) BETWEEN 1 AND 200),
    is_active boolean NOT NULL DEFAULT true
);
INSERT INTO app.facility_types(type_key,name) VALUES
('enterprise','Предприятие'),('plant','Завод'),('site','Производственная площадка'),
('workshop','Цех'),('station','Станция'),('other','Другой объект');
ALTER TABLE app.facilities DROP CONSTRAINT facilities_type_check;
ALTER TABLE app.facilities ADD CONSTRAINT facilities_type_fkey
    FOREIGN KEY(facility_type) REFERENCES app.facility_types(type_key) ON DELETE RESTRICT;
ALTER TABLE catalog.equipment ADD COLUMN is_active boolean NOT NULL DEFAULT true;

CREATE FUNCTION app.facility_path_is_active_v7(p_facility_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,app AS $$
    WITH RECURSIVE path AS (
        SELECT id,parent_facility_id,is_active,ARRAY[id] AS seen FROM app.facilities WHERE id=p_facility_id
        UNION ALL
        SELECT f.id,f.parent_facility_id,f.is_active,p.seen||f.id
        FROM path p JOIN app.facilities f ON f.id=p.parent_facility_id WHERE NOT f.id=ANY(p.seen)
    ) SELECT COALESCE(bool_and(is_active),false) FROM path;
$$;

-- Evaluate validity at the start of each SQL statement, not the start of a
-- possibly long-running transaction. Authentication itself remains backend-owned.
CREATE OR REPLACE FUNCTION app.account_is_eligible_v6(p_account_id text) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,app AS $$
    SELECT EXISTS(SELECT 1 FROM app.accounts a WHERE a.id=p_account_id AND a.is_active
        AND NOT a.must_change_pin AND (a.locked_until IS NULL OR a.locked_until<=statement_timestamp())
        AND (a.account_type IN ('service','system') OR EXISTS(SELECT 1 FROM app.employees e
            WHERE e.account_id=a.id AND e.employment_status='active'))
        AND NOT EXISTS(SELECT 1 FROM app.employees e WHERE e.account_id=a.id AND e.employment_status<>'active'));
$$;
CREATE OR REPLACE FUNCTION app.account_has_permission_v4(p_account_id text,p_permission_key text) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,app AS $$
    SELECT app.account_is_eligible_v6(p_account_id) AND EXISTS(SELECT 1 FROM app.account_roles r
        JOIN app.role_permissions rp ON rp.role_key=r.role_key
        WHERE r.account_id=p_account_id AND rp.permission_key=p_permission_key AND r.revoked_at IS NULL
          AND r.valid_from<=statement_timestamp() AND (r.valid_until IS NULL OR statement_timestamp()<r.valid_until));
$$;

-- Human access belongs to the employee even when no login has yet been issued.
-- Technical identities retain their independent legacy account grants.
CREATE TABLE app.employee_object_access (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_id uuid NOT NULL REFERENCES app.employees(id) ON DELETE RESTRICT,
    facility_id uuid REFERENCES app.facilities(id) ON DELETE RESTRICT,
    equipment_id uuid REFERENCES catalog.equipment(id) ON DELETE RESTRICT,
    include_descendants boolean NOT NULL DEFAULT false,
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_until timestamptz,
    is_active boolean NOT NULL DEFAULT true,
    granted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    granted_by_employee_id uuid REFERENCES app.employees(id) ON DELETE RESTRICT,
    grant_reason text,
    revoked_at timestamptz,
    revoked_by_employee_id uuid REFERENCES app.employees(id) ON DELETE RESTRICT,
    revoke_reason text,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    legacy_account_id text,
    CHECK ((facility_id IS NOT NULL)::int + (equipment_id IS NOT NULL)::int = 1),
    CHECK (equipment_id IS NULL OR NOT include_descendants),
    CHECK (valid_until IS NULL OR valid_until>valid_from),
    CHECK ((is_active AND revoked_at IS NULL) OR (NOT is_active AND revoked_at IS NOT NULL)),
    CHECK (revoked_at IS NULL OR revoked_at>=granted_at),
    -- Legacy rationale may contain 1..2 characters; preserve it byte-for-byte.
    -- The write trigger enforces 3..500 for all new changes.
    CHECK (grant_reason IS NULL OR length(btrim(grant_reason)) BETWEEN 1 AND 500),
    CHECK (revoke_reason IS NULL OR length(btrim(revoke_reason)) BETWEEN 1 AND 500),
    FOREIGN KEY(legacy_account_id,facility_id)
        REFERENCES app.account_facility_access(account_id,facility_id) ON DELETE RESTRICT
);
CREATE UNIQUE INDEX employee_facility_access_uidx ON app.employee_object_access(employee_id,facility_id)
WHERE facility_id IS NOT NULL;
CREATE UNIQUE INDEX employee_equipment_access_uidx ON app.employee_object_access(employee_id,equipment_id)
WHERE equipment_id IS NOT NULL;
CREATE INDEX employee_object_access_facility_idx ON app.employee_object_access(facility_id,employee_id)
WHERE facility_id IS NOT NULL;
CREATE INDEX employee_object_access_equipment_idx ON app.employee_object_access(equipment_id,employee_id)
WHERE equipment_id IS NOT NULL;
CREATE INDEX employee_object_access_expiry_idx ON app.employee_object_access(valid_until)
WHERE is_active AND valid_until IS NOT NULL;
CREATE FUNCTION app.object_access_snapshot_v7(g app.employee_object_access) RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,app AS $$
    SELECT jsonb_build_object('id',g.id,'employee_id',g.employee_id,'facility_id',g.facility_id,
        'equipment_id',g.equipment_id,'include_descendants',g.include_descendants,
        'valid_from',g.valid_from,'valid_until',g.valid_until,'is_active',g.is_active,
        'granted_at',g.granted_at,'granted_by_employee_id',g.granted_by_employee_id,
        'grant_reason',g.grant_reason,'revoked_at',g.revoked_at,
        'revoked_by_employee_id',g.revoked_by_employee_id,'revoke_reason',g.revoke_reason,
        'legacy_account_id',g.legacy_account_id);
$$;
CREATE TABLE app.employee_object_access_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    assignment_id uuid NOT NULL REFERENCES app.employee_object_access(id) ON DELETE RESTRICT,
    event_type text NOT NULL CHECK(event_type IN ('baseline','granted','updated','revoked','regranted')),
    actor_employee_id uuid REFERENCES app.employees(id) ON DELETE RESTRICT,
    actor_account_id text REFERENCES app.accounts(id) ON DELETE RESTRICT,
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 500),
    request_id text,
    before_data jsonb CHECK(before_data IS NULL OR jsonb_typeof(before_data)='object'),
    after_data jsonb NOT NULL CHECK(jsonb_typeof(after_data)='object'),
    CHECK(event_type='baseline' OR (actor_employee_id IS NOT NULL AND actor_account_id IS NOT NULL))
);
CREATE INDEX employee_object_events_assignment_idx ON app.employee_object_access_events(assignment_id,occurred_at,id);
CREATE INDEX employee_object_events_actor_idx ON app.employee_object_access_events(actor_employee_id,occurred_at DESC);
CREATE UNIQUE INDEX employee_object_events_baseline_uidx ON app.employee_object_access_events(assignment_id)
WHERE event_type='baseline';

-- No fictitious grantor: unknown/service historical grantors remain in legacy history.
INSERT INTO app.employee_object_access(employee_id,facility_id,include_descendants,valid_from,valid_until,
    is_active,granted_at,granted_by_employee_id,grant_reason,revoked_at,revoked_by_employee_id,revoke_reason,
    updated_at,legacy_account_id)
SELECT e.id,g.facility_id,g.include_descendants,g.valid_from,g.valid_until,g.is_active,g.granted_at,
    actor.id,g.grant_reason,g.revoked_at,revoker.id,g.revoke_reason,g.updated_at,g.account_id
FROM app.account_facility_access g JOIN app.employees e ON e.account_id=g.account_id
LEFT JOIN app.employees actor ON actor.account_id=g.granted_by
LEFT JOIN app.employees revoker ON revoker.account_id=g.revoked_by;
INSERT INTO app.employee_object_access_events(assignment_id,event_type,reason,after_data)
SELECT g.id,'baseline','Imported current v6 assignment; prior events retained in account_facility_access_events',
    app.object_access_snapshot_v7(g)
FROM app.employee_object_access g;

CREATE FUNCTION app.prepare_object_access_v7() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,app,catalog AS $$
DECLARE actor text; actor_employee uuid; reason_text text; new_grant boolean;
BEGIN
    actor := NULLIF(btrim(current_setting('app.actor_account_id',true)),'');
    reason_text := NULLIF(btrim(current_setting('app.change_reason',true)),'');
    SELECT id INTO actor_employee FROM app.employees
    WHERE account_id=actor AND employment_status='active';
    IF actor_employee IS NULL OR NOT app.account_is_eligible_v6(actor) THEN
        RAISE EXCEPTION 'Access changes require an eligible employee actor' USING ERRCODE='23514';
    END IF;
    IF reason_text IS NULL OR length(reason_text) NOT BETWEEN 3 AND 500 THEN
        RAISE EXCEPTION 'Supply transaction-local change_reason (3..500 characters)' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' THEN
        IF NEW.id IS DISTINCT FROM OLD.id OR NEW.employee_id IS DISTINCT FROM OLD.employee_id
           OR NEW.facility_id IS DISTINCT FROM OLD.facility_id OR NEW.equipment_id IS DISTINCT FROM OLD.equipment_id
           OR NEW.legacy_account_id IS DISTINCT FROM OLD.legacy_account_id THEN
            RAISE EXCEPTION 'Assignment identity is immutable' USING ERRCODE='55000';
        END IF;
        new_grant := NOT OLD.is_active AND NEW.is_active;
        IF NEW.is_active AND NOT new_grant THEN
            NEW.granted_at:=OLD.granted_at;
            NEW.granted_by_employee_id:=OLD.granted_by_employee_id;
            NEW.grant_reason:=OLD.grant_reason;
        END IF;
    ELSE
        IF NEW.legacy_account_id IS NOT NULL THEN
            RAISE EXCEPTION 'Legacy provenance is migration-only' USING ERRCODE='23514';
        END IF;
        IF NOT NEW.is_active THEN
            RAISE EXCEPTION 'Create an active grant, then revoke it' USING ERRCODE='23514';
        END IF;
        new_grant:=true;
    END IF;
    -- Revocation remains possible even after an employee or object was disabled.
    IF NEW.is_active THEN
        IF NOT EXISTS(SELECT 1 FROM app.employees e LEFT JOIN app.accounts a ON a.id=e.account_id
            WHERE e.id=NEW.employee_id AND e.employment_status='active'
              AND (e.account_id IS NULL OR (a.is_active AND a.account_type='employee'))) THEN
            RAISE EXCEPTION 'Grant target employee/account is inactive or missing' USING ERRCODE='23514';
        END IF;
        IF NEW.facility_id IS NOT NULL AND NOT app.facility_path_is_active_v7(NEW.facility_id) THEN
            RAISE EXCEPTION 'Grant target facility/path is inactive or missing' USING ERRCODE='23514';
        END IF;
        IF NEW.equipment_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM catalog.equipment
            WHERE id=NEW.equipment_id AND is_active
              AND (facility_id IS NULL OR app.facility_path_is_active_v7(facility_id))) THEN
            RAISE EXCEPTION 'Grant target equipment/path is inactive or missing' USING ERRCODE='23514';
        END IF;
        IF new_grant THEN
            NEW.granted_at:=clock_timestamp(); NEW.granted_by_employee_id:=actor_employee;
            NEW.grant_reason:=reason_text;
        END IF;
        NEW.revoked_at:=NULL; NEW.revoked_by_employee_id:=NULL; NEW.revoke_reason:=NULL;
    ELSE
        IF OLD.is_active THEN
            NEW.revoked_at:=clock_timestamp(); NEW.revoked_by_employee_id:=actor_employee;
            NEW.revoke_reason:=reason_text;
        ELSE
            NEW.revoked_at:=OLD.revoked_at; NEW.revoked_by_employee_id:=OLD.revoked_by_employee_id;
            NEW.revoke_reason:=OLD.revoke_reason;
        END IF;
        NEW.granted_at:=OLD.granted_at; NEW.granted_by_employee_id:=OLD.granted_by_employee_id;
        NEW.grant_reason:=OLD.grant_reason;
    END IF;
    NEW.updated_at:=clock_timestamp();
    RETURN NEW;
END $$;
CREATE FUNCTION app.capture_object_access_v7() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,app AS $$
DECLARE kind text; actor text; eid uuid; previous jsonb;
BEGIN
    actor:=current_setting('app.actor_account_id',true);
    SELECT id INTO eid FROM app.employees WHERE account_id=actor;
    IF TG_OP='INSERT' THEN kind:='granted'; ELSE
        kind:=CASE WHEN OLD.is_active AND NOT NEW.is_active THEN 'revoked'
            WHEN NOT OLD.is_active AND NEW.is_active THEN 'regranted' ELSE 'updated' END;
        previous:=app.object_access_snapshot_v7(OLD);
    END IF;
    INSERT INTO app.employee_object_access_events(assignment_id,event_type,actor_employee_id,actor_account_id,
        reason,request_id,before_data,after_data)
    VALUES(NEW.id,kind,eid,actor,current_setting('app.change_reason',true),
        NULLIF(current_setting('app.request_id',true),''),previous,app.object_access_snapshot_v7(NEW));
    RETURN NEW;
END $$;
CREATE TRIGGER trg_object_access_prepare_v7 BEFORE INSERT OR UPDATE ON app.employee_object_access
FOR EACH ROW EXECUTE FUNCTION app.prepare_object_access_v7();
CREATE TRIGGER trg_object_access_event_v7 AFTER INSERT OR UPDATE ON app.employee_object_access
FOR EACH ROW EXECUTE FUNCTION app.capture_object_access_v7();
CREATE TRIGGER trg_object_access_no_delete_v7 BEFORE DELETE ON app.employee_object_access
FOR EACH ROW EXECUTE FUNCTION app.prevent_access_assignment_delete_v5();
CREATE TRIGGER trg_object_events_immutable_v7 BEFORE UPDATE OR DELETE ON app.employee_object_access_events
FOR EACH ROW EXECUTE FUNCTION app.prevent_access_history_mutation_v5();
CREATE TRIGGER trg_object_events_no_truncate_v7 BEFORE TRUNCATE ON app.employee_object_access_events
FOR EACH STATEMENT EXECUTE FUNCTION app.prevent_access_history_mutation_v5();

-- Legacy human grants are preserved as read-only provenance, not a second source of access.
CREATE FUNCTION app.guard_legacy_scope_v7() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,app AS $$
BEGIN
    IF TG_OP='UPDATE' AND (NEW.account_id IS DISTINCT FROM OLD.account_id OR
        NEW.facility_id IS DISTINCT FROM OLD.facility_id) THEN
        RAISE EXCEPTION 'Legacy scope identity is immutable' USING ERRCODE='55000';
    END IF;
    IF EXISTS(SELECT 1 FROM app.employees WHERE account_id=NEW.account_id) THEN
        RAISE EXCEPTION 'Use employee_object_access for employee grants; legacy scope is archived' USING ERRCODE='55000';
    END IF;
    IF NEW.is_active AND (NOT app.account_is_eligible_v6(NEW.account_id)
       OR NOT app.facility_path_is_active_v7(NEW.facility_id)) THEN
        RAISE EXCEPTION 'Technical scope target is ineligible or inactive' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_legacy_scope_guard_v7 BEFORE INSERT OR UPDATE ON app.account_facility_access
FOR EACH ROW EXECUTE FUNCTION app.guard_legacy_scope_v7();

CREATE FUNCTION app.employee_has_facility_scope_v7(p_employee_id uuid,p_facility_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,app AS $$
    WITH RECURSIVE ancestors AS (
        SELECT id,parent_facility_id,0 AS depth,ARRAY[id] AS seen FROM app.facilities WHERE id=p_facility_id
        UNION ALL
        SELECT f.id,f.parent_facility_id,a.depth+1,a.seen||f.id
        FROM ancestors a JOIN app.facilities f ON f.id=a.parent_facility_id WHERE NOT f.id=ANY(a.seen)
    ) SELECT app.facility_path_is_active_v7(p_facility_id) AND EXISTS(
        SELECT 1 FROM ancestors a JOIN app.employee_object_access g ON g.facility_id=a.id
        JOIN app.employees e ON e.id=g.employee_id
        WHERE e.id=p_employee_id AND e.employment_status='active' AND g.is_active AND g.revoked_at IS NULL
          AND g.valid_from<=statement_timestamp() AND (g.valid_until IS NULL OR statement_timestamp()<g.valid_until)
          AND (a.depth=0 OR g.include_descendants));
$$;
CREATE OR REPLACE FUNCTION app.account_can_access_facility_v6(p_account_id text,p_facility_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,app AS $$
    WITH RECURSIVE ancestors AS (
        SELECT id,parent_facility_id,0 AS depth,ARRAY[id] AS seen FROM app.facilities WHERE id=p_facility_id
        UNION ALL
        SELECT f.id,f.parent_facility_id,a.depth+1,a.seen||f.id
        FROM ancestors a JOIN app.facilities f ON f.id=a.parent_facility_id WHERE NOT f.id=ANY(a.seen)
    ) SELECT app.account_is_eligible_v6(p_account_id) AND app.facility_path_is_active_v7(p_facility_id) AND (
        app.account_has_permission_v4(p_account_id,'facilities.access_all')
        OR EXISTS(SELECT 1 FROM app.employees e WHERE e.account_id=p_account_id
            AND app.employee_has_facility_scope_v7(e.id,p_facility_id))
        OR EXISTS(SELECT 1 FROM ancestors a JOIN app.account_facility_access g ON g.facility_id=a.id
            JOIN app.accounts account ON account.id=g.account_id AND account.account_type IN ('service','system')
            WHERE g.account_id=p_account_id AND g.is_active AND g.revoked_at IS NULL AND g.valid_from<=statement_timestamp()
              AND (g.valid_until IS NULL OR statement_timestamp()<g.valid_until) AND (a.depth=0 OR g.include_descendants)));
$$;
CREATE OR REPLACE FUNCTION app.account_can_access_equipment_v4(p_account_id text,p_equipment_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,app,catalog AS $$
    SELECT app.account_is_eligible_v6(p_account_id) AND COALESCE((SELECT eq.is_active
        AND (eq.facility_id IS NULL OR app.facility_path_is_active_v7(eq.facility_id)) AND (
            (eq.facility_id IS NOT NULL AND app.account_can_access_facility_v6(p_account_id,eq.facility_id))
            OR (eq.facility_id IS NULL AND app.account_has_permission_v4(p_account_id,'equipment.read_unassigned'))
            OR EXISTS(SELECT 1 FROM app.employee_object_access g JOIN app.employees e ON e.id=g.employee_id
                WHERE e.account_id=p_account_id AND g.equipment_id=eq.id AND g.is_active AND g.revoked_at IS NULL
                  AND g.valid_from<=statement_timestamp() AND (g.valid_until IS NULL OR statement_timestamp()<g.valid_until)))
        FROM catalog.equipment eq WHERE eq.id=p_equipment_id),false);
$$;
CREATE FUNCTION app.account_can_read_equipment_v7(p_account_id text,p_equipment_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,app AS $$
    SELECT app.account_has_permission_v4(p_account_id,'equipment.read')
       AND app.account_can_access_equipment_v4(p_account_id,p_equipment_id);
$$;
CREATE FUNCTION app.account_can_read_employee_v7(p_actor_account_id text,p_employee_id uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,app AS $$
    WITH RECURSIVE departments AS (
        SELECT d.id,d.parent_department_id,d.is_active,ARRAY[d.id] AS seen
        FROM app.employees e JOIN app.departments d ON d.id=e.department_id WHERE e.id=p_employee_id
        UNION ALL
        SELECT d.id,d.parent_department_id,p.is_active AND d.is_active,p.seen||d.id
        FROM departments p JOIN app.departments d ON d.id=p.parent_department_id WHERE NOT d.id=ANY(p.seen)
    ) SELECT EXISTS(SELECT 1 FROM app.employees WHERE id=p_employee_id) AND (
        app.account_has_permission_v4(p_actor_account_id,'employees.read')
        OR (app.account_has_permission_v4(p_actor_account_id,'employees.read_own') AND EXISTS(
            SELECT 1 FROM app.employees WHERE id=p_employee_id AND account_id=p_actor_account_id))
        OR (app.account_has_permission_v4(p_actor_account_id,'employees.read_assigned') AND EXISTS(
            SELECT 1 FROM departments d JOIN app.department_facilities df ON df.department_id=d.id
            WHERE d.is_active AND app.account_can_access_facility_v6(p_actor_account_id,df.facility_id))));
$$;
CREATE OR REPLACE FUNCTION app.account_can_access_employee_v6(p_actor_account_id text,p_employee_account_id text)
RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER SET search_path=pg_catalog,app AS $$
    SELECT COALESCE((SELECT app.account_can_read_employee_v7(p_actor_account_id,id)
        FROM app.employees WHERE account_id=p_employee_account_id),false);
$$;

-- Secrets must not be embedded in authorization payloads or history JSON.
-- No PIN/hash is rehashed or replaced by this migration.
ALTER TABLE app.accounts ADD CONSTRAINT accounts_payload_no_credentials_v7
    CHECK(NOT app.audit_json_has_sensitive_key_v4(payload));
ALTER TABLE app.accounts ADD CONSTRAINT accounts_pin_phc_v7 CHECK(pin_hash IS NULL OR
    pin_hash ~ '^\$argon2id\$v=19\$m=[1-9][0-9]*,t=[1-9][0-9]*,p=[1-9][0-9]*\$[A-Za-z0-9+/]{8,}\$[A-Za-z0-9+/]{16,}$');
CREATE FUNCTION app.reject_history_secrets_v7() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,app AS $$
DECLARE data jsonb;
BEGIN
    -- Known credential signatures in free text are also blocked. Arbitrary unlabeled
    -- passwords cannot be classified by SQL; backend must use non-secret allowlists.
    data:=to_jsonb(NEW);
    IF app.audit_json_has_sensitive_key_v4(data) OR
       data::text ~* '(\$argon2(id|i|d)\$|\$2[aby]\$|bearer[[:space:]]+[a-z0-9._~-]+)' THEN
        RAISE EXCEPTION 'Sensitive data is forbidden in audit/history' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_audit_secrets_v7 BEFORE INSERT ON app.audit_log
FOR EACH ROW EXECUTE FUNCTION app.reject_history_secrets_v7();
CREATE TRIGGER trg_object_event_secrets_v7 BEFORE INSERT ON app.employee_object_access_events
FOR EACH ROW EXECUTE FUNCTION app.reject_history_secrets_v7();
CREATE TRIGGER trg_role_event_secrets_v7 BEFORE INSERT ON app.account_role_events
FOR EACH ROW EXECUTE FUNCTION app.reject_history_secrets_v7();
CREATE TRIGGER trg_legacy_event_secrets_v7 BEFORE INSERT ON app.account_facility_access_events
FOR EACH ROW EXECUTE FUNCTION app.reject_history_secrets_v7();
CREATE TRIGGER trg_location_history_secrets_v7 BEFORE INSERT ON catalog.equipment_facility_history
FOR EACH ROW EXECUTE FUNCTION app.reject_history_secrets_v7();

-- Event insertion/compatibility/session side effects run as the migration owner;
-- runtime receives no direct INSERT/UPDATE/DELETE on history or credential columns.
ALTER FUNCTION app.capture_account_role_event_v5() SECURITY DEFINER;
ALTER FUNCTION app.capture_account_role_event_v5() SET search_path=pg_catalog,app;
ALTER FUNCTION app.capture_account_facility_event_v5() SECURITY DEFINER;
ALTER FUNCTION app.capture_account_facility_event_v5() SET search_path=pg_catalog,app;
ALTER FUNCTION catalog.capture_equipment_facility_change_v5() SECURITY DEFINER;
ALTER FUNCTION catalog.capture_equipment_facility_change_v5() SET search_path=pg_catalog,app,catalog;
ALTER FUNCTION app.sync_employee_legacy_account_fields_v4() SECURITY DEFINER;
ALTER FUNCTION app.handle_employee_status_change_v4() SECURITY DEFINER;
ALTER FUNCTION app.handle_employee_status_change_v4() SET search_path=pg_catalog,app;
ALTER FUNCTION app.revoke_sessions_for_disabled_account_v4() SECURITY DEFINER;
ALTER FUNCTION app.revoke_sessions_for_disabled_account_v4() SET search_path=pg_catalog,app;
REVOKE ALL ON FUNCTION app.capture_object_access_v7(),app.capture_account_role_event_v5(),
    app.capture_account_facility_event_v5(),catalog.capture_equipment_facility_change_v5(),
    app.sync_employee_legacy_account_fields_v4(),app.handle_employee_status_change_v4(),
    app.revoke_sessions_for_disabled_account_v4() FROM PUBLIC;

-- Harden TRUNCATE as well as DELETE; owners/superusers remain privileged operators.
DO $truncate$
DECLARE rel text;
BEGIN
    FOREACH rel IN ARRAY ARRAY['app.accounts','app.employees','app.facilities','app.departments',
        'app.account_roles','app.account_facility_access','app.employee_object_access'] LOOP
        EXECUTE format('CREATE TRIGGER trg_no_truncate_v7 BEFORE TRUNCATE ON %s
            FOR EACH STATEMENT EXECUTE FUNCTION app.prevent_access_history_mutation_v5()',rel);
    END LOOP;
END $truncate$;

-- Validate inherited NOT VALID constraints; failures roll back the whole migration.
DO $validate$
DECLARE r record;
BEGIN
    FOR r IN SELECT conrelid::regclass AS relation,conname FROM pg_constraint
        WHERE NOT convalidated AND connamespace IN ('app'::regnamespace,'reports'::regnamespace,'catalog'::regnamespace)
    LOOP EXECUTE format('ALTER TABLE %s VALIDATE CONSTRAINT %I',r.relation,r.conname); END LOOP;
END $validate$;

-- Existing trees must also be audited, even if 007 did not update their rows.
DO $existing_trees$
DECLARE r record; bad boolean;
BEGIN
    FOR r IN SELECT * FROM (VALUES ('facilities','parent_facility_id'),
        ('departments','parent_department_id'),('employees','supervisor_id')) AS t(relation,parent_column)
    LOOP
        EXECUTE format('WITH RECURSIVE edges(origin,id,parent_id) AS (
            SELECT id,id,%1$I FROM app.%2$I UNION
            SELECT e.origin,p.id,p.%1$I FROM edges e JOIN app.%2$I p ON p.id=e.parent_id
        ) SELECT EXISTS(SELECT 1 FROM edges WHERE origin=parent_id)',r.parent_column,r.relation) INTO bad;
        IF bad THEN RAISE EXCEPTION 'Existing % hierarchy contains a cycle',r.relation; END IF;
    END LOOP;
    IF EXISTS(SELECT 1 FROM reports.inspection_items ii
        JOIN reports.inspections i ON i.id=ii.inspection_id JOIN catalog.components c ON c.id=ii.component_id
        WHERE i.equipment_id<>c.equipment_id) THEN
        RAISE EXCEPTION 'Existing inspection/component equipment mismatch; reconcile verified source data first';
    END IF;
END $existing_trees$;

CREATE VIEW reports.v_employee_activity_v7 WITH(security_invoker=true) AS
SELECT e.id AS employee_id,e.account_id,e.employee_number,e.full_name,e.position_title,e.department_id,
    e.supervisor_id,e.employment_status,a.is_active AS account_is_active,count(i.id) AS inspections_total,
    max(i.performed_at) AS last_inspection_at
FROM app.employees e LEFT JOIN app.accounts a ON a.id=e.account_id
LEFT JOIN reports.inspections i ON i.author_id=e.account_id
GROUP BY e.id,a.is_active;
COMMENT ON TABLE app.employees IS 'Personnel identity (UUID id); optional immutable account link. Unknown legacy HR fields remain NULL.';
COMMENT ON COLUMN app.employees.supervisor_id IS 'Canonical manager employee identity, including managers without accounts.';
COMMENT ON COLUMN app.employees.supervisor_account_id IS 'Compatibility mirror of supervisor_id; use supervisor_id in new code.';
COMMENT ON TABLE app.employee_object_access IS 'Canonical human object scope, independent of RBAC; one current row per employee/object, immutable event history.';
COMMENT ON TABLE app.account_facility_access IS 'Technical account scope; human v6 rows retained read-only as migration provenance.';
COMMENT ON FUNCTION app.account_can_read_equipment_v7(text,uuid) IS 'Complete read decision: equipment.read AND effective object scope AND active employee/account/equipment/path.';
INSERT INTO app.schema_version(version) VALUES(7);
COMMIT;
