# Phase 6 — Mise en production : plan d'implémentation

> **Pour les agents qui exécutent le plan :** sous-skill requis : `superpowers:subagent-driven-development`
> (recommandé) ou `superpowers:executing-plans`, tâche par tâche. Lire d'abord `docs/plans/00-master.md` (contrats
> transverses, sections citées « §5.x », §9 et §10) et les blocs **Interfaces** des phases 1 à 5 (noms réutilisés
> ici). Les étapes utilisent des cases `- [ ]`. Les étapes marquées **(serveur)** se font sur l'EliteDesk, celles
> marquées **(Louis)** demandent une action de Louis.

**But :** installer rushes sur le serveur de façon reproductible, avec retour arrière automatique (39) ; ajouter les
contrôles qui prouvent le bon fonctionnement sans rien publier : audit des règles, bascule des licences, mémoire de
pointe (40a) ; tenir 7 jours de répétition générale (40b) ; lancer, puis vérifier sur les vrais posts les critères du
rodage (41).

**Architecture :** un script hôte `deploy.sh` construit, teste et promeut l'image, sauvegarde la base, migre et
installe les unités ; il revient en arrière seul en cas d'échec. `rushes doctor` vérifie l'installation depuis le
conteneur. Un audit nocturne relit la base et `outbox/` contre les règles. La bascule des licences remet en file les
posts dont le statut de test a changé. Un bilan de répétition et un bilan de rodage sont calculés depuis `runs`,
`metrics`, `post_outcomes` et `derived_values`.

**Pile :** bash (`set -euo pipefail`, shellcheck), Docker Compose, systemd, GoDoxy (veille du conteneur, OIDC
PocketID), ntfy v2.36.0, cgroup v2 (`memory.stat`, `memory.events`).

**Spec :** `docs/spec.md` (Automatisation et exploitation, Publication manuelle, Plan de développement : phase 6,
tâches 39 à 41, Points à vérifier), `docs/decisions.md`, `docs/plans/00-master.md` §9 et §10.

## Global Constraints

Toutes celles de `00-master.md` §2 et des phases 1 à 5 s'appliquent. En plus, pour cette phase :

- **Migration** : 0017 (tâche 40a) seulement.
- **Le serveur ne modifie jamais le dépôt** : il fait seulement `git pull` (Louis) puis `scripts/deploy.sh`. Un
  `uv.lock` proposé par la maintenance est appliqué sur le Mac, commité par Louis, puis déployé.
- **Scripts hôte** : bash `set -euo pipefail`, compatibles bash 3.2, propres sous `shellcheck` ; testés par
  `fake_bin` (tâche 20), jamais contre le vrai Docker en test. Toute commande `rushes` lancée à la main sur le
  serveur passe par `scripts/rushes` (tâche 39), qui charge `/etc/rushes/rushes.env` ; le runbook et les exercices
  n'écrivent jamais `docker compose … run` à la main.
- **CLI** : chaque commande nouvelle de la phase est ajoutée à `IMPLEMENTED` dans `tests/test_cli.py` (Files de sa
  tâche).
- **Secrets** : fichiers 0400 appartenant à l'uid 1000 dans `/etc/rushes/secrets/`, lus par `<VARIABLE>_FILE`
  (P23) ; aucun secret dans `rushes.env`, `compose.yaml`, le dépôt, la sortie ni le journal (R47).
- **Réseau** : aucun `ports:` dans `compose.yaml` ; `serve` et `ntfy` ne sont joignables que par GoDoxy.
- **Rien n'est publié par rushes** (R48), pendant la répétition comme après le lancement.
- **Étiquettes GoDoxy** : noms vérifiés dans la documentation de la version de GoDoxy installée chez Louis avant de
  les écrire. Base de départ (docs.godoxy.dev, « Idle Sleep » et « Docker labels and Route Files ») :
  `proxy.idle_timeout`, `proxy.wake_timeout`, `proxy.stop_method`, `proxy.middlewares.oidc` (avec
  `allowed_users`/`allowed_groups` facultatifs).
- Doublures réutilisées : `fake_bin` (journal `calls.log`), `FakeNotifier`, `FakeHttp`, horloge figée.

## Review Focus

1. **Déploiement qui échoue à mi-chemin** (tests de l'image candidate en échec, migration en erreur, `doctor` rouge
   après la promotion) : ancienne image remise en `rushes:current`, base restaurée depuis la sauvegarde d'avant
   déploiement, minuteurs relancés, alerte `deploy_failed`, code 1. Test :
   `test_deploy_failure_after_promote_rolls_back` (tâche 39).
2. **Licence retirée alors que des posts sont prêts dans `outbox/`** (Louis les a peut-être déjà programmés) :
   rien n'est déplacé (décision de Louis, L9) : alerte `licence_revoked:<id>` qui liste les posts prêts et leurs
   créneaux, répétée chaque jour (dédoublonnage 24 h) tant qu'un de ces posts reste `ready` ; Louis les écarte ou
   les publie sous sa responsabilité. Les posts pas encore empaquetés partent en `outbox_test/`. Test :
   `test_r14_licence_revoked_alerts_and_keeps_ready_post` (tâche 40a).
3. **Commande tuée en plein travail** (OOM, `docker kill`, redémarrage du serveur) : la ligne `runs` restée
   `running` est close en `failed` (« interrompu ») au run suivant et signalée ; rien n'est produit deux fois
   (R16). Test : `test_stale_running_run_closed_as_interrupted` (tâche 40a).
4. **Restauration mal engagée** (archive corrompue, révision inconnue, minuteur en cours, `serve` qui tient la base
   ouverte avec un `-wal`) : refusée avec un message français, base en place intacte ; jamais une base restaurée à
   côté du `-wal` de l'ancienne. Tests : `test_restore_refuses_corrupt_archive_and_busy_lock`,
   `test_restore_with_wal_files_is_clean` (tâche 39).
5. **Lancement ou rodage avec des données maigres** (aucune licence obtenue, compte sans publication, export
   Instagram jamais déposé) : `launch check` échoue avec la piste de solution ; le bilan de rodage affiche « en
   attente » sans erreur. Tests : `test_launch_check_without_publishable_stock`,
   `test_burnin_sparse_accounts_pending` (tâche 41).

---

### Task 39 : Déploiement reproductible (Compose final, GoDoxy, ntfy, secrets, sauvegarde hors RAID, `doctor`, procédure)

**Files :**
- Create : `src/rushes/ops/doctor.py`, `scripts/deploy.sh`, `scripts/lib_image.sh`, `scripts/rushes` (commande
  hôte), `docs/ops/runbook.md`, `tests/test_doctor.py`, `tests/test_deploy.py`, `tests/test_restore.py`,
  `tests/test_runbook.py`, `tests/test_host_wrapper.py`
