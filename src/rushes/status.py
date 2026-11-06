"""`rushes status`: last runs, queue per account, alerts and disk space, in one screen."""

import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from rushes.context import Context
from rushes.ui import Table

DISK_ALERT_PERCENT = 85  # ntfy alert threshold of the spec


def _duration(started: str, ended: str | None) -> str:
    if not ended:
        return "—"
    try:
        seconds = (datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds()
    except ValueError:
        return "—"
    return f"{seconds:.0f} s".replace(".", ",")


def _gigabytes(size: int) -> str:
    return f"{size / 1e9:.1f} Go".replace(".", ",")


def _existing_parent(path: Path) -> Path:
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def _last_runs(ctx: Context) -> list[tuple[str, str, str | None, str, int | None]]:
    with ctx.engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT command, started_at, ended_at, status, exit_code FROM runs "
                "WHERE id IN (SELECT max(id) FROM runs GROUP BY command) "
                "ORDER BY started_at DESC, id DESC"
            )
        ).all()
    return [(r[0], r[1], r[2], r[3], r[4]) for r in rows]


def collect_status(ctx: Context) -> list[Table]:
    runs = _last_runs(ctx)
    last_runs = Table(
        "Derniers runs",
        ["Commande", "Début", "Statut", "Code", "Durée"],
        [
            [
                command,
                started,
                status,
                "—" if code is None else str(code),
                _duration(started, ended),
            ]
            for command, started, ended, status, code in runs
        ],
    )

    with ctx.engine.connect() as conn:
        queue_rows = conn.execute(
            text(
                "SELECT a.id, "
                "coalesce(sum(p.status='ready' AND p.is_test=0), 0), "
                "coalesce(sum(p.status='ready' AND p.is_test=1), 0), "
                "coalesce(sum(p.status IN ('planned','rendered')), 0) "
                "FROM accounts a LEFT JOIN posts p ON p.account_id = a.id "
                "GROUP BY a.id ORDER BY a.id"
            )
        ).all()
    queue = Table(
        "File par compte",
        ["Compte", "Prêts", "Vitrine", "En cours"],
        [[str(r[0]), str(r[1]), str(r[2]), str(r[3])] for r in queue_rows],
    )

    usage = shutil.disk_usage(_existing_parent(ctx.paths.data_dir))
    percent = round(usage.used / usage.total * 100) if usage.total else 0
    alerts: list[list[str]] = [
        ["✗", f"{command} : échec (code {'?' if code is None else code}) le {started}"]
        for command, started, _ended, status, code in runs
        if status == "failed"
    ]
    if percent > DISK_ALERT_PERCENT:
        alerts.append(["⚠", f"disque occupé à {percent} % (seuil {DISK_ALERT_PERCENT} %)"])
    alert_table = Table("Alertes", ["", "Message"], alerts or [["✓", "Aucune alerte"]])

    disk = Table(
        "Disque",
        ["Dossier", "Utilisé", "Libre", "Total", "Occupation"],
        [
            [
                str(ctx.paths.data_dir),
                _gigabytes(usage.used),
                _gigabytes(usage.free),
                _gigabytes(usage.total),
                f"{percent} %",
            ]
        ],
    )
    return [last_runs, queue, alert_table, disk]
