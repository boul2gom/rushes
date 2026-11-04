# Phase 4 — Verticale créateurs : plan d'implémentation

> **Pour les agents qui exécutent le plan :** sous-skill requis : `superpowers:subagent-driven-development`
> (recommandé) ou `superpowers:executing-plans`, tâche par tâche. Lire d'abord `docs/plans/00-master.md` (contrats
> transverses, sections citées « §5.x ») et les blocs **Interfaces** des phases 1 à 3 (noms réutilisés ici).
> Les étapes utilisent des cases `- [ ]`.

**But :** une vidéo de créateur YouTube suit le même pipeline qu'un épisode de dessin animé, avec les stratégies
propres aux créateurs (SponsorBlock, coupes en fin de phrase, autocut, recadrage sur le visage, sous-titres mot par
mot, citation en accroche, crédit et campagne). rushes trouve seul de nouveaux créateurs, surveille leurs chaînes
toutes les 6 h et propose des extraits d'une nouvelle vidéo en moins d'une heure.

**Architecture :** une table de stratégies par verticale et un « sujet » (série ou créateur) résolu pour chaque
épisode (22) ; mots, SponsorBlock, autocut et ligne de temps « après autocut » (24) ; pistes de visages et
sous-titres ASS (25) ; crédits et campagnes (26) ; prospection et instantanés (27) ; catalogue, veille, voie express
et second passage (28).

**Pile :** yt-dlp (+ Deno, `yt-dlp-ejs`), API publique SponsorBlock, flux RSS YouTube (`xml.etree`), OpenCV YuNet,
scipy (`linprog`, déjà là), libass, rapidfuzz.

**Spec :** `docs/spec.md` (sections « Verticale créateurs », « Prospection, catalogue et veille », « Campagnes de
clipping », règles R10, R21, R23–R30, R32, R45, R46), `docs/decisions.md`, `docs/plans/00-master.md`.

## Global Constraints

Toutes celles de `00-master.md` §2 et des phases 1 à 3 s'appliquent. En plus, pour cette phase :

- **Migrations dans l'ordre d'exécution** : 0012 (tâche 25), 0013 (26), 0014 (27), 0015 (28) ; l'étude de marché
  (tâche 31) passe à 0016 (§5.10 du master mis à jour).
- **Une seule source de vérité pour la verticale** : une étape ne teste jamais `vertical == "creators"` ni le type de
  sujet ; elle lit `strategy_for(subject.vertical)` (tâche 22). Les marques, langues et autorisations restent celles
  de la config (R21, R22, R32).
- **Temps** : les passages, le registre et les signaux restent en temps de `clean.mp4` (R17) ; l'autocut n'existe
  qu'au rendu. Les durées des familles (R6) se vérifient sur la durée **après autocut**, c'est-à-dire la durée
  réelle de la vidéo publiée (`Timeline`, tâche 24).
- **Réseau** : YouTube par `ctx.net.ytdlp` (métadonnées, R18), SponsorBlock et flux RSS par `ctx.net.http` ; jamais de
  cookies hors découverte (R18) : `YtDlp.extract(..., cookies_allowed=False)` par défaut (§5.8, tâche 10a), seul
  `DiscoverYoutubeStep` passe `True` ; la veille, le catalogue (`creators_backfill` n'est pas une découverte), les
  extractions de la voie express, le second passage et la prospection n'en utilisent jamais ; le téléchargement
  (`ingest_source`) suit la règle de secours de `download` (L32) ; tests sur
  cassettes yt-dlp et fichiers enregistrés (`tests/fixtures/rss/*.xml`, `tests/fixtures/sponsorblock/*.json`).
- **Vitrine** : un créateur de la config non `authorized` et un créateur prospecté suivent tout le pipeline en mode
  test (`outbox_test/`, R22, R32) ; seuls les créateurs prospectés consomment le budget `showcase`
  (P11 étendu, décision P25).
- **Alertes** : par `Message(alert=…)` (tâche 20) ; nouvelle clé utilisée : `creator_video:<v_key>` (une clé par
  vidéo : la 2e vidéo du jour d'un créateur n'est pas muette), envoyée par `send_pending_alerts` (tâche 20).
- Doublures ajoutées ou étendues : `FakeHttp(routes={préfixe d'URL: objet | str | HttpError})` ;
  `FakeFaceDetector(mode="red")` (boîtes des taches rouge pur, `cv2.inRange`) ; générateur :
  `face_tracks: Sequence[Sequence[tuple[int, int]]]` (rectangles rouges 200×240 qui suivent chacun une liste
  `(t_ms, x_px)`) et `face_hidden_ms: Sequence[Span]` (visages masqués) ; fixture `two_verticals_ctx`
  (construite par `tests/fixtures/two_verticals.py`, enregistrée dans `tests/conftest.py`) : un épisode de série
  (propriétaire `st_tt`) et un épisode de créateur (propriétaire `ic_tt`) à chaque statut d'épisode, de segment et de
  post.

## Review Focus

1. **Vidéo de créateur sans parole exploitable, ou dans une autre langue que la marque** (gameplay muet, musique,
   vidéo anglaise d'un créateur français) : sans mots, découpe sur les plans et sans autocut ; langue détectée
   différente de la marque, aucun passage et un avertissement « langue détectée en ≠ fr (R21) », jamais de plantage.
   Test : `test_creator_video_without_speech_or_in_wrong_language` (tâche 24).
2. **Plusieurs visages ou visage intermittent** (interview à deux, visage qui sort du cadre, faux positif d'une
   image) : la fenêtre ne saute jamais d'un visage à l'autre ; elle cadre les deux visages si leur union tient dans la
   fenêtre, sinon le plus grand ; une détection de moins de 0,5 s est ignorée. Test :
   `test_face_reframe_two_faces_and_flicker_no_jumps` (tâche 25).
3. **Campagne modifiée après l'empaquetage** (mention ou hashtag ajouté, campagne retirée) : les posts non publiés
   reçoivent une nouvelle légende et un nouveau paquet en gardant leur créneau ; un post publié n'est jamais touché.
   Test : `test_campaign_change_repackages_unpublished_posts` (tâche 26).
4. **Short, direct ou première détectés par la veille** : un Short (≤ 60 s) est rejeté ; un direct en cours ou une
   première pas encore diffusée est ignoré, puis repris au contrôle suivant ; jamais d'ingestion partielle. Test :
   `test_watch_skips_shorts_lives_and_premieres` (tâche 28).
5. **Deux nouvelles vidéos à la fois, ou la voie express pendant le run de nuit** : les deux vidéos sont traitées
   étape par étape (chaque modèle chargé une fois par étape, R19) ; l'échec de l'une ne bloque pas l'autre (code 4) ;
   le run de nuit cède le verrou entre deux éléments ; la voie express ne se bloque jamais sur ses propres verrous. Test : `test_express_two_videos_one_fails_models_loaded_once` (tâche 28).

---

### Task 22 : Verticales (stratégies, sujet, filtres, langue par marque, citation en accroche)

**Files :**
- Create : `src/rushes/verticals.py`, `src/rushes/subjects.py`, `src/rushes/selectors.py`
- Modify : `src/rushes/context.py` (`Filters`), `src/rushes/cli.py` (option `--vertical` commune),
  `src/rushes/config.py` (`CreatorConfig.display_name`, `BestofSettings.creator_level2_weights`), le `select` de
  chaque étape de `src/rushes/steps.py` (par `selectors`), `src/rushes/hooks/features.py` (citations),
  `src/rushes/hooks/rank.py` (`PriorRanker`), `src/rushes/hooks/rules.py` (guillemets par langue),
  `src/rushes/highlights/ladder.py` (poids par stratégie), `src/rushes/queue/rank.py` (fraîcheur par stratégie),
  `src/rushes/meta/meta.py` (nom et jeton `{creator}` par `Subject`)
- Test : `tests/fixtures/two_verticals.py`, `tests/conftest.py` (fixture `two_verticals_ctx`),
  `tests/test_verticals.py`, `tests/test_selectors.py`, `tests/test_creator_hooks.py`, `tests/test_creator_language.py`

**Interfaces :**
- Consomme : `Config.brand_of`, `creator_by_id`, `series_by_id`, `hashtag_from_name`, `derived.resolve`, `STEPS`, `HookFeatures`,
  `Candidate`, `PriorRanker`, `find_moments`, `BestofSettings`, `WordsFile`, `AudioFile.events`.
- Produit :
  ```python
  # context.py (§5.1)
  @dataclass(frozen=True)
  class Filters: ...   # + vertical: Vertical | None = None ; + episodes: tuple[int, ...] = ()   (voie express, tâche 28)
  # verticals.py
  @dataclass(frozen=True)
  class Strategy:
      vertical: Vertical
      cut_points: Literal["shots", "sentences"]      # R24 (tâche 24)
      interior_cleaning: bool                        # SponsorBlock retiré partout (R46) ; sinon bords seulement (R45)
      autocut: bool                                  # R45 (tâche 24)
      reframe_target: Literal["action", "face"]      # R26 (tâche 25)
      subtitles: Literal["dialogue", "always"]       # R10, R25 (tâche 25)
      quote_first: bool                              # citation de la transcription en tête des accroches
      credit: bool                                   # R23 (tâche 26)
      fresh: bool                                    # fraîcheur exp(−âge/48 h) dans la file (16b)
  STRATEGIES: Mapping[Vertical, Strategy] = {
      "cartoons": Strategy("cartoons", "shots", False, False, "action", "dialogue", False, False, False),
      "creators": Strategy("creators", "sentences", True, True, "face", "always", True, True, True),
  }
  def strategy_for(vertical: Vertical) -> Strategy: ...
  # subjects.py
  @dataclass(frozen=True)
  class Subject:
      kind: Literal["series", "creator"]; id: str; name: str; vertical: Vertical; language: str
      speech: bool          # série audio == "dialogue_en", ou créateur
      publishable: bool     # licensed / authorized dans le YAML (R14, R22, R32)
      origin: Literal["config", "prospect"]
      hashtag: str          # jeton {series} / {creator} : hashtag_from_name(title), effective_hashtag, ou
                            # hashtag_from_name(name) pour un créateur sans YAML (prospect)
  def subject_of_episode(session: Session, config: Config, episode_id: int) -> Subject: ...
  def subject_of_post(session: Session, config: Config, post_id: int) -> Subject: ...
  def display_name(session: Session, config: Config, kind: Literal["series", "creator"], subject_id: str) -> str: ...
  # selectors.py
  def filter_episodes(stmt: Select[Any], filters: Filters, config: Config) -> Select[Any]: ...
  def filter_posts(stmt: Select[Any], filters: Filters, config: Config) -> Select[Any]: ...
  def accounts_for(filters: Filters, config: Config) -> list[str]: ...
  def subjects_for(filters: Filters, config: Config, kind: Literal["series", "creator"]) -> list[str]: ...
  GLOBAL_STEPS: frozenset[str]   # étapes sans élément filtrable : backup, backup_verify, retention, disk, report, report_weekly
  FILTERABLE_STEPS: frozenset[str]   # toutes les autres étapes de STEPS ; les deux ensembles sont disjoints
  # hooks/features.py
  def quote_candidates(words: Sequence[Word], sentences: Sequence[Span], span: Span, *, events: Sequence[SoundEvent],
                       min_words: int = 3, max_words: int = 7, head: float = 0.6) -> list[str]: ...
  # config.py
  class CreatorConfig: ...   # + display_name: str | None = None
  class BestofSettings: ...  # + creator_level2_weights: dict[str, float] = {"loudness": 0.4, "shot_density": 0.2,
                             #   "motion": 0.3, "impact": 0.2, "laughter": 1.2, "exclamation": 0.8, "short_sentence": 0.6}
  ```
- Règles fixées :
  - Langue d'un sujet = langue de la marque du compte propriétaire de l'épisode (R21). La config refuse déjà un
    créateur dont la langue diffère de sa marque (F4) ; un créateur prospecté reçoit une marque de sa langue (27).
  - `display_name` : série → `branding.display_name` sinon `title` ; créateur → `display_name` du YAML, sinon valeur
    dérivée `youtube.channel_title` (tâche 27), sinon `id` avec « _ » remplacé par une espace et capitalisé
    (`"squeezie"` → `"Squeezie"`). Utilisé pour la légende (R12), le bandeau et les accroches.
  - Créateur sans YAML (prospecté, `origin="prospect"`) : `name` = `display_name` (titre de chaîne enregistré par la
    tâche 27) ; jeton `{creator}` = `Subject.hashtag` = `hashtag_from_name(name)` (`meta` lit `Subject.hashtag` au
    lieu de `CreatorConfig.effective_hashtag`) ; crédit par défaut `"🎥 {name}"` (tâche 26).
  - Filtres combinés par ET. `vertical` = verticale de la marque du compte propriétaire (épisodes, segments, posts,
    compilations) ou du compte lui-même (étapes par compte : `queue`, `recalibrate`, `views_drop`) ; étapes par série
    (`discover_youtube`) : aucune série si `vertical == "creators"`. `episodes` restreint aux épisodes listés et, pour
    les étapes par compte, aux comptes propriétaires de ces épisodes. Chaque étape de `STEPS` est soit filtrable,
    soit dans `GLOBAL_STEPS` ; chaque tâche qui ajoute une étape après celle-ci la classe dans `selectors.py` (Files
    « Modify »). Exception : `stats_collect` filtre seulement ses comptes TikTok ; les fichiers d'`inbox/` (illisibles
    avant lecture) sont traités quelle que soit la verticale.
  - CLI : `--vertical cartoons|creators` sur `run` et sur chaque commande d'étape ; l'en-tête affiche la portée
    (`Header.scope = "creators"`).
  - Citations (créateurs, `strategy.quote_first`) : phrases entièrement dans le passage, qui commencent dans les
    `head` premiers pour cent (une phrase de la fin révélerait la chute, R11), de `min_words` à `max_words` mots ;
    ordre : phrase finissant par « ! » ou « ? », puis phrase suivie d'un événement `laughter` dans les 1,5 s, puis
    position ; 3 au plus → `HookFeatures.quotes`. Gabarit de la règle `quote` (tâche 14) : en `“{quote}”`,
    fr `« {quote} »`. Les filtres R11 s'appliquent tels quels.
  - `PriorRanker` : si `quote_first`, les candidats de type `quote` d'abord (ordre conservé), puis `llm`, puis
    `rules`. Le bandit (tâche 18) reprend cet ordre a priori tant qu'il manque de résultats, puis décide seul
    (décision P29).
  - Best-of d'un créateur : `find_moments` reçoit `creator_level2_weights` (rires, exclamations et phrases courtes
    pèsent plus que le volume).
  - La fraîcheur de la file (16b) et les facettes (`words` si `subject.speech`, 12a) lisent `Strategy`/`Subject` :
    comportement inchangé, plus aucun test de nom de verticale.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_strategies_table():                     # valeurs exactes de STRATEGIES
