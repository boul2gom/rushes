"""Owner account of a new episode (R4, R44): a candidate account with the smallest stock."""

from datetime import date

from sqlalchemy.orm import Session

from rushes.config import Config, RotationPeriod
from rushes.errors import RushesError
from rushes.ingest.stock import stock_days


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


def candidate_accounts(
    config: Config, *, series_id: str | None, creator_id: str | None, today: date
) -> list[str]:
    """Accounts that may own an episode of the series or creator (R4), sorted by id."""
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
    return accounts


def choose_owner(
    session: Session,
    config: Config,
    *,
    series_id: str | None,
    creator_id: str | None,
    today: date,
) -> str:
    """The candidate account with the smallest `stock_days`; a tie goes to the smallest id."""
    accounts = candidate_accounts(config, series_id=series_id, creator_id=creator_id, today=today)
    return min(accounts, key=lambda account: (stock_days(session, config, account), account))
