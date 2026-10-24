PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users (
        id text PRIMARY KEY NOT NULL,
        username text NOT NULL UNIQUE,
        password text NOT NULL,
        role text DEFAULT 'member' NOT NULL,
        created integer NOT NULL
      );
CREATE TABLE IF NOT EXISTS sessions (
        token text PRIMARY KEY NOT NULL,
        user_id text NOT NULL REFERENCES users(id),
        expires integer NOT NULL
      );
CREATE INDEX IF NOT EXISTS session_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS settings (
        key text PRIMARY KEY NOT NULL,
        value text NOT NULL
      );
CREATE TABLE IF NOT EXISTS projects (
        id text PRIMARY KEY NOT NULL,
        user_id text NOT NULL REFERENCES users(id),
        name text NOT NULL,
        description text DEFAULT '' NOT NULL,
        created integer NOT NULL
      );
CREATE INDEX IF NOT EXISTS project_owner ON projects(user_id);
CREATE TABLE IF NOT EXISTS documents (
        hash text PRIMARY KEY NOT NULL,
        size integer NOT NULL,
        status text DEFAULT 'uploaded' NOT NULL,
        pages integer DEFAULT 0 NOT NULL,
        cursor integer DEFAULT 0 NOT NULL,
        total integer DEFAULT 0 NOT NULL,
        index_cursor integer DEFAULT 0 NOT NULL,
        indexed integer DEFAULT 0 NOT NULL,
        lease integer DEFAULT 0 NOT NULL,
        error text,
        created integer NOT NULL
      );
CREATE TABLE IF NOT EXISTS project_docs (
        id text PRIMARY KEY NOT NULL,
        project_id text NOT NULL REFERENCES projects(id),
        hash text NOT NULL REFERENCES documents(hash),
        filename text NOT NULL,
        doc_kind text DEFAULT 'spec' NOT NULL,
        log_version integer DEFAULT 0 NOT NULL,
        imported integer DEFAULT 0 NOT NULL,
        empty_review text,
        created integer NOT NULL
      );
CREATE UNIQUE INDEX IF NOT EXISTS project_hash ON project_docs(project_id,hash);
CREATE INDEX IF NOT EXISTS project_docs_owner ON project_docs(project_id);
CREATE TABLE IF NOT EXISTS requirements (
        id text PRIMARY KEY NOT NULL,
        doc_id text NOT NULL REFERENCES project_docs(id),
        data text NOT NULL,
        status text DEFAULT 'needs_review' NOT NULL,
        review_note text DEFAULT '' NOT NULL,
        revision integer DEFAULT 0 NOT NULL,
        updated integer NOT NULL
      );
CREATE INDEX IF NOT EXISTS req_doc ON requirements(doc_id);
CREATE TABLE IF NOT EXISTS messages (
        id text PRIMARY KEY NOT NULL,
        project_id text NOT NULL REFERENCES projects(id),
        doc_id text,
        role text NOT NULL,
        content text NOT NULL,
        citations text DEFAULT '[]' NOT NULL,
        created integer NOT NULL
      );
CREATE INDEX IF NOT EXISTS chat_project_time ON messages(project_id,created);
CREATE TABLE IF NOT EXISTS events (
        id text PRIMARY KEY NOT NULL,
        project_id text NOT NULL REFERENCES projects(id),
        message text NOT NULL,
        created integer NOT NULL
      );
CREATE INDEX IF NOT EXISTS event_project_time ON events(project_id,created);
CREATE TABLE IF NOT EXISTS limits (
        key text PRIMARY KEY NOT NULL,
        count integer NOT NULL,
        expires integer NOT NULL
      );
CREATE TABLE IF NOT EXISTS submittal_jobs (
        hash text PRIMARY KEY NOT NULL,
        status text DEFAULT 'pending' NOT NULL,
        cursor integer DEFAULT 0 NOT NULL,
        total integer DEFAULT 0 NOT NULL,
        lease integer DEFAULT 0 NOT NULL,
        error text
      );
CREATE TABLE IF NOT EXISTS drawing_jobs (
        hash text PRIMARY KEY NOT NULL,
        status text DEFAULT 'pending' NOT NULL,
        cursor integer DEFAULT 0 NOT NULL,
        total integer DEFAULT 0 NOT NULL,
        lease integer DEFAULT 0 NOT NULL,
        error text
      );
CREATE TABLE IF NOT EXISTS equipment_logs (
        id text PRIMARY KEY NOT NULL,
        project_id text NOT NULL REFERENCES projects(id),
        revision integer DEFAULT 0 NOT NULL,
        created integer NOT NULL,
        updated integer NOT NULL
      );
CREATE UNIQUE INDEX IF NOT EXISTS equipment_log_project ON equipment_logs(project_id);
CREATE TABLE IF NOT EXISTS equipment_log_items (
        id text PRIMARY KEY NOT NULL,
        log_id text NOT NULL REFERENCES equipment_logs(id),
        drawing_doc_id text,
        unit_tag text NOT NULL,
        sheet_number text DEFAULT '' NOT NULL,
        schedule_title text DEFAULT '' NOT NULL,
        table_id text DEFAULT '' NOT NULL,
        row_index integer DEFAULT 0 NOT NULL,
        band text DEFAULT 'no_match' NOT NULL,
        match_method text,
        proposed_csi text,
        proposed_description text,
        selected_csi text,
        selected_description text,
        status text DEFAULT 'needs_review' NOT NULL,
        basis_of_design text,
        raw_fields text DEFAULT '{}' NOT NULL,
        candidates text DEFAULT '[]' NOT NULL,
        equipment_types text DEFAULT '[]' NOT NULL,
        revision integer DEFAULT 0 NOT NULL,
        updated integer NOT NULL
      );
CREATE INDEX IF NOT EXISTS equipment_items_log ON equipment_log_items(log_id);
CREATE UNIQUE INDEX IF NOT EXISTS equipment_item_dedupe ON equipment_log_items(log_id,drawing_doc_id,unit_tag);
