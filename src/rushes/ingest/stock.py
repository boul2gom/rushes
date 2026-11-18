"""Stock of an account, in days of posts (task 10b): what is ready, plus what is still to cut."""

import math

from sqlalchemy import text
from sqlalchemy.orm import Session

from rushes.config import Config

# Unpublished posts, tests included; the showcase of a prospect subject (task 27) does not count.
# A post reaches its episode through its segment or its compilation (a theme compilation may have
# none: it counts, the doubt keeps the stock high).
_POSTS = text(
    """
    SELECT count(*) FROM posts p
    LEFT JOIN segments s ON s.id = p.segment_id
    LEFT JOIN compilations c ON c.id = p.compilation_id
    LEFT JOIN episodes e ON e.id = COALESCE(s.episode_id, c.episode_id)
    LEFT JOIN series sr ON sr.id = e.series_id
    LEFT JOIN creators cr ON cr.id = e.creator_id
    WHERE p.account_id = :account AND p.status IN ('planned', 'rendered', 'ready')
      AND NOT (COALESCE(p.is_test, 0) AND COALESCE(sr.origin, cr.origin, 'config') = 'prospect')
    """
)

# Episodes still to cut: a community episode is a signal and is never cut (R15); a `split` parent
# is counted by its children; an episode with a part is counted by its posts.
_UNCUT_MS = text(
    """
    SELECT COALESCE(SUM(e.duration_ms), 0) FROM episodes e
    WHERE e.owner_account_id = :account AND e.provenance <> 'community'
      AND e.status IN ('imported', 'cleaned', 'analyzed')
      AND NOT EXISTS (
          SELECT 1 FROM segments s WHERE s.episode_id = e.id AND s.kind = 'part'
      )
    """
)


def account_posts_per_day(config: Config, account_id: str) -> float:
    """Posts a day of the account's platform; 0 when accounts.yaml does not list the platform."""
    return config.accounts.posts_per_day.get(config.account(account_id).platform, 0.0)


def stock_days(
    session: Session, config: Config, account_id: str, *, est_post_s: int | None = None
) -> float:
    """Days of posts the account can publish without any new download.

    `est_post_s` defaults to `settings.ingest.est_post_s`. An account that posts nothing a day
    (or whose platform has no rate) has an endless stock: nothing is downloaded for it.
    """
    per_day = account_posts_per_day(config, account_id)
    if per_day <= 0:
        return math.inf
    seconds = config.settings.ingest.est_post_s if est_post_s is None else est_post_s
    session.flush()  # raw SQL below: the pending rows of this session must be visible
    posts = int(session.scalar(_POSTS, {"account": account_id}) or 0)
    uncut_ms = int(session.scalar(_UNCUT_MS, {"account": account_id}) or 0)
    return (posts + uncut_ms / 1000 / seconds) / per_day
