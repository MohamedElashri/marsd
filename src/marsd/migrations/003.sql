CREATE TABLE project_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE root_mappings(original_root TEXT PRIMARY KEY, current_root TEXT NOT NULL);
CREATE TABLE maintenance_events(id TEXT PRIMARY KEY, kind TEXT NOT NULL, details TEXT NOT NULL, created_at TEXT NOT NULL);
