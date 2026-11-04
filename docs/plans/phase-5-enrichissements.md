# Phase 5 — Enrichissements : plan d'implémentation

> **Pour les agents qui exécutent le plan :** sous-skill requis : `superpowers:subagent-driven-development`
> (recommandé) ou `superpowers:executing-plans`, tâche par tâche. Lire d'abord `docs/plans/00-master.md` (contrats
> transverses, sections citées « §5.x ») et les blocs **Interfaces** des phases 1 à 4 (noms réutilisés ici).
> Les étapes utilisent des cases `- [ ]`.

**But :** rushes constitue seul un panel de comptes TikTok par série et par créateur et en tire ses hashtags
tournants et le hashtag nostalgie (31) ; il compose des compilations thématiques et propose des thèmes (30) ; Louis
peut revoir les passages en attente dans une page hors ligne (32) ; rushes trouve seul des séries en tendance,
en produit la vitrine et tient le tableau de suivi des licences (38).

**Architecture :** étude de marché hebdomadaire (panel vérifié par la page publique `embed`, collecte yt-dlp,
analyse des hashtags qui surperforment) écrite dans `derived_values` ; étape `compile_themes` dans `RUN_ORDER` et
suggestions hebdomadaires ; page de revue construite depuis `web/` en un seul fichier, décisions réappliquées par la
CLI ; découverte des séries par extraction de noms et vérification par chaîne officielle, puis vitrine dans le budget.

**Pile :** yt-dlp (TikTok, curl-cffi), `Http.get_text` (page `embed`), numpy (regroupement des vecteurs CLIP),
Vite + React + shadcn/ui + Tailwind v4 + `vite-plugin-singlefile` (page de revue), Playwright.

**Spec :** `docs/spec.md` (étapes 0, 2, 7 et 9, « Hashtags qui surperforment », R13, R14, R32, R44),
`docs/decisions.md`, `docs/plans/00-master.md`.

## Global Constraints

Toutes celles de `00-master.md` §2 et des phases 1 à 4 s'appliquent. En plus, pour cette phase :

