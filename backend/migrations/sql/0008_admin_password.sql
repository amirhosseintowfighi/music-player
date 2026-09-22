-- 0008 — a password login for the admin panel, next to the Telegram one.
--
-- The Login Widget ties the panel to one bot *and* one domain (BotFather accepts a
-- single one per bot), which is fine until you need to sign in from a machine where
-- that bot is not reachable. A username and a password have neither constraint.
--
-- Both columns are nullable: an admin created by `add-admin` has no password until
-- someone sets one, and an admin who only ever uses Telegram never needs one.
ALTER TABLE admin_users
    ADD COLUMN IF NOT EXISTS login_username text,
    ADD COLUMN IF NOT EXISTS password_hash  text,
    ADD COLUMN IF NOT EXISTS password_set_at timestamptz;

-- Case-insensitive, because nobody remembers whether they capitalised it.
CREATE UNIQUE INDEX IF NOT EXISTS admin_users_login_username_key
    ON admin_users (lower(login_username))
    WHERE login_username IS NOT NULL;
