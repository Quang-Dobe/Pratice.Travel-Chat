CREATE TABLE IF NOT EXISTS conversations (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    title text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_conversations_user_id_created_at
    ON conversations (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS exports (
    id uuid PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES conversations (id),
    card_id text,
    format text NOT NULL CHECK (format IN ('html', 'docx', 'xlsx', 'csv')),
    file_name text NOT NULL,
    content bytea NOT NULL,
    draft boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_exports_conversation_id
    ON exports (conversation_id);

CREATE TABLE IF NOT EXISTS trip_summaries (
    conversation_id uuid PRIMARY KEY REFERENCES conversations (id),
    user_id uuid NOT NULL,
    destination text NOT NULL,
    start_date date,
    end_date date,
    trip_type text,
    preferences text[] NOT NULL DEFAULT '{}' CHECK (cardinality(preferences) <= 3),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_trip_summaries_user_id_updated_at
    ON trip_summaries (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS suggestion_settings (
    user_id uuid PRIMARY KEY,
    enabled boolean NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
