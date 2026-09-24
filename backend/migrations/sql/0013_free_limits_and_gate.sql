-- 0013 — what the free plan gets, and the channels everyone has to join.
--
-- The limit machinery already existed and is edited from the admin panel; this only
-- changes the numbers and adds one the code did not have yet ("library": how many
-- tracks a free listener can keep). Premium stays unlimited — -1 means no ceiling.
UPDATE plans
   SET limits = '{"channels": 1, "playlists": 2, "daily_plays": 40, "library": 200,
                  "download": false}'::jsonb
 WHERE code = 'free';

UPDATE plans
   SET limits = limits || '{"library": -1}'::jsonb
 WHERE code <> 'free';

-- Channels a user must be a member of before the app opens. Empty list = no gate,
-- which is how every existing installation stays exactly as it was.
--
-- The bot must be an administrator in each of these: Telegram only answers
-- getChatMember for a chat the bot itself is in. A username that fails that check is
-- reported in the admin panel rather than locking everybody out.
INSERT INTO settings (key, value) VALUES ('required_channels', '[]')
ON CONFLICT (key) DO NOTHING;
