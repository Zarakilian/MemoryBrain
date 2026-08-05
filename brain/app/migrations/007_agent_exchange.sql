-- v2.4.0: Synapse — the Agent Exchange.
--
-- Threads are units of collaboration (task / review / question / handoff /
-- discussion) inside a project. Messages are addressed agent-to-agent
-- (to_agent = '' means broadcast: whoever shows up next). Delivery is
-- pull-based: agents call get_agent_inbox at session start; read cursors
-- keep the inbox idempotent.
--
-- Threads are operational, memories are knowledge: durable conclusions from
-- a thread should still be written with add_memory.

CREATE TABLE IF NOT EXISTS agent_threads (
    id          TEXT PRIMARY KEY,
    project     TEXT NOT NULL,
    title       TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'task'
                CHECK (kind IN ('task','review','question','handoff','discussion')),
    status      TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open','in_progress','review','done','closed')),
    created_by  TEXT NOT NULL,
    assigned_to TEXT NOT NULL DEFAULT '',
    priority    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_threads_project ON agent_threads(project, status);
CREATE INDEX IF NOT EXISTS idx_threads_assigned ON agent_threads(assigned_to, status);

CREATE TABLE IF NOT EXISTS agent_messages (
    id          TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL REFERENCES agent_threads(id),
    from_agent  TEXT NOT NULL,
    to_agent    TEXT NOT NULL DEFAULT '',
    intent      TEXT NOT NULL DEFAULT 'update'
                CHECK (intent IN ('request','update','review','approval',
                                  'question','answer','handoff','done')),
    body        TEXT NOT NULL,
    refs        TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_thread ON agent_messages(thread_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_to ON agent_messages(to_agent, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_from ON agent_messages(from_agent, created_at);

CREATE TABLE IF NOT EXISTS agent_read_cursors (
    agent        TEXT NOT NULL,
    thread_id    TEXT NOT NULL,
    last_read_at TEXT NOT NULL,
    PRIMARY KEY (agent, thread_id)
);
