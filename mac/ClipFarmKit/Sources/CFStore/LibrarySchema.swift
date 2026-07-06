import CFDomain
import GRDB

/// The library database schema — plan §2.3, versioned via `DatabaseMigrator`
/// from day one. One registered migration per version bump; the migrator is
/// the enforcer of schema version, `meta.schema_version` is the
/// informational copy (inspectability; the backup JSON carries it).
///
/// Deliberate non-FKs (spec behavior, not oversights):
/// - `attempt_clips.clip_id` — tombstones dangle by design: deleting a base
///   clip keeps the attempt slot as a "removed — pick a replacement"
///   placeholder.
/// - `attempts.parent_attempt_id` — a fork of a deleted parent keeps its
///   dangling reference ("fork of [deleted attempt]").
enum LibrarySchema {
    /// Informational schema version written to `meta`; bump together with a
    /// new registered migration. Mirrors `ClipFarmState.currentVersion` —
    /// one versioning stream, DB schema and backup shape together.
    static let schemaVersion = ClipFarmState.currentVersion

    static func migrator() -> DatabaseMigrator {
        var migrator = DatabaseMigrator()

        migrator.registerMigration("v1") { db in
            try db.execute(sql: """
                CREATE TABLE meta(
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE sources(
                    id              TEXT PRIMARY KEY,
                    filename        TEXT NOT NULL,
                    path            TEXT NOT NULL,
                    duration_sec    REAL,
                    fps             REAL,
                    transcript_path TEXT,
                    added_at        TEXT NOT NULL,
                    unavailable     INTEGER NOT NULL DEFAULT 0,
                    is_hdr          INTEGER,
                    natural_width   INTEGER,
                    natural_height  INTEGER
                );

                CREATE TABLE clips(
                    id                   TEXT PRIMARY KEY,
                    source_id            TEXT NOT NULL REFERENCES sources(id),
                    start_sec            REAL NOT NULL,
                    end_sec              REAL NOT NULL,
                    transcript_text      TEXT NOT NULL DEFAULT '',
                    derived_from_clip_id TEXT,
                    boundary_edited      INTEGER NOT NULL DEFAULT 0,
                    tracks               TEXT,
                    created_at           TEXT NOT NULL
                );
                CREATE INDEX idx_clips_source_id ON clips(source_id);

                CREATE TABLE projects(
                    id           TEXT PRIMARY KEY,
                    name         TEXT NOT NULL,
                    brief_md     TEXT NOT NULL DEFAULT '',
                    script_lines TEXT,
                    created_at   TEXT NOT NULL
                );

                CREATE TABLE project_tags(
                    id         TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    kind       TEXT NOT NULL,
                    name       TEXT NOT NULL,
                    parent_id  TEXT,
                    order_idx  INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (project_id, id)
                );

                -- project_id is deliberately NOT an FK (plan §2.3 marks FKs
                -- explicitly; the reference implementation tolerates tag
                -- rows for not-yet-materialized projects in fixtures).
                CREATE TABLE clip_project_tags(
                    clip_id        TEXT NOT NULL REFERENCES clips(id),
                    project_id     TEXT NOT NULL,
                    project_tag_id TEXT,
                    category       TEXT NOT NULL,
                    confidence     REAL NOT NULL DEFAULT 1.0,
                    source         TEXT NOT NULL DEFAULT 'user',
                    stale          INTEGER NOT NULL DEFAULT 0,
                    notes          TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX idx_clip_project_tags_project ON clip_project_tags(project_id);

                -- NULL-proof uniqueness backstop (finding 10): SQLite unique
                -- indexes treat bare NULLs as distinct, and project_tag_id is
                -- NULL for every bucket-category row — COALESCE closes that
                -- hole. Domain validation is the primary enforcer.
                CREATE UNIQUE INDEX idx_clip_project_tags_unique
                    ON clip_project_tags(clip_id, project_id, COALESCE(project_tag_id, ''), category);

                -- project_id: not an FK (per plan §2.3); delete-project
                -- hard-deletes attempts as explicit op code at N6.
                -- parent_attempt_id: not an FK — forks of deleted parents
                -- keep the dangling reference by design.
                CREATE TABLE attempts(
                    id                TEXT PRIMARY KEY,
                    project_id        TEXT NOT NULL,
                    name              TEXT NOT NULL,
                    parent_attempt_id TEXT,
                    source            TEXT NOT NULL,
                    premade_bucket    TEXT,
                    continuity_score  REAL,
                    needs_review      INTEGER NOT NULL DEFAULT 0,
                    created_at        TEXT NOT NULL
                );

                CREATE TABLE attempt_clips(
                    attempt_id             TEXT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
                    position               INTEGER NOT NULL,
                    clip_id                TEXT NOT NULL,
                    trim_start_offset      REAL NOT NULL DEFAULT 0,
                    trim_end_offset        REAL NOT NULL DEFAULT 0,
                    internal_pause_max_sec REAL,
                    notes                  TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (attempt_id, position)
                );

                CREATE TABLE voice_annotations(
                    source_id         TEXT NOT NULL,
                    timestamp_sec     REAL NOT NULL,
                    text              TEXT NOT NULL,
                    resolved_clip_id  TEXT,
                    target_project_id TEXT,
                    target_tag_id     TEXT
                );

                -- Per-library settings travel with the library (finding 12);
                -- app-level prefs live in UserDefaults, the API key in the
                -- Keychain (D23) — never here.
                CREATE TABLE settings(
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                -- FTS5 external-content search index over clip transcript
                -- text, kept in sync by triggers so search never surfaces
                -- deleted clips or stale text — including through undo,
                -- which replays ordinary INSERT/UPDATE/DELETE statements.
                --
                -- RESTORE-TIME CAVEAT (cold-review finding 2): the index is
                -- keyed to clips' IMPLICIT rowid (TEXT primary key), and
                -- SQLite does not document that VACUUM/VACUUM INTO preserves
                -- implicit rowids (observed to hold on 3.51, guaranteed
                -- nowhere). Whoever implements snapshot/backup restore
                -- (Settings→Restore / N13) MUST run
                --   INSERT INTO clips_fts(clips_fts) VALUES('rebuild')
                -- after opening a restored database, or renumbered rowids
                -- silently desync search from the clips table.
                CREATE VIRTUAL TABLE clips_fts USING fts5(
                    transcript_text,
                    content='clips',
                    content_rowid='rowid'
                );

                CREATE TRIGGER clips_fts_after_insert AFTER INSERT ON clips BEGIN
                    INSERT INTO clips_fts(rowid, transcript_text)
                    VALUES (new.rowid, new.transcript_text);
                END;

                CREATE TRIGGER clips_fts_after_delete AFTER DELETE ON clips BEGIN
                    INSERT INTO clips_fts(clips_fts, rowid, transcript_text)
                    VALUES ('delete', old.rowid, old.transcript_text);
                END;

                CREATE TRIGGER clips_fts_after_update AFTER UPDATE OF transcript_text ON clips BEGIN
                    INSERT INTO clips_fts(clips_fts, rowid, transcript_text)
                    VALUES ('delete', old.rowid, old.transcript_text);
                    INSERT INTO clips_fts(rowid, transcript_text)
                    VALUES (new.rowid, new.transcript_text);
                END;
                """)

            // Literal '1': each migration stamps the version IT produces, so
            // a fresh database walks 1 → 2 → … exactly like an upgraded one.
            try db.execute(
                sql: "INSERT INTO meta(key, value) VALUES ('schema_version', '1')"
            )
        }

        // v2 (N3): `.mkv` sources are remuxed to a sibling `.mp4` at ingest
        // (D15) — `path` records the playable `.mp4`, `original_path` keeps
        // the original `.mkv` as provenance (provenance forever; N3
        // PROVISIONAL 1). NULL for every non-remuxed source.
        migrator.registerMigration("v2") { db in
            try db.execute(sql: """
                ALTER TABLE sources ADD COLUMN original_path TEXT;
                UPDATE meta SET value = '2' WHERE key = 'schema_version';
                """)
        }

        return migrator
    }
}
