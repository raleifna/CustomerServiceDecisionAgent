-- agent_runs/tool_calls, as created by 001, are missing a few columns the
-- QueryToolProvider observability wiring needs: agent_name/model_version/
-- workflow_version/duration_ms on agent_runs, error_code/duration_ms/
-- attempt_number on tool_calls. All additive and IF NOT EXISTS-guarded, so
-- this is safe to apply against a database that already has these tables
-- from 001 (with or without these columns) and safe to re-run.

ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS agent_name TEXT;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS model_version TEXT;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS workflow_version TEXT;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS duration_ms INTEGER;

CREATE INDEX IF NOT EXISTS ix_agent_runs_agent_name ON agent_runs (agent_name);

ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS error_code TEXT;
ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS duration_ms INTEGER;
ALTER TABLE tool_calls ADD COLUMN IF NOT EXISTS attempt_number INTEGER NOT NULL DEFAULT 1;
