-- Intentionally forward-only. Never remove new personnel or access history.
BEGIN;
DO $$ BEGIN
    RAISE EXCEPTION '007 has no lossless down migration. Restore a pre-upgrade backup into a NEW database, or apply a forward fix. Preserve post-upgrade writes separately.';
END $$;
ROLLBACK;
