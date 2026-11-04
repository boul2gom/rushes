"""Typer CLI. Planned commands exist but are empty until their task (see docs/spec.md, plan)."""

import traceback
from collections.abc import Callable
from importlib.metadata import version as package_version
from typing import Annotated

import typer

from rushes import ui
from rushes.config import Config, ConfigError, load_config
from rushes.paths import Paths

EXIT_NOT_IMPLEMENTED = 1
EXIT_INVALID_CONFIG = 2

app = typer.Typer(
    name="rushes",
    help="Pipeline de vidéos courtes : cartoons et créateurs, pour TikTok et Instagram.",
    no_args_is_help=True,
    add_completion=False,
)


class _State:
    verbose = False
    plain = False
    json = False


state = _State()


def _version(value: bool) -> None:
    if value:
        ui.info(f"rushes {package_version('rushes')}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Affiche la version."),
    ] = False,
    plain: Annotated[bool, typer.Option("--plain", help="Sortie texte simple.")] = False,
    json: Annotated[bool, typer.Option("--json", help="Sortie JSON ligne par ligne.")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", help="Trace complète des erreurs.")] = False,
) -> None:
    state.plain, state.json, state.verbose = plain, json, verbose


def _not_implemented(task: int) -> None:
    ui.error(f"commande non implémentée (tâche {task})")
    raise typer.Exit(EXIT_NOT_IMPLEMENTED)


def _plain_stub(task: int) -> Callable[[], None]:
    def command() -> None:
        _not_implemented(task)

    return command


def _pipeline_stub(task: int) -> Callable[..., None]:
    def command(
        series: Annotated[str | None, typer.Option("--series", help="Limiter à une série.")] = None,
        creator: Annotated[
            str | None, typer.Option("--creator", help="Limiter à un créateur.")
        ] = None,
        episode: Annotated[
            str | None, typer.Option("--episode", help="Limiter à un épisode.")
        ] = None,
        account: Annotated[
            str | None, typer.Option("--account", help="Limiter à un compte.")
        ] = None,
        limit: Annotated[
            int | None, typer.Option("--limit", help="Nombre maximum d'éléments.")
        ] = None,
        force: Annotated[
            bool, typer.Option("--force", help="Refaire ce qui est déjà fait.")
        ] = False,
    ) -> None:
        _not_implemented(task)

    return command


def _group(name: str, help_text: str) -> typer.Typer:
    group = typer.Typer(help=help_text, no_args_is_help=True)
    app.add_typer(group, name=name)
    return group


# --- top-level pipeline commands -----------------------------------------------------------------

app.command("run", help="Enchaîne tout ce qui est prêt.")(_pipeline_stub(8))
app.command("status", help="Derniers runs, files, alertes et espace disque.")(_plain_stub(2))
app.command("ingest", help="Télécharge et enregistre les épisodes.")(_pipeline_stub(4))
app.command("clean", help="Retire génériques, cartons, noirs et silences en bordure.")(
    _pipeline_stub(4)
)
app.command("analyze", help="Plans, audio, parole et index des scènes.")(_pipeline_stub(5))
app.command("cut", help="Découpe en parties et best-of.")(_pipeline_stub(6))
app.command("approve", help="Approbation automatique des passages.")(_pipeline_stub(15))
app.command("assign", help="Attribue les passages approuvés au compte propriétaire.")(
    _pipeline_stub(8)
)
app.command("render", help="Rend les posts (9:16, bandeau, sous-titres, loudnorm).")(
    _pipeline_stub(7)
)
app.command("meta", help="Légendes, hashtags et couvertures.")(_pipeline_stub(8))
app.command("package", help="Prépare outbox/ et INDEX.html.")(_pipeline_stub(8))

# --- groups ---------------------------------------------------------------------------------

discover = _group("discover", "Découverte de vidéos sources.")
discover.command("youtube", help="Métadonnées, heatmap, commentaires et score.")(_pipeline_stub(10))

