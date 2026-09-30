-- v2.5.0: workspace layer. Files and folders as first class nodes joined to memories.
-- Design rules: the brain never reads the filesystem (manifests are pushed);
-- file edges live here, not in memory_links; everything below is rebuildable
-- from memory content plus the last manifest, except project_folders bindings.

-- init_db creates `projects` after run_migrations on a fresh database, so the
-- table is created here first (no-op on an upgraded database).
CREATE TABLE IF NOT EXISTS projects (
    slug          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    last_activity TEXT NOT NULL,
    one_liner     TEXT DEFAULT ''
);
ALTER TABLE projects ADD COLUMN description TEXT NOT NULL DEFAULT '';
-- description_source: 'user' | 'tool' | 'auto' | ''  (auto = drafted by consolidation, replaceable)
ALTER TABLE projects ADD COLUMN description_source TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD COLUMN description_updated_at TEXT;

CREATE TABLE IF NOT EXISTS workspace_roots (
    root_id     TEXT NOT NULL,               -- short label, e.g. 'git'
    machine     TEXT NOT NULL,               -- hostname that reported it
    abs_path    TEXT NOT NULL,               -- 'C:\work\repos' on that machine
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    PRIMARY KEY (root_id, machine)
);

CREATE TABLE IF NOT EXISTS project_folders (
    root_id        TEXT NOT NULL,
    rel_path       TEXT NOT NULL,            -- 'Daily Reports', 'Daily Reports/Tools/ReportFlow'
    rel_path_ci    TEXT NOT NULL,
    depth          INTEGER NOT NULL DEFAULT 1,
    project        TEXT NOT NULL DEFAULT '', -- '' = seen but unmapped
    label          TEXT NOT NULL DEFAULT '',
    role           TEXT NOT NULL DEFAULT 'primary'
                   CHECK (role IN ('primary','repo','related','area','archive')),
    remote_url     TEXT NOT NULL DEFAULT '',
    how            TEXT NOT NULL DEFAULT 'init'
                   CHECK (how IN ('marker','init','cwd','tool','memory')),
    confidence     REAL NOT NULL DEFAULT 1.0,
    confirmed      INTEGER NOT NULL DEFAULT 0,
    evidence_count INTEGER NOT NULL DEFAULT 1,
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (root_id, rel_path_ci)
);
CREATE INDEX IF NOT EXISTS idx_pf_project ON project_folders(project);

CREATE TABLE IF NOT EXISTS workspace_files (
    file_id      TEXT PRIMARY KEY,
    root_id      TEXT NOT NULL,
    rel_path     TEXT NOT NULL,
    rel_path_ci  TEXT NOT NULL,
    basename_ci  TEXT NOT NULL,
    folder       TEXT NOT NULL,              -- first path segment
    ext          TEXT NOT NULL DEFAULT '',
    size         INTEGER NOT NULL DEFAULT 0,
    mtime        TEXT NOT NULL,
    content_hash TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active','deleted','moved')),
    moved_to     TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL,
    seen_on      TEXT NOT NULL DEFAULT '[]',
    ref_degree   REAL NOT NULL DEFAULT 0,
    UNIQUE (root_id, rel_path_ci)
);
CREATE INDEX IF NOT EXISTS idx_wf_folder   ON workspace_files(folder, status);
CREATE INDEX IF NOT EXISTS idx_wf_basename ON workspace_files(basename_ci);
CREATE INDEX IF NOT EXISTS idx_wf_hash     ON workspace_files(content_hash);

CREATE TABLE IF NOT EXISTS file_links (
    src_kind   TEXT NOT NULL CHECK (src_kind IN ('memory','file')),
    src_id     TEXT NOT NULL,
    dst_kind   TEXT NOT NULL CHECK (dst_kind IN ('file','dangling')),
    dst_id     TEXT NOT NULL,                -- file_id, or the raw token when dangling
    kind       TEXT NOT NULL CHECK (kind IN ('file_ref','touched','links_to')),
    weight     REAL NOT NULL DEFAULT 1.0,
    meta       TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (src_kind, src_id, dst_kind, dst_id, kind)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_fl_dst ON file_links(dst_kind, dst_id);

CREATE TABLE IF NOT EXISTS workspace_touches (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL,
    root_id     TEXT NOT NULL,
    rel_path    TEXT NOT NULL,
    op          TEXT NOT NULL CHECK (op IN ('edit','write','scan_diff')),
    session_key TEXT NOT NULL DEFAULT '',
    at          TEXT NOT NULL,
    attached_to TEXT
);
CREATE INDEX IF NOT EXISTS idx_wt_project_at ON workspace_touches(project, at);
