-- Inbox contract 2: message kind and correlation.
--
-- Additive on purpose. The signed delivery envelope is unchanged, so an older API image
-- and an older adapter keep working against a database that has this table: they ignore it.
-- A delivery without a row here is a plain NOTE.
CREATE TABLE work.inbox_message_context (
    delivery_id uuid PRIMARY KEY REFERENCES work.inbox_deliveries ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN
        ('NOTE','TASK','QUESTION','ANSWER','PROGRESS','RESULT','FAILED','DECLINED','CANCELLED')),
    in_reply_to uuid REFERENCES work.inbox_deliveries,
    thread_id uuid NOT NULL REFERENCES work.inbox_deliveries,
    kid text NOT NULL,
    signature text NOT NULL,
    CHECK (in_reply_to IS NULL OR in_reply_to <> delivery_id)
);
CREATE INDEX inbox_message_context_thread ON work.inbox_message_context (thread_id, kind);
CREATE INDEX inbox_message_context_reply ON work.inbox_message_context (in_reply_to) WHERE in_reply_to IS NOT NULL;
CREATE INDEX inbox_deliveries_sent ON work.inbox_deliveries (initiative_id, sender_id, created_at DESC, delivery_id);
