# Phase 2 — Contenu intelligent : plan d'implémentation

> **Pour les agents qui exécutent le plan :** sous-skill requis : `superpowers:subagent-driven-development`
> (recommandé) ou `superpowers:executing-plans`, tâche par tâche. Lire d'abord `docs/plans/00-master.md` (contrats
> transverses, sections citées « §5.x ») et les blocs **Interfaces** de `phase-1-squelette.md` (noms réutilisés ici).
> Les étapes utilisent des cases `- [ ]`.

**But :** rushes trouve seul sur YouTube les épisodes les plus vus d'une série, les télécharge, les nettoie et les
découpe, les analyse (vision, sons, parole), en tire parties et best-of avec accroches, approuve, attribue, classe,
recadre, et rend chaque post dans le module d'originalité de son compte, sans intervention.

**Architecture :** découverte puis ingestion (10a, 10b), nettoyage v2 (11), analyse en « facettes » qui sont chacune
une étape distincte, pour charger chaque modèle une seule fois par étape (R19, L23) (12a, 12b, 12c), best-of (13), accroches
(14), approbation (15), rotation (16a), file et rendu à la demande (16b), recadrage (34), module avancé (35).

**Pile :** yt-dlp (+ Deno, `yt-dlp-ejs`, curl-cffi), scipy, open\_clip, sherpa-onnx (CED, Supertonic, Kokoro),
faster-whisper, llama-cpp-python, OpenCV contrib (Farneback, saillance, YuNet), rapidfuzz, libass.

**Spec :** `docs/spec.md`, `docs/decisions.md`, `docs/plans/00-master.md`.

## Global Constraints

Toutes celles de `00-master.md` §2 et de la phase 1 (chemins relatifs à `data_dir`, `ids`, `atomic_path`, horodatages
relatifs à `clean.mp4`, préset `ultrafast` en test, fin de tâche) s'appliquent. En plus, pour cette phase :

- **Un modèle lourd par étape** (R19, lecture L23) : chaque modèle est chargé une fois par étape, jamais par
  élément ; un seul modèle lourd en mémoire à la fois. Chaque facette d'analyse et chaque usage d'un modèle au
  rendu est une étape distincte qui traite tous ses éléments ; jamais deux modèles lourds en mémoire dans la même
  étape. Deux étapes d'une même commande peuvent recharger le même modèle (LLM dans `hooks` puis `commentary`, CLIP
  dans `vision` puis `reframe`, Whisper dans `transcribe` puis `voice`) : ce n'est pas une violation.
  `RUN_ORDER` final de la phase :
  `("discover_youtube", "ingest", "clean", "analyze", "vision", "sounds", "transcribe", "resource", "cut", "bestof",
  "hooks", "approve", "assign", "queue", "reframe", "commentary", "voice", "render", "meta", "package")`.
- **Horodatages externes** (heatmap, commentaires, signaux) : stockés en temps de la **vidéo source** dans
  `video_signals` ; convertis en temps de `clean.mp4` par `source_to_clean_ms` (tâche 13) au moment de l'usage.
- **Réseau** : uniquement par `ctx.net.ytdlp` et `ctx.net.http` ; dans les tests, `CassetteYtDlp` et `FakeHttp`
  (`tests/fakes/net.py`). Une cassette absente lève `CassetteMissing` : on ne « devine » jamais une réponse.
- **Valeurs recalables** (`approval_threshold`, `slots`, `families.*.share`, `layouts.vertical_share`) : facultatives
  dans la config (décision de rédaction, `decisions.md`) ; lues par `derived.resolve(config_value=<valeur écrite ou
  None>, default=<défaut de la spec>)`.
- **`--force`** (§5.3) ne retouche jamais un élément qui a un descendant attribué, rendu ou publié (`cut`,
  `bestof`, `hooks`, `approve`) : élément `skipped`, message « déjà attribué ». Pour calibrer, `approve --dry-run`
  (tâche 15).
- **Textes générés** dans la langue de la marque propriétaire (R21) : `config.brand_of(owner_account_id).language`.
- Doublures de modèles (`tests/fakes/models.py`) déterministes : `FakeImageEncoder` (vecteurs fournis par le test),
  `FakeSoundTagger` (bips de 1000 Hz → `impact`), `FakeTranscriber` (mots fournis par le test), `FakeTextGenerator`
  (réponses scriptées, `None`, exception ou lenteur), `FakeSpeechSynth` (sinus de 300 ms par mot),
  `FakeFaceDetector` (boîtes scriptées).

## Review Focus

1. **YouTube casse en plein run** (`DownloadError`, « Sign in to confirm », chaîne supprimée) : la série concernée
   est `warned`, les autres continuent, rien n'est à moitié écrit, code de sortie 5 (ou 4 si un élément a échoué).
   Test : `test_discover_survives_extractor_failure` (tâche 10a).
2. **Longue compilation sans images de référence ni thème** (2 h, épisodes séparés par un noir et un silence) :
   découpée sur les noirs + silences, sinon traitée comme un seul long épisode ; jamais de plantage. Test :
   `test_long_compilation_without_refs_splits_on_black_silence` (tâche 11).
3. **Modèle de langage défaillant** (fichier absent, mémoire insuffisante, sortie invalide, trop lent) : accroches
   par règles, étape `done`, avertissement unique. Test : `test_hooks_fall_back_to_rules_on_timeout_and_garbage`
   (tâche 14).
4. **Compte sans stock** (aucun passage approuvé, tout en `needs_review`) : la file est vide, un avertissement
   « 0 jour de stock » apparaît dans `status`, aucune erreur. Test : `test_queue_empty_stock_warns` (tâche 16b).
5. **Plans dégénérés au recadrage** (plan fixe sans mouvement, plan de moins de 0,5 s, images noires) : fenêtre
   centrée statique, aucun `NaN`, rendu conforme. Test : `test_reframe_static_and_tiny_shots_no_nan` (tâche 34).

---

### Task 10a : `discover youtube` (chaînes officielles, vidéos les plus vues, score, signaux)

**Files :**
- Create : `src/rushes/net/ytdlp.py`, `src/rushes/net/ratelimit.py`, `src/rushes/discover/__init__.py`,
  `src/rushes/discover/channels.py`, `src/rushes/discover/videos.py`, `src/rushes/discover/score.py`,
  `src/rushes/discover/signals.py`, `src/rushes/discover/discover.py`,
  `src/rushes/db/migrations/versions/0004_discovery.py`, `scripts/record_cassette.py`
- Modify : `src/rushes/net/base.py` (`YtDlp.download`, `YtDlp.extract(..., cookies_allowed)`, §5.8),
  `src/rushes/context.py` (vrai `YtDlpClient`),
  `src/rushes/config.py` (`DiscoverSettings`), `src/rushes/db/models.py`, `src/rushes/ids.py` (`source_key`),
  `src/rushes/steps.py`, `src/rushes/cli.py` (`discover youtube`)
- Test : `tests/fakes/net.py` (`CassetteYtDlp` complet), `tests/test_ytdlp.py`, `tests/test_discover_channels.py`,
  `tests/test_discover_score.py`, `tests/test_discover.py`, `tests/fixtures/cassettes/*.json`, `tests/test_cli.py`
  (`discover youtube` passe de `PENDING` à `IMPLEMENTED`)

**Interfaces :**
- Consomme : `Settings.rate_limit_s`, `Settings.youtube.cookies`, `Paths`, `Emitter`, `derived.record/resolve`,
  `SourceUnavailable`, `hashtag_from_name`.
- Produit :
  ```python
  # net/ytdlp.py
  SIGNIN_MARKERS = ("Sign in to confirm", "confirm you're not a bot")
  class ExtractError(SourceUnavailable): signin: bool
  def cassette_key(url: str, *, flat: bool, platform: str, comments: int, playlist_items: str | None) -> str: ...
  #   sha1 hex de json.dumps({...}, sort_keys=True)
  def sanitize(info: dict[str, Any]) -> dict[str, Any]: ...
  #   retire récursivement formats, requested_formats, thumbnails, http_headers, automatic_captions, subtitles,
  #   cookies et toute clé "url" sous un format ; applique yt_dlp.YoutubeDL.sanitize_info
  class YtDlpClient:   # implémente YtDlp
      def __init__(self, settings: Settings, paths: Paths, ui: Emitter, *,
                   ydl_factory: Callable[[dict[str, Any]], Any] = yt_dlp.YoutubeDL) -> None: ...
      def options(self, *, flat: bool, platform: Literal["youtube", "tiktok"], comments: int,
                  playlist_items: str | None, cookies: Path | None) -> dict[str, Any]: ...
      def extract(self, url: str, *, flat: bool, platform: Literal["youtube", "tiktok"],
                  comments: int = 0, playlist_items: str | None = None,
                  cookies_allowed: bool = False) -> dict[str, Any]: ...   # True : DiscoverYoutubeStep seul (R18)
      def download(self, url: str, out_dir: Path, *, max_height: int) -> Path: ...   # tâche 10b
  # net/ratelimit.py
  class RateLimiter:
      def __init__(self, min_interval_s: float, *, monotonic: Callable[[], float] = time.monotonic,
                   sleep: Callable[[float], None] = time.sleep) -> None: ...
      def wait(self) -> None: ...
  # ids.py
  def source_key(source_video_id: int) -> str: ...   # "v_000012"
  # config.py
  class DiscoverWeights(_Model): views_per_day: float = 0.5; overperformance: float = 0.3; engagement: float = 0.2
  class DiscoverSettings(_Model):
      refresh_days: int = 7; popular_limit: int = 200; median_window: int = 50; full_extract_per_run: int = 20
      search_results: int = 20; min_duration_s: int = 120; max_duration_s: int = 10_800
      keep_top: int = 30; keep_community: int = 5; min_score: float = 0.0
      official_min_followers: int = 100_000; official_min_name_sim: float = 80.0
      official_min_content_share: float = 0.6; weights: DiscoverWeights = DiscoverWeights()
  class Settings: ...  # + discover: DiscoverSettings = DiscoverSettings()
  # discover/channels.py
  @dataclass(frozen=True)
  class ChannelCandidate:
      channel_id: str; handle: str | None; title: str; verified: bool; followers: int | None
      name_sim: float; content_share: float
  def channel_search_url(query: str) -> str: ...    # https://www.youtube.com/results?search_query=<quote_plus>&sp=EgIQAg%3D%3D
  def popular_playlist_url(channel_id: str) -> str: ...   # https://www.youtube.com/playlist?list=UULP<channel_id[2:]>
  def rank_channels(series_title: str, search_info: Mapping[str, Any],
                    popular_titles: Mapping[str, Sequence[str]]) -> list[ChannelCandidate]: ...
  def classify(c: ChannelCandidate, s: DiscoverSettings) -> tuple[Literal["official", "community", "ignore"], float]: ...
  def tiktok_handles(descriptions: Iterable[str]) -> list[str]: ...
  # discover/score.py
  @dataclass(frozen=True)
  class VideoStats:
      views: int | None; likes: int | None; comments: int | None; published: date | None
      channel_median_views: float | None
  def video_score(v: VideoStats, *, today: date, w: DiscoverWeights) -> float: ...
  # discover/signals.py
  def heatmap_signals(heatmap: Sequence[Mapping[str, float]] | None) -> list[tuple[int, float]]: ...  # (t_ms milieu, value)
  def comment_timestamps(comments: Sequence[Mapping[str, Any]]) -> list[tuple[int, float]]: ...
  # discover/videos.py
  def candidate_queries(title: str, characters: Sequence[str]) -> list[str]: ...
  def upsert_source_video(session: Session, info: Mapping[str, Any], *, kind: Literal["official", "community"],
                          series_id: str | None, creator_id: str | None, via: str) -> int: ...
  # discover/discover.py
  class DiscoverYoutubeStep(BaseStep): name = "discover_youtube"   # éléments : séries actives à rafraîchir
  ```
