"""listening features: folders, artist follows, lyrics, blends, private session

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-27
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0015_listening_features.sql")


def downgrade() -> None:
    run_sql(
        "DELETE FROM notifications WHERE kind = 'new_release';"
        "ALTER TABLE notifications DROP CONSTRAINT IF EXISTS notifications_kind_check;"
        "ALTER TABLE notifications ADD CONSTRAINT notifications_kind_check CHECK (kind IN"
        " ('new_tracks','digest','sub_expiry','discover_ready','payment','system'));"
        "DROP TABLE IF EXISTS track_progress;"
        "ALTER TABLE users DROP COLUMN IF EXISTS private_until;"
        "DROP TABLE IF EXISTS blends;"
        "DROP TABLE IF EXISTS track_lyrics;"
        "DROP TABLE IF EXISTS hidden_tracks;"
        "ALTER TABLE artists DROP COLUMN IF EXISTS lastfm_top,"
        " DROP COLUMN IF EXISTS lastfm_fetched_at;"
        "DROP TABLE IF EXISTS artist_follows;"
        "ALTER TABLE playlists DROP COLUMN IF EXISTS folder_id;"
        "DROP TABLE IF EXISTS playlist_folders;"
        "DELETE FROM playlists WHERE kind IN ('release_radar','blend','daylist');"
        "ALTER TABLE playlists DROP CONSTRAINT IF EXISTS playlists_kind_check;"
        "ALTER TABLE playlists ADD CONSTRAINT playlists_kind_check CHECK (kind IN"
        " ('manual','smart_ai','discover_weekly','daily_mix','radio'));"
    )