def test_subject_of_creator_episode(ctx):        # épisode squeezie : name "Squeezie", language "fr", speech, publishable False
def test_display_name_order(ctx, edit_yaml):     # YAML display_name > youtube.channel_title dérivé (ligne derived_values insérée par le test : la tâche 27 l'écrira) > id capitalisé
@pytest.mark.parametrize("step", FILTERABLE_STEPS)
def test_vertical_filter_on_every_step(two_verticals_ctx, step):
    # select(Filters(vertical="creators")) ne renvoie que des éléments de créateur, "cartoons" que des éléments de série ;
    # seule exception inscrite dans le test : les fichiers d'inbox/ de stats_collect (renvoyés pour les deux verticales)
def test_every_registered_step_is_filterable_or_global():   # set(STEPS) == FILTERABLE_STEPS | GLOBAL_STEPS
def test_prospect_subject_name_and_hashtag(ctx):  # créateur yt_mrbeast sans YAML, youtube.channel_title « MrBeast » inséré
    # par le test → name "MrBeast", hashtag "#mrbeast", origin "prospect" ; jeton {creator} de la légende → #mrbeast
def test_episodes_filter_restricts_episode_and_account_steps(two_verticals_ctx):
def test_cli_vertical_option(cli_env):           # rushes --plain run --vertical creators → en-tête « creators », code 0
def test_quote_candidates_short_first_half():    # phrase de 9 mots écartée ; phrase dans les 40 % finaux écartée ; « ! » d'abord
def test_creator_hooks_quote_first(ctx):         # partie de créateur : rang 1 de type quote « … » ; partie de série inchangée
def test_creator_bestof_uses_creator_weights(ctx, synth):   # rires simulés battent un pic de volume isolé
def test_r21_creator_pipeline_single_language(ctx, synth):
    # instant_culte : transcribe(language="fr"), accroches fr, légende au gabarit fr (« Partie »), aucun mot outil anglais
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS ; retirer R21 de
  `rules_pending.toml`. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(verticals): strategy table, subject resolution, vertical filter and quote-first creator hooks (R21)`

---

### Task 24 : Mots et phrases, SponsorBlock, autocut

**Files :**
- Create : `src/rushes/net/sponsorblock.py`, `src/rushes/clean/sponsor.py`, `src/rushes/core/timeline.py`,
  `src/rushes/cut/points.py`, `src/rushes/render/autocut.py`
- Modify : `src/rushes/net/base.py` et `src/rushes/net/http.py` (`HttpError`), `src/rushes/core/text.py`
  (`guess_language`), `src/rushes/config.py` (`SponsorBlockSettings`, `CreatorCutSettings`),
  `src/rushes/schemas.py` (`CleanFile`), `src/rushes/ingest/ingest.py` (lecture SponsorBlock),
  `src/rushes/clean/clean.py` (trous intérieurs), `src/rushes/highlights/popularity.py`, `src/rushes/db/trace.py`,
  `src/rushes/cut/parts.py`, `src/rushes/cut/bestof.py`, `src/rushes/highlights/ladder.py`,
  `src/rushes/approve/rules.py` (R6 après autocut), `src/rushes/render/encode.py`, `src/rushes/render/render.py`
- Test : `tests/test_sponsorblock.py`, `tests/test_timeline.py`, `tests/test_clean_creators.py`,
  `tests/test_creator_cut.py`, `tests/test_autocut.py`, `tests/fixtures/sponsorblock/*.json`

**Interfaces :**
- Consomme : `Strategy`, `Subject` (22), `Word`, `WordsFile`, `sentences` (12c), `AudioFile.speech`, `CleanFile`,
  `cut_point_costs`, `partition`, `widen`, `pick_items`, `render_plate`, `render_plate_multi`, `video_signals`.
- Produit :
  ```python
  # net/base.py
  class HttpError(SourceUnavailable): status: int | None     # statut hors 2xx (None : erreur réseau) ; levée par UrllibHttp
  # config.py
  class SponsorBlockSettings(_Model):
      url: str = "https://sponsor.ajay.app/api/skipSegments"
      categories: tuple[str, ...] = ("sponsor", "selfpromo", "interaction", "intro", "outro")
      max_share: float = 0.5
  class CreatorCutSettings(_Model):
      word_margin_ms: int = 30; boundary_pad_ms: int = 250; context_sentences: int = 1
      autocut_min_gap_ms: int = 600; autocut_keep_ms: int = 150; language_min_words: int = 50
  class Settings: ...  # + sponsorblock: SponsorBlockSettings ; + creator_cut: CreatorCutSettings
  # net/sponsorblock.py
  @dataclass(frozen=True)
  class SponsorSegment: start_ms: int; end_ms: int; category: str; action: Literal["skip", "poi"]
  def fetch_segments(http: Http, s: SponsorBlockSettings, video_id: str) -> list[SponsorSegment] | None: ...
  #   [] : aucune donnée (404) ; None : service indisponible
  def tidy_segments(segs: Sequence[SponsorSegment], duration_ms: int, s: SponsorBlockSettings) -> list[SponsorSegment]: ...
  # core/timeline.py
  @dataclass(frozen=True)
  class Timeline:
      span: Span; holes: tuple[Span, ...]          # trous triés, disjoints, inclus dans span
      @property
      def length_ms(self) -> int: ...
      def to_out(self, t_ms: int) -> int | None: ...    # None dans un trou
      def to_in(self, t_out_ms: int) -> int: ...
      def pieces(self) -> list[Span]: ...                # morceaux gardés
  def make_timeline(span: Span, holes: Iterable[Span]) -> Timeline: ...
  # core/text.py
  STOPWORDS: Mapping[str, frozenset[str]]          # "fr" et "en", 40 mots outils chacun
  def guess_language(texts: Iterable[str], *, min_words: int = 20) -> str | None: ...
  # clean/sponsor.py
  def sponsor_removals(segs: Sequence[SponsorSegment], kept: Span) -> list[tuple[int, int, str]]: ...   # temps source
  # schemas.py
  class CleanFile(BaseModel): ...
  #   removed : genres + "sponsor", "selfpromo", "interaction" ; un retrait peut être intérieur (créateurs)
  #   + sponsorblock: Literal["ok", "none", "unavailable"] | None = None
  # highlights/popularity.py
  def source_to_clean_ms(clean: CleanFile, t_src_ms: int) -> int | None: ...   # tient compte des trous intérieurs
  def clean_to_source_ms(clean: CleanFile, t_clean_ms: int) -> int: ...
  # cut/points.py
  def sentence_points(words: Sequence[Word], sentences: Sequence[Span], *, duration_ms: int,
                      s: CreatorCutSettings) -> list[int]: ...
  def widen_sentences(t_ms: int, sentences: Sequence[Span], *, min_ms: int, max_ms: int, context: int,
                      timeline: Timeline) -> Span | None: ...
  # render/autocut.py
  def autocut_holes(span: Span, *, words: Sequence[Word], sentences: Sequence[Span], speech: Sequence[Span],
                    s: CreatorCutSettings) -> list[Span]: ...
  def episode_timeline(paths: Paths, episode_id: int, s: CreatorCutSettings) -> Timeline: ...   # trous de tout l'épisode
  def post_timelines(session: Session, paths: Paths, config: Config, post_id: int) -> list[Timeline]: ...  # un par passage
  # render/encode.py
  def render_plate(..., holes: Sequence[Span] = ()) -> None: ...                # + trous retirés
  def render_plate_multi(..., holes: Sequence[Sequence[Span]] = ()) -> None: ...
  # db/trace.py
  class PostTrace: ...   # + source_start_ms: int ; + source_end_ms: int (par clean_to_source_ms)
  ```
- Règles fixées :
  - **SponsorBlock (R46)** : `GET {url}?videoID=<id>&categories=["sponsor","selfpromo","interaction","intro","outro","poi_highlight"]&actionTypes=["skip","poi"]`
    (paramètres JSON encodés) ; 404 → `[]` ; autre `HttpError` → `None`. Lu à l'ingestion d'une vidéo YouTube de
    créateur, écrit dans `source_videos.sponsorblock_json` (`{"status": "ok"|"none"|"unavailable", "segments": […]}`) ;
    `poi_highlight` → `video_signals(kind="sponsor_highlight", t_ms=début, weight=1.0)`. Statut `unavailable` → un
    nouvel essai au nettoyage, puis nettoyage sans SponsorBlock et avertissement ; jamais une erreur.
  - `tidy_segments` : bornes ramenées dans `[0, durée]`, segments vides ou inversés retirés, chevauchements de même
    catégorie fusionnés ; si les segments `skip` couvrent plus de `max_share` de la vidéo, ils sont tous ignorés
    (données douteuses, avertissement).
  - **Nettoyage d'un créateur** (`strategy.interior_cleaning`) : bords comme pour les cartoons, puis retrait des
    segments `skip` partout, y compris au milieu. `clean.mp4` = `kept` moins les trous intérieurs, en une passe
    (`select='between(t,a0,b0)+between(t,a1,b1)…',setpts=N/FRAME_RATE/TB` et `aselect=…,asetpts=N/SR/TB` sur les
    morceaux gardés) ; `source_to_clean_ms` et `clean_to_source_ms` tiennent compte des trous (P28). Cartoons
    inchangés (R45).
  - **Langue (R21)** : après transcription, `guess_language(texte des mots)` ; au moins `language_min_words` mots et
    langue ≠ langue de la marque → `cut` et `bestof` produisent zéro passage, élément `warned` « langue détectée en
    ≠ fr (R21) : vidéo écartée » (P31). Moins de mots : aucune conclusion, découpe normale.
  - **Points de coupe d'un créateur (R24)** : entre deux phrases consécutives `i`, `i+1` dont l'écart est
    `≥ 2 × word_margin_ms`, point `fin_i + min(boundary_pad_ms, écart / 2)` ; plus 0 et la fin. `cut_point_costs`
    reçoit comme parole les intervalles des **mots** élargis de `word_margin_ms` (la parole Silero fusionne les
    pauses courtes) ; garde de 1,5 s avant un pic inchangée (R5). Sans aucun mot (vidéo sans parole = `words.json`
    présent avec zéro mot) : points des plans de la tâche 6, pas d'autocut. `words.json` absent (transcription pas
    faite ou en échec) n'est jamais « sans parole » : `cut` et `bestof` attendent la facette `words`
    (`facets_needed ⊆ facets_done`, tâches 12c et 13, `test_r24_cut_waits_for_transcription`).
  - **Durée après autocut (R6)** : `episode_timeline` calcule les trous d'autocut de tout l'épisode ; la
    programmation dynamique tourne sur les points projetés par `to_out` (un point dans un trou est projeté sur le
    début du trou), avec `duration_ms = length_ms` ; les bornes choisies sont rendues en temps `clean.mp4`. Le
    contrôle R6 de l'approbation et `pick_items` utilisent la longueur après autocut des passages de créateur.
  - **Autocut (R45)** : seulement si `strategy.autocut` ; trous = intervalles **entre deux phrases** sans parole
    Silero ni mot, plus longs que `autocut_min_gap_ms`, réduits de `autocut_keep_ms` de chaque côté ; jamais à
    l'intérieur d'une phrase. Appliqué au rendu : `render_plate(..., holes=trous ∩ passage)` ; la durée du
    `RenderJob` est celle du plan après retrait ; les incrustations sont placées en temps de sortie.
  - **Best-of d'un créateur** : points admissibles = `sentence_points` ; un moment s'élargit par
    `widen_sentences` : du début de la phrase qui précède (`context_sentences`) à la fin de la phrase du moment, puis
    des phrases suivantes tant que la longueur après autocut est sous `min_item_ms` ; au-delà de `max_item_ms`, la
    phrase de contexte saute d'abord ; aucune solution → moment écarté.
  - Traçabilité (R17) : `source_start_ms`, `source_end_ms` par `clean_to_source_ms`.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_r46_sponsorblock_categories_removed(ctx, synth):
    # créateur, 60 s ; FakeHttp : sponsor 20–30 s, poi_highlight à 40 s → clean.mp4 de 50 s (± 40 ms), signal sponsor_highlight
def test_r46_missing_sponsorblock_not_error(ctx, synth):    # 404 → status "none", étape done ; 503 → "unavailable", warned, done
def test_sponsorblock_junk_segments_tidied():               # hors bornes, inversés, chevauchants, > 50 % de la vidéo
def test_source_clean_mapping_with_interior_hole():         # t dans le trou → None ; aller-retour hors trou exact
def test_timeline_to_out_to_in_roundtrip():
def test_r24_creator_cuts_only_at_sentence_end(ctx, synth):
    # mots factices : chaque borne de partie est dans un écart entre deux phrases, jamais dans un mot ni une phrase
def test_r45_autocut_creators_only(ctx, synth):
    # mêmes mots, pause de 2 s entre deux phrases : créateur → vidéo plus courte de 2 − 0,3 s ; série dialogue_en → inchangée
def test_autocut_never_inside_sentence():                   # pause de 650 ms dans une phrase (écart < 700 ms) → gardée
def test_r6_creator_part_bounds_after_autocut(ctx, synth):  # durée de la vidéo rendue dans [65, 100] s malgré l'autocut
def test_creator_bestof_items_start_at_setup_sentence(ctx, synth):
def test_creator_video_without_speech_or_in_wrong_language(ctx, synth):
    # words.json vide → parties sur les plans, aucun trou ; 80 mots anglais pour instant_culte → 0 passage, warned « (R21) »
def test_r17_trace_source_times_with_interior_hole(ctx, synth):
def test_r16_creator_clean_and_cut_rerun_changes_nothing(ctx, synth):
```

- [ ] **Step 2 : Enregistrer** deux réponses réelles de SponsorBlock (une vidéo de Squeezie avec segments, une sans,
  donc 404) dans `tests/fixtures/sponsorblock/` par `curl` sur le Mac ; aucune donnée personnelle dedans.

- [ ] **Step 3 : Lancer** → FAIL. **Step 4 : Implémenter.** **Step 5 : Lancer** → PASS ; retirer R24, R45, R46 de
  `rules_pending.toml`. Suite rapide, ruff, mypy verts.

- [ ] **Step 6 : Commit proposé** — `feat(creators): sponsorblock removal, sentence-end cuts and render-time autocut with post-autocut durations (R24, R45, R46)`

---

### Task 25 : Recadrage sur le visage et sous-titres ASS

**Files :**
- Create : `src/rushes/reframe/faces.py`, `src/rushes/captions/speech.py`,
  `src/rushes/db/migrations/versions/0012_face_tracks.py`
- Modify : `src/rushes/media/frames.py` (`sample_bgr`), `src/rushes/reframe/path.py`, `src/rushes/reframe/choose.py`,
  `src/rushes/reframe/step.py`, `src/rushes/originality/base.py` (`RenderJob.subtitles`, `RenderPlan.ass`),
  `src/rushes/originality/advanced.py` (mots de la voix rendus par le fichier commun), `src/rushes/render/render.py`,
  `src/rushes/render/overlays.py`, `src/rushes/render/charter.py` et `config/charter.yaml` (`subtitles`),
  `src/rushes/config.py` (`FaceSettings`), `src/rushes/db/models.py`, `src/rushes/steps.py` (`faces` avant
  `reframe` dans `RUN_ORDER`)
- Test : `tests/fixtures/generator.py` (`face_tracks`, `face_hidden_ms`), `tests/fakes/models.py`
  (`FakeFaceDetector(mode="red")`), `tests/test_faces.py`, `tests/test_face_reframe.py`, `tests/test_subtitles.py`

**Interfaces :**
- Consomme : `FaceDetector`, `Box` (§5.5), `smooth_path`, `plan_shot`, `crop_width`, `plan_post`, `ShotCrop`,
  `build_ass`, `AssStyle` (35), `Timeline`, `post_timelines` (24), `VoiceFile`, `Strategy`, `Subject`.
- Produit :
  ```python
  # config.py
  class FaceSettings(_Model):
      fps: float = 2.0; width: int = 640; min_score: float = 0.8; iou_link: float = 0.3; max_gap_ms: int = 1000
      min_track_ms: int = 500; min_presence: float = 0.5; inside_share: float = 0.95; max_speed: float = 0.08
      dead_zone: float = 0.05; pair_fit_share: float = 0.8
  class Settings: ...  # + faces: FaceSettings = FaceSettings()
  # media/frames.py
  def sample_bgr(path: Path, *, fps: float, width: int, span: Span | None = None) -> Iterator[tuple[int, NDArray[np.uint8]]]: ...
  # reframe/faces.py
  @dataclass(frozen=True)
  class FacePoint: track: int; t_ms: int; box: Box          # pixels source
  def link_tracks(samples: Sequence[tuple[int, Sequence[Box]]], s: FaceSettings) -> list[FacePoint]: ...
  def face_targets(points: Sequence[FacePoint], shot: Span, *, win: int, s: FaceSettings) -> list[tuple[int, float, Box]] | None: ...
  #   (t_ms, centre x visé, boîte à garder) ; None : pas assez de visage dans le plan (R26 → action)
  def plan_face_shot(targets: Sequence[tuple[int, float, Box]], *, layout: Layout, src_w: int, src_h: int,
                     faces: FaceSettings, reframe: ReframeSettings) -> ShotCrop | None: ...
  class FacesStep(BaseStep): name = "faces"; heavy = True   # épisodes de créateur analyzed sans faces_at ;
  #   respecte Filters.episodes (voie express, tâche 28)
  # render/charter.py
  class SubtitleStyle(_Model):
      font: str; size: int = 64; primary: str = "#FFFFFF"; outline: int = 4; margin_v: int = 500; max_chars: int = 32
  class BrandCharter: ...   # + subtitles: SubtitleStyle
  # captions/speech.py
  def output_words(words: Sequence[Word], timelines: Sequence[Timeline], *, transition_ms: int) -> list[Word]: ...
  def subtitle_words(speech: Sequence[Word], voice: Sequence[Word], voice_window: Span | None) -> list[Word]: ...
  def write_subtitles(words: Sequence[Word], charter: BrandCharter, out: Path) -> Path | None: ...   # None si aucun mot
  # originality/base.py
  class RenderJob: ...   # + subtitles: tuple[Word, ...] = ()   (temps de sortie)
  class RenderPlan: ...  # + ass: str | None = None              (nom de fichier dans le dossier de travail)
  ```
- Règles fixées :
  - Migration `0012_face_tracks.py` : `face_tracks(id INTEGER PRIMARY KEY, episode_id INTEGER NOT NULL REFERENCES
    episodes(id) ON DELETE CASCADE, track INTEGER NOT NULL, t_ms INTEGER NOT NULL, x INTEGER, y INTEGER, w INTEGER,
    h INTEGER, score REAL)`, index `(episode_id, t_ms)` ; `episodes` + `faces_at TEXT` (posé même sans visage).
  - `FacesStep` : `sample_bgr` à `fps` (2 échantillons par seconde, spec) en `width` px ; YuNet (`hub.faces()`,
    taille d'entrée réglée à chaque image) ; boîtes ramenées en pixels source, `score ≥ min_score`. `link_tracks` :
    association gloutonne de proche en proche par IoU ≥ `iou_link`, trou toléré jusqu'à `max_gap_ms`, sinon nouvelle
    piste ; pistes plus courtes que `min_track_ms` jetées (faux positifs fugaces).
  - `face_targets` (par plan) : piste principale = plus grande Σ(aire × score) dans le plan ; présence = part des
    échantillons du plan où elle existe ; présence < `min_presence` → `None`. Deuxième piste présente au moins
    `min_presence` du temps et union des deux boîtes de largeur ≤ `win` pendant au moins `pair_fit_share` des
    échantillons → on vise le centre de l'union ; sinon la piste principale seule, sans jamais passer d'une piste à
    l'autre dans le plan. Échantillons manquants : interpolation linéaire si le trou fait au plus `max_gap_ms`,
    sinon dernière position.
  - `plan_face_shot` : `smooth_path` de la tâche 34 (qui ne reçoit qu'un `s: ReframeSettings`, en colonnes de
    profil) appelé avec `s=reframe.model_copy(update={"max_speed": faces.max_speed, "dead_zone": faces.dead_zone})`,
    `fps = faces.fps`, `width = src_w` et des cibles en pixels source (déplacement maximal de 8 % de la largeur par
    seconde, soit 4 % par échantillon à 2 i/s) ; réussite si la boîte visée est entièrement dans la fenêtre pendant
    au moins `inside_share` des échantillons, sinon `None`.
  - `plan_post` d'un créateur (`strategy.reframe_target == "face"`) : par plan, `plan_face_shot` si
    `face_targets` n'est pas `None`, sinon le plan d'action de la tâche 34 (R26) ; une mise en page échoue si un plan
    échoue → mise en page suivante dans l'ordre de R8. Mise en page de départ : `initial_layout` (34), comme pour les
    cartoons (décision P27). Pas de panoramique à travers une coupe (R8).
  - Sous-titres : `strategy.subtitles == "always"` (créateurs, R25), ou `"dialogue"` et série `audio ==
    "dialogue_en"` (R10, langue de la marque, R21). Mots de `words.json` dans les passages du post, ramenés en temps
    de sortie (`output_words` : autocut de la tâche 24 ; pour un best-of, chaque élément décalé de la somme des
    longueurs précédentes moins `transition_ms` par transition). Module avancé : mots de parole qui chevauchent la
    fenêtre de la voix retirés, mots de la voix ajoutés (`subtitle_words`) ; un seul fichier `subs.ass` par post,
    qui remplace le `voice.ass` de la tâche 35.
  - Style : `build_ass` (35) avec `charter.subtitles` et `highlight` = `charter.highlight` ; mot courant surligné,
    2 lignes au plus, `max_chars` par ligne ; `PlayResX/Y = 1080/1920`, `Alignment=2`, `MarginV = margin_v` (bas du
    texte à 1 420 px, au-dessus de la marge basse de 480 px), `MarginL = safe.left + 20`, `MarginR = safe.right + 20`
    (R9). Le bandeau (230–390 px) et l'accroche (460–660 px, correction de la phase 1) restent en haut : aucun
    chevauchement avec les sous-titres.
  - `render_overlays` applique `ass=subs.ass` dans le dossier de travail (`cwd`, comme le recadrage) ; la version
    d'incrustation passe à `ov-2` (rendu léger des posts existants).
  - `RUN_ORDER` : `faces` juste avant `reframe`.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_link_tracks_iou_gaps_and_short_tracks():
def test_r26_creator_crop_follows_face(ctx, synth):
    # visage rouge qui traverse l'image en 10 s, square → visage dans la fenêtre ≥ 95 % des images ; saut maximal entre
    # deux images consécutives d'un plan ≤ 5 % de la largeur source (lignes de crop.cmd)
def test_r26_no_face_falls_back_to_action(ctx, synth):   # aucun visage, cible mobile → même chemin que la tâche 34
def test_face_reframe_two_faces_and_flicker_no_jumps(ctx, synth):
    # deux visages proches → centre de l'union ; deux visages aux bords → le plus grand, sans saut ; tache de 0,3 s ignorée
def test_face_layout_fallback_order(ctx, synth):         # visage trop large pour square → portrait, puis full (R8)
def test_faces_step_marks_done_without_faces(ctx, synth):
def test_r25_creator_subtitles_highlight_current_word_two_lines(ctx, synth):
    # subs.ass : un Dialogue par mot, balise de surlignage, ≤ 2 lignes, marges dans la zone sûre (R9)
def test_r10_dialogue_cartoon_gets_subtitles_in_brand_language(ctx, synth):   # dialogue_en → subs.ass ; nonverbal → aucun
def test_subtitles_follow_autocut_and_bestof_offsets():
def test_voice_window_replaces_speech_subtitles():
def test_r16_faces_rerun_changes_nothing(ctx, synth):
@pytest.mark.slow
def test_yunet_tracks_face_in_cc0_clip(tmp_path):        # vidéo bâtie sur tests/fixtures/images/face_cc0.jpg qui glisse
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS ; retirer R10, R25, R26 de
  `rules_pending.toml`. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Mesurer** — temps de `faces` par minute de vidéo sur le serveur, noté dans `docs/decisions.md`.

- [ ] **Step 6 : Commit proposé** — `feat(reframe,captions): face-following creator crops and word-highlight ass subtitles (R10, R25, R26)`

---

### Task 26 : Crédits et campagnes

**Files :**
- Create : `src/rushes/meta/credit.py`, `src/rushes/creators/__init__.py`, `src/rushes/creators/campaign.py`,
  `src/rushes/db/migrations/versions/0013_campaign_posts.py`
- Modify : `src/rushes/config.py` (`CreatorConfig.handles`, validations), `src/rushes/meta/caption.py`,
  `src/rushes/meta/meta.py`, `src/rushes/schemas.py` (`MetaFile.credit_fingerprint`), `src/rushes/package/outbox.py`,
  `src/rushes/package/upload.py`, `src/rushes/ops/report.py` (sections), `src/rushes/db/models.py`,
  `src/rushes/cli.py` (`campaign list`, `campaign submitted`), `src/rushes/serve/schemas.py` (`CampaignView`),
  `src/rushes/serve/app.py` (routes de campagne), `config/creators/*.yaml` (`credit_template: "🎥 {name}"`)
- Create (front) : `web/src/components/CampaignToSubmit.tsx` ; Modify (front) : `web/src/components/AccountColumn.tsx`,
  `web/openapi.json` et `web/src/api/schema.d.ts` (régénérés), `web/tests/smoke.spec.ts`
- Test : `tests/test_credit.py`, `tests/test_campaign.py`, `tests/test_serve_campaign.py`, `tests/test_cli.py`
  (`campaign list` et `campaign submitted` passent de `PENDING` à `IMPLEMENTED`)

**Interfaces :**
- Consomme : `build_caption`, `caption_problems`, `resolve_hashtags`, `build_upload`, `Subject` (22),
  `derived.resolve`, `DAILY_SECTIONS`, `WEEKLY_SECTIONS` (20), `posts.platform_url` (17), `create_app`,
  `require_same_origin`, `ActionConflict`, `record_action` (36), fixtures `api` et `make_api(...)` de
  `tests/conftest.py` (36).
- Produit :
  ```python
  # config.py
  class CreatorConfig: ...   # + handles: dict[Platform, str] = {}   (amorce : comptes du créateur, sinon déduits en 27 ;
  #   servent seulement à identifier le créateur sur Instagram, jamais dans la légende, L19)
  # meta/credit.py
  @dataclass(frozen=True)
  class CreditRequirements:
      credit: str; mentions: tuple[str, ...]; hashtags: tuple[str, ...]; tag_account: str | None
      campaign_url: str | None
  def creator_handle(session: Session, config: Config, creator_id: str, platform: Platform) -> str | None: ...
  def requirements(session: Session, config: Config, creator_id: str, platform: Platform) -> CreditRequirements: ...
  def credit_problems(caption: str, hashtags: Sequence[str], req: CreditRequirements) -> list[str]: ...   # R23
  def credit_fingerprint(req: CreditRequirements) -> str: ...      # sha1 hex[:12] du JSON trié
  # schemas.py
  class MetaFile(BaseModel): ...   # + credit_fingerprint: str | None = None
  # creators/campaign.py
  @dataclass(frozen=True)
  class CampaignRow:
      post_key: str; account_id: str; creator: str; url: str | None; campaign_url: str
      published_at: datetime | None; submitted_at: datetime | None
  def record_campaign_post(session: Session, post_id: int, req: CreditRequirements, *, at: datetime) -> None: ...
  def campaign_rows(session: Session, *, since: datetime | None, unsubmitted_only: bool) -> list[CampaignRow]: ...
  def mark_submitted(session: Session, post_id: int, *, at: datetime) -> bool: ...   # False si déjà soumis ; ActionConflict
  #   « pas un post de campagne » ou « pas encore publié » ; sinon submitted_at = at et
  #   record_action(kind="submitted", value=None, origin="page", at=at) (aucune migration : 0008 prévoit 'submitted')
  # serve/schemas.py
  class CampaignView(BaseModel):
      post_id: int; key: str; account_id: str; creator: str; platform_url: str | None; campaign_url: str
      published_at: datetime
  ```
- Règles fixées :
  - Validation de la config : `handles` et `campaign.required_mentions` de forme `^@[\w.]{2,30}$` ;
    `campaign.required_hashtags` normalisés, valides, jamais `#viral`/`#foryou`, sans `#fyp` (Instagram) ;
    `credit_template` n'utilise que `{name}` (un `{mention}` restant → ConfigError « le crédit cite le nom du
    créateur : remplacer {mention} par {name} ») ; pour chaque plateforme,
    `|hashtags obligatoires de creators ∪ hashtags de la campagne| ≤ 5`, sinon erreur « R13 et R23 incompatibles :
    6 hashtags exigés pour tiktok ».
  - Crédit (décision de Louis, L19) = `credit_template.format(name=display_name)` : toujours le nom du créateur,
    jamais son @. Créateur sans YAML (prospect) : gabarit `"🎥 {name}"`, aucune mention ni campagne ; R23 vérifiée
    de la même façon. `creator_handle` : `handles[plateforme]` du YAML > valeur dérivée `creator.handles` (27) > `None`,
    utilisé seulement pour `tag_account` ; mentions = `required_mentions` absentes du
    crédit ; hashtags exigés = `required_hashtags` ; `tag_account` = handle Instagram s'il existe (Instagram
    seulement).
  - Légende (R23) : crédit puis mentions à la place `credit` de `build_caption` ; hashtags : obligatoires de R13,
    puis ceux de la campagne, puis la réserve jusqu'à 5. Le raccourcissement TikTok (phase 1) ne retire jamais
    crédit, mentions ni hashtags : il retire la situation, la question, puis l'accroche ; au-delà de 150 caractères
    la légende est gardée avec un avertissement (R23 prime sur la longueur visée, R12 vérifiée quand même, P30).
  - `credit_problems` : crédit absent (sous-chaîne exacte), mention absente (insensible à la casse, frontière de
    mot) ou hashtag exigé absent → une raison par manque (« R23 : mention @x absente »). `meta` : raisons → élément
    `failed`, légende non écrite ; `package` refait le contrôle sur la légende en base et refuse d'écrire le dossier
    (`failed`).
  - Changement de campagne : `meta.json.credit_fingerprint` ≠ empreinte courante → `meta` reprend les posts
    `rendered`/`ready` non publiés (légende effacée, statut `rendered`, `planned_at` et `ready_at` conservés),
    `package` les réécrit. Un post `published` n'est jamais modifié. Campagne retirée du YAML → `meta` supprime aussi
    la ligne `campaign_posts` de ces posts non publiés (ils n'apparaîtront jamais « À soumettre ») ; celle d'un post
    publié reste.
  - `upload.json` : TikTok `mentions: []` ; Instagram `mentions: [tag_account]` s'il existe ; `campaign` =
    `{"url", "required_mentions", "required_hashtags", "note": "Vérifier l'étiquette de contenu de marque selon les
    règles de la campagne"}` ; checklist + « Identifier {tag_account} » (Instagram) et « Après publication :
    soumettre le lien à la campagne ({url}) ».
  - Migration `0013_campaign_posts.py` : `campaign_posts(post_id INTEGER PRIMARY KEY REFERENCES posts(id),
    campaign_url TEXT NOT NULL, required_json TEXT NOT NULL, recorded_at TEXT NOT NULL, submitted_at TEXT)`.
    `package` l'écrit pour un post non test d'un créateur qui a une campagne (R28) ; un post de vitrine n'y entre
    jamais.
  - Rapports : quotidien « Posts de campagne à soumettre » (publiés, `submitted_at` nul, avec `platform_url` ou
    « URL inconnue : la retrouver dans l'appli ») ; hebdomadaire « Campagnes » (publiés dans la semaine, URL, soumis
    ou non). `rushes campaign list [--all]` affiche le tableau.
  - Soumission (décision de Louis, L16) : depuis la page de `serve`, section « À soumettre » en tête de chaque
    colonne de compte (posts publiés de `campaign_posts` sans `submitted_at`), avec liens « Voir le post »
    (`platform_url`) et « Ouvrir la campagne », et bouton « Soumis ». Routes : `GET /api/campaign?account=` →
    `list[CampaignView]` ; `POST /api/posts/{id}/submitted` (`require_same_origin`) → 200, idempotent (déjà
    soumis : 200 sans écriture), 409 avec le message de `ActionConflict`. `rushes campaign submitted <p_key>` reste
    en secours. Composant shadcn, `frontend-design` puis `break-ui` et `web-design-guidelines` avant de livrer.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_r23_caption_without_credit_refused(ctx):          # légende modifiée en base sans crédit → package failed, aucun dossier
def test_r23_campaign_mentions_required(ctx, edit_yaml):   # mention @a et #x exigés → présents ; légende privée de @a → meta failed
def test_credit_always_uses_creator_name(ctx, edit_yaml):   # handles TikTok et Instagram connus → légende « 🎥 Squeezie », aucun @squeezie
def test_credit_template_with_mention_refused(config_dir, edit_yaml):
def test_r23_prospect_credit_defaults_to_name(ctx):  # créateur yt_mrbeast sans YAML → légende « 🎥 MrBeast », credit_problems vide
def test_instagram_tag_account_from_derived_handles(ctx):   # la ligne derived creator.handles est insérée par le test (la tâche 27 l'écrira)
def test_campaign_hashtags_count_within_r13(config_dir, edit_yaml):   # 2 obligatoires + 4 de campagne → ConfigError « R13 et R23 »
def test_long_caption_keeps_credit_over_150(ctx):
def test_instagram_upload_tags_creator_account(ctx):
def test_campaign_change_repackages_unpublished_posts(ctx, edit_yaml):
    # ajout d'un hashtag de campagne : posts ready → nouvelle légende et nouveau paquet, même planned_at ; post published intact ;
    # puis campagne retirée : plus aucune ligne campaign_posts pour les posts non publiés, celle du post published gardée
def test_r28_campaign_post_listed_in_weekly_report(ctx):
def test_daily_report_lists_campaign_posts_to_submit(ctx):
def test_campaign_submitted_cli(cli_env):
# tests/test_serve_campaign.py
def test_campaign_lists_published_unsubmitted_posts(make_api):   # make_api(...) de tests/conftest.py (36) + lignes campaign_posts
def test_campaign_submitted_from_page_is_idempotent(api):
    # deux clics → 200 et 200 ; une seule ligne post_actions(kind="submitted", origin="page")
def test_campaign_submit_conflicts(api):               # post de vitrine ou non publié → 409 avec message
def test_campaign_submit_requires_same_origin(api):
def test_showcase_post_never_recorded_as_campaign(ctx):    # créateur non autorisé avec campagne → aucune ligne campaign_posts
def test_r16_meta_with_campaign_rerun_changes_nothing(ctx):
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS ; retirer R23, R28 de
  `rules_pending.toml`. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(meta,creators): creator credits by name, campaign requirements and submission from the page (R23, R28)`

---

### Task 27 : Prospection et instantanés

**Files :**
- Create : `src/rushes/net/rss.py`, `src/rushes/creators/channel.py`, `src/rushes/creators/prospect.py`,
  `src/rushes/creators/snapshot.py`, `src/rushes/db/migrations/versions/0014_channel_snapshots.py`
- Modify : `src/rushes/config.py` (`ProspectSettings`, `CreatorConfig.youtube_channel` facultatif),
  `src/rushes/db/sync.py` (créateurs prospectés gardés, miroir de `channels.status`, rattachement),
  `src/rushes/ingest/owner.py` (créateur prospecté), `src/rushes/db/models.py`, `src/rushes/steps.py` (profil
  `weekly`), `src/rushes/selectors.py` (classement de `creators_prospect` et `creators_snapshot`), `src/rushes/cli.py`
  (`creators prospect`, `creators snapshot`, `creators list`), `src/rushes/ops/report.py` (section « Prospection »)
- Test : `tests/test_rss.py`, `tests/test_channel_links.py`, `tests/test_prospect.py`, `tests/test_snapshot.py`,
  `tests/fixtures/rss/*.xml`, cassettes, `tests/test_cli.py` (les trois commandes `creators` passent de `PENDING` à
  `IMPLEMENTED`)

**Interfaces :**
- Consomme : `channel_search_url`, `rank_channels` (10a), `video_score`, `guess_language` (24), `derived.record/resolve`,
  `Right` (0002), `choose_owner`, `is_test_post`, `Http.get_text`, `rushes.config.CHANNEL_ID` (tâche 33, jamais
  redéfini), `selectors` (22).
- Produit :
  ```python
  # config.py
  class ProspectSettings(_Model):
      queries: dict[str, list[str]] = {"fr": ["vlog", "défi", "réaction", "podcast"],
                                       "en": ["vlog", "challenge", "reaction", "podcast"]}
      search_results: int = 20; min_followers: int = 50_000; follow_threshold: float = 0.6
      max_follow_per_week: int = 5; names_per_week: int = 10; recent_n: int = 10; history_n: int = 50
      history_days: int = 30; cadence_ref_per_week: float = 2.0; growth_ref: float = 0.02; candidate_weeks: int = 8
      weights: dict[str, float] = {"recent": 0.5, "cadence": 0.2, "growth": 0.3}
  class Settings: ...  # + prospect: ProspectSettings = ProspectSettings()
  class CreatorConfig: ...   # youtube_channel: str | None = None  (amorce ; "UC..." traité comme absent)
  # net/rss.py
  @dataclass(frozen=True)
  class FeedEntry: video_id: str; published: datetime; title: str; views: int | None; description: str
  def feed_url(channel_id: str) -> str: ...      # https://www.youtube.com/feeds/videos.xml?channel_id=<id>
  def parse_feed(xml: str) -> list[FeedEntry]: ...
  # creators/channel.py   (from rushes.config import CHANNEL_ID : ^UC[\w-]{22}$, tâche 33)
  @dataclass(frozen=True)
  class ChannelLinks: youtube_handles: tuple[str, ...]; youtube_ids: tuple[str, ...]; tiktok: tuple[str, ...]; instagram: tuple[str, ...]
  def links_in(text: str) -> ChannelLinks: ...
  def guest_names(title: str) -> list[str]: ...  # « ft. X », « feat. X », « avec X », « with X »
  def resolve_creator_channel(ctx: Context, session: Session, creator: CreatorConfig) -> str | None: ...
  # creators/prospect.py
  @dataclass(frozen=True)
  class Momentum: score: float; recent: float | None; cadence: float | None; growth: float | None
  def momentum(feed: Sequence[FeedEntry], listing_views: Sequence[int], subscribers: Sequence[tuple[date, int]], *,
               today: date, s: ProspectSettings) -> Momentum: ...
  def prospect_creator_id(handle: str | None, channel_id: str) -> str: ...   # "yt_" + slug du handle, sinon de l'id
  class ProspectStep(BaseStep): name = "creators_prospect"   # éléments : langues des marques creators
  # creators/snapshot.py
  class SnapshotStep(BaseStep): name = "creators_snapshot"   # éléments : chaînes candidate, followed, authorized
  # selectors.py : creators_prospect et creators_snapshot dans FILTERABLE_STEPS (aucun élément si
  #   vertical == "cartoons")
  ```
- Règles fixées :
  - Migration `0014_channel_snapshots.py` : `channel_snapshots(channel_id INTEGER NOT NULL REFERENCES channels(id),
    taken_on TEXT NOT NULL, subscribers INTEGER, total_views INTEGER, video_count INTEGER, PRIMARY KEY (channel_id,
    taken_on))` ; `channels` + `title TEXT`, `handle TEXT`, `language TEXT`, `subscribers INTEGER`,
    `momentum_json TEXT`, `reason TEXT`, `last_prospected_at TEXT` ; `creators.channel_id` devient nullable
    (`batch_alter_table`) : `db/sync.py` y écrit `youtube_channel` s'il respecte `CHANNEL_ID`, sinon la chaîne
    résolue (`youtube.creator_channel`), sinon NULL (le modèle ORM suit : `Mapped[str | None]`).
  - Chaîne d'un créateur de la config (P26) : `youtube_channel` qui respecte `CHANNEL_ID` > valeur dérivée
    `youtube.creator_channel` > recherche de chaînes sur `display_name` (10a : vérifiée, `name_sim ≥ 80`, plus
    d'abonnés) enregistrée par `record("youtube.creator_channel", id, …, source="channel_search")` ; rien de sûr →
    avertissement « chaîne de squeezie introuvable : préciser youtube_channel ». Titre de la chaîne →
    `youtube.channel_title`.
  - Algorithme (hebdomadaire, profil `weekly`) :

```text
1. semences = créateurs de la config (chaîne résolue) ; channels upsert, discovered_via "config" :
   ligne absente → status "followed" (vitrine, P25) ; status d'une ligne existante jamais touché ("authorized"
   est le miroir du YAML posé par db/sync.py, au prochain sync pour une ligne neuve, R32)
2. pour chaque chaîne followed/authorized : flux RSS (15 vidéos) + 5 vidéos complètes les plus récentes →
   links_in(descriptions) : chaînes citées (@handle, /channel/UC…) ; guest_names(titres) → recherche de chaînes,
   names_per_week noms au plus ; tiktok/instagram de la description de la chaîne →
   record("creator.handles", id, {"tiktok": "@…", "instagram": "@…"})
3. recherches de la semaine : pour chaque langue L des marques creators, chaque requête de queries[L]
   (+ mots-clés dérivés market.keywords de L : point d'accroche, ajouté par un « Modify » de la tâche 31) :
   ytsearch filtré « cette semaine » (sp=EgIIAw%3D%3D), search_results résultats → chaînes des vidéos ;
   les comptes TikTok ne sont pas prospectés (rushes a besoin d'une source YouTube)
4. chaque chaîne nouvelle : onglet /videos à plat (history_n) + flux RSS + abonnés ;
   langue = guess_language(titres) ; langue absente des marques creators → rejected (reason « langue ») ;
   abonnés < min_followers → rejected (reason « trop petite ») ; sinon candidate
5. momentum : recent = clamp(log10(1 + r) / log10(5), 0, 1) avec r = médiane des vues/jour des recent_n dernières
   vidéos du flux âgées d'au moins 2 jours ÷ max(1, médiane des vues de la liste / history_days) ;
   cadence = min(1, vidéos des 28 derniers jours / (4 × cadence_ref_per_week)) ;
   growth = clamp(gain relatif d'abonnés sur 7 jours (instantanés) / growth_ref, 0, 1) ;
   score = moyenne pondérée des composantes présentes (poids renormalisés) ; calculé pour les candidates et aussi
   pour les chaînes followed/authorized (semences comprises) → channels.momentum_json ; la tâche 38 en tire la
   priorité des droits des créateurs de la config (score déjà dans [0, 1])
6. candidates classées par score : score ≥ follow_threshold → followed, max_follow_per_week par semaine ;
   ligne creators (id prospect_creator_id, language, channel_id, authorized 0, origin "prospect", status "candidate") ;
   ligne rights (creator, to_request, priority = score) ;
   record("youtube.channel_title", id, titre de la chaîne) (nom affiché, crédit et hashtag du prospect, tâches 22 et 26)
7. candidate restée sous le seuil candidate_weeks semaines → rejected (reason « dynamique faible »)
```

  - R32 : `channels.status = "authorized"` n'est écrit que par `db/sync.py`, miroir du YAML ; aucune commande ne
    l'écrit ; un créateur prospecté n'a pas de YAML, donc `is_test_post` vrai et `outbox_test/` (R22).
  - Rattachement (`db/sync.py` : `rekey_subject(session: Session, kind: Literal["series", "creator"], old_id: str,
    new_id: str) -> None`, réutilisé par la tâche 38) : un YAML dont la chaîne est celle d'un créateur prospecté
    (identifiant différent) → `sync` réécrit dans une transaction `creators`, `channels.creator_id`, `episodes.creator_id`, `source_videos.creator_id`,
    `rights.subject_id` et la portée des `derived_values` vers l'identifiant du YAML (décision P32). Une table créée
    plus tard qui porte un sujet (`market_accounts.subject_id`, 0016) est ajoutée à cette liste par la tâche 38, avec
    son test.
  - `choose_owner` : un créateur absent de `creators_by_brand` (prospecté) → marques creators de sa langue.
  - `creators snapshot` : une ligne par chaîne et par jour (`taken_on`), abonnés de l'onglet chaîne, nombre de
    vidéos et vues totales si yt-dlp les donne (sinon NULL).
  - `creators list` : tableau chaîne, créateur, statut, langue, abonnés, dynamique, origine, raison. Rapport
    hebdomadaire « Prospection » : nouvelles chaînes suivies et rejetées de la semaine.
  - Cassettes : recherche de chaînes « Squeezie » et « Byilhan », `/videos` (`1:50`) de chacune, une recherche
    `ytsearch20` filtrée « cette semaine » en français ; flux RSS enregistrés par `curl` dans
    `tests/fixtures/rss/`.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_parse_feed_from_recorded_rss():
def test_links_and_guest_names_parsed():
@pytest.mark.parametrize(("feed", "views", "subs", "expected"), MOMENTUM_CASES)   # 3 cas à la main, dont croissance absente
def test_momentum_reference_values(feed, views, subs, expected):
def test_config_creator_channel_resolved_by_search(ctx):   # youtube_channel "UC..." → cassette → youtube.creator_channel
def test_config_channel_id_wins(ctx, edit_yaml):
def test_prospect_ranking_reproducible_on_recorded_data(ctx):
def test_wrong_language_or_small_channel_rejected(ctx):
def test_followed_prospect_becomes_candidate_creator_with_rights_row(ctx):
    # + derived youtube.channel_title du prospect = titre de la chaîne (cassette) ; display_name → ce titre
def test_seed_channels_get_momentum(ctx):                   # squeezie et byilhan : channels.momentum_json non nul après le run
def test_r32_prospect_never_changes_existing_channel_status(ctx):
    # chaîne squeezie "authorized" (miroir de sync) → inchangée après creators_prospect ; ligne neuve → "followed"
def test_r32_prospect_never_publishable_without_yaml(ctx, synth):
    # créateur prospecté suivi, épisode découpé et rendu → posts is_test, outbox_test/ ; channels.status ≠ "authorized"
def test_only_sync_writes_authorized_channel_status():     # recherche dans src/ : seul db/sync.py écrit "authorized" dans channels
def test_yaml_for_prospect_rekeys_rows(ctx, edit_yaml):
def test_prospect_owner_by_language(ctx):                   # créateur anglais prospecté → compte de peak_moments
def test_handles_recorded_from_channel_links(ctx):
def test_snapshot_one_row_per_channel_per_day(ctx):
def test_r16_prospect_rerun_same_week_changes_nothing(ctx):
```

- [ ] **Step 2 : Enregistrer les cassettes et les flux** (liste ci-dessus) ; relire : aucun cookie, aucune URL signée.
  Noter dans `docs/decisions.md` les champs que la liste à plat fournit réellement (date de publication, nombre de
  vidéos) ; l'algorithme n'utilise que le flux RSS pour les dates.

- [ ] **Step 3 : Lancer** → FAIL. **Step 4 : Implémenter.** **Step 5 : Lancer** → PASS ; retirer R32 de
  `rules_pending.toml`. Suite rapide, ruff, mypy verts.

- [ ] **Step 6 : Commit proposé** — `feat(creators): weekly prospecting with momentum score, channel snapshots and yaml-only authorization (R32)`

---

### Task 28 : Catalogue, veille 6 h, voie express et second passage

**Files :**
- Create : `src/rushes/creators/backfill.py`, `src/rushes/creators/watch.py`, `src/rushes/creators/express.py`,
  `src/rushes/creators/revisit.py`, `src/rushes/db/migrations/versions/0015_creators_watch.py`
- Modify : `src/rushes/ingest/select.py` (créateurs, budget vitrine), `src/rushes/ingest/ingest.py`
  (`ingest_source`), `src/rushes/discover/signals.py` (`quote_signals`), `src/rushes/cut/bestof.py` (citations des
  commentaires ; le retrait réutilise `withdraw_bestof`), `src/rushes/config.py` (`CreatorsSettings`, `BestofSettings.level1_weights`),
  `src/rushes/steps.py` (`RUN_ORDER`, profil `watch`), `src/rushes/selectors.py` (classement de `creators_backfill`,
  `creators_watch`, `creators_revisit`), `docker/systemd/rushes-watch.service`, `src/rushes/status.py`
  (« Chaînes surveillées »), `src/rushes/cli.py` (`creators backfill`, `creators watch`, `creators express`,
  `creators revisit`), `src/rushes/db/models.py`
- Test : `tests/test_backfill.py`, `tests/test_watch.py`, `tests/test_express.py`, `tests/test_revisit.py`,
  modifier `tests/test_systemd_units.py`, `tests/test_profiles.py`, `tests/test_cli.py` (les quatre commandes
  `creators` passent de `PENDING` à `IMPLEMENTED`)

**Interfaces :**
- Consomme : `parse_feed`, `feed_url` (27), `video_score`, `upsert_source_video`, `comment_timestamps`,
  `heatmap_signals` (10a), `YtDlp.extract(..., cookies_allowed=False)` (§5.8), `select_downloads`, `stock_days`
  (10b), `withdraw_bestof` (15), `express_slot` (`queue/slots.py`, 16b), `Config.slots_for`, `run_pipeline(...,
  lock, yield_to_express)` (2), `express_intent`, `heavy_lock`, `HeavyLock` (§5.11), `STEPS`,
  `Filters.episodes`/`posts` (22, 36), `collector_health`, `send_pending_alerts` (20), `find_moments` (13),
  `Strategy`, `selectors` (22).
- Produit :
  ```python
  # config.py
  class CreatorsSettings(_Model):
      backfill_channels_per_run: int = 4; backfill_listing: int = 200; backfill_extract_per_run: int = 20
      backfill_comments: int = 100; backfill_keep: int = 20; refresh_days: int = 7
      watch_silent_h: int = 24; new_max_age_h: int = 72; express_comments: int = 200; express_parts: int = 2
      revisit_after_h: tuple[int, int] = (24, 72); revisit_min_gain: float = 0.10; overdue_h: int = 6
  class Settings: ...  # + creators: CreatorsSettings = CreatorsSettings()
  class BestofSettings: ...  # level1_weights + "quote": 0.8
  # discover/signals.py
  def quote_signals(comments: Sequence[Mapping[str, Any]], sentences: Sequence[Span], words: Sequence[Word], *,
                    min_ratio: float = 85.0) -> list[tuple[int, float]]: ...   # (t_ms clean, poids)
  # ingest/select.py
  def showcase_budget_left(session: Session, config: Config, *, today: date) -> int: ...   # prospects (séries 38 et créateurs)
  # ingest/ingest.py
  def ingest_source(ctx: Context, session: Session, source_video_id: int) -> int | None: ...   # épisode, téléchargement immédiat
  #   (cookies en secours seulement, L32) ; vidéo déjà ingérée → épisode existant renvoyé, sans téléchargement
  # creators/backfill.py
  class BackfillStep(BaseStep): name = "creators_backfill"   # éléments : chaînes à rafraîchir
  # creators/watch.py
  class WatchStep(BaseStep): name = "creators_watch"         # éléments : chaînes followed et authorized
  # creators/express.py
  EXPRESS_ANALYSIS: tuple[str, ...] = ("clean", "analyze", "transcribe", "sounds", "cut", "bestof", "hooks",
                                       "approve", "assign", "queue", "faces")   # faces filtré par Filters.episodes
  EXPRESS_RENDER: tuple[str, ...] = ("reframe", "commentary", "voice", "render", "meta", "package")
  def pending_express(session: Session) -> list[int]: ...    # source_videos express_requested_at posé, express_done_at nul
  def express_posts(session: Session, ctx: Context, episode_ids: Sequence[int], *, parts: int,
                    now: datetime) -> tuple[list[int], list[Message]]: ...   # posts à rendre ; warn sans créneau
  def run_express(ctx: Context, *, video: str | None) -> int: ...   # code de sortie (§5.4)
  # creators/revisit.py   (retrait par withdraw_bestof de la tâche 15)
  class RevisitStep(BaseStep): name = "creators_revisit"     # vidéos nouvelles arrivées à 24 h ou 72 h
  # selectors.py : creators_backfill, creators_watch, creators_revisit dans FILTERABLE_STEPS (aucun élément si
  #   vertical == "cartoons")
  ```
- Règles fixées :
  - « Priorité processeur basse, une vidéo à la fois » (spec) : assurée par `nice -n 10` de l'entrypoint (tâche 9),
    les limites du conteneur et le traitement des vidéos en attente l'une après l'autre, étape par étape ; aucun
    réglage de plus.
  - Migration `0015_creators_watch.py` : `channels` + `last_checked_at`, `last_rss_ok_at`, `last_listed_at`,
    `last_video_at`, `last_backfill_at` (TEXT) ; `source_videos` + `comments_json TEXT` (200 commentaires au plus :
    texte, likes, horodatage cité), `express_requested_at`, `express_done_at`, `revisited_24_at`, `revisited_72_at`.
  - Créateurs suivis (P25) : tous ceux de la config (autorisés ou non) et les prospectés `followed` ; ces derniers
    n'ingèrent que dans la limite de `showcase_budget_left` (épisodes de prospects ingérés dans la semaine ISO
    < `showcase.max_episodes_per_week`).
  - **Catalogue** (`creators_backfill`, dans `RUN_ORDER` juste après `discover_youtube`) : les
    `backfill_channels_per_run` chaînes au `last_backfill_at` le plus ancien ; `/videos` à plat
    (`playlist_items 1:{backfill_listing}`, au plus tous les `refresh_days`) → `upsert_source_video(kind="official",
    creator_id, via="backfill")` ; durées hors `[min_duration_s, max_duration_s]` → `rejected` ; extraction complète
    (`comments=backfill_comments`, heatmap) des `backfill_extract_per_run` meilleures par vues jamais extraites →
    `video_signals` (`heatmap`, `comment_ts`), `comments_json` ; score `video_score` (vues/jour, surperformance sur la
    médiane de la chaîne, engagement) ; `retained` pour les `backfill_keep` meilleures. `select_downloads` traite les
    créateurs comme les séries : besoin d'un créateur = somme des besoins des comptes de ses marques
    (`creators_by_brand`, ou langue pour un prospect).
  - **Veille** (`creators_watch`, R29) : chaque chaîne suivie est contrôlée à chaque passage (toutes les 6 h) :

```text
flux RSS (Http.get_text(feed_url)) → entrées ; succès → last_rss_ok_at = now
échec du flux, ou entrée la plus récente plus vieille que watch_silent_h et last_listed_at plus vieux que
  watch_silent_h → liste yt-dlp /videos à plat (playlist_items 1:5) ; last_listed_at = now
vidéo « nouvelle » = inconnue, ou connue (même par creators_backfill) avec express_requested_at nul et sans
  épisode ; entrées du flux : publiées depuis moins de new_max_age_h ; liste à plat (sans date) : au plus 5
  candidates, la date vient de l'extraction ci-dessous, puis même filtre new_max_age_h (plus vieille → rien)
chaque nouvelle → extraction complète (comments=express_comments, sans cookies) :
  live_status is_live / is_upcoming / post_live en cours de traitement → ignorée (reprise au passage suivant)
  durée ≤ 60 s (Short) ou hors bornes → rejected
  sinon source_video (inconnue : via "rss" ou "channel" ; connue : via gardé), comments_json, signaux,
  express_requested_at = now
last_checked_at = now ; last_video_at = publication la plus récente vue
flux et liste en échec → chaîne warned ; collector_health["youtube_watch"] mis à jour une fois par run (finish) : échec
  si toutes les chaînes contrôlées ont échoué, succès sinon (alerte au 2e échec consécutif, tâche 20)
```

  - **Voie express** (`rushes creators express [VIDEO]`, R30) : `VIDEO` = URL, identifiant YouTube ou `v_key` ;
    sans argument, `pending_express`. Verrous (§5.11, D1) : toute la commande tourne dans
    `with express_intent(paths), heavy_lock(paths, wait=True) as hl:` (le run de nuit ou de veille cède entre deux
    éléments) ; les deux `run_pipeline` reçoivent `lock=hl, yield_to_express=False` (jamais un second `open()` de
    ses propres verrous, jamais de cession à elle-même) :

```text
1. pour chaque vidéo (une à la fois) : ingest_source → épisode (déjà ingérée → épisode existant, sans téléchargement)
   prospect hors budget vitrine → express_done_at = now, Message info « hors budget vitrine », jamais retentée
   échec de téléchargement → élément failed, les autres continuent
2. run_pipeline(EXPRESS_ANALYSIS, Filters(episodes=E), command="creators express", lock=hl,
   yield_to_express=False) : aucune étape sautée (approbation R3/R33, registre R1) ; faces en dernier, sur E
   seulement ; vision et re-sourçage attendent le run de nuit
3. P, warns = express_posts(E, parts=express_parts) : pour chaque épisode, dans l'ordre best-of, partie 1, …,
   partie express_parts, les posts planned de l'épisode :
     planned_at déjà posé par queue (créneau normal libre) → gardé ;
     sinon express_slot(slots = créneaux résolus du compte (16b), tz = fuseau de la marque, now,
       taken = planned_at des posts du compte, extra_taken = planned_at de ses posts express_slot = 1)
       → planned_at et posts.express_slot = 1 (créneau en plus de posts_per_day, au plus +1 par jour local et par
       compte, le jour même ou le lendemain ; décision L27) ;
     une partie n > 1 n'est prise que si la partie n − 1 a un planned_at antérieur (parties dans l'ordre, spec) ;
     les posts ready ne bougent jamais ; post sans créneau → non rendu, Message warn « aucun créneau express libre :
     extraits rendus au prochain run de nuit »
4. run_pipeline(EXPRESS_RENDER, Filters(posts=P, episodes=E), command="creators express", lock=hl,
   yield_to_express=False)
5. express_done_at = now ; épisode avec au moins un post ready → Message("info", "Squeezie : « titre », 3 extraits
   prêts", alert=f"creator_video:{v_key}")
fin de commande : send_pending_alerts(ctx, messages de run_express) (tâche 20 ; dispatch_alerts n'est appelé par
  run_pipeline que pour ses propres messages)
code de sortie : combine_exit_codes(exit_code_for([], erreurs d'ingestion de l'étape 1), code du run_pipeline
  d'analyse, code du run_pipeline de rendu) (tâche 2)
```

    Le hub de modèles est le même pour les deux appels : chaque modèle est chargé une fois par étape, jamais par
    élément (R19).
    Décision P24 : les extraits proposés tout de suite sont le best-of provisoire et les premières parties (seuls
    posts publiables aussitôt, les parties sortant dans l'ordre) ; les autres parties suivent la nuit.
  - **Moments provisoires** : `comment_ts` (horodatages cités dans les commentaires), `quote_signals` (commentaire
    qui cite une phrase de la transcription : `rapidfuzz.fuzz.partial_ratio ≥ min_ratio` sur textes normalisés, au
    début de la phrase, poids `1 + log1p(likes)`), rires et exclamations (niveau 2, tâche 22). `BestofStep` calcule
    `quote_signals` depuis `comments_json` et `words.json` (signaux externes de niveau 1, non stockés).
  - **Second passage** (`creators_revisit`, profil `watch`) : vidéos signalées par la veille (`express_requested_at`
    non nul, quelle que soit leur voie d'arrivée), `published_at + revisit_after_h[k] ≤ now` et `revisited_*_at`
    nul : nouvelle extraction (`comments=express_comments`, heatmap, sans cookies) →
    `video_signals` `heatmap`/`comment_ts` de la vidéo remplacés, `comments_json` mis à jour ; best-of recalculé
    hors base (`find_moments`) : niveau plus fort, ou score moyen supérieur de `revisit_min_gain` →
    `withdraw_bestof(reason="remplacé par le second passage")` (tâche 15) :
    - vrai (ancien best-of `planned` ou `rendered` : posts `expired`, rendus supprimés, attributions libérées,
      compilation `rejected` ; jamais compté au bandit, P16) → `express_requested_at = now`,
      `express_done_at = NULL` : la voie express suivante réutilise l'épisode, crée et rend le nouveau best-of ;
    - faux (ancien best-of `ready` ou `published`) : gardé (Louis l'a peut-être programmé, P9), ligne dans le rapport.
    Les parties ne sont jamais redécoupées : R5 s'évalue sur les pics connus au découpage (P24).
  - `BestofStep.select` : épisodes sans compilation `bestof` **non rejetée**.
  - Profils (tâche 20) : `RUN_ORDER` gagne `creators_backfill` après `discover_youtube` (donc `night`) ;
    `watch = ("creators_watch", "creators_revisit", "queue")` ; `rushes-watch.service` a un second `ExecStart=`
    (même forme `docker compose run --rm --no-deps -T`, lancé à la fin du premier) qui exécute
    `rushes creators express`, pour que la vidéo détectée entre en voie express dans l'heure (R29). Le premier
    `ExecStart` est préfixé par `-` (un échec du profil `watch` n'empêche pas la voie express) et l'unité garde
    `SuccessExitStatus=4 5` comme toutes les unités (tâche 20).
  - `status` : tableau « Chaînes surveillées » (chaîne, statut, dernier contrôle, dernière vidéo) ; chaîne
    `followed`/`authorized` non contrôlée depuis plus de `overdue_h` heures → ligne dans « Alertes ».

- [ ] **Step 1 : Tests qui échouent**

```python
def test_r29_authorized_channel_checked_every_6h(ctx):
    # après creators_watch, chaque chaîne followed/authorized a last_checked_at == horloge ; une chaîne contrôlée il y a
    # 7 h apparaît dans les alertes de status
def test_watch_detects_new_video_from_rss(ctx):              # flux enregistré + entrée nouvelle → source_video via "rss", express_requested_at
def test_watch_rss_down_falls_back_to_listing(ctx):
    # FakeHttp lève HttpError → liste yt-dlp (cassette) → au plus 5 extractions → vidéo de moins de 72 h retenue,
    # vidéo plus vieille sans express_requested_at
def test_r29_watch_picks_video_already_listed_by_backfill(ctx):
    # vidéo de 3 h déjà en base via "backfill", sans épisode → express_requested_at posé, via "backfill" gardé
def test_watch_silent_feed_lists_once_per_day(ctx):
def test_watch_skips_shorts_lives_and_premieres(ctx):
def test_watch_collector_health_once_per_run(ctx):
    # 3 chaînes, 2 en échec → consecutive_failures("youtube") == 0 ; 3 en échec → 1 (une seule mise à jour par run)
def test_r18_no_cookies_for_watch_backfill_prospect(ctx):
    # cookies mode always : FakeYtDlp → aucun appel avec cookies_allowed=True ni "cookiefile" pour creators_watch,
    # creators_backfill, creators_revisit, creators_prospect, extractions de run_express (le téléchargement suit L32)
def test_backfill_ranks_old_videos_reproducibly(ctx):
def test_backfill_prospects_within_showcase_budget(ctx):
def test_select_downloads_includes_creators_by_stock(ctx):
def test_quote_signals_from_comments():
def test_r30_express_lane_goes_through_approval_and_registry(ctx, synth):
    # vidéo synthétique de créateur : approve et assign tournent ; passage needs_review jamais rendu ; refus du registre → non rendu
def test_express_proposes_bestof_and_first_parts(ctx, synth):
    # best-of + parties 1 et 2 ready dans outbox_test/ ; partie 3 planned sans rendu ;
    # FakeNotifier a reçu une alerte creator_video:<v_key> (envoyée par send_pending_alerts en fin de commande)
def test_express_extra_slot_beyond_posts_per_day(ctx, synth):
    # compte à posts_per_day 1, 7 jours de posts ready ; best-of et parties 1, 2 sur ce compte → best-of planned_at
    # aujourd'hui, express_slot = 1 ; partie 1 → demain ; partie 2 sans créneau, non rendue, warn « aucun créneau
    # express libre » ; planned_at des posts ready identiques ; queue relancée → planned_at express inchangés
def test_express_ignores_other_pending_posts(ctx, synth):
    # un autre post planned (série) et un épisode de créateur sans faces_at hors E → ni reframe, ni rendu, ni faces
def test_express_reuses_ingested_episode_and_skips_over_budget_prospect(ctx, synth):
    # vidéo déjà ingérée → aucun téléchargement, même episode_id ; prospect hors budget → express_done_at posé, info
def test_express_two_videos_one_fails_models_loaded_once(ctx, synth):
    # deux vidéos, téléchargement de la 2e en échec → la 1re prête, code 4 ; compte en module de base :
    # hub.loads()["transcriber"] == 1 (une fois pour l'étape transcribe, pas par vidéo, R19)
def test_express_waits_for_heavy_lock_handover(ctx):
    # vraie paire de verrous sous ctx.paths (tmp_path) : un thread tient heavy.lock et cède entre deux éléments → l'express
    # passe ; seul, l'express se termine sans attente (aucun interblocage sur ses propres verrous)
def test_revisit_replaces_unpackaged_bestof_only(ctx, synth):
    # signaux plus forts à 24 h : best-of rendered → expired « remplacé », aucun échec de bandit ; best-of ready → gardé
def test_revisit_runs_once_at_24h_and_once_at_72h(ctx):
def test_watch_unit_runs_express_after_profile():
    # rushes-watch.service : ExecStart=- du profil watch puis ExecStart de creators express ; SuccessExitStatus=4 5
def test_r16_watch_and_backfill_rerun_change_nothing(ctx):
```

- [ ] **Step 2 : Enregistrer** les cassettes : `/videos` (`1:5` et `1:200`) et l'extraction complète avec 200
  commentaires d'une vidéo récente de Squeezie ; un flux RSS dont la dernière entrée est un Short ; une extraction
  d'une première programmée (`live_status = is_upcoming`).

- [ ] **Step 3 : Lancer** → FAIL. **Step 4 : Implémenter.** **Step 5 : Lancer** → PASS ; retirer R29, R30 de
  `rules_pending.toml`. Suite rapide, ruff, mypy verts.

- [ ] **Step 6 : Mesurer sur le serveur** (critère de la spec) — `rushes --json creators express <URL d'une vidéo
  récente d'environ 20 min de Squeezie ou Byilhan>` dans l'image : durée totale lue dans `runs`, temps par étape et
  pic mémoire notés dans `docs/decisions.md`. Au-delà de 60 min : passer `settings.models.whisper` à `base` pour
  tous, ou le preset x264 à `veryfast`, dans cet ordre, mesurer de nouveau et le consigner.

- [ ] **Step 7 : Commit proposé** — `feat(creators): nightly backfill, 6-hourly rss watch, express lane and 24/72h revisit (R29, R30)`

---

## Porte de sortie de la phase 4

- [ ] Sur le serveur, `rushes creators express <URL>` sur une vraie vidéo d'environ 20 min d'un créateur de la
  config : au moins 2 posts `ready` dans `outbox_test/` (vitrine, créateur non autorisé), durée totale **sous
  60 min**, mémoire du conteneur sous 6 Go.
- [ ] Unité `rushes-watch` installée (mise à jour de l'installation provisoire de la tâche 20) : pendant 3 jours,
  `rushes status` montre chaque chaîne suivie contrôlée depuis moins de 6 h ; toute nouvelle vidéo publiée par un
  créateur suivi pendant ces jours a donné ses extraits (alerte `creator_video` reçue sur l'iPhone).
- [ ] `rushes run --profile weekly` sur le serveur : `rushes creators list` montre des chaînes candidates avec langue
  et dynamique ; aucun créateur prospecté n'est publiable (posts dans `outbox_test/` seulement).
- [ ] Louis regarde deux rendus de créateur (un `square`, un `vertical`) : sous-titres, cadrage sur le visage,
  crédit ; remarques et validation de la charte des créateurs consignées dans `docs/decisions.md`.
- [ ] `tests/rules_pending.toml` ne contient plus R10, R21, R23, R24, R25, R26, R28, R29, R30, R32, R45, R46 ; suite
  rapide, ruff, mypy verts ; `test_vertical_filter_on_every_step` vert.