- Règles fixées :
  - `options()` : `quiet`, `no_warnings`, `skip_download=True`, `check_formats=False`,
    `ignore_no_formats_error=True`, `sleep_interval_requests=settings.rate_limit_s`, `extractor_retries=2`,
    `js_runtimes={"deno": {}}` (YouTube), `extract_flat="in_playlist"` si `flat`, `playlist_items` si donné ;
    `comments > 0` → `getcomments=True`, `extractor_args={"youtube": {"max_comments": [str(comments)],
    "comment_sort": ["top"]}}` ; TikTok → `impersonate=ImpersonateTarget("chrome")`. Jamais `cookiefile` sauf
    `cookies` fourni.
  - Cookies (§5.8, R18) : seulement si `cookies_allowed=True`, que seul `DiscoverYoutubeStep` passe (la veille, le
    catalogue `creators_backfill`, les extractions de la voie express et la prospection jamais ; `creators_backfill`
    n'est pas une découverte). `download` suit la même règle de secours que la découverte (L32). Sans `cookies_allowed`, une erreur de connexion → `ExtractError(signin=True)` sans nouvelle
    tentative ni cookies, quel que soit le mode. Avec `cookies_allowed` : mode `never` → `ExtractError(signin=True)` ;
    `fallback` → une seule nouvelle tentative avec une
    copie 0600 de `data/cookies/youtube.txt` dans un `TemporaryDirectory` (supprimée ensuite), après une erreur dont le
    texte contient un `SIGNIN_MARKERS`, puis `Message("warn", "YouTube exige une connexion : cookies de secours
    utilisés", hint=..., alert="youtube_signin")` ; `always` → cookies dès la première requête. Nouvelle erreur de
    connexion avec les cookies, ou fichier `data/cookies/youtube.txt` absent quand il en faut →
    `ExtractError(signin=True)` et `Message("warn", "Cookies YouTube expirés ou absents", hint=...,
    alert="cookies_expired")`. Le contenu du fichier est passé à
    `register_secret` ligne par ligne (R47). Toute autre `DownloadError` → `ExtractError(signin=False)`.
  - `RateLimiter(settings.rate_limit_s)` partagé par toutes les requêtes du client, en plus de
    `sleep_interval_requests` (R18).
  - Migration `0004_discovery.py` : `source_videos` + `title TEXT`, + `channel_handle TEXT`,
    + `discovered_via TEXT CHECK IN ('popular','search','channel','rss','backfill')`, + `extracted_at TEXT`,
    `status CHECK IN ('found','retained','downloaded','rejected','failed')` ; `compilations.status CHECK IN
    ('proposed','approved','needs_review','rejected')` (valeurs utilisées par les tâches 13, 15, 30 et 32 ;
    `review build` liste les compilations `needs_review`) ; table `video_signals`
    (`id` PK, `source_video_id` FK, `kind CHECK IN ('heatmap','comment_ts','sponsor_highlight','laughter','quote','community')`,
    `t_ms INTEGER`, `weight REAL`, `collected_at TEXT`, index `(source_video_id, kind)`) ; table `channels` (colonnes
    de la spec, `status CHECK IN ('candidate','followed','authorized','rejected')`, utilisée en tâche 27).
  - Score (spec, Algorithmes) : `a = log10(1 + vues/jour)` (jours depuis la publication, minimum 1),
    `b = log10(1 + vues / médiane des median_window dernières vidéos de la chaîne)`,
    `c = log10(1 + 1000 × (likes + 2 × commentaires) / vues)` ; score = moyenne pondérée des composantes
    **disponibles** (poids renormalisés) ; aucune composante → 0.
  - Classement des chaînes : `name_sim = rapidfuzz.fuzz.token_set_ratio(titre série, titre chaîne)` sur textes
    repliés ASCII minuscules ; `content_share` = part des 30 titres populaires dont `fuzz.partial_ratio(titre série,
    titre vidéo) ≥ 80`. Officielle si vérifiée, abonnés ≥ `official_min_followers` et
    (`name_sim ≥ official_min_name_sim` ou `content_share ≥ official_min_content_share`), confiance
    `min(1, 0,4 + 0,3 × name_sim/100 + 0,3 × content_share)` ; communautaire si `content_share ≥ 0,5` sans les
    autres critères (doute → `community`, R15) ; sinon ignorée.
  - Algorithme d'une série :

```text
1. officielles = resolve("youtube.official_channels", sid, config_value=[ids kind official de la config] or None, default=[])
   si origine "default", ou dérivée plus vieille que refresh_days :
     recherche de chaînes (playlist_items "1:10") ; pour les 5 premières : popular_playlist_url (playlist_items "1:30")
     rank_channels → classify → record("youtube.official_channels", sid, [ids], source="channel_search", confidence=max)
     communautaires → record("youtube.community_channels", sid, [ids], ...)
2. pour chaque officielle : UULP (playlist_items f"1:{popular_limit}") → upsert kind official, via "popular" ;
   médiane : /channel/<id>/videos (playlist_items f"1:{median_window}")
2 bis. communautaires = resolve("youtube.community_channels", sid, config_value=[ids kind community de la config]
   or None, default=[]) ; pour chacune : UULP (playlist_items "1:30") → upsert kind community, via "channel"
3. requêtes = resolve("youtube.search_queries", sid, config_value=sources.search_queries or None,
                      default=candidate_queries(title, personnages)) ;
   ytsearch{search_results}:<q> → vidéos des chaînes non officielles → kind community, via "search" ;
   si la valeur dérivée est à recalculer : garder les 3 requêtes de plus forte médiane de vues → record
4. durée hors [min_duration_s, max_duration_s] → status 'rejected'
5. extraction complète (flat=False, comments=0 pour une série) des full_extract_per_run meilleures vidéos jamais
   extraites (classement provisoire par vues) → likes, commentaires, date, description, heatmap → video_signals ;
   tiktok_handles(descriptions officielles) → record("tiktok.official_accounts", sid, …)
6. score de chaque vidéo extraite ; 'retained' pour les keep_top meilleures officielles ≥ min_score et les
   keep_community meilleures communautaires ; record("youtube.last_discovery", sid, today)
```

  `candidate_queries` : `[title, f"{title} cartoon", f"{title} full episode"]` + `f"{title} {perso}"` pour les deux
  premiers personnages. `tiktok_handles` : motifs `tiktok.com/@([\w.]+)` et `TikTok\s*[:：]\s*@([\w.]+)`, dédoublonnés,
  en minuscules, préfixés « @ ». `comment_timestamps` : motif `\b(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\b`, poids
  `1 + log1p(like_count)`.
  - Une erreur `ExtractError` sur une requête fait passer la série en `warned` (message court), les données déjà
    obtenues sont gardées ; l'étape renvoie `Outcome(status="warned", message=…, source_unavailable=True)` :
    `StepReport.unavailable` le compte et `exit_code_for` sort en 5 si `unavailable > 0` sans élément `failed` ni
    autre erreur (§5.3, §5.4). Une exception non attrapée reste `failed` (code 4).
  - Amorces de la config (tâche 33) : un identifiant de chaîne hors `^UC[\w-]{22}$`, un compte TikTok hors
    `TIKTOK_HANDLE` (`^@(?=[\w.]*\w)[\w.]+$`, C27) ou un fichier `refs/…` absent est refusé au chargement (`ConfigError`) ; l'étape ne voit donc
    jamais de valeur fictive (`UC...`, `@...`).
  - `scripts/record_cassette.py URL [--flat] [--platform] [--comments N] [--playlist-items X]` : appelle le vrai
    client sans cookies, applique `sanitize`, écrit `tests/fixtures/cassettes/<cassette_key>.json`. Cassettes à
    enregistrer (sur le Mac, réseau de développement) : recherche de chaînes « Oggy » et « Tom and Jerry », les UULP
    (`1:30`) et `/videos` (`1:50`) des 3 premières chaînes trouvées, une recherche `ytsearch20:oggy`, l'extraction
    complète d'une vidéo officielle avec heatmap et d'une sans heatmap.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_r18_ytdlp_params_metadata_only(tmp_path):
    o = client(tmp_path).options(flat=True, platform="youtube", comments=0, playlist_items=None, cookies=None)
    assert o["skip_download"] is True and o["check_formats"] is False and o["ignore_no_formats_error"] is True
    assert o["sleep_interval_requests"] == 2 and "cookiefile" not in o and o["extract_flat"] == "in_playlist"
def test_tiktok_options_impersonate_chrome(tmp_path):
def test_r18_cookies_only_after_signin_error(tmp_path):
    # fabrique : 1er appel DownloadError("Sign in to confirm you're not a bot"), 2e appel ok
    # → 2e options contiennent cookiefile ≠ data/cookies/youtube.txt, mode 0o600 ; Message "warn" émis
    # erreur "Video unavailable" → aucune nouvelle tentative, ExtractError(signin=False)
    # (appel avec cookies_allowed=True) ; le Message "warn" porte alert="youtube_signin"
def test_r18_no_cookies_unless_allowed(tmp_path):        # mode always, cookies_allowed=False → "cookiefile" absent des
    # options ; erreur de connexion → ExtractError(signin=True), une seule tentative
def test_r18_cookies_retry_failure_alerts_cookies_expired(tmp_path):
    # 2e erreur "Sign in" avec cookies → ExtractError(signin=True) + alert="cookies_expired" ;
    # fichier data/cookies/youtube.txt absent (mode always) → même alerte, aucune requête avec cookies
def test_r18_cookies_never_mode_raises_source_unavailable(tmp_path):
def test_r47_cookie_values_never_logged(tmp_path):        # valeur d'un cookie absente de la sortie et du journal
def test_rate_limiter_spaces_calls():                     # horloge factice : 3 wait() → sleep(2.0) deux fois
def test_cassette_key_stable_and_sanitize_drops_media_urls():
def test_official_channel_identified_from_recorded_search():   # cassettes Oggy → chaîne officielle, confiance ≥ 0,7
def test_unverified_lookalike_stays_community():
def test_config_channel_overrides_derived(ctx, edit_yaml):
@pytest.mark.parametrize(("stats", "expected"), SCORE_CASES)   # 3 cas calculés à la main, dont likes absents
def test_video_score_reference_values(stats, expected): assert video_score(stats, today=D, w=W) == pytest.approx(expected)
def test_score_ranking_reproducible_on_recorded_metadata(ctx):  # deux runs --force → même liste 'retained', même ordre
def test_heatmap_and_comment_signals_parsed():
def test_tiktok_handles_from_descriptions():
def test_duration_filter_rejects_shorts_and_huge(ctx):
def test_discover_survives_extractor_failure(ctx):        # une série lève ExtractError → warned ; l'autre done ; code 5
    # StepReport.unavailable == 1, failed == 0
def test_community_channels_read_via_channel(ctx):         # chaîne community de la config → UULP 1:30, via "channel"
def test_r16_discover_rerun_within_refresh_changes_nothing(ctx):
```

- [ ] **Step 2 : Enregistrer les cassettes** — `uv run python -I scripts/record_cassette.py …` pour la liste
  ci-dessus ; relire chaque fichier : aucune URL `googlevideo.com`, aucun cookie, aucun jeton.

- [ ] **Step 3 : Lancer** → FAIL. **Step 4 : Implémenter** (migration, client, découverte ; `discover_youtube` en tête
  de `RUN_ORDER`). **Step 5 : Lancer** → PASS ; retirer R18 de `rules_pending.toml`. Suite rapide, ruff, mypy verts.

- [ ] **Step 6 : Commit proposé** — `feat(discover): youtube discovery with official channel detection, popular videos and scoring (R18)`

---

### Task 10b : Ingestion YouTube (sélection selon le stock, téléchargement)

**Files :**
- Create : `src/rushes/ingest/select.py`
- Modify : `src/rushes/net/ytdlp.py` (`download`), `src/rushes/ingest/ingest.py`, `src/rushes/ingest/owner.py`
  (plus petit `stock_days`), `src/rushes/cut/parts.py` (épisodes `community` exclus), `src/rushes/config.py`
  (`IngestSettings`)
- Test : `tests/test_ingest_youtube.py`, `tests/fakes/net.py` (`download` de `CassetteYtDlp` copie une vidéo
  synthétique enregistrée par le test)

**Interfaces :**
- Consomme : `YtDlp.download`, `choose_owner`, `source_key`, `atomic_path`, `probe`, `sha256_file`.
- Produit :
  ```python
  class IngestSettings(_Model):
      max_downloads_per_run: int = 6; stock_days_target: int = 14; est_post_s: int = 90
      community_max_per_series: int = 5; max_height: int = 1080
  class Settings: ...  # + ingest: IngestSettings = IngestSettings()
  # ingest/select.py
  def stock_days(session: Session, config: Config, account_id: str) -> float: ...
  def select_downloads(session: Session, config: Config, s: IngestSettings, *, today: date) -> list[int]: ...
  # IngestStep : éléments = fichiers de data/import/ (tâche 4) puis vidéos de select_downloads
  ```
- Règles fixées :
  - `stock_days` = (posts du compte en `planned|rendered|ready`, tests compris, + Σ durée / `est_post_s` des épisodes
    du compte pas encore découpés) / `posts_per_day[plateforme]`. « Pas encore découpés » = `provenance !=
    'community'`, `status IN ('imported','cleaned','analyzed')` et aucun segment `part` (ni épisode communautaire,
    jamais découpé, ni parent `split`, compté par ses enfants). Les posts `is_test` d'un sujet hors config (candidat
    de la prospection, tâches 27 et 38) ne comptent pas : leur vitrine ne freine pas le téléchargement.
  - Propriétaire (remplace le critère de la tâche 4) : parmi les comptes des marques candidates, celui de plus petit
    `stock_days`, recalculé après chaque épisode inséré du run (flush) ; égalité → identifiant le plus petit.
  - `select_downloads` : besoin d'un compte = `max(0, stock_days_target − stock_days)` × `posts_per_day` ; besoin
    d'une série = somme des besoins des comptes de ses marques candidates (celles de `choose_owner` : période de
    `rotation` de la config couvrant `today`, sinon toutes les marques de la verticale ; la tâche 16a remplace par
    `current_rotation`) ; séries triées par besoin
    décroissant ; on prend la vidéo `retained` officielle de meilleur score de la série, on retire
    `durée / est_post_s` au besoin, on recommence, jusqu'à `max_downloads_per_run`. Ensuite, au plus une vidéo
    communautaire par série et par run, dans la limite de `community_max_per_series` au total.
  - `download` : options distinctes de la collecte : `format = f"bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]"`,
    `merge_output_format="mp4"`, `outtmpl={"default": str(out_dir / "%(id)s.%(ext)s")}`, `noplaylist=True`,
    `concurrent_fragment_downloads=1`, même `sleep_interval_requests` que la collecte ; cookies en secours comme la découverte
    (R18 étendue, L32) : connexion exigée → une nouvelle tentative avec cookies et alerte `youtube_signin` ; échec avec
    cookies → `cookies_expired` ; mode `never` → `failed`, nouvel essai au run suivant. Test
    `test_r18_download_falls_back_to_cookies_with_alert`. Téléchargement
    dans `paths.tmp / f"dl-{pid}"` (`data/tmp/`, tâche 2), puis renommage atomique en `sources/<source_key>.mp4`.
  - Épisode créé : `provenance = source_videos.kind`, `source_video_id`, propriétaire par `choose_owner`, statut
    `imported` ; `source_videos.status = 'downloaded'` ; échec → `failed`, dossier temporaire supprimé.
  - `CutStep.select` (tâche 6) et `BestofStep.select` (tâche 13) excluent `provenance = 'community'` : un épisode
    communautaire n'est qu'un signal (R15, re-sourcé en tâche 15).

- [ ] **Step 1 : Tests qui échouent**

```python
def test_select_downloads_fills_lowest_stock_first(session):
def test_select_downloads_respects_max_per_run(session):
def test_stock_days_ignores_community_and_split_parents(session):   # épisode community + parent split → 0 jour
def test_owner_balances_within_one_run(ctx, synth):     # 4 épisodes dans un run, 2 comptes à stock nul → 2 et 2
def test_download_uses_separate_options_from_collection(tmp_path):   # format, mp4, pas de skip_download, sleep conservé
def test_ingest_youtube_downloads_and_creates_episode(ctx, synth):     # provenance 'official', sources/v_000001.mp4
def test_failed_download_leaves_no_file_and_marks_failed(ctx):
def test_r15_community_episode_never_cut(ctx, synth):
def test_r16_ingest_youtube_rerun_changes_nothing(ctx, synth):
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(ingest): stock-driven youtube download into sources/ (R15 community never cut)`

---

### Task 11 : Nettoyage v2 (empreinte audio du générique, découpe des compilations, logos)

**Files :**
- Create : `src/rushes/media/fingerprint.py`, `src/rushes/clean/splitter.py`, `src/rushes/clean/logo.py`,
  `src/rushes/db/migrations/versions/0005_episode_split_logo.py`
- Modify : `src/rushes/clean/clean.py`, `src/rushes/clean/intro.py`, `src/rushes/schemas.py` (`CleanFile.span`),
  `src/rushes/db/models.py`, `src/rushes/config.py` (`CleanSettings`)
- Test : `tests/fixtures/generator.py` (`theme_ms`, `make_theme`), `tests/test_fingerprint.py`,
  `tests/test_splitter.py`, `tests/test_logo.py`, `tests/test_clean_v2.py`

**Interfaces :**
- Consomme : `decode_pcm16k`, `detect_black`, `detect_silence`, `match_reference_frames`, `sample_gray`, `CleanStep`.
- Produit :
  ```python
  class CleanSettings(_Model):
      theme_threshold: float = 0.6; theme_hop_ms: int = 50; compilation_min_s: int = 1200
      episode_min_s: int = 240; episode_max_s: int = 1800; typical_episode_s: int = 420
      boundary_min_ms: int = 500; logo_samples: int = 120; logo_static_std: float = 6.0
      logo_min_edge_ratio: float = 0.01
  class Settings: ...  # + clean: CleanSettings = CleanSettings()
  # schemas.py — CleanFile gagne : span: tuple[int, int] | None = None  (portion de la source, épisode enfant)
  # media/fingerprint.py
  def band_spectrogram(pcm: NDArray[np.float32], *, hop_ms: int = 50, bands: int = 48) -> NDArray[np.float32]: ...
  def theme_matches(track: NDArray[np.float32], theme: NDArray[np.float32], *, hop_ms: int = 50,
                    threshold: float = 0.6) -> list[tuple[int, float]]: ...   # (début en ms, corrélation)
  # clean/splitter.py
  def black_silence_boundaries(blacks: Sequence[Span], silences: Sequence[Span], *, min_ms: int) -> list[Span]: ...
  def split_spans(duration_ms: int, *, theme_starts: Sequence[int], boundaries: Sequence[Span],
                  s: CleanSettings) -> list[Span]: ...    # [] = pas une compilation
  # clean/logo.py
  Corner = Literal["tl", "tr", "bl", "br"]
  def detect_logo(video: Path, *, samples: int, static_std: float, min_edge_ratio: float) -> Corner | None: ...
  ```
- Règles fixées :
  - Empreinte : STFT (`scipy.signal.stft`, fenêtre 1024, pas = `hop_ms`) du PCM 16 kHz ; 48 bandes logarithmiques
    entre 100 et 7 000 Hz ; log(1 + énergie) ; moyenne de chaque bande retirée. Corrélation normalisée (Pearson sur
    toutes les bandes) du thème glissé sur la piste (`scipy.signal.correlate`, mode `valid`) ; pics ≥ `threshold`
    séparés d'au moins la durée du thème. Le thème vient de `intro_outro.theme_audio` (chemin relatif à `config/`) ;
    clé non écrite → aucune détection par l'audio (un chemin écrit mais introuvable est refusé au chargement,
    tâche 33). Un pic est confirmé par les images de référence si la série en a (une
    correspondance `match_reference_frames` dans [pic, pic + durée du thème + 5 s]) ; sans images, l'audio suffit.
  - Génériques en bordure : un pic de thème qui touche le début ou la fin (à 2 s près) est retiré comme `intro` ou
    `outro`, en plus des images de référence (tâche 4).
  - Compilation (seulement si la marque du propriétaire est de verticale `cartoons` ; une vidéo de créateur n'est
    jamais découpée en épisodes) : durée ≥ `compilation_min_s` **ou** au moins 2 pics de thème intérieurs. Coupes = débuts des pics
    de thème ; sinon frontières noir ∩ silence ≥ `boundary_min_ms` : en partant de 0, prendre la frontière dont la
    distance à la coupe précédente est dans [`episode_min_s`, `episode_max_s`] et la plus proche de
    `typical_episode_s`, et recommencer. Morceaux de moins de `episode_min_s` abandonnés. Aucune coupe trouvée →
    `[]` (l'épisode reste entier).
  - Découpe : épisode parent → statut `split` (jamais analysé) ; un enfant par morceau : `parent_episode_id`,
    `span_start_ms`, `span_end_ms`, même `file_path` et même propriétaire que le parent (unicité d'une vidéo
    source), `provenance` du parent, `sha256 = f"{sha_parent}:{k}"` (clé d'idempotence), statut `imported`. Le même
    run nettoie ensuite chaque enfant sur sa portion (`CleanFile.span`).
  - Logo : 120 images grises réparties sur `clean.mp4` ; pour chaque coin (20 % × 20 %) : `std` temporel par pixel,
    médiane temporelle, contours `cv2.Canny(médiane, 50, 150)` dilatés de 1 px ; part des pixels du coin à la fois
    statiques (`std < logo_static_std`) et sur un contour ≥ `logo_min_edge_ratio`, **et** `std` moyen du centre
    > 10 (sinon vidéo fixe, pas de logo). Coin de part maximale → `episodes.logo_corner`.
  - Migration `0005_episode_split_logo.py` : `episodes` + `parent_episode_id INTEGER REFERENCES episodes(id)`,
    + `span_start_ms`, + `span_end_ms`, + `logo_corner TEXT CHECK IN ('tl','tr','bl','br')`, statut
    `CHECK IN ('imported','split','cleaned','analyzed')`.
  - Générateur : `theme_ms: Sequence[int]` insère aux instants donnés un thème fixe (accord de sinus 440/554/659 Hz
    modulé + glissando, 6 s) ; `make_theme(out: Path) -> Path` écrit ce même thème seul en WAV 16 kHz.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_theme_matches_finds_inserted_theme(synth, tmp_path):   # thème à 10 s et 70 s → 2 pics ± 100 ms ; bruit seul → []
def test_compilation_split_by_theme(ctx, synth, edit_yaml):      # réglages réduits (episode_min_s=20, compilation_min_s=60) → 3 enfants
def test_long_compilation_without_refs_splits_on_black_silence(ctx, synth, edit_yaml):
def test_creator_long_video_never_split(ctx, synth, edit_yaml):  # même vidéo, propriétaire créateur → aucun enfant
def test_children_inherit_owner_and_parent_never_analyzed(ctx, synth):
def test_theme_at_border_removed_as_intro(ctx, synth, edit_yaml):
def test_r34_logo_marks_episode(ctx, synth):                     # logo_corner="tr" → episodes.logo_corner == "tr"
def test_no_logo_on_clean_video(ctx, synth):
def test_static_slideshow_not_a_logo(ctx, synth):
def test_r16_split_rerun_changes_nothing(ctx, synth):
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(clean): theme audio fingerprint, compilation splitting and burned-in logo detection (R34)`

---

### Task 12a : Analyse — vision (CLIP, étiquettes, personnages, recherche en texte libre)

**Files :**
- Create : `src/rushes/models/clip.py`, `src/rushes/analyze/facets.py`, `src/rushes/analyze/tags.py`,
  `src/rushes/analyze/index.py`, `src/rushes/analyze/vision.py`,
  `src/rushes/db/migrations/versions/0006_vectors_words.py`
- Modify : `src/rushes/models/hub.py` (`clip()` réel), `src/rushes/db/models.py`, `src/rushes/config.py`
  (`AnalyzeSettings`), `src/rushes/steps.py`, `src/rushes/cli.py` (`scenes search "<texte>" [--series] [--account]`)
- Test : `tests/fixtures/generator.py` (`shot_labels`), `tests/test_vision.py`, `tests/test_index.py`,
  `tests/test_cli.py` (`scenes search` ajouté à `IMPLEMENTED`)

**Interfaces :**
- Consomme : `ImageEncoder` (§5.5), `ScenesFile`, keyframes de la tâche 5, `SeriesConfig.characters`.
- Produit :
  ```python
  # analyze/facets.py
  Facet = Literal["base", "vision", "sounds", "words"]
  def facets_needed(config: Config, session: Session, episode_id: int) -> set[Facet]: ...
  #   base, vision, sounds toujours ; words si série audio == "dialogue_en" ou créateur
  def facets_done(session: Session, paths: Paths, episode_id: int) -> set[Facet]: ...
  #   base = statut analyzed ; vision = une ligne scene_vectors par scène ; sounds = audio.json.events non None ;
  #   words = words.json présent
  # config.py
  class AnalyzeSettings(_Model): tag_min_cos: float = 0.24; tag_top: int = 3; character_min_cos: float = 0.82; clip_batch: int = 32
  class Settings: ...  # + analyze: AnalyzeSettings = AnalyzeSettings()
  # models/clip.py
  class OpenClipEncoder:   # ImageEncoder ; dim = 512 ; ViT-B-32 / settings.models.clip_pretrained, poids du manifeste
      def __init__(self, weights: Path, *, threads: int = 4, batch: int = 32) -> None: ...
  # analyze/tags.py
  TAG_PROMPTS: Mapping[str, str]
  def zero_shot_tags(img: NDArray[np.float32], txt: NDArray[np.float32], labels: Sequence[str], *,
                     min_cos: float, top: int) -> list[list[str]]: ...
  def character_presence(img: NDArray[np.float32], refs: Mapping[str, NDArray[np.float32]], *,
                         min_cos: float) -> list[list[str]]: ...
  # analyze/index.py
  def vector_blob(v: NDArray[np.float32]) -> bytes: ...           # float32 little-endian
  def blob_vector(b: bytes, dim: int) -> NDArray[np.float32]: ...
  def search_scenes(session: Session, encoder: ImageEncoder, query: str, *, account_id: str | None = None,
                    series_id: str | None = None, k: int = 5) -> list[tuple[int, float]]: ...   # (scene_id, cosinus)
  # analyze/vision.py
  class VisionStep(BaseStep): name = "vision"; heavy = True   # épisodes analyzed sans la facette vision
  ```
- Règles fixées :
  - `TAG_PROMPTS` : `chase` « a cartoon character chasing another one », `fall` « a cartoon character falling
    down », `explosion` « a cartoon explosion », `trap` « a cartoon character caught in a trap », `hit` « a cartoon
    character getting hit on the head », `food` « a cartoon character eating food », `sleep` « a cartoon character
    sleeping », `scared` « a scared cartoon character », `angry` « an angry cartoon character », `music` « a cartoon
    character playing music ». Étiquettes d'une scène : les `top` meilleures de cosinus ≥ `tag_min_cos`.
  - Personnages : vecteur moyen normalisé des images de référence de la config (chemins relatifs à `config/`) ;
    présent si cosinus ≥ `character_min_cos`. Sans images : liste vide (un chemin écrit mais introuvable est refusé
    au chargement, tâche 33).
  - Écritures : `scene_vectors(scene_id, model="ViT-B-32/<pretrained>", dim, embedding)`, `scenes.tags_json`,
    `scenes.characters_json` (listes JSON triées).
  - Migration `0006_vectors_words.py` : `scene_vectors` (`scene_id` PK FK `scenes.id` ON DELETE CASCADE, `model TEXT`,
    `dim INTEGER`, `embedding BLOB`) ; `words` (`id` PK, `episode_id` FK, `start_ms`, `end_ms`, `word TEXT`,
    `prob REAL`, `sentence_id INTEGER`, index `(episode_id, start_ms)`), remplie en tâche 12c.
  - Générateur : `shot_labels: Sequence[str]` écrit un mot en gros (`drawtext`, 160 px, centré) sur chaque plan.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_vector_blob_roundtrip():
def test_zero_shot_tags_top3_above_threshold():
def test_character_presence_from_refs():
def test_search_returns_top_k_filtered_by_account(session):    # vecteurs factices, filtre compte via episodes.owner_account_id
def test_vision_step_writes_vectors_tags_and_characters(ctx, synth):
def test_r19_clip_loaded_once_for_many_episodes(ctx, synth):    # 2 épisodes → hub.loads()["clip"] == 1
def test_r16_vision_rerun_changes_nothing(ctx, synth):
@pytest.mark.slow
def test_free_text_search_finds_known_gag_top5(ctx, synth):
    # 10 plans, shot_labels ["BANANA", "ROCKET", …] ; vrai CLIP ; search_scenes("a banana") → plan BANANA dans le top 5
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(analyze): clip scene vectors, zero-shot tags, characters and free-text scene search`

---

### Task 12b : Analyse — événements sonores (CED)

**Files :**
- Create : `src/rushes/models/ced.py`, `src/rushes/analyze/sounds.py`
- Modify : `src/rushes/models/hub.py` (`sounds()` réel), `src/rushes/schemas.py`
  (`AudioFile.events: list[SoundEvent] | None = None`), `src/rushes/steps.py`
- Test : `tests/test_sounds.py`

**Interfaces :**
- Consomme : `SoundTagger`, `SoundEvent` (§5.5), `decode_pcm16k`, `AudioFile`.
- Produit :
  ```python
  # models/ced.py
  class CedTagger:   # SoundTagger ; sherpa_onnx.AudioTagging (modèle CED-mini du manifeste, labels CSV fourni)
      def __init__(self, model_dir: Path, *, threads: int = 4, window_ms: int = 1000, hop_ms: int = 500,
                   top_k: int = 5) -> None: ...
      def window_tags(self, pcm16k: NDArray[np.float32]) -> list[tuple[int, list[tuple[str, float]]]]: ...
  # analyze/sounds.py
  AUDIOSET_GROUPS: Mapping[str, Literal["impact", "explosion", "splash", "scream", "laughter"]]
  def group_events(windows: Sequence[tuple[int, Sequence[tuple[str, float]]]], *, min_score: float = 0.3,
                   gap_ms: int = 500, window_ms: int = 1000) -> list[SoundEvent]: ...
  class SoundsStep(BaseStep): name = "sounds"; heavy = True   # épisodes analyzed sans la facette sounds
  ```
- Règles fixées :
  - `AUDIOSET_GROUPS` (noms exacts de l'ontologie AudioSet) : impact ← « Thump, thud », « Bang », « Slap, smack »,
    « Whack, thwack », « Smash, crash », « Knock », « Crushing », « Boing » ; explosion ← « Explosion », « Boom »,
    « Gunshot, gunfire », « Fireworks » ; splash ← « Splash, splatter », « Slosh », « Pour » ; scream ←
    « Screaming », « Yell », « Shout », « Children shouting » ; laughter ← « Laughter », « Giggle », « Chuckle,
    chortle », « Belly laugh », « Snicker », « Baby laughter ».
  - Fenêtres consécutives de même étiquette (écart ≤ `gap_ms`) fusionnées ; score = maximum.
  - `audio.json.events` écrit (liste vide si rien) ; `scenes.sound_events_json` = `{"impact": 2, …}` des événements
    qui chevauchent la scène.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_group_events_merges_and_thresholds():
def test_unmapped_labels_ignored():
def test_sounds_step_fills_events_and_scenes(ctx, synth):       # FakeSoundTagger : bip 4–5 s → impact sur la scène
def test_events_none_means_not_analyzed_empty_means_done(ctx, synth):
def test_r16_sounds_rerun_changes_nothing(ctx, synth):
@pytest.mark.slow
def test_ced_runs_on_synthetic_audio(tmp_path):                 # vrai modèle : liste de SoundEvent, aucun "laughter" sur un bip
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(analyze): ced sound events grouped into impact, explosion, splash, scream, laughter`

---

### Task 12c : Analyse — transcription mot par mot (faster-whisper)

**Files :**
- Create : `src/rushes/models/whisper.py`, `src/rushes/analyze/words.py`, `src/rushes/analyze/transcribe.py`
- Modify : `src/rushes/models/hub.py` (`transcriber()` réel), `src/rushes/steps.py`, `src/rushes/cut/parts.py`
  (`CutStep.select` attend les facettes, R24)
- Test : `tests/test_words.py`, `tests/test_transcribe.py`, `tests/test_cut.py`

**Interfaces :**
- Consomme : `Transcriber`, `Word` (§5.5), `WordsFile` (§5.6), `AudioFile.speech`, `merge`, table `words`.
- Produit :
  ```python
  # models/whisper.py
  class FasterWhisperTranscriber:   # Transcriber
      def __init__(self, model_dir: Path, *, threads: int = 4, beam_size: int = 5,
                   model_factory: Callable[..., Any] = faster_whisper.WhisperModel) -> None: ...
  # analyze/words.py
  def speech_clips(speech: Sequence[Span], *, gap_ms: int = 1000, pad_ms: int = 200, duration_ms: int) -> list[Span]: ...
  def sentences(words: Sequence[Word], *, gap_ms: int = 700) -> list[Span]: ...
  def sentence_ids(words: Sequence[Word], sents: Sequence[Span]) -> list[int]: ...
  # analyze/transcribe.py
  class TranscribeStep(BaseStep): name = "transcribe"; heavy = True   # épisodes dont words ∈ needed \ done
  ```
- Règles fixées :
  - Modèle : `WhisperModel(str(model_dir), device="cpu", compute_type="int8", cpu_threads=threads)` ;
    `model_dir = paths.cache_models / f"whisper_{settings.models.whisper}"` (fichiers `model.bin`, `config.json`,
    `tokenizer.json`, `vocabulary.txt` du manifeste, tâche 9). Appel : `transcribe(str(wav), language=…,
    word_timestamps=True, vad_filter=False, clip_timestamps=[s0, e0, s1, e1, …] (secondes),
    condition_on_previous_text=False, beam_size=beam_size)`. Jamais `BatchedInferencePipeline`.
  - Langue = langue de la marque propriétaire (R21). WAV 16 kHz mono temporaire dans `episodes/<e_key>/`, supprimé
    après usage. Aucun segment de parole → `words.json` vide (facette faite).
  - Fin de phrase : mot finissant par `.`, `!`, `?`, `…`, ou silence ≥ `gap_ms` avant le mot suivant.
  - Écritures : `words.json` (`WordsFile`, `model = f"faster-whisper-{taille}-int8"`), lignes `words` avec
    `sentence_id`.
  - Attente des facettes (R24) : `CutStep.select` (et `BestofStep.select`, tâche 13) ne prend un épisode que si
    `facets_needed ⊆ facets_done`. Transcription en échec → `words.json` absent → l'épisode n'est pas découpé (il
    l'est au run qui la réussit). « Vidéo sans parole » = `words.json` présent avec zéro mot (points des plans).

- [ ] **Step 1 : Tests qui échouent**

```python
def test_speech_clips_merge_and_pad():
def test_sentences_split_on_punctuation_and_gaps():
def test_whisper_called_with_clips_and_without_vad_filter(tmp_path):   # fabrique factice : kwargs inspectés
def test_transcribe_step_writes_words_json_and_table(ctx, synth):     # FakeTranscriber
def test_nonverbal_series_not_transcribed(ctx, synth):
def test_r21_transcription_in_brand_language(ctx, synth):              # marque fr → language="fr"
def test_r24_cut_waits_for_transcription(ctx, synth):
    # créateur, FakeTranscriber qui lève → cut ne sélectionne pas l'épisode, aucun segment ; run suivant réussi → découpé
def test_r16_transcribe_rerun_changes_nothing(ctx, synth):
@pytest.mark.slow
def test_whisper_runs_without_onnxruntime(tmp_path):                  # vrai modèle sur du bruit ; "onnxruntime" ∉ sys.modules (installé pour Silero, mais le VAD interne de faster-whisper est désactivé)
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(analyze): word-level faster-whisper transcription on silero speech clips (R21)`

---

### Task 13 : Best-of et échelle de signaux

**Files :**
- Create : `src/rushes/core/signals.py`, `src/rushes/highlights/__init__.py`, `src/rushes/highlights/ladder.py`,
  `src/rushes/highlights/popularity.py`, `src/rushes/cut/bestof.py`
- Modify : `src/rushes/render/encode.py` (`render_plate_multi`), `src/rushes/render/render.py` (posts de
  compilation), `src/rushes/approve/approve.py` (v0 : compilations ; pics de heatmap pour R5),
  `src/rushes/assign/assign.py` (compilations), `src/rushes/cut/parts.py` (`peaks_ms` lus dans la heatmap),
  `src/rushes/render/charter.py` (`Copy.bestof`) et `config/charter.yaml` (clé `copy`, `bestof`),
  `src/rushes/config.py` (`BestofSettings`), `src/rushes/steps.py`
- Test : `tests/test_core_signals.py`, `tests/test_ladder.py`, `tests/test_bestof.py`, `tests/test_cut.py`

**Interfaces :**
- Consomme : `cut_point_costs`, `CutSettings`, `ScenesFile`, `AudioFile`, `WordsFile`, `CleanFile`, `video_signals`,
  `derive_seed`, `weighted_choice`, `draw_target_ms`, `registry.assign`, `render_plate`, `facets_needed`,
  `facets_done`, `Config.family_share`, `BrandCharter.texts`.
- Produit :
  ```python
  class BestofSettings(_Model):
      bin_ms: int = 500; min_distance_ms: int = 8000; pre_ms: int = 3000; post_ms: int = 2000
      min_item_ms: int = 4000; max_item_ms: int = 25_000; transition_ms: int = 300; z_k: float = 1.5
      level1_weights: dict[str, float] = {"heatmap": 1.0, "comment_ts": 0.7, "sponsor_highlight": 1.0, "community": 0.8}
      level2_weights: dict[str, float] = {"loudness": 1.0, "shot_density": 0.5, "motion": 0.7, "impact": 0.8,
                                          "laughter": 1.0, "exclamation": 0.5, "short_sentence": 0.3}
  class Settings: ...  # + bestof: BestofSettings = BestofSettings()
  # core/signals.py
  def zscore(values: Sequence[float]) -> list[float]: ...                 # écart-type nul → zéros
  def exploitable(curve: Sequence[float], *, k: float) -> bool: ...        # un point > moyenne + k × écart-type
  def peaks(curve: Sequence[float], *, bin_ms: int, min_distance_ms: int) -> list[tuple[int, float]]: ...
  def dedupe(items: Sequence[tuple[Span, float]], *, max_overlap: float = 0.5) -> list[tuple[Span, float]]: ...
  # highlights/popularity.py
  def source_to_clean_ms(clean: CleanFile, t_src_ms: int) -> int | None: ...   # tient compte de span et kept
  def attach_popularity(session: Session, paths: Paths, episode_id: int) -> None: ...   # scenes.popularity
  def heatmap_peaks_ms(session: Session, paths: Paths, episode_id: int, *, k: float = 1.5) -> list[int]: ...
  #   maxima locaux de la heatmap (video_signals kind 'heatmap', temps clean par source_to_clean_ms, hors clean
  #   ignorés) au-dessus de moyenne + k × écart-type ; [] sans heatmap. Seule source de peaks_ms (R5) pour
  #   CutStep, rule_violations (v0) et segment_checks (tâche 15).
  # highlights/ladder.py
  @dataclass(frozen=True)
  class EpisodeSignals:
      duration_ms: int; external: Sequence[tuple[int, float, str]]       # (t_ms clean, poids, kind)
      loudness: Sequence[LoudPoint]; shots: Sequence[Span]; motion: Sequence[float]
      events: Sequence[SoundEvent]; words: Sequence[Word]
  @dataclass(frozen=True)
  class Moment: t_ms: int; score: float; level: Literal[1, 2, 3]
  def find_moments(sig: EpisodeSignals, s: BestofSettings, *, n_slices: int,
                   admissible: Sequence[int], costs: Sequence[float]) -> tuple[Literal[1, 2, 3], list[Moment]]: ...
  def widen(t_ms: int, *, admissible: Sequence[int], s: BestofSettings) -> Span | None: ...
  def pick_items(cands: Sequence[tuple[Span, float]], *, target_ms: int, min_ms: int, max_ms: int,
                 transition_ms: int, min_items: int = 4, max_items: int = 8) -> list[Span]: ...
  # cut/bestof.py
  class BestofStep(BaseStep): name = "bestof"
  #   épisodes analyzed, non community, facets_needed ⊆ facets_done (R24, tâche 12c), sans compilation bestof non rejetée
  # render/charter.py — Copy gagne : bestof: str   (lu par charter.texts[langue].bestof)
  # render/encode.py
  def render_plate_multi(items: Sequence[tuple[Path, Span]], layout: Layout, out: Path, *, s: RenderSettings,
                         transition_ms: int = 300, on_progress: Callable[[int], None] | None = None) -> None: ...
  #   un élément = (clean.mp4 source, portion) ; sources de tailles et cadences différentes admises (tâche 30)
  ```
- Algorithme :

```text
signaux externes : video_signals de la vidéo source (heatmap, comment_ts, sponsor_highlight, community), convertis
  par source_to_clean_ms ; hors de clean → ignorés
niveau 1 : courbe par cases de bin_ms = Σ poids × level1_weights[kind] des signaux de la case ; exploitable → pics
niveau 2 : par case, z-scores de : loudness court terme moyenne, nombre de changements de plan à ± 2 s, mouvement du
  plan, durée d'événements impact/laughter, mots finissant par « ! », phrases de moins de 6 mots ; courbe = Σ poids × z
niveau 3 : n_slices tranches égales ; dans chacune, la fenêtre [a, b] de points admissibles (coût fini), de
  longueur dans [min_item_ms, max_item_ms], qui minimise coût(a) + coût(b) ; score = 1 / (1 + coût(a) + coût(b))
moment → passage : widen = du plus grand point admissible ≤ t − pre_ms au plus petit ≥ t + post_ms ; longueur
  ramenée dans [min_item_ms, max_item_ms] en rapprochant la borne la plus éloignée de t ; aucun → moment écarté
dedupe (chevauchement > 50 % de la plus courte → meilleur score gardé)
famille = weighted_choice(derive_seed(episode_id, "bestof_family"), parts du propriétaire : dict[Family, float]
  = {f: config.family_share(f, plateforme)} ; la tâche 37 passe à resolve("families.share", compte), même type)
cible = draw_target_ms(derive_seed(episode_id, "bestof"), min_s, max_s) ;
n_slices = clamp(round(cible / 12 000), 4, 8)
pick_items : par score décroissant, ajouter tant que total + transitions ≤ max_ms et nombre ≤ max_items ;
  arrêt dès total ≥ cible ; total < min_ms avec 8 éléments → élargir chaque passage au point admissible suivant
  (ordre de score) ; toujours < min_ms ou moins de 4 → niveau suivant ; niveau 3 échoue → aucun best-of, warned
remise dans l'ordre chronologique
```

  - Écritures : `Compilation(kind="bestof", episode_id, account_id=propriétaire, status="proposed")`, un
    `Segment(kind="bestof_item", family, target_ms, seed, signal_level=niveau, score=score du moment)` par passage,
    `compilation_items` dans l'ordre. Épisode de moins de 65 s nettoyées → aucun best-of, `warned`.
  - Rendu d'un post de compilation : `render_plate_multi` découpe chaque passage dans sa source, met chaque élément
    à l'échelle de la mise en page (`video_rect(layout)`, même cadence 30 i/s, `setsar=1`) **avant** `xfade=fade` et
    `acrossfade` de `transition_ms`, écrit une plaque intermédiaire, puis loudnorm en deux passes sur elle (tâche
    7a). Bandeau = `charter.texts[langue].bestof.format(title=nom)` : en `"Best of {title}"`, fr
    `"Le meilleur de {title}"` ; aucun numéro (R9).
  - Pics R5 : `CutStep` passe `peaks_ms = heatmap_peaks_ms(…)` à `cut_point_costs` (remplace le `[]` de la tâche 6) ;
    `rule_violations` (v0) utilise la même fonction.
  - Approbation v0 (remplacée en tâche 15) : une compilation est `approved` si chaque passage passe R5 et R1 simulée
    et si la durée totale (transitions déduites) est dans les bornes de la famille (R6). Attribution : tous les
    passages par `registry.assign` dans une seule transaction ; un refus annule tout (`warned`) ; succès →
    `Post(compilation_id, part_number=None, status="planned")`. Un `bestof_item` n'est jamais attribué seul :
    `AssignStep` ne prend que les segments `part` (tâche 8a), l'`ApproveStep` v0 aussi (R2).
  - `attach_popularity` : `scenes.popularity` = poids heatmap maximal (temps clean) dans la scène, sinon NULL ;
    appelé par `BestofStep` avant le calcul.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_zscore_exploitable_peaks_dedupe():
def test_source_to_clean_mapping_with_trim_and_split():
def test_r43_no_signal_gives_level_3(ctx, synth):        # vidéo uniforme → signal_level 3, 4 à 8 passages, total 65–100 s
def test_r43_simulated_peaks_give_level_1(ctx, synth):   # video_signals heatmap à 3 pics → niveau 1, chaque pic couvert
def test_level2_from_beeps(ctx, synth):                   # bips sur 6 instants, sans signal externe → niveau 2
def test_r43_episode_always_gets_parts_and_bestof(ctx, synth):
def test_items_chronological_widened_and_deduped():
def test_pick_items_respects_family_bounds_with_transitions():
def test_bestof_rendered_conform_without_part_number(ctx, synth):   # check_delivery == [] ; bandeau "Best of …"
def test_render_plate_multi_two_sources_of_different_sizes(tmp_path, synth):
    # 1920×1080 25 i/s + 1280×720 30 i/s, un passage de 5 s chacun → check_delivery == [], durée 10 s − 0,3 s ± 100 ms
def test_r5_cut_reads_heatmap_peaks(ctx, synth):    # pic de heatmap à t = 20 000 ms → aucune frontière de partie
    # dans [18 500, 20 000[ ; rule_violations du même segment == []
def test_r2_bestof_items_never_assigned_alone(ctx): # compilation approved → aucune ligne assignments hors de son post
def test_r2_bestof_item_overlapping_part_same_account_assigned(ctx):
def test_compilation_assignment_all_or_nothing(ctx):
def test_r16_bestof_rerun_changes_nothing(ctx, synth):
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS ; retirer R5, R43 de
  `rules_pending.toml`. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(highlights): signal ladder best-of with crossfaded multi-span render (R43)`

---

### Task 14 : Accroches (règles, modèle local, filtres R11)

**Files :**
- Create : `config/hooks/rules.yaml`, `src/rushes/hooks/__init__.py`, `src/rushes/hooks/features.py`,
  `src/rushes/hooks/rules.py`, `src/rushes/hooks/llm.py`, `src/rushes/hooks/filters.py`, `src/rushes/hooks/rank.py`,
  `src/rushes/hooks/propose.py`, `src/rushes/models/llm.py`
- Modify : `src/rushes/models/hub.py` (`llm()` réel), `src/rushes/config.py` (`HookSettings`),
  `src/rushes/render/render.py` (`RenderJob.hook_text` = accroche choisie), `src/rushes/meta/meta.py`
  (`hook_applied`, `hook_alternatives`), `src/rushes/package/outbox.py` (`hook.txt`, `hook_2.txt`, `hook_3.txt`),
  `src/rushes/steps.py`, `src/rushes/cli.py` (`hooks propose`)
- Test : `tests/test_hook_rules.py`, `tests/test_hook_filters.py`, `tests/test_hooks.py`, `tests/test_llm.py`,
  `tests/test_cli.py` (`hooks propose` passe de `PENDING` à `IMPLEMENTED`)

**Interfaces :**
- Consomme : `TextGenerator` (§5.5), `ScenesFile`, `AudioFile`, `WordsFile`, tables `hooks`, `scenes`, `posts`.
- Produit :
  ```python
  HookType = Literal["curiosity", "conflict", "challenge", "prediction", "situation", "quote"]
  class HookSettings(_Model):
      llm_timeout_s: float = 30; temperature: float = 0.3; max_tokens: int = 200; n_ctx: int = 2048
      recent_window: int = 30; max_similarity: float = 0.85
  class Settings: ...  # + hooks: HookSettings = HookSettings()
  # hooks/features.py
  @dataclass(frozen=True)
  class HookFeatures:
      subject_kind: Literal["series", "creator"]; name: str; language: str; vertical: Vertical
      kind: Literal["part", "bestof", "compilation"]; characters: tuple[str, ...]; tags: tuple[str, ...]
      sounds: tuple[str, ...]; punchline_ms: int | None; punchline_tags: tuple[str, ...]
      quotes: tuple[str, ...]; family: str; recent: tuple[str, ...]
  def build_features(session: Session, paths: Paths, config: Config, *, segment_id: int | None,
                     compilation_id: int | None, recent_window: int) -> HookFeatures: ...
  # hooks/rules.py
  class Condition(_Model):
      tags_any: list[str] = []; sounds_any: list[str] = []; characters_min: int = 0
      kind: list[Literal["part", "bestof", "compilation"]] = []; has_quote: bool | None = None
      vertical: Vertical | None = None
  class Rule(_Model): id: str; type: HookType; when: Condition = Condition(); templates: dict[str, list[str]]
  class RuleSet(_Model):
      version: Literal[1]; banned: dict[str, list[str]]; spoilers: dict[str, dict[str, list[str]]]
      stopwords: dict[str, list[str]]; rules: list[Rule]
  @dataclass(frozen=True)
  class Candidate: text: str; type: HookType; engine: Literal["rules", "llm"]; rule_id: str | None
  def load_rules(config_dir: Path) -> RuleSet: ...
  def rule_candidates(rs: RuleSet, f: HookFeatures, *, seed: int) -> list[Candidate]: ...
  # hooks/llm.py
  class HookOut(BaseModel): text: str = Field(min_length=2, max_length=60); type: HookType
  class HookBatch(BaseModel): hooks: list[HookOut] = Field(min_length=3, max_length=6)
  def hook_prompt(f: HookFeatures, rules: Sequence[Candidate]) -> tuple[str, str]: ...   # (system, user)
  def llm_candidates(gen: TextGenerator, f: HookFeatures, rules: Sequence[Candidate], *, timeout_s: float) -> list[Candidate]: ...
  # hooks/filters.py
  def word_count(text: str) -> int: ...          # mots séparés par des espaces, emoji et ponctuation seuls ignorés
  def language_mismatch(text: str, language: str, rs: RuleSet) -> bool: ...
  def hook_problems(text: str, f: HookFeatures, rs: RuleSet, *, others: Sequence[str], max_similarity: float) -> list[str]: ...
  # hooks/rank.py
  class HookRanker(Protocol):
      def order(self, account_id: str, cands: Sequence[Candidate]) -> list[Candidate]: ...
  class PriorRanker:   # llm puis règles, dans l'ordre d'arrivée ; remplacé par le bandit (tâche 18)
  def choose_three(ordered: Sequence[Candidate]) -> list[Candidate]: ...   # 3 types distincts, ordre conservé
  # hooks/propose.py
  class HooksStep(BaseStep): name = "hooks"; heavy = True   # parts proposed/approved et compilations sans accroche
  # models/llm.py
  class LlamaTextGenerator:   # TextGenerator
      def __init__(self, model_path: Path, *, threads: int, n_ctx: int, temperature: float, max_tokens: int,
                   no_think: bool) -> None: ...
  ```
- Règles fixées :
  - `config/hooks/rules.yaml` (rédigé ici, relu par Louis avant le lancement, L36) : règles génériques **toujours vraies**, une par type sauf `quote`, en `en` et `fr`,
    chacune avec **au moins 8 gabarits par langue**, variés par `{name}` et `{a}` (sinon les 30 dernières accroches du
    compte les filtrent tous dès quelques posts, R11). Premiers gabarits génériques : en — curiosity « Wait for the
    ending 😭 », challenge « Try not to laugh 😂 », prediction « Guess what happens next 👀 », situation « {name} at
    its best », conflict « Who wins this one? » ; fr — « Attends la fin 😭 », « Essaie de ne pas rire 😂 », « Devine
    la suite 👀 », « {name} au sommet », « Qui gagne celle-là ? ». Règles spécifiques (avant les génériques) : `chase` →
    conflict « {a} vs {b}: who wins? » / « {a} contre {b} : qui gagne ? » (si 2 personnages) ; `trap` → prediction
    « Will {a} escape this? » / « {a} va-t-il s'en sortir ? » ; quote → « "{quote}" » si `has_quote`. Variables :
    `{name}`, `{a}`, `{b}` (identifiants de personnages capitalisés), `{quote}` ; un gabarit dont une variable manque
    est sauté ; une règle spécifique donne un seul gabarit tiré à graine `derive_seed(cible, rule_id)` ; une règle
    générique donne tous ses gabarits, dans un ordre mélangé à la même graine (le filtre garde le premier conforme).
    `banned` : en `["viral", "foryou", "fyp", "subscribe", "link in bio"]`, fr `["viral", "abonne-toi", "lien en bio"]`.
    `spoilers` : par langue et par étiquette (`fall`, `explosion`, `trap`, `hit`, `impact`, `splash`), les mots qui
    décrivent l'issue (ex. en `fall: [fall, falls, fell, falling]`, fr `fall: [tombe, tombé, chute]`).
    `stopwords` : 15 mots courants par langue.
  - `hook_problems` (R11, R21) : `word_count ≥ 8` ; mot interdit ; mot de `spoilers` d'une étiquette de
    `punchline_tags` (étiquettes et sons de la scène de la chute) ; `language_mismatch` (au moins 2 mots outils de
    l'autre langue et aucun de la sienne) ; similarité `rapidfuzz.fuzz.ratio(norm(a), norm(b))/100 ≥ max_similarity`
    avec une accroche de `others` (les `recent_window` dernières accroches choisies du compte, plus les propositions
    déjà retenues).
  - Chute : `punchline_ms` = instant du plus fort pic de loudness ou du plus fort événement `impact`/`explosion` dans
    le dernier tiers du passage ; `punchline_tags` = étiquettes et sons de la scène qui le contient.
  - Prompt (système, langue de la marque) : en « You write short hooks for vertical cartoon clips. Reply in English
    only. Each hook has fewer than 8 words, never tells how the scene ends, contains no hashtag. Answer with JSON
    only. » ; fr équivalent en français. Si `no_think` : « /no_think » ajouté. Utilisateur : JSON de `name`, `kind`,
    `characters`, `tags`, `sounds`, `quotes`, `family`, `allowed_types`, `avoid` (10 accroches récentes), `examples`
    (propositions des règles).
  - `LlamaTextGenerator.generate` : `create_chat_completion(messages, response_format={"type": "json_object",
    "schema": schema.model_json_schema()}, temperature, max_tokens, stream=True)` ; chaînes accumulées, abandon et
    `None` si `timeout_s` est dépassé ; validation `schema.model_validate_json` ; invalide → `None`. Le hub essaie
    `settings.llm.model`, puis `settings.llm.fallback` (avec `no_think=True`) si le premier est absent ou lève
    `MemoryError`/`ValueError` au chargement ; `llm.enabled: false` → `None`.
  - Étape : candidats = règles + modèle ; filtrés par `hook_problems` ; `PriorRanker` ; `choose_three`. Jamais de
    candidat non filtré (R11) : s'il reste moins de 3 types conformes, les accroches conformes sont gardées et le
    passage (ou la compilation) passe en `needs_review`, raison « moins de 3 accroches conformes (R11) ». Lignes
    `hooks` (`rank` 1–3, `chosen` sur le rang 1, `engine`, `type`). Une exception du modèle → avertissement unique
    par run, règles seules.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_r11_hook_under_8_words():               # "One two three four five six seven eight" refusé ; 7 mots + emoji accepté
def test_r11_hook_similarity_below_085():
def test_r11_three_distinct_types(ctx):          # pour une partie : 3 lignes hooks, types distincts, une seule chosen
def test_r11_hook_never_reveals_punchline():     # punchline_tags ("fall",) : "Tom falls again" refusé
def test_r21_texts_in_brand_language(ctx):       # marque fr : gabarits fr ; prompt en français ; candidat anglais refusé
def test_rules_yaml_loads_and_conditions_match():
def test_generic_rules_guarantee_three_types_each_language():   # ≥ 8 gabarits par type générique et par langue
def test_r11_never_unfiltered_fill(ctx):
    # 30 accroches récentes = tous les gabarits d'un type → aucune ligne hooks non conforme ; < 3 types conformes →
    # segment needs_review, raison "moins de 3 accroches conformes (R11)" ; 40 posts sans LLM → aucune violation R11
def test_hooks_fall_back_to_rules_on_timeout_and_garbage(ctx):   # None, exception, lenteur → engine "rules" ; étape done
def test_llm_disabled_rules_only(ctx, edit_yaml):
def test_chosen_hook_rendered_and_alternatives_in_meta(ctx, synth):
def test_r16_hooks_rerun_changes_nothing(ctx):
@pytest.mark.slow
def test_llm_json_valid_100_runs(tmp_path):      # vrai Qwen3-4B : 100 caractéristiques variées → 100 HookBatch valides
@pytest.mark.slow
def test_llm_fallback_model_used_when_main_missing(tmp_path):
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS ; retirer R11 de
  `rules_pending.toml` (R21 reste en attente pour la tâche 22). Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Mesurer sur le serveur** — `pytest -m slow tests/test_llm.py` dans l'image ; noter dans
  `docs/decisions.md` le temps moyen d'un appel (3 accroches) et le pic mémoire ; au-delà de 60 s par appel,
  passer `settings.llm.model` au modèle 1,7B et le consigner.

- [ ] **Step 6 : Commit proposé** — `feat(hooks): rule engine, local llm with schema-constrained json and R11 filters`

---

### Task 15 : Approbation automatique et re-sourçage communautaire

**Files :**
- Create : `src/rushes/approve/score.py`, `src/rushes/approve/rules.py`, `src/rushes/approve/resource.py`
- Rewrite : `src/rushes/approve/approve.py` (remplace la v0 des tâches 8a et 13)
- Modify : `src/rushes/config.py` (`ApprovalSettings`), `src/rushes/schemas.py` (`ResourceFile`), `src/rushes/steps.py`,
  `src/rushes/cut/bestof.py` (`withdraw_bestof`, nouveau calcul après un signal communautaire),
  `src/rushes/cli.py` (`approve --dry-run`)
- Test : `tests/test_approve.py`, `tests/test_resource.py`, `tests/test_cli.py` (`approve --dry-run`)

**Interfaces :**
- Consomme : `can_assign`, `rule_violations` (tâche 8a, absorbé ici), `derived.resolve`, `phash_distance`,
  `sample_gray`, `ScenesFile`, `AudioFile`, `WordsFile`, `video_signals`.
- Produit :
  ```python
  class ApprovalSettings(_Model):
      weights: dict[str, float] = {"bounds": 0.3, "action": 0.25, "audio": 0.2, "start": 0.1, "signal": 0.15}
      motion_ref: float = 0.15; resource_fps: float = 2.0; resource_window_s: float = 6.0
      resource_step_s: float = 3.0; resource_max_distance: int = 10
  class Settings: ...  # + approval: ApprovalSettings = ApprovalSettings() ; approval_threshold : Threshold | None (§5.9)
  # approve/score.py
  def passage_quality(spans: Sequence[Span], *, bounds_score: float, scenes: ScenesFile, audio: AudioFile,
                      level: int | None, s: ApprovalSettings) -> float: ...   # 0–1
  # approve/rules.py
  @dataclass(frozen=True)
  class Decision: status: Literal["approved", "needs_review", "rejected"]; score: float; reasons: tuple[str, ...]
  def segment_checks(session: Session, ctx: Context, segment_id: int) -> tuple[list[str], list[str]]: ...
  #   (violations → needs_review : R1 simulée, R5, R6, R24 ; blocages → rejected : R34, R15)
  def compilation_checks(session: Session, ctx: Context, compilation_id: int) -> tuple[list[str], list[str]]: ...
  def decide(*, score: float, threshold: float, violations: Sequence[str], blocked: Sequence[str]) -> Decision: ...
  def approval_threshold(session: Session, config: Config, vertical: Vertical) -> Resolved[float]: ...
  # approve/approve.py
  class ApproveStep(BaseStep): name = "approve"   # segments part 'proposed' et compilations 'proposed'
  #   rushes approve --dry-run : affiche score, seuil, raisons et décision de chaque élément, n'écrit rien
  # approve/resource.py
  def phash_track(video: Path, *, fps: float) -> list[str]: ...   # en cache dans episodes/<e_key>/phash_<fps>.json
  def locate(needle: Sequence[str], hay: Sequence[str], *, max_distance: int) -> tuple[int, float] | None: ...
  class ResourceFile(BaseModel):   # schemas.py
      version: Literal[1]; episode_id: int; matches: list[tuple[int, int, int, float]]   # (t_comm_ms, ep_officiel, t_off_ms, distance)
      compared: list[int]   # épisodes officiels déjà comparés
  class ResourceStep(BaseStep): name = "resource"; heavy = True
  #   épisodes community analyzed sans resource.json, ou dont resource.json.compared n'a pas tous les épisodes
  #   officiels analyzed de la série
  # cut/bestof.py
  def withdraw_bestof(session: Session, paths: Paths, compilation_id: int, *, reason: str, at: datetime) -> bool: ...
  #   False si un post de la compilation est ready ou published (gardé) ; sinon compilation → rejected, posts
  #   planned|rendered → expired (rendus supprimés, attributions libérées, jamais compté au bandit)
  ```
- Règles fixées :
  - Composantes (0–1) : `bounds` = score de bornes du découpage (moyenne pour une compilation) ; `action` =
    `min(1, mouvement moyen des plans couverts / motion_ref)` ; `audio` = part des cases de 500 ms au-dessus de la
    loudness médiane de l'épisode + 0,1 par événement sonore, plafonnée à 1 ; `start` = `min(1, mouvement des 2
    premières secondes / motion_ref)` ; `signal` = 0,7 pour une partie, `{1: 1.0, 2: 0.7, 3: 0.4}[niveau]` pour un
    best-of. Score = Σ poids × composante / Σ poids.
  - Seuil : `resolve("approval.threshold", vertical, config_value=settings.approval_threshold[vertical] si écrit,
    default=0.7)`.
  - `decide` : blocage → `rejected` (raisons « logo incrusté (R34) », « passage communautaire (R15) ») ; violation →
    `needs_review` ; score < seuil → `needs_review` (« score 0,62 < seuil 0,70 ») ; sinon `approved` (R33). Une série
    non `licensed` ou un créateur non `authorized` n'est jamais une raison (R14, R22). R24 pour un créateur (borne =
    fin d'une phrase de `sentences`) ; sans facette `words` → violation « R24 non vérifiable » → `needs_review`.
    R5 lit les pics de `heatmap_peaks_ms` (tâche 13).
  - `--force` : un segment ou une compilation déjà attribué(e) est `skipped` (« déjà attribué ») ; le calibrage
    passe par `--dry-run`.
  - Compilation : `approved` seulement si chaque passage passe ses contrôles, si la durée totale respecte R6, et si
    le score moyen ≥ seuil ; sinon `needs_review` ou `rejected` (statuts de `compilations.status`, migration 0004) ;
    chaque passage prend le statut de la compilation (un `bestof_item` `approved` n'est attribué que par sa
    compilation, R2).
  - Re-sourçage (R15) : aiguilles = fenêtres de `resource_window_s` tous les `resource_step_s` de l'épisode
    communautaire (`phash_track` à `resource_fps`) ; meule = chaque épisode officiel analysé de la même série ;
    `locate` glisse l'aiguille et renvoie le décalage de distance de Hamming moyenne minimale si
    ≤ `resource_max_distance`. Trouvé → `video_signals(kind="community", source_video_id=vidéo officielle,
    t_ms=temps source du milieu de la fenêtre, weight=log10(1 + vues de la vidéo communautaire)/6)` : le passage est
    recréé comme moment de niveau 1 du best-of officiel. Introuvable → rien de rendu ; le signal reste attaché à la
    vidéo communautaire. Fenêtres consécutives trouvées fusionnées. Précision : un pas de `1/resource_fps` s.
    `resource.json.compared` garde les épisodes officiels comparés : un épisode officiel téléchargé plus tard
    (cas courant, `select_downloads` prend les communautaires après) relance la comparaison sur lui seul.
  - Nouveau signal `community` sur une vidéo officielle dont un épisode (portion qui contient `t_ms`) a déjà un
    best-of : `withdraw_bestof(reason="nouveau signal communautaire")` ; si elle renvoie vrai, `BestofStep`
    (sélection : sans compilation `bestof` non rejetée) le recalcule au même run ; si le best-of est `ready` ou
    `published`, il est gardé (Louis l'a peut-être programmé, P9) et le signal attend.
  - Calibrage : sur les épisodes réels de la tâche 9, la part des parties approuvées doit être entre 50 et 90 % ;
    sinon ajuster les poids par défaut (jamais une règle) et consigner dans `docs/decisions.md`.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_passage_quality_reference_values():
def test_r33_auto_approval_needs_threshold_and_rules(ctx):    # au-dessus sans violation → approved ; dessous → needs_review ; R5 → needs_review
def test_r34_logo_detected_blocks_segment(ctx):               # episodes.logo_corner → rejected, jamais rendu
def test_r14_unlicensed_series_still_approved(ctx):
def test_threshold_resolution_config_over_derived_over_default(ctx, edit_yaml):
def test_compilation_approved_only_if_all_items_ok(ctx):
def test_r15_community_segment_resourced_to_official(ctx, synth):
    # épisode officiel synthétique (plans étiquetés + testsrc2) ; vidéo communautaire = extrait 30–45 s réencodé
    # en 854×480 → signal "community" sur la vidéo officielle à 30 000–45 000 ± 500 ms
def test_r15_unmatched_stays_signal(ctx, synth):              # vidéo sans rapport → aucun signal sur l'officielle, aucun post
def test_r15_official_downloaded_after_community_compared(ctx, synth):
    # run 1 : communautaire seule → resource.json.compared == [] ; run 2 : officielle analysée → signal posé, compared == [id]
def test_r15_new_community_signal_recomputes_unready_bestof(ctx, synth):
    # best-of planned → rejected, post expired, nouveau best-of qui couvre le signal ; best-of ready → gardé
def test_r24_creator_without_words_needs_review(ctx):       # créateur sans words.json → needs_review, "R24 non vérifiable"
def test_force_never_touches_assigned_segment(ctx):         # approve --force : segment attribué → skipped "déjà attribué", statut inchangé
def test_approve_dry_run_writes_nothing(ctx):               # scores affichés ; segments et compilations inchangés
def test_r16_approve_rerun_changes_nothing(ctx):
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS ; retirer R15, R33, R34 de
  `rules_pending.toml`. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Calibrer sur le serveur** (règle ci-dessus) avec `rushes --json approve --dry-run --series tom_and_jerry`
  (aucune écriture ; `--force` ne retouche jamais un segment attribué).

- [ ] **Step 6 : Commit proposé** — `feat(approve): scored auto-approval with rule checks and community re-sourcing (R15, R33, R34)`

---

### Task 16a : Rotation générée et propriété

**Files :**
- Create : `src/rushes/assign/rotation.py`
- Modify : `src/rushes/ingest/owner.py`, `src/rushes/ingest/select.py` (marques candidates par `current_rotation`),
  `src/rushes/config.py` (`RotationSettings`), `src/rushes/status.py` (tableau « Rotation »)
- Test : `tests/test_rotation.py`, `tests/test_owner.py`

**Interfaces :**
- Consomme : `derived.record/resolve`, `RotationPeriod` (config), `choose_owner` (tâche 4).
- Produit :
  ```python
  class RotationSettings(_Model): period_days: int = Field(21, ge=7); epoch: date | None = None   # forçage ; sinon `rotation.epoch`
  class Settings: ...  # + rotation: RotationSettings = RotationSettings()
  # assign/rotation.py
  @dataclass(frozen=True)
  class RotationPlan:
      start: date; end: date; by_brand: Mapping[str, tuple[str, ...]]; origin: Literal["config", "derived"]
  def generate_rotation(brands: Sequence[str], series: Sequence[str], *, today: date, period_days: int,
                        epoch: date) -> RotationPlan: ...
  def monday_on_or_before(d: date) -> date: ...
  def rotation_epoch(session: Session, s: RotationSettings, *, today: date) -> date: ...
  def current_rotation(session: Session, config: Config, s: RotationSettings, *, today: date) -> RotationPlan: ...
  ```
- Règles fixées :
  - Ancre (décision de Louis, L6) : `s.epoch` s'il est écrit ; sinon la valeur déduite `rotation.epoch` (portée
    `cartoons`). Absente : `rotation_epoch` enregistre `monday_on_or_before(today)` (`source="provisional"`), ancre
    provisoire de la répétition générale. `rushes launch start` (tâche 41) la remplace une fois par le lundi du
    lancement (lundi suivant, ou le jour même si c'est un lundi ; `source="launch"`). Le recalage ne touche que les
    épisodes ingérés ensuite : le stock déjà produit garde son propriétaire (R4, R44).
  - `generate_rotation` : marques cartoons et séries actives (`status='active'`, origine `config`) triées par
    identifiant ; `i = (today − epoch).days // period_days` (division par défaut : entre le lancement et son lundi, `i = −1`) ; période `[epoch + i × period, epoch + (i+1) × period − 1 jour]`.
    Si `|S| ≥ |B|` : la série `j` va à la marque `(j + i) mod |B|`. Sinon : la marque `b` reçoit la série
    `(b + i) mod |S|` (une série peut alors aller à plusieurs marques ; chaque épisode n'a qu'un propriétaire).
  - `current_rotation` : une période de `accounts.yaml` couvrant `today` l'emporte (`origin="config"`) ; sinon le plan
    généré, enregistré par `record("rotation.plan", "cartoons", …, source="generated", confidence=1.0)` quand il
    change de période (historique dans `derived_values_history`).
  - `choose_owner` (tâche 4) : marques candidates d'une série = celles du plan courant ; série absente du plan
    (candidate de la prospection, tâche 38) → toutes les marques cartoons ; puis le compte au plus petit
    `stock_days`, comme en tâche 10b. `select_downloads` (10b) prend les mêmes marques candidates, par
    `current_rotation`. Un épisode déjà ingéré garde son propriétaire (R4, R44).

- [ ] **Step 1 : Tests qui échouent**

```python
def test_rotation_generated_swaps_every_21_days():
    # marques saturday_toons, toon_tape ; séries oggy, tom_and_jerry (tri par identifiant) :
    # période 0 = {saturday_toons: (oggy,), toon_tape: (tom_and_jerry,)} ; période 1 = l'inverse
    # (l'exemple de la spec, à une période près)
def test_rotation_reproducible():
def test_rotation_epoch_provisional_is_monday_of_first_run(ctx):   # FakeClock jeudi 2026-11-19 → rotation.epoch 2026-11-16, source "provisional"
def test_config_epoch_overrides_derived_epoch(ctx, edit_yaml):
@pytest.mark.parametrize(("n_brands", "n_series"), [(2, 1), (2, 3), (3, 7)])
def test_rotation_every_brand_gets_a_series_and_every_series_a_brand(n_brands, n_series):
def test_config_rotation_overrides_generated(ctx, edit_yaml):
def test_r4_generated_rotation_drives_new_episodes(ctx, synth):
def test_r4_existing_episode_keeps_owner_after_rotation(ctx, synth):   # FakeClock + 21 jours : ancien épisode inchangé
def test_rotation_plan_recorded_once_per_period(ctx):
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS ; retirer R4 de
  `rules_pending.toml`. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(assign): generated cartoon rotation with config override and rotation-driven ownership (R4)`

---

### Task 16b : File de publication, rendu à la demande, renumérotation, expiration, gestes de secours

**Files :**
- Create : `src/rushes/queue/rank.py`, `src/rushes/queue/schedule.py`, `src/rushes/queue/expire.py`,
  `src/rushes/queue/actions.py`, `src/rushes/queue/manual.py`, `src/rushes/queue/step.py`,
  `src/rushes/db/migrations/versions/0007_queue.py`
- Modify : `src/rushes/render/render.py` (sélection par la fenêtre), `src/rushes/queue/numbering.py`
  (`stale_numbers` ; sans import de `rushes.render`, `serve` le lit en tâche 36), `src/rushes/queue/slots.py`
  (`express_slot`), `src/rushes/package/outbox.py` (`ready_at`, `planned_at` de la file),
  `src/rushes/assign/assign.py` (partie orpheline), `src/rushes/db/models.py`, `src/rushes/config.py`
  (`QueueSettings`), `src/rushes/status.py` (jours de stock), `src/rushes/steps.py`, `src/rushes/cli.py`
  (`queue rank`)
- Test : `tests/test_queue.py`, `tests/test_expire.py`, `tests/test_actions.py`, `tests/test_renumber.py`,
  `tests/test_slots.py`, `tests/test_cli.py` (`queue rank` passe de `PENDING` à `IMPLEMENTED`)

**Interfaces :**
- Consomme : `plan_slots` (`queue/slots.py`, tâche 8b, gardé comme calcul des créneaux), `Config.slots_for`,
  `passage_quality`, `part_number` (`queue/numbering.py`, tâche 7b), `stock_days`, `derived.resolve`, `post_key`.
- Produit :
  ```python
  class QueueSettings(_Model):
      weights: dict[str, float] = {"quality": 0.35, "freshness": 0.25, "trend": 0.15, "learned": 0.25}
      diversity_series: float = 0.10; diversity_family: float = 0.05; window_days: int = 7; margin: float = 0.5
      expiry_days: int = 7; expired_penalty: float = 0.10; freshness_half_h: float = 48.0; level3_factor: float = 0.8
  class Settings: ...  # + queue: QueueSettings = QueueSettings()
  # queue/rank.py
  @dataclass(frozen=True)
  class QueueItem:
      post_id: int; account_id: str; subject: str; family: str; kind: Literal["part", "bestof", "compilation"]
      episode_id: int | None; start_ms: int | None; quality: float; freshness: float; trend: float
      expired_count: int
  class LearnedPerformance(Protocol):
      def score(self, account_id: str, item: QueueItem) -> float: ...   # 0–1
  class NeutralPerformance:   # renvoie 0,5 ; remplacé par OutcomePerformance (tâche 18)
  def build_items(session: Session, ctx: Context, account_id: str, *, now: datetime) -> list[QueueItem]: ...
  def base_score(item: QueueItem, learned: float, s: QueueSettings) -> float: ...
  # queue/schedule.py
  def sequence(items: Sequence[QueueItem], scores: Mapping[int, float], *, slots: Sequence[datetime],
               slot_series: Sequence[str | None], placed: Mapping[int, datetime],
               s: QueueSettings) -> list[tuple[datetime, int]]: ...
  # queue/slots.py — créneau en plus de la voie express (L27, consommé par la tâche 28)
  def express_slot(*, slots: Sequence[str], tz: ZoneInfo, now: datetime, taken: Sequence[datetime],
                   extra_taken: Sequence[datetime], extra_per_day: int = 1) -> datetime | None: ...   # UTC
  # queue/expire.py
  def expire_posts(session: Session, paths: Paths, *, now: datetime, days: int) -> list[int]: ...       # R35
  # queue/actions.py   (réutilisées par rushes serve, tâche 36)
  def discard_post(session: Session, paths: Paths, post_id: int, *, reason: str, at: datetime,
                   origin: Literal["page", "folder", "system"] = "page") -> None: ...
  #   R37 ; seul endroit qui journalise un écart : appelle record_action(kind="discard", origin=origin) dès la tâche 36
  def choose_hook(session: Session, post_id: int, rank: Literal[1, 2, 3]) -> None: ...
  # queue/manual.py
  def detect_manual_actions(session: Session, paths: Paths, *, at: datetime) -> list[str]: ...
  # queue/numbering.py (créé en tâche 7b avec part_number)
  def stale_numbers(session: Session) -> list[int]: ...   # posts rendus non publiés dont part_number ≠ part_number()
  # queue/step.py
  class QueueStep(BaseStep): name = "queue"   # éléments : comptes ; expire, gestes de secours, puis classement
  ```
- Règles fixées :
  - Score (spec, File de publication) : `0,35 × qualité + 0,25 × fraîcheur + 0,15 × tendance + 0,25 × appris`.
    Qualité = `passage_quality` (best-of de niveau 3 × `level3_factor`) − `expired_penalty × expired_count`.
    Fraîcheur : créateurs `exp(−âge_source_h / freshness_half_h)`, cartoons 1. Tendance = `min(1,
    log10(1 + vues/jour de la vidéo source) / 6)`, 0 pour un fichier local. Appris = `LearnedPerformance`.
  - Créneaux : `plan_slots(slots résolus, posts_per_day, fuseau de la marque, start=now, taken=planned_at des posts
    ready, n=ceil(window_days × ppd × (1 + margin)))` (jours locaux absolus, quota par jour : tâche 8b). Créneaux
    résolus = `resolve("slots", compte, config_value=accounts.slots[plateforme] si écrit, sinon None,
    default=défaut par fuseau de la marque)`, soit la règle de `Config.slots_for(compte)` (tâche 33) quand rien
    n'est déduit.
  - Série fixe par créneau : séries (ou créateurs) ayant du stock sur le compte, triées par identifiant ; créneau
    absolu `g = floor(d × ppd) + j` (`d` = jour local `toordinal()`, `j` = rang du créneau dans son jour, comme
    `plan_slots`) ; `slot_series[k] = séries[g_k % len(séries)]`. Jamais indexé sur le rang relatif à `now` : un même
    créneau garde sa série d'un run à l'autre.
  - `sequence` (glouton, créneau par créneau) : candidats admissibles = posts `planned|rendered` non placés, hors
    créneau express (`express_slot = 1`), dont, pour une partie, la partie précédente du même épisode est publiée,
    `ready` ou déjà placée plus tôt (créneau express compris) ; préférer les candidats de la série du créneau s'il y
    en a ; un post `is_test` ne prend un créneau que si aucun candidat publiable n'est admissible (avant le
    lancement, tout est vitrine : rien ne change) ; score = `base_score` − `diversity_series` si même sujet que le
    créneau précédent − `diversity_family` si même famille + `family_deficit_bonus` (0,15) si la famille du
    candidat est sous sa part (`{f: config.family_share(f, plateforme)}` comme le best-of de la tâche 13 ; la tâche 37
    passe à `resolve("families.share", …)`, `dict[Family, float]` par compte) sur les posts
    placés ou publiés des 14 derniers jours du compte (spec : « part des familles respectée ») ; meilleur score,
    égalité → plus petit `post_id`.
    `placed` = posts `ready`, posts remis en `rendered` par un motif explicite (accroche, numéro : `ready_at` non
    nul) et posts à créneau express : ils gardent leur `planned_at`.
  - Écritures : posts placés → `planned_at` (UTC), `rank_score`, `order_after_post_id` (partie précédente) ; non
    placés → `planned_at = NULL`. Les posts `ready` gardent leur créneau (Louis les a peut-être déjà programmés).
    Le rendu (et `reframe`, `commentary`, `voice`) ne prend que les posts `planned` avec `planned_at` non nul, plus
    `stale_numbers()` et les versions de rendu périmées des posts `planned|rendered` (jamais un `ready` pour une
    simple montée de `RENDER_*_VERSION`, tâche 7a).
  - Créneau express (décision L27) : une vidéo de la voie express (tâche 28 : best-of, parties 1 et 2) peut recevoir
    un créneau **en plus** de `posts_per_day`, au plus `extra_per_day` (1) post de plus par jour local et par compte,
    le jour même ou le lendemain. `express_slot` : pour le jour local de `now` puis le lendemain, si ce jour a moins
    de `extra_per_day` posts dans `extra_taken`, la première heure de `slots` postérieure à `now` dont l'instant
    n'est pas dans `taken` ; toutes prises → la dernière heure de `slots` + 1 h si elle est postérieure à `now` ;
    sinon le lendemain ; aucun des deux jours → `None`. L'appelant écrit `planned_at` et `posts.express_slot = 1`.
    Ces posts ne comptent ni dans le quota de `plan_slots` (`taken`) ni comme candidats de `sequence` ; les posts
    `ready` ne bougent jamais.
  - Renumérotation (R9) : `part_number()` exclut les posts `discarded` et `expired` ; un post rendu dont le numéro
    change repasse en `rendered` avec `caption = NULL` (rendu léger, puis `meta` et `package` refaits). `stale_numbers`
    vit dans `queue/numbering.py` : `serve` (tâche 36) le lit pour `PostView.pending_render`.
  - Expiration (R35) : post `ready` dont `ready_at` < now − `expiry_days` → `expired` ; fichiers de rendu
    (`renders/<p_key>.*`) et dossier `outbox*/…/<p_key>/` supprimés ; lignes `assignments` de ses passages supprimées.
    `assign` ne recrée pas de post pour une partie dont une partie suivante du même épisode est déjà `published`.
  - Écarter (R37) : post → `discarded`, attribution supprimée, segment → `rejected` (message « écarté par Louis »)
    **seulement si `origin != "system"`** (un doublon retiré par le système, tâche 17, laisse son passage `approved`,
    R3), fichiers de rendu supprimés, dossier `outbox/` déplacé dans `outbox/_ecartes/<p_key>/` s'il n'y est pas déjà.
    Le segment n'appartient qu'à l'épisode du compte propriétaire (R44) : il n'est donc plus jamais proposé à ce
    compte. Journal : `discard_post` écrit lui-même la ligne `post_actions` (tâche 36) ; ni l'API ni
    `detect_manual_actions` ne journalisent un écart. L'échec du bandit est compté en tâche 18, jamais pour un écart
    d'origine `system` ni pour un post `is_test`. Écarter une partie rend les suivantes `stale_numbers()` (R9).
  - `choose_hook` : `chosen` déplacé sur le rang demandé ; `render_version = NULL`, `caption = NULL` et
    `status = 'rendered'` (comme la renumérotation : rendu léger, puis `meta` refait la légende, qui commence par la
    nouvelle accroche, et la couverture, puis `package`). Le post garde son `planned_at`.
  - Gestes de secours : dossier présent dans `outbox/_ecartes/` → `discard_post(reason="dossier déplacé",
    origin="folder")`, sauf si le post est déjà `discarded` (dossier déplacé par la page : rien) ;
    `hook.txt` dont le contenu est égal à celui d'une autre accroche du post → `choose_hook` du rang correspondant.
  - Migration `0007_queue.py` : `posts` + `order_after_post_id INTEGER REFERENCES posts(id)`, + `ready_at TEXT`,
    + `express_slot INTEGER NOT NULL DEFAULT 0` (créneau en plus, L27).
    `package` écrit `ready_at` ; `upload.json.order_note` utilise `order_after_post_id`.
  - `status` : tableau « File par compte » avec jours de stock (`stock_days`) ; 0 jour → ligne dans « Alertes ».

- [ ] **Step 1 : Tests qui échouent**

```python
def test_base_score_reference():
def test_parts_never_ranked_before_previous_part(ctx):
def test_series_fixed_per_slot(ctx):
def test_slot_series_stable_across_runs_24h_apart(ctx):  # deux runs à 24 h : un même créneau garde sa série
def test_only_window_plus_margin_rendered(ctx):          # ppd 1 → 11 posts placés ; render.select == ces 11
def test_ready_posts_keep_their_slot(ctx):
def test_test_post_only_where_no_publishable_candidate(ctx):   # 1 post publiable + 1 is_test admissibles → publiable au 1er créneau
def test_ranking_reproducible(ctx):
def test_express_slot_one_extra_per_day():
    # Europe/Paris, slots ["18:00","20:00"], now 2026-11-16T16:00Z, taken [2026-11-16T17:00Z]
    # → 2026-11-16T19:00Z ; extra_taken = [ce créneau] → 2026-11-17T17:00Z ; extra_taken sur les deux jours → None
def test_express_post_outside_quota_and_ready_unchanged(ctx):
    # post express_slot=1 : planned_at inchangé après queue ; planned_at des posts ready identiques ; quota de
    # plan_slots du jour inchangé (ppd 1 → toujours 1 créneau normal ce jour-là)
def test_r35_ready_post_expires_after_7_days(ctx):       # horloge + 7 j + 1 s → expired, fichiers supprimés, attribution libérée
def test_r37_discard_releases_segment(ctx):
def test_r37_discarded_not_reproposed_to_account(ctx):
def test_system_discard_keeps_segment_approved(ctx):     # origin "system" → post discarded, segment toujours approved
def test_r9_renumber_without_gap(ctx, synth):            # parties 1, 2, 3 rendues ; 2 écartée → 3 rendue « Part 2 », légende refaite
def test_r9_stale_numbers_after_discard(ctx):            # parties 1, 2, 3 ready ; discard_post(2) → stale_numbers() == [id partie 3]
def test_manual_gesture_folder_moved_to_ecartes(ctx):    # post déjà discarded (page) → aucun nouvel écart
def test_manual_gesture_hook_renamed(ctx):
    # hook.txt = accroche de rang 2 → status rendered puis, après render/meta/package : caption.txt commence par
    # cette accroche, cover.jpg refaite (empreinte différente), planned_at inchangé
def test_queue_empty_stock_warns(ctx):
def test_r16_queue_rerun_changes_nothing(ctx):
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter** (`queue` après `assign` dans `RUN_ORDER` ; `plan_slots`
  n'est plus appelé par `package`). **Step 4 : Lancer** → PASS ; retirer R9, R35 de `rules_pending.toml` (R37 reste en
  attente pour les tâches 18 et 36). Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(queue): ranked per-account schedule, on-demand rendering, renumbering, expiry and discard (R9, R35, R37)`

---

### Task 34 : Recadrage intelligent et mises en page `square`, `vertical`, `portrait`

**Files :**
- Create : `src/rushes/reframe/__init__.py`, `src/rushes/reframe/importance.py`, `src/rushes/reframe/path.py`,
  `src/rushes/reframe/choose.py`, `src/rushes/reframe/step.py`, `src/rushes/models/yunet.py`,
  `src/rushes/render/cropcmd.py`
- Modify : `src/rushes/render/canvas.py` (`plate_filter(..., crop_cmd: bool)` ; `video_rect("full")` déjà correct pour une
  source verticale depuis la tâche 7a), `src/rushes/render/encode.py` (`render_plate(..., reframe: ReframeFile | None = None)` ;
  `render_plate_multi(..., reframes: Sequence[ReframeFile | None] | None = None)`, un par élément, dans l'ordre de
  `items`), `src/rushes/media/ffmpeg.py`
  (`run_ffmpeg(..., cwd: Path | None = None)`), `src/rushes/render/render.py`, `src/rushes/models/hub.py`
  (`faces()` réel), `src/rushes/schemas.py` (`ReframeFile`, §5.6), `src/rushes/config.py` (`ReframeSettings`),
  `src/rushes/steps.py`
- Test : `tests/fixtures/generator.py` (`static_targets`), `tests/test_reframe.py`, `tests/test_reframe_render.py`

**Interfaces :**
- Consomme : `sample_gray`, `ScenesFile`, `FaceDetector`, `ImageEncoder`, `video_rect`, `Layout`, `weighted_choice`,
  `derive_seed`, `derived.resolve`.
- Produit :
  ```python
  class ReframeSettings(_Model):
      fps: float = 5.0; width: int = 320; w_motion: float = 0.6; w_saliency: float = 0.3; w_faces: float = 0.1
      static_coverage: float = 0.80; frame_min_coverage: float = 0.60; max_bad_share: float = 0.30
      dead_zone: float = 0.08; max_speed: float = 0.15; tie_margin: float = 0.05
      l1_weights: tuple[float, float, float] = (1.0, 10.0, 100.0)
  class Settings: ...  # + reframe: ReframeSettings = ReframeSettings()
  # reframe/importance.py
  def importance_profiles(video: Path, span: Span, *, faces: FaceDetector | None,
                          s: ReframeSettings) -> tuple[list[int], NDArray[np.float32]]: ...   # (t_ms, profils (n, width))
  # reframe/path.py
  def crop_width(layout: Layout, src_w: int, src_h: int) -> int: ...
  def window_coverage(profiles: NDArray[np.float32], xs: NDArray[np.int_], win: int) -> NDArray[np.float32]: ...
  def best_static(profiles: NDArray[np.float32], win: int) -> tuple[int, float]: ...   # (x, couverture moyenne)
  def smooth_path(targets: NDArray[np.float64], *, win: int, width: int, fps: float, s: ReframeSettings) -> NDArray[np.float64]: ...
  def plan_shot(t_ms: Sequence[int], profiles: NDArray[np.float32], *, layout: Layout, src_w: int, src_h: int,
                s: ReframeSettings, tiebreak: Callable[[int, int], int] | None = None) -> ShotCrop | None: ...
  # reframe/choose.py
  def initial_layout(session: Session, config: Config, post_id: int) -> Layout: ...
  def plan_post(video: Path, shots: Sequence[Span], *, first: Layout, preference: Sequence[Layout], src_w: int,
                src_h: int, faces: FaceDetector | None, tiebreak_encoder: ImageEncoder | None,
                s: ReframeSettings) -> ReframeFile: ...
  # render/cropcmd.py
  def write_crop_commands(rf: ReframeFile, out: Path, *, fps: int = 30, offset_ms: int = 0) -> None: ...
  # reframe/step.py
  class ReframeStep(BaseStep): name = "reframe"; heavy = True
  #   posts de la fenêtre sans leur(s) fichier(s) de recadrage (Filters.posts respecté dès la tâche 36)
  #   partie : renders/<p_key>.reframe.json ; compilation : un ReframeFile par élément,
  #   renders/<p_key>.reframe.<k>.json (k = 1, 2, … dans l'ordre de compilation_items)
  # models/yunet.py
  class YunetDetector:   # FaceDetector ; cv2.FaceDetectorYN.create(model, "", (w, h), 0.8, 0.3, 20)
  ```
- Algorithme :

```text
importance (par plan, 5 i/s, gris 320 px de large) :
  mouvement = |flux Farneback(pyr 0.5, levels 3, winsize 15, iter 3, poly_n 5, sigma 1.2) − flux médian| (panoramique retiré)
  saillance = cv2.saliency.StaticSaliencySpectralResidual ; visages = 1 dans les boîtes YuNet (si détecteur)
  carte = w_motion × norm(mouvement) + w_saliency × norm(saillance) + w_faces × visages ; norm = / max (0 si max nul)
  profil(t) = somme de la carte par colonne (vecteur de 320) ; profil nul partout → profil uniforme
crop_width : square = src_h ; portrait = round(src_h × 3/4) ; vertical = round(src_h × 9/16) ; full = src_w ;
  arrondi pair, plafonné à src_w ; crop_w = src_w → aucun recadrage horizontal (mode static centré)
source plus haute que large (src_h > src_w) : largeur = src_w, hauteur = min(src_h, round(src_w / r)) arrondie
  paire (r = 1 square, 3/4 portrait, 9/16 vertical), fenêtre statique centrée verticalement (aucun suivi) ;
  video_rect("full") reste celui de la tâche 7a (hauteur ≤ 1020, largeur au prorata, centré)
plan_shot (win = crop_w en unités de profil) :
  (x*, c*) = best_static ; c* ≥ static_coverage → static, un point
  sinon cibles x_t = argmax couverture par image ; smooth_path = linprog :
    min Σ e_t + λ1 Σ |Δx| + λ2 Σ |Δ²x|   (λ = l1_weights[1:], e_t ≥ |x_t − cible_t| − dead_zone × width)
    sous |x_{t+1} − x_t| ≤ max_speed × width / fps et 0 ≤ x_t ≤ width − win
  part des images de couverture < frame_min_coverage > max_bad_share → None (la mise en page échoue sur ce plan)
  égalité (deux fenêtres statiques à moins de tie_margin, écartées de plus de win/2) : tiebreak CLIP sur l'image clé,
    phrase « the main cartoon character », la fenêtre la plus proche gagne
plan_post : essai de first, puis des mises en page de preference qui suivent ; réussi si tous les plans réussissent ;
  full réussit toujours ; un plan ne déborde jamais sur le suivant : aucun panoramique à travers une coupe (R8)
compilation : plan_post par élément (sa source, ses plans) ; mise en page retenue = la première de l'ordre qui
  réussit pour tous les éléments ; chaque ReframeFile est écrit avec cette mise en page
initial_layout : vs = resolve("layouts.vertical_share", compte, config_value=layouts.vertical_share si écrit,
  default=0.15) ; weighted_choice(derive_seed(post_id, "layout"), {"vertical": vs, "default": 1 − vs}) ;
  "default" → preference[0] ; vertical qui échoue → preference dans l'ordre
```

- Règles fixées :
  - Rendu : `crop=w=<crop_w>:h=<src_h>:x=<x0>:y=0` nommé `crop@rf` (source verticale : `crop=w=<src_w>:h=<crop_h>:x=0:y=<y0>`,
    statique), piloté par `sendcmd=f=crop.cmd` ; le fichier de
    commandes (une ligne `<t> crop@rf x <x>;` par image à 30 i/s, interpolation linéaire entre points d'un plan,
    saut net à une coupe) est écrit dans le dossier de travail, et FFmpeg est lancé avec `cwd` = ce dossier : aucun
    chemin dans la chaîne de filtres. Puis `scale` vers `video_rect(layout)`.
  - `posts.layout` = mise en page réellement retenue ; `renders/<p_key>.reframe.json` (`ReframeFile`).
  - Générateur : `moving_target` (contrat §6) ; `static_targets: Sequence[int]` (abscisses de carrés immobiles
    mais clignotants, pour simuler deux pôles d'action).

- [ ] **Step 1 : Tests qui échouent**

```python
def test_crop_width_per_layout():                        # 1920×1080 : square 1080, portrait 810, vertical 608, full 1920
def test_portrait_source_cropped_vertically_and_full_fits(ctx, synth):
    # source 1080×1920 : square → crop 1080×1080 en y = 420 ; full → video_rect 574×1020 ; check_delivery == []
def test_compilation_reframed_per_item(ctx, synth):      # 2 éléments de sources 1920×1080 et 1280×720 → reframe.1.json,
    # reframe.2.json, même layout ; rendu conforme
def test_best_static_on_centered_target():
def test_smooth_path_respects_speed_and_dead_zone():
def test_r8_crop_follows_moving_target(ctx, synth):      # carré de gauche à droite en 8 s → dans la fenêtre ≥ 95 % des images
def test_r8_never_pans_across_cut(ctx, synth):           # 2 plans, cible qui saute → chemin continu dans chaque plan
def test_r8_layout_fallback_order(ctx, synth):           # 2 pôles aux bords → square ✗, portrait ✗ → full ; vertical ✗ → square d'abord
def test_vertical_share_draw_seeded(ctx):                # 1000 posts, vs 0,15 → 120 à 180 tirages vertical, reproductible
def test_reframe_static_and_tiny_shots_no_nan(ctx, synth):
def test_crop_commands_render_conform_in_unicode_dir(tmp_path, config_dir, synth):   # data « data ü dir » ; check_delivery == []
def test_r16_reframe_rerun_changes_nothing(ctx, synth):
@pytest.mark.slow
def test_yunet_detects_face_in_cc0_image(tmp_path):     # tests/fixtures/images/face_cc0.jpg (licence notée dans SOURCES.md)
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter** (`reframe` avant `render` dans `RUN_ORDER`).
  **Step 4 : Lancer** → PASS ; retirer R8 de `rules_pending.toml`. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Mesurer** — temps de `reframe` par minute de vidéo sur le serveur (objectif de la recherche :
  environ 50 ms par seconde de vidéo), noté dans `docs/decisions.md`.

- [ ] **Step 6 : Commit proposé** — `feat(reframe): action-following square, vertical and portrait crops with ordered fallback (R8)`

---

### Task 35 : Module d'originalité avancé (commentaire, voix off, atténuation, sous-titres)

**Files :**
- Create : `src/rushes/originality/advanced.py`, `src/rushes/originality/voiceover.py`,
  `src/rushes/originality/steps.py`, `src/rushes/captions/__init__.py`,
  `src/rushes/captions/ass.py`, `src/rushes/models/tts.py`
- Modify : `src/rushes/originality/__init__.py` (`module_for("advanced")`), `src/rushes/render/overlays.py`
  (couches audio, ASS, loudnorm du mélange), `src/rushes/models/hub.py` (`tts()` réel), `src/rushes/schemas.py` (`CommentaryFile`, `VoiceFile`),
  `src/rushes/queue/step.py` (alignement de `posts.originality` sur la config), `src/rushes/steps.py`
- Test : `tests/test_ass.py`, `tests/test_voiceover.py`, `tests/test_advanced.py`

**Interfaces :**
- Consomme : `TextGenerator`, `SpeechSynth`, `Transcriber`, `HookFeatures`, `build_features`, `RenderJob`,
  `RenderPlan`, `Overlay`, `AudioLayer`, `BaseModule`, `LoudnormMeasure`, `Config.effective_originality`,
  `settings.originality.voice`.
- Produit :
  ```python
  # schemas.py
  class CommentaryFile(BaseModel):
      version: Literal[1]; post_id: int; language: str; sentences: list[str]; model: str
  class VoiceFile(BaseModel):
      version: Literal[1]; post_id: int; wav: str; duration_ms: int; start_ms: int; words: list[Word]; engine: str
  # originality/voiceover.py
  class CommentaryOut(BaseModel): sentences: list[str] = Field(min_length=1, max_length=3)
  def commentary_prompt(f: HookFeatures) -> tuple[str, str]: ...
  def write_commentary(gen: TextGenerator | None, f: HookFeatures, *, timeout_s: float) -> list[str] | None: ...
  def place_voice(duration_ms: int, speech: Sequence[Span], *, voice_ms: int, horizon: float = 0.3,
                  default_ms: int = 500) -> int: ...
  def align_words(text: str, heard: Sequence[Word]) -> list[Word]: ...
  # captions/ass.py (réutilisé par la tâche 25)
  @dataclass(frozen=True)
  class AssStyle:
      font: str; size: int; primary: str; highlight: str; outline: int; margin_v: int; margin_l: int; margin_r: int
  def build_ass(words: Sequence[Word], *, style: AssStyle, play_res: tuple[int, int] = (1080, 1920),
                max_lines: int = 2, max_chars: int = 32, offset_ms: int = 0) -> str: ...
  # models/tts.py
  class SherpaTts:   # SpeechSynth ; Supertonic 3 (fr, en) ; repli Kokoro (en) ; API reprise de scripts/spike_models.py
  # originality/advanced.py
  class AdvancedModule: name = "advanced"   # plan() = BaseModule().plan() + voix + sous-titres
  # originality/steps.py
  class CommentaryStep(BaseStep): name = "commentary"; heavy = True   # posts.originality advanced de la fenêtre sans commentary.json
  class VoiceStep(BaseStep): name = "voice"; heavy = True             # avec commentary.json, sans voice.json
  #   Filters.posts (tâche 36) : la tâche 36 fait respecter ce filtre aux deux, comme à reframe
  ```
- Règles fixées :
  - Commentaire : 1 à 3 phrases de 20 mots au plus, dans la langue de la marque (R21), écrites à partir de
    `HookFeatures` (nom, personnages, étiquettes, sons, citations) ; prompt système en « You comment a short cartoon
    clip like a friendly narrator. 1 to 3 short sentences, English only, never tell how it ends, no hashtag, no
    emoji. Answer with JSON only. » (fr équivalent) ; mêmes filtres que les accroches pour la langue et la chute.
    Modèle absent ou sortie invalide → aucune voix (le module avancé rend alors comme le module de base).
  - Voix : `SherpaTts.synthesize(texte, language, out_wav)` (moteur `settings.originality.voice.engine` ; `kokoro`
    seulement pour l'anglais, sinon Supertonic) ; puis `Transcriber.transcribe` du WAV (mots horodatés), alignés sur
    le texte connu par `align_words` (rapidfuzz, orthographe du texte, horodatage entendu) ; écrit
    `renders/<p_key>.voice.wav` et `.voice.json`. `tts` est un modèle **léger** du hub (tâche 5) : `VoiceStep.run`
    synthétise le WAV de chaque post ; `VoiceStep.finish` transcrit ensuite tous les WAV du run (Whisper, lourd) et
    écrit les `.voice.json` (un WAV sans `.voice.json` après une interruption est repris au run suivant). Chaque
    modèle est chargé une fois pour l'étape, jamais par post (R19, L23) ; Whisper est le seul modèle lourd chargé.
  - Placement : premier intervalle sans parole d'au moins la durée de la voix + 300 ms dans les `horizon` premiers
    pour cent de la vidéo ; sinon `default_ms`.
  - Mélange : `[0:a]volume=enable='between(t,a,b)':volume=<duck_db>dB[o];[1:a]adelay=<start>|<start>[v];
    [o][v]amix=inputs=2:normalize=0` puis loudnorm en deux passes **sur le mélange** (mesure, puis correction) :
    R7 tient après l'ajout de la voix.
  - Sous-titres de la voix : `build_ass` — un événement `Dialogue` par mot, ligne courante de 2 lignes au plus
    (`max_chars` par ligne), mot courant en couleur `highlight` (`\c&HBBGGRR&`) et `\fscx110\fscy110`, style de la
    charte, marges des zones sûres (R9) ; fichier `voice.ass` passé au filtre `ass=voice.ass` avec `cwd` (tâche 34).
  - `RenderPlan.ai_voice = True` quand une voix est présente (rendu). `upload.json.settings.ai_generated_label` ne lit
    pas le plan : vrai si `renders/<p_key>.voice.json` existe et que `posts.render_version` est la version courante
    (tâche 8b, aucune colonne) ; la checklist rappelle alors l'étiquette « contenu généré par IA ».
  - Le module vient de la config seule (R50) : au début de `QueueStep`, `posts.originality` des posts
    `planned|rendered` est aligné sur `Config.effective_originality(compte)` ; un changement met
    `render_version = NULL` (et supprime `commentary.json`/`voice.*` si le compte repasse en `base`). Les posts
    `ready` et `published` ne changent pas (Louis les a peut-être programmés). `CommentaryStep`, `VoiceStep` et le
    rendu lisent `posts.originality` ; `module_for("advanced")` renvoie `AdvancedModule`.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_r50_originality_switch_by_config_only(ctx, edit_yaml, synth):
    # st_tt passé advanced dans le YAML → posts planned/rendered : originality "advanced", render_version NULL, voix au
    # rendu suivant ; post ready : originality et fichiers inchangés
def test_r50_both_modules_render(ctx, synth):                             # un post base, un post advanced : check_delivery == []
def test_r21_voice_in_brand_language(ctx):                                # marque fr → synthesize(language="fr"), prompt en français
def test_r7_advanced_mix_integrated_loudness(ctx, synth):                 # −14 ± 1 LU après mélange
def test_original_audio_ducked_during_voice(ctx, synth):                  # RMS de l'original −12 dB ± 2 dans la fenêtre de voix
def test_advanced_without_llm_renders_like_base(ctx, edit_yaml, synth):   # overlays identiques au module base, aucune couche audio
def test_build_ass_one_event_per_word_two_lines_max():
def test_ass_inside_safe_margins():
def test_align_words_fixes_spelling():
def test_place_voice_avoids_dialogue():
def test_upload_ai_label_true_with_voice(ctx, synth):                     # voice.json + version courante → true
def test_r19_voice_step_one_heavy_model(ctx):
    # 3 posts : hub.loads() tts == 1 et transcriber == 1 ; tts léger (aucun déchargement de Whisper par TTS)
def test_r16_voice_rerun_changes_nothing(ctx, synth):
@pytest.mark.slow
def test_supertonic_fr_and_en_wav(tmp_path):
@pytest.mark.slow
def test_tts_then_whisper_alignment_roundtrip(tmp_path):                  # texte connu → mots alignés, ordre conservé
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter** (`commentary`, `voice` avant `render` dans `RUN_ORDER`).
  **Step 4 : Lancer** → PASS ; retirer R50 de `rules_pending.toml`. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Écouter** — rendre sur le serveur un post `advanced` en anglais et un en français ; Louis les écoute
  (qualité de la voix, niveau de l'atténuation) ; remarques consignées dans `docs/decisions.md`.

- [ ] **Step 6 : Commit proposé** — `feat(originality): advanced module with local commentary voice-over, ducking, word subtitles (R50)`

---

## Porte de sortie de la phase 2

- [ ] Sur le serveur, dans l'image, minuteur de nuit simulé par `rushes run` lancé chaque nuit pendant au plus
  7 nuits, sans autre action : `rushes status` montre au moins 7 jours de stock pour `st_tt`, `st_ig`, `tt_tt`,
  `tt_ig` (posts `ready` dans `outbox_test/`, vitrine).
- [ ] Les deux modules d'originalité sont présents dans ce stock (au moins un compte `base` et un compte
  `advanced`), tous les rendus passent `check_delivery`.
- [ ] Les épisodes viennent de la découverte seule (aucun fichier déposé à la main) ; chaînes officielles
  enregistrées dans `derived_values`.
- [ ] Relancer `rushes run` ne change rien (R16).
- [ ] `tests/rules_pending.toml` ne contient plus R4, R8, R9, R11, R15, R18, R33, R34, R35, R43, R50.
- [ ] Mesures complétées dans `docs/decisions.md` (accroches, recadrage, voix, CLIP, transcription par minute).
- [ ] Suite rapide verte sur le Mac ; suite `slow` verte dans l'image sur le serveur ; ruff et mypy propres.
