"""Typer CLI. Planned commands exist but are empty until their task (see docs/spec.md, plan)."""

import functools
import traceback
from collections.abc import Callable, Sequence
from typing import Annotated, Any, NoReturn

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session
from typer.exceptions import TyperException

from rushes import steps
from rushes.analyze.index import search_scenes
from rushes.config import ConfigError, load_config
from rushes.context import Context, Filters, app_version, build_context
from rushes.db.models import Scene, SceneVector
from rushes.errors import EXIT_ERROR, EXIT_INVALID_CONFIG, RushesError
from rushes.ids import episode_key, parse_key
from rushes.lock import heavy_lock
from rushes.models import fetch as model_fetch
from rushes.models.clip import MODEL_ID
from rushes.net.base import Http
from rushes.net.http import UrllibHttp
from rushes.paths import Paths
from rushes.runner import Step, run_pipeline
from rushes.status import collect_status
from rushes.ui import Emitter, Message, Table, emit_trace, format_duration, make_emitter

EXIT_NOT_IMPLEMENTED = EXIT_ERROR

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
    ctx: Context | None = None  # the context of the running command (closed by `_guarded`)
    console: Emitter | None = None  # emitter used before a context exists


state = _State()


def _ui() -> Emitter:
    """The running command's emitter (console + log), else a console-only one."""
    if state.ctx is not None:
        return state.ctx.ui
    if state.console is None:
        state.console = make_emitter(plain=state.plain, json=state.json, verbose=state.verbose)
    return state.console


def _open() -> Context:
    """Build the context (config, migrations, mirrors, log) for the running command."""
    ctx = build_context(plain=state.plain, json=state.json, verbose=state.verbose)
    state.ctx = ctx
    return ctx


def _report_config_error(exc: ConfigError, config_dir: str) -> None:
    ui = _ui()
    ui.emit(Message("error", f"configuration invalide ({config_dir})"))
    issues = [str(issue) for issue in exc.issues]
    for position, issue in enumerate(issues, start=1):
        hint = "Corrige les fichiers indiqués puis relance `rushes config check`."
        ui.emit(Message("error", f"  {issue}", hint=hint if position == len(issues) else None))


def _guarded[**P](command: Callable[P, None]) -> Callable[P, None]:
    """Turn errors into messages and exit codes: ConfigError 2, RushesError its code, else 1."""

    @functools.wraps(command)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        try:
            command(*args, **kwargs)
        except typer.Exit:
            raise
        except ConfigError as exc:
            _report_config_error(exc, str(Paths.from_env().config_dir))
            emit_trace(_ui(), traceback.format_exc())
            raise typer.Exit(EXIT_INVALID_CONFIG) from exc
        except TyperException as exc:  # usage error raised inside a command
            _ui().emit(Message("error", str(getattr(exc, "message", "") or exc)))
            raise typer.Exit(EXIT_ERROR) from exc
        except RushesError as exc:
            _ui().emit(Message("error", str(exc) or type(exc).__name__, hint=exc.hint))
            emit_trace(_ui(), traceback.format_exc())
            raise typer.Exit(exc.exit_code) from exc
        except Exception as exc:
            # a log file exists only once the context is open
            where = " ; la trace complète est dans data/logs/" if state.ctx is not None else ""
            _ui().emit(
                Message(
                    "error",
                    f"erreur inattendue : {type(exc).__name__}: {exc}",
                    hint=f"Relance avec --verbose{where}.",
                )
            )
            emit_trace(_ui(), traceback.format_exc())
            raise typer.Exit(EXIT_ERROR) from exc
        finally:
            if state.ctx is not None:
                state.ctx.close()
            elif state.console is not None:
                state.console.close()
            state.ctx = state.console = None

    return wrapper


def _version(value: bool) -> None:
    if value:
        make_emitter(plain=True, json=False, verbose=False).emit(
            Message("info", f"rushes {app_version()}")
        )
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


# --- pipeline plumbing ---------------------------------------------------------------------------


