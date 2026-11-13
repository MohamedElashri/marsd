CREATE TABLE artifacts(id TEXT PRIMARY KEY, uri TEXT NOT NULL UNIQUE, media_type TEXT NOT NULL, registered_at TEXT NOT NULL);
CREATE TABLE versions(id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id), fingerprint TEXT NOT NULL, strength TEXT NOT NULL, captured_at TEXT NOT NULL, UNIQUE(artifact_id, fingerprint, strength));
CREATE TABLE contracts(digest TEXT PRIMARY KEY, name TEXT NOT NULL, revision TEXT NOT NULL, document TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE jobs(id TEXT PRIMARY KEY, operation TEXT NOT NULL, state TEXT NOT NULL, request TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, result TEXT, error TEXT);
CREATE TABLE reports(id TEXT PRIMARY KEY, job_id TEXT REFERENCES jobs(id), kind TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX versions_artifact ON versions(artifact_id);
CREATE INDEX reports_created ON reports(created_at);
