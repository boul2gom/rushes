# Phase 1 — Squelette de bout en bout : plan d'implémentation

> **Pour les agents qui exécutent le plan :** sous-skill requis : `superpowers:subagent-driven-development`
> (recommandé) ou `superpowers:executing-plans`, tâche par tâche. Lire d'abord `docs/plans/00-master.md` (contrats
> transverses, sections citées « §5.x »). Les étapes utilisent des cases `- [ ]`.

**But :** un épisode déposé en local devient des parties rendues, conformes et empaquetées dans `outbox/`, sans
aucun modèle lourd, avec une console soignée, un runner idempotent, une image Docker mesurée sur le serveur.

**Architecture :** la tâche 33 nettoie la config (Postiz retiré), la tâche 42 prouve que la pile Python 3.14 tourne
sur le serveur, puis on construit dans l'ordre du pipeline : runner et console (2), registre (3), ingestion et
nettoyage (4), analyse sans modèle lourd (5), découpage (6), rendu (7a, 7b), métadonnées et paquet (8a, 8b), image
et mesures (9).

**Pile :** uv, Typer, Rich, Pydantic v2, SQLAlchemy 2.1 + Alembic, FFmpeg 7.1, PySceneDetect 0.7.2, OpenCV
contrib headless, imagehash, Silero VAD (ONNX), Pillow, fontTools, Jinja2.

**Spec :** `docs/spec.md`, `docs/decisions.md`, `docs/plans/00-master.md`.

## Global Constraints

Toutes celles de `00-master.md` §2 s'appliquent. En plus, pour cette phase :

- Chemins de fichiers stockés en base **relatifs à `paths.data_dir`** (le dossier est monté ailleurs dans le
  conteneur) ; les fonctions qui ouvrent un fichier le résolvent par `paths.data_dir / rel`.
- Identifiants affichés et noms de fichiers : `e_000045` (épisode), `s_000007` (segment), `p_000123` (post), donnés
  par `rushes.ids` (tâche 2). Jamais de titre dans un chemin.