def _episode_id(value: str | None) -> int | None:
    """`--episode` takes 45 or e_000045."""
    if value is None:
        return None
    try:
        if value.isdigit():
            return int(value)
        kind, number = parse_key(value)
    except ValueError as exc:
        raise typer.BadParameter(f"épisode invalide : {value!r} (45 ou e_000045)") from exc
    if kind != "e":
        raise typer.BadParameter(f"{value!r} n'est pas un épisode (e_000045)")
    return number


def make_filters(
    *,
    series: str | None = None,
    creator: str | None = None,
    episode: str | None = None,
    account: str | None = None,
    limit: int | None = None,
    force: bool = False,
) -> Filters:
    """The common command-line options as `Filters`."""
    return Filters(
        series=series,
        creator=creator,
        episode=_episode_id(episode),
        account=account,
        limit=limit,
        force=force,
    )


@_guarded
def execute(
    command: str, step_names: Sequence[str], filters: Filters, *, wait: bool = True
) -> None:
    """Build the context, run the named steps in order and exit with the pipeline's code."""
    ctx = _open()
    pipeline: list[Step[Any]] = []
    for name in step_names:
        if name not in steps.STEPS:
            raise RushesError(f"étape inconnue : {name}", hint="Voir `rushes --help`.")
        pipeline.append(steps.STEPS[name]())
    raise typer.Exit(run_pipeline(ctx, pipeline, filters, command=command, wait=wait))


def _not_implemented(task: str) -> NoReturn:
    _ui().emit(Message("error", f"commande non implémentée (tâche {task})"))
    raise typer.Exit(EXIT_NOT_IMPLEMENTED)


def _plain_stub(task: str) -> Callable[[], None]:
    @_guarded
    def command() -> None:
        _not_implemented(task)

    return command