- **Migration** : 0016 (tâche 31) seulement ; les tâches 30, 32 et 38 n'en ont pas.
- **Valeurs déduites** : `market.panel`, `market.keywords`, `hashtags.pool`, `hashtags.nostalgia_instagram`,
  `discover.series_evidence` par `derived.record(..., source="market:<méthode>" | "discover_series")`. Seuls
  `hashtags.pool` et `hashtags.nostalgia_instagram` suivent `derived.resolve` (une amorce de la config l'emporte).
  `market.panel` n'est qu'un instantané : les comptes `sources.tiktok_accounts` de la config sont des semences du
  panel, toujours gardées, jamais retirées par la vérification « hors sujet », et ne figent jamais le panel. Jamais de
  valeur enregistrée sous l'échantillon minimal : la dernière valeur reste.
- **Étapes et filtres** : chaque étape ajoutée est classée dans `src/rushes/selectors.py` (tâche 22, Files
  « Modify ») ; les posts produits passent par `render`, `meta` et `package`, qui respectent `Filters.posts`
  (tâche 36).
- **CLI** : une commande déjà prévue dans `tests/test_cli.py` passe de `PENDING` à `IMPLEMENTED` dans la tâche qui
  l'implémente (`compile themes` 30, `market collect` et `market analyze` 31, `review build` et `review apply` 32) ;
  une commande nouvelle est ajoutée à `IMPLEMENTED`.
- **Hashtags** : la réserve ne contient jamais un hashtag obligatoire (R13, ajouté d'office), `#viral`, `#foryou`,
  `#fyp`, ni le hashtag propre du sujet ; 5 hashtags au plus par post (R13, tâche 8a).
- **TikTok** : lecture publique seulement, sans cookies (R36), `impersonate=chrome`, 2 s entre requêtes (R18) ; un
  échec de collecte est journalisé et jamais bloquant (spec, étape 2).
- **Vitrine** : une série candidate n'est jamais publiable (`is_test_post`, R14, R32) ; elle consomme le budget
  `showcase_budget_left` (tâche 28) avec les créateurs prospectés.
- **Page de revue** : skill `frontend-design` avant de l'écrire ; `break-ui` et `web-design-guidelines` avant de
  livrer ; fonctionne ouverte depuis le disque (`file://`), sans réseau ni serveur.
- Doublures ajoutées : `tests/fixtures/tiktok_embed/*.html` (pages `embed` enregistrées), `tiktok_profile(videos)`
  (tâche 17) réutilisé pour le panel.

## Review Focus

1. **Collecte TikTok entièrement en panne** (extraction cassée, page `embed` changée) : la réserve et le panel
   gardent leur dernière valeur, avertissement, alerte de collecteur au deuxième échec ; la réserve n'est jamais
   vidée. Test : `test_market_total_failure_keeps_previous_pool` (tâche 31).
2. **Données de marché maigres** (panel de moins de 10 comptes, hashtag vu moins de 10 fois, série neuve) : aucune
   valeur enregistrée, hashtags tirés du nom, hashtag nostalgie `#nostalgia`. Test :
   `test_market_sparse_data_keeps_defaults` (tâche 31).
3. **Thème sans assez de gags sur un compte** (moins de 4 passages pertinents, ou tous déjà utilisés) : aucune
   compilation partielle, simple information. Test : `test_theme_without_enough_gags_produces_nothing` (tâche 30).
4. **Fichier de décisions périmé ou abîmé** (exporté d'une ancienne page, clé inconnue, passage approuvé ou écarté
   depuis, JSON invalide) : éléments périmés sautés avec leur raison, clés inconnues signalées, JSON invalide refusé
   avec un message français, rien d'appliqué à moitié. Test : `test_review_apply_stale_unknown_and_malformed`
   (tâche 32).
5. **Faux candidat de série** (expression générique « Full Episodes », série déjà connue sous une autre
   orthographe, nom sans chaîne officielle) : écarté par la liste de mots génériques, le rapprochement avec les titres
   connus et la vérification de la chaîne officielle. Test : `test_series_candidates_reject_generic_and_duplicates`
   (tâche 38).

---

### Task 31 : Étude de marché TikTok (panel, collecte, hashtags tournants, hashtag nostalgie)

**Files :**
- Create : `src/rushes/market/__init__.py`, `src/rushes/market/panel.py`, `src/rushes/market/collect.py`,
  `src/rushes/market/analyze.py`, `src/rushes/db/migrations/versions/0016_market.py`
- Modify : `src/rushes/config.py` (`MarketSettings`), `src/rushes/db/models.py`, `src/rushes/steps.py` (profil
  `weekly`), `src/rushes/cli.py` (`market collect`, `market analyze`, `market panel`), `src/rushes/queue/rank.py`
  (tendance), `src/rushes/ops/report.py` (section « Étude de marché »), `src/rushes/selectors.py` (`market_panel`,
  `market_collect`, `market_analyze` dans `FILTERABLE_STEPS` : verticale du sujet, séries → `cartoons`, créateurs →
  `creators`, élément nostalgie → `cartoons`), `src/rushes/creators/prospect.py` (requêtes enrichies par
  `market.keywords`, point d'accroche de la tâche 27), `tests/test_cli.py` (`market collect` et `market analyze`
  passent de `PENDING` à `IMPLEMENTED`, `market panel` ajouté à `IMPLEMENTED`)
- Test : `tests/test_market_panel.py`, `tests/test_market_analyze.py`, `tests/test_market_steps.py`,
  `tests/fixtures/tiktok_embed/*.html`, cassettes de profils TikTok

**Interfaces :**
- Consomme : `collect_tiktok(ytdlp, handle, *, n)`, `parse_profile`, `PublicVideo` (17), `tiktok.official_accounts`
  (10a), `creator.handles` (27), `ProspectStep` (27), `SeriesConfig.sources.tiktok_accounts`, `resolve_hashtags`,
  `default_pool` (8a), `post_outcomes` (18), `collector_health` (17), `Subject`/`display_name` (22),
  `hashtag_from_name`.
- Produit :
  ```python
  # config.py
  class MarketSettings(_Model):
      panel_min: int = 10; panel_max: int = 30; candidate_min_followers: int = 10_000; subject_min_share: float = 0.3
      new_candidates_per_run: int = 10; videos_per_account: int = 30; window_days: int = 90
      min_hashtag_videos: int = 10; pool_size: int = 8; own_min_posts: int = 5; own_drop_ratio: float = 1.0
      keywords_size: int = 5
      nostalgia_candidates: list[str] = ["#nostalgia", "#90skids", "#childhoodmemories", "#throwback",
                                         "#oldcartoons", "#cartoonnostalgia"]
  class Settings: ...  # + market: MarketSettings = MarketSettings()
  # market/panel.py
  @dataclass(frozen=True)
  class EmbedProfile: handle: str; verified: bool; followers: int | None; private: bool; descriptions: tuple[str, ...]
  def embed_url(handle: str) -> str: ...                # https://www.tiktok.com/embed/@<handle sans @>
  def parse_embed(html: str) -> EmbedProfile | None: ...
  def mentions_in(descriptions: Iterable[str]) -> list[str]: ...   # @handles en minuscules, dédoublonnés
  def subject_share(descriptions: Sequence[str], name: str, hashtag: str) -> float: ...
  class PanelStep(BaseStep): name = "market_panel"        # éléments : sujets (séries et créateurs actifs ou candidats)
  # market/collect.py
  class MarketCollectStep(BaseStep): name = "market_collect"   # éléments : market_accounts status 'panel'
  # market/analyze.py
  @dataclass(frozen=True)
  class HashtagLift: tag: str; lift: float; n_with: int
  def hashtag_lifts(videos: Sequence[tuple[str, int, Sequence[str]]], *, min_videos: int) -> list[HashtagLift]: ...
  #   (compte, vues, hashtags) → écart moyen de log(vues / médiane du compte), avec contre sans ; trié lift desc, n desc
  #   réutilisé tel quel avec les mots des légendes à la place des hashtags (mots-clés)
  def build_pool(lifts: Sequence[HashtagLift], *, excluded: set[str], own: Mapping[str, tuple[int, float]],
                 s: MarketSettings) -> list[str]: ...
  def pick_nostalgia(lifts: Sequence[HashtagLift], candidates: Sequence[str]) -> str | None: ...
  STOPWORDS: frozenset[str]      # mots vides fr et en (liste courte écrite dans le module)
  def caption_words(caption: str, *, excluded: set[str]) -> list[str]: ...
  #   minuscules, dédoublonnés ; sans hashtags, @mentions, URL, nombres, mots de moins de 4 lettres, STOPWORDS, excluded
  class MarketAnalyzeStep(BaseStep): name = "market_analyze"   # éléments : sujets, puis « cartoons » (nostalgie)
  ```
- Règles fixées :
  - Migration `0016_market.py` : `market_accounts(id INTEGER PRIMARY KEY, subject_kind TEXT NOT NULL CHECK IN
    ('series','creator'), subject_id TEXT NOT NULL, handle TEXT NOT NULL, verified INTEGER, followers INTEGER,
    status TEXT NOT NULL CHECK IN ('candidate','panel','rejected'), source TEXT NOT NULL CHECK IN
    ('official','config','creator','mention'), reason TEXT, checked_at TEXT, UNIQUE (subject_kind, subject_id,
    handle))` ; `market_videos(id INTEGER PRIMARY KEY, video_id TEXT NOT NULL UNIQUE, account_handle TEXT NOT NULL,
    views INTEGER, likes INTEGER, shares INTEGER, duration_s REAL, caption TEXT, hashtags_json TEXT, posted_at TEXT,
    collected_at TEXT NOT NULL)`, index `(account_handle, posted_at)`.
  - **Panel** (`market_panel`) : semences = `tiktok.official_accounts` (source `official`), amorces
    `sources.tiktok_accounts` (`config`), compte TikTok d'un créateur (`creator`) ; élargissement par les
    `mentions_in` des descriptions déjà collectées (`market_videos`) et des `EmbedProfile.descriptions` des comptes
    vérifiés (`mention`), `new_candidates_per_run` vérifications au plus **par sujet** et par run. Vérification d'un
    candidat par `Http.get_text(embed_url)` → `parse_embed` : refusé si privé, abonnés < `candidate_min_followers`
    ou, hors comptes officiels et semences de la config, `subject_share < subject_min_share` (part des descriptions
    qui citent le nom du sujet ou son hashtag) ; raison écrite dans `reason`. Une semence `config` n'est jamais
    refusée pour « hors sujet » ni retirée du panel. Panel = comptes `status = 'panel'` triés (officiels, semences de
    la config, puis abonnés), `panel_max` au plus, **quel que soit leur nombre** (la collecte en a besoin pour faire
    grandir le panel par les mentions) ; `record("market.panel", sujet, [handles])` (instantané pour le rapport et
    `market panel`) seulement si `panel_min` comptes au moins.
  - `parse_embed` s'appuie sur la page enregistrée (Step 2) : le bloc JSON de données de la page, repéré une fois et
    décrit en commentaire dans le code avec la date ; bloc absent ou illisible → `None` (le candidat reste
    `candidate`, nouvel essai la semaine suivante).
  - **Collecteurs** (`collector_health`, table de la tâche 17 ; une mise à jour par run et par collecteur, dans
    `finish`) : `tiktok_market` (`market_collect` : échec si tous les comptes traités ont échoué, succès sinon) et
    `tiktok_embed` (`market_panel` : échec si `parse_embed` renvoie `None` pour tous les candidats vérifiés du run,
    succès si au moins un est lu) ; aucun élément traité → inchangé. Deuxième échec consécutif → alerte
    `collector:tiktok_market` ou `collector:tiktok_embed` (tâche 20). Le compteur `tiktok` (statistiques, 17) n'est
    jamais touché ici.
  - **Collecte** (`market_collect`, hebdomadaire) : `collect_tiktok(ytdlp, handle, n=videos_per_account)` pour chaque
    ligne `market_accounts.status = 'panel'` → `market_videos` (upsert par `video_id` ; hashtags lus dans la
    description, normalisés). Échec d'un compte → `warned` ; jamais `failed`.
  - **Mots-clés** (spec, étapes 0 et 2 ; décision C35) : dans `market_analyze`, par sujet, `hashtag_lifts` sur
    `(compte, vues, caption_words(légende, excluded = mots du nom du sujet))`, mots vus dans au moins
    `min_hashtag_videos` vidéos, `lift > 0`, `keywords_size` premiers → `record("market.keywords", sujet, [...],
    source="market:lift")` ; trop peu de vidéos → rien d'enregistré. La tâche 27 (`creators/prospect.py`, étape 3)
    ajoute à ses requêtes YouTube de la langue L les mots-clés des sujets créateurs dont la marque parle L (union
    dédoublonnée, `keywords_size` au plus par langue), avec le même filtre « cette semaine ». Les comptes TikTok du
    panel ne sont jamais prospectés comme créateurs : rushes a besoin d'une source YouTube.
  - **Analyse** (`market_analyze`) : vidéos des comptes du panel du sujet postées dans les `window_days` derniers
    jours ; `hashtag_lifts` (spec, « Hashtags qui surperforment ») sur les hashtags vus dans au moins
    `min_hashtag_videos` vidéos. `build_pool` : `lift > 0`, hors `excluded` (obligatoires résolus du sujet sur les
    deux plateformes, interdits, `#fyp`, hashtag propre du sujet), puis retrait des hashtags qui ne donnent rien chez
    nous (`own[tag] = (n posts jugés qui l'utilisent, ratio moyen)`, `n ≥ own_min_posts` et ratio < `own_drop_ratio`),
    `pool_size` premiers → `record("hashtags.pool", sujet, …, source="market:lift", confidence=min(1, n_vidéos/200))`.
    Moins de `min_hashtag_videos` vidéos utiles ou panel absent → rien d'enregistré (la dernière valeur reste, sinon
    le défaut de 8a).
  - **Hashtag nostalgie** : `hashtag_lifts` sur toutes les vidéos des panels de séries ; `pick_nostalgia` = candidat
    de `nostalgia_candidates` au plus fort lift positif vu au moins `min_hashtag_videos` fois →
    `record("hashtags.nostalgia_instagram", "cartoons", …)` ; aucun → la valeur reste (défaut `#nostalgia`).
    Instagram n'étant pas lisible sans connexion, le panel TikTok sert d'indicateur (décision P37).
  - Rapport hebdomadaire « Étude de marché » : par sujet, taille du panel, vidéos de la semaine (saturation), 5
    meilleurs hashtags (lift, n), durée médiane et heure de publication la plus fréquente des vidéos au-dessus de la
    médiane de leur compte, longueur médiane de leur légende.
  - Tendance de la file (spec, « hashtags en hausse ») : `queue` (16b) devient `tendance = 0,8 × vitesse de la
    source + 0,2 × part des hashtags du post présents dans `hashtags.pool` du sujet` ; sans réserve enregistrée,
    la seconde part vaut 0 et la formule de 16b est inchangée. Test :
    `test_queue_trend_counts_pool_hashtags` (même post, avec et sans hashtag de la réserve → tendance plus haute).
  - Profil `weekly` : `market_panel`, `market_collect`, `market_analyze` avant `discover_series` et `recalibrate`
    (ordre final : `00-master.md` §3).
  - `rushes market panel [--series|--creator]` : tableau des comptes (statut, source, abonnés, raison).

- [ ] **Step 1 : Tests qui échouent**

```python
def test_parse_embed_from_recorded_page():                  # badge, abonnés, descriptions ; page privée → private True
def test_panel_built_from_official_seeds_and_mentions(ctx):  # cassettes + pages embed → 10 à 30 comptes, officiels d'abord
def test_panel_below_panel_min_still_collected_and_grows(ctx):
    # 3 comptes panel (< panel_min) : market_collect les lit ; run suivant → nouveaux candidats tirés des mentions,
    # au plus new_candidates_per_run par sujet ; aucun market.panel enregistré tant que < 10
def test_config_seed_accounts_kept_in_panel(ctx, edit_yaml):  # semence config hors sujet → reste panel ; d'autres comptes s'ajoutent
def test_candidate_rejected_private_small_or_off_topic(ctx):
def test_embed_parse_failure_counts_and_alerts(ctx):
    # pages embed sans bloc JSON pour tous les candidats, 2 runs → tiktok_embed à 2, alerte collector:tiktok_embed
def test_market_collect_failure_uses_own_counter(ctx):       # tous les comptes en échec → tiktok_market + 1, tiktok inchangé
def test_market_keywords_from_overperforming_captions(ctx):  # « prank » dans 12 vidéos au-dessus de la médiane → market.keywords
def test_prospect_queries_include_market_keywords(ctx):      # market.keywords ["prank"] d'un créateur fr → requête ytsearch « prank »
@pytest.mark.parametrize(("videos", "expected"), LIFT_CASES)    # 3 cas calculés à la main
def test_hashtag_lift_reference_values(videos, expected):
def test_pool_excludes_required_forbidden_fyp_and_own_hashtag():
def test_own_stats_drop_hashtag_that_does_nothing(ctx):       # 6 posts jugés avec #x, ratio moyen 0,6 → #x retiré
def test_nostalgia_hashtag_chosen_from_candidates(ctx):
def test_meta_uses_market_pool(ctx):                          # légende suivante : hashtag tournant tiré de la réserve dérivée
def test_config_seed_pool_wins(ctx, edit_yaml):               # branding.hashtags_pool écrit → resolve origin "config"
def test_market_total_failure_keeps_previous_pool(ctx):
def test_market_sparse_data_keeps_defaults(ctx):
def test_r36_market_never_uses_cookies(ctx):                  # options TikTok sans cookiefile ; data/cookies jamais lu
def test_r16_market_rerun_same_week_changes_nothing(ctx):
```

- [ ] **Step 2 : Enregistrer** les pages `embed` de `@oggyandthecockroaches`, d'un compte de clips et d'un compte
  privé (`curl` sur le Mac) dans `tests/fixtures/tiktok_embed/`, et les cassettes de profil de 3 comptes ; noter dans
  `docs/decisions.md` l'emplacement du bloc JSON de la page `embed`.

- [ ] **Step 3 : Lancer** → FAIL. **Step 4 : Implémenter.** **Step 5 : Lancer** → PASS. Suite rapide, ruff, mypy verts.

- [ ] **Step 6 : Commit proposé** — `feat(market): self-built tiktok panels, overperforming hashtag pool, keywords and nostalgia hashtag`

---

### Task 30 : Compilations thématiques

**Files :**
- Create : `src/rushes/compile/__init__.py`, `src/rushes/compile/themes.py`, `src/rushes/compile/suggest.py`,
  `config/themes/chases.yaml` (exemple actif)
- Modify : `src/rushes/config.py` (`ThemeConfig`, chargement de `config/themes/`), `src/rushes/paths.py`
  (`proposals`), `src/rushes/originality/base.py` (`RenderJob.counters`), `src/rushes/render/render.py`,
  `src/rushes/steps.py` (`compile_themes` après `bestof` dans `RUN_ORDER`, `themes_suggest` dans `weekly`),
  `src/rushes/cli.py` (`compile themes`, `compile suggest`), `src/rushes/selectors.py` (`compile_themes` et
  `themes_suggest` dans `FILTERABLE_STEPS` : verticale du compte), `tests/test_cli.py` (`compile themes` passe de
  `PENDING` à `IMPLEMENTED`, `compile suggest` ajouté à `IMPLEMENTED`)
- Test : `tests/test_themes.py`, `tests/test_theme_suggest.py`

**Interfaces :**
- Consomme : `search_scenes`, `blob_vector` (12a), `ImageEncoder.encode_texts`, `widen`, `widen_sentences` (24),
  `pick_items`, `dedupe`, `draw_target_ms`, `derive_seed` (13), `cut_point_costs`,
  `render_plate_multi(items: Sequence[tuple[Path, Span]], layout, out, *, s, transition_ms, on_progress, reframes,
  holes)` (13, 24, 34), `registry.assign`, `Strategy`, `Subject`, `subject_of_episode` (22), `is_test_post` (8a),
  `scenes.popularity`, `TAG_PROMPTS`.
- Produit :
  ```python
  # config.py
  class ThemeConfig(_Model):
      id: str; title: dict[str, str]                 # par langue : {"en": "Best chases", "fr": "Meilleures poursuites"}
      queries: list[str] = Field(min_length=1)        # texte libre pour CLIP
      characters: list[str] = []; sounds: list[Literal["impact", "explosion", "splash", "scream", "laughter"]] = []
      tags: list[str] = []; items: tuple[int, int] = (4, 8); family: Literal["monetizable", "long"] = "monetizable"
      vertical: Vertical = "cartoons"; active: bool = True
  class ThemeSettings(_Model):
      min_cos: float = 0.22; cooldown_days: int = 14; counter_ms: int = 2000
      cluster_cos: float = 0.85; cluster_min_scenes: int = 8; cluster_min_episodes: int = 3
  class Settings: ...  # + themes: ThemeSettings = ThemeSettings()
  class Config: ...    # + themes: dict[str, ThemeConfig]
  # compile/themes.py
  @dataclass(frozen=True)
  class ThemeCandidate: scene_id: int; episode_id: int; span: Span; relevance: float; popularity: float; complete: float
      @property
      def score(self) -> float: ...                 # relevance × (0,5 + 0,5 × popularity) × complete
  def theme_candidates(session: Session, ctx: Context, theme: ThemeConfig, account_id: str, subject: Subject, *,
                       text_vectors: NDArray[np.float32]) -> list[ThemeCandidate]: ...
  def order_items(chosen: Sequence[ThemeCandidate]) -> list[ThemeCandidate]: ...   # du plus faible au plus fort
  class CompileThemesStep(BaseStep): name = "compile_themes"; heavy = True
  #   éléments : (thème actif, compte de sa verticale, sujet ayant au moins un épisode de ce compte avec facette vision)
  # compile/suggest.py
  def cluster_scenes(vectors: NDArray[np.float32], episode_ids: Sequence[int], *, cos: float, min_scenes: int,
                     min_episodes: int) -> list[list[int]]: ...          # indices, regroupement glouton par meneur
  class ThemesSuggestStep(BaseStep): name = "themes_suggest"; heavy = True   # éléments : comptes
  # originality/base.py
  class RenderJob: ...   # + counters: tuple[tuple[int, int, str], ...] = ()   (début, fin en temps de sortie, texte)
  ```
- Règles fixées :
  - **Un seul sujet par compilation** (décision L25) : une compilation thématique = thème × sujet × compte. Ses
    passages viennent tous d'épisodes d'une même série ou d'un même créateur ; nom dans les 80 premiers caractères
    (R12), `#<série>` ou `{creator}` (R13), crédit et campagne (R23) et `is_test` (`is_test_post`, R14) sont ceux de
    ce sujet, lus comme pour un best-of (`subject_of_episode` du premier passage). Deux sujets d'un même compte →
    deux compilations distinctes, chacune avec son propre délai de retour.
  - Candidats (spec, étape 7) : scènes des épisodes **du compte** (`owner_account_id`, R44) **et du sujet** avec
    facette `vision` ;
    `relevance` = cosinus maximal entre le vecteur de la scène et les vecteurs des `queries`, ramené à `[0, 1]` par
    `(cos − min_cos) / (1 − min_cos)`, scènes sous `min_cos` écartées ; filtres `characters` (au moins un),
    `sounds` (au moins un événement), `tags` (au moins une étiquette) ; `popularity` = `scenes.popularity` normalisée
    sur le compte (absente → 0,5) ; passage = élargissement du centre de la scène aux points admissibles de la
    stratégie (plans, ou phrases pour un créateur) dans `[min_item_ms, max_item_ms]` de la tâche 13 ; `complete` =
    `1 / (1 + coût(a) + coût(b))`.
  - Famille = `theme.family` du YAML (défaut `monetizable`) : aucun tirage, `families.share` (tâche 37) ne
    s'applique pas aux compilations thématiques.
  - Cible : `target_ms = draw_target_ms(derive_seed(theme_id, account_id, subject_id, date locale du run, "theme"),
    min_s, max_s de la famille)` (R6, R17 : graine enregistrée).
  - Choix : `dedupe` (chevauchement > 50 %), passages déjà utilisés dans une compilation thématique du même compte
    exclus ; `pick_items(target_ms=…)` dans les bornes de la famille (transitions déduites, R6) avec `items` ; moins
    de `items[0]` passages → aucune compilation, élément `skipped` « pas assez de gags pour <thème> sur <compte> ».
    Un thème revient sur un couple (compte, sujet) quand `cooldown_days` sont écoulés depuis la compilation
    précédente de ce thème et que celle-ci est close : son dernier post est `published`, `expired` ou `discarded`,
    ou la compilation est `rejected`. Une compilation encore `proposed`, `needs_review` ou `approved` sans post
    clos bloque le thème.
  - Écritures : `Compilation(kind="theme", theme_id, account_id, status="proposed")`, un
    `Segment(kind="compilation_item", family, target_ms, seed, signal_level=None, score)` par passage,
    `compilation_items` dans l'ordre de `order_items` (score croissant). Approbation (15), accroches (14),
    attribution tout ou rien (13) : inchangées.
  - Rendu : `render_plate_multi(items=[(clean.mp4 de l'épisode du passage, span), …], …, reframes=[ReframeFile du
    passage ou None, …])` : un élément par passage, sources de tailles et cadences différentes admises (tâche 13) ;
    les posts passent par `RenderStep`, qui respecte `Filters.posts`. Bandeau = `title[langue]` ; compteur « #N » …
    « #1 » (compte à rebours : le dernier passage, le plus fort, porte « #1 ») incrusté au début de chaque passage
    pendant `counter_ms`, dans la zone de l'accroche décalée sous elle (`Rect(left, 680, …, 140)`, zones sûres R9) ;
    aucun numéro de partie.
  - Suggestions (`themes_suggest`, profil `weekly`) : `cluster_scenes` sur les vecteurs du compte ; chaque groupe
    nommé par l'étiquette de `TAG_PROMPTS` la plus proche de son vecteur moyen et ses deux personnages les plus
    fréquents ; plus les titres de vidéos `retained` qui contiennent « compilation », « best of », « moments » ou
    « top » (expression qui suit le nom de la série) ; fichier `data/proposals/themes/<id>.yaml` au schéma de
    `ThemeConfig` avec `active: false` et un commentaire d'origine. Louis le copie dans `config/themes/`, le nomme
    et l'active (spec ; décision P38). Rapport hebdomadaire : liste des suggestions nouvelles.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_theme_config_validation(config_dir, edit_yaml):     # titre manquant pour une langue de la verticale, items (2, 9) → refus
def test_theme_candidate_score_reference():
def test_r44_compilation_uses_own_episodes_only(ctx):        # scène très pertinente d'un autre compte jamais retenue
def test_r14_theme_compilation_one_subject_name_and_test_flag(ctx, synth):
    # compte avec tom_and_jerry (licenciée) et oggy (vitrine) : deux compilations, passages d'une seule série chacune ;
    # légende de la première commence par « Tom and Jerry », #tomandjerry présent ; celle d'oggy is_test → outbox_test/
def test_theme_items_weakest_to_strongest_4_to_8_within_family(ctx, synth):
def test_r6_theme_target_and_seed_recorded(ctx, synth):       # chaque Segment : target_ms et seed non nuls ; même seed au rerun
def test_theme_family_from_yaml_not_drawn(ctx, edit_yaml):    # family: long dans le YAML → segments long, quelle que soit families.share
def test_theme_render_job_one_item_per_passage(ctx, synth):   # passages de deux épisodes de tailles différentes → 2 sources dans items
def test_counter_overlay_countdown_inside_safe_zone(tmp_path):
def test_theme_compilation_assignment_all_or_nothing(ctx):
def test_theme_cooldown_and_no_reused_passages(ctx):
    # + cas : précédente rejected, ou dernier post discarded → thème admissible après cooldown_days ;
    #   précédente encore proposed → bloqué
def test_theme_without_enough_gags_produces_nothing(ctx):
def test_cluster_scenes_groups_close_vectors():
def test_theme_suggestions_written_inactive(ctx):
def test_r16_compile_themes_rerun_changes_nothing(ctx):
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS ; retirer R44 de
  `rules_pending.toml` si elle y est encore. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(compile): single-subject theme compilations from own-account scenes with countdown, and weekly theme suggestions (R44)`

---

### Task 32 : Revue facultative (`review build`, `review apply`)

**Files :**
- Create : `src/rushes/review/__init__.py`, `src/rushes/review/build.py`, `src/rushes/review/apply.py`,
  `src/rushes/review/static/review.html` (généré), `web/review.html`, `web/src/review/main.tsx`,
  `web/src/review/ReviewApp.tsx`, `web/src/review/ItemCard.tsx`, `web/src/review/types.ts`,
  `web/vite.review.config.ts` (configuration Vite à part : entrée `review.html`, `vite-plugin-singlefile`, sortie
  `src/rushes/review/static/` ; celle de `serve` reste intacte), `web/tests/review.spec.ts`,
  `web/tests/fixtures/review-data.json`
- Modify : `src/rushes/schemas.py` (`ReviewDecision`, `ReviewDecisions`, `ReviewData`), `src/rushes/paths.py`
  (`review`), `web/package.json` (`build:review` = `vite build -c vite.review.config.ts`, `vite-plugin-singlefile`),
  `docker/Dockerfile` (étape Node : `build:review`), `src/rushes/cli.py` (`review build`, `review apply`),
  `tests/test_cli.py` (`review build` et `review apply` passent de `PENDING` à `IMPLEMENTED`)
- Test : `tests/test_review_build.py`, `tests/test_review_apply.py`, `tests/fixtures/review/decisions-export.json`
  (fichier exporté par `web/tests/review.spec.ts`, versionné)

**Interfaces :**
- Consomme : `segment_checks`, `compilation_checks`, `decide`, `approval_threshold` (15), `can_assign`, tables
  `hooks`, `segments`, `compilations`, `run_ffmpeg`, `extract_frame`, `sentence_points` (24), `Subject`.
- Produit :
  ```python
  # schemas.py
  class ReviewItem(BaseModel):
      key: str; kind: Literal["part", "bestof", "compilation"]; subject: str; account_id: str; episode: str | None
      spans: list[tuple[int, int]]; duration_s: float; family: str; reasons: list[str]; score: float
      hooks: list[tuple[int, str, str]]; preview: str; thumb: str; state: str        # empreinte de l'état au moment du build
      nudges: list[int]                                                              # points admissibles à ± 5 s des bornes
  class ReviewData(BaseModel): version: Literal[1]; built_at: str; threshold: dict[str, float]; items: list[ReviewItem]
  class ReviewDecision(BaseModel):
      key: str; state: str; action: Literal["approve", "reject", "keep"]
      start_ms: int | None = None; end_ms: int | None = None; hook_rank: Literal[1, 2, 3] | None = None
      note: str = Field("", max_length=200)
  class ReviewDecisions(BaseModel): version: Literal[1]; built_at: str; decisions: list[ReviewDecision]
  # review/build.py
  def item_state(session: Session, key: str) -> str: ...      # sha1[:12] de statut + bornes + accroche choisie
  def build_review(ctx: Context, *, account: str | None, limit: int | None) -> Path: ...   # data/review/<AAAA-MM-JJ-HHMM>/index.html
  # review/apply.py
  @dataclass(frozen=True)
  class ApplyResult: applied: int; skipped: list[tuple[str, str]]; refused: list[tuple[str, str]]
  def apply_review(ctx: Context, path: Path) -> ApplyResult: ...
  ```
- Règles fixées :
  - `review build` : segments `part` en `needs_review` et compilations `status = 'needs_review'` (statut du CHECK
    de la migration 0004) (filtres `--account`, `--limit`) ; raisons
    recalculées sans rien écrire (`segment_checks`/`compilation_checks` puis `decide`) ; aperçu 480 px de large par
    élément (`libx264 -preset ultrafast -crf 30`, AAC 96 k, passages enchaînés pour une compilation) dans
    `previews/<key>.mp4`, vignette `thumbs/<key>.jpg` ; page = `review/static/review.html` dans laquelle les données
    sont injectées en `<script id="review-data" type="application/json">` (`ReviewData`, `</` échappé). Aucune
    ressource externe : tout est dans le fichier ou relatif au dossier.
  - Page (`web/`, construite en un seul fichier par `vite-plugin-singlefile` avec `vite.review.config.ts`, composants shadcn,
    Tailwind v4) : une carte par élément (aperçu `<video preload="none">`, sujet, compte, durée, raisons, score contre
    seuil, 3 accroches) ; boutons Approuver, Rejeter, Laisser ; réglage des bornes par pas parmi `nudges` ; choix de
    l'accroche ; compteur de décisions ; bouton « Exporter les décisions » → téléchargement
    `review-decisions-<built_at>.json` (`ReviewDecisions`, par `Blob` et lien `download`). Décisions gardées dans
    `localStorage` (clé = `built_at`, accès dans `try/catch`) pour ne rien perdre en rechargeant.
  - `review apply <fichier>` : JSON validé (`ReviewDecisions`) ; invalide → `RushesError`, code 1, hors de toute
    transaction, « fichier de décisions illisible : … », piste « exporter de nouveau depuis la page » (le code 2
    reste réservé à la config, §5.4) ; clé inconnue → `refused` ; `state` ≠ `item_state` actuel → `skipped`
    « déjà modifié depuis la page (approuvé, écarté ou redécoupé) ». Chaque décision dans sa propre transaction :
    - `approve` : bornes appliquées si données (seulement parmi les points admissibles), puis contrôles : R1 simulée,
      R2 (`can_assign` simulé), R5, R6, R24, et blocages R15, R34 → si l'un échoue, `refused` avec la raison et rien
      d'écrit ; sinon `approved` (approbation par Louis, R3 : le seuil de score ne s'applique pas, une règle si) ;
    - `reject` : `rejected` (« rejeté à la revue ») ; jamais compté au bandit (aucun post) ;
    - `hook_rank` : `hooks.chosen` déplacé sur le rang ;
    - `keep` : rien.
    Rapport : `Table` des appliqués, sautés, refusés ; les passages approuvés passent à `assign` au run suivant.
  - Version de la page : comme pour la tâche 36, la fraîcheur se vérifie en fin de tâche
    (`npm --prefix web run build:review && git diff --exit-code src/rushes/review/static/review.html`), jamais dans
    pytest : la cible Docker `test` n'a pas Node et tourne sans réseau. Le test pytest vérifie seulement que le
    fichier existe, contient `<script id="review-data"` et aucun `http://` ni `https://`.
  - Test navigateur : `web/tests/review.spec.ts` (Playwright npm, comme la phase 3), jamais une fixture pytest.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_review_build_lists_needs_review_with_reasons(ctx, synth):   # 2 éléments, raisons « score 0,62 < seuil 0,70 », « R5 »
def test_review_page_is_offline_single_file(ctx, synth):              # aucun http(s):// dans les src/href ; données JSON lisibles
def test_review_apply_approves_rejects_and_sets_hook(ctx):
def test_review_apply_refuses_rule_violation(ctx):                     # approuver un passage R5 → refused, statut inchangé
def test_review_apply_bounds_change_rechecked(ctx):                    # bornes déplacées hors bornes de famille → refused
def test_r3_manual_approval_below_threshold_allowed(ctx):              # score < seuil, aucune règle violée → approved
def test_review_apply_stale_unknown_and_malformed(ctx, tmp_path):     # JSON invalide → code 1, message français, rien d'écrit
def test_r16_review_apply_twice_changes_nothing(ctx):
def test_review_html_is_offline_single_file():                         # existe, <script id="review-data">, aucun http(s)://
def test_review_apply_reads_page_export(ctx):                          # tests/fixtures/review/decisions-export.json validé et appliqué
```

```ts
// web/tests/review.spec.ts (Playwright npm)
test("review page offline roundtrip", ...)   // review.html + review-data.json injecté, ouvert en file:// : approuver un
                                             // élément, choisir l'accroche 2, exporter → fichier conforme à ReviewDecisions,
                                             // identique à tests/fixtures/review/decisions-export.json ; aucune requête réseau
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter** (skill `frontend-design` avant la page).
  **Step 4 : Lancer** → PASS ; `npm --prefix web run lint`, `npm --prefix web run test:e2e -- review.spec.ts` et
  `npm --prefix web run build:review && git diff --exit-code src/rushes/review/static/review.html` verts ;
  `break-ui` et `web-design-guidelines` passés sur la page, corrections faites ; retirer R3 de `rules_pending.toml`.
  Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(review): offline single-file review page and rule-checked review apply (R3)`

---

### Task 38 : `discover series` (séries en tendance, vitrine, tableau de suivi des licences)

**Files :**
- Create : `src/rushes/discover/series.py`, `src/rushes/rights/__init__.py`, `src/rushes/rights/table.py`
- Modify : `src/rushes/config.py` (`SeriesDiscoverSettings`, `RightsSettings`), `src/rushes/discover/discover.py`
  (séries candidates incluses), `src/rushes/db/sync.py` (séries prospectées gardées, rattachement par chaîne
  officielle ; `rekey_subject` étendu à `market_accounts.subject_id`), `src/rushes/steps.py` (profil `weekly`),
  `src/rushes/cli.py` (`discover series`, `rights list`, `rights export`), `src/rushes/ops/report.py` (« Suivi des
  licences », « Séries candidates »), `src/rushes/ingest/select.py` (`stock_days`, `select_downloads` : vitrine des
  candidates), `src/rushes/queue/schedule.py` (`sequence` : posts de test des prospects en dernier),
  `src/rushes/selectors.py` (`discover_series` dans `FILTERABLE_STEPS`, aucun élément si `vertical == "creators"` ;
  `rights` dans `GLOBAL_STEPS`), `tests/test_cli.py` (`discover series`, `rights list` passent de `PENDING` à `IMPLEMENTED`,
  `rights export` ajouté à `IMPLEMENTED`)
- Test : `tests/test_discover_series.py`, `tests/test_rights_table.py`, cassettes

**Interfaces :**
- Consomme : `channel_search_url`, `rank_channels`, `classify`, `popular_playlist_url` (10a), `guess_language` (24),
  `hashtag_lifts` (31), `showcase_budget_left` (28), rattachement de `db/sync.py` (27), `channels.momentum_json`
  (27), `stock_days`, `select_downloads` (10b), `sequence` (16b), `is_test_post`, `Right`, `derived.record`.
- Produit :
  ```python
  # config.py
  class SeriesDiscoverSettings(_Model):
      queries: list[str] = ["classic cartoons", "cartoon compilation", "old cartoons full episodes",
                            "90s cartoons", "cartoon funny moments"]
      search_results: int = 20; min_mentions: int = 3; dedupe_ratio: float = 90.0; max_new_per_week: int = 3
      min_words_per_min: float = 20.0; drop_after_weeks: int = 4
      generic_words: list[str] = ["compilation", "full", "episode", "episodes", "season", "best", "of", "moments",
                                  "cartoon", "cartoons", "classic", "new", "hd", "4k", "official", "funny", "the"]
  class RightsSettings(_Model): alert_priority: float = 0.8
  class Settings: ...  # + series_discover, rights
  # discover/series.py
  def name_candidates(titles: Iterable[str], *, generic: set[str]) -> Counter[str]: ...
  def is_known(name: str, titles: Iterable[str], *, ratio: float) -> bool: ...
  def potential(followers: int | None, popular_vpd_median: float | None, mentions: int, *, max_mentions: int) -> float: ...
  def settle_audio(session: Session, series_id: str, s: SeriesDiscoverSettings) -> Literal["nonverbal", "dialogue_en", "drop"] | None: ...
  class DiscoverSeriesStep(BaseStep): name = "discover_series"   # éléments : sources de noms, puis candidates à réévaluer
  # rights/table.py
  @dataclass(frozen=True)
  class RightsRow:
      kind: Literal["series", "creator"]; subject_id: str; name: str; holder: str | None; status: str
      requested_at: str | None; priority: float | None; showcase_posts: int; origin: str; allowed: bool
  def rights_rows(session: Session, config: Config) -> list[RightsRow]: ...   # priorité décroissante
  class RightsStep(BaseStep): name = "rights"   # recalcule priority et showcase_posts, alerte unlicensed_top
  ```
- Règles fixées :
  - Sources de noms (hebdomadaire, spec étape 0) : recherches YouTube de la semaine (`queries`, filtre « cette
    semaine », `search_results`) ; titres des vidéos `retained` des séries connues (« Tom and Jerry vs Pink
    Panther ») ; hashtags du panel (31) à lift positif qui ne sont le hashtag d'aucune série connue.
    `name_candidates` : titres coupés sur `|`, `-`, `–`, `:`, `,`, ` vs `, ` & `, ` x `, parenthèses ; mots de
    `generic_words`, nombres et années retirés ; morceaux de 1 à 5 mots dont le premier est en capitale ; hashtags
    sans `#`. Candidat retenu si vu au moins `min_mentions` fois et `is_known` faux (rapidfuzz `token_set_ratio ≥
    dedupe_ratio` avec les titres de la config et des séries en base).
  - Vérification : recherche de chaînes sur le nom → `rank_channels` + `classify` de la tâche 10a ; seule une chaîne
    `official` valide le candidat (sinon écarté, raison dans le rapport). `max_new_per_week` candidats au plus, par
    potentiel décroissant.
  - Création : ligne `series` (id = slug du nom, `title` = nom, `audio = "dialogue_en"` provisoire,
    `licensed = 0`, `origin = "prospect"`, `status = "candidate"`) ; `record("youtube.official_channels", id,
    [chaîne])` et `record("discover.series_evidence", id, {"mentions", "sources", "channel", "followers"})` ; ligne
    `rights` (`series`, `to_request`, `holder` = titre de la chaîne officielle, `priority = potential`).
  - `potential` = `0,5 × min(1, log10(1 + abonnés) / 8) + 0,3 × min(1, log10(1 + médiane des vues/jour des 30
    vidéos populaires) / 6) + 0,2 × min(1, mentions / max_mentions)` ; composante absente → poids renormalisés.
    `max_mentions` = plus grand nombre de mentions parmi les candidats du run, au moins 1 (échelle linéaire pour les
    mentions, logarithmique pour les deux autres).
  - Vitrine : `discover_youtube` traite aussi les séries `candidate` d'origine `prospect` ; propriétaire : toutes
    les marques cartoons (16a) ; posts `is_test` → `outbox_test/` (R14, R32). Une candidate n'est dans aucune
    rotation : `select_downloads` lui donne, indépendamment du stock des comptes, jusqu'à `showcase_budget_left`
    épisodes par semaine (budget seul).
  - Posts de test des sujets prospectés (séries candidates et créateurs `origin = "prospect"`) : `stock_days` et le
    besoin calculé par `select_downloads` les ignorent (ils ne freinent jamais le téléchargement des sujets de la
    config) ; `sequence` ne les place que sur les créneaux qu'aucun autre post ne prend. Les posts de test des sujets
    de la config (vitrine avant licence) restent traités comme aujourd'hui.
  - `settle_audio` (réévaluation hebdomadaire) : sur les épisodes transcrits d'une candidate, mots par minute de
    parole < `min_words_per_min` → `audio = "nonverbal"` ; au moins 50 mots et `guess_language ≠ "en"` → `status =
    "dropped"` (« dialogue non anglais », décision P40). Candidate sans vidéo `retained` depuis `drop_after_weeks`
    semaines → `dropped`.
  - Rattachement : un YAML de série dont la chaîne officielle (config ou dérivée) est celle d'une candidate prend ses
    lignes (même mécanisme que la tâche 27, généralisé : `rekey_subject(session, kind, old_id, new_id)`, qui
    réécrit aussi `market_accounts.subject_id`, table 0016).
  - Tableau des licences : `rights_rows` (séries et créateurs, config et prospects) ; `RightsStep` écrit
    `showcase_posts` (posts `is_test` `ready` ou plus loin du sujet) et recalcule chaque semaine `priority` de tous
    les sujets non autorisés, config comprise : série → `potential(abonnés de la chaîne officielle résolue, médiane
    des vues/jour de ses vidéos populaires, mentions de la semaine, max_mentions)` ; créateur → score de
    `channels.momentum_json` (tâche 27, déjà dans [0, 1]) ; aucune donnée → `priority` NULL (en bas du tableau) ;
    sujet non autorisé dont la priorité ≥
    `alert_priority` → `Message("warn", "<nom> : très bien classé (0,86) sans licence — <ligne du tableau>",
    alert=f"unlicensed_top:{id}")`. `rushes rights list` (tableau), `rushes rights export` →
    `data/reports/rights-<date>.csv` ; rapport hebdomadaire : 10 premières lignes et séries candidates de la
    semaine. Aucune commande ne met `licensed` à vrai : seul le YAML de Louis le fait (R14, R32).
  - Profil `weekly` : `discover_series` et `rights` avant `report_weekly` (tableau de la tâche 20).

- [ ] **Step 1 : Tests qui échouent**

```python
def test_name_candidates_from_titles():                    # « Tom and Jerry vs Pink Panther | Full Episodes » → {"Pink Panther", …}
def test_series_candidates_reject_generic_and_duplicates(ctx):
def test_candidate_requires_official_channel(ctx):         # cassettes : chaîne vérifiée → créée ; communautaire seule → écartée
def test_potential_reference_values():
def test_candidate_series_created_with_rights_row(ctx):
def test_r14_candidate_series_goes_to_outbox_test(ctx, synth):
def test_r32_candidate_series_never_publishable_without_yaml(ctx, synth):
def test_showcase_budget_limits_candidate_ingestion(ctx):
def test_candidate_showcase_triggered_by_budget_alone(ctx):   # comptes cartoons à 20 jours de stock → candidate ingérée quand même
def test_prospect_test_posts_ignored_by_stock_and_last_in_sequence(ctx):
    # 10 posts de test d'une candidate : stock_days inchangé ; sequence place d'abord les posts des sujets de la config
def test_settle_audio_nonverbal_and_drop_non_english(ctx):
def test_yaml_for_candidate_series_rekeys_rows(ctx, edit_yaml):  # + market_accounts.subject_id réécrit
def test_rights_table_sorted_with_showcase_counts(ctx):
def test_rights_priority_computed_for_config_subjects(ctx):   # tom_and_jerry et squeezie (config, non autorisés) : priority non nulle
def test_unlicensed_top_alert(ctx):
def test_r16_discover_series_rerun_same_week_changes_nothing(ctx):
```

- [ ] **Step 2 : Enregistrer** les cassettes : une recherche « classic cartoons » filtrée « cette semaine », la
  recherche de chaînes « Pink Panther » et « The Flintstones », les UULP (`1:30`) des chaînes trouvées.

- [ ] **Step 3 : Lancer** → FAIL. **Step 4 : Implémenter.** **Step 5 : Lancer** → PASS ; `tests/rules_pending.toml`
  ne contient plus que des règles terminées en phase 6 (R14 → 40a, §7) ; il est vide à la fin de la tâche 40a.
  Suite rapide, ruff, mypy verts.

- [ ] **Step 6 : Commit proposé** — `feat(discover,rights): trending series discovery with showcase budget and licence tracking table (R14, R32)`

---

## Porte de sortie de la phase 5

- [ ] Sur le serveur, après au plus 4 runs `rushes run --profile weekly` (relancés à la main si besoin, R18
  respectée) : `rushes market panel` montre 10 à 30 comptes pour `tom_and_jerry` et `oggy` ; `hashtags.pool` des
  deux séries, `market.keywords` d'au moins un créateur et `hashtags.nostalgia_instagram` présents dans
  `derived_values` avec la source `market:*` (ou, si la collecte TikTok échoue, l'échec figure au rapport et
  l'alerte `collector:tiktok_market` ou `collector:tiktok_embed` part au deuxième run en échec).
- [ ] Une série candidate trouvée seule a, dans la semaine suivante, des posts `ready` dans `outbox_test/` dans la
  limite du budget de vitrine, et sa ligne dans `rushes rights list` ; aucun post de candidate dans `outbox/`.
- [ ] Au moins une compilation thématique (`config/themes/chases.yaml`) rendue pour un compte cartoons, ses passages
  d'une seule série ; une suggestion de thème écrite dans `data/proposals/themes/`.
- [ ] Louis ouvre une page de revue sur son Mac (fichier copié depuis le serveur), exporte deux décisions,
  `rushes review apply` les applique.
- [ ] `tests/rules_pending.toml` ne contient que des règles terminées en phase 6 ; `test_rules_coverage` vert ;
  suite rapide, ruff, mypy, lint, build du front et `web/tests/review.spec.ts` verts.
