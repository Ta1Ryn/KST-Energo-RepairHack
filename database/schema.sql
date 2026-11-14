-- BackendFork PostgreSQL schema v2. Run inside the fork database.
BEGIN;
CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS catalog;
CREATE SCHEMA IF NOT EXISTS standards;
CREATE SCHEMA IF NOT EXISTS telemetry;
CREATE SCHEMA IF NOT EXISTS knowledge;
CREATE SCHEMA IF NOT EXISTS reports;
CREATE SCHEMA IF NOT EXISTS storage;
CREATE SCHEMA IF NOT EXISTS ai;

CREATE TABLE IF NOT EXISTS app.schema_version (
    version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app.accounts (
    id text PRIMARY KEY, payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object')
);
CREATE TABLE IF NOT EXISTS storage.photos (
    id uuid PRIMARY KEY, payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object')
);
CREATE TABLE IF NOT EXISTS storage.documents (
    id uuid PRIMARY KEY, equipment_id uuid, category text NOT NULL,
    payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object')
);
CREATE TABLE IF NOT EXISTS catalog.equipment (
    id uuid PRIMARY KEY, qr_code text NOT NULL UNIQUE, serial_number text NOT NULL,
    model text NOT NULL, name text NOT NULL, payload jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS equipment_model_idx ON catalog.equipment(model);
CREATE TABLE IF NOT EXISTS catalog.nameplates (
    equipment_id uuid PRIMARY KEY REFERENCES catalog.equipment(id), payload jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog.passports (
    equipment_id uuid PRIMARY KEY REFERENCES catalog.equipment(id), payload jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog.components (
    id uuid PRIMARY KEY, equipment_id uuid NOT NULL REFERENCES catalog.equipment(id),
    name text NOT NULL, component_type text NOT NULL, payload jsonb NOT NULL,
    UNIQUE(id,equipment_id)
);
CREATE TABLE IF NOT EXISTS catalog.installations (
    id uuid PRIMARY KEY, component_id uuid NOT NULL REFERENCES catalog.components(id),
    installed_at timestamptz NOT NULL, removed_at timestamptz,
    payload jsonb NOT NULL,
    CHECK(removed_at IS NULL OR removed_at >= installed_at),
    UNIQUE(id,component_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_installation_per_component
    ON catalog.installations(component_id) WHERE removed_at IS NULL;

CREATE TABLE IF NOT EXISTS standards.resource_norms (
    component_id uuid PRIMARY KEY REFERENCES catalog.components(id),
    lifetime_hours numeric CHECK(lifetime_hours > 0), service_interval_hours numeric CHECK(service_interval_hours > 0)
);
CREATE TABLE IF NOT EXISTS standards.sensor_norms (
    id uuid PRIMARY KEY, name text NOT NULL, sensor_kind text NOT NULL, unit text NOT NULL,
    min_value numeric, max_value numeric, warning_min numeric, warning_max numeric,
    revision integer NOT NULL CHECK(revision>0), is_demo boolean NOT NULL,
    source_document_id uuid REFERENCES storage.documents(id), payload jsonb NOT NULL,
    CHECK(min_value IS NULL OR max_value IS NULL OR min_value<=max_value),
    CHECK(warning_min IS NULL OR warning_max IS NULL OR warning_min<=warning_max),
    CHECK(min_value IS NULL OR warning_min IS NULL OR min_value<=warning_min),
    CHECK(max_value IS NULL OR warning_max IS NULL OR warning_max<=max_value)
);
CREATE TABLE IF NOT EXISTS telemetry.sensors (
    component_id uuid NOT NULL REFERENCES catalog.components(id), sensor_key text NOT NULL,
    norm_id uuid REFERENCES standards.sensor_norms(id), sensor_kind text NOT NULL, unit text NOT NULL,
    active boolean NOT NULL DEFAULT true, payload jsonb NOT NULL,
    PRIMARY KEY(component_id,sensor_key)
);
CREATE TABLE IF NOT EXISTS telemetry.readings (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    component_id uuid NOT NULL, sensor_key text NOT NULL, installation_id uuid NOT NULL,
    measured_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL DEFAULT now(),
    value numeric NOT NULL, unit text NOT NULL,
    FOREIGN KEY(component_id,sensor_key) REFERENCES telemetry.sensors(component_id,sensor_key),
    FOREIGN KEY(installation_id,component_id) REFERENCES catalog.installations(id,component_id),
    UNIQUE(component_id,sensor_key,installation_id,measured_at)
);
CREATE INDEX IF NOT EXISTS sensor_readings_recent_idx ON telemetry.readings
    (component_id,sensor_key,installation_id,measured_at DESC);
CREATE TABLE IF NOT EXISTS telemetry.operating_hours (
    installation_id uuid NOT NULL REFERENCES catalog.installations(id),
    measured_at timestamptz NOT NULL, value numeric NOT NULL CHECK(value>=0),
    PRIMARY KEY(installation_id,measured_at)
);

CREATE TABLE IF NOT EXISTS knowledge.defect_types (
    code text PRIMARY KEY, name text NOT NULL, description text NOT NULL
);
INSERT INTO knowledge.defect_types(code,name,description) VALUES
('oilLeak','Течь масла','Визуальные признаки утечки масла'),
('displacement','Смещение','Сдвиг узла или крепления относительно нормального положения'),
('corrosion','Коррозия','Визуальные признаки коррозии поверхности'),
('geometryDeformation','Нарушение геометрии','Деформация или изменение формы детали')
ON CONFLICT(code) DO NOTHING;
CREATE TABLE IF NOT EXISTS knowledge.instructions (
    id uuid PRIMARY KEY, title text NOT NULL, revision integer NOT NULL CHECK(revision>0),
    document_id uuid REFERENCES storage.documents(id), payload jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge.recommendation_rules (
    id uuid PRIMARY KEY, trigger text NOT NULL,
    instruction_id uuid NOT NULL REFERENCES knowledge.instructions(id),
    payload jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS ai.analyses (
    id uuid PRIMARY KEY, equipment_id uuid NOT NULL REFERENCES catalog.equipment(id),
    component_id uuid NOT NULL, installation_id uuid NOT NULL,
    photo_id uuid NOT NULL REFERENCES storage.photos(id), created_at timestamptz NOT NULL,
    payload jsonb NOT NULL,
    FOREIGN KEY(component_id,equipment_id) REFERENCES catalog.components(id,equipment_id),
    FOREIGN KEY(installation_id,component_id) REFERENCES catalog.installations(id,component_id)
);
CREATE INDEX IF NOT EXISTS analyses_component_idx ON ai.analyses(component_id,created_at DESC);
CREATE TABLE IF NOT EXISTS reports.inspections (
    id uuid PRIMARY KEY, equipment_id uuid NOT NULL REFERENCES catalog.equipment(id),
    author_id text NOT NULL REFERENCES app.accounts(id), performed_at timestamptz NOT NULL,
    payload jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS inspections_equipment_idx ON reports.inspections(equipment_id,performed_at DESC);
CREATE TABLE IF NOT EXISTS reports.inspection_items (
    inspection_id uuid NOT NULL REFERENCES reports.inspections(id), component_id uuid NOT NULL REFERENCES catalog.components(id),
    installation_id uuid NOT NULL, new_installation_id uuid REFERENCES catalog.installations(id),
    analysis_id uuid REFERENCES ai.analyses(id), action text NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY(inspection_id,component_id),
    FOREIGN KEY(installation_id,component_id) REFERENCES catalog.installations(id,component_id)
);
CREATE TABLE IF NOT EXISTS reports.inspection_photos (
    inspection_id uuid NOT NULL, component_id uuid NOT NULL, photo_id uuid NOT NULL REFERENCES storage.photos(id),
    PRIMARY KEY(inspection_id,component_id,photo_id),
    FOREIGN KEY(inspection_id,component_id) REFERENCES reports.inspection_items(inspection_id,component_id)
);
INSERT INTO app.schema_version(version) VALUES(2) ON CONFLICT DO NOTHING;
COMMIT;