def _pipeline_stub(task: str) -> Callable[..., None]:
    @_guarded
    def command(
        series: Annotated[str | None, typer.Option("--series", help="Limiter à une série.")] = None,
        creator: Annotated[
            str | None, typer.Option("--creator", help="Limiter à un créateur.")
        ] = None,
        episode: Annotated[
            str | None, typer.Option("--episode", help="Limiter à un épisode (45 ou e_000045).")
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
        make_filters(
            series=series,
            creator=creator,
            episode=episode,
            account=account,
            limit=limit,
            force=force,
        )
        _not_implemented(task)

    return command


def _pipeline_command(command: str, step_names: Sequence[str]) -> Callable[..., None]:
    """A top-level command that runs the named steps with the common filter options."""

    @_guarded
    def run_command(
        series: Annotated[str | None, typer.Option("--series", help="Limiter à une série.")] = None,
        creator: Annotated[
            str | None, typer.Option("--creator", help="Limiter à un créateur.")
        ] = None,
        episode: Annotated[
            str | None, typer.Option("--episode", help="Limiter à un épisode (45 ou e_000045).")
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
        filters = make_filters(
            series=series,
            creator=creator,
            episode=episode,
            account=account,
            limit=limit,
            force=force,
        )
        execute(command, step_names, filters)

    return run_command


def _group(name: str, help_text: str) -> typer.Typer:
    group = typer.Typer(help=help_text, no_args_is_help=True)
    app.add_typer(group, name=name)
    return group


@_guarded
def status() -> None:
    ctx = _open()
    for table in collect_status(ctx):
        ctx.ui.emit(table)


# --- top-level pipeline commands -----------------------------------------------------------------

app.command("status", help="Derniers runs, files, alertes et espace disque.")(status)

app.command("run", help="Enchaîne tout ce qui est prêt.")(_pipeline_command("run", steps.RUN_ORDER))
app.command("ingest", help="Télécharge et enregistre les épisodes.")(
    _pipeline_command("ingest", ("ingest",))
)
app.command("clean", help="Retire génériques, cartons, noirs et silences en bordure.")(
    _pipeline_command("clean", ("clean",))
)
app.command("analyze", help="Plans, audio, parole et index des scènes.")(
    _pipeline_command("analyze", ("analyze", "vision", "sounds"))
)
app.command("cut", help="Découpe en parties et best-of.")(_pipeline_command("cut", ("cut",)))
app.command("approve", help="Approbation automatique des passages.")(
    _pipeline_command("approve", ("approve",))
)
app.command("assign", help="Attribue les passages approuvés au compte propriétaire.")(
    _pipeline_command("assign", ("assign",))
)
app.command("render", help="Rend les posts (9:16, bandeau, sous-titres, loudnorm).")(
    _pipeline_command("render", ("render",))
)
app.command("meta", help="Légendes, hashtags et couvertures.")(_pipeline_command("meta", ("meta",)))
app.command("package", help="Prépare outbox/ et INDEX.html.")(
    _pipeline_command("package", ("package",))
)
app.command("serve", help="Sert la page de suivi et le tableau de bord.")(_plain_stub("36"))

# --- groups ---------------------------------------------------------------------------------

discover = _group("discover", "Découverte de vidéos sources.")
discover.command("youtube", help="Métadonnées, heatmap, commentaires et score.")(
    _pipeline_command("discover youtube", ("discover_youtube",))
)
discover.command("series", help="Séries en tendance et chaînes officielles.")(_plain_stub("38"))

models = _group("models", "Modèles locaux.")

scenes = _group("scenes", "Scènes indexées.")


@scenes.command("search", help="Retrouve des scènes par une description libre (en anglais).")
@_guarded
def scenes_search(
    query: Annotated[str, typer.Argument(help='Description, ex. "a banana".')],
    series: Annotated[str | None, typer.Option("--series", help="Limiter à une série.")] = None,
    account: Annotated[str | None, typer.Option("--account", help="Limiter à un compte.")] = None,
) -> None:
    ctx = _open()
    with heavy_lock(ctx.paths, wait=False):  # CLIP is heavy (R19)
        with Session(ctx.engine) as session:  # short: closed before the model loads
            anything = session.scalar(select(SceneVector.scene_id).limit(1)) is not None
            current = (
                session.scalar(
                    select(SceneVector.scene_id).where(SceneVector.model == MODEL_ID).limit(1)
                )
                is not None
            )
        if not anything or not current:
            what = (
                "Aucune scène indexée."
                if not anything
                else f"Aucune scène indexée avec le modèle actuel ({MODEL_ID})."
            )
            hint = "Lance `rushes analyze` : l'étape vision indexe les scènes (ou les refait)."
            ctx.ui.emit(Message("info", what, hint=hint))
            return
        encoder = ctx.models.clip()
        with Session(ctx.engine) as session:
            found = search_scenes(session, encoder, query, account_id=account, series_id=series)
            places = {
                row.id: (row.episode_id, row.start_ms)
                for row in session.execute(
                    select(Scene.id, Scene.episode_id, Scene.start_ms).where(
                        Scene.id.in_([scene_id for scene_id, _ in found])
                    )
                )
            }
    if not found:
        ctx.ui.emit(Message("info", "Aucune scène ne correspond à ces filtres."))
        return
    rows = [
        [
            str(scene_id),
            episode_key(places[scene_id][0]),
            format_duration(places[scene_id][1]),
            f"{cosine:.3f}",
        ]
        for scene_id, cosine in found
    ]
    ctx.ui.emit(Table("Scènes", ["Scène", "Épisode", "Début", "Cosinus"], rows))


rights = _group("rights", "Suivi des licences.")
rights.command("list", help="Tableau des licences et de leur priorité.")(_plain_stub("38"))

market = _group("market", "Étude de marché TikTok.")
market.command("collect", help="Collecte le panel de comptes.")(_pipeline_stub("31"))
market.command("analyze", help="Hashtags qui surperforment.")(_pipeline_stub("31"))

compile_ = _group("compile", "Compilations thématiques.")
compile_.command("themes", help="Compose les compilations par thème.")(_pipeline_stub("30"))

hooks = _group("hooks", "Accroches.")
hooks.command("propose", help="Propose 3 accroches par passage.")(_pipeline_stub("14"))

queue = _group("queue", "File de publication.")
queue.command("rank", help="Classe les posts par compte.")(_pipeline_stub("16b"))

stats = _group("stats", "Statistiques des comptes.")
stats.command("collect", help="Collecte les statistiques publiques.")(_pipeline_stub("17"))

posts = _group("posts", "Suivi des posts.")
posts.command("detect-published", help="Détecte les posts publiés.")(_pipeline_stub("17"))

creators = _group("creators", "Verticale créateurs.")
creators.command("prospect", help="Prospection hebdomadaire.")(_pipeline_stub("27"))
creators.command("snapshot", help="Instantanés des chaînes.")(_pipeline_stub("27"))
creators.command("backfill", help="Classe les anciennes vidéos.")(_pipeline_stub("28"))
creators.command("watch", help="Détecte les nouvelles vidéos.")(_pipeline_stub("28"))
creators.command("express", help="Voie express pour une nouvelle vidéo.")(_pipeline_stub("28"))
creators.command("revisit", help="Second passage à 24 h et 72 h.")(_pipeline_stub("28"))

review = _group("review", "Revue facultative.")
review.command("build", help="Génère la page de revue.")(_pipeline_stub("32"))
review.command("apply", help="Applique les décisions exportées.")(_pipeline_stub("32"))


def make_http() -> Http:
    """The network client of `models fetch` (tests replace it)."""
    return UrllibHttp()


@models.command("fetch", help="Télécharge les modèles une fois (sha256 vérifié).")
@_guarded
def models_fetch(
    only: Annotated[
        list[str] | None,
        typer.Option("--only", help="Un modèle seulement (répétable), ex. --only clip."),
    ] = None,
) -> None:
    ui = _ui()
    paths = Paths.from_env()
    chosen = tuple(only or ())
    done = model_fetch.fetch(
        make_http(),
        paths,
        only=chosen,
        progress=lambda name: ui.emit(Message("info", f"téléchargement de {name}…")),
    )
    names = dict.fromkeys(entry.name for entry in model_fetch.load_manifest())
    for name in names:
        if chosen and name not in chosen:
            continue
        ui.emit(
            Message("info", f"✓ {name} téléchargé" if name in done else f"✓ {name} déjà présent")
        )


@models.command("check", help="Vérifie la présence et l'empreinte des modèles (sans réseau).")
@_guarded
def models_check() -> None:
    ui = _ui()
    manifest = model_fetch.load_manifest()
    states = model_fetch.check(Paths.from_env(), manifest=manifest)
    ui.emit(
        Table(
            "Modèles",
            ["Modèle", "Fichier", "État"],
            [[entry.name, entry.file, states[entry.file]] for entry in manifest],
        )
    )
    bad = [entry for entry in manifest if states[entry.file] != "ok"]
    if bad:
        names = ", ".join(dict.fromkeys(entry.name for entry in bad))
        ui.emit(
            Message(
                "error",
                f"{len(bad)} fichier(s) absents ou incorrects : {names}",
                hint="Lance `rushes models fetch`.",
            )
        )
        raise typer.Exit(EXIT_ERROR)
    ui.emit(Message("info", f"✓ {len(manifest)} fichiers de modèles présents et conformes"))


# --- tooling (not in the spec's command list) -----------------------------------------------

config = _group("config", "Configuration.")
db = _group("db", "Base de données.")


@config.command("check", help="Valide la configuration et affiche les avertissements.")
@_guarded
def config_check() -> None:
    cfg = load_config(Paths.from_env().config_dir)
    ui = _ui()
    for warning in cfg.warnings:
        ui.emit(Message("warn", warning))
    ui.emit(
        Message(
            "info",
            f"✓ configuration valide : {len(cfg.accounts.brands)} marques, "
            f"{len(cfg.accounts.accounts)} comptes, {len(cfg.series)} séries, "
            f"{len(cfg.creators)} créateurs",
        )
    )


@db.command("upgrade", help="Applique les migrations et recharge le miroir de la config.")
@_guarded
def db_upgrade() -> None:
    from rushes.db.migrate import current_revision

    ctx = _open()  # migrates and reloads the mirrors; sync warnings are emitted on the way
    ctx.ui.emit(Message("info", f"✓ base à jour (révision {current_revision(ctx.paths.db)})"))
