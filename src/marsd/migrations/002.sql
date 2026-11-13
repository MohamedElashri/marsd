CREATE TABLE schema_snapshots(id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES versions(id), software_version TEXT NOT NULL, policy_digest TEXT NOT NULL, descriptor TEXT NOT NULL, completeness INTEGER NOT NULL, captured_at TEXT NOT NULL, UNIQUE(version_id,software_version,policy_digest));
CREATE UNIQUE INDEX contracts_revision ON contracts(name, revision);