- Modify : `src/rushes/ops/backup.py` (`restore`, sauvegardes étiquetées), `src/rushes/ops/alerts.py`
  (`deploy_failed`), `src/rushes/cli.py` (`doctor`, `backup --label`, `backup restore`, `db head`), `docker/compose.yaml`
  (étiquettes GoDoxy et `healthcheck` de `serve`, montage hors RAID, `NTFY_BASE_URL`, volumes de `ntfy`),
  `docker/ntfy/server.yml` (sans `base-url`), `docker/rushes.env.example`, `docker/systemd/*.service`
  (`-e RUSHES_TRIGGER=timer`, `path` pour `rushes-refresh.service` et `rushes-update.service`),
  `scripts/update_ytdlp.sh` (fonctions de `lib_image.sh`), `scripts/install_units.sh` (`--uninstall`),
  `config/settings.yaml` (`backup.offsite_dir: /offsite`, `serve.public_origin` en commentaire d'exemple),
  `tests/test_compose.py`, `tests/test_update_ytdlp.py`, `tests/test_systemd_units.py` (tâche 20), `tests/test_cli.py` (`doctor`, `backup restore`, `db head` dans
  `IMPLEMENTED`)

**Interfaces :**
- Consomme : `backup`, `verify_latest`, `rotate`, `BackupSettings` (20), `models.fetch.check` (9),
  `migrate.current_revision` (1), `migrate.head_revision` (36), `NtfySettings.topic()`/`token()`, `register_secret`,
  `heavy_lock` (§5.11), `disk_usage_ratio`, `RetentionSettings.disk_alert` (20), `fake_bin` (20).
- Produit :
  ```python
  # ops/doctor.py
  @dataclass(frozen=True)
  class Check: name: str; ok: bool; detail: str; hint: str | None = None   # detail jamais porteur d'un secret
  def run_checks(ctx: Context, *, mounts_file: Path = Path("/proc/mounts")) -> list[Check]: ...
  # ops/backup.py
  def backup(paths: Paths, engine: Engine, *, now: datetime, s: BackupSettings, label: str | None = None) -> BackupResult: ...
  def restore(paths: Paths, archive: Path, *, now: datetime, to: Path | None = None) -> BackupResult: ...
  ```
  CLI : `rushes doctor` (code 0 si tout est vert, sinon 1) ; `rushes backup [--label L]` ;
  `rushes backup restore <archive> [--to CHEMIN]` ; `rushes db head` (affiche `head_revision()` de l'image, sans
  base).
  Scripts : `scripts/deploy.sh <dépôt> [--rollback] [--allow-downgrade]` ; `scripts/lib_image.sh`
  (`build_candidate`, `test_candidate`, `promote`, `rollback_image`, `package_versions <image>` : versions de
  `yt-dlp`, `yt-dlp-ejs`, `curl-cffi` lues par `importlib.metadata` dans l'image), sourcé par `deploy.sh` et
  `update_ytdlp.sh` ; `scripts/rushes <args>` : `set -a; . /etc/rushes/rushes.env; set +a; exec docker compose
  --env-file /etc/rushes/rushes.env -f "$RUSHES_HOME/docker/compose.yaml" run --rm -T rushes rushes "$@"`.
- Règles fixées :
  - `run_checks`, dans cet ordre, chaque contrôle indépendant (un échec n'arrête pas les suivants) :

    | Nom | Vert si | Piste en cas d'échec |
    | --- | --- | --- |
    | `config` | `load_config` sans erreur, `serve.public_origin` sans `<` | `rushes config check` |
    | `database` | révision = `head` | `rushes db upgrade` |
    | `models` | `check()` tout `ok` | `rushes models fetch` |
    | `secrets` | topic et jeton ntfy lisibles (fichier 0400 ou variable) ; le contenu n'est jamais affiché | vérifier `/etc/rushes/secrets/` |
    | `data_local` | `data_dir` n'est pas sur `nfs`, `nfs4`, `cifs`, `smb3`, `fuse.sshfs` (lu dans `mounts_file`, point de montage le plus long) | le verrou `flock` exige un disque local |
    | `offsite` | `backup.offsite_dir` défini, accessible en écriture, `st_dev` différent de celui de `data_dir` | monter le disque hors RAID sur `RUSHES_OFFSITE_DIR` |
    | `disk` | `disk_usage_ratio(data_dir) <= disk_alert` | rétention, `rushes status` |
    | `lock` | `heavy_lock(wait=False)` pris puis relâché | une commande lourde tourne : attendre |
    | `ffmpeg` | version 7.1, `--enable-libass` et `--enable-libx264` dans `-buildconf` | reconstruire l'image |
    | `ytdlp` | `deno --version` répond, `yt_dlp_ejs` importable, `curl_cffi` importable | reconstruire l'image |
    | `backup` | dernière sauvegarde de moins de 26 h et `verify_latest` vert (ignoré si aucune sauvegarde encore) | `rushes backup` |

  - `backup(label=...)` : nom `rushes-<AAAAMMJJ-HHMM>-<label>.db.zst` ; `rotate` ignore les fichiers étiquetés ;
    pour chaque étiquette, seuls les 3 plus récents sont gardés.
  - `restore` : prend `heavy_lock(wait=False)` (`LockBusy`, code 3, sinon) ; décompresse dans
    `<db>.restore-tmp`, `integrity_check` = `ok`, révision connue de la chaîne Alembic, sinon `RushesError` et base
    en place intacte ; puis, sur la base en place, `PRAGMA wal_checkpoint(TRUNCATE)` : résultat `busy` ≠ 0 →
    `RushesError` « base ouverte ailleurs : arrêter serve (`docker compose stop serve`) », rien de touché ; sinon
    renomme `rushes.db`, et `rushes.db-wal` / `rushes.db-shm` s'ils existent, en `…before-restore-<ts>` (même
    suffixe), puis met la copie à sa place (renommages atomiques sur le même disque) : jamais une nouvelle base à
    côté du `-wal` de l'ancienne. `to` donné → écrit seulement à cet endroit, ne touche pas la base. Après une
    restauration, `rushes db upgrade` remet la révision à `head`. `serve` est arrêté avant toute restauration
    (étape 3 de `deploy.sh`, procédure du runbook).
  - `compose.yaml` :
    - service `serve` : étiquettes `proxy.aliases: rushes`, `proxy.rushes.port: "8080"`, `proxy.idle_timeout:
      15m`, `proxy.wake_timeout: 30s`, `proxy.stop_method: stop`, `proxy.middlewares.oidc` (utilisateur de Louis
      dans `allowed_users`, valeur prise dans `${RUSHES_OIDC_USER}`) ; noms et syntaxe confirmés à l'étape 6 ;
      `healthcheck: ["CMD", "python", "-I", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]`
      (intervalle 30 s, `start_period: 5s`) ; la route GoDoxy accepte un corps de 5 Mo au moins
      (`POST /api/inbox/instagram`, décision L28 ; réglage de taille ajouté si la version installée en a un, vérifié à
      l'étape 8) ;
    - services `rushes` et `rushes-test` : volume `${RUSHES_OFFSITE_DIR}:/offsite` en plus ;
    - service `ntfy` : `environment: NTFY_BASE_URL: https://${NTFY_HOST}` ; `server.yml` n'a plus de `base-url` ;
      volumes `${RUSHES_DATA_DIR}/ntfy/lib:/var/lib/ntfy` (`auth-file`, utilisateurs et jetons),
      `${RUSHES_DATA_DIR}/ntfy/cache:/var/cache/ntfy` et `./ntfy/server.yml:/etc/ntfy/server.yml:ro` : un
      `docker compose down` ou une recréation ne perd ni utilisateurs ni jetons ; étiquettes GoDoxy de `ntfy` sans
      OIDC (l'appli iPhone s'authentifie par jeton) ;
    - toute image hors `rushes:*` est épinglée par `@sha256:`.
  - `rushes.env.example` : `RUSHES_HOME`, `RUSHES_DATA_DIR`, `RUSHES_OFFSITE_DIR`, `GODOXY_NETWORK`, `NTFY_HOST`,
    `RUSHES_OIDC_USER`, `RUSHES_SECRETS_DIR=/etc/rushes/secrets` ; chaque `${VAR}` de `compose.yaml` et des unités y
    figure (test).
  - Unités : `docker compose … run --rm --no-deps -T -e RUSHES_TRIGGER=timer --name rushes-%N …` ;
    `rushes-refresh.service` et `rushes-update.service` (lancés par une unité `.path`) passent
    `RUSHES_TRIGGER=path` (`update_ytdlp.sh` le transmet à ses `docker compose run`) ; `manual` par défaut (origine
    enregistrée par la tâche 40a). `test_systemd_units_well_formed` vérifie la valeur de chaque unité.
  - `deploy.sh <dépôt>` (lit `/etc/rushes/rushes.env`) :

```text
0. première installation = pas d'image rushes:current OU pas de ${RUSHES_DATA_DIR}/rushes.db : notée au journal
1. commit = git -C <dépôt> rev-parse --short HEAD ; journal dans ${RUSHES_DATA_DIR}/logs/deploy-<ts>.log
2. build_candidate (cibles runtime et test, --build-arg RUSHES_COMMIT) ; test_candidate (pytest -m "not slow",
   --network none) → échec : sortie 1, rien d'autre n'a changé
2b. hors première installation : package_versions current et candidate ; yt-dlp, yt-dlp-ejs ou curl-cffi plus
   ancien dans le candidat → sortie 1 « le dépôt ramènerait un yt-dlp plus ancien : appliquer
   reports/uv-lock-<date>.diff sur le Mac », sauf --allow-downgrade ; rien d'autre n'a changé
3. systemctl stop rushes-*.timer rushes-*.path ; docker compose stop serve
4. hors première installation : docker compose run --rm -T rushes rushes backup --label pre-deploy
   (attend le verrou lourd)
5. promote (current → prev si current existe, candidate → current)
6. rushes db upgrade ; rushes models fetch ; rushes doctor
   → échec : première installation → sortie 1 sans retour arrière (rien à restaurer) ; sinon rollback_image
     (prev → current), rushes backup restore <archive pre-deploy>, rushes db upgrade, unités relancées,
     rushes notify send --key deploy_failed, sortie 1
7. install_units.sh ; docker compose up -d --no-deps serve ntfy ; systemctl start des .timer et .path
8. résumé : commit déployé, révision, chemin du journal ; sortie 0
```

    `--rollback` : étape 3, `backup --label pre-rollback`, `rollback_image` ; la base n'est restaurée (dernière
    `pre-deploy`) que si sa révision actuelle diffère de `rushes db head` de l'image `prev` (révision inconnue de
    `prev`) ; sinon elle est gardée telle quelle ; puis `db upgrade`, étape 7.
    Un `trap` sur `ERR` et `INT` relance toujours les minuteurs arrêtés et `serve`.
  - `ALERT_KINDS["deploy_failed"]` : « Déploiement en échec : version précédente remise », priorité 5.
  - `docs/ops/runbook.md` (français ; toute commande `rushes` y passe par `scripts/rushes`) : prérequis de l'hôte
    (Docker Engine et Compose v2, git, cgroup v2) ; première installation (clone, `rushes.env`, secrets, disque hors
    RAID, routes GoDoxy, client OIDC PocketID, `docker compose up -d ntfy` puis création de l'utilisateur et du
    jeton ntfy, abonnement iPhone, enfin `deploy.sh`) ; mise à jour ; retour arrière ; restauration (d'abord
    `docker compose stop serve` et arrêt des minuteurs) ; une ligne par clé d'`ALERT_KINDS` avec l'action à mener ;
    renouvellement des cookies de secours (`data/cookies/youtube.txt`, 0600, compte Google dédié) ; disque plein ;
    redémarrage du serveur ; ajout d'une série ou d'un créateur ; revue facultative (copier `data/review/<ts>/` vers
    le Mac, ouvrir la page, remettre le JSON exporté sous `data/review/` puis `scripts/rushes review apply
    /data/review/<fichier>`) ; thèmes proposés (`data/proposals/themes/*.yaml` copiés dans `config/themes/` du dépôt
    sur le Mac, commités par Louis) ; mise à jour de `uv.lock` (`reports/uv-lock-<date>.diff` appliqué sur le Mac,
    commité, puis `deploy.sh`) ; lecture de `rushes status` et des rapports. La ligne « licence obtenue ou retirée »
    (`rushes rights sync`) est ajoutée par la tâche 40a.

- [ ] **Step 1 : Tests qui échouent**

```python
# tests/test_doctor.py
def test_doctor_all_green_on_healthy_install(ctx, tmp_path):     # modèles factices ok, offsite sur un autre st_dev (monkeypatch os.stat)
def test_doctor_reports_each_failure_independently(ctx, tmp_path):
    # modèle manquant, révision en retard, secret illisible, data_dir sur nfs4 (mounts_file factice),
    # offsite sur le même st_dev, disque à 90 % → 6 Check ok=False, chacun avec sa piste ; les autres restent verts
def test_r47_doctor_never_prints_secret(ctx, capsys):             # jeton ntfy absent de stdout, du journal et des Check
# tests/test_restore.py
def test_backup_restore_roundtrip(ctx):                           # backup → posts modifiés → restore → contenu d'origine ; before-restore-<ts> présent
def test_restore_to_path_leaves_live_db_untouched(ctx, tmp_path):
def test_restore_refuses_corrupt_archive_and_busy_lock(ctx):      # archive tronquée → RushesError, base intacte ; verrou pris → code 3
def test_restore_with_wal_files_is_clean(ctx):
    # base en place avec -wal et -shm : après restore, contenu = archive, -wal/-shm renommés before-restore-<ts> ;
    # connexion ouverte en écriture (checkpoint busy) → RushesError, rien de touché
def test_labeled_backups_outside_rotation_keep_3(ctx):
# tests/test_deploy.py (fake_bin : git, docker, systemctl)
def test_deploy_happy_path_order(tmp_path):
    # calls.log : build, pytest réseau coupé, versions des paquets, stop des timers, stop serve,
    # backup --label pre-deploy, tag current→prev, tag candidate→current, db upgrade, models fetch, doctor,
    # install_units, up -d serve ntfy, start des timers
def test_deploy_candidate_tests_fail_changes_nothing(tmp_path):   # aucun tag, aucun stop de timer, code 1
def test_deploy_failure_after_promote_rolls_back(tmp_path):       # doctor sort 1 → tag prev→current, restore pre-deploy, notify deploy_failed, timers relancés, code 1
def test_deploy_first_install_skips_backup_and_prev(tmp_path):    # ni rushes:current ni base → aucun backup, aucun tag prev, code 0
def test_deploy_refuses_ytdlp_downgrade(tmp_path):                # yt-dlp 2026.10 → 2026.08 : code 1, piste uv-lock ; --allow-downgrade → déployé
def test_deploy_rollback_keeps_db_when_revision_unchanged(tmp_path):
    # db head de prev == révision actuelle → backup --label pre-rollback, tag prev→current, aucun restore
def test_deploy_rollback_restores_db_when_prev_lacks_revision(tmp_path):   # révision inconnue de prev → restore pre-deploy
def test_deploy_restarts_timers_on_interrupt(tmp_path):           # docker « build » reçoit INT pendant l'étape 4 → systemctl start présent
# tests/test_compose.py (ajouts)
def test_compose_serve_godoxy_labels_and_healthcheck():
def test_compose_offsite_mount_and_settings():                     # ${RUSHES_OFFSITE_DIR}:/offsite ; config d'exemple backup.offsite_dir == "/offsite"
def test_compose_third_party_images_pinned_by_digest():
def test_env_example_lists_every_variable():                       # ${VAR} de compose.yaml et docker/systemd/* ⊆ rushes.env.example
def test_ntfy_base_url_from_environment():
def test_compose_ntfy_state_persisted():                           # /var/lib/ntfy et /var/cache/ntfy montés sous ${RUSHES_DATA_DIR}, server.yml en :ro
# tests/test_systemd_units.py (ajout à test_systemd_units_well_formed)
#   RUSHES_TRIGGER=path pour rushes-refresh.service et rushes-update.service, timer pour les autres
# tests/test_host_wrapper.py (fake_bin)
def test_host_wrapper_loads_env_and_runs_compose(tmp_path):       # rushes.env factice → docker compose --env-file … run --rm -T rushes rushes status
# tests/test_runbook.py
def test_runbook_covers_every_alert_kind():                        # chaque clé (ou préfixe) d'ALERT_KINDS apparaît dans docs/ops/runbook.md
def test_runbook_covers_regular_gestures():                        # sections revue, thèmes, uv.lock ; aucun « docker compose -f … run » hors scripts/rushes
# tests/test_update_ytdlp.py : les trois tests de la tâche 20 restent verts avec lib_image.sh
```

- [ ] **Step 2 : Lancer** `uv run pytest tests/test_doctor.py tests/test_restore.py tests/test_deploy.py tests/test_compose.py tests/test_systemd_units.py tests/test_host_wrapper.py tests/test_runbook.py -v` → FAIL.

- [ ] **Step 3 : Implémenter** (`doctor`, `restore`, sauvegardes étiquetées, CLI, `lib_image.sh`, `deploy.sh`,
  `scripts/rushes`, Compose, `server.yml`, `rushes.env.example`, unités, `install_units.sh --uninstall`, runbook).

- [ ] **Step 4 : Lancer** → PASS. Suite rapide, ruff, mypy verts ; `shellcheck scripts/*.sh` propre.

- [ ] **Step 5 : Désinstaller l'installation provisoire (serveur)** — `scripts/install_units.sh --uninstall` ;
  `docker compose --env-file /etc/rushes/rushes.env -f docker/compose.yaml down` ; `systemctl list-timers 'rushes-*'` → aucune ligne. La base et `data/` restent.

- [ ] **Step 6 : Vérifier GoDoxy (Louis et serveur)** — lire la documentation de la version installée (veille,
  réseau, OIDC) ; corriger les étiquettes et `test_compose_serve_godoxy_labels_and_healthcheck` si les noms diffèrent,
  noter la version et les noms dans `docs/decisions.md`. Louis crée le client OIDC dans PocketID (URL de redirection
  donnée par la doc GoDoxy) et renseigne GoDoxy.

- [ ] **Step 7 : Réinstaller en suivant le runbook seul (serveur)** — d'abord une vraie première installation :
  `docker image rm rushes:current rushes:prev`, clone propre dans `RUSHES_HOME`, `rushes.env` dont
  `RUSHES_DATA_DIR` pointe vers un dossier vide, secrets, disque hors RAID monté (Louis), ntfy démarré et jeton
  créé, `scripts/deploy.sh "$RUSHES_HOME"` → code 0 et `scripts/rushes doctor` vert ; puis `RUSHES_DATA_DIR`
  remis sur le vrai dossier de données et `scripts/deploy.sh "$RUSHES_HOME"` (mise à jour normale) → code 0. Tout
  écart entre le runbook et ce qu'il a fallu faire est corrigé dans le runbook, jamais contourné à la main.

- [ ] **Step 8 : Contrôles d'installation (serveur)**

```bash
scripts/rushes doctor                                                   # tout vert
docker inspect --format '{{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}' $(docker compose --env-file /etc/rushes/rushes.env -f docker/compose.yaml ps -q serve)   # 536870912 1000000000
docker compose --env-file /etc/rushes/rushes.env -f docker/compose.yaml run -d --name rushes-limits rushes sleep 30 && docker inspect --format '{{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}' rushes-limits && docker rm -f rushes-limits   # 6442450944 4000000000
curl -s -o /dev/null -w '%{http_code}' https://rushes.<domaine>/        # depuis le Mac, sans session : redirection vers PocketID (302 ou 303)
curl -s --max-time 3 http://<ip du serveur>:8080/healthz                # depuis le réseau local : connexion refusée
curl -s -o /dev/null -w '%{http_code}' -d test https://ntfy.<domaine>/<topic>   # sans jeton : 403
docker compose --env-file /etc/rushes/rushes.env -f docker/compose.yaml --profile test run --rm -T rushes-test pytest -m slow   # vert (§10)
scripts/rushes run --profile night & sleep 60; scripts/rushes run --no-wait; echo $?   # 3 : la seconde ne passe pas (§10) ; la nuit finit seule
```

  Puis : après `idle_timeout`, `docker compose ps serve` → arrêté ; une requête authentifiée le réveille et la page
  s'affiche en moins de `wake_timeout`. `scripts/rushes notify test` → une alerte par clé reçue sur l'iPhone. Depuis
  la page (Mac, session PocketID), déposer un CSV factice de 4,9 Mo → « Déposé » (aucun 413 de GoDoxy, L28), puis
  retirer ce fichier de `data/inbox/` avant le run `stats` suivant. Après `docker compose down` puis `up -d ntfy`,
  `scripts/rushes notify test` arrive toujours (jetons conservés).

- [ ] **Step 9 : Exercices de restauration et de retour arrière (serveur)** —
  `scripts/rushes backup restore <dernière archive> --to /data/tmp/restore-check.db` → révision et nombre de posts affichés ;
  copie hors RAID présente dans `RUSHES_OFFSITE_DIR` ; `scripts/deploy.sh "$RUSHES_HOME" --rollback` puis
  `scripts/deploy.sh "$RUSHES_HOME"` → codes 0, base gardée (même révision), `scripts/rushes status` identique avant
  et après. Résultats notés dans
  `docs/decisions.md` (« Déploiement »).

- [ ] **Step 10 : Commits proposés** —
  `feat(ops): add doctor checks, labeled backups and restore`
  `build(deploy): reproducible deploy script with automatic rollback, GoDoxy labels and offsite backups`
  `docs(ops): add operations runbook`

---

### Task 40a : Audit des règles, bascule des licences, mémoire de pointe, runs interrompus

**Files :**
- Create : `src/rushes/ops/audit.py`, `src/rushes/ops/memory.py`, `src/rushes/rights/sync.py`,
  `src/rushes/db/migrations/versions/0017_runs_ops.py`, `tests/test_audit.py`, `tests/test_licence_sync.py`,
  `tests/test_runs_ops.py`
- Modify : `src/rushes/db/registry.py` (`pair_verdict` extrait de `can_assign`), `src/rushes/db/models.py`
  (`Run.peak_mem_bytes`, `Run.trigger`), `src/rushes/runner.py` (origine, mémoire de pointe, runs interrompus),
  `src/rushes/steps.py` (`licence_sync` en tête de `RUN_ORDER` et du profil `watch` ; `audit` dans le profil `night`
  avant `disk` ; ordre final : `00-master.md` §3), `src/rushes/selectors.py` (`audit` et `licence_sync` dans
  `GLOBAL_STEPS`), `src/rushes/ops/alerts.py`, `src/rushes/ops/report.py` (section « Audit »),
  `src/rushes/status.py` (mémoire de pointe par minuteur), `src/rushes/cli.py` (`audit`, `rights sync`),
  `docs/ops/runbook.md` (ligne `licence_revoked`, procédure « licence obtenue ou retirée » avec
  `scripts/rushes rights sync`, ligne `rule_violation` complétée par l'audit), `tests/conftest.py` (fixture
  `synth_ready`), `tests/test_cli.py` (`audit`, `rights sync` dans `IMPLEMENTED`), `tests/rules_pending.toml`
  (vidé)

**Interfaces :**
- Consomme : `can_assign` et ses tolérances (3), `is_test_post` (8a), `PostTrace` (8b), `credit_problems` (26),
  `caption_problems` et `required_hashtags` (8a, 33), `media.probe` (4), bornes de `config.families` (33, R6),
  `head_revision` (36), `QueueSettings.expiry_days` (16b), `queue` (16b, replacement des posts non empaquetés),
  `write_index` (8b), `heavy_lock`, `runs`, `ALERT_KINDS["rule_violation"]` (20).
- Produit :
  ```python
  # db/registry.py
  @dataclass(frozen=True)
  class Placed: segment_id: int; account_id: str; episode_id: int; span: Span; kind: str; phashes: Sequence[str]
  def pair_verdict(
      a: Placed, b: Placed, *, tolerance_ms: int, phash_max: int, phash_min_share: float
  ) -> Verdict: ...   # utilisé par can_assign et par l'audit ; a = le candidat (le passage attribué le plus tard), b = le passage déjà attribué
  # core/phash.py (déjà là depuis le correctif C53, que pair_verdict réutilise tel quel)
  def usable_phashes(hashes: Sequence[str]) -> list[int]: ...        # retire les empreintes dégénérées
  def phash_conflict(mine, theirs, *, max_distance, min_share) -> PhashConflict | None: ...   # les deux sens
  # ops/audit.py
  @dataclass(frozen=True)
  class Violation: rule: str; subject: str; message: str        # rule = "R1", subject = "post:p_000123"
  def audit(session: Session, ctx: Context, *, now: datetime, probe_files: bool = True) -> list[Violation]: ...
  class AuditStep(BaseStep): name = "audit"
  # rights/sync.py
  @dataclass(frozen=True)
  class Flip: post_id: int; account_id: str; to_test: bool; was_ready: bool
  def licence_flips(session: Session, config: Config) -> list[Flip]: ...
  #   posts planned, rendered ou ready dont is_test diffère de is_test_post (jamais discarded, expired, published)
  class LicenceSyncStep(BaseStep): name = "licence_sync"
  # ops/memory.py
  def anon_bytes(*, cgroup_root: Path = Path("/sys/fs/cgroup")) -> int | None: ...   # ligne anon de memory.stat
  def oom_kills(*, cgroup_root: Path = Path("/sys/fs/cgroup")) -> int | None: ...    # ligne oom_kill de memory.events
  # runner.py
  def run_lock_path(paths: Paths, run_id: int) -> Path: ...   # data/locks/run-<id>.lock
  ```
  ```python
  # tests/conftest.py
  @pytest.fixture
  def synth_ready(ctx, synth) -> list[int]: ...   # épisode synthétique d'une série licenciée mené jusqu'à ready
  #   (cut, approve, assign, queue, render, meta, package) : posts dans outbox/, renvoie leurs id
  ```
- Règles fixées :
  - Audit, contrôles (chacun donne zéro ou plusieurs `Violation`) :

    | Règle | Contrôle |
    | --- | --- |
    | R1, R2, R27 | `pair_verdict` sur chaque paire d'attributions vivantes (posts non `discarded`/`expired`) d'un même épisode, et sur les paires à pHash proche. Le pHash suit C53 : empreintes dégénérées (moins de 5 bits à 1 ou à 0) retirées, puis part de plans proches `registry.phash_min_share` (0,25) dans les deux sens (celui de la partie, et celui du passage attribué de 3 plans utilisables ou plus) via `core.phash.phash_conflict`, jamais « un plan proche suffit ». Une paire de passages qui ne partage qu'un fondu au noir n'est donc pas une violation. **Ordre de la paire** : le verdict pHash dépend de l'ordre quand un passage a moins de 3 plans utilisables (le minimum de 3 ne vaut que pour le passage déjà attribué). L'audit évalue donc chaque paire d'attributions vivantes avec comme `a` (candidat) le passage attribué LE PLUS TARD (`assignments.assigned_at`, puis `segment_id` le plus grand à égalité) et comme `b` l'autre, exactement l'ordre dans lequel `can_assign` l'a jugée ; jamais les deux ordres |
    | R44 | compte de l'attribution = propriétaire de l'épisode |
    | R3, R33 | tout post `rendered`/`ready`/`published` repose sur un passage `approved` |
    | R6, R7 | chaque `video.mp4` prêt : durée dans les bornes de sa famille, 1080×1920, H.264 High, yuv420p, 30 i/s, AAC 48 kHz (`media.probe` ; sauté si `probe_files=False`) |
    | R12, R13 | longueur de légende par plateforme ; 5 hashtags au plus, obligatoires présents, `#viral`/`#foryou` absents, `#fyp` absent sur Instagram |
    | R14, R22, R32 | un dossier `outbox/<compte>/<p_key>/` est en faute seulement si son post a `posts.is_test = 1` (statut au moment de l'empaquetage) ou n'existe pas en base. Sont admis : `ready`, `published` (dossier gardé par la rétention, tâche 20), `rendered` en attente d'un nouveau paquet (accroche 2/3, renumérotation, bascule). `outbox/_ecartes/` et `INDEX.html` sont ignorés. Un post prêt dont la licence a été retirée depuis relève de `licence_revoked`, pas d'une violation (L9) |
    | R17 | trace complète (`PostTrace`) pour chaque post `ready` |
    | R23 | `credit_problems` vide pour chaque post de créateur `ready` |
    | R35 | aucun post `ready` dont `ready_at < now − expiry_days − 6 h` (marge = période du profil `watch`, qui relance `queue` et l'expiration ; la nuit, `queue` tourne avant `package` et `audit`) |

    `AuditStep` : un élément par violation (`failed`, code 4), message `"<règle> <sujet> : <message>"`,
    `alert="rule_violation"` sur le premier ; la section « Audit » du rapport quotidien liste tout.
  - `ALERT_KINDS` : `rule_violation` existe déjà (tâche 20, priorité 5) ; ajout de `licence_revoked:<id>` (« Licence
    retirée : posts prêts à écarter ou déprogrammer », 4). Le runbook reçoit la ligne de chacune.
  - Bascule (`licence_sync`, léger) : `licence_flips` ne prend que les posts `planned`, `rendered` et `ready` (un post
    `discarded` ou `expired` n'est jamais ressuscité, R37). Un `Flip` `to_test` et `was_ready` ne touche pas au post
    (L9) : `Message("warn", …, alert="licence_revoked:<sujet>")` qui liste les `p_key` et créneaux, à chaque run tant
    qu'il reste `ready` (l'alerte est dédoublonnée sur 24 h). Post `planned` (pas encore rendu) : seul `is_test` est
    recalculé. Pour chaque autre `Flip` (`rendered`, ou `ready` qui devient publiable), post remis à `rendered`,
    `caption = NULL` (`meta` refait la légende : préfixe « NE PAS PUBLIER — » posé ou retiré), `planned_at` NULL,
    dossier `outbox*/<compte>/<p_key>/` supprimé, `is_test` recalculé ; `queue` lui redonne un créneau et `package` le
    réempaquette au bon endroit ; `INDEX.html` des deux dossiers réécrit. Un post `published` ne bouge jamais (ligne
    dans le rapport si son sujet n'est plus autorisé). `rushes rights sync` lance `licence_sync`, `queue`, `meta`,
    `package` (sans verrou lourd).
  - Migration `0017_runs_ops.py` : `runs` + `peak_mem_bytes INTEGER` (pic de mémoire anonyme), + `oom_kills
    INTEGER`, + `trigger TEXT NOT NULL DEFAULT 'manual'` (`CHECK trigger IN ('timer','path','manual')`), lu dans
    `RUSHES_TRIGGER`.
  - Mémoire : `memory.peak` compte le cache de pages des vidéos lues et écrites, qui remplit la limite sans vrai
    manque ; il n'est donc que journalisé à la clôture du run, jamais comparé. Le runner lit `anon_bytes` à la fin de
    chaque étape et garde le maximum (repli sans cgroup v2 : `max(ru_maxrss SELF, ru_maxrss CHILDREN) × 1024`) →
    `peak_mem_bytes` ; `oom_kills` = écart du compteur `oom_kill` de `memory.events` entre l'ouverture et la clôture
    du run (NULL sans cgroup v2).
  - Runs interrompus : chaque commande prend, pour toute sa durée, un `flock` exclusif sur `run_lock_path(run_id)`,
    supprimé à la clôture. Au démarrage d'une commande, une ligne `running` est périmée si et seulement si son verrou
    peut être pris (`LOCK_NB`) : le processus qui la tenait est mort. Une cession du verrou lourd à la voie express ne
    clôt donc jamais le run de nuit. Ligne périmée → `failed`, `exit_code` NULL, message « interrompu »,
    `Message("warn", …, alert="timer_failed:<commande>")`, fichier de verrou supprimé.

- [ ] **Step 1 : Tests qui échouent**

```python
# tests/test_audit.py
def test_audit_clean_database_has_no_violation(ctx, synth_ready):
def test_r1_audit_detects_cross_account_overlap(ctx):            # attribution insérée sans registre, chevauchement 2 001 ms → Violation R1
def test_r13_audit_detects_missing_required_hashtag(ctx):
@pytest.mark.parametrize(("age", "violation"), [(timedelta(days=7, hours=1), False), (timedelta(days=7, hours=7), True)])
def test_r35_audit_stale_ready_post_margin(ctx, age, violation):   # expiry_days = 7, marge 6 h
def test_audit_ignores_published_and_ecartes_folders(ctx, synth_ready):
    # post publié (dossier gardé), dossier sous outbox/_ecartes/, INDEX.html, post rendered après « Accroche 2 » → aucune violation ;
    # dossier d'un post is_test dans outbox/ ou d'un p_key absent de la base → violation R14
def test_audit_step_fails_items_and_alerts_once(ctx):            # 3 violations → 3 éléments failed, code 4, une seule alerte rule_violation
def test_pair_verdict_matches_can_assign(session):               # mêmes cas que test_r1_* et test_r2_* de la tâche 3, y compris C53 : plans noirs ignorés des deux côtés, 1 plan sur 4 refusé et 1 sur 5 accepté, gag de 3 plans recopié dans une partie de 30 plans refusé (sens passage attribué), passage attribué de 2 plans non refusé de ce sens, pas de cumul entre passages
def test_audit_pair_order_follows_assignment_order(session):        # passage de 2 plans attribué d'abord, partie de 30 plans avec 1 coïncidence attribuée ensuite (acceptée par can_assign) : aucune violation R1 ; le candidat de la paire est le plus tardif (assigned_at, puis segment_id), jamais l'inverse
# tests/test_licence_sync.py
def test_r14_licence_granted_moves_showcase_post_to_outbox(ctx):
    # licensed passe à true → post requeued, réempaqueté dans outbox/, caption.txt sans « NE PAS PUBLIER », plus rien dans outbox_test/
def test_r14_licence_revoked_alerts_and_keeps_ready_post(ctx):     # post prêt reste dans outbox/, alerte licence_revoked:tom_and_jerry avec p_key et créneau
def test_r14_licence_revoked_unpacked_post_goes_to_outbox_test(ctx):   # caption.txt commence par « NE PAS PUBLIER — »
def test_licence_sync_ignores_discarded_and_keeps_planned_unrendered(ctx):
    # posts discarded et expired : inchangés ; post planned : is_test recalculé, statut planned, aucun fichier
def test_audit_no_violation_for_ready_post_after_licence_revoked(ctx):
def test_licence_sync_never_moves_published_post(ctx):
def test_r16_licence_sync_rerun_changes_nothing(ctx):
# tests/test_runs_ops.py
def test_run_records_trigger_and_peak_memory(ctx, tmp_path, monkeypatch):
    # cgroup_root factice : anon 100 puis 123 entre deux étapes → peak_mem_bytes == 123 ; oom_kill 0 → 1 → oom_kills == 1 ;
    # memory.peak à 6 Gio ignoré ; trigger "timer"
def test_peak_memory_falls_back_to_rusage(tmp_path):
def test_stale_running_run_closed_as_interrupted(ctx):           # ligne running dont le verrou run-<id>.lock est libre → failed « interrompu »
def test_express_handover_does_not_close_night_run(ctx, tmp_path):
    # run de nuit vivant (verrou run-<id> tenu) qui cède le verrou lourd ; l'express démarre → ligne de nuit toujours running, aucune alerte
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter** (`pair_verdict` d'abord, sans changer le comportement
  de `can_assign` : les tests de la tâche 3 restent verts ; runbook complété). **Step 4 : Lancer** → PASS ;
  `test_runbook_covers_every_alert_kind` vert ; R14 retirée de `tests/rules_pending.toml` (il ne reste que R44, tâche 41) ;
  `test_rules_coverage` vert ; suite rapide, ruff, mypy verts ; test « ORM identique aux migrations » vert.

- [ ] **Step 5 : Sur le serveur** — `scripts/deploy.sh "$RUSHES_HOME"` ; `scripts/rushes audit` sur la vraie base → aucune
  violation, ou chaque violation corrigée dans le code (jamais dans la règle) avant la tâche 40b.

- [ ] **Step 6 : Commits proposés** —
  `feat(ops): nightly rule audit with violation alerts`
  `feat(rights): requeue posts whose licence status changed (R14, R22, R32)`
  `feat(runner): record run trigger and peak memory, close interrupted runs`

---

### Task 40b : Répétition générale de 7 jours

**Files :**
- Create : `src/rushes/ops/rehearsal.py`, `tests/test_rehearsal.py`
- Modify : `src/rushes/cli.py` (`rehearsal report`), `tests/test_cli.py` (`rehearsal report` dans `IMPLEMENTED`),
  `docs/decisions.md` (bilan)

**Interfaces :**
- Consomme : `runs` (`status`, `trigger`, `peak_mem_bytes`, `oom_kills`, 40a), `stock_days` (10b), résultats de
  l'étape `audit`, `reports/`, `Check` (39).
- Produit :
  ```python
  # ops/rehearsal.py
  EXPECTED_RUNS: Mapping[str, int]          # par profil sur 7 jours : night 7, watch 28, stats 28, weekly 1, maintenance 1
  def rehearsal_checks(session: Session, ctx: Context, *, start: datetime, days: int = 7,
                       memory_limit_bytes: int = 6 * 1024**3) -> list[Check]: ...
  ```
  CLI : `rushes rehearsal report --since AAAA-MM-JJ [--days 7] [--drills FICHIER]` → `reports/rehearsal-<date>.txt`
  et `reports/rehearsal-<date>.json` (`{"ok": bool, "since": "AAAA-MM-JJ", "checks": [{"name", "ok", "detail"}]}`,
  lu par `launch_checks`), code 0 si tous les contrôles sont verts, sinon 1.
- Règles fixées (contrôles) :

  | Nom | Vert si |
  | --- | --- |
  | `runs` | pour chaque profil, au moins `EXPECTED_RUNS` runs `trigger='timer'` ; aucun run `runs.status = 'failed'` hors exercices (`partial`, code 4, et source indisponible, code 5, ne comptent pas : ce sont des sorties normales, `SuccessExitStatus=4 5`) |
  | `interrupted` | aucune ligne close « interrompu » hors exercice |
  | `stock` | `stock_days >= 7` pour chacun des 8 comptes (posts `ready` + `planned`, `outbox/` ou `outbox_test/`) ; Peak Moments est alimenté par Beta Squad, CaseOh et Dude Perfect en vitrine (`decisions.md` L31) |
  | `audit` | 7 runs de l'étape `audit`, zéro élément `failed` |
  | `memory` | aucun `oom_kills > 0` et `max(peak_mem_bytes) < memory_limit_bytes` (mémoire anonyme, cache de pages exclu) |
  | `reports` | 7 rapports quotidiens et 1 hebdomadaire présents |
  | `manual` | liste des runs `trigger='manual'` (information : chacun doit correspondre à un exercice du calendrier) |

  Un exercice est reconnu à sa fenêtre horaire, notée dans `--drills FICHIER` (TOML : `[[drill]] name, start, end`),
  qui excuse les échecs et interruptions dans cette fenêtre.

- [ ] **Step 1 : Tests qui échouent**

```python
def test_rehearsal_all_green_on_simulated_week(ctx):             # runs simulés conformes → 7 Check verts, code 0, JSON {"ok": true}
def test_rehearsal_flags_missing_runs_and_memory(ctx):
    # 6 nuits seulement, un pic anon à 6,2 Gio → runs et memory rouges ; autre cas : oom_kills = 1 → memory rouge
def test_rehearsal_partial_runs_not_failures(ctx):              # runs status partial (code 4 ou 5) → runs vert
def test_rehearsal_excuses_failures_inside_drill_window(ctx, tmp_path):
def test_rehearsal_stock_per_account(ctx):                       # un compte à 5 jours → stock rouge, le compte nommé
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS ; suite rapide, ruff, mypy
  verts. `scripts/deploy.sh "$RUSHES_HOME"` sur le serveur.

- [ ] **Step 5 : Répétition (serveur, 7 jours, rien n'est publié)** — début un lundi à 0 h (heure de Paris), noté
  `J1`. Calendrier des exercices, écrit d'avance dans `data/ops/drills.toml` :

  | Jour | Exercice | Attendu |
  | --- | --- | --- |
  | J1 | aucun : les minuteurs tournent seuls | — |
  | J2, 3 h | `docker kill rushes-rushes-night` pendant le rendu | run clos « interrompu » au run suivant ; la nuit J3 termine ; aucun post en double (R16) |
  | J3, 10 h | `scripts/rushes run --profile night` lancé à la main, puis `scripts/rushes creators express` pendant son rendu | le rendu cède entre deux posts ; express sous 60 min ; la nuit termine ensuite, sa ligne `runs` jamais close « interrompu » |
  | J4, 12 h–14 h | `docker compose stop ntfy` (couvre `watch` de 12 h 30 et `stats` de 13 h 15) | runs verts ; alertes en attente renvoyées au run suivant (P20) |
  | J5, 10 h | copie de la config dans `data/ops/config-drill/` avec `licensed: true` sur une série de vitrine ; `set -a; . /etc/rushes/rushes.env; set +a; docker compose --env-file /etc/rushes/rushes.env -f "$RUSHES_HOME/docker/compose.yaml" run --rm -T -v "$RUSHES_DATA_DIR/ops/config-drill:/config:ro" rushes rushes rights sync`, puis `scripts/rushes rights sync` avec la vraie config | posts déplacés vers `outbox/`, puis laissés en place au retour à la vraie config ; alerte `licence_revoked` reçue ; Louis les écarte depuis la page ; aucune violation à l'audit ; dépôt intact |
  | J6, 10 h | `scripts/deploy.sh "$RUSHES_HOME" --rollback` puis `scripts/deploy.sh "$RUSHES_HOME"` | codes 0 ; sauvegarde `pre-rollback` présente ; base gardée (révision inchangée), runs J1–J6 toujours là ; `scripts/rushes status` identique |
  | J7, 10 h | redémarrage du serveur | minuteurs rattrapés (`Persistent=true`) ; `serve` se réveille à la première requête |

  Pendant la semaine, Louis utilise la page de `serve` deux fois (J2 et J5) comme au lancement : télécharger,
  AirDrop, copier la légende, « Écarter » un post, « Accroche 2 » sur un autre ; il ne publie rien. Il note chaque
  alerte et chaque résumé quotidien reçus sur l'iPhone.

- [ ] **Step 6 : Bilan** — `scripts/rushes rehearsal report --since <J1> --drills /data/ops/drills.toml` → code 0 ;
  `scripts/install_units.sh` n'a pas été relancé à la main ; aucun fichier de `data/` modifié à la main. Un échec
  → correction (tâche de correctif, tests d'abord), nouveau déploiement, et **nouvelle répétition de 7 jours**.
  Bilan (contrôles, pics de mémoire par profil, durées des nuits, alertes reçues) consigné dans `docs/decisions.md`.

- [ ] **Step 7 : Commit proposé** — `feat(ops): rehearsal report over runs, stock, audits and peak memory`

---

### Task 41 : Lancement et rodage

**Files :**
- Create : `src/rushes/ops/launch.py`, `src/rushes/ops/burnin.py`, `tests/test_launch.py`, `tests/test_burnin.py`
- Modify : `src/rushes/cli.py` (`launch check`, `launch start`, `burnin status`), `tests/test_cli.py` (ces trois
  commandes dans `IMPLEMENTED`), `src/rushes/ops/report.py` (section hebdomadaire « Rodage »),
  `docs/ops/runbook.md` (lancement, rodage),
  `docs/spec.md` (Points à vérifier), `docs/decisions.md`

**Interfaces :**
- Consomme : `run_checks` (39), `rehearsal_checks` (40b), `is_test_post`, `post_actions` (36), `metrics`,
  `stats_imports`, `collector_health` (17), `post_outcomes`, `bandit_arms` (18), `derived_values` (37), étape
  `audit` (40a), `derived.record`/`resolve`.
- Produit :
  ```python
  # ops/launch.py
  def launch_checks(ctx: Context, *, now: datetime) -> list[Check]: ...
  def mark_launched(session: Session, *, now: datetime) -> None: ...    # derived "ops.launched_at", portée "global", source "launch" ;
  #   + "rotation.epoch" (portée "cartoons") = lundi du lancement, source "launch" (tâche 16a, L6)
  # ops/burnin.py
  @dataclass(frozen=True)
  class Criterion: name: str; account_id: str | None; state: Literal["ok", "pending", "failed"]; detail: str
  def burnin_status(session: Session, ctx: Context, *, now: datetime) -> list[Criterion]: ...
  ```
  CLI : `rushes launch check` (code 0 si tout est vert) ; `rushes launch start` (refuse si `launch check` est rouge,
  écrit `ops.launched_at` et l'ancre de rotation une seule fois) ; `rushes burnin status` (tableau ; code 0 quand tout est `ok`).
- Règles fixées :
  - `launch_checks` : tous les contrôles de `run_checks` ; dernier `reports/rehearsal-*.json` avec `"ok": true` et
    daté de moins de 14 jours (le `.txt` n'est jamais analysé) ; au moins un compte avec des posts `ready` publiables (non test), sinon rouge, avec la piste
    « aucune licence : passer `licensed: true` / `authorized: true` puis `rushes rights sync` » ; un compte sans
    post publiable → `Check` jaune (vert, détail « vitrine seulement ») ; `rushes audit` sans violation.
  - Critères du rodage, par compte publiable (jour 0 = `ops.launched_at`) :

    | Critère | `ok` si | `pending` tant que |
    | --- | --- | --- |
    | `detection` | parmi les 10 premiers posts publiés (bouton « Publié » ou détection), au moins 9 rattachés à leur vidéo publique (`platform_post_id` posé, tâche 17) : sous 48 h pour TikTok, au premier export importé après la publication pour Instagram | moins de 10 posts publiés, ou délai de rattachement pas encore écoulé |
    | `tiktok_stats` | dernière ligne `metrics` d'un post TikTok de moins de 7 h ; `collector_health.tiktok` à 0 échec (seul ce compteur : `tiktok_market` et `tiktok_embed` de l'étude de marché n'y entrent pas) | aucun post TikTok publié |
    | `instagram_import` | au moins un `stats_imports` Instagram réussi et des `metrics` pour ses posts | moins de 8 jours depuis le lancement |
    | `outcomes` | au moins 5 `post_outcomes` de posts non test dont la publication (`published_at`) ou l'écart est postérieur à `ops.launched_at` (bandits mis à jour) | moins de 72 h depuis le premier post publié |
    | `recalibration` | après J21, une valeur `slots` du compte avec `source` `recalibrate:*`, ou la mention « échantillon insuffisant » au rapport si moins de 15 posts | avant J21 |
    | `audit` (global) | aucune violation depuis J0 | — |
    | `unmatched` (global) | aucune vidéo publique non rapprochée de plus de 48 h | — |

    Un critère passé `ok` ne redevient `pending` jamais ; `failed` seulement pour `detection` (< 9 sur 10),
    `tiktok_stats` (plus de 24 h sans données), `audit` et `unmatched`. La section « Rodage » du rapport
    hebdomadaire disparaît quand tout est `ok` après J21. Seules les données postérieures à `ops.launched_at`
    comptent ; `launch start` ne remet rien à zéro (les écarts de la répétition sont des posts de test, ignorés par
    `BanditsStep`, tâche 18).
  - Louis clique « Publié » quand il y pense ; la détection sert de secours (post resté `ready` reconnu sur le
    profil public) et rattache dans tous les cas le post à sa vidéo publique, ce qui permet les statistiques. Le
    critère `detection` mesure ce rattachement, quelle que soit l'origine du statut `published`.

- [ ] **Step 1 : Tests qui échouent**

```python
# tests/test_launch.py
def test_launch_check_green_when_ready(ctx, tmp_path):
def test_launch_check_without_publishable_stock(ctx):            # aucune licence → rouge avec la piste rights sync
def test_launch_check_stale_rehearsal_report(ctx, tmp_path):     # rehearsal-*.json ok de 20 jours → rouge ; récent avec "ok": false → rouge
def test_launch_start_refuses_when_red_and_is_idempotent(ctx):
def test_launch_start_reanchors_rotation_to_launch_monday(ctx):   # lancement jeudi 2026-12-03 → rotation.epoch 2026-12-07 ; lundi 2026-12-07 → même jour
def test_r44_launch_reanchor_keeps_existing_owners(ctx, synth):     # épisodes ingérés avant : propriétaire inchangé
# tests/test_burnin.py
def test_burnin_sparse_accounts_pending(ctx):                    # J3, aucun post publié → tout pending, code 1, aucune exception
def test_burnin_detection_ok_and_failed(ctx):                    # 10 publiés (6 par le bouton, 4 détectés) dont 9 rattachés → ok ; 8 → failed
def test_burnin_outcomes_count_only_after_launch(ctx):           # 6 post_outcomes datés d'avant ops.launched_at → outcomes pending
def test_burnin_recalibration_after_day_21(ctx):
def test_burnin_weekly_section_disappears_when_all_ok(ctx):
```

- [ ] **Step 2 : Lancer** → FAIL. **Step 3 : Implémenter.** **Step 4 : Lancer** → PASS ; suite rapide, ruff, mypy
  verts ; R44 retirée : `tests/rules_pending.toml` vide et `test_rules_coverage` vert. `scripts/deploy.sh` sur le serveur.

- [ ] **Step 5 : Checklist de lancement (Louis et serveur)** — chaque case de `00-master.md` §10 « Avant le
  lancement » cochée, avec en plus :
  - licences obtenues inscrites (`licensed: true` / `authorized: true`), config commitée par Louis, `git pull` et
    `scripts/deploy.sh` sur le serveur, puis `scripts/rushes rights sync` → posts de vitrine des sujets autorisés
    dans `outbox/` ;
  - charte de chaque marque validée sur des rendus d'exemple (Q10) ; test TikTok Studio fait et noté dans
    `docs/decisions.md` ;
  - `scripts/rushes launch check` → code 0 ; `scripts/rushes launch start`.

- [ ] **Step 6 : Première séance de publication (Louis)** — depuis la page de `serve`, programmer les posts du
  premier jour dans les applis selon `upload.json` (parties dans l'ordre) ; cliquer « Publié » une fois le post en
  ligne, quand c'est possible.

- [ ] **Step 7 : Rodage (au moins 21 jours)** — chaque semaine : `scripts/rushes burnin status` et la section « Rodage »
  du rapport ; Louis vérifie le Statut du compte Instagram et les vidéos « non éligibles » de TikTok, puis décide du
  module d'originalité de chaque compte (config `originality`, noté dans `docs/decisions.md`). Un critère `failed`
  → correctif (tests d'abord) et déploiement ; la règle ne change pas.

- [ ] **Step 8 : Clôture** — `scripts/rushes burnin status` → code 0 après J21 ; « Points à vérifier » de la spec : seuils
  calibrés, format réel de l'export Instagram, TikTok Studio, imitation de navigateur (surveillée), vitesses réelles
  cochés avec renvoi à `docs/decisions.md` ; entrée « Fin du rodage » dans `docs/decisions.md` (valeurs recalées,
  taux de détection, module d'originalité par compte).

- [ ] **Step 9 : Commits proposés** —
  `feat(ops): launch checklist and burn-in criteria`
  `docs(spec): close verification points after burn-in`

---

## Porte de sortie de la phase 6

**Avant le lancement (tâches 39, 40a, 40b) :**

- [ ] Installation refaite depuis le dépôt en suivant seulement `docs/ops/runbook.md`, dont une première
  installation sans image ni base ; `rushes doctor` vert ; corps de 5 Mo accepté par GoDoxy ; jetons ntfy conservés
  après `docker compose down` ;
  limites vérifiées par `docker inspect` ; `serve` injoignable sans PocketID et depuis le réseau local ; réveil
  après mise en veille vérifié ; restauration, copie hors RAID et retour arrière exercés.
- [ ] `rushes rehearsal report` vert sur 7 jours consécutifs, exercices compris ; bilan dans `docs/decisions.md`.
- [ ] Toutes les cases de `00-master.md` §10 « Avant le lancement » cochées ; `rushes launch check` vert.

**Après le lancement (tâche 41) :**

- [ ] `rushes burnin status` vert après au moins 21 jours ; « Points à vérifier » de la spec tous cochés ou
  renvoyés à une décision datée ; entrée « Fin du rodage » dans `docs/decisions.md`.