- Tout fichier produit est écrit par `rushes.fsutil.atomic_path` (fichier temporaire du même dossier, renommé à la
  fin, supprimé en cas d'erreur).
- Les horodatages internes d'un épisode sont relatifs à `clean.mp4` ; `clean.json` donne le décalage vers la source.
- FFmpeg sur le Mac : `brew install ffmpeg` ; vérifier `ffmpeg -hide_banner -buildconf` contient `--enable-libass`
  et `--enable-libx264`. Les tests qui l'exigent sont rapides tant qu'une vidéo synthétique dure moins de 20 s.
- Préset x264 : `settings.render.preset` (`medium` en production, `ultrafast` dans les tests).
- Fin de chaque tâche : `uv run pytest -m "not slow"`, `uv run ruff check .`, `uv run ruff format --check .`,
  `uv run mypy src/rushes/core src/rushes/db`, résultats rapportés tels quels ; puis **commit proposé** (Louis
  commite, jamais l'agent).

## Review Focus

1. **Source exotique** (sans piste audio, pixels non carrés 1440×1080 SAR 4:3, fréquence d'images variable) :
   `clean.mp4` sort toujours en pixels carrés, 30 i/s constants, stéréo 48 kHz (piste muette ajoutée). Test :
   `test_clean_normalizes_vfr_sar_and_missing_audio` (tâche 4).
2. **Épisode trop court** (moins de 65 s après nettoyage) : aucune partie, un avertissement, aucun plantage ; même
   chose pour un épisode sans aucun changement de plan, qui doit retomber sur des points calmes. Tests :
   `test_cut_short_episode_gives_no_part_and_warns`, `test_cut_without_shot_changes_uses_quiet_points` (tâche 6).
3. **Texte long, accentué, avec emoji** dans le bandeau : réduit, puis sur deux lignes, jamais hors des zones
   sûres, emoji rendu par la police de repli. Test : `test_banner_long_unicode_title_fits_safe_zone` (tâche 7b).
4. **Run interrompu** (processus tué pendant un rendu) : aucun fichier final partiel, le run suivant reprend.
   Test : `test_interrupted_step_leaves_no_partial_output` (tâche 2), réutilisé par le rendu (tâche 7a).
5. **Dossier de données avec espaces et accents** (`data ü dir/`) : ingestion, rendu et paquet fonctionnent. Test :
   `test_render_works_with_unicode_path` (tâche 7b).

---

### Task 33 : Config v2 (Postiz retiré, hashtags obligatoires, mises en page, originalité, licences)

**Files :**
- Modify : `src/rushes/config.py`, `src/rushes/db/models.py`, `src/rushes/db/sync.py`, `src/rushes/db/migrate.py`,
  `src/rushes/cli.py`
- Create : `src/rushes/core/__init__.py` (existe), `src/rushes/core/text.py`,
  `src/rushes/db/migrations/versions/0002_config_v2.py`
- Modify config : `config/settings.yaml`, `config/accounts.yaml`, `config/series/tom_and_jerry.yaml` (reste
  minimal : `id`, `title`, `audio`, `licensed`, `rights` ; l'exemple complet de la spec y figure en commentaire
  YAML, jamais en valeurs), `config/creators/squeezie.yaml`, `config/creators/byilhan.yaml`
- Delete : `config/series/grizzy.yaml`
- Test : modifier `tests/test_config.py`, `tests/test_db.py`, `tests/test_cli.py` ; créer
  `tests/test_config_v2.py`, `tests/test_migration_0002.py`, `tests/test_r48_no_publication.py`

**Interfaces :**
- Consomme : schéma de la tâche 1.
- Produit (§5.9 de `00-master.md`, plus) :
  ```python
  Originality = Literal["base", "advanced"]
  Layout = Literal["square", "vertical", "portrait", "full"]      # réexporté par rushes.schemas
  Vertical = Literal["cartoons", "creators"]
  HASHTAG_TOKENS: frozenset[str] = frozenset({"{series}", "{creator}", "{nostalgia}"})
  FORBIDDEN_HASHTAGS: frozenset[str] = frozenset({"#viral", "#foryou"})
  CHANNEL_ID: re.Pattern[str] = re.compile(r"^UC[\w-]{22}$")        # réutilisé par la tâche 27
  TIKTOK_HANDLE: re.Pattern[str] = re.compile(r"^@(?=[\w.]*\w)[\w.]+$")   # « @... » refusé (C27)

  class ModelSettings(_Model): whisper: Literal["base", "small"] = "small"; clip_pretrained: str = "datacomp_xl_s13b_b90k"
  class NtfySettings(_Model):
      url: str; topic_env: str; token_env: str | None = None
      def topic(self) -> SecretStr: ...          # lu dans l'environnement à l'usage, ConfigError si absent
      def token(self) -> SecretStr | None: ...
  class Settings(_Model):
      rate_limit_s: float; approval_threshold: Threshold | None = None; registry: RegistrySettings; llm: LlmSettings
      originality: OriginalitySettings = OriginalitySettings(); youtube: YoutubeSettings = YoutubeSettings()
      showcase: ShowcaseSettings = ShowcaseSettings(); serve: ServeSettings | None = None
      models: ModelSettings = ModelSettings(); ntfy: NtfySettings | None = None

  class CreatorConfig(_Model):  # + rights: Rights | None = None ; hashtag: str | None = None ; − hashtags_anchor
      @property
      def effective_hashtag(self) -> str: ...     # hashtag or hashtag_from_name(id with "_" → " ")

  class Config:  # dataclass existante, méthodes ajoutées
      def account(self, account_id: str) -> Account: ...
      def brand(self, brand_id: str) -> Brand: ...
      def brand_of(self, account_id: str) -> Brand: ...
      def series_by_id(self, series_id: str) -> SeriesConfig | None: ...
      def creator_by_id(self, creator_id: str) -> CreatorConfig | None: ...
      def effective_originality(self, account_id: str) -> Originality: ...   # R50
      def slots_for(self, account_id: str) -> list[str]: ...   # config, sinon défaut par plateforme et fuseau de la marque
      def family_share(self, family: str, platform: Platform) -> float: ...

  # rushes/core/text.py
  def hashtag_from_name(name: str) -> str: ...   # "Tom and Jerry" → "#tomandjerry" ; accents repliés ASCII ; seuls [a-z0-9]
  def normalize_hashtag(tag: str) -> str: ...    # "#TomAndJerry" → "#tomandjerry" ; ajoute "#" si absent

  # rushes/db/migrate.py
  def upgrade(db_path: Path, revision: str = "head") -> None: ...
  ```

- [ ] **Step 1 : Écrire les tests qui échouent** (`tests/test_config_v2.py`, `tests/test_migration_0002.py`,
  `tests/test_r48_no_publication.py`)

```python
@pytest.mark.parametrize(("file", "mutate", "loc"), [
    ("settings.yaml", lambda d: d.update(postiz={"enabled": False}), "postiz"),
    ("settings.yaml", lambda d: d.update(manual_posts_notice=10), "manual_posts_notice"),
    ("accounts.yaml", lambda d: d["accounts"][0].update(publish_mode="manual"), "accounts.0.publish_mode"),
    ("accounts.yaml", lambda d: d.update(platform_hashtags={}), "platform_hashtags"),
])
def test_legacy_postiz_key_refused_with_d1_message(config_dir, edit_yaml, file, mutate, loc):
    edit_yaml(config_dir / file, mutate)
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir)
    issue = next(i for i in exc.value.issues if i.location == loc)
    assert "Postiz a été abandonné (décision D1 du 2026-11-01)" in issue.message

def test_legacy_hashtags_anchor_refused(config_dir, edit_yaml):
    # creators/squeezie.yaml avec hashtags_anchor → message « remplacé par required_hashtags (R13) et hashtag »
def test_r13_config_rejects_6_hashtags(config_dir, edit_yaml):
    # required_hashtags.cartoons.tiktok = 6 éléments → issue contenant « 5 hashtags au maximum (R13) »
def test_r13_never_viral_foryou_in_config(config_dir, edit_yaml):        # "#viral" ou "#foryou" → refus
def test_r13_no_fyp_on_instagram_config(config_dir, edit_yaml):          # "#fyp" sous instagram → refus
def test_r13_unknown_token_refused(config_dir, edit_yaml):               # "{serie}" → « jeton inconnu »
def test_r13_required_hashtags_cover_every_vertical(config_dir, edit_yaml):
    # suppression de required_hashtags.creators alors qu'une marque creators existe → refus
def test_r50_account_originality_defaults_to_settings(config_dir):
    cfg = load_config(config_dir)
    assert cfg.effective_originality("st_tt") == "base"
    assert cfg.effective_originality("tt_tt") == "advanced"
def test_r49_no_business_account_requirement():
    assert set(Account.model_fields) == {"id", "brand", "platform", "handle", "originality"}
def test_recalibrable_keys_optional_with_spec_defaults(config_dir, edit_yaml):
    # sans approval_threshold, slots, share, vertical_share → config valide ;
    # slots_for("st_tt") == ["15:00", "18:00"] (America/New_York) ; slots_for("ic_tt") == ["18:00", "20:00"]
    # et slots_for("ic_ig") == ["19:00"] (Europe/Paris)
def test_series_placeholder_channel_refused(config_dir, edit_yaml):
    # sources.youtube_channels: [{id: "UC...", kind: official}] → ConfigError, issue de location
    # "sources.youtube_channels.0.id" dans "series/tom_and_jerry.yaml"
def test_series_placeholder_tiktok_account_refused(config_dir, edit_yaml):   # tiktok_accounts: ["@..."] → idem
def test_series_missing_ref_file_refused(config_dir, edit_yaml):
    # intro_outro.reference_frames: ["refs/absent.png"] → ConfigError qui nomme la clé et "refs/absent.png"
    # (même règle pour theme_audio et characters[].refs)
def test_layouts_preference_rejects_vertical(config_dir, edit_yaml):
    # preference: [vertical] → refus « vertical se règle par vertical_share »
@pytest.mark.parametrize(("name", "tag"), [("Tom and Jerry", "#tomandjerry"),
    ("Oggy and the Cockroaches", "#oggyandthecockroaches"), ("Les Zinzins de l'Espace", "#leszinzinsdelespace")])
def test_hashtag_from_name(name, tag): assert hashtag_from_name(name) == tag
def test_creator_hashtag_defaults_to_id(config_dir):
    assert load_config(config_dir).creator_by_id("squeezie").effective_hashtag == "#squeezie"

# tests/test_migration_0002.py
def test_migration_0002_keeps_rows_and_maps_statuses(tmp_path):
    db = tmp_path / "r.db"; upgrade(db, "0001")
    # insérer brand, account (publish_mode='manual'), series, episode, deux posts status 'drafted' et 'scheduled'
    upgrade(db)
    # accounts: ligne présente, originality IS NULL, colonnes publish_mode/postiz_integration absentes
    # posts: statuts 'ready' et 'ready' ; colonnes postiz_* absentes ; layout/originality présentes
    # tables rights, derived_values, derived_values_history présentes
def test_sync_writes_origin_status_and_rights(tmp_path, config_dir):
    # après sync : series.origin='config', status='active' ; rights('series','tom_and_jerry').status='to_request'
    # une série licensed: true sans bloc rights → rights.status='granted'

# tests/test_r48_no_publication.py
PUBLICATION_MARKERS = ("postiz", "open.tiktokapis.com", "graph.facebook.com", "graph.instagram.com",
                       "content_publishing", "video.publish", "/publish/video")
def test_r48_no_publication_client_in_code():
    # balaie src/rushes/**/*.py (chemin construit depuis __file__) ; « postiz » n'est toléré que dans
    # db/migrations/versions/ (colonnes postiz_* de 0001) et, dans config.py, sur les lignes de clés héritées
    # (_D1_MESSAGE et table _LEGACY_KEYS) ; les marqueurs réseau s'appliquent partout (C28)
    assert hits == []
def test_r48_waiver_does_not_cover_a_client_in_config(): ...     # auto-contrôles du balayage : un client « postiz »
def test_r48_waiver_keeps_the_legacy_key_table_in_config(): ...  # ajouté dans config.py ou sync.py est vu ; la table
def test_r48_waiver_still_covers_past_migrations(): ...          # héritée et les migrations sont seules exemptées
```

  Remplacer dans `tests/test_config.py` : `test_r48_default_publish_mode_manual`, `test_r48_postiz_mode_requires_integration`,
  `test_placeholder_refused_when_postiz_enabled` sont supprimés ; `test_r47_secret_never_in_repr_or_error` utilise
  `ntfy.token_env` ; l'ensemble des séries attendu devient `{"tom_and_jerry", "oggy"}` (`grizzy` supprimé). Dans `tests/test_db.py`, `test_publish_mode_defaults_to_manual_r48` est supprimé. Dans
  `tests/test_cli.py`, la liste des commandes prévues perd `publish push`, `publish sync`, `vendor check` et gagne
  `serve` (36), `models fetch` et `models check` (9), `discover series` (38), `rights list` (38).

- [ ] **Step 2 : Lancer les tests**

Run : `uv run pytest tests/test_config_v2.py tests/test_migration_0002.py tests/test_r48_no_publication.py -v`
Expected : FAIL (champs inconnus, `effective_originality` absent, révision 0002 absente).

- [ ] **Step 3 : Implémenter le schéma v2 dans `src/rushes/config.py`**

  - Modèles de §5.9 ; `PostizSettings` supprimé ; `Account` sans `publish_mode` ni `postiz_integration`.
  - Clés héritées détectées sur le YAML brut **avant** validation par `_legacy_keys(rel: str, data: Any, issues: list[Issue]) -> None`
    (emplacements relatifs au fichier, comme dans le test : `postiz`, `manual_posts_notice`, `accounts.N.publish_mode`,
    `accounts.N.postiz_integration`, `platform_hashtags`, `branding.hashtags_anchor`, `hashtags_anchor`) ; message
    Postiz : « Postiz a été abandonné (décision D1 du 2026-11-01) : retirer cette clé » ; message des ancres :
    « remplacé par required_hashtags (R13) et hashtag du créateur : retirer cette clé ». La clé fautive est retirée
    avant validation pour ne pas produire un second message « champ inconnu ».
  - Validation de `required_hashtags` (validateur de `AccountsConfig`) : un constat par problème, à l'emplacement
    `required_hashtags.<verticale>.<plateforme>[.<indice>]` ; doublon refusé (« doublon : #x », seulement entre éléments au format valide) ; liste vide refusée (C30) ; au plus 5 éléments par plateforme ;
    chaque élément est un jeton de `HASHTAG_TOKENS` ou un hashtag `^#[a-z0-9_]+$` ; jamais `FORBIDDEN_HASHTAGS` ;
    pas de `#fyp` sous `instagram`. Contrôle croisé : chaque verticale portée par une marque a une entrée pour les
    deux plateformes.
  - `LayoutSettings.preference` n'accepte que `square`, `portrait`, `full`, sans doublon ; `vertical` y est refusé.
  - Valeurs recalables facultatives (§5.9) : `Settings.approval_threshold`, `AccountsConfig.slots` (plateforme
    absente → défaut selon le fuseau de la marque : `Europe/Paris` → TikTok `["18:00", "20:00"]`, Instagram
    `["19:00"]` ; autre fuseau → `DEFAULT_SLOTS`), `Family.share` (absente → `DEFAULT_SHARE`),
    `LayoutSettings.vertical_share` ; helpers `Config.slots_for(account_id)` et `Config.family_share(family,
    platform)` (valeur écrite, sinon défaut). La config d'exemple les laisse en commentaire (« décommenter pour forcer »).
  - Amorces de série (validateur de `SeriesConfig`, au chargement) : `sources.youtube_channels[].id` hors
    `CHANNEL_ID`, `sources.tiktok_accounts[]` hors `TIKTOK_HANDLE` (donc `"UC..."` et `"@..."` refusés), ou fichier
    absent sous `config/` pour `intro_outro.reference_frames`, `intro_outro.theme_audio`, `characters[].refs` →
    `ConfigError` qui nomme la clé et le fichier (jamais une erreur plus tard au nettoyage ou à l'analyse).
    `CreatorConfig.youtube_channel` garde son placeholder jusqu'à la tâche 27 (qui le traite comme absent).
  - `ServeSettings.public_origin` : texte libre (placeholder toléré ; `rushes serve` refusera de démarrer avec un
    placeholder, tâche 36).

- [ ] **Step 4 : Écrire `core/text.py`** — `unicodedata.normalize("NFKD")`, suppression des diacritiques, minuscules,
  seuls `[a-z0-9]` gardés.

- [ ] **Step 5 : Migration `0002_config_v2.py`** (contenu exact de §5.10 de `00-master.md`)

  `op.batch_alter_table` (recréation SQLite) pour `accounts` et `posts` ; mise à jour
  `UPDATE posts SET status='ready' WHERE status IN ('drafted','scheduled')` avant la nouvelle contrainte ;
  `series` et `creators` gagnent `origin TEXT NOT NULL DEFAULT 'config' CHECK IN ('config','prospect')` et
  `status TEXT NOT NULL DEFAULT 'active' CHECK IN ('candidate','active','dropped')` ; `posts` gagne
  `layout TEXT CHECK IN ('square','vertical','portrait','full')` et `originality TEXT CHECK IN ('base','advanced')` ;
  tables `rights`, `derived_values` (§5.10) et `derived_values_history` (`id` autoincrément + colonnes de
  `derived_values`). Modèles ORM alignés (`PUBLISH_MODES` supprimé, `POST_STATUSES` à 6 valeurs). `downgrade()` lève
  `NotImplementedError` (les données Postiz supprimées ne reviennent pas ; le retour arrière restaure la sauvegarde, C24).
  `migrate.upgrade` est atomique (moteur de migration dédié : `isolation_level=None`, `BEGIN` explicite,
  `foreign_keys=OFF` hors transaction, `PRAGMA foreign_key_check` dans la transaction, seulement si une migration a été appliquée) : un échec ne laisse ni
  table `_alembic_tmp_*` ni révision changée (`test_migration_failure_rolls_back_ddl`).

- [ ] **Step 6 : `db/sync.py`** — `accounts.originality` (valeur du compte, NULL sinon) ; `series`/`creators` avec
  `origin='config'`, `status='active'` ; `rights` : upsert depuis `rights` de la config, sinon `granted` si
  `licensed`/`authorized`, sinon `to_request` ; `priority` et `showcase_posts` jamais écrasés. La config reste
  la seule source de `rights.status` : un statut modifié seulement en base est écrasé à la synchro suivante (C26 ;
  tests `test_sync_rights_status_follows_config`).

- [ ] **Step 7 : Config d'exemple et CLI** — fichiers YAML de la spec (section Configuration), sauf
  `tom_and_jerry.yaml` qui reste minimal (`id`, `title`, `audio`, `licensed`, `rights`), l'exemple complet en
  commentaire ; créateurs sans `hashtags_anchor`, `credit_template: "🎥 {mention}"` toléré jusqu'à la tâche 26 (seul
  endroit qui passe à `{name}`) ; créateurs de Peak Moments (L31) : `config/creators/beta_squad.yaml`,
  `caseoh.yaml`, `dude_perfect.yaml` (`language: en`, `authorized: false`, `youtube_channel: "UC..."` comme les
  autres jusqu'à la tâche 27, qui le rend facultatif et le déduit), et `creators_by_brand.peak_moments: [beta_squad, caseoh, dude_perfect]` dans `accounts.yaml` ; `settings.yaml`
  sans `postiz` ni `manual_posts_notice`, avec `originality`, `youtube`, `showcase`, `serve`, `ntfy.token_env` ;
  `rotation` reste active dans `accounts.yaml` d'exemple (des tests existants la mutent ; rushes la génère si elle
  est absente) ; `cli.py` : groupes `publish` et `vendor` retirés, commandes `serve`, `models fetch`, `models check`,
  `discover series`, `rights list` ajoutées en attente.

- [ ] **Step 8 : Lancer toute la suite**

Run : `uv run pytest -m "not slow" && uv run ruff check . && uv run ruff format --check . && uv run mypy src/rushes/core src/rushes/db`
Expected : tout vert ; `test_orm_models_match_migration` vert.

- [ ] **Step 9 : Commit proposé** — `refactor(config)!: drop postiz and introduce config v2 (R13 hashtags, layouts, originality, rights)`

---

### Task 42 : Essai de faisabilité Python 3.14 sur le serveur (d'abord essayé en 3.15)

**Files :**
- Modify : `pyproject.toml`, `uv.lock`, `.gitignore`
- Create : `docker/Dockerfile` (v0), `.dockerignore`, `scripts/spike_models.py`
- Modify : `docs/decisions.md` (entrées « Essai 3.15 » puis « Repli en Python 3.14 »)

**Interfaces :**
- Produit : l'environnement verrouillé de tout le projet (toutes les dépendances lourdes et légères de la pile),
  le Dockerfile que la tâche 9 finalise, et le tableau go/no-go. Aucun code de `src/` n'est écrit.

- [ ] **Step 1 : Ajouter les dépendances**

```bash
uv add numpy scipy pillow fonttools imagehash jinja2 rapidfuzz "opencv-contrib-python-headless>=5" \
  "scenedetect-headless==0.7.2" torch open-clip-torch silero-vad faster-whisper sherpa-onnx sherpa-onnx-core \
  llama-cpp-python "yt-dlp[default,curl-cffi]" yt-dlp-ejs torchvision
```

  Puis dans `pyproject.toml` : bornes `>=x.y,<x+1` sur la version résolue de chaque paquet ; et

```toml
[tool.uv]
# scenedetect-headless tire opencv-python-headless, qui écraserait cv2/ d'opencv-contrib ; marqueur jamais vrai
override-dependencies = ["opencv-python-headless; python_version < '3'"]
required-environments = ["sys_platform == 'linux' and platform_machine == 'x86_64'",
                         "sys_platform == 'darwin' and platform_machine == 'arm64'"]

[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true

[tool.uv.sources]
torch = [{ index = "pytorch-cpu", marker = "sys_platform == 'linux'" }]
# direct pour que la source s'applique : le wheel PyPI Linux est construit pour CUDA
torchvision = [{ index = "pytorch-cpu", marker = "sys_platform == 'linux'" }]
```

- [ ] **Step 2 : Vérifier sur le Mac**

Run : `uv lock && uv sync && uv run python -I -c "import torch, open_clip, faster_whisper, sherpa_onnx, llama_cpp, cv2, scenedetect, silero_vad, imagehash, scipy, PIL, fontTools, jinja2, rapidfuzz, yt_dlp, curl_cffi; print('ok')"`
Expected : `ok` ; `uv tree | grep onnxruntime` montre onnxruntime (via `silero-vad`) et `uv tree | grep opencv-python-headless` ne renvoie rien.

- [ ] **Step 3 : Écrire `docker/Dockerfile` v0**

```dockerfile
# syntax=docker/dockerfile:1.7
ARG PY_IMAGE=python:3.14-slim-trixie@sha256:<digest relevé le jour de la tâche>
FROM ${PY_IMAGE} AS builder
COPY --from=ghcr.io/astral-sh/uv:<version exacte> /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake git && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 \
    CMAKE_ARGS="-DGGML_NATIVE=OFF -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_BMI2=ON -DGGML_AVX512=OFF"
WORKDIR /app
COPY pyproject.toml uv.lock README* CLAUDE.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM ${PY_IMAGE} AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=denoland/deno:bin-<version exacte> /deno /usr/local/bin/deno
WORKDIR /app
COPY --from=builder /app /app
COPY scripts/ scripts/
ENV PATH="/app/.venv/bin:$PATH" OMP_NUM_THREADS=4 PYTHONUNBUFFERED=1
```

  Toutes les versions et empreintes sont relevées le jour de la tâche et écrites en dur ; jamais `latest`.

- [ ] **Step 4 : Écrire `scripts/spike_models.py`** (développement uniquement, réseau autorisé)

  `main(out: Path)` lance un sous-processus par modèle (`--one NAME`), chacun télécharge son fichier dans
  `$RUSHES_DATA_DIR/cache/models/`, calcule son sha256, le charge, fait une inférence, puis imprime une ligne JSON
  `{"name", "ok", "load_s", "infer_s", "peak_rss_mb", "sha256", "bytes", "error"}` ; le pic vient de
  `resource.getrusage(RUSAGE_CHILDREN).ru_maxrss` lu par le parent. Modèles et inférences :

  | Nom | Fichier | Inférence | Succès si |
  | --- | --- | --- | --- |
  | `silero` | poids ONNX du paquet `silero-vad` (`load_silero_vad(onnx=True)`) | `get_speech_timestamps` sur 10 s de bruit 16 kHz | pas d'exception |
  | `whisper_small`, `whisper_base` | `Systran/faster-whisper-small` / `-base` (révision figée) | 10 s de sinus, `vad_filter=False`, `word_timestamps=True` | pas d'exception |
  | `clip` | open_clip `ViT-B-32` / `datacomp_xl_s13b_b90k` | 2 images + 2 phrases | vecteurs de dimension 512, norme 1 ± 1e-3 |
  | `ced` | modèle sherpa-onnx CED-mini AudioSet | 10 s de bruit | au moins une étiquette |
  | `yunet` | `face_detection_yunet_2026may.onnx` | image noire 640×360 | liste vide, pas d'exception |
  | `qwen4b`, `qwen1_7b` | GGUF Q4\_K\_M | `response_format` schéma `{"hooks": [{"text","type"}]}` | JSON valide pour le schéma |
  | `supertonic` | modèle sherpa-onnx Supertonic 3 | « Bonjour, ceci est un essai. » (fr) et une phrase en anglais | WAV de plus de 0,5 s |
  | `kokoro` | modèle sherpa-onnx Kokoro (en) | une phrase | WAV de plus de 0,5 s |

- [ ] **Step 5 : Construire et lancer sur le serveur**

```bash
grep -o -w 'avx2\|fma\|f16c\|bmi2\|avx512f' /proc/cpuinfo | sort -u
docker build -f docker/Dockerfile -t rushes:spike .
docker run --rm --memory 6g --cpus 4 -e RUSHES_DATA_DIR=/data -v rushes-spike:/data rushes:spike \
  python -I scripts/spike_models.py --out /data/spike.jsonl
```

Expected : `avx2 bmi2 f16c fma` présents, `avx512f` absent ; une ligne `"ok": true` par modèle.

- [ ] **Step 6 : Décider** — entrée « 2026-11-xx — Essai Python 3.15 sur le serveur » puis « 2026-11-05 — Repli en Python 3.14 (décision de Louis) » dans `docs/decisions.md` :
  tableau des lignes JSON, taille de l'image, durée de construction. **Si un modèle échoue** : s'arrêter, proposer à
  Louis un contournement (autre version épinglée, compilation, repli prévu par la spec : PANNs réécrit pour les
  sons, Whisper base, Qwen3-1.7B) ; le repli en Python 3.14, décidé par Louis le 2026-11-05, est appliqué (l'essai d'origine était en 3.15).
  Les sha256 relevés servent au manifeste de la tâche 9.

- [ ] **Step 7 : Suite rapide** — `uv run pytest -m "not slow"` et lint verts.

- [ ] **Step 8 : Commit proposé** — `build: lock the full python 3.14 stack and add feasibility spike image`

---

### Task 2 : Console, runner, contexte, verrou, codes de sortie, `status`

**Files :**
- Rewrite : `src/rushes/ui.py`
- Create : `src/rushes/ids.py`, `src/rushes/fsutil.py`, `src/rushes/clock.py`, `src/rushes/errors.py`,
  `src/rushes/context.py`, `src/rushes/runner.py`, `src/rushes/lock.py`, `src/rushes/steps.py`,
  `src/rushes/status.py`, `src/rushes/models/__init__.py`, `src/rushes/models/hub.py`, `src/rushes/net/__init__.py`,
  `src/rushes/net/base.py`, `src/rushes/db/migrations/versions/0003_runs.py`
- Modify : `src/rushes/paths.py` (+ `logs`, `locks`, `imports` → dossier `data/import/`, `tmp` → dossier
  `data/tmp/`), `src/rushes/cli.py`, `src/rushes/db/models.py`
- Test : `tests/test_cli.py`, `tests/test_ui.py`, `tests/test_runner.py`, `tests/test_lock.py`, `tests/test_status.py`,
  `tests/test_guards.py`, `tests/test_rules_coverage.py`, `tests/rules_pending.toml`, `tests/helpers.py`,
  `tests/fakes/__init__.py`, `tests/fakes/clock.py`, `tests/fakes/models.py`, `tests/fakes/net.py`

**Interfaces :**
- Consomme : `Config`, `load_config`, `Paths`, `upgrade`, `sync_config`, `make_engine` (tâches 1 et 33).
- Produit : §5.1 à §5.4 et §5.11 de `00-master.md`, avec ces précisions :
  ```python
  # rushes/ids.py
  def episode_key(episode_id: int) -> str: ...   # "e_000045"
  def segment_key(segment_id: int) -> str: ...   # "s_000007"
  def post_key(post_id: int) -> str: ...         # "p_000123"
  def parse_key(key: str) -> tuple[Literal["e", "s", "p"], int]: ...

  # rushes/fsutil.py
  @contextmanager
  def atomic_path(final: Path) -> Iterator[Path]: ...   # ".<nom>.tmp-<pid>" dans le même dossier

  # rushes/ui.py (en plus de §5.2) ; Message.alert: str | None = None est créé ici (la tâche 17 l'émet,
  # la tâche 20 l'envoie)
  def make_emitter(*, plain: bool, json: bool, verbose: bool,
                   stream: TextIO | None = None, log_path: Path | None = None) -> Emitter: ...
  # Rich si stream est un TTY sans --plain ni --json ; texte simple avec --plain ; JSON sinon.
  # log_path : copie JSON complète de chaque événement (fichier data/logs/<AAAA-MM-JJ>.jsonl).

  # rushes/runner.py (en plus de §5.3)
  class BaseStep:                       # implémentation par défaut de Step
      heavy: ClassVar[bool] = False
      def finish(self, ctx: Context) -> None: ...   # appelé une fois après le dernier élément (no-op)
  def exit_code_for(reports: Sequence[StepReport], errors: Sequence[RushesError]) -> int: ...  # priorité 2>3>1>4>5>0
  def combine_exit_codes(*codes: int) -> int: ...   # même priorité ; pour une commande qui enchaîne plusieurs
                                                     # run_pipeline (voie express, refresh)
  def run_pipeline(ctx: Context, steps: Sequence[Step[Any]], filters: Filters, *, command: str,
                   lock: HeavyLock | None = None, yield_to_express: bool = True) -> int: ...
  # lock fourni → le runner ne prend pas le verrou lourd (l'appelant le tient déjà) ;
  # yield_to_express=False → yield_if_express_waiting n'est jamais appelé (la voie express ne se cède pas à elle-même)

  # rushes/lock.py (précise §5.11)
  class HeavyLock:
      def yield_if_express_waiting(self, *, before_yield: Callable[[], None]) -> bool: ...

  # rushes/models/hub.py (minimal ; complété en tâche 5)
  class ModelHub:
      def __init__(self, paths: Paths, settings: Settings, *, factories: Mapping[str, Callable[[], object]] | None = None) -> None: ...
      def loads(self) -> Mapping[str, int]: ...
      def release(self) -> None: ...

  # rushes/net/base.py : Protocols YtDlp et Http, NetClients (§5.8) ; Http gagne
  #     def download(self, url: str, dest: Path) -> None: ...
  # UnavailableYtDlp / UnavailableHttp lèvent SourceUnavailable("client réseau non disponible avant la tâche 10a")

  # rushes/context.py
  def build_context(*, plain: bool, json: bool, verbose: bool, environ: Mapping[str, str] | None = None) -> Context: ...
  # charge la config (ConfigError → exit 2), applique les migrations, recharge les miroirs (sync_config),
  # ouvre le journal data/logs/<date>.jsonl (rotation : fichiers de plus de 30 jours supprimés).

  # rushes/status.py
  def collect_status(ctx: Context) -> list[Table]: ...  # « Derniers runs », « File par compte », « Alertes », « Disque »
  ```
- `Header.commit` vient de la variable `RUSHES_COMMIT` (posée à la construction de l'image), sinon `None`.

- [ ] **Step 1 : Écrire les tests qui échouent**

```python
# tests/test_ui.py
def test_json_mode_one_valid_line_per_event():
    buf = io.StringIO(); em = make_emitter(plain=False, json=True, verbose=False, stream=buf)
    em.emit(StepEvent("render", "p_000123", "done", 61234)); em.close()
    line = json.loads(buf.getvalue().splitlines()[0])
    assert line["event"] == "step" and line["status"] == "done" and line["item"] == "p_000123"
    assert line["ts"].endswith("Z")
def test_non_tty_defaults_to_json():                      # stream StringIO sans flag → JSON
def test_rich_mode_shows_symbols_and_duration(monkeypatch):
    # stream TTY simulé (isatty=True), StepEvent done/skipped/warned/failed → « ✓ », « ↷ », « ⚠ », « ✗ » et « 61,2 s »
def test_no_color_disables_ansi(monkeypatch):            # NO_COLOR=1 → aucune séquence "\x1b["
def test_test_mode_header_says_ne_pas_publier():          # Header(test_mode=True) → « NE PAS PUBLIER »
def test_r47_no_secret_in_any_output(tmp_path):
    register_secret("tok-123-SECRET")
    for mode in ("rich", "plain", "json"):
        # Message("erreur avec tok-123-SECRET et cookie=abcdef") émis vers stream et log_path
        # → ni "tok-123-SECRET" ni "abcdef" dans la sortie ni dans le journal ; "***" présent
def test_log_file_receives_every_event_even_in_rich_mode(tmp_path):

# tests/test_runner.py (FakeStep : éléments [1, 2, 3] ; écrit data/out/<n>.txt puis une ligne runs_test)
def test_run_step_counts_done_skipped_failed(ctx):        # l'élément 2 lève → done=2, failed=1, code 4, runs.status='partial'
def test_r16_step_rerun_changes_nothing(ctx):             # second run : select vide, snapshot(base + fichiers) identique
def test_force_reruns_everything(ctx):                    # Filters(force=True) → 3 éléments rejoués
def test_interrupted_step_leaves_no_partial_output(ctx):  # run lève pendant l'écriture via atomic_path → ni fichier final ni .tmp
@pytest.mark.parametrize(("reports", "errors", "code"), [
    ([rep(failed=1)], [SourceUnavailable("x")], 4), ([rep()], [SourceUnavailable("x")], 5),
    ([], [LockBusy("x")], 3), ([rep()], [], 0), ([rep(failed=1)], [], 4)])
def test_exit_code_priority(reports, errors, code): assert exit_code_for(reports, errors) == code
def test_combine_exit_codes():   # combine_exit_codes(0, 5) == 5 ; (4, 5) == 4 ; (1, 2) == 2 ; (0, 0) == 0
def test_runs_row_records_exit_code_and_summary(ctx):
def test_r19_models_released_before_yielding_lock(ctx):
    # FakeModelHub avec un modèle lourd chargé ; un thread tient express_intent ; étape heavy à 2 éléments
    # → hub.release appelé une fois, le thread obtient le verrou lourd entre les deux éléments
def test_run_pipeline_with_held_lock_never_waits_on_itself(ctx):
    # vraie paire de verrous dans tmp_path : with express_intent(paths), heavy_lock(paths, wait=True) as hl:
    #   run_pipeline(ctx, [étape heavy à 2 éléments], Filters(), command="t", lock=hl, yield_to_express=False)
    # → code 0 en moins de 5 s (thread joint avec délai), aucun yield, verrous libres à la sortie

# tests/test_lock.py (threads : deux open() distincts du même fichier se bloquent par flock)
def test_second_heavy_lock_waits():
def test_no_wait_raises_lock_busy():                      # LockBusy, exit_code 3
def test_lock_released_on_exception():
def test_express_yield_hands_over_and_resumes():

# tests/test_guards.py
def test_r20_network_is_blocked_in_tests():
    with pytest.raises(RuntimeError, match="R20"): socket.create_connection(("example.org", 80))
def test_r39_no_remote_ai_client_imported():
    # AST de src/rushes : aucun import de openai, anthropic, google.generativeai, google.genai, mistralai,
    # cohere, groq, replicate ; aucune chaîne "api.openai.com", "api.anthropic.com"
def test_only_ui_imports_rich():                          # seul src/rushes/ui.py importe rich
def test_r40_vendor_dir_requires_vendor_md():             # si vendor/ existe → vendor/VENDOR.md existe
def test_r41_vendor_check_absent_while_vendor_empty():    # pas de vendor/ → « vendor » absent de `rushes --help`

# tests/test_status.py
def test_status_on_empty_db_exits_0_with_four_tables(cli_env):
def test_status_lists_last_run_per_command_and_failed_as_alert(cli_env):

# tests/test_rules_coverage.py
def test_every_active_rule_has_a_test_or_is_pending():
    # règles actives = lignes "- **R<n>** :" de docs/spec.md sans « supprimée »
    # testées = noms de fonctions test_r<n>_* trouvés par AST dans tests/
    # pending = tests/rules_pending.toml  ({ R7 = "7a", R8 = "34", … } : tâche qui termine la règle)
    # échoue si une règle n'est ni testée ni en attente, ou si une valeur n'est pas un numéro de tâche du plan ;
    # une règle déjà testée en partie peut rester en attente jusqu'à la tâche indiquée (00-master.md §6)
```

  `tests/helpers.py` fournit `make_context(tmp_path, config_dir, *, clock=None, hub=None, net=None) -> Context`
  (base migrée et synchronisée, émetteur JSON vers un `StringIO` accessible par `ctx_output(ctx)`) et
  `snapshot(ctx) -> dict[str, object]` (toutes les tables triées + `{chemin relatif: sha256}` de `data/`).
  `tests/fakes/clock.py` : `FakeClock(now: datetime)` avec `advance(**kwargs)`. `tests/conftest.py` gagne les
  fixtures `ctx` (`make_context` sur `config_dir` et `FakeClock(2026-11-16T12:00Z)`) et `cli_env`
  (`RUSHES_CONFIG_DIR`/`RUSHES_DATA_DIR` posés, renvoie un `CliRunner` Typer), et la tâche 3 ajoute `session`
  (session SQLAlchemy sur une base migrée de `tmp_path`, avec une marque, deux comptes, une série et un épisode).

- [ ] **Step 2 : Lancer** — `uv run pytest tests/test_ui.py tests/test_runner.py tests/test_lock.py tests/test_guards.py tests/test_status.py tests/test_rules_coverage.py -v` → FAIL.

- [ ] **Step 3 : Implémenter `ids.py`, `fsutil.py`, `clock.py`, `errors.py`** (§5.4).

- [ ] **Step 4 : Implémenter `ui.py`** — trois émetteurs (`RichEmitter`, `PlainEmitter`, `JsonEmitter`), `TeeEmitter`
  vers le journal ; `redact` remplace les secrets enregistrés et le motif
  `(?i)\b(token|key|secret|password|cookie|authorization)\s*[=:]\s*\S+` par `«***»` ; les `Progress` JSON sont
  limités à un par seconde et par `task` ; couleurs fixes (vert, jaune, rouge) ; `NO_COLOR` respecté ; Rich
  `Console(soft_wrap=True)` pour rester lisible en SSH sur téléphone ; erreurs en français avec `hint`, trace
  seulement avec `--verbose` (toujours dans le journal).

- [ ] **Step 5 : Implémenter `lock.py`** — algorithme de cession :

```text
heavy holder, entre deux éléments :
  essayer LOCK_EX|LOCK_NB sur express.lock
  succès  → personne n'attend : relâcher express.lock, continuer
  échec   → before_yield() ; relâcher heavy.lock ; LOCK_EX bloquant sur express.lock (fin de l'express) ;
            le relâcher ; reprendre heavy.lock (bloquant) ; renvoyer True
voie express : express_intent() prend express.lock, puis heavy_lock(wait=True) as hl ;
  run_pipeline(..., lock=hl, yield_to_express=False) (jamais un second open() de ses propres verrous) ;
  relâche heavy puis express
```

- [ ] **Step 6 : Implémenter `runner.py`, `context.py`, `models/hub.py`, `net/base.py`, `status.py`** — règles du
  runner de §5.3 ; `run_pipeline` ouvre une ligne `runs`, émet `Header`, exécute les étapes dans l'ordre, prend le
  verrou lourd si une étape est `heavy` et que `lock` n'est pas fourni, cède entre deux éléments seulement si
  `yield_to_express`, appelle `finish`, émet `Summary`, ferme la ligne `runs` avec `exit_code`.

- [ ] **Step 7 : Migration `0003_runs.py`** — `runs.status CHECK IN ('running','ok','partial','failed')`, `+ exit_code INTEGER`.

- [ ] **Step 8 : CLI** — `cli.py` construit le contexte par `build_context`, convertit `Filters`, appelle
  `run_pipeline` ; `ConfigError` → messages par fichier et code 2 ; `RushesError` → son code ; autre exception →
  code 1, trace dans le journal. `status` affiche `collect_status`. `config check` et `db upgrade` émettent des
  `Message`. `steps.py` contient `STEPS: dict[str, Callable[[], Step[Any]]]` et `RUN_ORDER: tuple[str, ...]`
  (vides pour l'instant). `tests/test_cli.py` : `COMMANDS` devient `PENDING: dict[str, str]` (commande → tâche qui
  l'implémente, ex. `"ingest": "4"`, `"creators express": "28"` ; chaîne car les tâches ont des suffixes comme
  `8a`) et `IMPLEMENTED: tuple[str, ...]` ; `test_unimplemented_command_exits_1_with_message` ne porte que sur
  `PENDING`, l'aide sur les deux. Chaque tâche qui implémente une commande la déplace de `PENDING` vers
  `IMPLEMENTED` (`tests/test_cli.py` dans ses Files) ; ici : `status`. `PENDING` part des bouchons actuels de
  `COMMANDS` avec leur tâche (`run` 9, `discover youtube` 10a, `market collect|analyze` 31, `ingest` et `clean` 4,
  `analyze` 5, `cut` 6, `compile themes` 30, `hooks propose` 14, `approve` et `assign` 8a, `queue rank` 16b,
  `render` 7a, `meta` et `package` 8b, `stats collect` et `posts detect-published` 17, `creators *` 27 ou 28,
  `review build|apply` 32) ; la tâche 33 retire `publish push|sync` et `vendor check`, et ajoute `serve` 36,
  `models fetch|check` 9, `discover series` et `rights list` 38. Une commande absente de `PENDING` (ex. `refresh`,
  `notify *`, `backup *`, `campaign *`, `creators list`) est ajoutée directement à `IMPLEMENTED` par sa tâche.

- [ ] **Step 9 : Tests au vert, puis `tests/rules_pending.toml`** — chaque règle active non testée y figure avec la
  tâche qui la couvrira (§7 de `00-master.md`) ; une règle couverte en partie y reste jusqu'à sa dernière tâche
  (R14 = "40a" même si un test « fait » existe déjà).

Run : `uv run pytest -m "not slow" && uv run ruff check . && uv run ruff format --check . && uv run mypy src/rushes/core src/rushes/db`
Expected : vert.

- [ ] **Step 10 : Commit proposé** — `feat(ui,runner): add event console, idempotent step runner, heavy lock and status`

---

### Task 3 : `core` (intervalles, timecodes, tirages, pHash) et `db.registry`

**Files :**
- Create : `src/rushes/core/intervals.py`, `src/rushes/core/timecode.py`, `src/rushes/core/rng.py`,
  `src/rushes/core/phash.py`, `src/rushes/db/registry.py`
- Test : `tests/test_core_intervals.py`, `tests/test_core_timecode.py`, `tests/test_core_rng.py`,
  `tests/test_registry.py`

**Interfaces :**
- Consomme : modèles ORM (`Segment`, `Episode`, `Assignment`, `Account`), `RushesError`.
- Produit :
  ```python
  # core/intervals.py  (Span est réexporté par rushes.models.base)
  type Span = tuple[int, int]
  def overlap_ms(a: Span, b: Span) -> int: ...
  def merge(spans: Iterable[Span], *, gap_ms: int = 0) -> list[Span]: ...
  def subtract(span: Span, holes: Iterable[Span]) -> list[Span]: ...
  def inside(t_ms: int, spans: Sequence[Span]) -> bool: ...        # bornes incluses
  # core/timecode.py
  def s_to_ms(s: float) -> int: ...      # arrondi au plus proche, .5 vers le haut
  def ms_to_s(ms: int) -> float: ...
  def ms_to_tc(ms: int) -> str: ...      # "00:01:02.345"
  def tc_to_ms(tc: str) -> int: ...
  # core/rng.py
  def derive_seed(*parts: str | int) -> int: ...                  # sha256 des parties jointes par "\x1f", 63 bits
  def draw_target_ms(seed: int, min_s: float, max_s: float) -> int: ...   # random.Random(seed).randint(min_ms, max_ms)
  def weighted_choice[T: str](seed: int, weights: Mapping[T, float]) -> T: ...   # clés triées ; poids nuls ignorés
  # core/phash.py
  def phash_distance(a: str, b: str) -> int: ...                  # hex 16 caractères, distance de Hamming
  def min_phash_distance(a: Sequence[str], b: Sequence[str]) -> int | None: ...
  # db/registry.py
  VerdictCode = Literal["ok", "owner", "overlap", "phash", "same_account_part"]
  @dataclass(frozen=True)
  class Verdict: ok: bool; code: VerdictCode; message: str
  class RegistryRefused(RushesError): ...
  def can_assign(session: Session, segment_id: int, account_id: str, *, tolerance_ms: int, phash_max: int) -> Verdict: ...
  def assign(session: Session, segment_id: int, account_id: str, *, at: datetime, tolerance_ms: int, phash_max: int) -> Verdict: ...
  ```
  `can_assign` n'écrit jamais ; `assign` appelle `can_assign` puis insère `Assignment` dans la même transaction
  (session de l'appelant, ouverte par `db.engine.write_session` : `BEGIN IMMEDIATE`, sinon `assign` lève `RegistryRefused` ;
  `can_assign` reste utilisable sur toute session, R33). Ordre des contrôles : propriétaire (R44), chevauchement avec un passage du même épisode
  attribué à un autre compte de plus de `tolerance_ms` (R1, strictement supérieur), pHash à distance
  `<= phash_max` d'un passage attribué à un autre compte (R1), même compte : deux `part` qui se chevauchent de plus
  de `tolerance_ms` (R2). Un `bestof_item` ou `compilation_item` peut chevaucher une `part` du même compte.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_r1_overlap_over_2s_other_account_refused(session):     # chevauchement 2001 ms → code "overlap"
def test_r1_overlap_within_tolerance_allowed(session):          # 2000 ms exactement → ok
def test_r1_phash_close_other_account_refused(session):         # distance 10 → "phash" ; 11 → ok
def test_r2_part_and_bestof_same_account_allowed(session):
def test_r2_two_parts_same_passage_refused(session):            # → "same_account_part"
def test_r27_registry_applies_to_creators(session):             # mêmes cas sur un épisode de créateur
def test_r44_owner_mismatch_refused(session):                   # → "owner"
def test_r33_simulated_can_assign_writes_nothing(session):      # nombre de lignes assignments inchangé
def test_assign_inserts_only_when_ok(session):
# unitaires : merge/subtract/inside (bornes), ms_to_tc ↔ tc_to_ms aller-retour, s_to_ms(1.0005) == 1001,
# derive_seed stable (valeur figée dans le test), draw_target_ms dans [65000, 100000] sur 1000 graines,
# weighted_choice déterministe et ignorant les poids nuls, phash_distance("0"*16, "f"*16) == 64
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS ; retirer R1, R27,
  R33 (partiel) de `rules_pending.toml` (R33 reste en attente pour la tâche 15, R2 pour la tâche 17). Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(core,registry): add interval, timecode, seeded draw helpers and can_assign (R1, R2, R27, R44)`

---

### Task 4 : `media`, `ingest` local, `clean` v1, générateur de vidéos synthétiques

**Files :**
- Create : `src/rushes/media/__init__.py`, `src/rushes/media/probe.py`, `src/rushes/media/ffmpeg.py`,
  `src/rushes/media/detect.py`, `src/rushes/schemas.py`, `src/rushes/ingest/__init__.py`,
  `src/rushes/ingest/ingest.py`, `src/rushes/ingest/owner.py`, `src/rushes/clean/__init__.py`,
  `src/rushes/clean/borders.py`, `src/rushes/clean/intro.py`, `src/rushes/clean/clean.py`
- Modify : `src/rushes/steps.py`, `src/rushes/cli.py` (commandes `ingest`, `clean`)
- Test : `tests/fixtures/__init__.py`, `tests/fixtures/generator.py`, `tests/conftest.py` (fixture `synth`),
  `tests/test_media.py`, `tests/test_ingest.py`, `tests/test_clean.py`, `tests/fixtures/ffmpeg_logs/*.txt`,
  `tests/test_cli.py` (`ingest`, `clean` passent dans `IMPLEMENTED`)

**Interfaces :**
- Consomme : `Context`, `BaseStep`, `atomic_path`, `episode_key`, `Span`, `derive_seed`.
- Produit :
  ```python
  # media/probe.py
  @dataclass(frozen=True)
  class ProbeInfo:
      duration_ms: int; width: int; height: int; display_width: int; fps: Fraction; vfr: bool
      has_audio: bool; vcodec: str; acodec: str | None
  def probe(path: Path) -> ProbeInfo: ...                  # ffprobe JSON ; MediaError si illisible
  def sha256_file(path: Path) -> str: ...
  # media/ffmpeg.py
  def run_ffmpeg(args: Sequence[str | Path], *, duration_ms: int | None = None,
                 on_progress: Callable[[int], None] | None = None, threads: int = 4) -> str: ...
  # ajoute -hide_banner -nostdin -y -progress pipe:1 ; lit out_time_ms ; MediaError avec les 20 dernières lignes de stderr
  # media/detect.py (parseurs purs + appels)
  def parse_blackdetect(stderr: str) -> list[Span]: ...
  def parse_silencedetect(stderr: str) -> list[Span]: ...
  def parse_freezedetect(stderr: str) -> list[Span]: ...
  def detect_black(path: Path, *, min_ms: int) -> list[Span]: ...                     # blackdetect=d=<s>:pix_th=0.10
  def detect_silence(path: Path, *, min_ms: int, noise_db: float = -50.0) -> list[Span]: ...
  def detect_freeze(path: Path, *, min_ms: int, noise: float = 0.003) -> list[Span]: ...
  # schemas.py
  def write_json(model: BaseModel, path: Path) -> None: ...      # atomique, UTF-8, indent=1
  def read_json[M: BaseModel](model: type[M], path: Path) -> M: ...
  class CleanFile(BaseModel):  # extra="forbid"
      version: Literal[1]; episode_id: int; source_duration_ms: int; kept: tuple[int, int]
      removed: list[tuple[int, int, Literal["black", "silence", "intro", "outro", "title_card"]]]
  # clean/borders.py
  def border_spans(spans: Sequence[Span], duration_ms: int, *, min_ms: int = 2000, eps_ms: int = 100) -> list[Span]: ...
  def kept_range(duration_ms: int, removed: Sequence[Span]) -> Span: ...
  # clean/intro.py
  def match_reference_frames(video: Path, refs: Sequence[Path], *, window_ms: int = 90_000, fps: int = 2,
                             max_distance: int = 12) -> list[Span]: ...
  # ingest/owner.py
  def choose_owner(session: Session, config: Config, *, series_id: str | None, creator_id: str | None, today: date) -> str: ...
  # ingest/ingest.py et clean/clean.py
  class IngestStep(BaseStep): name = "ingest"   # éléments : fichiers de data/import/<id série ou créateur>/*
  class CleanStep(BaseStep): name = "clean"; heavy = True   # éléments : épisodes 'imported'
  ```
- Règles fixées :
  - Ingestion locale : `data/import/<id>/fichier` ; `<id>` est une série ou un créateur de la config (sinon
    élément `warned`). Copie (lien physique si possible) vers `sources/local_<sha256[:16]>.<ext>` ; épisode
    `provenance='local'`, `source_video_id=NULL`, `file_path` relatif ; fichier d'origine déplacé dans
    `import/_done/`. Un sha256 déjà connu → déplacé dans `_done/`, `skipped`. Les dossiers dont le nom commence par
    `_` (dont `_done/`) sont ignorés, sans avertissement ; un sous-dossier ordinaire est signalé (`warned`) ; un
    fichier modifié depuis moins de 60 s est laissé pour plus tard (C39).
  - Propriétaire (R4, R44) : marques candidates = celles qu'une période de `rotation` de la config couvrant `today`
    associe à la série ; **sans période couvrant `today`**, toutes les marques de la verticale (cartoons) ; une période
    couvrante qui ne liste pas la série ne laisse aucun compte candidat : `RushesError`, l'élément échoue et le fichier
    reste dans `import/` (C37) ; pour un créateur, les marques dont `creators_by_brand` contient le créateur. Parmi leurs comptes, celui qui possède le moins d'épisodes « en
    cours » = sans aucun post, ou avec au moins un post non `published` ; égalité → identifiant le plus petit. Le
    compte est recalculé après chaque épisode inséré (flush), donc deux épisodes d'un même run se répartissent.
    (La rotation générée arrive en tâche 16a ; la tâche 10b remplace ce critère par le plus petit `stock_days`.)
  - Nettoyage cartoons (R45) : noirs (`blackdetect`) et silences (`silencedetect`) de plus de 2 s (durée mesurée
    avant d'être ramenée au bord) **touchant un bord** ; un retrait compte aussi s'il touche une zone déjà retirée
    depuis un bord, quel que soit l'ordre de détection (répété jusqu'à stabilité, C38) ; carton de titre = image figée (`freezedetect`, 1,5 s minimum) commençant dans les 20 premières
    secondes et touchant le début ou une zone déjà retirée ; génériques = images de référence de la série
    (`intro_outro.reference_frames`, chemins relatifs à `config/`), dans les 90 premières et dernières secondes,
    zones touchant un bord ou une zone déjà retirée (C38). Rien n'est retiré à l'intérieur. Pour les créateurs, la tâche 24 complète.
  - `clean.mp4` normalisé : `-vf "scale=trunc(iw*sar/2)*2:ih,setsar=1,fps=30"`, `libx264 -preset veryfast
    -crf 16 -pix_fmt yuv420p`, `aac -b:a 192k -ar 48000 -ac 2` ; sans audio, ajout de
    `-f lavfi -i anullsrc=r=48000:cl=stereo -shortest`. Épisode → `cleaned`.

- [ ] **Step 1 : Générateur de vidéos synthétiques** (`tests/fixtures/generator.py`, contrat §6 de `00-master.md`)
  — niveaux fixés pour que les doublures les reconnaissent : ton de fond `sine` à amplitude 0,1 ; `beeps_ms` :
  sinus 1000 Hz à 0,9 ; `speech_like_ms` : `anoisesrc` blanc à 0,5 (rose abandonné, C41) ; `silence_ms` et `black_ms` exacts ;
  `shots_ms` : couleur de fond différente à chaque plan, `testsrc2` en incrustation mobile pour le mouvement ;
  `title_card_ms` : image fixe unie avec un motif `drawbox`, sans texte (`drawtext` absent de beaucoup de builds FFmpeg, C36 ; `freezedetect` n'exige qu'une image statique). Paramètres supplémentaires : `sar: tuple[int, int] = (1, 1)`,
  `vfr: bool = False`, `audio: bool = True`. Fixture `synth(spec) -> Path` en cache par session (`tmp_path_factory`).

- [ ] **Step 2 : Tests qui échouent**

```python
def test_probe_reads_synthetic(synth):                    # 8 s, 1280×720, 30 i/s, audio → ProbeInfo exact (±40 ms)
def test_parse_blackdetect_from_captured_log():           # tests/fixtures/ffmpeg_logs/blackdetect.txt → [(0, 3000)]
def test_ingest_copies_hashes_and_assigns_owner(ctx, synth):
def test_ingest_twice_skips_same_sha(ctx, synth):          # second dépôt du même fichier → skipped, 1 épisode
def test_ingest_unknown_folder_warns(ctx, synth):
def test_ingest_ignores_underscore_dirs(ctx, synth):       # data/import/_done/x.mp4 → aucun élément, aucun warned
def test_r4_new_episode_goes_to_current_rotation(ctx, edit_yaml):   # rotation de la config couvrant aujourd'hui → compte de la marque listée
def test_owner_balances_stock(ctx):                        # deux comptes candidats → celui qui a le moins d'épisodes en cours
def test_owner_balances_within_one_run(ctx, synth):        # deux fichiers d'une série sans rotation, un seul run → deux comptes différents
def test_border_black_over_2s_removed(ctx, synth):         # noir 0–3 s → kept commence à 3000 (±1 image)
def test_border_black_under_2s_kept(ctx, synth):           # noir 0–1,5 s → kept commence à 0
def test_r45_cartoon_interior_silence_kept(ctx, synth):    # silence 30–34 s d'une vidéo de 60 s → durée nettoyée = 60 s
def test_title_card_removed_via_freeze(ctx, synth):
def test_intro_removed_via_reference_frame(ctx, synth):    # image de référence extraite de la vidéo elle-même
def test_clean_normalizes_vfr_sar_and_missing_audio(ctx, synth):
    # source 1440×1080 SAR 4:3, VFR, sans audio → clean.mp4 : 1920×1080, SAR 1:1, 30/1 constant, AAC 48 kHz stéréo
def test_r16_clean_rerun_changes_nothing(ctx, synth):
```

- [ ] **Step 3 : Lancer** → FAIL. **Step 4 : Implémenter** `media/`, `schemas.py`, `ingest/`, `clean/` ; enregistrer
  `ingest` et `clean` dans `steps.py` et la CLI. **Step 5 : Lancer** → PASS ; retirer R4 (partiel), R45 (partiel)
  de `rules_pending.toml` si leurs autres tests sont rattachés à d'autres tâches (R45 reste pour la tâche 24).
  Suite rapide, ruff, mypy verts.

- [ ] **Step 6 : Commit proposé** — `feat(ingest,clean): local ingest with owner choice and border-only cartoon cleaning (R4, R45)`

---

### Task 5 : `analyze` v1 (plans, loudness, mouvement, parole) et `ModelHub`

**Files :**
- Create : `src/rushes/core/types.py` (`Word`, `SoundEvent`, §5.5), `src/rushes/models/base.py`,
  `src/rushes/models/vad_silero.py`, `src/rushes/media/audio.py`,
  `src/rushes/media/frames.py`, `src/rushes/analyze/__init__.py`, `src/rushes/analyze/shots.py`,
  `src/rushes/analyze/loudness.py`, `src/rushes/analyze/motion.py`, `src/rushes/analyze/speech.py`,
  `src/rushes/analyze/analyze.py`
- Modify : `src/rushes/models/hub.py`, `src/rushes/schemas.py`, `src/rushes/steps.py`, `src/rushes/cli.py`
- Test : `tests/fakes/models.py`, `tests/test_models_hub.py`, `tests/test_analyze.py`, `tests/test_cli.py` (`analyze`)

**Interfaces :**
- Consomme : `ProbeInfo`, `run_ffmpeg`, `CleanFile`, `write_json`, `Span`, `phash` (imagehash).
- Produit : Protocols et `ModelHub` de §5.5 (seul `vad()` est réel ici ; les autres accesseurs lèvent
  `NotImplementedError("tâche N")` jusqu'à leur tâche) ; schémas `ScenesFile` et `AudioFile` de §5.6 avec
  **`Shot.phash: str`** (pHash hex de l'image clé) ; et :
  ```python
  # media/audio.py
  def decode_pcm16k(path: Path, *, span: Span | None = None) -> NDArray[np.float32]: ...   # ffmpeg -f f32le -ac 1 -ar 16000
  # media/frames.py
  def sample_gray(path: Path, *, fps: float, width: int) -> Iterator[tuple[int, NDArray[np.uint8]]]: ...  # (t_ms, image)
  def extract_frame(path: Path, t_ms: int, out: Path, *, width: int = 512) -> None: ...
  # analyze/shots.py
  def detect_shots(path: Path, *, adaptive_threshold: float = 3.0, min_scene_len: int = 15,
                   window_width: int = 2, min_content_val: float = 15.0) -> list[Span]: ...
  # analyze/loudness.py
  def loudness_track(path: Path) -> tuple[float, list[LoudPoint]]: ...   # (intégrée, court terme toutes les 100 ms)
  # analyze/motion.py
  def shot_motion(path: Path, shots: Sequence[Span], *, fps: float = 4.0, width: int = 160) -> list[float]: ...  # 0–1
  # analyze/speech.py
  def speech_spans(vad: Vad, pcm16k: NDArray[np.float32]) -> list[Span]: ...
  class AnalyzeStep(BaseStep): name = "analyze"; heavy = True   # épisodes 'cleaned'
  ```
- Règles fixées :
  - Plans : `scenedetect.detect(str(path), AdaptiveDetector(...), backend="opencv")`, paramètres explicites
    (spec, Plateformes et bibliothèques).
  - Loudness : `-af ebur128=framelog=verbose` avec `-v verbose`, lignes `t: … S: …` (court terme) ; `-inf` → -70,0 ;
    intégrée lue dans le résumé `I:`.
  - Mouvement d'un plan : moyenne des différences absolues entre images grises successives (4 i/s, 160 px de
    large), divisée par 255.
  - Image clé : milieu du plan, JPEG 512 px dans `episodes/<e_key>/keyframes/<shot_id>.jpg` ; pHash par
    `imagehash.phash`.
  - `ModelHub` : `vad`, `faces`, `sounds`, `tts` légers ; `transcriber`, `clip`, `llm` lourds (charger un lourd
    décharge le précédent, puis `gc.collect()`). R19 : chaque modèle est chargé une fois par étape, jamais par
    élément ; un seul modèle lourd en mémoire à la fois. `loads()` compte les chargements par nom.
  - `FakeVad` (`tests/fakes/models.py`) : fenêtres de 20 ms dont RMS > 0,25 **et** taux de passages par zéro
    > 0,2, regroupées si elles durent au moins 250 ms (reconnaît `speech_like_ms`, ignore les bips à 1000 Hz).
  - Lignes `scenes` : une par plan (`loudness`, `motion`, `speech` = part du plan couverte par la parole).
    Épisode → `analyzed`.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_r19_models_loaded_once_per_step(ctx):             # étape factice à 3 éléments, hub.vad() à chaque élément → loads()["vad"] == 1
def test_r19_one_heavy_model_at_a_time(tmp_path):          # fabriques lourdes factices : clip puis transcriber → weakref de clip morte
def test_release_drops_heavy_model(tmp_path):
def test_detect_shots_on_synthetic(synth):                 # shots_ms=[3000, 7000] sur 10 s → [(0,3000),(3000,7000),(7000,10000)] ± 34 ms
def test_loudness_track_peaks_on_beep(synth):              # bip 4–5 s → maximum court terme entre 4000 et 7000 ms
def test_motion_higher_on_moving_shot(synth):
def test_fake_vad_finds_speech_like_not_beeps(synth):      # speech_like 2–4 s, bip 6–7 s → [(2000±60, 4000±60)]
def test_scenes_json_matches_schema(ctx, synth):           # read_json(ScenesFile, …) ok ; nombre de lignes scenes == len(shots)
def test_r16_analyze_rerun_changes_nothing(ctx, synth):
@pytest.mark.slow
def test_silero_vad_runs_on_noise(tmp_path):               # vrai modèle : liste de Span sans exception
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS ; retirer R19 de
  `rules_pending.toml`. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(analyze): shots, short-term loudness, motion and silero speech with model hub (R19)`

---

### Task 6 : `cut` parties (programmation dynamique, familles)

**Files :**
- Create : `src/rushes/core/dp.py`, `src/rushes/core/scoring.py`, `src/rushes/cut/__init__.py`, `src/rushes/cut/parts.py`
- Modify : `src/rushes/config.py` (`CutSettings`), `src/rushes/steps.py`, `src/rushes/cli.py`
- Test : `tests/test_core_dp.py`, `tests/test_cut.py`, `tests/test_cli.py` (`cut`)

**Interfaces :**
- Consomme : `ScenesFile`, `AudioFile`, `derive_seed`, `draw_target_ms`, `weighted_choice`, `Span`.
- Produit :
  ```python
  # config.py
  class CutSettings(_Model):
      loud_weight: float = 1.0; target_weight: float = 1.0      # par seconde² d'écart à la cible
      motion_start_bonus: float = 0.5; speech_margin_ms: int = 150; peak_guard_ms: int = 1500
      quiet_point_step_ms: int = 2000; quiet_point_penalty: float = 1.0
      start_motion_min: float = 0.15; start_shift_ms: int = 2000   # début accrocheur (spec, partition étape 5)
  class Settings: ...  # + cut: CutSettings = CutSettings()
  # core/scoring.py
  def cut_point_costs(points_ms: Sequence[int], *, loudness: Sequence[tuple[int, float]], speech: Sequence[Span],
                      peaks_ms: Sequence[int], motion_after: Callable[[int], float], s: CutSettings) -> list[float]: ...
  # core/dp.py
  def partition(points_ms: Sequence[int], point_cost: Sequence[float], *, duration_ms: int,
                targets_ms: Sequence[int], min_ms: int, max_ms: int, target_weight: float) -> list[Span]: ...
  # cut/parts.py
  class CutStep(BaseStep): name = "cut"   # épisodes 'analyzed' sans segment 'part'
  ```
- Algorithme (non déterminé par la signature) :

```text
cut_point_costs : pour chaque point p
  inf si p est dans un intervalle de parole élargi de speech_margin_ms, ou si un pic est dans ]p, p + peak_guard_ms] (R5)
  sinon loud_weight × z(loudness court terme à p)   (z calculé sur l'épisode ; creux = coût négatif)
        − motion_start_bonus × motion_after(p)       (mouvement moyen des 2 s qui suivent, 0–1)
  le point 0 et le point final ont un coût 0
partition : points triés, p0 = 0 ; dp[k][j] = coût minimal pour couvrir [0, p_j] en k parties
  dp[0][0] = 0 ; dp[k][j] = min_i dp[k-1][i] + cost[j] + target_weight × ((p_j − p_i − targets[k-1]) / 1000)²
                 pour min_ms ≤ p_j − p_i ≤ max_ms
  fin admissible : j tel que duration_ms − p_j < min_ms (reliquat abandonné) ; coût du point final compté 0
  solution, ordre lexicographique sur (k, j) admissibles : plus grande durée couverte p_j d'abord, puis coût
    minimal, puis moins de parties (le dernier morceau trop court est donc fusionné au précédent dès que max_ms le
    permet, sinon abandonné : spec, partition étape 4)
  aucune solution → []
Début accrocheur (après partition) : pour chaque frontière p (début d'une partie, 0 compris) avec
  motion_after(p) < start_motion_min, chercher le premier début de plan p' dans ]p, p + start_shift_ms] de coût
  fini ; si la partie qui commence (et celle qui finit en p, le cas échéant) reste dans [min_ms, max_ms], la
  frontière passe à p' (fin de la partie k−1 = début de la partie k = p'). Sinon rien ne bouge.
Points candidats : 0, débuts de plans, duration_ms ; si partition() renvoie [] : ajouter des points calmes
  (minimum local de loudness tous les quiet_point_step_ms, coût + quiet_point_penalty) et recommencer une fois.
Famille des parties d'un épisode : toujours "monetizable" (spec, Familles de durée : `long` est réservé au best-of et
  aux compilations ; leur famille est tirée selon les parts, tâches 13 et 30).
targets[k] = draw_target_ms(derive_seed(episode_id, "cut", k), min_s, max_s) pour k < duration_ms // min_ms + 1.
peaks_ms = [] en phase 1 (aucun signal « Les plus revus » avant la tâche 13, qui modifie cut/parts.py pour lire
  la heatmap de video_signals par heatmap_peaks_ms).
```

  Chaque partie devient un `Segment(kind="part", family, target_ms, seed=derive_seed(episode_id, "cut"),
  score=1/(1+max(0, coût du point de fin)), status="proposed", phash_json=pHash des plans qu'elle chevauche)`.
  Épisode trop court ou partition vide : aucune ligne, élément `warned` avec le message « épisode trop court pour
  la famille <f> (<durée> s) » ou « aucun point de coupe admissible ».

- [ ] **Step 1 : Tests qui échouent**

```python
@pytest.mark.parametrize(("points", "costs", "duration", "targets", "expected"), REFERENCE_CASES)  # 6 cas écrits à la main
def test_partition_reference_cases(points, costs, duration, targets, expected):
def test_r5_no_cut_during_speech():                 # parole 60–75 s → aucune borne de partie dans [59850, 75150]
def test_r5_no_cut_1500ms_before_peak():            # pic à 80 000 → aucune borne dans [78500, 80000[
def test_r6_segment_out_of_bounds_never_proposed():  # 200 épisodes synthétiques en données (graines) → 65000 ≤ durée ≤ 100000
def test_r6_target_seed_recorded(ctx):               # segments.seed et target_ms renseignés ; relance → mêmes bornes
def test_tail_dropped_when_too_short():
    # points [0, 95000, 190000, 200000], durée 200000, bornes 65–100 s, cibles 95000
    # → [(0, 95000), (95000, 190000)] (fusion impossible : 105 s > max)
def test_tail_merged_when_max_allows():
    # points [0, 75000, 140000, 150000], durée 150000, bornes 65–100 s, cibles 75000
    # → [(0, 75000), (75000, 150000)] (reliquat de 10 s fusionné, jamais abandonné)
def test_start_shifted_to_next_shot():
    # frontière à 70000, mouvement nul sur [70000, 72000], plan suivant à 71000, parties restant dans les bornes
    # → fin de la partie 1 == début de la partie 2 == 71000
def test_r43_episode_always_gets_parts(ctx, synth):  # 4 min, plans toutes les 5 s → ≥ 2 parties consécutives (fin k == début k+1)
def test_cut_short_episode_gives_no_part_and_warns(ctx, synth):     # 50 s → 0 segment, StepEvent "warned"
def test_cut_without_shot_changes_uses_quiet_points(ctx, synth):    # 3 min sans changement de plan → ≥ 1 partie
def test_r16_cut_rerun_changes_nothing(ctx, synth):
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS ; R5 reste dans
  `rules_pending.toml` jusqu'à la tâche 13 (pics « Les plus revus ») ; R6 et R43 restent en attente pour leurs autres tâches si besoin. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(cut): dynamic-programming part partition with seeded targets (R5, R6, R43)`

---

### Task 7a : Rendu — canevas, mises en page `square` centrée et `full`, encodage, loudnorm

**Files :**
- Create : `src/rushes/render/__init__.py`, `src/rushes/render/canvas.py`, `src/rushes/render/loudnorm.py`,
  `src/rushes/render/encode.py`, `src/rushes/render/render.py`, `src/rushes/media/verify.py`
- Modify : `src/rushes/config.py` (`RenderSettings`), `src/rushes/steps.py`, `src/rushes/cli.py`
- Test : `tests/test_render_canvas.py`, `tests/test_render.py`, `tests/helpers.py` (`make_post`),
  `tests/test_cli.py` (`render`)

**Interfaces :**
- Consomme : `Segment`, `Post`, `CleanFile`, `run_ffmpeg`, `probe`, `atomic_path`, `post_key`.
- Produit :
  ```python
  # config.py
  class RenderSettings(_Model):
      preset: Literal["ultrafast", "veryfast", "fast", "medium", "slow"] = "medium"; crf: int = Field(19, ge=0, le=51)
  class Settings: ...  # + render: RenderSettings = RenderSettings()
  # render/canvas.py
  CANVAS_W, CANVAS_H, VIDEO_TOP = 1080, 1920, 420
  @dataclass(frozen=True)
  class Rect: x: int; y: int; w: int; h: int
  def video_rect(layout: Layout, src_w: int, src_h: int) -> Rect: ...
  #   square (0,420,1080,1080) ; portrait (0,420,1080,1440) ; vertical (0,0,1080,1920) ;
  #   full : hauteur paire h = min(round(1080 × src_h / src_w), 1020) (1020 = 1920 − 420 − 480), largeur paire
  #   w = round(h × src_w / src_h) ≤ 1080, centrée : Rect((1080 − w) // 2, 420, w, h) ; aucune image perdue
  def plate_filter(layout: Layout, src_w: int, src_h: int, *, crop_x: str | None = None) -> str: ...
  #   crop_x : expression FFmpeg du bord gauche (fournie par le recadrage, tâche 34) ; None = centré
  # render/loudnorm.py
  @dataclass(frozen=True)
  class LoudnormMeasure: input_i: float; input_tp: float; input_lra: float; input_thresh: float; target_offset: float
  def measure(src: Path, span: Span) -> LoudnormMeasure: ...
  def second_pass_filter(m: LoudnormMeasure) -> str: ...
  #   "loudnorm=I=-14:TP=-1.5:LRA=11:measured_I=…:measured_TP=…:measured_LRA=…:measured_thresh=…:offset=…:linear=true,aresample=48000"
  # render/encode.py
  def encode_args(s: RenderSettings) -> list[str]: ...
  #   -c:v libx264 -preset <p> -crf <c> -profile:v high -pix_fmt yuv420p -r 30 -fps_mode cfr
  #   -c:a aac -b:a 160k -ar 48000 -ac 2 -movflags +faststart -threads 4
  def render_plate(src: Path, span: Span, layout: Layout, out: Path, *, s: RenderSettings,
                   crop_x: str | None = None, on_progress: Callable[[int], None] | None = None) -> None: ...
  # media/verify.py
  def check_delivery(path: Path) -> list[str]: ...   # violations R7 en français ; [] = conforme
  # render/render.py
  RENDER_PLATE_VERSION = "plate-1"
  class RenderStep(BaseStep): name = "render"; heavy = True
  ```
- Règles fixées :
  - Fond (toutes les mises en page sauf `vertical`) : copie de la source à un quart de résolution, floutée :
    `scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,scale=1080:1920`.
  - `square` centré : `crop=min(iw\,ih):min(iw\,ih)` centré puis `scale=1080:1080`, posé sur `video_rect`.
  - Sélection : posts `planned` dont le segment est `approved` ; posts `rendered` dont `render_version` est NULL ou
    diffère de la version courante. Une montée de `RENDER_*_VERSION` ne touche jamais un post `ready` (Louis l'a
    peut-être programmé) : seul un motif explicite (accroche, numéro, licence, campagne) le remet en `rendered` avec
    `render_version = NULL` (tâches 16b, 36, 26, 40a). Mise en page : `posts.layout`, sinon `layouts.preference[0]`.
  - Le rendu écrit `renders/<p_key>.plate.mp4` (vidéo posée + loudnorm), puis la vidéo finale
    `renders/<p_key>.mp4` (en 7a, simple copie de la plaque ; la tâche 7b ajoute la passe d'incrustations) ;
    `check_delivery` doit renvoyer `[]`, sinon `failed`. Post → `rendered`, `render_version` renseigné.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_video_rect_per_layout():                     # valeurs exactes ci-dessus ; full 4:3 → Rect(0, 420, 1080, 810) ; full 1080×1920 → Rect(253, 420, 574, 1020)
def test_version_bump_never_rerenders_ready_post(ctx):  # ancienne version : post 'rendered' re-rendu, post 'ready' intact (fichiers, render_version)
def test_r8_square_is_default_layout(ctx):            # post sans layout → rendu en square
def test_full_layout_keeps_whole_image():             # plate_filter("full", 1440, 1080) ne contient pas "crop=" sur la vidéo principale
def test_r7_render_matches_ffprobe_spec(ctx, synth):  # segment de 4 s, preset ultrafast → check_delivery == []
def test_r7_integrated_loudness_minus14(ctx, synth):  # source à −30 LUFS → sortie −14 ± 1 LU (ebur128)
def test_check_delivery_reports_wrong_fps(tmp_path, synth):   # vidéo à 25 i/s → message contenant « 30 i/s »
def test_r3_unapproved_never_rendered(ctx):           # segment 'proposed' → aucun rendu
def test_interrupted_render_leaves_no_final_file(ctx, synth, monkeypatch):
def test_r16_render_rerun_changes_nothing(ctx, synth):
@pytest.mark.slow
def test_r7_full_length_render_medium_preset(ctx, synth):     # partie de 70 s, preset medium
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS ; retirer R7 de
  `rules_pending.toml`. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(render): 9:16 canvas with square and full layouts, two-pass loudnorm (R7, R8)`

---

### Task 7b : Rendu — habillage (bandeau, accroche, barre, carte de fin), charte, module `base`

**Files :**
- Create : `config/charter.yaml`, `config/avatars/<marque>.png` (déjà fournis par Louis, 800 × 800 RGBA ; en test, un
  PNG synthétique dans `config_dir`), `src/rushes/assets/fonts/` (`LuckiestGuy-Regular.ttf` sous licence Apache 2.0,
  `BebasNeue-Regular.ttf` et `NotoEmoji-Regular.ttf` sous OFL, `LICENSE-Apache-2.0.txt`, `OFL.txt`, `SOURCES.md` avec URL et sha256 de chaque fichier),
  `src/rushes/render/charter.py`, `src/rushes/render/text.py`, `src/rushes/render/overlays.py`,
  `src/rushes/queue/__init__.py`, `src/rushes/queue/numbering.py` (sans import de `rushes.render` : `serve` le lit,
  tâche 36), `src/rushes/originality/__init__.py`, `src/rushes/originality/base.py`
- Modify : `src/rushes/render/render.py`, `src/rushes/config.py` (chargement de `charter.yaml`), `pyproject.toml`
  (fichiers de police inclus dans le paquet)
- Test : `tests/test_charter.py`, `tests/test_overlays.py`, `tests/test_render_overlays.py`

**Interfaces :**
- Consomme : `Rect`, `video_rect`, `render_plate`, `encode_args`, `RenderStep`, `Config`.
- Produit :
  ```python
  # render/charter.py
  class SafeMargins(_Model): top: int = 150; bottom: int = 480; left: int = 60; right: int = 140
  class Copy(_Model):
      banner: str; next_part: str; last_part: str; follow: str; subscribe: str   # gabarits str.format
  class BrandCharter(_Model):
      font: str; emoji_font: str = "NotoEmoji-Regular.ttf"
      banner_bg: str; banner_fg: str; highlight: str; progress: str     # "#RRGGBB"
      safe: SafeMargins = SafeMargins()
      texts: dict[str, Copy] = Field(alias="copy")                      # clé = langue ; clé YAML `copy` (évite BaseModel.copy)
  def load_charter(config_dir: Path) -> dict[str, BrandCharter]: ...    # ConfigError si une marque ou `config/avatars/<marque>.png` manque
  # couleurs de départ (L35, relevées sur les avatars ; banner_bg, banner_fg, highlight, progress) :
  #   saturday_toons #3B1F0E #FFF4DC #FFB627 #FF6B35 ; toon_tape #1E1B2E #FFFFFF #FF4FA3 #FFD23F ;
  #   instant_culte #121316 #F5F1E8 #E63946 #E63946 ; peak_moments #0E1A2B #F5F1E8 #F2A541 #F2A541
  # render/text.py
  def fit_text(text: str, *, font: Path, emoji_font: Path, box: Rect, max_px: int, min_px: int,
               max_lines: int = 2) -> Image.Image: ...                 # RGBA ; réduit, puis 2 lignes, puis « … »
  # queue/numbering.py (la tâche 16b y ajoute stale_numbers)
  def part_number(session: Session, post_id: int) -> int | None: ...
  #   rang (1, 2, …) parmi les posts 'part' non écartés du même épisode et du même compte, ordonnés par start_ms
  # originality/base.py
  @dataclass(frozen=True)
  class RenderJob:
      post_id: int; account_id: str; handle: str; language: str; title: str; layout: Layout; duration_ms: int
      part_number: int | None; bestof_title: str | None; hook_text: str | None; next_label: str | None
      is_last_part: bool; charter: BrandCharter; avatar: Path; src_w: int; src_h: int
  @dataclass(frozen=True)
  class Overlay: png: Path; x: int; y: int; start_ms: int; end_ms: int
  @dataclass(frozen=True)
  class AudioLayer: wav: Path; start_ms: int; duck_db: float
  @dataclass(frozen=True)
  class RenderPlan: overlays: list[Overlay]; audio_layers: list[AudioLayer]; progress_bar: bool; ai_voice: bool
  class OriginalityModule(Protocol):
      name: ClassVar[Originality]
      def plan(self, job: RenderJob, workdir: Path) -> RenderPlan: ...
  class BaseModule: name = "base"   # bandeau, accroche, barre, carte de fin
  # originality/__init__.py
  def module_for(name: Originality) -> OriginalityModule: ...   # "advanced" lève NotImplementedError("tâche 35") jusqu'à la tâche 35
  # render/overlays.py
  def overlay_texts(job: RenderJob) -> list[str]: ...           # tous les textes incrustés, pour les tests R9
  def render_overlays(plate: Path, plan: RenderPlan, out: Path, *, duration_ms: int, bar_rect: Rect,
                      bar_color: str, s: RenderSettings) -> None: ...
  RENDER_OVERLAY_VERSION = "ov-1"
  ```
- Textes exacts (`config/charter.yaml`, clé `copy`, champ `texts`) :
  - `en` : `banner: "{title} — Part {n}"`, `next_part: "Part {n} next"`, `last_part: "Watch {other} next"`,
    `follow: "Follow for more"`, `subscribe: "Follow"`.
  - `fr` : `banner: "{title} — Partie {n}"`, `next_part: "Partie {n} ensuite"`, `last_part: "À voir ensuite : {other}"`,
    `follow: "Abonne-toi pour la suite"`, `subscribe: "Abonne-toi"`.
  - Best-of et compilation : bandeau = `bestof_title` seul, sans numéro (R9).
- Placement : bandeau dans `Rect(left, 230, 1080 − left − right, 160)` (tiers haut) ; accroche dans
  `Rect(left, 460, 1080 − left − right, 200)` de 0 à 2 500 ms (juste sous le bandeau ; le bas de l'écran, jusqu'à la
  marge basse de 480 px, est réservé aux sous-titres de la tâche 25) ; carte de
  fin centrée sur `video_rect` pendant les 2 000 dernières ms, sur tous les posts ; barre de progression de 12 px collée sous
  `video_rect` (une source `color` en `overlay=x='-W+W*t/<durée>'`). Les PNG sont produits par Pillow et passés
  en entrées FFmpeg (aucun texte dans une chaîne de filtre, donc aucun échappement de chemin).
- Polices (choix de Louis, L35, revues sur des rendus d'exemple, checklist de lancement) : cartoons
  `LuckiestGuy-Regular.ttf`, créateurs `BebasNeue-Regular.ttf`, emoji `NotoEmoji-Regular.ttf` ; un caractère absent de la
  table `cmap` de la police principale (lue par fontTools) est dessiné avec la police emoji.
- Rendu léger : si la plaque existe avec la version `RENDER_PLATE_VERSION`, seule la passe d'incrustations est
  refaite (changement d'accroche ou de numéro). `render_version = f"{RENDER_PLATE_VERSION}+{RENDER_OVERLAY_VERSION}"`.
- Module d'originalité : `module_for(posts.originality)`, jamais la config directement (la valeur est posée à
  l'attribution, tâche 8a, et réalignée sur la config par la file, tâche 16b).
- Dernière partie : `is_last_part` vrai quand aucun autre segment `part` de l'épisode n'a un `start_ms` plus grand ;
  `{other}` = une autre série active du compte (tirage à graine sur le post), sinon le texte `follow`.
- Carte de fin (décision de Louis, L10), de haut en bas : photo de profil de la marque (`config/avatars/<marque>.png`,
  recadrée en disque de 240 px par Pillow), `Account.handle` du compte tel qu'écrit (il commence déjà par `@`),
  bouton `subscribe` (fond `highlight`), puis la ligne `next_part` ou `last_part` pour une partie ; rien de plus pour
  un best-of ou une compilation. Aucune seconde ajoutée à la vidéo.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_charter_requires_every_brand(config_dir, edit_yaml):
def test_r9_total_parts_never_shown():
    for job in jobs_for_parts(1, 2, 3, last=3):
        assert not any(re.search(r"\d+\s*(/|of|sur)\s*\d+", t) for t in overlay_texts(job))
def test_r9_text_inside_safe_margins(tmp_path):
    plan = BaseModule().plan(job(), tmp_path)
    for o in plan.overlays:
        w, h = Image.open(o.png).size
        assert o.x >= 60 and o.x + w <= 1080 - 140 and o.y >= 150 and o.y + h <= 1920 - 480
def test_banner_long_unicode_title_fits_safe_zone(tmp_path):   # "Les Zinzins de l'Espace 🚀 — Partie 12", charte fr
def test_bestof_has_no_part_number():
def test_last_part_end_card_points_to_other_series():
def test_end_card_shows_avatar_handle_and_subscribe(tmp_path):   # overlay_texts contient job.handle ("@saturdaytoons", un seul @) et "Follow" ; un overlay avatar 240×240
def test_end_card_subscribe_in_brand_language(tmp_path):         # charte fr → "Abonne-toi", jamais "Follow"
def test_bestof_end_card_has_no_next_line(tmp_path):
def test_charter_requires_avatar_file(config_dir, edit_yaml):    # config/avatars/toon_tape.png absent → ConfigError qui nomme la marque
def test_part_number_ignores_discarded_posts(session):
def test_light_rerender_reuses_plate(ctx, synth):     # changer hook_text → sha256 de la plaque inchangé, vidéo finale changée
def test_render_works_with_unicode_path(tmp_path, config_dir, synth):   # data_dir = tmp_path / "data ü dir"
def test_module_for_base_by_default(ctx):
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter** (polices téléchargées depuis le dépôt Google Fonts à un
  commit figé, sha256 notés dans `SOURCES.md`). **Step 4 : Lancer** → PASS ; R9 reste dans
  `rules_pending.toml` (valeur 36 : la renumérotation est à la tâche 16b, le bouton de la page à la tâche 36). Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(render): banner, hook, progress bar and end card overlays with brand charter (R9)`

---

### Task 8a : Approbation v0, attribution, métadonnées (légende, hashtags, couverture)

**Files :**
- Create : `src/rushes/approve/__init__.py`, `src/rushes/approve/approve.py`, `src/rushes/assign/__init__.py`,
  `src/rushes/assign/assign.py`, `src/rushes/db/derived.py`, `src/rushes/meta/__init__.py`,
  `src/rushes/meta/hashtags.py`, `src/rushes/meta/caption.py`, `src/rushes/meta/cover.py`, `src/rushes/meta/meta.py`,
  `src/rushes/meta/templates/caption.en.j2`, `src/rushes/meta/templates/caption.fr.j2`
- Modify : `src/rushes/schemas.py` (`MetaFile`), `src/rushes/steps.py`, `src/rushes/cli.py`
- Test : `tests/test_derived.py`, `tests/test_approve_v0.py`, `tests/test_assign.py`, `tests/test_hashtags.py`,
  `tests/test_caption.py`, `tests/test_cover.py`, `tests/test_meta.py`, `tests/test_cli.py` (`approve`, `assign`, `meta`)

**Interfaces :**
- Consomme : `can_assign`, `assign`, `Config.effective_originality`, `hashtag_from_name`, `normalize_hashtag`,
  `derive_seed`, `BrandCharter`, `video_rect`, `ScenesFile`, `AudioFile`.
- Produit :
  ```python
  # db/derived.py (§5.7 de 00-master.md ; table d'historique : une ligne par record)
  def record(session: Session, key: str, scope: str, value: object, *, source: str, confidence: float, at: datetime) -> None: ...
  def resolve[T](session: Session, key: str, scope: str, *, config_value: T | None, default: T,
                 parse: Callable[[object], T]) -> Resolved[T]: ...
  # approve/approve.py
  class ApproveStep(BaseStep): name = "approve"   # v0 : segments 'proposed' de kind 'part'
  def rule_violations(session: Session, segment_id: int, ctx: Context) -> list[str]: ...   # R5, R6, R1 simulée
  # assign/assign.py
  class AssignStep(BaseStep): name = "assign"     # segments 'approved' de kind 'part' sans attribution
  #   (bestof_item et compilation_item passent seulement par l'attribution de leur compilation, tâche 13 : R2)
  def is_test_post(config: Config, session: Session, episode_id: int) -> bool: ...   # R14, R22, R32
  # meta/hashtags.py
  def resolve_hashtags(*, required: Sequence[str], tokens: Mapping[str, str], pool: Sequence[str],
                       platform: Platform, seed: int, max_total: int = 5) -> list[str]: ...
  def default_pool(name: str, vertical: Vertical) -> list[str]: ...
  #   cartoons : [f"{tag}clips", f"{tag}cartoon", "#classiccartoons"] ;
  #   creators : [f"{tag}clips", f"{tag}bestof", f"{tag}moments", f"{tag}fan"] (R13 : jusqu'à 4 tournants sur Instagram)
  # meta/caption.py
  def build_caption(*, platform: Platform, language: str, hook: str | None, name: str, part_number: int | None,
                    situation: str | None, question: str, credit: str | None, hashtags: Sequence[str],
                    test: bool) -> str: ...
  def caption_problems(caption: str, *, platform: Platform, name: str) -> list[str]: ...
  # meta/cover.py
  def cover_candidates(keyframes: Sequence[tuple[int, Path]], *, n: int = 3) -> list[tuple[int, float]]: ...  # (t_ms, score)
  def render_cover(frame: Path, *, layout: Layout, words: str, part_number: int | None,
                   charter: BrandCharter, language: str, out: Path) -> None: ...
  # schemas.py
  class MetaFile(BaseModel):
      version: Literal[1]; post_id: int; caption: str; hashtags: list[str]; cover_frame_ms: int
      hook_applied: str | None; hook_alternatives: list[str]
  class MetaStep(BaseStep): name = "meta"         # posts 'rendered' dont caption IS NULL
  ```
- Règles fixées :
  - Approbation v0 (remplacée par la tâche 15) : `approved` si `rule_violations` est vide, sinon `needs_review`
    avec les violations dans le message de l'événement ; le score n'est pas encore comparé au seuil.
  - Attribution : vers `episodes.owner_account_id` uniquement, par `registry.assign` dans une `write_session` (`BEGIN IMMEDIATE`) ; refus → `warned` avec le
    message du verdict (« refus du registre : … ») ; succès → `Post(status="planned", family, layout=preference[0],
    originality=effective_originality, is_test=is_test_post(...))`. `is_test_post` : série non `licensed` ou
    `origin='prospect'`, créateur non `authorized`.
  - Hashtags : jetons `{series}` = `hashtag_from_name(title)`, `{creator}` = `effective_hashtag`,
    `{nostalgia}` = `resolve("hashtags.nostalgia_instagram", "cartoons", default="#nostalgia")` ; réserve =
    `resolve("hashtags.pool", <id>, config_value=branding.hashtags_pool or None, default=default_pool(...))` ;
    normalisation, dédoublonnage insensible à la casse, `#viral`/`#foryou` retirés, `#fyp` retiré sur Instagram ;
    obligatoires d'abord, puis réserve tirée à graine `derive_seed(post_id, "hashtags")` jusqu'à 5.
  - Légende (gabarit Jinja `caption.<langue>.j2`, surchargeable par `config/templates/<id série ou créateur>.j2`),
    forme exacte (`part_label` = « Part N » en anglais, « Partie N » en français ; `hashtags` joints par une espace) :
    `{% if hook %}{{ hook }} {% endif %}{{ name }}{% if part_label %}, {{ part_label }}{% endif %}{% if situation %} {{ situation }}{% endif %}. {{ question }}{% if credit %} {{ credit }}{% endif %} {{ hashtags }}`.
    `situation` vaut `None` tant qu'aucune tâche ne la produit. Questions (tirage à graine) : en `["Who wins? 👇", "Did you see it coming? 👀", "Best moment? 👇"]`,
    fr `["Qui gagne ? 👇", "Tu l'avais vu venir ? 👀", "Le meilleur moment ? 👇"]`. TikTok : 50 à 150
    caractères — au-delà, retirer la situation puis la question ; nom dans les 80 premiers (R12), sinon l'accroche
    est retirée. Instagram : nom dans les 125 premiers. Post de test : préfixe « NE PAS PUBLIER — ».
  - Couverture : 3 candidates parmi les images clés des plans du segment, score = netteté (variance du laplacien
    normalisée) × (0,5 + mouvement du plan) ; la meilleure est composée en 1080×1920 avec la mise en page du post,
    2 à 4 mots en gros (les 4 premiers mots de l'accroche, sinon le nom) et « Part N » s'il y a lieu, dans la zone
    centrale (recadrages 4:5 et 3:4 d'Instagram). Fichiers : `renders/<p_key>.cover.jpg`, `renders/<p_key>.meta.json`.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_derived_resolve_order_and_history(session):     # config > dérivée > défaut ; deux record → 2 lignes d'historique
def test_approve_v0_flags_rule_violation(ctx):           # segment hors bornes → needs_review
def test_r3_unapproved_never_assigned(ctx):
def test_assign_refusal_is_warned_with_reason(ctx):
def test_r2_assign_ignores_bestof_items(ctx):            # segment 'bestof_item' approved → aucun post, aucune ligne assignments
def test_r14_test_flag_for_unlicensed_series(ctx):        # is_test == True
def test_r13_required_hashtags_tiktok_cartoons():
    tags = resolve_hashtags(required=["#fyp", "#nostalgiatok", "#cartoons", "{series}"],
                            tokens={"{series}": "#tomandjerry"}, pool=["#slapstick"], platform="tiktok", seed=1)
    assert tags == ["#fyp", "#nostalgiatok", "#cartoons", "#tomandjerry", "#slapstick"]
def test_r13_instagram_max_5_no_fyp():
def test_r13_never_viral_foryou():                        # réserve ["#viral", "#foryou", "#x"] → seul "#x" retenu
def test_r12_name_within_80_chars_tiktok():               # accroche de 120 caractères → nom dans les 80 premiers
def test_r12_name_within_125_chars_instagram():
def test_caption_length_50_150_tiktok():
def test_caption_matches_spec_example():
    # hook "Tom really thought this plan would work 😭", name "Tom and Jerry", part 2, question "Who wins? 👇",
    # hashtags de l'exemple → texte exact de la spec
def test_test_post_caption_prefixed():
def test_cover_three_candidates_best_chosen(tmp_path):
def test_r16_meta_rerun_changes_nothing(ctx, synth):
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS ; retirer R12, R13 de
  `rules_pending.toml` (R3 reste en attente jusqu'à la tâche 32). Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(meta,assign): v0 approval, owner assignment, captions, required hashtags and covers (R3, R12, R13)`

---

### Task 8b : Paquet (`outbox/`, `upload.json`, `INDEX.html`), créneaux simples, `rushes run`

**Files :**
- Create : `src/rushes/package/__init__.py`, `src/rushes/package/outbox.py`, `src/rushes/package/upload.py`,
  `src/rushes/package/index_html.py`, `src/rushes/package/templates/index.html.j2`,
  `src/rushes/queue/slots.py` (`queue/__init__.py` existe depuis 7b), `src/rushes/db/trace.py`
- Modify : `src/rushes/schemas.py` (`UploadFile`), `src/rushes/steps.py` (`RUN_ORDER`), `src/rushes/cli.py` (`run`)
- Test : `tests/test_slots.py`, `tests/test_upload.py`, `tests/test_package.py`, `tests/test_trace.py`,
  `tests/test_run_e2e.py`, `tests/test_cli.py` (`package`, `run`)

**Interfaces :**
- Consomme : `MetaFile`, `check_delivery`, `post_key`, `Config`, `run_pipeline`, toutes les étapes précédentes.
- Produit :
  ```python
  # queue/slots.py (appelé par la file de la tâche 16b)
  def plan_slots(*, slots: Sequence[str], posts_per_day: float, tz: ZoneInfo, start: datetime,
                 taken: Sequence[datetime], n: int) -> list[datetime]: ...   # UTC
  #   jours locaux (fuseau de la marque) à partir du lendemain de start, indexés en absolu : d = date_locale.toordinal()
  #   (jamais relatif à start, qui change à chaque run) ; quota q_d = floor((d+1)×ppd) − floor(d×ppd) ;
  #   heures = slots[(d + j) % len(slots)] pour j < q_d ; taken retire du quota du jour local autant de créneaux
  #   qu'il en contient ce jour-là (comparaison par jour local, pas par heure exacte)
  # schemas.py — champs et ordre exacts de l'exemple de la spec
  class PublishAt(BaseModel): brand_local: str; paris: str
  class UploadFiles(BaseModel): video: str; cover: str; cover_frame_s: float
  class UploadHook(BaseModel): applied: str | None; alternatives: list[str]
  class UploadFile(BaseModel):
      post_id: str; platform: Platform; account: str; publish_at: PublishAt; order_note: str | None
      files: UploadFiles; caption: str; hashtags: list[str]; hook: UploadHook; settings: dict[str, Any]
      mentions: list[str]; campaign: dict[str, Any] | None; checklist: list[str]
  # package/upload.py
  def build_upload(config: Config, paths: Paths, session: Session, post_id: int) -> UploadFile: ...
  # sans Context ni import lourd : rushes serve (tâche 36) l'appelle aussi ; schemas.py n'importe que rushes.core
  # package/index_html.py
  def write_index(ctx: Context, *, test: bool) -> Path: ...   # outbox/INDEX.html ou outbox_test/INDEX.html
  # package/outbox.py
  class PackageStep(BaseStep): name = "package"   # posts 'rendered' avec caption ; finish() écrit les deux INDEX.html
  # db/trace.py
  @dataclass(frozen=True)
  class PostTrace:
      post_id: int; episode_id: int; provenance: str; source_offset_ms: int; start_ms: int; end_ms: int
      seed: int | None; signal_level: int | None; render_version: str; hook_type: str | None; hook_engine: str | None
      items: tuple[tuple[int, int, int], ...] = ()   # (episode_id, start_ms, end_ms) de chaque passage ; partie : un seul
  def post_trace(session: Session, paths: Paths, post_id: int) -> PostTrace: ...
  # best-of et compilation (tâches 13, 30) : start_ms/end_ms = début du premier et fin du dernier passage,
  # episode_id = épisode du premier ; R17 exige chaque item tracé
  # steps.py
  RUN_ORDER = ("ingest", "clean", "analyze", "cut", "approve", "assign", "render", "meta", "package")
  ```
- Règles fixées :
  - Dossier : `outbox/<account>/<p_key>/` (ou `outbox_test/` si `is_test`, R14, R22) : `video.mp4` (lien physique
    vers le rendu si même volume, sinon copie), `cover.jpg`, `caption.txt`, `hook.txt`, `hook_2.txt`, `hook_3.txt`
    (seulement ceux qui existent), `upload.json`. `check_delivery` refait avant d'écrire ; violation → `failed`.
    Post → `ready`.
  - `planned_at` : créneau conseillé (UTC) donné par `plan_slots` (créneaux `config.slots_for(compte)`), dans
    l'ordre des parties ; `publish_at` affiché
    en heure de la marque (`"2026-11-16 15:00 America/New_York"`) et de Paris (`"2026-11-16 21:00"`).
  - `order_note` pour la partie k > 1 : `"Publier après la partie {k-1} ({p_key précédent})"`.
  - `settings` = `upload_defaults[platform]` + `branded_content: false` (TikTok) ; `ai_generated_label` vrai si
    `renders/<p_key>.voice.json` existe et que `posts.render_version` est la version courante (voix écrite par la
    tâche 35 ; aucune colonne : `build_upload` le lit sans `Context`).
  - `checklist` : TikTok `["Couverture : image à {cover_frame_s} s ou cover.jpg", "Vérifier que la légende tient avant « plus »"]`,
    `cover_frame_s` écrit avec une virgule décimale (`f"{v:.1f}".replace(".", ",")` → « 3,2 ») ;
    Instagram `["Couverture : cover.jpg", "Partager dans le fil activé"]` ; post de test : premier élément
    `"NE PAS PUBLIER : série sans licence (vitrine)"` (ou « créateur non autorisé »).
  - `INDEX.html` : une section par compte, posts dans l'ordre de `planned_at`, liens relatifs vers la vidéo et la
    couverture, légende et hashtags en `<pre>`, aucun JavaScript requis.
  - `rushes run` : `run_pipeline(ctx, [STEPS[n]() for n in RUN_ORDER], filters, command="run")` ; `Summary`
    donne les posts prêts par compte et le chemin de `INDEX.html`.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_plan_slots_one_per_day_tiktok():                 # ppd 1, slots ["15:00","18:00"] → un créneau par jour local, heure slots[d % 2]
def test_plan_slots_instagram_0_6_per_day():              # 10 jours → 6 créneaux
def test_plan_slots_stable_across_runs_24h_apart():
    # run 1 : start 2026-11-16T12:00Z, n=5 ; run 2 : start + 24 h, taken = créneaux du run 1 encore à venir
    # → les jours communs gardent la même heure ; aucun jour local n'a plus de ceil(ppd) créneaux au total
def test_upload_json_matches_spec_example(ctx):           # partie 2 : order_note, publish_at, settings, checklist exacts (« 3,2 s »)
def test_ai_label_from_current_voice_file(ctx):           # voice.json présent + version courante → ai_generated_label True ; absent → False
def test_r14_unlicensed_series_goes_to_outbox_test(ctx):
def test_r22_unauthorized_creator_goes_to_outbox_test(ctx):
def test_r3_unapproved_never_packaged(ctx):
def test_package_refuses_non_conforming_video(ctx):
def test_index_html_lists_posts_by_account_in_order(ctx):
def test_r17_post_traceability_complete(ctx):             # partie : tous les champs sauf hook_* et signal_level non nuls (pas d'accroche avant la tâche 14, niveau de signal propre au best-of) ; best-of : items tous tracés
@pytest.mark.slow
def test_run_e2e_synthetic_episode(cli_env, synth):
    # épisode synthétique de 3 min déposé dans data/import/tom_and_jerry/, preset ultrafast
    # → code 0 ; outbox_test/<compte>/<p_key>/ complet pour chaque partie ; check_delivery == [] ; INDEX.html présent
@pytest.mark.slow
def test_r16_run_twice_changes_nothing(cli_env, synth):
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer**
  `uv run pytest -m "not slow"` puis `uv run pytest -m slow tests/test_run_e2e.py` → PASS ; retirer R17, R22
  de `rules_pending.toml` (R14 reste jusqu'à la tâche 40a). Ruff, mypy verts.

- [ ] **Step 5 : Commit proposé** — `feat(package): outbox with upload.json, static INDEX.html, simple slots and rushes run (R14, R17, R22)`

---

### Task 9 : Image définitive, téléchargement des modèles, mesures sur le serveur

**Files :**
- Modify : `docker/Dockerfile` (de la tâche 42 : cible `runtime` avec `uv sync --frozen --no-dev`, utilisateur non
  root `rushes` (uid 1000), `ENTRYPOINT ["/app/docker/entrypoint.sh"]`, `ARG RUSHES_COMMIT` → `ENV RUSHES_COMMIT` ;
  cible `test` = `runtime` + `uv sync --frozen` (dépendances de dev) + `tests/` : `pytest` ne tourne que dans
  cette image, jamais dans l'image de production)
- Create : `docker/entrypoint.sh`, `docker/compose.yaml` (service `rushes` seulement), `src/rushes/models/manifest.toml`,
  `src/rushes/models/fetch.py`, `src/rushes/net/http.py`, `scripts/measure_pipeline.py`
- Modify : `src/rushes/cli.py` (`models fetch`, `models check`), `src/rushes/context.py` (vrai `Http`), `docs/decisions.md`
- Test : `tests/test_manifest.py`, `tests/test_fetch.py`, `tests/test_compose.py`, `tests/test_cli.py`
  (`models fetch`, `models check`)

**Interfaces :**
- Consomme : `Http` (§5.8, avec `download`), `Paths.cache_models`, sha256 relevés à la tâche 42.
- Produit :
  ```python
  # models/fetch.py
  @dataclass(frozen=True)
  class ModelFile: name: str; file: str; url: str; sha256: str; bytes: int; license: str
  def load_manifest() -> list[ModelFile]: ...                 # manifest.toml empaqueté
  def fetch(http: Http, paths: Paths, *, only: Sequence[str] = ()) -> list[str]: ...    # noms téléchargés
  def check(paths: Paths) -> dict[str, Literal["ok", "missing", "bad_sha256"]]: ...      # sans réseau
  # net/http.py
  class UrllibHttp:   # implémente Http ; User-Agent "rushes/<version>" ; délai 30 s ; download en flux vers <dest>.part puis renommage
  ```
- Règles fixées :
  - `manifest.toml` : une table par fichier (`whisper_small`, `whisper_base`, `clip`, `ced`, `yunet`, `qwen4b`,
    `qwen1_7b`, `supertonic`, `kokoro`) ; un modèle à plusieurs fichiers a une table par fichier, `file` contenant
    le sous-dossier (Whisper : `whisper_small/model.bin`, `config.json`, `tokenizer.json`, `vocabulary.txt` ; CED et
    les voix : modèle ONNX, étiquettes ou jetons, fichiers annexes), URL https figée sur une révision (`/resolve/<commit>/…` pour Hugging Face,
    version de release pour GitHub), sha256, taille, licence. Silero vient du paquet `silero-vad`.
  - `fetch` : fichier présent et sha256 correct → sauté ; sha256 faux après téléchargement → fichier supprimé et
    `RushesError("empreinte incorrecte pour <nom>")`.
  - `compose.yaml` : `mem_limit: 6g`, `cpus: 4`, `cpu_shares: 512`, `environment: OMP_NUM_THREADS=4`,
    volumes `${RUSHES_DATA_DIR}:/data` et `../config:/config:ro`, `RUSHES_DATA_DIR=/data`, `RUSHES_CONFIG_DIR=/config`,
    aucun `ports:` ; service `rushes-test` (mêmes réglages, `build.target: test`, `profiles: ["test"]`).
    `entrypoint.sh` : `exec nice -n 10 "$@"`.
  - `scripts/measure_pipeline.py` (dans l'image) : lance `rushes --json ingest` sur les fichiers déposés, puis un
    `rushes --json run --episode <e_key>` par épisode nouvellement ingéré (`Filters.episodes` n'arrive qu'en tâche
    22). Premier passage, jamais `--force` (`--force` ne retouche pas un élément qui a un descendant attribué, rendu
    ou publié, §5.3). Lit la table `runs` et le journal, écrit `reports/measures-<date>.json` : par étape, secondes
    par minute de vidéo, et pic de mémoire de chaque run (`ru_maxrss` des enfants), plus le temps de rendu d'une
    partie au preset `medium`.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_manifest_entries_complete():                     # url https, sha256 de 64 hex, bytes > 0, licence non vide, noms uniques
def test_fetch_verifies_sha256(tmp_path):                 # FakeHttp renvoie des octets dont le sha256 diffère → erreur, fichier absent
def test_fetch_skips_present_valid_file(tmp_path):
def test_models_check_reports_missing_without_network(tmp_path):
def test_compose_limits_and_no_published_port():          # lit docker/compose.yaml
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS. Suite rapide, ruff, mypy verts.

- [ ] **Step 5 : Mesurer sur le serveur**

```bash
docker compose -f docker/compose.yaml build --build-arg RUSHES_COMMIT=$(git rev-parse --short HEAD)
docker compose -f docker/compose.yaml run --rm -T rushes rushes models fetch
docker compose -f docker/compose.yaml --profile test run --rm -T rushes-test pytest -m slow
docker compose -f docker/compose.yaml run --rm -T rushes python -I scripts/measure_pipeline.py
```

Expected : `models check` tout `ok` ; suite `slow` verte ; rapport écrit pour trois épisodes. Ce sont la vidéo
synthétique (générée sur le Mac par `tests/fixtures/generator.py`, copiée dans `data/import/tom_and_jerry/`) et
deux vrais épisodes déposés par Louis dans `data/import/tom_and_jerry/` et `data/import/oggy/` (vitrine,
`licensed: false`), tous déposés avant le script.

- [ ] **Step 6 : Consigner** — entrée « Mesures de la phase 1 » dans `docs/decisions.md` (tableau du rapport,
  comparaison avec les estimations de la spec, Charge du serveur) ; si un poste déborde, appliquer le repli de la spec
  et le noter.

- [ ] **Step 7 : Commit proposé** — `build(docker): production image, pinned model manifest with sha256 and phase 1 measurements`

---

## Porte de sortie de la phase 1

- [ ] `rushes run` produit un `outbox_test/` complet (vidéo conforme R7, couverture, légende, `upload.json`,
  `INDEX.html`) sur la vidéo synthétique **et** sur 2 vrais épisodes, sur le serveur, dans l'image.
- [ ] Relancer `rushes run` ne change rien (R16).
- [ ] Rapport de mesures consigné dans `docs/decisions.md`.
- [ ] `tests/rules_pending.toml` ne contient plus R12, R19, R20, R22, R27, R39, R40, R41 (règles closes pendant la
  phase 1). Les autres restent jusqu'à leur dernière tâche, selon la matrice du plan maître (par exemple R1 et R14
  (40a), R2 (17), R3 (32), R5 (13), R7 (35), R13 (40a), R17 (24)) : C48 et C49.
- [ ] Suite rapide verte sur le Mac ; suite `slow` verte dans l'image sur le serveur ; ruff et mypy propres.
