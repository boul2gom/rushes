"""Owner account of a new episode (R4, R44): a candidate account with the least stock."""

from datetime import date

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from rushes.config import Config, RotationPeriod
from rushes.errors import RushesError

# An episode is "in progress" when it has no post at all, or at least one post that is not
# published (a post reaches an episode through its segment or its compilation).
_STOCK = text(
    """
    SELECT e.owner_account_id, count(*) FROM episodes e
    WHERE e.owner_account_id IN :accounts AND (
        NOT EXISTS (
            SELECT 1 FROM posts p
            LEFT JOIN segments s ON s.id = p.segment_id
            LEFT JOIN compilations c ON c.id = p.compilation_id
            WHERE COALESCE(s.episode_id, c.episode_id) = e.id
        ) OR EXISTS (
            SELECT 1 FROM posts p
            LEFT JOIN segments s ON s.id = p.segment_id
            LEFT JOIN compilations c ON c.id = p.compilation_id
            WHERE COALESCE(s.episode_id, c.episode_id) = e.id AND p.status <> 'published'
        )
    )
    GROUP BY e.owner_account_id
    """
).bindparams(bindparam("accounts", expanding=True))


def _covering(period: RotationPeriod, today: date) -> bool:
    return period.from_ <= today <= period.to


def _series_brands(config: Config, series_id: str, today: date) -> list[str]:
    """Brands that a rotation period covering `today` gives the series to (R4).

    With no period covering `today`, every brand of the cartoons vertical is a candidate (the
    generated rotation arrives with task 16a). A covering period that lists nothing for the
    series leaves no candidate: R4 forbids any account from receiving it.
    """
    covering = [p for p in config.accounts.rotation if _covering(p, today)]
    if not covering:
        return sorted(b.id for b in config.accounts.brands if b.vertical == "cartoons")
    brands = sorted(
        {
            brand
            for p in covering
            for brand, series in p.series_by_brand.items()
            if series_id in series
        }
    )
    if not brands:
        raise RushesError(
            f"{series_id} n'est pas dans la rotation en cours",
            hint="Ajouter la série à la rotation en cours (accounts.yaml).",
        )
    return brands


def _creator_brands(config: Config, creator_id: str) -> list[str]:
    return sorted(
        brand
        for brand, creators in config.accounts.creators_by_brand.items()
        if creator_id in creators
    )


def choose_owner(
    session: Session,
    config: Config,
    *,
    series_id: str | None,
    creator_id: str | None,
    today: date,
) -> str:
    """The candidate account with the fewest episodes in progress; a tie goes to the smallest id."""
    if (series_id is None) == (creator_id is None):
        raise ValueError("une série ou un créateur, pas les deux ni aucun")
    if series_id is not None:
        brands, subject = _series_brands(config, series_id, today), series_id
    else:
        assert creator_id is not None
        brands, subject = _creator_brands(config, creator_id), creator_id
    accounts = sorted(a.id for a in config.accounts.accounts if a.brand in brands)
    if not accounts:
        raise RushesError(
            f"aucun compte candidat pour {subject}",
            hint="Lister le créateur dans creators_by_brand (accounts.yaml).",
        )
    rows = session.execute(_STOCK, {"accounts": accounts}).all()
    stock = {str(account): int(count) for account, count in rows}
    return min(accounts, key=lambda account: (stock.get(account, 0), account))
