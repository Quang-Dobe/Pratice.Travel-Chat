CREATE TABLE IF NOT EXISTS conversations (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    title text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_conversations_user_id_created_at
    ON conversations (user_id, created_at DESC);
