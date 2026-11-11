-- Collaboration is a separate fail-closed authorization mode. An empty or
-- expired grant set never turns a participant into a general contributor.
DO $$
DECLARE constraint_name text;
BEGIN
    SELECT conname INTO STRICT constraint_name FROM pg_constraint
      WHERE conrelid = 'docplane.principals'::regclass AND contype = 'c'
        AND pg_get_constraintdef(oid) LIKE '%authorization_mode%';
    EXECUTE format('ALTER TABLE docplane.principals DROP CONSTRAINT %I', constraint_name);
END $$;
ALTER TABLE docplane.principals ADD CONSTRAINT principals_authorization_mode_check CHECK (
    authorization_mode IN ('CONTRIBUTOR', 'SCOPED_AUTOMATION', 'WORK_COLLABORATOR')
    AND (authorization_mode <> 'SCOPED_AUTOMATION' OR principal_kind = 'AUTOMATION')
    AND (authorization_mode <> 'WORK_COLLABORATOR' OR principal_kind IN ('AGENT', 'AUTOMATION'))
);
CREATE TABLE work.collaboration_grants (
    initiative_id uuid NOT NULL REFERENCES work.initiatives ON DELETE CASCADE,
    principal_id uuid NOT NULL REFERENCES docplane.principals ON DELETE CASCADE,
    participant_role text NOT NULL CHECK (participant_role IN ('AGENT', 'ARCHITECT')),
    can_contribute boolean NOT NULL DEFAULT false,
    expires_at timestamptz,
    revoked_at timestamptz,
    PRIMARY KEY (initiative_id, principal_id)
);
-- The inbox_positions row lock serializes each recipient feed through transaction commit.
-- Unlike a standalone sequence, an allocated but uncommitted position cannot
-- be overtaken by a later committed delivery for the same recipient and scope.
CREATE TABLE work.inbox_positions (
    initiative_id uuid NOT NULL,
    recipient_id uuid NOT NULL,
    last_position bigint NOT NULL DEFAULT 0 CHECK (last_position >= 0),
    PRIMARY KEY (initiative_id, recipient_id),
    FOREIGN KEY (initiative_id, recipient_id) REFERENCES work.collaboration_grants ON DELETE CASCADE
);
CREATE TABLE work.inbox_deliveries (
    delivery_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    initiative_id uuid NOT NULL,
    recipient_id uuid NOT NULL,
    position bigint NOT NULL CHECK (position > 0),
    activity_id uuid NOT NULL REFERENCES work.initiative_activities,
    sender_id uuid NOT NULL REFERENCES docplane.principals,
    sender_role text NOT NULL,
    envelope jsonb NOT NULL,
    signature text NOT NULL,
    acknowledged_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (initiative_id, recipient_id) REFERENCES work.collaboration_grants,
    UNIQUE (initiative_id, recipient_id, position),
    UNIQUE (activity_id, recipient_id)
);
