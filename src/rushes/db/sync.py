"""Mirror the YAML config into brands/accounts/series/creators (upsert, never delete)."""

from sqlalchemy import Engine, text

from rushes.config import Config


def sync_config(engine: Engine, config: Config) -> list[str]:
    """Reload the config mirrors. Returns warnings; nothing is ever deleted."""
    warnings: list[str] = []
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
                    "INSERT INTO accounts(id, brand_id, platform, handle, publish_mode, "
                    "postiz_integration) VALUES (:id, :brand, :platform, :handle, "
                    ":publish_mode, :postiz_integration) "
                    "ON CONFLICT(id) DO UPDATE SET brand_id=excluded.brand_id, "
                    "platform=excluded.platform, handle=excluded.handle, "
                    "publish_mode=excluded.publish_mode, "
                    "postiz_integration=excluded.postiz_integration"
                ),
                a.model_dump(),
            )
        for s in config.series:
            conn.execute(
                text(
                    "INSERT INTO series(id, title, audio, licensed) "
                    "VALUES (:id, :title, :audio, :licensed) "
                    "ON CONFLICT(id) DO UPDATE SET title=excluded.title, "
                    "audio=excluded.audio, licensed=excluded.licensed"
                ),
                {"id": s.id, "title": s.title, "audio": s.audio, "licensed": s.licensed},
            )
        for c in config.creators:
            conn.execute(
                text(
                    "INSERT INTO creators(id, language, channel_id, authorized) "
                    "VALUES (:id, :language, :channel_id, :authorized) "
                    "ON CONFLICT(id) DO UPDATE SET language=excluded.language, "
                    "channel_id=excluded.channel_id, authorized=excluded.authorized"
                ),
                {
                    "id": c.id,
                    "language": c.language,
                    "channel_id": c.youtube_channel,
                    "authorized": c.authorized,
                },
            )

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
