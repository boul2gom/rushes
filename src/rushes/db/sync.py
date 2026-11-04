"""Mirror the YAML config into brands/accounts/series/creators/rights (upsert, never delete)."""

from datetime import UTC, datetime

from sqlalchemy import Connection, Engine, text

from rushes.config import Config, Rights


def _rights_row(rights: Rights | None, *, cleared: bool) -> dict[str, object]:
    """Config block when present, else `granted` for a licensed/authorized subject."""
    if rights is not None:
        return {
            "holder": rights.holder,
            "status": rights.status,
            "requested_at": rights.requested_at.isoformat() if rights.requested_at else None,
        }
    return {"holder": None, "status": "granted" if cleared else "to_request", "requested_at": None}


def _upsert_rights(
    conn: Connection, kind: str, subject_id: str, row: dict[str, object], now: str
) -> None:
    # config is the source of truth for rights.status; priority and showcase_posts are never
    # overwritten (they belong to the rights step)
    conn.execute(
        text(
            "INSERT INTO rights(subject_kind, subject_id, holder, status, requested_at, "
            "updated_at) VALUES (:kind, :id, :holder, :status, :requested_at, :now) "
            "ON CONFLICT(subject_kind, subject_id) DO UPDATE SET holder=excluded.holder, "
            "status=excluded.status, requested_at=excluded.requested_at, "
            "updated_at=excluded.updated_at "
            "WHERE rights.holder IS NOT excluded.holder OR rights.status IS NOT excluded.status "
            "OR rights.requested_at IS NOT excluded.requested_at"
        ),
        {"kind": kind, "id": subject_id, "now": now, **row},
    )


def sync_config(engine: Engine, config: Config) -> list[str]:
    """Reload the config mirrors. Returns warnings; nothing is ever deleted."""
    warnings: list[str] = []
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with engine.begin() as conn:
        for b in config.accounts.brands:
            conn.execute(
                text(
                    "INSERT INTO brands(id, vertical, language, timezone) "
                    "VALUES (:id, :vertical, :language, :timezone) "
                    "ON CONFLICT(id) DO UPDATE SET vertical=excluded.vertical, "
                    "language=excluded.language, timezone=excluded.timezone"
                ),
                b.model_dump(),
            )
        for a in config.accounts.accounts:
            conn.execute(
                text(
                    "INSERT INTO accounts(id, brand_id, platform, handle, originality) "
                    "VALUES (:id, :brand, :platform, :handle, :originality) "
                    "ON CONFLICT(id) DO UPDATE SET brand_id=excluded.brand_id, "
                    "platform=excluded.platform, handle=excluded.handle, "
                    "originality=excluded.originality"
                ),
                a.model_dump(),
            )
        for s in config.series:
            conn.execute(
                text(
                    "INSERT INTO series(id, title, audio, licensed, origin, status) "
                    "VALUES (:id, :title, :audio, :licensed, 'config', 'active') "
                    "ON CONFLICT(id) DO UPDATE SET title=excluded.title, "
                    "audio=excluded.audio, licensed=excluded.licensed, "
                    "origin=excluded.origin, status=excluded.status"
                ),
                {"id": s.id, "title": s.title, "audio": s.audio, "licensed": s.licensed},
            )
            _upsert_rights(conn, "series", s.id, _rights_row(s.rights, cleared=s.licensed), now)
        for c in config.creators:
            conn.execute(
                text(
                    "INSERT INTO creators(id, language, channel_id, authorized, origin, status) "
                    "VALUES (:id, :language, :channel_id, :authorized, 'config', 'active') "
                    "ON CONFLICT(id) DO UPDATE SET language=excluded.language, "
                    "channel_id=excluded.channel_id, authorized=excluded.authorized, "
                    "origin=excluded.origin, status=excluded.status"
                ),
                {
                    "id": c.id,
                    "language": c.language,
                    "channel_id": c.youtube_channel,
                    "authorized": c.authorized,
                },
            )
            _upsert_rights(conn, "creator", c.id, _rights_row(c.rights, cleared=c.authorized), now)

        for table, ids in (
            ("series", {s.id for s in config.series}),
            ("creators", {c.id for c in config.creators}),
        ):
            column = "series_id" if table == "series" else "creator_id"
            rows = conn.execute(text(f"SELECT id FROM {table}")).scalars().all()
            for row_id in rows:
                if row_id in ids:
                    continue
                used = conn.execute(
                    text(f"SELECT count(*) FROM episodes WHERE {column} = :id"),
                    {"id": row_id},
                ).scalar_one()
                if used:
                    warnings.append(
                        f"{table[:-1] if table == 'series' else 'creator'} {row_id} a disparu "
                        f"de la config mais possède {used} épisode(s) : conservé"
                    )
    return warnings
