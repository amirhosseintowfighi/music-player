"""web-preview crawler

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-19
"""

from migrations.sqlrun import run_sql, run_sql_file

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_sql_file("0004_crawler.sql")


def downgrade() -> None:
    # Rows created by the crawler have no file_unique_id, so restoring NOT NULL would
    # fail on them: they are dropped, which is what going back to MTProto-only means.
    run_sql(
        """
        DELETE FROM tracks WHERE file_unique_id IS NULL;
        DROP TABLE IF EXISTS channel_candidates;
        DELETE FROM feature_flags
         WHERE key IN ('indexing_source', 'crawler_enabled', 'lazy_resolve');
        DROP INDEX IF EXISTS tracks_unresolved;
        DROP INDEX IF EXISTS tracks_source_key;
        DROP INDEX IF EXISTS tracks_file_unique_id;
        ALTER TABLE tracks DROP CONSTRAINT IF EXISTS tracks_identity_present;
        ALTER TABLE tracks ALTER COLUMN file_unique_id SET NOT NULL;
        ALTER TABLE tracks ADD CONSTRAINT tracks_file_unique_id_key UNIQUE (file_unique_id);
        ALTER TABLE tracks
            DROP COLUMN IF EXISTS resolved_at,
            DROP COLUMN IF EXISTS resolve_attempts,
            DROP COLUMN IF EXISTS resolve_status,
            DROP COLUMN IF EXISTS cdn_url_fetched_at,
            DROP COLUMN IF EXISTS cdn_url,
            DROP COLUMN IF EXISTS source_key;
        DROP INDEX IF EXISTS channels_crawl_due;
        ALTER TABLE channels
            DROP COLUMN IF EXISTS crawl_error,
            DROP COLUMN IF EXISTS crawl_status,
            DROP COLUMN IF EXISTS crawl_interval_sec,
            DROP COLUMN IF EXISTS next_crawl_at,
            DROP COLUMN IF EXISTS last_crawl_at,
            DROP COLUMN IF EXISTS newest_crawled_msg_id,
            DROP COLUMN IF EXISTS oldest_crawled_msg_id,
            DROP COLUMN IF EXISTS preview_available,
            DROP COLUMN IF EXISTS source_type;
        """
    )
