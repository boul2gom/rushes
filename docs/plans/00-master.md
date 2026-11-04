# rushes — Plan d'implémentation : document maître

> **Pour les agents qui exécutent le plan :** lire ce document, puis le fichier de la phase en cours
> (`phase-N-*.md`). Chaque tâche s'exécute avec `superpowers:subagent-driven-development` (recommandé) ou
> `superpowers:executing-plans`. Les étapes utilisent des cases `- [ ]`.

**But :** amener rushes de la tâche 1 (faite) à un logiciel prêt pour la production, entièrement terminé avant le
premier post.

**Architecture :** un paquet Python 3.15 unique, des étapes idempotentes qui lisent SQLite et des fichiers, écrivent
leurs fichiers puis la base dans une transaction ; FFmpeg pour toute la vidéo ; modèles locaux derrière des
interfaces, chargés un à la fois ; une image Docker qui sert à la fois aux minuteurs et à `rushes serve`.

**Pile :** uv, Typer, Rich, Pydantic v2, SQLAlchemy 2.1 + Alembic, yt-dlp (+ Deno, `yt-dlp-ejs`, curl-cffi),
FFmpeg 7.1, PySceneDetect 0.7.2, OpenCV contrib headless 5, open\_clip, Silero VAD, faster-whisper, sherpa-onnx,
llama-cpp-python, FastAPI + uvicorn, React + shadcn/ui + Tailwind v4 (page de `serve`). Reverse proxy : GoDoxy
(authentification PocketID et mise en veille du conteneur `serve`), géré par Louis.

**Spec :** `docs/spec.md` (référence unique) et `docs/decisions.md` (décisions datées). Les deux voyagent avec ce plan.

---

## 1. Comment ce plan est organisé

| Fichier | Contenu |
| --- | --- |
| `00-master.md` | Ce document : contraintes globales, ordre et dépendances, contrats transverses écrits en code, stratégie de test, matrices de couverture, définition de « prêt » |
| `phase-1-squelette.md` | Tâches 33, 42, 2, 3, 4, 5, 6, 7a, 7b, 8a, 8b, 9 |
| `phase-2-contenu.md` | Tâches 10a, 10b, 11, 12a, 12b, 12c, 13, 14, 15, 16a, 16b, 34, 35 |
| `phase-3-publication.md` | Tâches 36, 17, 18, 20, 37 |
| `phase-4-createurs.md` | Tâches 22, 24, 25, 26, 27, 28 |
| `phase-5-enrichissements.md` | Tâches 31, 30, 32, 38 |
| `phase-6-production.md` | Tâches 39, 40a, 40b, 41 |

- Les numéros sont ceux de la spec (traçabilité). Les tâches 19, 21, 23 et 29 sont supprimées ; 33 à 42 sont nouvelles.
  Les tâches 7, 8, 10, 12 et 16, trop grosses pour une session, sont découpées en 7a/7b, 8a/8b, 10a/10b,
  12a/12b/12c et 16a/16b ; la tâche de la spec est terminée quand toutes ses sous-tâches le sont.
- Chaque tâche d'un fichier de phase suit le format writing-plans : **Files**, **Interfaces** (consomme / produit,
  signatures exactes), étapes TDD (test qui échoue, exécution, code, exécution, proposition de commit).
- Un contrat défini ici n'est pas recopié dans les phases : la tâche renvoie à la section de ce document.
- Louis commite lui-même : chaque tâche se termine par des **noms de commits conventionnels proposés**, jamais par
  un `git commit`.

## 2. Contraintes globales

Chaque tâche les inclut implicitement. Valeurs copiées de la spec et de `decisions.md`.

- Python `>=3.15,<3.16` ; **uv uniquement** (`uv add`, `uv run`), jamais `pip` ni `python` nus.
- `onnxruntime` est écarté par `[tool.uv] override-dependencies = ["onnxruntime; python_version < '3.15'"]` ; aucun
  module n'importe `onnxruntime`. faster-whisper : `vad_filter=False`, segments Silero passés par `clip_timestamps`,
  jamais `BatchedInferencePipeline`.
- torch depuis l'index CPU sous Linux (`[[tool.uv.index]] name = "pytorch-cpu"`, marqueur `sys_platform == 'linux'`) ;
  `required-environments` : linux x86\_64 et macOS arm64.
- Avant chaque fin de tâche : `uv run pytest -m "not slow"`, `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy src/rushes/core src/rushes/db`, et le résultat est rapporté tel quel.
- ruff : règles E, F, I, UP, B, SIM, RUF, longueur 100. mypy strict sur `rushes.core.*` et `rushes.db.*`.
- Tests avant le code ; le nom d'un test qui couvre une règle la cite : `test_r9_...`. Une règle ne change jamais
  pour faire passer un test.
- Tests : jamais de réseau (fixture autouse existante), jamais `data/` ; `tmp_path`, vidéos synthétiques, métadonnées
  enregistrées (« cassettes »), doublures des modèles. Les vrais modèles et rendus longs portent `@pytest.mark.slow`.
- Code et identifiants en anglais, commentaires courts ; textes visibles par Louis en français.
- Aucun chemin en dur : `rushes.paths.Paths` et la config. Fichiers nommés par identifiant, jamais par titre.
- Unités : positions et durées internes en millisecondes entières (`_ms`) ; statistiques plateformes en secondes
  (`_s`) ; la config est en secondes, convertie au chargement.