market = _group("market", "Étude de marché TikTok.")
market.command("collect", help="Collecte le panel de comptes.")(_pipeline_stub(31))
market.command("analyze", help="Hashtags qui surperforment.")(_pipeline_stub(31))

compile_ = _group("compile", "Compilations thématiques.")
compile_.command("themes", help="Compose les compilations par thème.")(_pipeline_stub(30))

hooks = _group("hooks", "Accroches.")
hooks.command("propose", help="Propose 3 accroches par passage.")(_pipeline_stub(14))

queue = _group("queue", "File de publication.")
queue.command("rank", help="Classe les posts par compte.")(_pipeline_stub(16))

publish = _group("publish", "Publication (Postiz).")
publish.command("push", help="Crée les brouillons Postiz.")(_pipeline_stub(19))
publish.command("sync", help="Synchronise les statuts depuis Postiz.")(_pipeline_stub(19))

stats = _group("stats", "Statistiques des comptes.")
stats.command("collect", help="Collecte les statistiques publiques.")(_pipeline_stub(17))

posts = _group("posts", "Suivi des posts.")
posts.command("detect-published", help="Détecte les posts publiés.")(_pipeline_stub(17))

creators = _group("creators", "Verticale créateurs.")
creators.command("prospect", help="Prospection hebdomadaire.")(_pipeline_stub(27))
creators.command("snapshot", help="Instantanés des chaînes.")(_pipeline_stub(27))
creators.command("backfill", help="Classe les anciennes vidéos.")(_pipeline_stub(28))
creators.command("watch", help="Détecte les nouvelles vidéos.")(_pipeline_stub(28))
creators.command("express", help="Voie express pour une nouvelle vidéo.")(_pipeline_stub(28))
creators.command("revisit", help="Second passage à 24 h et 72 h.")(_pipeline_stub(28))

review = _group("review", "Revue facultative.")
review.command("build", help="Génère la page de revue.")(_pipeline_stub(32))
review.command("apply", help="Applique les décisions exportées.")(_pipeline_stub(32))

vendor = _group("vendor", "Code tiers.")
vendor.command("check", help="Signale les évolutions des projets suivis.")(_plain_stub(21))

# --- tooling (not in the spec's command list) -----------------------------------------------

config = _group("config", "Configuration.")
db = _group("db", "Base de données.")


def _load() -> tuple[Paths, Config]:
    paths = Paths.from_env()
    try:
        return paths, load_config(paths.config_dir)
    except ConfigError as exc:
        ui.error_lines(
            f"configuration invalide ({paths.config_dir})",
            [str(issue) for issue in exc.issues],
            hint="Corrige les fichiers indiqués puis relance `rushes config check`.",
        )
        if state.verbose:
            ui.info(traceback.format_exc())
        raise typer.Exit(EXIT_INVALID_CONFIG) from exc


@config.command("check", help="Valide la configuration et affiche les avertissements.")
def config_check() -> None:
    _, cfg = _load()
    for warning in cfg.warnings:
        ui.warn(warning)
    ui.info(
        f"✓ configuration valide : {len(cfg.accounts.brands)} marques, "
        f"{len(cfg.accounts.accounts)} comptes, {len(cfg.series)} séries, "
        f"{len(cfg.creators)} créateurs"
    )


@db.command("upgrade", help="Applique les migrations et recharge le miroir de la config.")
def db_upgrade() -> None:
    from rushes.db.engine import make_engine
    from rushes.db.migrate import current_revision, upgrade
    from rushes.db.sync import sync_config

    paths, cfg = _load()
    upgrade(paths.db)
    engine = make_engine(paths.db)
    try:
        for warning in sync_config(engine, cfg):
            ui.warn(warning)
    finally:
        engine.dispose()
    ui.info(f"✓ base à jour (révision {current_revision(paths.db)})")
