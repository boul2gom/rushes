# Phase 3 — Publication et mesure : plan d'implémentation

> **Pour les agents qui exécutent le plan :** sous-skill requis : `superpowers:subagent-driven-development`
> (recommandé) ou `superpowers:executing-plans`, tâche par tâche. Lire d'abord `docs/plans/00-master.md` (contrats
> transverses, sections citées « §5.x ») et les blocs **Interfaces** des phases 1 et 2 (noms réutilisés ici).
> Les étapes utilisent des cases `- [ ]`.

**But :** Louis prépare ses posts depuis une page servie par rushes. rushes détecte seul ce qui a été publié,
collecte les statistiques, apprend quels types d'accroche et quelles mises en page marchent, tourne seul par
minuteurs (sauvegarde, maintenance, alertes sur l'iPhone) et recale seul ses créneaux, parts, seuils et part de
vertical.

**Architecture :** `rushes serve` (FastAPI léger + page React) lit la base et `outbox/`, écrit trois décisions (une
quatrième, « Soumis », en tâche 26), reçoit l'export Instagram déposé par Louis dans `data/inbox/` (L28) et dépose
une demande que systemd transforme en rendu léger (36) ; collecteurs en lecture publique et import d'exports
(17) ; chaque post jugé une fois, bandits de Thompson et performance apprise (18) ; profils de run, unités systemd,
sauvegarde, rétention, mise à jour de yt-dlp, ntfy (20) ; recalage hebdomadaire écrit dans `derived_values` (37).

**Pile :** FastAPI, uvicorn (sans extras), Vite, React, TypeScript, shadcn/ui, Tailwind v4, `openapi-typescript` +
`openapi-fetch`, Playwright ; yt-dlp (TikTok, curl-cffi) ; numpy (tirages Beta) ; systemd, Docker Compose,
ntfy v2.36.0 ; `compression.zstd` (bibliothèque standard depuis Python 3.14).

**Spec :** `docs/spec.md`, `docs/decisions.md`, `docs/plans/00-master.md`.

## Global Constraints

Toutes celles de `00-master.md` §2 et des phases 1 et 2 s'appliquent. En plus, pour cette phase :

- **Migrations dans l'ordre d'exécution** : 0008 (tâche 36), 0009 (17), 0010 (18), 0011 (20) ; la table §5.10 du
  master est renumérotée en conséquence (0012 à 0016 pour les tâches 25, 26, 27, 28, 31).
- **`rushes.serve` reste léger** : n'importe jamais `torch`, `cv2`, `open_clip`, `faster_whisper`, `ctranslate2`,
  `llama_cpp`, `sherpa_onnx`, `scenedetect`, `yt_dlp`, `silero_vad`, ni `rushes.models`, `rushes.media`,
  `rushes.render`, `rushes.net` ; ne lance jamais FFmpeg ; aucune requête sortante (R48).
- **Plateformes** : lecture publique seulement, jamais de cookies ni d'identifiants des comptes de publication
  (R36) ; TikTok par `ctx.net.ytdlp` (`impersonate=chrome`, 2 s entre requêtes, R18).
- **Heures** : stockées en UTC ISO 8601 ; affichées dans le fuseau de la marque et à Paris.
- **Résultat d'un post** : jugé une seule fois, au plus tôt `stats.judge_after_h` (72) heures après sa publication
  (tâche 18) ; toutes les mesures apprises (bandits, performance, recalage) partent de ce jugement.
- **Valeurs recalées** : `derived.record(..., source="recalibrate:<méthode>")` ; une valeur écrite dans la config
  l'emporte toujours (`derived.resolve`) ; rien n'est recalé sous l'échantillon minimal.
- **Alertes** : une étape les signale par `Message(level="warn", text, alert="<clé>")` ; elles partent vers ntfy à
  la fin de la commande, dédoublonnées ; un échec d'envoi n'échoue jamais une commande.
- **Page web** : skill `frontend-design` avant d'écrire la page ; `break-ui` et `web-design-guidelines` avant de
  livrer ; `npm --prefix web run lint`, `npm --prefix web run build` et `npm --prefix web run gen:api` sans diff
  font partie de la fin de la tâche 36.
- Doublures ajoutées : `FakeNotifier` (`tests/fakes/notify.py`, liste des envois), `FakeHttp.post` qui enregistre
  `(url, body, headers)`, `tiktok_profile(videos)` (`tests/fakes/tiktok.py`, dictionnaire au format yt-dlp bâti sur
  le gabarit de la cassette réelle), `fake_bin(tmp_path, scripts)` (`tests/helpers.py`, exécutables factices placés
  en tête de `PATH` qui journalisent leurs arguments dans `calls.log`).

## Review Focus

1. **Double clic, deux onglets, clic contradictoire** (« Écarter » deux fois, « Publié » puis « Écarter ») : action
   idempotente (200, rien de plus), conflit refusé en 409 avec un message français, jamais deux lignes de journal
   ni deux échecs pour le bandit, y compris pour deux requêtes simultanées. Tests :
   `test_serve_actions_idempotent_and_conflicting`, `test_concurrent_discards_write_one_action` (tâche 36).
2. **Publication hors du chemin prévu** (post publié après son expiration, légende retouchée, vidéo publiée sans
   post) : le post expiré est reconnu et repasse `published`, la vidéo inconnue est listée dans le rapport et
   `status`, jamais ignorée. Test : `test_detect_published_after_expiry_and_unmatched_reported` (tâche 17).
3. **Export Instagram imparfait** (BOM, séparateur `;`, milliers « 1 234 » ou « 1,2 k », colonnes vides, même
   fichier déposé deux fois, format inconnu) : importé une fois, ou refusé avec la liste des colonnes trouvées.
   Test : `test_instagram_import_variants_and_duplicate_file` (tâche 17).
4. **ntfy absent ou en panne** (section absente, serveur injoignable, jeton faux) : la commande finit avec son code
   normal, un avertissement journalisé, l'alerte retentée au run suivant. Test :
   `test_notify_failure_never_fails_run` (tâche 20).
5. **Données de recalage pauvres ou extrêmes** (compte sans post, une vidéo à 100 fois la médiane, tous les posts à
   la même heure) : valeurs bornées, aucune modification sous l'échantillon minimal, aucun `NaN`. Test :
   `test_recalibrate_sparse_and_outlier_data_bounded` (tâche 37).

---

### Task 36 : `rushes serve` (page des posts prêts, actions, rendu léger à la demande)

**Files :**
- Create : `src/rushes/serve/__init__.py`, `src/rushes/serve/context.py`, `src/rushes/serve/schemas.py`,
  `src/rushes/serve/api.py`, `src/rushes/serve/auth.py`, `src/rushes/serve/files.py`, `src/rushes/serve/app.py`,
  `src/rushes/queue/refresh.py`, `src/rushes/ops/__init__.py`, `src/rushes/ops/requests.py`,
  `src/rushes/db/migrations/versions/0008_post_actions.py`, `scripts/export_openapi.py`, `scripts/serve_fixture.py`
- Create (front) : `web/package.json`, `web/package-lock.json`, `web/vite.config.ts`, `web/tsconfig.json`,
  `web/eslint.config.js`, `web/components.json`, `web/index.html`, `web/openapi.json` (généré),
  `web/src/main.tsx`, `web/src/App.tsx`, `web/src/index.css`, `web/src/api/client.ts`,
  `web/src/api/schema.d.ts` (généré), `web/src/components/{AccountColumn,PostCard,CopyField,HookSwitcher,DiscardDialog,ShareButton,InboxDrop}.tsx`,
  `web/src/components/ui/*` (shadcn), `web/playwright.config.ts`, `web/tests/smoke.spec.ts`
- Modify : `src/rushes/context.py` (`Filters.posts`), `src/rushes/paths.py` (`requests`), `src/rushes/queue/actions.py`
  (`mark_published`, `record_action`, `ActionConflict` ; `discard_post` appelle `record_action`),
  `src/rushes/queue/manual.py` (journal des choix d'accroche par `hook.txt`), `src/rushes/render/render.py`,
  `src/rushes/meta/meta.py`, `src/rushes/package/outbox.py`, `src/rushes/reframe/step.py` (34),
  `src/rushes/originality/steps.py` (35) (`select` respecte `Filters.posts`), `src/rushes/db/models.py`,
  `src/rushes/db/migrate.py` (`head_revision`), `src/rushes/config.py` (`ServeSettings.public_origin` normalisé),
  `src/rushes/cli.py` (`serve`, `refresh`), `src/rushes/steps.py`, `docker/Dockerfile` (étape Node),
  `docker/compose.yaml` (service `serve`), `pyproject.toml`, `.gitignore` (`src/rushes/serve/static/`,
  `web/node_modules/`, `web/test-results/`)
- Test : `tests/conftest.py` (fixtures `api`, `api_nostatic`, `make_api(...)`), `tests/test_serve_api.py`,
  `tests/test_serve_security.py`, `tests/test_serve_startup.py`, `tests/test_serve_inbox.py`, `tests/test_refresh.py`,
  `tests/test_compose.py`, `tests/test_config.py` (`public_origin`), `tests/test_cli.py` (`serve` et `refresh`
  passent de `PENDING` à `IMPLEMENTED`)

**Interfaces :**
- Consomme : `discard_post`, `choose_hook` (16b), `part_number`, `stale_numbers` (`queue/numbering.py`, 7b et 16b),
  `build_upload` (8b), `stock_days` (10b), `post_key`, `parse_key`, `make_engine` (WAL et `busy_timeout=5000` déjà
  posés), `load_config`, `migrate.current_revision` (1), `make_emitter`, `ServeSettings` (§5.9), `run_pipeline`
  (paramètres `lock`, `yield_to_express`, tâche 2), `express_intent`, `heavy_lock` (§5.11), `STEPS`, `atomic_path`.
- Produit :
  ```python
  # context.py (§5.1) : Filters gagne
  posts: tuple[int, ...] = ()        # les étapes de post (render, meta, package, reframe, commentary, voice) s'y limitent
  # paths.py
  @property
  def requests(self) -> Path: ...    # data/requests/<genre>/ : demandes déposées pour systemd (tâche 20)
  # ops/requests.py
  RequestKind = Literal["refresh", "update_ytdlp"]
  def post_request(paths: Paths, kind: RequestKind, *, at: datetime) -> Path: ...   # fichier <horodatage>-<pid>, écriture atomique
  def take_requests(paths: Paths, kind: RequestKind) -> int: ...                    # supprime les fichiers présents, renvoie leur nombre
  # db/migrate.py (ajout)
  def head_revision() -> str: ...    # tête de la chaîne Alembic empaquetée, sans base (servira aussi aux tâches 20, 39)
  # queue/actions.py (complète 16b)
  ActionKind = Literal["published", "discard", "hook", "submitted"]   # "submitted" : bouton « Soumis », tâche 26
  class ActionConflict(RushesError): ...                      # message français, ex. « déjà publié : rien à écarter »
  def mark_published(session: Session, post_id: int, *, at: datetime,
                     source: Literal["button", "detected", "export"], url: str | None = None) -> None: ...
  def record_action(session: Session, post_id: int, kind: ActionKind, *, value: str | None,
                    origin: Literal["page", "folder", "system"], at: datetime) -> int: ...
  # queue/refresh.py
  REFRESH_STEPS = ("render", "meta", "package")
  def pending_refresh_posts(session: Session) -> list[int]: ...   # actions hook non appliquées + stale_numbers()
  def run_refresh(ctx: Context) -> int: ...                       # code de sortie ; voir « rushes refresh »
  # serve/context.py
  @dataclass(frozen=True)
  class ServeContext: paths: Paths; config: Config; engine: Engine; ui: Emitter; clock: Clock
  def build_serve_context(*, environ: Mapping[str, str] | None = None) -> ServeContext: ...
  #   vérifie la révision de la base, ne migre pas, ne réécrit pas les miroirs ; sans ModelHub ni NetClients
  # serve/schemas.py
  class HookView(BaseModel): rank: int; text: str; type: str; chosen: bool
  class PostView(BaseModel):
      id: int; key: str; account_id: str; status: str; is_test: bool; subject: str; part_number: int | None
      family: str; layout: Layout | None; originality: Originality; duration_s: float; publish_at: PublishAt | None
      order_note: str | None; caption: str | None; hashtags: list[str]; hooks: list[HookView]
      video_url: str | None; cover_url: str | None; pending_render: bool; checklist: list[str]
  class AccountView(BaseModel): id: str; brand: str; platform: Platform; handle: str; stock_days: float; ready: int
  class HookChoice(BaseModel): rank: Literal[1, 2, 3]
  class DiscardBody(BaseModel): reason: str = Field("écarté depuis la page", max_length=200)
  class Health(BaseModel): status: Literal["ok"]; db: bool; static: bool
  class InboxDrop(BaseModel): name: str; duplicate: bool
  # config.py : ServeSettings.public_origin normalisé au chargement (validateur) :
  #   schéma et hôte en minuscules, sans barre finale ni chemin ; "https://Rushes.X/" → "https://rushes.x"
  # serve/auth.py
  def require_same_origin(request: Request, public_origin: str) -> None: ...   # 403 si Origin absent ou différent
  # serve/files.py
  ALLOWED_FILES: frozenset[str] = frozenset({"video.mp4", "cover.jpg"})
  INBOX_MAX_BYTES = 5 * 1024 * 1024
  def post_file(paths: Paths, account_id: str, post_key: str, name: str) -> Path: ...  # 404 hors liste ou hors outbox*
  def save_inbox_csv(paths: Paths, account_id: str, data: bytes) -> InboxDrop: ...  # voir « Dépôt de l'export Instagram »
  # serve/app.py
  def create_app(sctx: ServeContext) -> FastAPI: ...
  def run_server(sctx: ServeContext) -> None: ...
  ```
- Règles fixées :
  - Routes (§5.12) : `GET /api/accounts` → `list[AccountView]` ; `GET /api/posts?account=&test=false` →
    `list[PostView]` des posts `ready` (et des posts dont le rendu léger est en attente), triés par `planned_at` puis
    `id` ; `test=true` → seulement les posts de vitrine (`is_test = 1`), jamais mélangés ; `GET /api/posts/{id}` ;
    `GET /files/{account}/{post_key}/{name}` ; `POST /api/posts/{id}/published` ; `POST /api/posts/{id}/discard`
    (`DiscardBody`) ; `POST /api/posts/{id}/hook` (`HookChoice`) ; `POST /api/inbox/instagram?account=<id>` (corps
    = le fichier CSV, `X-Filename` = son nom) → `InboxDrop` ; `GET /healthz` → `Health`. Les `PostView`
    reprennent les champs de `upload.json` (`build_upload`) : la page et le fichier ne divergent jamais.
    `PostView.part_number` lit la colonne `posts.part_number` ; `PostView.pending_render = render_version IS NULL
    or id in stale_numbers()`.
  - Actions, toutes dans une transaction ; `published` et `hook` journalisées par `record_action(origin="page")`,
    `discard` par `discard_post` lui-même (16b : seul endroit qui journalise un écart, l'API n'écrit rien de plus) ;
    jamais de journal pour un no-op :

    | Action | Statuts admis | Déjà fait | Conflit (409) |
    | --- | --- | --- | --- |
    | `published` | `ready` | `published` → 200 sans écriture | `discarded`, `expired` : « post écarté » / « post expiré » |
    | `discard` | `planned`, `rendered`, `ready` | `discarded` → 200 sans écriture | `published` : « déjà publié : rien à écarter » |
    | `hook` | `rendered`, `ready` | rang déjà choisi → 200 sans écriture | `published`, rang absent |

    Transition conditionnelle : `UPDATE posts SET status = … WHERE id = ? AND status IN (<statuts admis>)` ;
    `rowcount = 0` → relire le post et répondre 200 (déjà fait) ou 409 (conflit). Deux requêtes simultanées
    (deux onglets, pool de threads d'uvicorn) donnent donc une seule écriture, un seul journal, un seul
    déplacement de dossier.
    `discard` appelle `discard_post(origin="page")` (R37) ; si `stale_numbers()` n'est pas vide ensuite (partie
    suivie d'autres parties rendues), il dépose aussi `post_request(paths, "refresh", at=now)` (renumérotation R9 :
    les suivantes passent `pending_render`, téléchargement désactivé, jusqu'au nouveau paquet). `hook` appelle
    `choose_hook` (16b : `render_version`, `caption` à NULL, statut `rendered`) puis `post_request(paths,
    "refresh", at=now)`. Le post reste listé avec
    `pending_render = true` jusqu'au nouveau paquet ; la page désactive alors le téléchargement. L'échec compté au
    bandit (R37) est fait en tâche 18 à partir du statut `discarded`.
  - `rushes refresh` (`run_refresh`) suit le schéma de la voie express (D1, B16) : `with express_intent(paths),
    heavy_lock(paths, wait=True) as hl:` (le run en cours, de nuit ou de veille, cède la main entre deux éléments) ;
    **puis seulement** `take_requests(paths, "refresh")` (une demande n'est jamais perdue si l'unité est tuée en
    attendant le verrou ; une demande arrivée pendant le run relance l'unité systemd), puis `run_pipeline(ctx,
    [STEPS[n]() for n in REFRESH_STEPS], Filters(posts=tuple(pending_refresh_posts(session))), command="refresh",
    lock=hl, yield_to_express=False)` ; rien en attente → code 0 sans étape. À la fin, `post_actions.applied_at`
    est posé pour les actions `hook` dont le post est de nouveau `ready`. Le rendu léger réutilise `reframe.json`,
    `voice.json` et `commentary.json` existants.
  - `Filters.posts` non vide : `render`, `meta`, `package`, `reframe`, `commentary` et `voice` ne sélectionnent que
    ces posts (la voie express de la tâche 28 s'en sert aussi).
  - Gestes de secours (16b) : `detect_manual_actions` journalise un choix d'accroche par `hook.txt` avec
    `record_action(kind="hook", origin="folder")` ; un écart par dossier déplacé est journalisé par `discard_post`
    (`origin="folder"`) ; un post déjà `discarded` (dossier déplacé par la page) est sauté, sans journal.
  - Démarrage : `rushes serve` refuse une section `serve` absente ou un `public_origin` qui contient `<` (code 2,
    « serve.public_origin manquant ou laissé en exemple ») ; puis vérifie `migrate.current_revision(db) ==
    head_revision()`, sinon code 1 (`RushesError`) « base pas à jour : lancer rushes db upgrade » (`serve` ne migre jamais et ne
    réécrit pas les miroirs : pas de course avec les minuteurs au réveil) ; puis `uvicorn.run(app, host, port,
    log_config=None, access_log=False, proxy_headers=False)` ; un seul événement `Message("info", "serve prêt sur
    <host>:<port>")`.
  - Erreurs non gérées : gestionnaire d'exceptions FastAPI qui émet `Message("error", …)` par `sctx.ui` (donc
    `redact`, journal) et répond 500 `{"detail": "erreur interne"}` sans trace ; un handler `logging` relié à
    l'émetteur remplace le `lastResort` de stderr pour les journaux `uvicorn` et `fastapi` (seul `rushes.ui` écrit
    sur la console).
  - Sécurité : `require_same_origin` sur tous les `POST` (en-tête `Origin` égal à `public_origin` normalisé) ;
    `GET` non contrôlés. En-têtes sur toutes les réponses : `Content-Security-Policy: default-src 'self'; img-src
    'self' blob: data:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'`,
    `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` ; `Cache-Control: no-store` sur `/api/*`,
    `public, max-age=31536000, immutable` sur `/assets/*`.
  - Fichiers : `post_file` résout `outbox/` ou `outbox_test/` selon `is_test`, refuse tout nom hors
    `ALLOWED_FILES`, tout chemin dont `resolve()` sort du dossier, tout lien symbolique ; `FileResponse` (requêtes
    Range gérées par Starlette ≥ 0.39), `Content-Disposition: attachment; filename="<account>-<p_key>.<ext>"`.
  - Dépôt de l'export Instagram (décision L28, cinquième écriture de la page) : `POST /api/inbox/instagram?account=<id>`,
    `require_same_origin` comme tout `POST`. Corps brut (pas de multipart : aucune dépendance `python-multipart`),
    lu par `request.stream()` en comptant les octets ; `X-Filename` encodé par `encodeURIComponent` côté page
    (nom accentué), décodé par `urllib.parse.unquote`, seulement pour vérifier l'extension. Refus : `account` qui n'est pas un compte Instagram de la
    config → 400 « compte Instagram inconnu » ; `X-Filename` sans extension `.csv` (casse ignorée) → 400
    « seul un fichier .csv est accepté (exporter en CSV) » ; corps vide → 400 ; plus de `INBOX_MAX_BYTES` (5 Mio)
    → 413 « fichier trop gros (5 Mo au plus) », lecture arrêtée, rien d'écrit. `save_inbox_csv` : nom
    `ig-<sha256[:16]>.csv` écrit par `atomic_path` dans `inbox/<account_id>/` (le compte est connu même si l'export
    n'a pas de colonne de compte) ; idempotent par empreinte du contenu : même nom déjà présent dans
    `inbox/<account_id>/` ou dans `inbox/_done/` (`*-ig-<sha256[:16]>.csv`) → 200 `duplicate=true`, rien d'écrit.
    `serve` ne lit ni n'importe le fichier : la tâche 17 le traite au run `stats` suivant (`stats_imports` le
    dédoublonne aussi).
  - Base occupée (`OperationalError` « database is locked » après `busy_timeout`) → 503 « base occupée, réessayer »,
    jamais de trace dans la réponse.
  - Statique : `serve/static/` (sortie de `npm run build`) servi à `/`, `index.html` pour toute route inconnue hors
    `/api` et `/files` ; dossier absent → `/` renvoie 503 et une page d'une ligne « Interface non construite :
    npm --prefix web run build » ; l'API fonctionne quand même.
  - Migration `0008_post_actions.py` : `post_actions(id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL REFERENCES
    posts(id), kind TEXT NOT NULL CHECK (kind IN ('published','discard','hook','submitted')), value TEXT, origin
    TEXT NOT NULL CHECK (origin IN ('page','folder','system')), at TEXT NOT NULL, applied_at TEXT)`, index
    `(post_id)` ; `'submitted'` est prévu dès ici pour le bouton « Soumis » (`mark_submitted` de la tâche 26 appelle
    `record_action`, aucune migration de plus). Un écart d'origine `system` (doublon, tâche 17) n'est jamais compté
    comme échec par le bandit (tâche 18, `test_r37_system_discard_not_counted` ; R37 ne vise que les écarts de Louis).
  - Front (`web/`) :
    - Onglets « À publier » et « Vitrine — NE PAS PUBLIER » (badge rouge) ; une colonne par compte (en-tête :
      handle, plateforme, jours de stock, posts prêts), défilement horizontal sur écran étroit.
    - `PostCard` : couverture (chargement différé), vidéo `<video preload="none" controls>` à la demande, titre
      « Série — Part N » ou titre du best-of, créneau conseillé (marque et Paris), `order_note` en avertissement,
      checklist ; boutons Télécharger la vidéo, Télécharger la couverture, Partager (`navigator.share({files})`
      quand `navigator.canShare` l'accepte : AirDrop depuis Safari), Copier la légende, Copier les hashtags
      (`navigator.clipboard.writeText` + toast), `HookSwitcher` (3 accroches avec leur type, la choisie
      surlignée), Publié, Écarter (`AlertDialog` de confirmation).
    - Rendu en attente : badge « Nouveau rendu en cours », téléchargements désactivés, liste relue toutes les
      15 s tant qu'un post est en attente. Erreurs 409 et 503 : toast avec le message du serveur.
    - `InboxDrop` : zone « Déposer l'export Instagram » dans l'en-tête de chaque colonne de compte Instagram
      (composants shadcn `card`, `button` ; glisser-déposer ou `<input type="file" accept=".csv">`) ; envoi par
      `fetch` (corps = le fichier, en-tête `X-Filename`) ; toasts « Export déposé : importé au prochain relevé »,
      « Déjà déposé », ou le message 400/413 du serveur ; refus local d'un fichier de plus de 5 Mo avant l'envoi.
    - Client : `openapi-fetch` typé par `src/api/schema.d.ts` ; `npm run gen:api` =
      `uv run python -I ../scripts/export_openapi.py openapi.json && openapi-typescript openapi.json -o src/api/schema.d.ts`.
    - Vite : proxy `/api` et `/files` vers `http://127.0.0.1:8080` en développement ; `build.outDir =
      "../src/rushes/serve/static"`, `emptyOutDir: true`. Textes en français, dates par `Intl.DateTimeFormat("fr-FR")`.
  - Image : étape `FROM node:<version LTS exacte>-slim@sha256:<digest> AS web` (`npm ci && npm run build`), sortie
    copiée dans `src/rushes/serve/static/` de l'image finale. Compose : service `serve` (même image,
    `command: ["rushes", "serve"]`, `mem_limit: 512m`, `cpus: 1`, `restart: unless-stopped`, réseau externe
    `${GODOXY_NETWORK}` seulement, mêmes volumes, aucun `ports:`) ; étiquettes GoDoxy en tâche 39. Le proxy
    GoDoxy doit accepter un corps de 5 Mo sur `/api/inbox/instagram` (vérifié en tâche 39).
  - Fixtures (`tests/conftest.py`, réutilisées par les tâches 26 et 40a) : `make_api(tmp_path, *, static: bool =
    True, posts=…, accounts=…) -> TestClient` ; `api` = `make_api(...)` avec la base décrite à l'étape 1 ;
    `api_nostatic` = `make_api(..., static=False)`.
  - Dépendances : `uv add fastapi uvicorn` ; `uv add --dev httpx`.

- [ ] **Step 1 : Tests qui échouent**

```python
# tests/conftest.py — fixture api : make_api(...) = TestClient(create_app(sctx)) sur une base de tmp_path avec st_tt
# et st_ig, quatre posts ready (parties 1, 2 et 3 d'un épisode, un best-of), un post de vitrine, fichiers factices
# dans outbox*/
# tests/test_serve_api.py
def test_healthz_ok(api):
def test_list_posts_by_account_in_planned_order(api):
def test_test_posts_only_in_vitrine_tab(api):              # test=true → seul le post de vitrine ; test=false → jamais lui
def test_post_view_matches_upload_json(api):               # caption, hashtags, publish_at, order_note, checklist == upload.json
def test_published_button_marks_post(api):                 # status published, published_at = horloge, post_actions origin "page"
def test_r37_discard_button_releases_segment(api):
    # discarded, assignment supprimée, dossier dans outbox/_ecartes/<p_key>/ ; exactement 1 ligne post_actions
    # (kind "discard", origin "page"), écrite par discard_post
def test_r9_discard_part_marks_following_pending(api):
    # parties 1, 2, 3 ready ; discard partie 2 → GET partie 3 : pending_render true ; un fichier dans
    # data/requests/refresh/ ; discard du best-of (aucune partie suivante) → aucune demande
def test_hook_button_requests_light_rerender(api):
    # rang 2 chosen, render_version NULL, caption NULL, status rendered, pending_render true,
    # une ligne post_actions (hook, page), un fichier dans data/requests/refresh/
def test_serve_actions_idempotent_and_conflicting(api):
    # discard ×2 → 200 et 200, une seule ligne post_actions ; published sur un post écarté → 409 « post écarté »
    # published puis discard sur un autre post → 409 « déjà publié : rien à écarter » ; hook rang déjà choisi → 200, aucune demande
def test_concurrent_discards_write_one_action(api):
    # deux threads, POST discard simultanés (Barrier) → réponses {200, 200}, 1 ligne post_actions, dossier déplacé une fois, aucun 500
def test_video_file_supports_range(api):                   # Range: bytes=0-99 → 206, 100 octets, Accept-Ranges: bytes
def test_db_locked_returns_503(api, monkeypatch):
def test_unhandled_error_goes_through_ui(api, monkeypatch):
    # route qui lève RuntimeError("tok-123-SECRET") → 500 {"detail": "erreur interne"}, sans trace ; un Message error
    # dans la sortie de sctx.ui, secret masqué « *** » ; rien sur sys.stderr (capsys)
# tests/test_serve_inbox.py (L28)
def test_inbox_drop_accepts_csv(api):
    # st_ig, X-Filename "export.csv", 2 Kio → 200 duplicate false ; data/inbox/st_ig/ig-<sha256[:16]>.csv, octets
    # identiques, aucun .tmp ; même corps redéposé → 200 duplicate true, toujours 1 fichier ; corps déjà dans
    # inbox/_done/20261116-ig-<…>.csv → duplicate true
def test_inbox_drop_rejects_wrong_type(api):              # X-Filename "export.xlsx" → 400 « exporter en CSV » ; corps vide → 400 ; rien d'écrit
def test_inbox_drop_rejects_too_large(api):               # 5 Mio + 1 octet → 413, rien d'écrit dans data/inbox/
def test_inbox_drop_rejects_unknown_or_tiktok_account(api):   # account=st_tt → 400 ; account=zz → 400
def test_inbox_drop_requires_same_origin(api):            # sans Origin → 403 ; Origin étranger → 403 ; rien d'écrit
# tests/test_serve_security.py
def test_serve_post_requires_same_origin(api):             # sans Origin → 403 ; Origin étranger → 403 ; Origin = public_origin → 200
def test_serve_files_rejects_traversal(api):               # "../../rushes.db", "upload.json", lien symbolique sortant → 404
def test_security_headers_present(api):
def test_r48_serve_never_calls_platforms():                # AST de src/rushes/serve : aucun import urllib.request, http.client, httpx, requests, rushes.net
def test_serve_refuses_placeholder_origin(cli_env, edit_yaml):   # public_origin "https://<domaine>" → code 2
def test_serve_refuses_outdated_db(cli_env):               # base à une révision < head → code 1 « lancer rushes db upgrade » ; base non modifiée
# tests/test_config.py (ajout)
def test_public_origin_normalized(edit_yaml):              # "https://Rushes.Example.org/" → "https://rushes.example.org" ; Origin du navigateur accepté
# tests/test_compose.py (ajout)
def test_serve_not_published_on_host():                    # service serve : pas de ports, réseau ${GODOXY_NETWORK} seul, mem_limit 512m
# tests/test_serve_startup.py (sous-processus python -I : la fixture réseau de pytest n'y est pas nécessaire, aucun socket)
HEAVY_MODULES = {"torch", "cv2", "open_clip", "faster_whisper", "ctranslate2", "llama_cpp", "sherpa_onnx",
                 "scenedetect", "yt_dlp", "silero_vad", "rushes.models", "rushes.media", "rushes.render", "rushes.net"}
def test_serve_imports_no_heavy_module(tmp_path):          # build_serve_context + create_app → HEAVY_MODULES ∩ sys.modules == ∅
def test_serve_starts_under_2s(tmp_path):                  # du lancement de l'interpréteur à GET /healthz 200 (TestClient) < 2,0 s
def test_openapi_snapshot_up_to_date():                    # create_app(...).openapi() == json.load(web/openapi.json)
def test_missing_static_serves_build_hint(api_nostatic):   # / → 503 et la phrase « npm --prefix web run build » ; /healthz → 200
# tests/test_refresh.py
def test_refresh_renders_only_pending_posts(ctx, synth):
    # 3 posts ready, accroche changée sur l'un → seul lui est rendu ; hook.txt = rang 2 ; caption.txt commence par
    # l'accroche de rang 2 ; cover.jpg refaite (sha256 changé) ; upload.json à jour ; les deux autres inchangés
def test_refresh_consumes_requests_and_noop_when_empty(ctx):
def test_refresh_takes_requests_only_after_heavy_lock(ctx):
    # vraie paire de verrous dans tmp_path ; un thread tient le verrou lourd (étape heavy à 2 éléments,
    # yield_to_express=True) ; run_refresh → la demande reste dans data/requests/refresh/ tant que le verrou n'est
    # pas cédé ; le thread cède entre ses deux éléments ; refresh finit (code 0, délai < 10 s), demande consommée
def test_filters_posts_respected_by_post_steps(ctx, synth):
    # deux posts en attente de rendu ; Filters(posts=(a,)) → render, meta, package, reframe, commentary, voice
    # ne sélectionnent que a
def test_manual_gestures_logged_as_folder_actions(ctx):
    # hook.txt modifié → 1 ligne (hook, folder) ; dossier déplacé dans _ecartes/ → 1 ligne (discard, folder) ;
    # dossier d'un post déjà écarté par la page → aucune ligne de plus
def test_r16_refresh_rerun_changes_nothing(ctx, synth):
```

- [ ] **Step 2 : Lancer** → FAIL (`rushes.serve` absent).

- [ ] **Step 3 : Implémenter le serveur** (migration 0008, `ops/requests.py`, actions, `refresh`, `serve/*`,
  `scripts/export_openapi.py` qui écrit `create_app(<contexte factice>).openapi()` trié, indentation 2).

- [ ] **Step 4 : Lancer** → PASS ; retirer R9 de `rules_pending.toml` (son dernier test est
  `test_r9_discard_part_marks_following_pending`). Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Page** — invoquer le skill `frontend-design`, puis créer `web/` (Vite React TypeScript,
  `npx shadcn@<version exacte> init`, composants `button card badge tabs alert-dialog tooltip sonner scroll-area
  skeleton`), écrire les composants ci-dessus ; `npm --prefix web run gen:api && npm --prefix web run lint &&
  npm --prefix web run build` → aucune erreur, `git diff --exit-code web/openapi.json web/src/api/schema.d.ts`.

- [ ] **Step 6 : Éprouver la page** — skill `break-ui` avec des données extrêmes (`scripts/serve_fixture.py
  --worst-case` : légende de 2 200 caractères, 40 posts dans une colonne, emoji et texte arabe, couverture
  absente, 8 comptes, compte vide) ; puis `web-design-guidelines` ; corriger tout ce qui casse.

- [ ] **Step 7 : Test de fumée** — `web/tests/smoke.spec.ts` (Playwright, `webServer` =
  `uv run python -I scripts/serve_fixture.py --port 8765`, Origin fixé à `http://127.0.0.1:8765`) : deux colonnes
  affichées ; « Copier la légende » remplit le presse-papiers ; « Écarter » puis confirmer → la carte disparaît ;
  « Accroche 2 » → badge « Nouveau rendu en cours » ; un CSV déposé dans « Déposer l'export Instagram » → toast
  « Export déposé ». Run : `npm --prefix web run test:e2e` → PASS.

- [ ] **Step 8 : Image et Compose** — étape Node du Dockerfile, service `serve` ;
  `docker compose -f docker/compose.yaml build` puis `docker compose -f docker/compose.yaml run --rm -T serve
  python -I -c "import rushes.serve.app"` → aucune erreur. `uv run pytest -m "not slow"`, ruff, mypy verts
  (R37 reste en attente pour la tâche 18).

- [ ] **Step 9 : Commit proposé** — `feat(serve): ready-posts page with publish, discard and hook actions, instagram export drop, light re-render on request`

---

### Task 17 : Collecteurs TikTok et Instagram, `posts detect-published`

**Files :**
- Create : `src/rushes/stats/__init__.py`, `src/rushes/stats/tiktok.py`, `src/rushes/stats/imports.py`,
  `src/rushes/stats/detect.py`, `src/rushes/stats/metrics.py`, `src/rushes/stats/steps.py`,
  `src/rushes/db/migrations/versions/0009_metrics.py`, `tests/fixtures/instagram/export_en.csv`,
  `tests/fixtures/instagram/export_fr.csv`, `tests/fixtures/instagram/manual.csv`,
  `tests/fixtures/instagram/SOURCES.md`, `tests/fakes/tiktok.py`, cassette du profil TikTok
- Modify : `src/rushes/net/ytdlp.py` (TikTok : jamais de cookies), `src/rushes/queue/actions.py`
  (`mark_published` : `platform_post_id`, `platform_url`), `src/rushes/db/models.py`, `src/rushes/config.py`
  (`StatsSettings`), `src/rushes/steps.py`, `src/rushes/cli.py` (`stats collect`, `posts detect-published`),
  `src/rushes/status.py` (« Publications non rapprochées »), `docs/decisions.md`, `docs/spec.md` (point à vérifier)
- Test : `tests/test_stats_tiktok.py`, `tests/test_stats_imports.py`, `tests/test_detect.py`, `tests/test_cli.py`
  (`stats collect` et `posts detect-published` passent de `PENDING` à `IMPLEMENTED`)

**Interfaces :**
- Consomme : `YtDlp.extract` (§5.8), `CassetteYtDlp`, `mark_published`, `record_action`, `discard_post` (36, 16b),
  `registry.assign`, `Account.handle`, `Paths.inbox`, `SourceUnavailable`, `Message.alert` (tâche 2).
- Produit :
  ```python
  class StatsSettings(_Model):
      tiktok_videos: int = 60; match_window_h: int = 48; caption_prefix_chars: int = 40
      caption_min_ratio: float = 90.0; instagram_export_tz: str = "Europe/Paris"; judge_after_h: int = 72
  class Settings: ...  # + stats: StatsSettings = StatsSettings()
  # queue/actions.py (complète 36)
  def mark_published(session: Session, post_id: int, *, at: datetime, source: Literal["button", "detected", "export"],
                     url: str | None = None, platform_post_id: str | None = None) -> None: ...
  # stats/tiktok.py
  @dataclass(frozen=True)
  class PublicVideo:
      platform: Platform; platform_post_id: str | None; url: str | None; account_handle: str | None
      posted_at: datetime | None; description: str; views: int | None; likes: int | None; comments: int | None
      shares: int | None; saves: int | None; avg_watch_s: float | None; retention_3s: float | None
  def profile_url(handle: str) -> str: ...                    # https://www.tiktok.com/@<handle sans @>
  def parse_profile(info: Mapping[str, Any]) -> list[PublicVideo]: ...   # dédoublonnées par id, triées par date décroissante
  def collect_tiktok(ytdlp: YtDlp, handle: str, *, n: int) -> list[PublicVideo]: ...
  # stats/imports.py
  class ExportFormat(Protocol):
      name: str
      def matches(self, headers: Sequence[str]) -> bool: ...
      def rows(self, records: Iterable[Mapping[str, str]], *, tz: ZoneInfo) -> list[PublicVideo]: ...
  FORMATS: tuple[ExportFormat, ...]                          # MetaExportEn, MetaExportFr, ManualCsv
  class UnknownExport(RushesError): ...                      # « format inconnu ; colonnes trouvées : … »
  def read_table(path: Path) -> tuple[list[str], list[dict[str, str]]]: ...
  def parse_int(text: str) -> int | None: ...
  @dataclass(frozen=True)
  class ImportResult: file: str; format: str; rows: int; matched: int; duplicate: bool
  def import_file(session: Session, config: Config, path: Path, *, tz: ZoneInfo, at: datetime) -> ImportResult: ...
  # stats/detect.py
  def normalize_caption(text: str) -> str: ...
  def caption_matches(description: str, caption: str, *, prefix: int, min_ratio: float) -> bool: ...
  def record_public_video(session: Session, account_id: str, v: PublicVideo, *, at: datetime) -> int: ...
  def match_video(session: Session, public_video_id: int, *, s: StatsSettings) -> int | None: ...
  # stats/metrics.py
  def add_metrics(session: Session, post_id: int, v: PublicVideo, *, source: str, at: datetime) -> bool: ...
  # stats/steps.py
  class StatsCollectStep(BaseStep): name = "stats_collect"        # éléments : comptes TikTok, puis fichiers de inbox/
  #   --vertical : seuls les comptes TikTok sont filtrés ; les fichiers de inbox/ sont traités quelle que soit la
  #   verticale (on ne connaît leur compte qu'après lecture ; exception inscrite au test de la tâche 22)
  class DetectPublishedStep(BaseStep): name = "detect_published"  # éléments : public_videos sans post_id
  ```
- Règles fixées :
  - TikTok : `ytdlp.extract(profile_url(handle), flat=True, platform="tiktok", playlist_items=f"1:{tiktok_videos}")`
    pour chaque compte TikTok (posts de test compris dans le parcours, jamais rapprochés). Champs : `id`,
    `timestamp`, `description`, `view_count`, `like_count`, `comment_count`, `repost_count`, `save_count`,
    `webpage_url`. Vidéos épinglées en tête : tri par `timestamp`. Une `ExtractError` → `Outcome(status="warned", source_unavailable=True)` (§5.3, code 5).
    `collector_health("tiktok")` est mis à jour **une fois par run** (dans `finish`) : échec (+1, `last_error`) si
    tous les comptes TikTok traités ont échoué, succès sinon (remis à 0, `last_ok_at`) ; aucun compte traité →
    inchangé (alertes en tâche 20). Ce compteur `tiktok` ne sert qu'aux statistiques : l'étude de marché (31) a ses
    propres compteurs `tiktok_market` et `tiktok_embed`.
  - R36 : `YtDlpClient.options(platform="tiktok", ...)` ne contient jamais `cookiefile`, quel que soit
    `settings.youtube.cookies` ; aucun collecteur ne lit `data/cookies/`.
  - Instagram : fichiers de `inbox/` ou `inbox/<account_id>/` (quand l'export n'a pas de colonne de compte). Louis
    les dépose depuis la page de `serve` (tâche 36, L28 : `inbox/<account_id>/ig-<empreinte>.csv`) ; un fichier
    copié à la main est traité de la même façon. `read_table` : encodage `utf-8-sig`, sinon `utf-16` ; séparateur deviné par `csv.Sniffer` parmi
    `,`, `;`, tabulation ; `.xlsx` refusé avec l'indice « exporter en CSV ». Formats reconnus par leurs colonnes :
    - `MetaExportEn` : colonnes `Post ID`, `Account username`, `Description`, `Publish time`, `Permalink`, `Views`,
      `Likes`, `Shares`, `Comments`, `Saves` (relevées par la recherche ; **confirmées sur l'export réel à
      l'étape 1**) ; `Publish time` lu au format de l'export réel, dans le fuseau `instagram_export_tz`.
    - `MetaExportFr` : mêmes champs, en-têtes français **relevés sur l'export réel de Louis à l'étape 1** (aucune
      supposition n'est codée avant).
    - `ManualCsv` (spec) : `post_url, date, views, likes, comments, shares, avg_watch_s, retention_3s` ; le compte
      vient de `inbox/<account_id>/` ou d'un post dont `platform_url = post_url`.
    - Aucun format → `UnknownExport`, fichier déplacé dans `inbox/_rejected/<date>-<nom>`, `failed` avec la liste
      des colonnes. Fichier importé → `inbox/_done/<date>-<nom>`.
    - `parse_int` : `""`, `"--"`, `"-"` → `None` ; espaces, espaces fines et insécables, `,` ou `.` de milliers
      retirés ; suffixes `k`/`K` (×1 000) et `M` (×1 000 000) avec décimale `,` ou `.`.
    - Idempotence : sha256 du fichier dans `stats_imports` ; même empreinte → `skipped` (« déjà importé »).
  - Détection (`match_video`) : candidats = posts du même compte, `is_test = 0`, statut `ready` ou `expired` avec
    `planned_at` à ± `match_window_h` de `posted_at`, ou `published` sans `platform_post_id` (bouton « Publié »
    déjà cliqué, souvent au moment où Louis programme, des jours avant la mise en ligne) avec `posted_at ≥
    published_at − 24 h` ; `caption_matches` : `normalize_caption` (NFKC, minuscules, espaces repliés, emoji
    conservés) puis `rapidfuzz.fuzz.ratio(d[:n], c[:n]) ≥ caption_min_ratio` avec
    `n = min(caption_prefix_chars, len(c))`. Plusieurs candidats → plus proche de `planned_at`, puis plus petit
    `id`. **Au rapprochement, `published_at := public_videos.posted_at`** quand il est connu (l'heure du clic reste
    dans `post_actions`) : le jugement à 72 h (18) et le recalage des heures (37) partent de l'heure réelle de mise
    en ligne. Un post `ready` ou `expired` reconnu → `mark_published(at=posted_at, source="detected" ou "export",
    url=…, platform_post_id=…)` ; `published` → `platform_post_id`, `platform_url` et `published_at` sont posés
    (aucune nouvelle ligne `post_actions`). Un post `expired` reconnu reprend ses
    attributions par `registry.assign` (dans une `write_session`). Si son passage a été réattribué entre-temps à un nouveau post (même compte,
    R44) encore non publié, ce nouveau post est écarté par `discard_post(reason="doublon d'un post publié après
    expiration", origin="system")` (qui journalise lui-même l'écart et laisse le segment `approved`, 16b), puis
    l'ancien reprend l'attribution (R2 : jamais deux posts du même passage) ;
    si le nouveau est déjà publié lui aussi, l'ancien reste `published` sans attribution et un `Message("warn",
    "p_… et p_… publiés tous deux : passage en double", alert="rule_violation")` part au rapport.
  - Vidéo sans post : reste dans `public_videos` avec `post_id NULL`, `Message("warn", ...)` la première fois
    seulement, listée dans `status` et le rapport quotidien (tâche 20) ; jamais ignorée.
  - Métriques : une ligne `metrics` par relevé (`collected_at` = heure de la collecte ou de l'import) pour chaque
    vidéo rapprochée ; relevé identique au précédent du même post et de la même source → pas de ligne.
  - Migration `0009_metrics.py` :
    - `metrics(id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL REFERENCES posts(id), collected_at TEXT NOT NULL,
      source TEXT NOT NULL CHECK (source IN ('tiktok_public','instagram_export','manual_csv')), views INTEGER,
      likes INTEGER, comments INTEGER, shares INTEGER, saves INTEGER, avg_watch_s REAL, retention_3s REAL,
      UNIQUE (post_id, collected_at, source))` ;
    - `posts` + `platform_post_id TEXT` (index) ;
    - `public_videos(id INTEGER PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), platform TEXT NOT
      NULL, platform_post_id TEXT, url TEXT, posted_at TEXT, description TEXT NOT NULL, first_seen_at TEXT NOT
      NULL, last_seen_at TEXT NOT NULL, post_id INTEGER REFERENCES posts(id), UNIQUE (platform,
      platform_post_id))` ;
    - `stats_imports(sha256 TEXT PRIMARY KEY, file_name TEXT, format TEXT, rows INTEGER, matched INTEGER,
      imported_at TEXT)` ;
    - `collector_health(name TEXT PRIMARY KEY, consecutive_failures INTEGER NOT NULL DEFAULT 0, last_ok_at TEXT,
      last_error TEXT)`.

- [ ] **Step 1 : Fixtures réelles** — Louis fournit (L37, avant de commencer la tâche : sinon la lui demander) un export Meta Business Suite de l'un de ses comptes
  Instagram, interface en français, et si possible un second en anglais (Insights → Contenu → Exporter, CSV).
  Garder la ligne d'en-têtes **exacte** et remplacer les données par 3 lignes inventées ; noter l'origine et le
  format des dates dans `SOURCES.md`. Inscrire les en-têtes réels dans `docs/decisions.md` et cocher le point
  « Format réel de l'export Instagram » de la spec. Sans export anglais réel : fixture anglaise bâtie sur la liste
  de la recherche, signalée comme non confirmée dans `SOURCES.md`. Les comptes n'ayant encore rien publié, un
  export vide (en-têtes seuls) suffit ; si Meta n'en produit aucun, les deux fixtures sont bâties sur la liste de
  la recherche, la tâche n'est pas bloquée, et le premier vrai export du rodage les confirme (critère
  `instagram_import`, tâche 41).
  Enregistrer la cassette : `uv run python -I scripts/record_cassette.py https://www.tiktok.com/@oggyandthecockroaches
  --flat --platform tiktok --playlist-items 1:60` (relire : aucune URL de média, aucun jeton).

- [ ] **Step 2 : Tests qui échouent**

```python
def test_parse_recorded_tiktok_profile():                  # cassette réelle : ids uniques, compteurs entiers, triés par date décroissante
def test_pinned_videos_sorted_by_date():
def test_r36_collectors_never_use_cookies(ctx, edit_yaml):  # youtube.cookies "always" → options TikTok sans cookiefile ; open() espionné : data/cookies jamais lu
def test_tiktok_failure_counts_and_warns(ctx):
    # deux comptes TikTok : les deux en ExtractError dans un run → warned, consecutive_failures == 1 (pas 2), code 5 ;
    # second run identique → 2 ; run où un seul des deux échoue → 0, last_ok_at posé
def test_post_published_detected_alone(ctx):
    # tiktok_profile : description = légende sans son emoji final, 3 h après planned_at
    # → published (source detected), platform_post_id posé, une ligne metrics
def test_r2_late_detection_retires_reassigned_duplicate(ctx):
    # expiré, passage réattribué à un post ready, ancien détecté → nouveau discarded, 1 ligne post_actions
    # (discard, system) ; segment toujours approved ; ancien attribué (l'absence d'échec au bandit : tâche 18)
def test_detect_published_after_expiry_and_unmatched_reported(ctx):
    # post expiré publié 2 j plus tard (dans la fenêtre de son créneau) → published, attribution reprise
    # vidéo sans post → public_videos.post_id NULL, un seul Message warn sur deux runs, ligne dans collect_status
def test_button_published_then_detected_links_video(ctx):
def test_button_clicked_days_before_live_uses_platform_time(ctx):
    # « Publié » cliqué le 16/11 12:00 (planned_at 20/11 18:00), vidéo posted_at 20/11 18:05 → rapprochée,
    # published_at == 20/11 18:05 UTC ; autre post cliqué le 16/11 12:00, vidéo postée le 15/11 10:00
    # (avant published_at − 24 h) → non rapprochée
def test_test_posts_never_matched(ctx):
def test_caption_prefix_tolerates_small_edit():
@pytest.mark.parametrize("name", ["export_en.csv", "export_fr.csv"])
def test_instagram_export_recognized(ctx, name):           # en-têtes réels → format reconnu, 3 lignes, metrics des posts rapprochés
def test_instagram_import_variants_and_duplicate_file(ctx):
    # BOM + « ; » + "1 234" + "1,2 k" + colonne vide → importé ; même fichier redéposé → skipped « déjà importé »
def test_unknown_export_refused_with_columns(ctx):         # failed ; message liste les colonnes ; fichier dans inbox/_rejected/
def test_manual_csv_columns(ctx):
@pytest.mark.parametrize(("text", "value"), [("1 234", 1234), ("1,234", 1234), ("1,2 k", 1200), ("3.4M", 3_400_000), ("--", None), ("", None)])
def test_parse_int_variants(text, value): assert parse_int(text) == value
def test_metrics_time_series_skips_identical_snapshot(ctx):
def test_r16_stats_rerun_changes_nothing(ctx):
```

- [ ] **Step 3 : Lancer** → FAIL. **Step 4 : Implémenter.** **Step 5 : Lancer** → PASS ; retirer R2, R36 de
  `rules_pending.toml`. Suite rapide, ruff, mypy verts.

- [ ] **Step 6 : Sonde sur le serveur** — `docker compose -f docker/compose.yaml run --rm -T rushes rushes --json
  stats collect` : chaque compte TikTok `done` (comptes encore vides : 0 vidéo, pas d'erreur) ; noter dans
  `docs/decisions.md` que le profil public est lisible depuis le serveur.

- [ ] **Step 7 : Commit proposé** — `feat(stats): public tiktok collector, instagram export import and publication detection (R36)`

---

### Task 18 : Résultat des posts, bandits des accroches et des mises en page, performance apprise

**Files :**
- Create : `src/rushes/core/bandit.py`, `src/rushes/stats/outcomes.py`, `src/rushes/stats/performance.py`,
  `src/rushes/db/migrations/versions/0010_bandits.py`
- Modify : `src/rushes/hooks/rank.py` (`BanditRanker`), `src/rushes/hooks/propose.py`, `src/rushes/queue/step.py`
  (`OutcomePerformance` à la place de `NeutralPerformance`), `src/rushes/db/models.py` (`BanditArm`,
  `PostOutcome`), `src/rushes/config.py` (`BanditSettings`), `src/rushes/steps.py`, `src/rushes/status.py`
  (tableau « Bandits »)
- Test : `tests/test_bandit.py`, `tests/test_outcomes.py`, `tests/test_performance.py`

**Interfaces :**
- Consomme : `metrics`, `posts`, `hooks`, `post_actions` (36), `HookRanker`, `PriorRanker`, `choose_three` (14),
  `HookType`, `LearnedPerformance`, `QueueItem` (16b), `derive_seed`.
- Produit :
  ```python
  class BanditSettings(_Model):
      explore: float = Field(0.10, ge=0, le=0.5); min_outcomes: int = 10; window_days: int = 90
      median_window_days: int = 60; median_min_posts: int = 5
  class Settings: ...  # + bandit: BanditSettings = BanditSettings()
  # core/bandit.py (pur, mypy strict)
  @dataclass(frozen=True)
  class Arm: name: str; alpha: float = 1.0; beta: float = 1.0
  def posterior_mean(a: Arm) -> float: ...
  def thompson_order(arms: Sequence[Arm], rng: np.random.Generator, *, explore: float) -> list[str]: ...
  def prob_better(a: Arm, b: Arm, rng: np.random.Generator, *, draws: int = 4000) -> float: ...
  # stats/outcomes.py
  @dataclass(frozen=True)
  class Outcome: post_id: int; success: bool; ratio: float | None; reason: Literal["views", "retention", "discarded"]
  def views_at(session: Session, post_id: int, *, after_h: int) -> int | None: ...   # premier relevé ≥ after_h après published_at
  def judge_post(session: Session, post_id: int, *, now: datetime, s: StatsSettings, b: BanditSettings) -> Outcome | None: ...
  def arms(session: Session, account_id: str, dimension: Literal["hook_type", "layout"]) -> list[Arm]: ...
  #   tous les bras de la dimension (chaque HookType ; square, vertical), Beta(1, 1) sans ligne bandit_arms
  class BanditsStep(BaseStep): name = "bandits"   # éléments : posts is_test = 0, published ou discarded, sans post_outcomes
  # hooks/rank.py
  class BanditRanker:   # HookRanker
      def __init__(self, session: Session, b: BanditSettings, *, seed: int) -> None: ...
  # stats/performance.py
  class OutcomePerformance:   # LearnedPerformance
      def __init__(self, session: Session, b: BanditSettings, *, now: datetime) -> None: ...
      def score(self, account_id: str, item: QueueItem) -> float: ...
  ```
- Règles fixées :
  - `thompson_order` : un tirage `rng.beta(alpha, beta)` par bras, ordre décroissant ; puis, avec probabilité
    `explore`, un bras tiré uniformément **parmi les bras autres que le premier** passe en tête (les types perdants
    restent testés : avec 6 types et `explore = 0,1`, chaque perdant est premier environ 2 % du temps).
  - `judge_post` (spec, Bandit des types d'accroche) :
    - post `is_test = 1` → `None` (jamais jugé : vitrine et répétition n'apprennent rien) ;
    - `discarded` dont la dernière ligne `post_actions(kind="discard")` a `origin="system"` (doublon retiré par la
      tâche 17) → `None`, jamais jugé (R37 ne vise que les écarts de Louis) ;
    - autre `discarded` (`origin` `page` ou `folder`) → échec, `reason="discarded"` (R37), immédiat ;
    - `published` depuis moins de `judge_after_h`, ou sans relevé ≥ `judge_after_h` → `None` (attendre) ;
    - base du compte = posts publiés du compte dans les `median_window_days` jours, hors ce post, ayant un relevé
      ≥ `judge_after_h` ; moins de `median_min_posts` → `None` ;
    - si le post et au moins `median_min_posts` posts de la base ont `retention_3s` : succès si
      `retention_3s > médiane`, `reason="retention"` ;
    - sinon `ratio = vues / médiane des vues` (médiane nulle → ratio = vues > 0), succès si `ratio > 1`,
      `reason="views"`.
    - Un post `expired` n'est jamais jugé.
  - Mise à jour : une ligne `post_outcomes` par post (clé d'idempotence, R16), puis `alpha += 1` (succès) ou
    `beta += 1` (échec) sur `bandit_arms(compte, "hook_type", type de l'accroche choisie)` et, si la mise en page
    réelle du post est `square` ou `vertical`, sur `bandit_arms(compte, "layout", layout)`. Les replis (`portrait`,
    `full`) ne sont pas des choix : jamais comptés.
  - `BanditRanker.order(account_id, cands)` : moins de `min_outcomes` lignes `post_outcomes` pour le compte →
    ordre de `PriorRanker` ; sinon types ordonnés par `thompson_order(arms(compte, "hook_type"))`, rng
    `np.random.default_rng(seed)`, chaque candidat placé au rang de son type, ordre d'arrivée à l'intérieur d'un
    type ; `HooksStep` passe `seed = derive_seed(account_id, segment ou compilation, "hooks")`.
  - `OutcomePerformance.score` : pour chacune des caractéristiques du post (sujet, famille, `kind`, type de
    l'accroche choisie), espérance `Beta(1 + succès, 1 + échecs)` des posts jugés du compte qui la partagent,
    sur `window_days` jours ; moyenne des quatre. Aucune donnée → 0,5 (comme `NeutralPerformance`).
  - Migration `0010_bandits.py` : `bandit_arms` (§5.10, + `updated_at TEXT`) ; `post_outcomes(post_id INTEGER
    PRIMARY KEY REFERENCES posts(id), success INTEGER NOT NULL, ratio REAL, reason TEXT NOT NULL CHECK (reason IN
    ('views','retention','discarded')), judged_at TEXT NOT NULL)`.
  - La part de `vertical` n'est pas choisie ici : la tâche 37 la recale chaque semaine à partir des bras `layout`.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_thompson_order_reproducible_with_seed():
def test_winning_hook_type_moves_first_others_still_tested():
    # 2 000 posts simulés, succès conflict 0,6, autres types 0,3, explore 0,1, seed fixe
    # → sur les 500 derniers : conflict premier ≥ 60 % ; chaque autre type premier ≥ 1 %
def test_arms_include_every_hook_type_without_rows(ctx):   # base vide → un Arm par HookType, alpha = beta = 1
def test_winning_layout_prob_better_rises():               # vertical 0,6 contre square 0,3 → prob_better > 0,9 après 300 résultats
def test_r37_discard_counts_as_failure(ctx):               # post écarté depuis la page → reason discarded ; beta +1 sur son type d'accroche et sa mise en page
def test_r37_system_discard_not_counted(ctx):
    # doublon écarté par la tâche 17 (discard_post origin "system") → judge_post None, aucune ligne post_outcomes,
    # bandit_arms inchangés ; écart origin "folder" → compté
def test_bandits_ignore_test_posts(ctx):                   # post is_test = 1 écarté puis un autre publié → aucune ligne post_outcomes
def test_judge_waits_72h_and_min_history(ctx):
def test_success_by_views_ratio_over_median(ctx):
def test_success_by_retention_when_available(ctx):
def test_fallback_layouts_never_counted(ctx):              # post en portrait → aucun bras layout modifié
def test_bandit_ranker_uses_prior_below_min_outcomes(ctx):
def test_outcome_performance_scores_features(ctx):        # sujet gagnant → score > 0,5 ; compte sans donnée → 0,5
def test_queue_uses_outcome_performance(ctx):
def test_r16_bandits_rerun_changes_nothing(ctx):           # un post jugé une seule fois
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter** (`bandits` enregistré dans `STEPS`).
  **Step 4 : Lancer** → PASS ; retirer R37 de `rules_pending.toml`. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(stats): one-time post outcomes, thompson bandits for hook types and layouts, learned queue performance (R37)`

---

### Task 20 : Profils de run, unités systemd, sauvegarde, maintenance, rapports, ntfy

**Files :**
- Create : `src/rushes/ops/notify.py`, `src/rushes/ops/alerts.py`, `src/rushes/ops/backup.py`,
  `src/rushes/ops/maintenance.py`, `src/rushes/ops/disk.py`, `src/rushes/ops/report.py`, `src/rushes/ops/steps.py`,
  `src/rushes/db/migrations/versions/0011_alerts.py`, `docker/systemd/rushes-{night,watch,stats,weekly,maintenance}.{service,timer}`,
  `docker/systemd/rushes-refresh.{path,service}`, `docker/systemd/rushes-update.{path,service}`,
  `docker/systemd/rushes-alert@.service`, `docker/rushes.env.example`, `docker/ntfy/server.yml`,
  `scripts/update_ytdlp.sh`, `scripts/install_units.sh`, `tests/fakes/notify.py`
- Modify : `src/rushes/runner.py` (envoi des alertes en fin de commande par `send_pending_alerts`),
  `src/rushes/config.py` (`Settings.backup`, `Settings.retention`, secrets `*_FILE` de `NtfySettings`),
  `src/rushes/paths.py` (`backups`), `src/rushes/steps.py` (`PROFILES`), `src/rushes/cli.py` (`run --profile`,
  `notify test`, `notify send`, `notify failure`, `backup`, `backup verify`), `src/rushes/stats/steps.py` et `src/rushes/discover/discover.py` (santé des
  collecteurs, alerte et demande de mise à jour après deux échecs), `src/rushes/status.py` (« Minuteurs »,
  « Disque »), `docker/Dockerfile` (cible `test` vérifiée), `docker/compose.yaml` (image `rushes:current`, service
  `ntfy` et ses volumes, `secrets:`). `Message.alert` existe déjà (tâche 2) : `ui.py` n'est pas modifié.
- Test : `tests/test_notify.py`, `tests/test_alerts.py`, `tests/test_backup.py`, `tests/test_retention.py`,
  `tests/test_report.py`, `tests/test_profiles.py`, `tests/test_systemd_units.py`, `tests/test_update_ytdlp.py`,
  `tests/test_compose.py`, `tests/test_cli.py` (`notify test|send|failure`, `backup`, `backup verify` passent de
  `PENDING` à `IMPLEMENTED`)

**Interfaces :**
- Consomme : `Notifier` (§5.13), `Http.post`, `Emitter`, `Message.alert` (tâche 2), `runs`, `collector_health` (17),
  `stock_days` (10b), `public_videos` (17), `post_request`/`take_requests`, `head_revision`, `run_refresh` (36),
  `derived.record`, journal `data/logs/<date>.jsonl`.
- Produit :
  ```python
  # ops/alerts.py
  @dataclass(frozen=True)
  class AlertKind: title: str; priority: Literal[1, 2, 3, 4, 5]; tags: tuple[str, ...]; dedup_h: int = 24
  ALERT_KINDS: Mapping[str, AlertKind]                 # clé ou préfixe avant « : »
  def dispatch_alerts(session: Session, notifier: Notifier, messages: Sequence[Message], *, now: datetime,
                      click: str | None) -> int: ...  # envoyées
  def send_pending_alerts(ctx: Context, messages: Sequence[Message]) -> None: ...
  #   garde les Message qui portent alert, construit le notifier (make_notifier), appelle dispatch_alerts avec
  #   click = serve.public_origin ; utilisée par run_pipeline après le Summary et par run_express (28) en fin de commande
  # ops/notify.py
  class NotifyError(RushesError): ...                  # statut HTTP ≥ 300 de ntfy
  class NtfyNotifier:   # Notifier
      def __init__(self, http: Http, url: str, topic: SecretStr, token: SecretStr | None) -> None: ...
  class NullNotifier: ...
  def make_notifier(settings: Settings, http: Http) -> Notifier: ...
  # ops/backup.py
  class BackupSettings(_Model): offsite_dir: str | None = None; daily: int = 7; weekly: int = 4; monthly: int = 6
  @dataclass(frozen=True)
  class BackupResult: path: Path; bytes: int; revision: str; posts: int
  def backup(paths: Paths, engine: Engine, *, now: datetime, s: BackupSettings) -> BackupResult: ...
  def rotate(names: Sequence[str], *, daily: int, weekly: int, monthly: int) -> list[str]: ...   # à supprimer
  def verify_latest(paths: Paths, s: BackupSettings) -> BackupResult: ...
  # ops/maintenance.py
  class RetentionSettings(_Model):
      published_days: int = 30; ecartes_days: int = 30; review_days: int = 30; disk_alert: float = 0.85
  def retention_candidates(session: Session, paths: Paths, *, now: datetime, s: RetentionSettings) -> list[Path]: ...
  # config.py (Settings est extra="forbid" : les deux sections y sont déclarées)
  class Settings: ...  # + backup: BackupSettings = BackupSettings(); + retention: RetentionSettings = RetentionSettings()
  # ops/disk.py
  def disk_usage_ratio(path: Path) -> float: ...       # used / (used + free) de shutil.disk_usage
  # ops/report.py
  ReportSection = Callable[[Session, Context, datetime], list[str]]
  DAILY_SECTIONS: list[ReportSection]; WEEKLY_SECTIONS: list[ReportSection]   # complétées par 37, 38, 26
  def write_report(ctx: Context, kind: Literal["daily", "weekly"], *, now: datetime) -> Path: ...
  # ops/steps.py
  class BackupStep(BaseStep): name = "backup"
  class BackupVerifyStep(BaseStep): name = "backup_verify"
  class RetentionStep(BaseStep): name = "retention"
  class DiskStep(BaseStep): name = "disk"
  class ReportStep(BaseStep): name = "report"                 # rapport quotidien + résumé ntfy ; stock.days ; stock_low
  class WeeklyReportStep(BaseStep): name = "report_weekly"
  # steps.py
  PROFILES: Mapping[str, tuple[str, ...]]
  ```
- Règles fixées :
  - Profils (`rushes run --profile <nom>`, `command="run:<nom>"` ; `rushes run` seul = `RUN_ORDER`) ; chaque tâche
    suivante ajoute ses étapes, un test vérifie que chaque nom existe dans `STEPS` :

    | Profil | Étapes | Ajouts prévus |
    | --- | --- | --- |
    | `night` | `backup`, `RUN_ORDER`, `disk`, `report` | `creators_backfill` dans `RUN_ORDER` (28) |
    | `watch` | `queue` | `creators_watch`, `creators_revisit` en tête ; l'unité lance ensuite `rushes creators express` (28) |
    | `stats` | `stats_collect`, `detect_published`, `bandits` | `views_drop` (37) |
    | `weekly` | `report_weekly` | avant lui, dans l'ordre du tuple `weekly` de `00-master.md` §3 : `creators_prospect`, `creators_snapshot` (27), `market_panel`, `market_collect`, `market_analyze` (31), `discover_series`, `rights` (38), `themes_suggest` (30), `recalibrate` (37) |
    | `maintenance` | `backup_verify`, `retention`, `disk` | — |

    Pas de profil `refresh` : le rendu léger passe par la commande `rushes refresh` (`run_refresh`, tâche 36), qui
    retire les demandes et pose `Filters.posts` ; `rushes run --profile refresh` est refusé (profil inconnu, code 1).

  - Unités (`docker/systemd/`), toutes avec `EnvironmentFile=/etc/rushes/rushes.env` (`RUSHES_HOME`,
    `RUSHES_DATA_DIR`), `Type=oneshot`, `SuccessExitStatus=4 5` (élément en échec ou source indisponible : déjà
    signalés par le rapport et leur alerte dédiée, jamais par `timer_failed`), `OnFailure=rushes-alert@%n.service`,
    `ExecStart=/usr/bin/docker compose -f ${RUSHES_HOME}/docker/compose.yaml run --rm --no-deps -T --name
    rushes-%N rushes rushes run --profile <p>`, `ExecStopPost=-/usr/bin/docker rm -f rushes-%N` ; jamais `Nice=` ni
    `CPUWeight=` (sans effet sur le conteneur) :

    | Unité | `OnCalendar` (`Persistent=true`) | `TimeoutStartSec` |
    | --- | --- | --- |
    | `rushes-night` | `*-*-* 02:00:00 Europe/Paris` | `6h` |
    | `rushes-watch` | `*-*-* 00/6:30:00 Europe/Paris` | `2h` ; `ExecStart=-…` (préfixe `-` : le second `ExecStart`, `rushes creators express` ajouté en tâche 28, tourne même si la veille échoue) |
    | `rushes-stats` | `*-*-* 01/6:15:00 Europe/Paris` | `30min` |
    | `rushes-weekly` | `Sun *-*-* 03:00:00 Europe/Paris` | `4h` |
    | `rushes-maintenance` | `Mon *-*-* 04:00:00 Europe/Paris` | `3h` ; d'abord `ExecStart=-${RUSHES_HOME}/scripts/update_ytdlp.sh ${RUSHES_HOME}` (préfixe `-` : une mise à jour refusée, code 1, n'empêche ni `backup_verify`, ni `retention`, ni `disk`), puis le profil `maintenance` |

    `rushes-refresh.path` : `DirectoryNotEmpty=@RUSHES_DATA_DIR@/requests/refresh` → `rushes-refresh.service`
    (`… rushes rushes refresh`, pas `run --profile` ; `TimeoutStartSec=2h15min` : il attend au pire la fin d'une voie
    express, puis la cession du run de nuit entre deux éléments ; la demande n'est retirée qu'une fois le verrou
    lourd obtenu, tâche 36). L39 : `rushes refresh` attend le verrou lourd 2 h au plus (`refresh_wait_s = 7200`) ;
    au-delà, il retire quand même les demandes (le post garde `render_version = NULL`, rendu au run suivant), émet
    `alert="refresh_delayed"` (priorité 3) et sort en 0, donc ni boucle de l'unité `.path` ni `timer_failed`. Test
    `test_refresh_gives_up_after_wait_and_alerts`. `rushes-update.path` : `DirectoryNotEmpty=@RUSHES_DATA_DIR@/requests/update_ytdlp`
    → `rushes-update.service` (`update_ytdlp.sh`, `SuccessExitStatus=1 4 5` : un refus est déjà signalé par
    `ytdlp_update_refused`). `rushes-alert@.service` : `rushes notify failure --unit %i`.
    Les sections `[Path]` ne développent pas les variables : `install_units.sh` remplace `@RUSHES_DATA_DIR@` en
    copiant les unités.
  - `scripts/install_units.sh [--dest DIR] [--no-systemctl]` (bash, `set -euo pipefail`) : lit
    `/etc/rushes/rushes.env`, copie `docker/systemd/*` dans `DIR` (défaut `/etc/systemd/system`) en substituant
    `@RUSHES_DATA_DIR@`, puis `systemctl daemon-reload` et `systemctl enable --now` des `.timer` et `.path`.
  - `scripts/update_ytdlp.sh <dépôt>` (R38, côté hôte, bash compatible 3.2) :

```text
take_requests : supprime data/requests/update_ytdlp/*
copie de travail : git -C <dépôt> worktree add --detach <tmp> HEAD
uv lock --upgrade-package yt-dlp --upgrade-package yt-dlp-ejs --upgrade-package curl-cffi
  (dans le conteneur ghcr.io/astral-sh/uv:<version exacte>, monté sur <tmp>)
uv.lock inchangé → nettoyage, sortie 0
docker build --target test -t rushes:candidate-test et --target runtime -t rushes:candidate depuis <tmp>
docker run --rm --network none rushes:candidate-test pytest -m "not slow" -q
  échec → aucune bascule ; docker compose run … rushes notify send --key ytdlp_update_refused ; sortie 1
  succès → docker tag rushes:current rushes:prev ; docker tag rushes:candidate rushes:current
dans tous les cas : diff de uv.lock écrit dans ${RUSHES_DATA_DIR}/reports/uv-lock-<date>.diff, worktree supprimé
```

  - Alertes (`ALERT_KINDS`) :

    | Clé | Titre | Priorité | Origine |
    | --- | --- | --- | --- |
    | `youtube_signin` | YouTube exige une connexion : cookies de secours utilisés | 4 | 10a |
    | `cookies_expired` | Cookies YouTube refusés : les renouveler | 4 | 10a |
    | `refresh_delayed` | « Accroche 2/3 » pas encore appliquée : rendu au prochain run | 3 | 36 |
    | `collector:youtube_watch` | Veille des créateurs en échec deux fois de suite | 4 | 28 |
    | `collector:<nom>` | Collecteur en échec deux fois de suite | 4 | 17 |
    | `rule_violation` | Règle enfreinte (ex. passage publié deux fois) | 5 | 17 (puis audit, 40a) |
    | `ytdlp_update_refused` | Mise à jour de yt-dlp refusée par les tests | 4 | 20 |
    | `disk` | Disque au-dessus de 85 % | 4 | 20 |
    | `stock_low:<compte>` | Moins de 3 jours de stock | 3 | 20 |
    | `backup_failed` | Sauvegarde en échec | 5 | 20 |
    | `timer_failed:<unité>` | Minuteur en échec | 5 | 20 |
    | `views_drop:<compte>` | Chute des vues du compte | 4 | 37 |
    | `unlicensed_top:<id>` | Très bien classé mais sans licence | 3 | 38 |
    | `creator_video:<v_key>` | Nouvelle vidéo, extraits prêts (une clé par vidéo : la 2e vidéo du jour d'un créateur n'est pas muette) | 3 | 28 |
    | `third_party` | Évolution d'un projet tiers suivi (R41) | 3 | futur `vendor/` |

    Clé inconnue de `ALERT_KINDS` (tâche future oubliée) : envoyée quand même, priorité 3, titre = la clé, plus un
    `Message("warn", "clé d'alerte inconnue : <clé>")` sans clé ; jamais perdue.
    `send_pending_alerts` (appelée par `run_pipeline` après le `Summary`, et par `run_express` de la tâche 28 en fin
    de commande pour ses propres messages) garde les `Message` qui portent `alert` ; `dispatch_alerts` envoie ceux
    dont la clé n'a pas été envoyée depuis `dedup_h` heures (`alerts_sent`), avec `click = serve.public_origin`.
    Toute exception d'envoi → `Message("warn", "alerte non envoyée : …")` sans clé, la ligne `alerts_sent` n'est pas
    écrite (nouvel essai au run suivant), code de sortie inchangé.
  - `NtfyNotifier.send` : `Http.post(url, body=json.dumps({"topic", "title", "message", "priority", "tags",
    "click"}).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer <jeton>"})` (publication
    JSON de ntfy : titres accentués sans encodage d'en-tête) ; statut HTTP ≥ 300 (401 ou 403 d'un jeton faux, 5xx)
    → `NotifyError` levée, traitée comme toute exception d'envoi (`alerts_sent` non écrite). `NtfySettings.topic()`/
    `token()` : variable `<topic_env>`/`<token_env>`, sinon fichier désigné par `<…>_FILE` (secrets Compose,
    fichiers 0400) ; `register_secret` sur les deux valeurs (R47).
  - Collecteurs suivis dans `collector_health` (table de la tâche 17), chacun mis à jour **une fois par run** :
    échec si tous ses éléments du run ont échoué, succès sinon. `tiktok` (statistiques, 17), `youtube`
    (`DiscoverYoutubeStep` de la tâche 10a : succès → 0, toutes les chaînes en `ExtractError` → +1), et plus tard
    `youtube_watch` (veille des créateurs, 28), `tiktok_market` et `tiktok_embed` (étude de marché, 31). Deuxième échec consécutif (`consecutive_failures == 2`)
    → `alert="collector:<nom>"` et `post_request(paths, "update_ytdlp", at=now)` (mise à jour anticipée, spec).
  - Sauvegarde : `VACUUM INTO backups/.tmp-<ts>.db` → `PRAGMA integrity_check` = `ok` sur la copie → révision
    Alembic = `head` → compression `compression.zstd` vers `backups/rushes-<AAAAMMJJ-HHMM>.db.zst` (écriture
    atomique) → copie dans `offsite_dir` s'il est défini (hors RAID, tâche 39) → `rotate` : garder les `daily`
    plus récentes, la plus récente de chacune des `weekly` dernières semaines ISO et des `monthly` derniers mois.
    Échec → `failed` et `alert="backup_failed"`. `verify_latest` : décompresse dans un dossier temporaire,
    `integrity_check`, révision, nombre de posts > 0 si la base en avait.
  - Rétention (spec, Stockage) : `sources/` et `renders/` d'un épisode **entièrement publié** (aucun post
    `planned`/`rendered`/`ready`, dernier `published_at` plus vieux que `published_days`) ; un fichier source
    partagé par des épisodes enfants (tâche 11) n'est effacé que si tous le sont ; dossiers
    `outbox/<compte>/<p_key>/` des posts publiés depuis `published_days` ; `outbox/_ecartes/*` de plus de
    `ecartes_days` ; dossiers `review/<horodatage>/` (aperçus MP4 de la revue, tâche 32) de plus de `review_days`
    (dossier absent avant la tâche 32 : rien). Jamais `clean.mp4`, les JSON d'analyse ni la base (R17, compilations
    de la tâche 30).
  - Disque : `disk_usage_ratio(data_dir) > disk_alert` → `alert="disk"` (`DiskStep`). Stock : `ReportStep` émet
    `alert="stock_low:<compte>"` pour chaque compte dont `stock_days` (posts de test compris, comme le calcul de
    10b) est < 3 ; il enregistre aussi chaque jour `derived.record("stock.days", <compte>, stock_days,
    source="report", confidence=1.0, at=now)` : l'historique (`derived_values_history`) sert au recalage du seuil et à
    la section « Rythme » (37).
  - Rapports : quotidien `reports/<AAAA-MM-JJ>.txt` (posts prêts et jours de stock par compte, runs `failed` ou
    `partial` des 24 h, `warn`/`error` du journal du jour dont les refus du registre, publications non rapprochées)
    et résumé ntfy (priorité 2, sans dédoublonnage) ; hebdomadaire `reports/<AAAA-MM-JJ>-weekly.txt` (posts publiés
    par compte, médiane des vues, alertes de la semaine, `uv-lock-*.diff` présents : « nouveau uv.lock proposé »).
  - Migration `0011_alerts.py` : `alerts_sent(key TEXT PRIMARY KEY, last_sent_at TEXT NOT NULL, count INTEGER NOT
    NULL DEFAULT 1)`.
  - `docker/ntfy/server.yml` : `base-url: "https://ntfy.<domaine>"` (remplacé en tâche 39), `listen-http: ":80"`,
    `behind-proxy: true`, `cache-file: /var/cache/ntfy/cache.db`, `auth-file: /var/lib/ntfy/user.db`,
    `auth-default-access: deny-all`, `enable-signup: false`, `upstream-base-url: "https://ntfy.sh"`. Compose :
    service `ntfy` (`binwiederhier/ntfy:v2.36.0@sha256:<digest>`, `command: serve`, `mem_limit: 128m`, réseau
    `${GODOXY_NETWORK}`, aucun `ports:`, `restart: unless-stopped`, volumes `${RUSHES_DATA_DIR}/ntfy/lib:/var/lib/ntfy`,
    `${RUSHES_DATA_DIR}/ntfy/cache:/var/cache/ntfy` et `./ntfy/server.yml:/etc/ntfy/server.yml:ro` : utilisateurs et
    jetons survivent à `docker compose down` et à la recréation du conteneur) ; `secrets:` `ntfy_token`, `ntfy_topic` (fichiers 0400) pour le service
    `rushes`, variables `NTFY_TOKEN_FILE`, `NTFY_TOPIC_FILE` (noms tirés de `token_env`/`topic_env` de la config +
    `_FILE`) ; `rushes` et `serve` utilisent
    `image: rushes:current`.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_profiles_reference_registered_steps():
def test_night_profile_backs_up_first_and_reports_last():
def test_ntfy_publish_json_with_bearer(ctx):               # FakeHttp : url, corps JSON (titre accentué intact), en-tête Authorization
def test_ntfy_secret_from_file_never_logged(ctx, tmp_path):   # *_FILE lu ; jeton absent de la sortie et du journal (R47)
def test_alerts_dedup_24h(ctx):                            # même clé deux runs le même jour → 1 envoi ; 25 h plus tard → 2
def test_notify_failure_never_fails_run(ctx):
    # FakeHttp.post lève ; puis FakeHttp.post renvoie 401 ; puis 503 ; puis section ntfy absente
    # → chaque fois code 0, Message warn « alerte non envoyée », alerts_sent vide
def test_unknown_alert_key_sent_with_priority_3(ctx):      # alert="zz_inconnue" → 1 envoi priorité 3, Message warn « clé d'alerte inconnue »
def test_rule_violation_alert_priority_5(ctx):             # Message(alert="rule_violation") → 1 envoi priorité 5
def test_send_pending_alerts_outside_run_pipeline(ctx):    # appel direct avec Message(alert="creator_video:v_abc") → 1 envoi FakeNotifier, alerts_sent écrite
def test_collector_second_failure_alerts_and_requests_update(ctx):   # deux runs en échec complet → alerte collector:tiktok, un fichier dans requests/update_ytdlp/
def test_backup_integrity_revision_and_zstd(ctx):          # fichier .db.zst, décompressé : integrity ok, révision head
def test_backup_rotation_7_4_6():                          # 400 noms quotidiens → 7 + 4 + 6 gardés au plus, les bons
def test_backup_verify_detects_corruption(ctx):
def test_settings_accept_backup_and_retention_sections(edit_yaml):   # backup.offsite_dir et retention.disk_alert écrits → chargés, pas d'erreur extra
def test_retention_deletes_only_fully_published_episodes(ctx):
def test_retention_keeps_shared_source_of_child_episodes(ctx):
def test_retention_deletes_old_review_dirs(ctx):           # review/<ts>/ de 31 j supprimé, de 29 j gardé ; review/ absent → aucun candidat
def test_disk_alert_over_85_percent(ctx, monkeypatch):
def test_stock_low_emitted_by_report_and_history_recorded(ctx):
    # compte à 2,5 jours (tests compris) → alert stock_low:<compte> ; derived_values_history : 1 ligne stock.days par compte et par run
def test_daily_report_sections(ctx):                       # prêts, stock, échecs, refus du registre, non rapprochées
def test_systemd_units_well_formed():
    # configparser : chaque .timer → OnCalendar du tableau, Persistent=true, Unit= ; chaque .service → Type=oneshot,
    # SuccessExitStatus=4 5, OnFailure=rushes-alert@%n.service, EnvironmentFile, TimeoutStartSec,
    # « run --rm --no-deps -T », profil attendu ; rushes-refresh.service lance « rushes refresh » (pas « --profile ») ;
    # premier ExecStart de rushes-watch et ExecStart de update_ytdlp.sh dans rushes-maintenance préfixés par « - » ;
    # aucun Nice= ni CPUWeight= ; les .path contiennent @RUSHES_DATA_DIR@
def test_no_refresh_profile():                             # "refresh" ∉ PROFILES ; rushes run --profile refresh → code 1
def test_install_units_substitutes_data_dir(tmp_path):     # --dest tmp --no-systemctl → plus aucun @RUSHES_DATA_DIR@
def test_r38_failed_tests_keep_previous_image(tmp_path):
    # fake_bin : git, docker (uv lock modifie uv.lock ; « pytest » sort 1) → calls.log sans « tag rushes:candidate rushes:current »,
    # « notify send --key ytdlp_update_refused » présent, code 1, uv-lock-<date>.diff écrit
def test_ytdlp_update_switches_tags_when_tests_pass(tmp_path):   # ordre : tag current→prev puis candidate→current
def test_ytdlp_update_noop_when_lock_unchanged(tmp_path):         # aucun docker build
def test_compose_ntfy_and_secrets():
def test_compose_ntfy_state_persisted():                   # service ntfy : volumes /var/lib/ntfy, /var/cache/ntfy et server.yml en lecture seule
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter** (`ops/*`, migration 0011, `PROFILES`, CLI, unités,
  scripts, Compose). **Step 4 : Lancer** → PASS ; retirer R38 de `rules_pending.toml`. Suite rapide, ruff, mypy
  verts ; `shellcheck scripts/*.sh` propre.

- [ ] **Step 5 : Vérifier les unités sur le serveur** — `systemd-analyze verify docker/systemd/*.service` et
  `systemd-analyze calendar "Sun *-*-* 03:00:00 Europe/Paris"` (et chaque `OnCalendar`) → aucune erreur, prochaines
  dates attendues.

- [ ] **Step 6 : Installation provisoire et essai des alertes** (la tâche 39 refait tout de façon reproductible) —
  Louis ajoute la route GoDoxy de `ntfy` ; `docker compose up -d ntfy` ; `ntfy user add rushes`,
  `ntfy token add rushes`, accès en écriture au topic ; fichiers de secrets 0400 ; Louis s'abonne au topic dans
  l'appli ntfy de l'iPhone. `scripts/install_units.sh` ; `rushes notify test` envoie une alerte de chaque clé →
  toutes reçues sur l'iPhone, noté dans `docs/decisions.md`.

- [ ] **Step 7 : Commit proposé** — `feat(ops): run profiles, systemd units, backups, retention, reports, ntfy alerts and gated yt-dlp updates (R38)`

---

### Task 37 : Recalage autonome des créneaux, parts, seuils et mises en page ; alerte de chute des vues

**Files :**
- Create : `src/rushes/stats/recalibrate.py`, `src/rushes/stats/drop.py`
- Modify : `src/rushes/queue/slots.py` (`plan_slots(..., explore, seed)`), `src/rushes/queue/step.py`,
  `src/rushes/queue/schedule.py` (part des familles par `derived.resolve("families.share", …)`),
  `src/rushes/cut/bestof.py` (tirage de famille du best-of par `derived.resolve`), `src/rushes/config.py`
  (`RecalibrateSettings`, `QueueSettings.slot_explore`), `src/rushes/steps.py` (`recalibrate` dans `weekly`,
  `views_drop` dans `stats`), `src/rushes/ops/report.py` (sections « Recalage » et « Originalité »),
  `src/rushes/status.py` (« Valeurs recalables » : valeur, origine, date)
- Test : `tests/test_recalibrate.py`, `tests/test_views_drop.py`, `tests/test_slot_explore.py`

**Interfaces :**
- Consomme : `post_outcomes`, `views_at`, `arms`, `prob_better` (18), `derived.record/resolve`,
  `derived_values_history` (`stock.days` enregistré par `ReportStep`, 20), `stock_days`,
  `Config.slots_for(account_id)`, `Config.family_share`, `DEFAULT_SLOTS`, `DEFAULT_SHARE`, `approval_threshold` (15),
  `initial_layout` (34), `WEEKLY_SECTIONS` (20), `public_videos` (17).
- Produit :
  ```python
  class RecalibrateSettings(_Model):
      start_after_days: int = 21; lookback_days: int = 42; min_posts: int = 15; prior_weight: float = 3.0
      slot_min_gain: float = 0.10; slot_min_obs: int = 3; slot_earliest: str = "10:00"; slot_latest: str = "23:00"
      slot_min_gap_h: int = 2
      long_explore_share: float = 0.10; long_min_posts: int = 5; long_bounds: tuple[float, float] = (0.05, 0.30)
      stock_low_days: float = 10.0; stock_high_days: float = 21.0; threshold_step: float = 0.02
      threshold_bounds: tuple[float, float] = (0.5, 0.9)
      vertical_min_outcomes: int = 10; vertical_bounds: tuple[float, float] = (0.05, 0.5)
      drop_ratio: float = 0.4; drop_recent_days: int = 21; drop_base_days: int = 28; drop_min_posts: int = 5
      rate_stock_days: float = 14.0; rate_window_days: int = 14
  class Settings: ...  # + recalibrate: RecalibrateSettings = RecalibrateSettings()
  class QueueSettings: ...  # + slot_explore: float = Field(0.25, ge=0, le=0.5)
  # stats/recalibrate.py
  @dataclass(frozen=True)
  class Change: key: str; scope: str; old: object; new: object; source: str; confidence: float; n: int; forced: bool
  def hour_scores(obs: Sequence[tuple[int, float]], *, prior_weight: float) -> dict[int, tuple[float, int]]: ...
  def recalibrate_slots(current: Sequence[str], scores: Mapping[int, tuple[float, int]], s: RecalibrateSettings) -> list[str]: ...
  def recalibrate_long_share(n_long: int, ratio_long: float | None, ratio_mon: float | None, s: RecalibrateSettings) -> float: ...
  def recalibrate_threshold(current: float, stock_days: Sequence[float], s: RecalibrateSettings) -> float: ...
  def recalibrate_vertical_share(square: Arm, vertical: Arm, rng: np.random.Generator, s: RecalibrateSettings) -> float | None: ...
  class RecalibrateStep(BaseStep): name = "recalibrate"   # éléments : comptes, puis verticales
  # stats/drop.py
  def views_drop(recent: Sequence[float], base: Sequence[float], s: RecalibrateSettings) -> float | None: ...
  class ViewsDropStep(BaseStep): name = "views_drop"      # éléments : comptes
  ```
- Règles fixées (les valeurs viennent des posts non test jugés par la tâche 18, `ratio` = vues / médiane) :
  - **Créneaux** (`slots`, portée compte) : seulement si le premier post publié du compte date d'au moins
    `start_after_days` et si au moins `min_posts` posts sont jugés dans `lookback_days`. `hour_scores` : par heure
    locale de publication (fuseau de la marque) **des seuls posts rapprochés d'une vidéo publique** (tâche 17 :
    `platform_post_id` non nul, `published_at` = `posted_at` de la plateforme ; un post marqué « Publié » sans vidéo
    rapprochée n'entre pas dans le recalage), `(Σ ratio + prior_weight × moyenne globale) / (n + prior_weight)`
    et `n`. Pour chaque créneau `h` : candidats `h−2 … h+2` dans `[slot_earliest, slot_latest]`, à au moins
    `slot_min_gap_h` des autres créneaux, avec `n ≥ slot_min_obs` ; on passe au meilleur s'il dépasse le score de
    `h` d'au moins `slot_min_gain` (relatif), sinon on garde `h`. Valeur triée `["HH:MM", …]`, confiance
    `min(1, n / 50)`.
  - **Exploration des heures** (pour que le recalage voie d'autres heures) : `plan_slots` décale chaque nouveau
    créneau, avec probabilité `slot_explore`, de δ ∈ {−2, −1, +1, +2} h tiré à graine
    `derive_seed(compte, date, k)`, borné à `[slot_earliest, slot_latest]` ; les posts `ready` gardent le leur.
  - **Parts de familles** (`families.share`, portée compte) : un seul type, `dict[Family, float]` par compte, pour
    les trois formes de `resolve` : valeur dérivée `{"monetizable": 1 − x, "long": x}` ; `default = {f:
    DEFAULT_SHARE[f][plateforme du compte] for f in Family}` ; `config_value = {f: config.family_share(f,
    plateforme) for f in Family}` si au moins une part est écrite dans la config, sinon `None`. `x =
    long_explore_share` tant que moins de `long_min_posts` posts `long` sont jugés ; ensuite `x =
    clamp(long_explore_share × ratio_long / ratio_monetizable, long_bounds)` (moyennes des ratios). Le tirage de
    famille du best-of (`cut/bestof.py`, tâche 13) passe à `resolve("families.share", compte propriétaire,
    config_value=…, default=…)` ; les parties restent `monetizable` ; une compilation thématique prend la famille
    écrite dans le YAML de son thème (tâche 30, défaut `monetizable`), sans tirage.
  - **Seuil d'approbation** (`approval.threshold`, portée verticale) : médiane des jours de stock des comptes de
    la verticale < `stock_low_days` → `− threshold_step` ; > `stock_high_days` → `+ threshold_step` ; borné à
    `threshold_bounds` ; actif dès la première semaine (il ne dépend que du stock).
  - **Part de vertical** (`layouts.vertical_share`, portée compte) : si le bras `vertical` a au moins
    `vertical_min_outcomes` résultats, `clamp(prob_better(vertical, square), vertical_bounds)`, rng à graine
    `derive_seed(compte, semaine ISO, "vertical")` ; sinon aucune valeur.
  - Enregistrement : `derived.record(key, scope, new, source=f"recalibrate:{méthode}", confidence, at)` seulement si
    `new` diffère de la dernière valeur dérivée ; une valeur écrite dans la config est quand même recalculée et
    enregistrée (historique), mais `resolve` renvoie la config (`Change.forced = True`, signalé dans le rapport :
    « forcé par la config : recalage non appliqué »). Aucun `NaN` ni infini n'est jamais enregistré (valeurs
    filtrées par `math.isfinite`).
  - **Chute des vues** (`views_drop`, profil `stats`) : vues à `judge_after_h` des posts publiés dans les
    `drop_recent_days` (21) derniers jours contre ceux des `drop_base_days` jours précédents ; au moins
    `drop_min_posts` (5) de chaque côté (réglage prudent de Louis, L33 ; au rythme de lancement, 1 post par jour sur
    TikTok et 0,6 sur Instagram, la fenêtre récente compte environ 18 et 10,8 posts jugeables : l'alerte peut partir) ; `médiane(récents) < drop_ratio × médiane(base)` → `Message("warn", "<compte> : vues médianes
    1 200 contre 4 800 (−75 %)", hint="Vérifier le Statut du compte Instagram ou les vidéos non éligibles sur TikTok ;
    envisager originality: advanced pour ce compte (R50)", alert="views_drop:<compte>")`. Compte déjà `advanced` :
    même alerte sans la suggestion.
  - Rapport hebdomadaire : section « Recalage » (une ligne par `Change`) et section « Originalité » (rappel : Statut
    du compte de chaque compte Instagram, vidéos « non éligibles au fil Pour toi » de chaque compte TikTok).
  - Section « Rythme » (décision de Louis du 2026-11-02 : il monte le rythme lui-même dans `posts_per_day`) :
    rushes ne modifie jamais `posts_per_day` ; il signale un compte qui pourrait monter d'un palier (TikTok 1 → 2
    → 3 par jour, Instagram 0,6 → 1 par jour) quand, avec la ligne de config à changer :
    - « stock au-dessus de `rate_stock_days` depuis `rate_window_days` » : toutes les valeurs `stock.days` du compte
      dans `derived_values_history` des `rate_window_days` derniers jours (une par jour, `ReportStep` de la tâche 20)
      sont > `rate_stock_days`, et il y en a au moins `rate_window_days − 1` ;
    - « médiane de vues qui ne baisse pas » : médiane des vues à `judge_after_h` des posts publiés dans les 14
      derniers jours ≥ médiane de ceux des 14 jours précédents (au moins `drop_min_posts` de chaque côté) ;
    - aucune alerte `views_drop:<compte>` envoyée dans les 7 derniers jours (`alerts_sent`).

- [ ] **Step 1 : Tests qui échouent**

```python
def test_hour_scores_smoothing_reference():
def test_slots_move_to_better_neighbor_hour():
    # créneaux ["15:00", "18:00"] ; 15 h : ratio 0,8 (n 10) ; 16 h : 1,6 (n 6) ; 17 h : 1,0 (n 4) ; 18 h : 1,0 (n 10)
    # → ["16:00", "18:00"]
def test_no_slot_recalibration_before_3_weeks_or_min_posts(ctx):
def test_slot_scores_use_only_matched_posts(ctx):          # posts « Publié » sans vidéo rapprochée → absents de hour_scores
def test_recalibrated_value_recorded_with_origin(ctx):     # derived_values.source "recalibrate:slots", 1 ligne d'historique, resolve → origin "derived"
def test_config_value_wins_over_recalibration(ctx, edit_yaml):   # slots écrits → resolve origin "config" ; dérivée enregistrée quand même, forced
def test_long_share_explores_then_follows_ratio():
def test_threshold_follows_stock_within_bounds():
def test_vertical_share_from_layout_arms():
def test_bestof_family_draw_uses_resolved_share(ctx):
    # derived families.share {"monetizable": 0.7, "long": 0.3} → tirage du best-of selon ces parts ; part écrite
    # dans la config → config_value dict[Family, float] l'emporte ; rien → default construit depuis DEFAULT_SHARE
def test_slot_explore_shifts_some_slots_reproducibly():    # 1 000 créneaux, explore 0,25 → 200 à 300 décalés ; deux appels identiques
def test_views_drop_alert_once_per_day(ctx):               # alert views_drop:st_tt ; second run le même jour → dédoublonnée
def test_views_drop_fires_at_launch_rhythm(ctx):
    # TikTok 1 post/jour et Instagram 0,6 post/jour pendant 56 jours, vues ÷ 4 sur les 21 derniers jours
    # → alert views_drop pour les deux comptes
def test_recalibrate_sparse_and_outlier_data_bounded(ctx):
    # compte sans post, une vidéo à 100× la médiane, tous les posts à 15 h → bornes respectées, aucun NaN, rien sous min_posts
def test_weekly_report_lists_changes_and_originality_reminders(ctx):
def test_weekly_report_suggests_rate_step_never_changes_config(ctx):
    # 14 lignes stock.days à 20 dans derived_values_history, médiane des vues des 14 derniers jours ≥ celle des 14
    # précédents → « tiktok : passer posts_per_day à 2 » ; config et derived_values des clés recalées inchangés ;
    # une valeur stock.days à 12 dans la fenêtre, ou médiane en baisse → aucune suggestion
def test_r16_recalibrate_rerun_same_week_changes_nothing(ctx):
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS. Suite rapide, ruff,
  mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(stats): weekly recalibration of slots, family shares, approval threshold and vertical share, views drop alert`

---

## Porte de sortie de la phase 3

**Avant le lancement :**

- [ ] Collecteurs verts sur données enregistrées (`tests/test_stats_*`, `tests/test_detect.py`), avec des
  fixtures Instagram tirées de l'export réel de Louis (L37 ; `tests/fixtures/instagram/SOURCES.md` ; repli C10 seulement
  si Meta ne produit aucun export) ; confirmation sur le premier vrai export au rodage (critère
  `instagram_import`, tâche 41).
- [ ] Sur le serveur, unités installées par `scripts/install_units.sh` : minuteurs actifs **3 jours** sans
  intervention (`systemctl list-timers` ; `rushes status` : chaque profil a tourné, aucun run `failed`) ;
  sauvegarde présente chaque matin et `rushes backup verify` vert.
- [ ] Une alerte de chaque clé reçue sur l'iPhone (`rushes notify test`) ; résumé quotidien reçu les 3 matins.
- [ ] Page de `rushes serve` utilisée depuis le Mac derrière GoDoxy et PocketID (route ajoutée par Louis) :
  télécharger, partager par AirDrop vers l'iPhone, copier la légende, écarter, « Accroche 2 » (nouveau paquet
  arrivé en moins de 10 min hors voie express, aucune alerte `timer_failed`), déposer un export Instagram (fichier
  présent dans `data/inbox/<compte>/`, importé au run `stats` suivant).
- [ ] `tests/rules_pending.toml` ne contient plus R9, R36, R37, R38 ; suite rapide, ruff, mypy, lint et build du front
  verts.

**Pendant le rodage (tâche 41)** : publication détectée seule sur chaque compte, statistiques collectées toutes les
6 h, premier export Instagram importé, bandits et recalage actifs après 3 semaines.