- Seul `rushes.ui` importe Rich (l'aide `--help` de Typer est exemptée). Aucun secret affiché ni journalisé (R47).
- Toute attribution passe par `db.registry.can_assign` (R1).
- Aucun LLM distant, aucune API payante (R39). Rien n'est jamais envoyé à une plateforme de publication (R48).
- yt-dlp toujours en mode métadonnées : `skip_download=True`, `check_formats=False`,
  `ignore_no_formats_error=True`, `sleep_interval_requests=2` (R18) ; TikTok : `impersonate=chrome`.
- Fichier rendu : 1080×1920, H.264 High 8 bits yuv420p, 30 i/s constants, AAC 48 kHz, `+faststart`,
  -14 LUFS ± 1 LU (R7).
- Durées des familles : `min_s > 60` ; `monetizable` 65–100 s, `long` 120–180 s (R6).
- Hashtags : 5 au maximum ; obligatoires selon R13 (config `required_hashtags`).
- Toute nouvelle page web : composants shadcn, Tailwind v4, skill `frontend-design` ; `break-ui` et
  `web-design-guidelines` avant de livrer (CLAUDE.md du dépôt).
- Une décision prise en route va dans `docs/decisions.md` (et dans la spec si elle change une décision arrêtée).

## 3. Ordre d'exécution et dépendances

Ordre d'exécution (une tâche par session) :

```text
Phase 1 : 33 → 42 → 2 → 3 → 4 → 5 → 6 → 7a → 7b → 8a → 8b → 9
Phase 2 : 10a → 10b → 11 → 12a → 12b → 12c → 13 → 14 → 15 → 16a → 16b → 34 → 35
Phase 3 : 36 → 17 → 18 → 20 → 37
Phase 4 : 22 → 24 → 25 → 26 → 27 → 28
Phase 5 : 31 → 30 → 32 → 38
Phase 6 : 39 → 40a → 40b → (lancement) → 41
```

| Tâche | Dépend de | Fournit surtout |
| --- | --- | --- |
| 33 Config v2 | 1 | Config sans Postiz, `required_hashtags`, `layouts`, `originality`, `rights`, `youtube.cookies`, `showcase`, `serve` ; migration 0002 |
| 42 Essai de faisabilité 3.15 | 33 | `docker/Dockerfile` v0 avec toutes les dépendances lourdes, `scripts/spike_models.py`, rapport go/no-go par bibliothèque (une inférence et le pic mémoire de chaque modèle, sur le serveur) |
| 2 ui, status, runner | 33 | `Emitter`, `Context`, `run_step`, `run_pipeline` (`lock`, `yield_to_express`), codes de sortie et `combine_exit_codes`, verrou, `status`, `PENDING`/`IMPLEMENTED` de `tests/test_cli.py` |
| 3 core, registry | 2 | Intervalles, timecodes, tirages, `can_assign` |
| 4 ingest, clean v1 | 3 | `media.probe`, `media.ffmpeg`, générateur de vidéos synthétiques, propriétaire simple |
| 5 analyze v1 | 4 | `ModelHub` (légers : `vad`, `faces`, `sounds`, `tts`), `Vad`, `scenes.json`, `audio.json` |
| 6 cut parties | 5 | Partition DP, familles, segments `part` |
| 7a render : canevas | 6 | Canevas 1080×1920, mises en page `full` et `square` centrée, encodage conforme, loudnorm deux passes (R7) |
| 7b render : habillage | 7a | Bandeau « Part N » ou titre, barre, carte de fin, `config/charter.yaml` (`BrandCharter`), polices, zones sûres (R9), `queue/numbering.py` (`part_number`), module `base` |
| 8a assign, meta | 7b | Approbation v0, `assign` simple, `db/derived.py`, légendes (R12), hashtags (R13), couverture |
| 8b package, run | 8a | `outbox/` et `outbox_test/` (R14, R22), `upload.json`, `INDEX.html`, traçabilité (R17), `rushes run` |
| 9 image, mesures | 8b, 42 | Dockerfile définitif, `rushes models fetch` (sha256), rapport de mesures sur le vrai pipeline |
| 10a discover youtube | 9 | `net.ytdlp` (`cookies_allowed`, R18), cassettes, chaînes officielles (écrites dans `derived_values`), `source_videos`, `video_signals` (migration 0004) |
| 10b ingest youtube | 10a | Sélection des téléchargements selon le stock (`stock_days`), `YtDlp.download` (cookies en secours seulement, R18), épisodes communautaires jamais découpés |
| 11 clean v2 | 10b | Empreinte audio, découpe de compilations, logos (migration 0005) |
| 12a analyze : vision | 11 | Facettes d'analyse, `ImageEncoder` CLIP, `scene_vectors`, recherche en texte libre (migration 0006) |
| 12b analyze : sons | 12a | `SoundTagger` CED, `events` de `audio.json` |
| 12c analyze : parole | 12b | `Transcriber`, `words.json`, table `words`, phrases |
| 13 best-of | 12c | Échelle de signaux, pics de la heatmap lus par `cut` (`heatmap_peaks_ms`, R5) |
| 14 accroches | 12c | Règles, `TextGenerator`, filtres R11 |
| 15 approbation | 13, 14 | Approbation automatique, re-sourçage |
| 16a rotation | 15 | Rotation générée, propriété selon la rotation |
| 16b file | 16a | File, rendu à la demande, renumérotation (`stale_numbers`), expiration, écarter, gestes de secours, `express_slot` (migration 0007) |
| 34 recadrage | 12a, 7b, 16b | `reframe` (action), mises en page `square`/`vertical`/`portrait` |
| 35 module avancé | 14, 34 | `SpeechSynth`, commentaire, voix off, atténuation |
| 36 serve | 8b, 16b | FastAPI + page React, client TypeScript généré, actions Publié/Écarter/Accroche, dépôt de l'export Instagram, `rushes refresh` (rendu léger à la demande, cède comme la voie express), `head_revision`, migration 0008 |
| 17 collecteurs | 36 | TikTok public, export Instagram, `detect-published` (migration 0009) |
| 18 bandits | 17, 14, 34 | Résultat de chaque post, bandits accroches et mises en page, performance apprise de la file (migration 0010) |
| 20 minuteurs, ntfy | 9, 17, 36 | Profils de run, unités systemd, sauvegarde, rétention, rapports (`stock.days`), alertes ntfy (`send_pending_alerts`), mise à jour de yt-dlp (R38) ; installation provisoire pour la porte de la phase 3 (migration 0011) |
| 37 recalage | 17, 18, 20 | Créneaux, parts, seuils, mises en page recalés ; alerte de chute |
| 22 verticales | phase 3 | `Strategy` par verticale, `Subject`, filtre `--vertical`, `Filters.episodes`, citation en accroche (R21) |
| 24 mots, SponsorBlock, autocut | 22 | SponsorBlock (R46), coupes en fin de phrase (R24), autocut au rendu (R45), `Timeline` et durées après autocut |
| 25 visages, sous-titres | 24, 34, 35 | `faces`, recadrage sur le visage (R26), `subs.ass` mot par mot (R10, R25) (migration 0012) |
| 26 crédits, campagnes | 22, 17, 20, 36 | Crédit et exigences de campagne (R23), suivi des soumissions (R28) (migration 0013) |
| 27 prospection | 26, 24 | `creators prospect`, `creators snapshot`, flux RSS, dynamique, autorisation par le YAML seul (R32) (migration 0014) |
| 28 veille, voie express | 27, 16b, 20 | `creators backfill`, `creators watch`, `creators express`, `creators revisit` (R29, R30) (migration 0015) |
| 31 étude de marché | 17, 10, 27, 18, 20, 22 | Panel vérifié par la page `embed`, `market_videos`, réserve de hashtags tournants, hashtag nostalgie, `market.keywords` ajoutés aux requêtes de prospection de la tâche 27 (les comptes TikTok eux-mêmes ne sont pas prospectés : rushes a besoin d'une source YouTube) (migration 0016) |
| 30 compilations | 12a, 13, 24 | `compile_themes` (compte à rebours « #N », R44), `config/themes/`, suggestions dans `data/proposals/themes/` |
| 32 revue | 15, 36, 22, 24 | `review build` (page hors ligne en un seul fichier, construite depuis `web/`), `review apply` contrôlé par les règles |
| 38 séries en tendance | 31, 28 | `discover series` (noms extraits, chaîne officielle exigée), vitrine dans le budget, `rights list`, alerte `unlicensed_top` |
| 39 déploiement | tout | `deploy.sh` (tests de l'image candidate, sauvegarde avant déploiement, retour arrière automatique), `rushes doctor`, `backup restore`, `rushes db head`, `scripts/rushes`, étiquettes GoDoxy (veille, OIDC), ntfy, secrets, sauvegarde hors RAID, `docs/ops/runbook.md` |
| 40a contrôles | 39 | Audit nocturne des règles (`rushes audit`), bascule des licences (`rushes rights sync`), origine, mémoire anonyme de pointe et `oom_kills` des runs, runs interrompus (`run_lock_path`) (migration 0017) |
| 40b répétition | 40a, 10b | 7 jours sur le serveur avec un calendrier d'exercices, `rushes rehearsal report` |
| 41 lancement, rodage | lancement | `rushes launch check/start` (`ops.launched_at`), `rushes burnin status` : critères sur les vrais posts |

**Ordre des étapes et profils à la fin de la phase 6** (référence ; chaque tâche ajoute ses étapes à sa place, et
`test_profiles_reference_registered_steps` vérifie les noms) :

```python
RUN_ORDER = ("licence_sync", "discover_youtube", "creators_backfill", "ingest", "clean", "analyze", "vision",
             "sounds", "transcribe", "resource", "cut", "bestof", "compile_themes", "hooks", "approve", "assign",
             "queue", "faces", "reframe", "commentary", "voice", "render", "meta", "package")
PROFILES = {
    "night": ("backup", *RUN_ORDER, "audit", "disk", "report"),
    "watch": ("licence_sync", "creators_watch", "creators_revisit", "queue"),   # puis `rushes creators express` (unité)
    "stats": ("stats_collect", "detect_published", "bandits", "views_drop"),
    "weekly": ("creators_prospect", "creators_snapshot", "market_panel", "market_collect", "market_analyze",
               "discover_series", "rights", "themes_suggest", "recalibrate", "report_weekly"),
    "maintenance": ("backup_verify", "retention", "disk"),
}
EXPRESS_ANALYSIS = ("clean", "analyze", "transcribe", "sounds", "cut", "bestof", "hooks", "approve", "assign",
                    "queue", "faces")                                      # tâche 28, Filters(episodes=E)
EXPRESS_RENDER = ("reframe", "commentary", "voice", "render", "meta", "package")   # tâche 28, Filters(posts=P, episodes=E)
REFRESH_STEPS = ("render", "meta", "package")                              # tâche 36, commande `rushes refresh`
```

`discover_series` vient après l'étude de marché, car il lit les hashtags du panel (P40) ; `rights` vient après
`discover_series`. Le profil `weekly` de la tâche 20 suit ce tuple. Pas de profil `refresh` : `rushes-refresh.service`
lance la commande `rushes refresh` (tâche 36), qui suit le schéma de la voie express (`express_intent` puis verrou
lourd, le run en cours lui cède la main entre deux éléments) et ne retire les demandes (`take_requests`) qu'après
avoir obtenu le verrou lourd ; `rushes run --profile refresh` sort en 1 (profil inconnu).

## 4. Arborescence cible

```text
src/rushes/
  cli.py  config.py  paths.py  ui.py  context.py  runner.py  lock.py  clock.py  errors.py  schemas.py
  ids.py  fsutil.py  steps.py  status.py  verticals.py  subjects.py  selectors.py  assets/fonts/
  core/        intervals.py timecode.py rng.py scoring.py dp.py signals.py bandit.py text.py timeline.py phash.py types.py
  db/          engine.py models.py migrate.py sync.py registry.py derived.py trace.py migrations/
  media/       probe.py ffmpeg.py frames.py audio.py fingerprint.py detect.py verify.py
  models/      base.py (Protocols) hub.py fetch.py manifest.toml vad_silero.py whisper.py ced.py clip.py yunet.py llm.py tts.py
  net/         base.py (Protocols, NetClients) ytdlp.py http.py sponsorblock.py rss.py ratelimit.py
  discover/    discover.py series.py channels.py videos.py score.py signals.py
  market/      panel.py collect.py analyze.py
  rights/      table.py sync.py
  creators/    channel.py prospect.py snapshot.py backfill.py watch.py express.py revisit.py campaign.py
  ingest/      ingest.py owner.py select.py
  clean/       clean.py borders.py intro.py splitter.py logo.py sponsor.py
  analyze/     analyze.py shots.py loudness.py motion.py speech.py facets.py vision.py tags.py index.py sounds.py words.py transcribe.py
  highlights/  ladder.py popularity.py
  cut/         parts.py bestof.py points.py
  compile/     themes.py suggest.py
  review/      build.py apply.py static/review.html (build de web/, entrée review)
  hooks/       features.py rules.py llm.py filters.py rank.py propose.py
  approve/     approve.py score.py rules.py resource.py
  assign/      assign.py rotation.py
  queue/       slots.py rank.py schedule.py expire.py actions.py manual.py refresh.py step.py
               numbering.py (part_number 7b, stale_numbers 16b ; sans import de rushes.render, lu par serve)
  render/      canvas.py encode.py loudnorm.py text.py overlays.py charter.py cropcmd.py render.py autocut.py
  reframe/     importance.py path.py choose.py step.py faces.py
  originality/ base.py advanced.py voiceover.py steps.py
  captions/    ass.py speech.py
  meta/        meta.py caption.py hashtags.py cover.py credit.py
  package/     outbox.py upload.py index_html.py
  serve/       context.py schemas.py api.py auth.py files.py app.py static/ (build de web/)
  stats/       tiktok.py imports.py detect.py metrics.py outcomes.py performance.py recalibrate.py drop.py steps.py
  ops/         requests.py notify.py alerts.py backup.py maintenance.py disk.py report.py steps.py
               doctor.py audit.py memory.py rehearsal.py launch.py burnin.py
web/           Vite + React + TypeScript + shadcn/ui + Tailwind v4 : vite.config.ts (page de serve),
               vite.review.config.ts (page de revue en un seul fichier, tâche 32), tests/*.spec.ts (Playwright npm)
docker/        Dockerfile entrypoint.sh compose.yaml rushes.env.example ntfy/server.yml
               systemd/ (*.service, *.timer, rushes-refresh.path, rushes-update.path, rushes-alert@.service)
scripts/       record_cassette.py spike_models.py measure_pipeline.py (développement, réseau) ; export_openapi.py
               serve_fixture.py ; rushes (commande hôte, tâche 39) update_ytdlp.sh install_units.sh deploy.sh
               lib_image.sh (hôte)
docs/ops/      runbook.md (procédure d'exploitation, tâche 39)
tests/         test_<domaine>.py (à plat, noms uniques) conftest.py helpers.py rules_pending.toml
               fixtures/ (cassettes/, images/, ffmpeg_logs/, generator.py) fakes/ (horloge, modèles, réseau)
```

## 5. Contrats transverses (code)

Le code ci-dessous fixe les noms et les types. Les corps sont laissés à la tâche qui crée le module.

### 5.1 Contexte d'exécution (`rushes/context.py`, tâche 2)

```python
@dataclass(frozen=True)
class Filters:
    series: str | None = None
    creator: str | None = None
    episode: int | None = None
    account: str | None = None
    limit: int | None = None
    force: bool = False
    posts: tuple[int, ...] = ()   # tâche 36 : render, meta, package, reframe, commentary, voice limités à ces posts
    vertical: Vertical | None = None   # tâche 22 : verticale de la marque du compte propriétaire (--vertical)
    episodes: tuple[int, ...] = ()     # tâche 22 : épisodes de la voie express (tâche 28), faces compris


class Clock(Protocol):
    def now(self) -> datetime: ...  # always timezone-aware UTC


@dataclass(frozen=True)
class Context:
    paths: Paths
    config: Config
    engine: Engine
    ui: Emitter
    clock: Clock
    models: ModelHub          # tâche 5 ; un hub vide avant
    net: NetClients           # tâche 10a ; doublures dans les tests


def build_context(*, plain: bool, json: bool, verbose: bool) -> Context: ...
```

`NetClients` regroupe `ytdlp: YtDlp`, `http: Http` (section 5.8). Les tests construisent `Context` avec
`FakeClock`, `FakeModelHub`, `CassetteYtDlp` et `FakeHttp`.

### 5.2 Événements et sortie (`rushes/ui.py`, tâche 2)

```python
Level = Literal["info", "warn", "error"]
StepStatus = Literal["done", "skipped", "warned", "failed"]


@dataclass(frozen=True)
class Header:   command: str; scope: str | None; test_mode: bool; version: str; commit: str | None
@dataclass(frozen=True)
class StepEvent: step: str; item: str | None; status: StepStatus; duration_ms: int; message: str = ""
@dataclass(frozen=True)
class Progress: task: str; done: float; total: float | None; unit: str = ""
@dataclass(frozen=True)
class Message:  level: Level; text: str; hint: str | None = None; alert: str | None = None  # clé d'alerte ntfy : champ créé en tâche 2, envoyé par la tâche 20
@dataclass(frozen=True)
class Table:    title: str; columns: list[str]; rows: list[list[str]]
@dataclass(frozen=True)
class Summary:  ready_by_account: dict[str, int]; warnings: int; duration_ms: int; index_path: str | None

Event = Header | StepEvent | Progress | Message | Table | Summary


class Emitter(Protocol):
    def emit(self, event: Event) -> None: ...
    def close(self) -> None: ...


def make_emitter(*, plain: bool, json: bool, verbose: bool, stream: TextIO | None = None,
                 log_path: Path | None = None) -> Emitter:
    """Rich on a TTY without flags; plain text with --plain; JSON lines otherwise. Honours NO_COLOR.
    log_path receives a full JSON copy of every event."""

def register_secret(value: str) -> None: ...   # every SecretStr read registers its value
def redact(text: str) -> str: ...              # replaces registered secrets and cookie-like tokens by «***»
```

Ligne JSON (une par événement, `redact` appliqué à toutes les chaînes) :

```json
{"ts": "2026-11-16T02:00:03.120Z", "event": "step", "level": "info", "command": "run",
 "step": "render", "item": "p_000123", "status": "done", "duration_ms": 61234, "message": ""}
```

Les étapes n'appellent jamais `print` ni Rich : elles appellent `ctx.ui.emit(...)`. Un journal JSON complet est
aussi écrit dans `data/logs/<date>.jsonl` (rotation 30 jours), même quand la console est en mode Rich.

### 5.3 Contrat d'une étape et runner (`rushes/runner.py`, tâche 2)

```python
@dataclass(frozen=True)
class Outcome:
    status: StepStatus
    message: str = ""
    data: Mapping[str, object] = field(default_factory=dict)
    source_unavailable: bool = False   # source externe indisponible (réseau, YouTube, TikTok) : compté pour le code 5


class Step[T](Protocol):
    name: ClassVar[str]          # "render", "cut", …
    heavy: ClassVar[bool]        # True → prend le verrou lourd

    def key(self, item: T) -> str: ...                        # identifiant affiché, ex. "p_000123"
    def select(self, ctx: Context, filters: Filters) -> Iterable[T]: ...   # travail pas encore fait, sauf --force
    def run(self, ctx: Context, item: T, *, force: bool) -> Outcome: ...    # écrit fichiers puis base (1 transaction)


@dataclass(frozen=True)
class StepReport:
    step: str
    done: int
    skipped: int
    warned: int
    failed: int
    duration_ms: int
    unavailable: int = 0          # éléments dont l'Outcome porte source_unavailable=True


class BaseStep:                                   # implémentation par défaut
    heavy: ClassVar[bool] = False
    def finish(self, ctx: Context) -> None: ...   # appelé une fois après le dernier élément (ex. INDEX.html)


def run_step[T](ctx: Context, step: Step[T], filters: Filters) -> StepReport: ...
def exit_code_for(reports: Sequence[StepReport], errors: Sequence[RushesError]) -> int: ...
def combine_exit_codes(*codes: int) -> int: ...   # code le plus prioritaire (§5.4) ; voie express, refresh
def run_pipeline(ctx: Context, steps: Sequence[Step[Any]], filters: Filters, *, command: str,
                 lock: HeavyLock | None = None, yield_to_express: bool = True) -> int: ...  # code de sortie
def run_lock_path(paths: Paths, run_id: int) -> Path: ...   # data/locks/run-<id>.lock (tâche 40a)
```

Règles du runner :

- Une ligne `runs` par commande (`status` : `running` → `ok` | `partial` | `failed`), `summary_json` = rapports.
- Un élément qui lève une exception est `failed`, journalisé avec sa trace (fichier) ; les suivants continuent.
- Idempotence (R16) : `select` ne renvoie que le travail non fait ; avec `force=True`, il renvoie tout ce qui
  correspond aux filtres. Écriture de fichiers d'abord, dans un fichier temporaire renommé à la fin, puis
  transaction de base.
- `--force` ne retouche jamais un élément qui a un descendant attribué, rendu ou publié (`cut`, `bestof`, `hooks`,
  `approve`) : l'élément est `skipped`, message « déjà attribué ». Pour calibrer, `approve --dry-run` affiche
  scores, seuil et décisions sans rien écrire (tâche 15).
- Une étape qui perd sa source externe renvoie `Outcome(status="warned", source_unavailable=True)` (jamais une
  exception, qui donnerait `failed`) ; `exit_code_for` renvoie 5 si un rapport a `unavailable > 0`, aucun `failed`
  et aucune autre erreur (§5.4).
- Les étapes `heavy` tiennent le verrou lourd (section 5.11) et lui cèdent la place entre deux éléments si la voie
  express attend. Avant de céder, le runner appelle `ctx.models.release()` : chaque conteneur a sa propre limite de
  6 Go, et deux modèles lourds chargés en même temps dépasseraient le budget du serveur. Le modèle est rechargé
  à la reprise (ce rechargement ne compte pas comme une violation de R19).
- `run_pipeline` prend le verrou lourd si une étape est `heavy`, sauf si `lock` est fourni (l'appelant le tient
  déjà) ; il ne cède entre deux éléments que si `yield_to_express`. La voie express (tâche 28) et `rushes refresh`
  (tâche 36) font `with express_intent(paths), heavy_lock(paths, wait=True) as hl:` puis
  `run_pipeline(..., lock=hl, yield_to_express=False)` pour chacun de leurs appels (jamais un second `open()` de
  leurs propres verrous), et combinent les codes par `combine_exit_codes`.
- Chaque commande tient, pour toute sa durée, un `flock` sur `run_lock_path(run_id)` (tâche 40a) : une ligne
  `running` n'est périmée (run interrompu) que si ce verrou peut être pris ; une cession à la voie express ne clôt
  donc jamais le run de nuit.
- En fin de commande, `run_pipeline` envoie les alertes par `send_pending_alerts` (§5.13, tâche 20).

### 5.4 Codes de sortie (`rushes/errors.py`, tâche 2)

| Code | Sens |
| --- | --- |
| 0 | Succès (rien à faire compris) |
| 1 | Erreur ou commande non implémentée |
| 2 | Configuration invalide (tâche 1) |
| 3 | Verrou occupé et `--no-wait` |
| 4 | Succès partiel : au moins un élément `failed` |
| 5 | Source externe indisponible (réseau, YouTube, TikTok) sans autre erreur |

Priorité quand plusieurs cas se cumulent : 2 > 3 > 1 > 4 > 5 > 0. Un run où des éléments échouent et où une source
est indisponible sort donc en 4. Le code 5 vient de `SourceUnavailable` levée hors des éléments ou de
`StepReport.unavailable > 0` (§5.3). `combine_exit_codes` applique la même priorité aux codes de plusieurs
`run_pipeline` d'une même commande. Les unités systemd tiennent 4 et 5 pour des succès (`SuccessExitStatus=4 5`,
tâche 20) : ces cas sont déjà signalés par le rapport et leur alerte dédiée, jamais par `timer_failed`.

```python
class RushesError(Exception):
    exit_code: ClassVar[int] = 1
    hint: str | None = None
class LockBusy(RushesError): exit_code = 3
class SourceUnavailable(RushesError): exit_code = 5
class MediaError(RushesError): ...
```

### 5.5 Modèles locaux (`rushes/models/base.py` et `hub.py`, tâches 5, 12, 14, 34, 35)

```python
from rushes.core.intervals import Span   # type Span = tuple[int, int], (start_ms, end_ms), défini en tâche 3
from rushes.core.types import SoundEvent, Word   # réexportés ici


# rushes/core/types.py (tâche 5) : types de données sans dépendance lourde, importables par rushes.schemas et
# rushes.serve (qui n'importe jamais rushes.models, tâche 36)
@dataclass(frozen=True)
class Word:        start_ms: int; end_ms: int; text: str; prob: float
@dataclass(frozen=True)
class SoundEvent:  start_ms: int; end_ms: int; label: Literal["impact", "explosion", "splash", "scream", "laughter"]; score: float

# rushes/models/base.py
@dataclass(frozen=True)
class Box:         x: int; y: int; w: int; h: int; score: float


class Vad(Protocol):
    def speech(self, pcm16k: NDArray[np.float32]) -> list[Span]: ...

class Transcriber(Protocol):
    def transcribe(self, wav16k: Path, *, language: str, clips: Sequence[Span]) -> list[Word]: ...

class SoundTagger(Protocol):
    def tag(self, pcm16k: NDArray[np.float32]) -> list[SoundEvent]: ...

class ImageEncoder(Protocol):
    dim: int
    def encode_images(self, images: Sequence[Path]) -> NDArray[np.float32]: ...  # L2-normalisés
    def encode_texts(self, texts: Sequence[str]) -> NDArray[np.float32]: ...

class FaceDetector(Protocol):
    def detect(self, bgr: NDArray[np.uint8]) -> list[Box]: ...

class TextGenerator(Protocol):
    def generate[M: BaseModel](self, system: str, user: str, schema: type[M], *, timeout_s: float) -> M | None: ...

class SpeechSynth(Protocol):
    def synthesize(self, text: str, *, language: str, out_wav: Path) -> int: ...  # durée en ms


class ModelHub:
    """Charge à la demande ; un seul modèle lourd en mémoire (décharge le précédent) ; une fois par étape (R19).
    Légers : vad, faces, sounds, tts. Lourds : transcriber, clip, llm."""
    def vad(self) -> Vad: ...
    def transcriber(self) -> Transcriber: ...
    def sounds(self) -> SoundTagger: ...
    def clip(self) -> ImageEncoder: ...
    def faces(self) -> FaceDetector: ...
    def llm(self) -> TextGenerator | None: ...   # None si llm.enabled est faux
    def tts(self) -> SpeechSynth | None: ...
    def loads(self) -> Mapping[str, int]: ...    # compteur de chargements, pour R19
    def release(self) -> None: ...               # décharge le modèle lourd (avant de céder le verrou)
```

Implémentations réelles (imports lourds à l'intérieur des fonctions) : `vad_silero.py` (`load_silero_vad(onnx=False)`),
`whisper.py` (faster-whisper int8, `vad_filter=False`, `clip_timestamps`), `ced.py` (sherpa-onnx CED-mini),
`clip.py` (open\_clip ViT-B-32 `datacomp_xl_s13b_b90k`, safetensors), `yunet.py`
(`face_detection_yunet_2026may.onnx`), `llm.py` (llama-cpp-python, Qwen3-4B Q4\_K\_M, repli 1.7B,
`response_format={"type": "json_object", "schema": …}`), `tts.py` (sherpa-onnx Supertonic 3, repli Kokoro).
Doublures déterministes dans `tests/fakes/models.py`. Poids dans `paths.cache_models`, téléchargés une fois par
`rushes models fetch` (tâche 9), jamais pendant un test. Un manifeste versionné (`src/rushes/models/manifest.toml` :
URL, taille, sha256, licence) fixe chaque fichier ; `fetch` refuse un fichier dont le sha256 diffère et le hub
refuse de charger un fichier absent du manifeste.

R19 (précisée par L23) : chaque modèle est chargé une fois par étape, jamais par élément ; un seul modèle lourd en
mémoire à la fois. Deux étapes distinctes d'une même commande peuvent donc recharger le même modèle (`hooks` puis
`commentary` pour le LLM, `transcribe` puis `voice` pour Whisper). Chaque usage d'un modèle lourd est une étape
distincte qui traite tous ses éléments (`analyze`, `vision`, `sounds`, `transcribe`, `hooks`, `reframe`,
`commentary`, `voice`) ; jamais deux modèles lourds dans la même étape (`voice` : TTS léger plus Whisper, tâche 35). Les facettes d'un épisode (`base`, `vision`, `sounds`, `words`) sont suivies par
`analyze.facets.facets_done` (tâche 12a).

### 5.6 Fichiers d'épisode et de post (`rushes/schemas.py`)

Modèles Pydantic `extra="forbid"`, champ `version: Literal[1]`, écrits en JSON UTF-8 par
`write_json(model, path)` (écriture atomique) et relus par `read_json(type, path)`.

```python
class CleanFile(BaseModel):    version: Literal[1]; episode_id: int; source_duration_ms: int; kept: tuple[int, int]; removed: list[tuple[int, int, str]]; span: tuple[int, int] | None = None  # tâche 4 ; span : épisode enfant (11)
class Shot(BaseModel):         id: int; start_ms: int; end_ms: int; keyframe_ms: int; motion: float; loudness_lufs: float | None; speech_ratio: float; phash: str
class ScenesFile(BaseModel):   version: Literal[1]; episode_id: int; duration_ms: int; detector: dict[str, float]; shots: list[Shot]          # tâche 5

class LoudPoint(BaseModel):    t_ms: int; lufs: float                 # court terme, pas de 100 ms
class AudioFile(BaseModel):    version: Literal[1]; episode_id: int; integrated_lufs: float; loudness: list[LoudPoint]; speech: list[tuple[int, int]]; events: list[SoundEvent] | None = None  # tâche 5 ; events en 12b (None = pas analysé)

class WordsFile(BaseModel):    version: Literal[1]; episode_id: int; language: str; model: str; words: list[Word]; sentences: list[tuple[int, int]]  # tâche 12/24

class PathPoint(BaseModel):    t_ms: int; x: int                      # bord gauche de la fenêtre, en pixels source
class ShotCrop(BaseModel):     start_ms: int; end_ms: int; mode: Literal["static", "pan"]; points: list[PathPoint]
class ReframeFile(BaseModel):  version: Literal[1]; layout: Layout; source_w: int; source_h: int; crop_w: int; shots: list[ShotCrop]  # tâche 34 ; un par élément d'une compilation multi-sources (30)

class MetaFile(BaseModel):     version: Literal[1]; post_id: int; caption: str; hashtags: list[str]; cover_frame_ms: int; hook_applied: str | None; hook_alternatives: list[str]  # tâche 8a
class UploadFile(BaseModel):   ...                                   # champs exacts de l'exemple de la spec (tâche 8b)
class ResourceFile(BaseModel): version: Literal[1]; episode_id: int; matches: list[tuple[int, int, int, float]]; compared: list[int]   # tâche 15 ; compared = épisodes officiels déjà comparés
class CommentaryFile(BaseModel): version: Literal[1]; post_id: int; language: str; sentences: list[str]; model: str  # tâche 35
class VoiceFile(BaseModel):    version: Literal[1]; post_id: int; wav: str; duration_ms: int; start_ms: int; words: list[Word]; engine: str  # tâche 35
class ReviewDecisions(BaseModel): version: Literal[1]; decisions: list[ReviewDecision]               # tâche 32
```

`Layout = Literal["square", "vertical", "portrait", "full"]` ; `Originality = Literal["base", "advanced"]`.

### 5.7 Valeurs déduites (`rushes/db/derived.py`, tâche 8a)

```python
@dataclass(frozen=True)
class Resolved[T]:
    value: T
    origin: Literal["config", "derived", "default"]
    source: str | None
    computed_at: datetime | None


def record(session: Session, key: str, scope: str, value: object, *, source: str, confidence: float, at: datetime) -> None: ...
def resolve[T](session: Session, key: str, scope: str, *, config_value: T | None, default: T, parse: Callable[[object], T]) -> Resolved[T]: ...
```

Ordre : valeur de la config (si présente) > valeur déduite > défaut. `derived_values` garde la valeur courante ;
chaque `record` ajoute aussi une ligne à `derived_values_history` (mêmes colonnes + `id`), pour auditer le recalage.
Catalogue des clés :

| Clé | Portée | Produite par |
| --- | --- | --- |
| `youtube.official_channels` | série | 10a, 38 (série candidate) |
| `youtube.community_channels` | série | 10a |
| `tiktok.official_accounts` | série, créateur | 10a |
| `youtube.search_queries` | série | 10a |
| `youtube.last_discovery` | série | 10a |
| `rotation.plan` | `cartoons` | 16a |
| `rotation.epoch` | `cartoons` | 16a (provisoire), 41 (lancement) |
| `hashtags.pool` (lecture, défaut tiré du nom) | série, créateur | 8a (lu), 31 (écrit) |
| `market.panel` | série, créateur | 31 |
| `hashtags.pool` | série, créateur | 31 |
| `hashtags.nostalgia_instagram` | `cartoons` | 31 |
| `market.keywords` (mots-clés des vidéos qui surperforment dans le panel) | série, créateur | 31 (lu par 27 : requêtes de prospection) |
| `discover.series_evidence` | série candidate | 38 |
| `youtube.creator_channel` | créateur | 27 |
| `youtube.channel_title` | créateur | 27 |
| `creator.handles` (comptes TikTok et Instagram du créateur) | créateur | 27 (lu par 26) |
| `slots` | compte | 37 |
| `families.share` (type unique `dict[Family, float]` : défaut, `config_value` et valeur déduite) | compte | 37 |
| `approval.threshold` | verticale | 37 |
| `layouts.vertical_share` | compte | 37 |
| `stock.days` (un enregistrement par jour, historique lu par 37) | compte | 20 (`ReportStep`) |
| `ops.launched_at` | `global` | 41 |

### 5.8 Accès réseau (`rushes/net/` : contrats en tâche 2, `Http` réel en tâche 9, `YtDlp` réel en tâches 10a et 10b)

```python
class YtDlp(Protocol):
    def extract(self, url: str, *, flat: bool, platform: Literal["youtube", "tiktok"],
                comments: int = 0, playlist_items: str | None = None,
                cookies_allowed: bool = False) -> dict[str, Any]: ...   # True : DiscoverYoutubeStep seul (R18)
    def download(self, url: str, out_dir: Path, *, max_height: int) -> Path: ...   # ingestion (10b, voie express) ; cookies en secours selon settings.youtube.cookies (R18, L32)

class Http(Protocol):
    def get_json(self, url: str, *, params: Mapping[str, str] | None = None) -> Any: ...
    def get_text(self, url: str) -> str: ...
    def post(self, url: str, *, body: bytes, headers: Mapping[str, str]) -> int: ...  # ntfy seulement
    def download(self, url: str, dest: Path) -> None: ...                              # poids des modèles (tâche 9)


@dataclass(frozen=True)
class NetClients:
    ytdlp: YtDlp
    http: Http
```

`YtDlpClient` (réel) : paramètres de la section 2 ; cookies seulement si `cookies_allowed=True`, que seule la
découverte YouTube passe (`DiscoverYoutubeStep`, 10a), plus `download` qui suit la même règle de secours (L32) ; ni la
veille, ni le catalogue, ni `creators_backfill`, ni la prospection, ni les extractions de la voie express ; sans `cookies_allowed`, une erreur de connexion donne
`ExtractError(signin=True)` sans nouvelle tentative, quel que soit le mode. Avec `cookies_allowed`, selon
`settings.youtube.cookies` (`never` | `fallback` | `always`) — en `fallback`, ne réessaie avec cookies qu'après une
erreur de type « sign in to confirm » (alerte `youtube_signin`) ; un échec avec cookies émet `cookies_expired` ; copie du fichier de cookies dans un fichier temporaire 0600 avant chaque usage (yt-dlp le
réécrit). `RateLimiter` partagé. `CassetteYtDlp` (tests) lit `tests/fixtures/cassettes/<sha1(url+options)>.json` et
lève `CassetteMissing` sinon. `scripts/record_cassette.py` enregistre une cassette (développement seulement), sans
`formats`, `thumbnails` ni URL signée.

### 5.9 Configuration v2 (`rushes/config.py`, tâche 33)

Modifications du schéma de la tâche 1 :

```python
class Account(_Model):
    id: str; brand: str; platform: Platform; handle: str
    originality: Originality | None = None          # None → settings.originality.default (R50)

class LayoutSettings(_Model):
    preference: list[Literal["square", "portrait", "full"]] = ["square", "portrait", "full"]
    vertical_share: float | None = Field(None, ge=0, le=1)      # None → rushes décide (défaut 0,15, recalé)

class AccountsConfig(_Model):
    brands: list[Brand]; accounts: list[Account]
    rotation: list[RotationPeriod] = []             # facultatif : rushes génère le plan (Q5)
    creators_by_brand: dict[str, list[str]] = {}
    families: dict[str, Family]
    required_hashtags: dict[Vertical, dict[Platform, list[str]]]   # R13, jetons {series} {creator} {nostalgia}
    layouts: LayoutSettings = LayoutSettings()
    posts_per_day: dict[Platform, float]
    slots: dict[Platform, list[str]] = {}            # plateforme absente → défaut par fuseau de la marque, recalé (tâche 37)
    upload_defaults: dict[Platform, dict[str, Any]] = {}

class Family(_Model): min_s: float; max_s: float; share: Share | None = None   # None → part de départ de la spec, recalée

DEFAULT_SLOTS = {"tiktok": ["15:00", "18:00"], "instagram": ["17:00"]}   # fuseau America/New_York (et tout autre)
# fuseau de marque Europe/Paris (Instant Culte, en soirée) : {"tiktok": ["18:00", "20:00"], "instagram": ["19:00"]}
# Config.slots_for(account_id) -> list[str] : config, sinon défaut selon la plateforme et le fuseau de la marque
CHANNEL_ID = re.compile(r"^UC[\w-]{22}$"); TIKTOK_HANDLE = re.compile(r"^@[\w.]+$")   # réutilisés par la tâche 27
DEFAULT_SHARE = {"monetizable": {"tiktok": 1.0, "instagram": 1.0}, "long": {"tiktok": 0.0, "instagram": 0.0}}
# Settings.approval_threshold : Threshold | None = None → 0,7 par verticale, recalé (tâche 37)

class Rights(_Model):
    holder: str | None = None
    status: Literal["to_request", "requested", "granted", "refused"] = "to_request"
    requested_at: date | None = None
    notes: str = ""

class VoiceSettings(_Model):    engine: Literal["supertonic", "kokoro"] = "supertonic"; duck_db: float = Field(-12, lt=0)
class OriginalitySettings(_Model): default: Originality = "base"; voice: VoiceSettings = VoiceSettings()
class YoutubeSettings(_Model):  cookies: Literal["never", "fallback", "always"] = "fallback"
class ShowcaseSettings(_Model): max_episodes_per_week: int = Field(4, ge=0)
class ServeSettings(_Model):    host: str = "0.0.0.0"; port: int = 8080; public_origin: str   # ex. "https://rushes.<domaine>"
class NtfySettings(_Model):     url: str; topic_env: str; token_env: str | None = None
```

- `Settings` perd `postiz` et `manual_posts_notice`, gagne `originality`, `youtube`, `showcase`, `serve`, `models`
  (tailles Whisper, étiquette CLIP). `Settings` est `extra="forbid"` : chaque tâche y déclare sa section (`cut`,
  `render`, …, `backup: BackupSettings` et `retention: RetentionSettings` avec `review_days` en tâche 20).
- `SeriesConfig` : `rights: Rights | None`, `sources.tiktok_accounts: list[str]`, `branding.hashtags_anchor`
  supprimé. `CreatorConfig` : `rights`, `hashtag: str | None` ; `hashtags_anchor` supprimé.
- **Valeurs recalables** (`approval_threshold`, `slots`, `families.*.share`, `layouts.vertical_share`) : facultatives.
  Absentes, rushes part du défaut de la spec et le recale (tâche 37) ; écrites, elles l'emportent toujours (forçage
  manuel). La config d'exemple les laisse en commentaire. Les tâches lisent ces valeurs par `derived.resolve` avec
  `config_value` = la valeur écrite ou `None`.
- Validation de `required_hashtags` : au plus 5 par plateforme ; jamais `#viral` ni `#foryou` ; pas de `#fyp` sur
  Instagram ; jetons connus seulement.
- Une clé héritée donne une erreur dédiée, à l'emplacement relatif au fichier (`postiz`, `accounts.0.publish_mode`) :
  `postiz`, `publish_mode`, `postiz_integration`, `manual_posts_notice`, `platform_hashtags` → « Postiz a été
  abandonné (décision D1 du 2026-11-01) : retirer cette clé » ; `hashtags_anchor`, `branding.hashtags_anchor` →
  « remplacé par required_hashtags (R13) et hashtag du créateur : retirer cette clé ».
- `config/series/grizzy.yaml` est supprimé ; `tom_and_jerry.yaml` reste minimal (id, titre, audio, `licensed`,
  `rights`), l'exemple complet de la spec y est cité en commentaire YAML, jamais en valeurs. Amorce de série
  invalide (chaîne hors `CHANNEL_ID`, compte hors `TIKTOK_HANDLE`, fichier `refs/…` absent) → `ConfigError` qui
  nomme la clé et le fichier.
- Charte graphique : `config/charter.yaml` (tâche 7b, modèle `BrandCharter`), une entrée par marque (police OFL
  embarquée dans `assets/fonts/`, couleurs du bandeau et du surlignage, marges des zones sûres en pixels, photo de
  profil dans `config/avatars/` pour la carte de fin, textes par langue). Champ `texts: dict[str, Copy] =
  Field(alias="copy")` : la clé YAML reste `copy`, le code lit `charter.texts[lang].bestof` (pas de champ `copy`,
  qui masquerait `BaseModel.copy`).

### 5.10 Base de données : suite des migrations

Chaque migration a sa tâche ; le test « ORM identique aux migrations » (tâche 1) reste vert à chaque étape. Les
numéros suivent l'ordre d'exécution des tâches (§3), car la chaîne des révisions Alembic est linéaire.

| Rév. | Tâche | Contenu |
| --- | --- | --- |
| 0002 | 33 | `accounts` : − `publish_mode`, − `postiz_integration`, + `originality` (`base`/`advanced`, NULL = défaut). `posts` : − `postiz_*`, + `layout`, + `originality`, statuts `planned, rendered, ready, published, discarded, expired`. `series` et `creators` : + `origin` (`config`/`prospect`), + `status` (`candidate`/`active`/`dropped`). + `rights`, + `derived_values`, + `derived_values_history` |
| 0003 | 2 | `runs` : `CHECK status IN (running, ok, partial, failed)`, + `exit_code` |
| 0004 | 10a | + `channels`, + `video_signals` ; `source_videos` : + `title`, + `channel_handle`, + `discovered_via`, + `extracted_at`, statuts `found, retained, downloaded, rejected, failed` ; `compilations.status CHECK IN ('proposed','approved','needs_review','rejected')` |
| 0005 | 11 | `episodes` : + `parent_episode_id`, + `span_start_ms`, + `span_end_ms`, + `logo_corner`, statut `split` |
| 0006 | 12a | + `scene_vectors`, + `words` (remplie par 12c) |
| 0007 | 16b | `posts` : + `order_after_post_id`, + `ready_at`, + `express_slot` (créneau en plus de la voie express, L27) (le créneau conseillé est `planned_at`, UTC, dès la tâche 8b) |
| 0008 | 36 | + `post_actions` (journal des clics de la page et des gestes de secours ; `kind CHECK IN ('published','discard','hook','submitted')`, `'submitted'` pour le bouton « Soumis » de la tâche 26 ; `origin CHECK IN ('page','folder','system')`) |
| 0009 | 17 | + `metrics`, + `public_videos`, + `stats_imports`, + `collector_health` ; `posts` : + `platform_post_id` |
| 0010 | 18 | + `bandit_arms` (remplace `hook_stats` de la spec : dimension `hook_type` ou `layout`), + `post_outcomes` |
| 0011 | 20 | + `alerts_sent` (dédoublonnage des alertes) |
| 0012 | 25 | + `face_tracks` ; `episodes` : + `faces_at` |
| 0013 | 26 | + `campaign_posts` |
| 0014 | 27 | + `channel_snapshots` ; `channels` : + `title`, + `handle`, + `language`, + `subscribers`, + `momentum_json`, + `reason`, + `last_prospected_at` ; `creators.channel_id` devient nullable |
| 0015 | 28 | `channels` : + `last_checked_at`, + `last_rss_ok_at`, + `last_listed_at`, + `last_video_at`, + `last_backfill_at` ; `source_videos` : + `comments_json`, + `express_requested_at`, + `express_done_at`, + `revisited_24_at`, + `revisited_72_at` |
| 0016 | 31 | + `market_accounts`, + `market_videos` |
| 0017 | 40a | `runs` : + `peak_mem_bytes` (pic de mémoire anonyme, cache de pages exclu), + `oom_kills`, + `trigger` (`timer`, `path`, `manual`) |

Tables nouvelles de la révision 0002 :

```python
class Right(Base):
    __tablename__ = "rights"
    subject_kind: Mapped[str] = mapped_column(Text, primary_key=True)      # "series" | "creator"
    subject_id: Mapped[str] = mapped_column(Text, primary_key=True)
    holder: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)                              # to_request | requested | granted | refused
    requested_at: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[float | None] = mapped_column(Float)                  # potentiel calculé (tâche 38)
    showcase_posts: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"))
    updated_at: Mapped[str] = mapped_column(Text)


class DerivedValue(Base):
    __tablename__ = "derived_values"
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    computed_at: Mapped[str] = mapped_column(Text)


class BanditArm(Base):                                                     # 0010
    __tablename__ = "bandit_arms"
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    dimension: Mapped[str] = mapped_column(Text, primary_key=True)        # "hook_type" | "layout"
    arm: Mapped[str] = mapped_column(Text, primary_key=True)
    alpha: Mapped[float] = mapped_column(Float, server_default=sql_text("1"))
    beta: Mapped[float] = mapped_column(Float, server_default=sql_text("1"))
```

Les colonnes des autres tables suivent le tableau « Modèle de données » de la spec ; le plan de phase de chaque
tâche les écrit en entier dans son étape de migration. Valeurs de statut sans contrainte en 0001 :
`compilations.status` ∈ {`proposed`, `approved`, `needs_review`, `rejected`} (contrainte ajoutée par la migration
0004, avec celles de `source_videos` ; `needs_review` vient de l'approbation, tâche 15, et `review build` liste ces
compilations, tâche 32).

### 5.11 Verrou (`rushes/lock.py`, tâche 2)

```python
@contextmanager
def heavy_lock(paths: Paths, *, wait: bool, timeout_s: float | None = None) -> Iterator[HeavyLock]: ...

class HeavyLock:
    def yield_if_express_waiting(self, *, before_yield: Callable[[], None]) -> bool: ...  # runner, entre deux éléments ; before_yield = ctx.models.release

@contextmanager
def express_intent(paths: Paths) -> Iterator[None]: ...   # la voie express annonce son attente
```

`fcntl.flock` sur `data/locks/heavy.lock` et `data/locks/express.lock` (volume local, jamais NFS). Le verrou est
libéré à la mort du processus ; le fichier n'est jamais supprimé. Deux `open()` du même fichier se bloquent, même
dans un processus : la voie express et `rushes refresh` passent donc le verrou qu'ils tiennent à `run_pipeline`
(`lock=hl, yield_to_express=False`, §5.3). Verrou de run : `data/locks/run-<id>.lock` (`run_lock_path`, tâche 40a),
tenu toute la commande, supprimé à la clôture.

### 5.12 Serveur `rushes serve` (tâche 36)

- Back : FastAPI + uvicorn, lancé par `rushes serve`. Corps et réponses typés par des modèles Pydantic ; le schéma
  OpenAPI génère le client TypeScript du front (`web/src/api/`, régénéré par `npm run gen:api`, vérifié en test).
  Routes (détail en tâche 36) : `GET /api/accounts`, `GET /api/posts?account=&test=false`, `GET /api/posts/{id}`, `GET /files/{account}/{post}/{name}` (vidéo,
  couverture ; requêtes Range), `POST /api/posts/{id}/published`, `POST /api/posts/{id}/discard`,
  `POST /api/posts/{id}/hook` (`{"rank": 2}`), `POST /api/inbox/instagram?account=<id>` (dépôt de l'export
  Instagram, L28 : corps brut = le fichier, en-tête `X-Filename`, pas de multipart ; `.csv` seulement, 5 Mo au plus,
  écrit atomiquement dans `data/inbox/` ; refus 400/413), `GET /healthz` ; tâche 26 : `GET /api/campaign?account=`,
  `POST /api/posts/{id}/submitted` (L16). La page a donc cinq écritures : Publié, Écarter, Accroche, Soumis, dépôt
  de l'export.
- Authentification : entièrement assurée par GoDoxy et PocketID (décision de Louis). `serve` ne vérifie aucune
  identité. Condition de sécurité : le conteneur n'est joignable que par GoDoxy. `compose.yaml` ne publie aucun port
  de `serve` sur l'hôte (`ports:` absent) et le place sur le seul réseau Docker de GoDoxy ; un test lit
  `compose.yaml` et échoue sinon. Les `POST` exigent un `Origin` égal à `serve.public_origin` (protection contre les
  requêtes croisées, pas une authentification).
- Mise en veille : GoDoxy arrête le conteneur `serve` au repos et le réveille à la première requête. `serve` démarre
  donc vite : `rushes.serve` n'importe ni torch, ni les modèles, ni FFmpeg (test sur `sys.modules`), démarrage
  mesuré sous 2 s. Il lit et écrit SQLite en WAL avec `busy_timeout`, à côté des minuteurs. Les étiquettes Docker
  exactes de GoDoxy (veille, réseau, OIDC) sont vérifiées dans sa documentation à la tâche 39.
- Front : `web/` (Vite, React, TypeScript, shadcn/ui, Tailwind v4), construit dans une étape Node de l'image et
  servi depuis `serve/static/`. Une colonne par compte, posts dans l'ordre de publication conseillé, aperçu, boutons
  Télécharger, Copier (par champ), Publié, Écarter, Accroche 2, Accroche 3 ; zone « Déposer l'export Instagram »
  (GoDoxy doit accepter un corps de 5 Mo, vérifié en tâche 39).
- `outbox/INDEX.html` reste généré par `package` (Jinja, sans JavaScript obligatoire) pour un usage hors serveur.

### 5.13 Notifications (`rushes/ops/notify.py`, tâche 20)

```python
class Notifier(Protocol):
    def send(self, title: str, body: str, *, priority: Literal[1, 2, 3, 4, 5] = 3, tags: Sequence[str] = (), click: str | None = None) -> None: ...
```

`NtfyNotifier` publie en JSON (`POST {url}` avec `{"topic", "title", "message", "priority", "tags", "click"}`,
titres accentués sans encodage d'en-tête) et `Authorization: Bearer <jeton>` ; le topic et le jeton sont lus dans
des secrets (`SecretStr`, jamais journalisés), depuis la variable nommée par la config ou le fichier désigné par
`<variable>_FILE` (secrets Compose). `NullNotifier` quand ntfy n'est pas configuré. Une réponse HTTP ≥ 300 de ntfy
lève `NotifyError(RushesError)` (`alerts_sent` non écrite). Les étapes ne l'appellent pas : elles émettent
`Message(alert="<clé>")`, envoyé et dédoublonné en fin de commande (tâche 20) par :

```python
def send_pending_alerts(ctx: Context, messages: Sequence[Message]) -> None: ...   # rushes/ops/alerts.py
```

appelée par `run_pipeline` après le `Summary`, et par toute commande qui enchaîne plusieurs `run_pipeline` pour
ses propres messages (voie express, tâche 28 : clé `creator_video:<v_key>`).

## 6. Stratégie de test

| Couche | Outil | Exécution |
| --- | --- | --- |
| Logique pure (`core/`) | pytest, données en mémoire | rapide |
| Base | SQLite dans `tmp_path`, migrations réelles | rapide |
| Média | vidéos synthétiques générées par FFmpeg (`tests/fixtures/generator.py`), 5 à 20 s | rapide si < 2 s par test, sinon `slow` |
| Réseau | cassettes (`CassetteYtDlp`, `FakeHttp`) | rapide |
| Modèles | doublures (`tests/fakes/models.py`) ; vrais modèles en `slow` | rapide / `slow` |
| Serveur | `fastapi.testclient` ; front : `npm run build`, `npm run lint`, client généré à jour, test de fumée Playwright | rapide / `slow` |
| Image | `docker build` puis `pytest -m slow` dans le conteneur, sur le serveur | tâches 42, 9, 20, 39 |

**Générateur de vidéos synthétiques** (`tests/fixtures/generator.py`, tâche 4) :

```python
@dataclass(frozen=True)
class SynthSpec:
    duration_ms: int
    size: tuple[int, int] = (1280, 720)
    fps: int = 30
    shots_ms: Sequence[int] = ()                    # instants de changement de plan (couleur de fond différente)
    black_ms: Sequence[Span] = ()
    silence_ms: Sequence[Span] = ()
    tone_hz: int = 440
    beeps_ms: Sequence[Span] = ()                   # pics sonores
    speech_like_ms: Sequence[Span] = ()             # bruit modulé que la doublure VAD reconnaît
    title_card_ms: Span | None = None               # carton de texte
    logo_corner: Literal["tl", "tr", "bl", "br"] | None = None
    moving_target: Sequence[tuple[int, int]] = ()   # (t_ms, x_px) d'un carré en mouvement, pour le recadrage
    face_image: Path | None = None                  # visage photo CC0 pour YuNet (tests slow)


def make_video(spec: SynthSpec, out: Path) -> Path: ...   # ffmpeg lavfi : color, testsrc2, sine, anoisesrc, drawbox, drawtext, overlay
```

**Cassettes** : métadonnées réelles enregistrées une fois (`scripts/record_cassette.py`), nettoyées (aucune URL de
média, aucun jeton), versionnées dans `tests/fixtures/cassettes/`. Aucune vidéo protégée n'entre dans le dépôt.

**Test de couverture des règles** (`tests/test_rules_coverage.py`, tâche 2) : lit la liste des règles actives de
`docs/spec.md` et échoue si une règle n'a aucun test dont le nom commence par `test_rN_` et n'est pas en attente.
Une règle couverte en partie (déjà un test, d'autres tâches à venir) peut rester dans `rules_pending.toml` jusqu'à
sa dernière tâche (§7) ; la liste diminue à chaque tâche et doit être vide à la fin de la tâche 40a (R14 se termine
là ; vérifié à la tâche 41).

**Commandes non implémentées** (`tests/test_cli.py`, tâche 2) : `PENDING: dict[str, str]` (commande → tâche qui
l'implémente, en texte car les tâches ont des suffixes, ex. `"8a"`) et `IMPLEMENTED: tuple[str, ...]`. Chaque tâche
qui implémente une commande la déplace de `PENDING` vers `IMPLEMENTED` et ajoute `tests/test_cli.py` à ses Files.

**Fixtures partagées** (`tests/conftest.py`) : `ctx`, `cli_env` (2), `session` (3), `two_verticals_ctx` (22), `api`,
`api_nostatic`, `make_api(...)` (36), `synth_ready` (40a). Les tests navigateur sont des tests Playwright npm
(`web/tests/*.spec.ts`), jamais une fixture pytest.

## 7. Matrice règles → tests → tâches

| Règle | Tests (noms exacts des phases ; tâche entre parenthèses) | Tâches |
| --- | --- | --- |
| R1 | `test_r1_overlap_over_2s_other_account_refused`, `test_r1_overlap_within_tolerance_allowed`, `test_r1_phash_close_other_account_refused` (3) ; `test_r1_audit_detects_cross_account_overlap` (40a) | 3, 40a |
| R2 | `test_r2_part_and_bestof_same_account_allowed`, `test_r2_two_parts_same_passage_refused` (3) ; `test_r2_assign_ignores_bestof_items` (8a) ; `test_r2_bestof_items_never_assigned_alone`, `test_r2_bestof_item_overlapping_part_same_account_assigned` (13) ; `test_r2_late_detection_retires_reassigned_duplicate` (17) | 3, 8a, 13, 17 |
| R3 | `test_r3_unapproved_never_rendered` (7a), `test_r3_unapproved_never_assigned` (8a), `test_r3_unapproved_never_packaged` (8b), `test_r3_manual_approval_below_threshold_allowed` (32) | 7a, 8a, 8b, 32 |
| R4 | `test_r4_rotation_periods_overlap_rejected` (fait), `test_r4_new_episode_goes_to_current_rotation` (4), `test_r4_generated_rotation_drives_new_episodes`, `test_r4_existing_episode_keeps_owner_after_rotation` (16a) | 4, 16a |
| R5 | `test_r5_no_cut_during_speech`, `test_r5_no_cut_1500ms_before_peak` (6) ; `test_r5_cut_reads_heatmap_peaks` (13) | 6, 13 |
| R6 | `test_r6_family_min_above_60s_rejected`, `test_r6_family_min_not_above_max_rejected` (faits) ; `test_r6_segment_out_of_bounds_never_proposed`, `test_r6_target_seed_recorded` (6) ; `test_r6_creator_part_bounds_after_autocut` (24) ; `test_r6_theme_target_and_seed_recorded` (30) | 6, 24, 30 |
| R7 | `test_r7_render_matches_ffprobe_spec`, `test_r7_integrated_loudness_minus14`, `test_r7_full_length_render_medium_preset` (7a) ; `test_r7_advanced_mix_integrated_loudness` (35) | 7a, 35 |
| R8 | `test_r8_square_is_default_layout` (7a) ; `test_r8_layout_fallback_order`, `test_r8_crop_follows_moving_target`, `test_r8_never_pans_across_cut` (34) | 7a, 34 |
| R9 | `test_r9_total_parts_never_shown`, `test_r9_text_inside_safe_margins` (7b) ; `test_r9_renumber_without_gap`, `test_r9_stale_numbers_after_discard` (16b) ; `test_r9_discard_part_marks_following_pending` (36) | 7b, 16b, 36 |
| R10 | `test_r10_dialogue_cartoon_gets_subtitles_in_brand_language` | 25 |
| R11 | `test_r11_hook_under_8_words`, `test_r11_hook_similarity_below_085`, `test_r11_three_distinct_types`, `test_r11_hook_never_reveals_punchline`, `test_r11_never_unfiltered_fill` | 14 |
| R12 | `test_r12_name_within_80_chars_tiktok`, `test_r12_name_within_125_chars_instagram` | 8a |
| R13 | `test_r13_config_rejects_6_hashtags`, `test_r13_never_viral_foryou_in_config`, `test_r13_no_fyp_on_instagram_config`, `test_r13_unknown_token_refused`, `test_r13_required_hashtags_cover_every_vertical` (33) ; `test_r13_required_hashtags_tiktok_cartoons`, `test_r13_instagram_max_5_no_fyp`, `test_r13_never_viral_foryou` (8a) ; `test_r13_audit_detects_missing_required_hashtag` (40a) | 33, 8a, 40a |
| R14 | `test_r14_outbox_test_is_distinct_from_outbox` (fait) ; `test_r14_test_flag_for_unlicensed_series` (8a) ; `test_r14_unlicensed_series_goes_to_outbox_test` (8b) ; `test_r14_unlicensed_series_still_approved` (15) ; `test_r14_theme_compilation_one_subject_name_and_test_flag` (30) ; `test_r14_candidate_series_goes_to_outbox_test` (38) ; `test_r14_licence_granted_moves_showcase_post_to_outbox`, `test_r14_licence_revoked_alerts_and_keeps_ready_post`, `test_r14_licence_revoked_unpacked_post_goes_to_outbox_test` (40a) | 8a, 8b, 15, 30, 38, 40a |
| R15 | `test_r15_community_episode_never_cut` (10b) ; `test_r15_community_segment_resourced_to_official`, `test_r15_unmatched_stays_signal`, `test_r15_official_downloaded_after_community_compared`, `test_r15_new_community_signal_recomputes_unready_bestof` (15) | 10b, 15 |
| R16 | `test_r16_migration_idempotent` (fait), `test_r16_step_rerun_changes_nothing` (2), une variante par étape (liste sous le tableau) | 2 et chaque étape |
| R17 | `test_r17_post_traceability_complete` (8b) ; `test_r17_trace_source_times_with_interior_hole` (24) | 8b, 24 |
| R18 | `test_r18_ytdlp_params_metadata_only`, `test_r18_cookies_only_after_signin_error`, `test_r18_cookies_never_mode_raises_source_unavailable`, `test_r18_cookies_retry_failure_alerts_cookies_expired`, `test_r18_no_cookies_unless_allowed` (10a) ; `test_r18_download_falls_back_to_cookies_with_alert` (10b) ; `test_r18_no_cookies_for_watch_backfill_prospect` (28) | 10a, 28 |
| R19 | `test_r19_models_released_before_yielding_lock` (2) ; `test_r19_models_loaded_once_per_step`, `test_r19_one_heavy_model_at_a_time` (5) ; `test_r19_clip_loaded_once_for_many_episodes` (12a) ; `test_r19_voice_step_one_heavy_model` (35) | 2, 5, 12a, 35 |
| R20 | `test_r20_network_is_blocked_in_tests` | 2 |
| R21 | `test_r21_brand_without_language_is_refused`, `test_r21_creator_language_must_match_brand` (faits) ; `test_r21_transcription_in_brand_language` (12c) ; `test_r21_texts_in_brand_language` (14) ; `test_r21_voice_in_brand_language` (35) ; `test_r21_creator_pipeline_single_language` (22) | 12c, 14, 35, 22 |
| R22 | `test_r22_unauthorized_creator_goes_to_outbox_test` | 8b |
| R23 | `test_r23_caption_without_credit_refused`, `test_r23_campaign_mentions_required`, `test_r23_prospect_credit_defaults_to_name` | 26 |
| R24 | `test_r24_cut_waits_for_transcription` (12c) ; `test_r24_creator_without_words_needs_review` (15) ; `test_r24_creator_cuts_only_at_sentence_end` (24) | 12c, 15, 24 |
| R25 | `test_r25_creator_subtitles_highlight_current_word_two_lines` | 25 |
| R26 | `test_r26_creator_crop_follows_face`, `test_r26_no_face_falls_back_to_action` | 25 |
| R27 | `test_r27_registry_applies_to_creators` | 3 |
| R28 | `test_r28_campaign_post_listed_in_weekly_report` | 26 |
| R29 | `test_r29_authorized_channel_checked_every_6h`, `test_r29_watch_picks_video_already_listed_by_backfill` | 28 |
| R30 | `test_r30_express_lane_goes_through_approval_and_registry` | 28 |
| R32 | `test_r32_prospect_never_publishable_without_yaml`, `test_r32_prospect_never_changes_existing_channel_status` (27) ; `test_r32_candidate_series_never_publishable_without_yaml` (38) | 27, 38 |
| R33 | `test_r33_simulated_can_assign_writes_nothing` (3) ; `test_r33_auto_approval_needs_threshold_and_rules` (15) | 3, 15 |
| R34 | `test_r34_logo_marks_episode` (11) ; `test_r34_logo_detected_blocks_segment` (15) | 11, 15 |
| R35 | `test_r35_ready_post_expires_after_7_days` (16b) ; `test_r35_audit_stale_ready_post_margin` (40a) | 16b, 40a |
| R36 | `test_r36_collectors_never_use_cookies` (17) ; `test_r36_market_never_uses_cookies` (31) | 17, 31 |
| R37 | `test_r37_discard_releases_segment`, `test_r37_discarded_not_reproposed_to_account` (16b) ; `test_r37_discard_button_releases_segment` (36) ; `test_r37_discard_counts_as_failure`, `test_r37_system_discard_not_counted` (18) | 16b, 36, 18 |
| R38 | `test_r38_failed_tests_keep_previous_image` | 20 |
| R39 | `test_r39_no_remote_ai_client_imported` | 2 |
| R40 | `test_r40_vendor_dir_requires_vendor_md` | 2 |
| R41 | `test_r41_vendor_check_absent_while_vendor_empty` | 2 |
| R43 | `test_r43_episode_always_gets_parts` (6) ; `test_r43_episode_always_gets_parts_and_bestof`, `test_r43_no_signal_gives_level_3`, `test_r43_simulated_peaks_give_level_1` (13) | 6, 13 |
| R44 | `test_r44_episode_requires_single_owner` (fait) ; `test_r44_owner_mismatch_refused` (3) ; `test_r44_compilation_uses_own_episodes_only` (30) ; `test_r44_launch_reanchor_keeps_existing_owners` (41) | 3, 30, 41 |
| R45 | `test_r45_cartoon_interior_silence_kept` (4) ; `test_r45_autocut_creators_only` (24) | 4, 24 |
| R46 | `test_r46_sponsorblock_categories_removed`, `test_r46_missing_sponsorblock_not_error` | 24 |
| R47 | `test_r47_secret_never_in_repr_or_error` (fait, adapté en 33) ; `test_r47_no_secret_in_any_output` (2) ; `test_r47_cookie_values_never_logged` (10a) ; `test_r47_doctor_never_prints_secret` (39) | 2, 10a, 39 |
| R48 | `test_r48_no_publication_client_in_code` (33) ; `test_r48_serve_never_calls_platforms` (36) | 33, 36 |
| R49 | `test_r49_no_business_account_requirement` | 33 |
| R50 | `test_r50_account_originality_defaults_to_settings` (33) ; `test_r50_originality_switch_by_config_only`, `test_r50_both_modules_render` (35) | 33, 35 |
| — | `test_serve_not_published_on_host`, `test_serve_imports_no_heavy_module`, `test_serve_post_requires_same_origin` (36 ; sécurité et veille de `serve`, sans règle) ; `test_audit_ignores_published_and_ecartes_folders` (40a, faux positifs de l'audit R14) | 36, 40a |

R16, une variante par étape : `test_r16_clean_rerun_changes_nothing` (4), `test_r16_analyze_rerun_changes_nothing` (5),
`test_r16_cut_rerun_changes_nothing` (6), `test_r16_render_rerun_changes_nothing` (7a),
`test_r16_meta_rerun_changes_nothing` (8a), `test_r16_run_twice_changes_nothing` (8b),
`test_r16_discover_rerun_within_refresh_changes_nothing` (10a), `test_r16_ingest_youtube_rerun_changes_nothing` (10b),
`test_r16_split_rerun_changes_nothing` (11), `test_r16_vision_rerun_changes_nothing` (12a),
`test_r16_sounds_rerun_changes_nothing` (12b), `test_r16_transcribe_rerun_changes_nothing` (12c),
`test_r16_bestof_rerun_changes_nothing` (13), `test_r16_hooks_rerun_changes_nothing` (14),
`test_r16_approve_rerun_changes_nothing` (15), `test_r16_queue_rerun_changes_nothing` (16b),
`test_r16_reframe_rerun_changes_nothing` (34), `test_r16_voice_rerun_changes_nothing` (35),
`test_r16_refresh_rerun_changes_nothing` (36), `test_r16_stats_rerun_changes_nothing` (17),
`test_r16_bandits_rerun_changes_nothing` (18), `test_r16_recalibrate_rerun_same_week_changes_nothing` (37),
`test_r16_creator_clean_and_cut_rerun_changes_nothing` (24), `test_r16_faces_rerun_changes_nothing` (25),
`test_r16_meta_with_campaign_rerun_changes_nothing` (26), `test_r16_prospect_rerun_same_week_changes_nothing` (27),
`test_r16_watch_and_backfill_rerun_change_nothing` (28), `test_r16_market_rerun_same_week_changes_nothing` (31),
`test_r16_compile_themes_rerun_changes_nothing` (30), `test_r16_review_apply_twice_changes_nothing` (32),
`test_r16_discover_series_rerun_same_week_changes_nothing` (38), `test_r16_licence_sync_rerun_changes_nothing` (40a).

## 8. Matrice spec → tâches

| Section de la spec | Tâches |
| --- | --- |
| Mission, autonomie, vitrine | 10, 16, 31, 37, 38 |
| Comptes et rotation | 33, 16a |
| Familles de durée | 6, 13, 37 |
| Volumes et créneaux | 16, 37 |
| Parties, ordre, numérotation | 6, 7b, 16b |
| Légendes, hashtags, couvertures | 8a, 31 |
| Architecture, stack, arborescence | 2, 42, 9, 39 |
| Configuration | 1, 33 |
| Stockage des fichiers | 1, 4, 8b, 20 |
| Modèle de données | 1, 33 et migrations de la section 5.10 |
| Étape 0 séries en tendance | 38 |
| Étape 1 découverte YouTube | 10 |
| Étape 2 étude de marché | 31 |
| Étapes 3–4 ingestion, nettoyage | 4, 11 |
| Étape 5 analyse | 5, 12a, 12b, 12c |
| Étape 6 découpage | 6, 13 |
| Étape 7 compilations | 30 |
| Étape 8 accroches | 14, 18 |
| Étape 9 approbation | 15, 32 |
| Étape 10 attribution | 8a, 13, 16a |
| Étape 11 file | 16b |
| Étape 12 rendu, mises en page, originalité | 7a, 7b, 34, 35 |
| Étapes 13–14 métadonnées, paquet | 8a, 8b |
| Étape 15 serve | 36 |
| Étape 16 statistiques | 17, 36 (dépôt de l'export Instagram) |
| Verticale créateurs | 22, 24–28 |
| Algorithmes clés | 6, 10a, 11, 13, 14, 15, 16a, 16b, 18, 31, 34 |
| Minuteurs, charge, verrou | 2, 9, 20 |
| Publication manuelle | 8b, 36 |
| Statistiques et détection | 17, 37 |
| Notifications | 20 |
| Sortie console | 2 |
| Code tiers et mises à jour | 2 (R40, R41), 20 (R38) |
| Déploiement | 42, 9, 39 |
| Répétition, lancement, rodage | 40a, 40b, 41 |

## 9. Budget mémoire et calcul (serveur, 16 Go partagés)

| Élément | Pic mesuré ou estimé |
| --- | --- |
| Base Python + torch + numpy | ≈ 0,5 Go |
| Whisper small int8 / base int8 | 1,0 / 0,8 Go (mesuré) |
| CLIP ViT-B/32 (safetensors) | ≈ 0,8 Go |
| CED-mini | < 0,2 Go |
| Silero VAD / YuNet | 0,24 / 0,05 Go (mesuré) |
| Qwen3-4B Q4\_K\_M, contexte 2048 | ≈ 3,1 Go |
| Supertonic 3 | ≈ 0,3 Go |
| FFmpeg x264 1080×1920 | 0,5 à 1 Go |
| `rushes serve` | < 0,2 Go |
| ntfy | ≈ 0,1 Go |

Conteneur des commandes : `mem_limit: 6g`, `cpus: 4`, `cpu_shares: 512`, `OMP_NUM_THREADS=4`, `nice -n 10` au
démarrage ; un seul modèle lourd à la fois (`ModelHub`) ; jamais d'encodage pendant que le modèle de langage est
chargé ; modèles déchargés avant de céder le verrou à la voie express (section 5.3). Conteneur `serve` :
`mem_limit: 512m`, mis en veille par GoDoxy. Premières mesures à la tâche 42 (modèles seuls), vraies valeurs à
la tâche 9 (pipeline) ; si un poste déborde : Whisper base, Qwen3-1.7B, preset x264 plus rapide.

## 10. Prêt pour la production

**Avant le lancement (porte de la phase 6, tâches 39, 40a, 40b) :**

Les cases marquées « (39-8) » sont exécutées par l'étape 8 de la tâche 39 (contrôles d'installation).

- [ ] Toutes les tâches des phases 1 à 6 terminées ; `tests/rules_pending.toml` vide depuis la fin de la tâche 40a.
- [ ] `uv run pytest -m "not slow"` vert sur le Mac ; `pytest -m slow` vert dans l'image sur le serveur
  (`docker compose --profile test run --rm -T rushes-test pytest -m slow`, 39-8) ; ruff et mypy propres.
- [ ] Image déployée sur l'EliteDesk ; limites vérifiées par `docker inspect` (`HostConfig.Memory`, `NanoCpus`) (39-8).
- [ ] Minuteurs actifs ; verrou vérifié : un `rushes run` lancé pendant le run de nuit avec `--no-wait` sort en 3
  (39-8).
- [ ] Sauvegarde nocturne, copie hors RAID et restauration testées.
- [ ] Alertes ntfy reçues sur l'iPhone (`scripts/rushes notify test`, une par clé, 39-8).
- [ ] `rushes serve` joignable depuis le Mac derrière GoDoxy et PocketID ; refus sans authentification vérifié ;
  port de `serve` injoignable depuis le réseau local ; réveil après mise en veille vérifié ; dépôt d'un export
  Instagram de 4,9 Mo accepté (39-8).
- [ ] Charte de chaque marque validée par Louis sur des rendus d'exemple, photo de profil de chaque marque déposée dans
  `config/avatars/` (avant le premier rendu sur le serveur, tâche 9).
- [ ] Répétition générale de 7 jours : stock produit pour les 8 comptes, aucune violation des règles dans les
  rapports, mémoire tenue (aucun `oom_kill` et pic de mémoire anonyme sous 6 Go ; `memory.peak`, qui compte le cache
  de pages, seulement journalisé), aucun run `failed` hors exercices, aucune intervention manuelle hors exercices
  (`rushes rehearsal report` vert : `reports/rehearsal-<date>.json` avec `"ok": true`, lu par `launch check`).
- [ ] Installation refaite depuis le dépôt en suivant `docs/ops/runbook.md` seul ; `rushes doctor` vert ; retour
  arrière (`deploy.sh --rollback`) exercé.
- [ ] `rushes launch check` vert juste avant `rushes launch start`.
- [ ] Séries et créateurs à publier passés `licensed: true` / `authorized: true` par Louis (sinon seule la vitrine existe).
- [ ] Gabarits génériques d'accroches (`config/hooks/rules.yaml`, ≥ 8 par type et par langue, rédigés à la tâche 14)
  relus et corrigés par Louis (L36).
- [ ] Test de Louis : programmation TikTok Studio disponible ou non sur un compte personnel (noté dans `decisions.md`).

**Rodage (tâche 41, après le lancement) :**

- [ ] Les posts publiés à la main sont détectés seuls (`detect-published`) sur chaque compte.
- [ ] Statistiques TikTok collectées toutes les 6 h ; premier export Instagram importé (en-têtes réels validés).
- [ ] Bandits et recalage (créneaux, parts, seuils, mises en page) actifs après 3 semaines, valeurs enregistrées
  dans `derived_values`.
- [ ] Statut du compte Instagram et éligibilité TikTok vérifiés chaque semaine ; décision de Louis sur le module
  avancé compte par compte.

## 11. Décisions de rédaction prises par moi (à relire)

- `hook_stats` de la spec devient `bandit_arms` (une table pour les bandits des accroches et des mises en page).
- Exécution de la tâche 33 en premier : elle retire Postiz avant que d'autres tâches ne s'appuient sur la config.
- FastAPI pour `serve` : client TypeScript généré depuis OpenAPI, utile au futur tableau de bord ; front React +
  shadcn/ui + Tailwind v4 pour respecter la règle UI du dépôt.
- Codes de sortie 3, 4 et 5 ajoutés (verrou, succès partiel, source indisponible), avec un ordre de priorité.
- Un journal JSON complet dans `data/logs/` en plus de la console.
- Poids des modèles téléchargés par une commande explicite `rushes models fetch`, jamais à la volée pendant un run
  de nuit, vérifiés par sha256.
- Historique des valeurs déduites (`derived_values_history`).
- Tâche 20 = écrire et tester les unités ; tâche 39 = les installer sur le serveur.

Ajoutés en rédigeant la phase 2 (à relire, détail dans `decisions.md`) : valeurs recalables facultatives dans la
config ; tâches 10 et 16 découpées ; facettes d'analyse en étapes distinctes (R19) ; épisodes communautaires jamais
découpés, re-sourcés en signaux du best-of officiel (R15) ; logo → `rejected` ; épisodes enfants d'une compilation ;
carte de fin avec photo de profil, @compte et « Abonne-toi » (L10 remplace l'image figée) ; rotation de 21 jours ancrée au
lundi du lancement (L6).

Ajoutés en rédigeant la phase 3 (à relire, détail dans `decisions.md`) : migrations renumérotées dans l'ordre
d'exécution ; « Accroche 2/3 » rendue par `rushes refresh`, déclenché par systemd sur une demande déposée par
`serve` ; détection des posts expirés publiés quand même ; résultat d'un post jugé une fois à 72 h ; bandit des
mises en page limité à `square`/`vertical`, qui recale la part de vertical ; exploration (10 % des accroches, 25 %
des créneaux) ; méthodes de recalage ; profils de run et sauvegarde nocturne ; alertes par `Message.alert` ;
cible Docker `test` ; installation provisoire des unités pour la porte de la phase 3.

Ajoutés en rédigeant la phase 4 (à relire, détail dans `decisions.md`) : table de stratégies par verticale ;
voie express qui propose le best-of provisoire et les premières parties ; créateurs de la config suivis même non
autorisés (vitrine) ; `youtube_channel` facultatif ; mise en page des créateurs selon R8 ; SponsorBlock retiré de
`clean.mp4`, autocut au rendu et durées vérifiées après autocut ; citation en tête des accroches ; crédit au nom du créateur (L19), prioritaire
sur la longueur de légende ; vidéo dans une autre langue écartée ; rattachement d'un prospect à son YAML ; migration
0015 pour la tâche 28 ; accroche déplacée sous le bandeau (correction de la phase 1) ; soumission des campagnes par
un bouton « Soumis » de la page (L16), la CLI en secours.

Ajoutés en rédigeant la phase 5 (à relire, détail dans `decisions.md`) : panel vérifié par la page publique `embed` ;
réserve de hashtags par écart de vues, purgée par nos propres résultats ; hashtag nostalgie choisi sur le panel
TikTok ; compte à rebours des compilations et suggestions de thèmes à copier dans la config ; page de revue hors
ligne construite depuis `web/` ; séries candidates validées par une chaîne officielle, audio fixé après transcription.

Ajoutés en rédigeant la phase 6 (à relire, détail dans `decisions.md`) : déploiement par script avec retour
arrière automatique de l'image (et de la base seulement si sa révision est inconnue de l'image précédente, après une
sauvegarde `pre-rollback`) ; `rushes doctor` ; audit nocturne des règles avec alerte de priorité
5 ; bascule des licences par remise en file, dans les deux sens ; origine, mémoire anonyme de pointe et `oom_kills`
de chaque run (migration 0017) ; répétition avec un calendrier d'exercices ; date de lancement enregistrée et critères de rodage
calculés. Validé par Louis le 2026-11-02 : bouton « Publié » utilisé la plupart du temps, détection en secours
(le rodage mesure le rattachement de chaque post à sa vidéo publique) ; créateurs en carré par défaut avec du
vertical de temps en temps (P27, R8).

Ajoutés par la relecture à froid n°2 (2026-11-02, détail dans `decisions.md`) : voie express et `rushes refresh`
qui passent leur verrou lourd à `run_pipeline` (`lock`, `yield_to_express=False`) et combinent leurs codes ;
`rushes refresh` lancé par son unité (plus de profil `refresh`) ; code 5 porté par `Outcome.source_unavailable` ;
unités systemd avec `SuccessExitStatus=4 5` ; `--force` qui épargne tout élément déjà attribué, calibrage par
`approve --dry-run` ; cookies YouTube réservés à la découverte ; `tts` léger ; créneaux par défaut selon le fuseau
de la marque ; compilations `needs_review` ; numérotation dans `queue/numbering.py` ; alertes envoyées par
`send_pending_alerts` ; verrou de run contre les faux « interrompu » ; mémoire jugée sur la mémoire anonyme ;
`rules_pending.toml` vide à la fin de la tâche 40a. Décisions de Louis : R19 précisée (L23), vitrine expirée
réattribuée (L24), une compilation thématique par sujet (L25), créneau express en plus (L27), dépôt de l'export
Instagram sur la page (L28), durées toujours au-dessus d'une minute (L29), Peak Moments alimenté par Beta Squad,
CaseOh et Dude Perfect après étude de marché (L26, L30, L31 ; `docs/research/2026-11-peak-moments-etude-marche.md`).

Validés par Louis le 2026-11-01 : tâche 42 d'essai de faisabilité juste après la 33, `ModelHub.release()` avant de
céder le verrou, FastAPI, découpage de 7, 8 et 12, et les points ci-dessus. Refusé : un en-tête secret partagé entre
le proxy et `serve` ; GoDoxy gère seul l'authentification.
