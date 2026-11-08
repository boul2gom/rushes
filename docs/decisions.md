# Décisions prises pendant le développement

Une entrée par décision : date, question, réponse, sections ou règles de `docs/spec.md` concernées.
Si une décision change une décision arrêtée de la spec, mettre aussi la spec à jour.

## 2026-11-01 — Revue de la spec avant la tâche 1

Décisions de Louis (« ok à tout » sur les recommandations). Les points C1 à C13 sont corrigés dans la spec.
Les modifications de R4, R33 et R47 sont des décisions explicites de Louis.

| # | Question | Réponse | Concerne |
| --- | --- | --- | --- |
| C1 | R1 vérifiée à l'approbation mais contrôlée seulement à l'attribution | L'approbation appelle `can_assign` en simulation (sans écrire) ; l'attribution refait le contrôle dans sa transaction | R33, étape 9 |
| C2 | Mauvais renvoi (R34) pour le re-sourçage | Corrigé en R15 | Étape 1 |
| C3 | `upload.json` ou `post.json` | `upload.json` seul ; la traçabilité (R17) reste en base | Étape 14 |
| C4 | Rotation vs propriété d'un épisode | Propriété définitive ; la rotation ne décide que des nouveaux épisodes ; l'épisode se termine sur son compte | R4, R44, Plan croisé |
| C5 | Partie écartée vs partition | Pas de régénération ; les suivantes sont renumérotées ; les parties sont indépendantes | Étape 6, R9, R37 |
| C6 | « Partition complète » vs reliquat abandonné | Reformulé : partition sauf reliquat trop court | Étape 6, algorithme |
| C7 | Unités de temps mélangées | Unité dans le suffixe : `_ms` entiers en interne (`duration_ms`, `target_ms`), `_s` pour les statistiques plateforme ; la config reste en secondes | Modèle de données |
| C8 | Sens de `share` et de Creator Rewards | `share` = part de chaque famille par plateforme ; Creator Rewards = TikTok seulement | Familles de durée |
| C9 | Statut `test` | Booléen `posts.is_test` ; cycle arrêté à `ready`, jamais poussé vers Postiz | Statuts, R14, R22 |
| C10 | Trois sources de vérité pour `authorized` | Le YAML est la source unique ; `channels.status` est un miroir ; aucune commande `creators authorize` | R32, prospection |
| C11 | Deux CLAUDE.md | Celui du dépôt fait foi ; la spec y renvoie | Fin de la spec |
| C12 | Numérotation des règles | « R1 à R49, R31 supprimée » | Règles, CLAUDE.md |
| C13 | Rich dans `--help` | L'aide rendue par Typer est exemptée de R47 | R47 |

Choix techniques pour la tâche 1 (Louis : « ok à tout »).

| # | Décision |
| --- | --- |
| F1 | `RUSHES_CONFIG_DIR` (défaut `./config`) et `RUSHES_DATA_DIR` (défaut `./data`, `/data` en conteneur) ; la config ne définit pas `data_dir` |
| F2 | Validation stricte seulement pour ce qui est activé ; placeholders `<...>` tolérés si `llm.enabled` ou `postiz.enabled` est faux |
| F3 | Référence orpheline (rotation, `creators_by_brand`, compte vers marque) = erreur ; l'exemple de config est complété |
| F4 | Erreurs : langue du créateur ≠ marque (R21), rotations qui se chevauchent pour une marque, même série à deux marques sur une période, identifiants en double. Avertissement : `creators_by_brand` vide |
| F5 | Commande non implémentée : message « non implémentée (tâche N) », code de sortie 1. Pas de flag `--test` : le mode test se déduit de `licensed` / `authorized` |
| F6 | SQLAlchemy 2.1 + Alembic (pas SQLModel) |
| F7 | Secrets lus à l'usage, en `SecretStr` ; `--help` et `status` fonctionnent sans clé |
| F8 | Clé primaire entière pour les entités du pipeline, affichée `p_000123` ; identifiants de config en texte |
| Q7 | `git init` fait par Louis |
| Q8 | `requires-python = ">=3.12,<3.13"` ; uv télécharge la 3.12 |
| — | Migration 0001 = cœur du pipeline (brands, accounts, series, creators, source_videos, episodes, scenes, segments, assignments, compilations, compilation_items, hooks, posts, runs) ; les autres tables arrivent avec leur tâche |

## 2026-11-01 — Durées : uniquement des vidéos monétisables

Question (Louis) : seulement des longueurs de vidéos permettant une monétisation, pour que toutes aient un potentiel de monétisation.
Réponse : la famille `discovery` (25 à 50 s) est supprimée. Restent `monetizable` (65 à 100 s, 100 % des posts courants sur chaque plateforme) et `long` (120 à 180 s, ponctuel). Toute famille doit avoir `min_s` > 60, sinon la config est refusée.
Concerne : glossaire (`family`), tableau des familles, `accounts.yaml` (`families`), R6, décisions arrêtées (ligne « Durées »).
Conséquence connue : il n'y a plus de format court pour attirer des abonnés ; à reconsidérer si la croissance est trop lente.

## 2026-11-01 — Tâche 1 : outillage ajouté (plan approuvé par Louis)

- Deux commandes hors de la liste du pipeline : `rushes config check` (valide la config, affiche les avertissements) et `rushes db upgrade` (migrations + miroir de la config). Elles servent le critère de la tâche 1.
- Code de sortie 2 pour une configuration invalide ; 1 pour une commande non implémentée (F5).
- Modèles ORM déclaratifs SQLAlchemy ; un test vérifie qu'ils restent identiques à la migration 0001 (diff Alembic vide).

## 2026-11-01 — Autonomie du logiciel : chaînes, popularité, hashtags, config

Demande (Louis) : pour trouver les vidéos, chercher les chaînes officielles et regarder les vidéos populaires (le plus de vues), par exemple `https://www.youtube.com/@oggy/videos`. Les hashtags sont du ressort du logiciel : il regarde les tendances et ce qui marche sur les vidéos du même secteur ; idem pour les autres informations de la config. Le tout doit être vraiment autonome.

Décisions :
- Le logiciel trouve seul les chaînes officielles d'une série (nom, badge, abonnés), lit leur onglet Vidéos trié par vues, ajoute chaînes communautaires et mots-clés (eux aussi déduits). En cas de doute sur une chaîne : `community` (R15) jusqu'à confirmation de Louis dans la config.
- Le panel de comptes TikTok et les hashtags (ancrage et tournants) sont déduits des tendances et du secteur. Jusqu'à la première étude : amorces de la config, sinon hashtags tirés du nom.
- Créneaux, parts de familles et seuils sont recalés par le logiciel ; la config donne des amorces.
- La config ne contient que des amorces facultatives ; une valeur de la config l'emporte sur la valeur déduite. Restent à Louis, jamais déduits ni par défaut : `licensed`, `authorized`, `publish_mode`, marques, langues, comptes.
- Schéma de config assoupli : une série n'exige plus que `id`, `title`, `audio`, `licensed` ; `sources`, `branding` et `hashtags_anchor` (série et créateur) deviennent facultatifs.

Concerne : principe et décisions arrêtées (ligne « Autonomie »), étapes 1 et 2, glossaire des hashtags, exemples de config, tâches 10 et 31, points à vérifier, CLAUDE.md.
Points ouverts (ajoutés aux points à vérifier) : tri par popularité via yt-dlp, fiabilité de la détection d'une chaîne officielle, source des tendances sans API, table de stockage des valeurs déduites.

## 2026-11-01 — Python 3.15 au lieu de 3.12

Demande (Louis) : passer à Python 3.15 (installé via Homebrew au besoin). Python 3.15.2 était déjà installé (`/opt/homebrew/bin/python3.15`).
Réponse : `requires-python = ">=3.15,<3.16"`, `.python-version` = 3.15, cibles ruff `py315` et mypy `3.15`. Remplace la décision Q8 (3.12) et la ligne « Langage et outillage » de la spec.
Vérifié sur PyPI le 1er novembre 2026 : wheels cp315 présents pour pydantic-core, SQLAlchemy, numpy, Pillow, torch, ctranslate2, rapidfuzz (OpenCV en abi3). pyyaml et llama-cpp-python se compilent à l'installation. **onnxruntime n'a aucun wheel pour 3.15** : faster-whisper en dépend, ce qui peut bloquer la tâche 12 ; à revérifier avant elle (repli possible : environnement 3.14 pour la transcription, à décider alors).
Mise à jour du jour même : contourné, voir « Recherche technique » ci-dessous ; Python 3.15 est conservé (choix de Louis : contourner plutôt que revenir en 3.14).
**Remplacé par la décision du 2026-11-05 : retour en Python 3.14** (voir « Repli en Python 3.14 (décision de Louis) » en fin de fichier).

## 2026-11-01 — Cadre de l'implementation plan complet

Réponses de Louis pour la rédaction du plan de tout le projet :

- Production : tout est fini avant le premier post (phases 1 à 5, console web exclue).
- Détail : les 31 tâches détaillées ; code écrit seulement pour les contrats (interfaces, schémas, migrations).
- Existant : Docker sur le serveur, les 8 comptes TikTok et Instagram créés, domaine HTTPS et reverse proxy. Pas de ntfy.
- Validation sans publier : métadonnées enregistrées et répétition générale de 7 jours avant le lancement ; les critères qui exigent de
  vrais posts (détection, statistiques, bandit, recalage) sont vérifiés pendant un rodage prévu au plan.
- Serveur : 16 Go de RAM partagés ; un modèle lourd chargé à la fois.

## 2026-11-01 — Décisions de Louis pendant la recherche

| # | Décision | Concerne |
| --- | --- | --- |
| D1 | **Postiz abandonné.** Louis prépare lui-même les posts sur son téléphone (programmation dans l'appli quand elle existe, sinon brouillons). Raisons : complexité, aspect moins « bot », et les règles TikTok refusent l'audit d'un « outil pour publier sur les comptes que vous gérez » | Décision « Publication », R42, R48, tâches 19 et 29, `publish push/sync`, `settings.postiz`, `accounts.publish_mode/postiz_integration`, colonnes `posts.postiz_*`, statuts `drafted`/`scheduled` |
| D2 | **Deux modules d'originalité, tous deux codés et prêts en production**, bascule rapide par la config. Module de base : accroche, légende, numéro de partie ou titre du mini best-of au-dessus de la vidéo. Module avancé : voix off générée en local et/ou mise en scène, activé plus tard si le contenu est trop signalé comme non original | Rendu, accroches, R8, R9 |
| D3 | **R8 modifiée : recadrage autorisé pour les cartoons**, avec cadrage intelligent qui suit l'action. Ordre de préférence : carré 1:1 (par défaut), vertical 9:16 de temps en temps (test), repli 3:4, image entière sur fond flou en tout dernier recours seulement | R8, rendu, tâche 7 |
| D4 | **R13 modifiée : hashtags, 5 au maximum sur les deux plateformes.** TikTok cartoons : `#fyp #nostalgiatok #cartoons #<nom anglais de la série>` + 1 selon le contenu et les tendances. Instagram cartoons : 3 de base + 2 selon le contenu et les tendances. TikTok créateurs : `#fyp #<créateur ou chaîne>` obligatoires ; le reste selon le contenu et les tendances | R13, `meta` |
| D5 | Mise à jour de yt-dlp : à prévoir comme une opération courante (Louis : « assez simple, mais à prévoir ») | R38, maintenance |

## 2026-11-01 — Questionnaire de décisions (étape 3 du plan)

| # | Question | Réponse de Louis | Concerne |
| --- | --- | --- | --- |
| Q1 | AI-Youtube-Shorts-Generator : copier ou réécrire ? | Réécrire ce dont on a besoin, sans se forcer à l'utiliser si c'est mauvais ou mal adapté. Plus de `vendor/` pour l'instant ; `vendor check` et les tâches 21 et 23 sont retirés ; R40 et R41 restent valables pour tout futur code tiers | R40, R41, tâches 21 et 23 |
| Q2 | Téléphone | iPhone (ntfy : `upstream-base-url: https://ntfy.sh`) | ntfy |
| Q3 | Accès aux posts prêts | **Depuis le Mac seulement** ; Louis envoie vidéos et textes au téléphone par AirDrop. Un tableau de bord compatible téléphone viendra plus tard, hors périmètre | `package`, `INDEX.html` |
| Q4 | Écarter un post, changer d'accroche | Boutons dans la page, servie par un petit serveur web de rushes sur l'EliteDesk (les données y sont), protégé par le reverse proxy de Louis et son SSO PocketID | Nouvelle commande `rushes serve`, R37 |
| Q5 | Rotation des séries cartoons | Générée par rushes ; une `rotation` écrite dans la config reste prioritaire | R4, tâche 16 |
| Q6 | Catalogue de départ | Tom et Jerry, Oggy, Squeezie et Byilhan comme base de travail (Grizzy retiré). Ensuite rushes cherche seul de nouvelles séries et de nouveaux créateurs dans les tendances, **sans se soucier des licences** : tout le pipeline tourne sur eux en mode test, comme **vitrine** prête à présenter aux ayants droit. Un **tableau de suivi des licences** liste ce qui en a besoin, pour que Louis fasse les demandes. But final : suivre seul des tendances qui changent, sans configuration manuelle | Autonomie, R14, R22, R32, nouvelle prospection de séries |
| Q7 | Granularité du module d'originalité avancé | Par compte, avec un réglage global par défaut | D2 |
| Q8 | Contenu de la voix off | Commentaire de 1 à 3 phrases écrit par le modèle local à partir de la scène, dans la langue de la marque ; son original atténué pendant la voix ; sous-titres calés sur la voix | D2 |
| Q9 | Modèle de langage | Qwen3-4B-Instruct-2507 Q4, repli automatique Qwen3-1.7B | Accroches, voix off |
| Q10 | Charte graphique | rushes propose une charte par marque (polices libres OFL embarquées, palette, gabarits) ; Louis valide sur des rendus d'exemple et corrige dans la config | Rendu |
| Q11 | Cookies Google | Aucun cookie par défaut ; cookies seulement en secours en cas de blocage « confirm you're not a bot », avec alerte ; idéalement un compte Google dédié | Décision « Récupération », R18 |
| Q12 | Hashtags de base | Instagram cartoons : `#cartoons` + un hashtag nostalgie choisi par l'étude de marché (ex. `#nostalgia`) + 3 selon le contenu et les tendances. TikTok créateurs : `#fyp #<créateur>` + 3. Instagram créateurs : `#<créateur>` + 4. `#<créateur>` = le nom du créateur de contenu (ex. `#squeezie`). Complète D4 | R13 |

Choix faits par moi, à relire : le rythme des comptes créateurs est le même que celui des cartoons (proposition de la spec) ;
les séries et créateurs candidats trouvés seuls sont produits en vitrine avec un budget de calcul plafonné, pour ne pas ralentir le stock publiable.

## 2026-11-01 — Relecture de `docs/plans/00-master.md` par Louis

| # | Sujet | Décision | Touche |
| --- | --- | --- | --- |
| M1 | Risque Python 3.15 | Nouvelle tâche 42, juste après la 33 : image avec toutes les dépendances lourdes, une inférence par modèle sur le serveur, rapport go/no-go | Ordre de la phase 1, tâche 9 |
| M2 | Voie express et mémoire | Le runner décharge les modèles (`ModelHub.release()`) avant de céder le verrou lourd | R19, budget mémoire |
| M3 | Authentification de `serve` | Entièrement assurée par le reverse proxy **GoDoxy** et PocketID ; `serve` ne vérifie aucune identité. Refusé : un en-tête secret partagé proxy → `serve`. Gardé : aucun port publié sur l'hôte (test) et contrôle `Origin` contre les requêtes croisées | Tâches 36, 39 |
| M4 | Veille | GoDoxy met le conteneur `serve` en veille au repos ; `serve` démarre vite, sans modèle ni torch | Tâche 36 |
| M5 | Serveur web | FastAPI au lieu de Starlette (client TypeScript généré depuis OpenAPI, futur tableau de bord) | Tâche 36 |
| M6 | Taille des tâches | Tâches 7, 8 et 12 découpées en 7a/7b, 8a/8b, 12a/12b/12c | Plans de phase 1 et 2 |
| M7 | Petits points | Code de sortie 4 prioritaire sur 5 ; historique des valeurs déduites ; sha256 des poids vérifié par `models fetch` ; tâche 20 écrit les unités systemd, tâche 39 les installe | Contrats de `00-master.md` |

## 2026-11-01 — Recherche technique (étape 2 du plan)

Quatre recherches menées le 2026-11-01, dont des sondes réelles avec yt-dlp (sans cookies, 2 s entre requêtes, métadonnées seulement)
et un `uv lock` universel suivi d'un `uv sync` réel sous Python 3.15.2. Ce sont des constats datés : à revérifier si un comportement change.

### Pile Python 3.15
_Constats datés du 2026-11-01 sous 3.15 ; le projet est revenu en 3.14 le 2026-11-05 (voir en fin de fichier) : l'override onnxruntime ci-dessous n'existe plus._
- **Seul paquet bloquant : onnxruntime** (ni wheel cp315 ni sdist). Contournement vérifié : `[tool.uv] override-dependencies =
  ["onnxruntime; python_version < '3.15'"]` (uv : https://github.com/astral-sh/uv/blob/main/docs/concepts/resolution.md).
  faster-whisper n'importe onnxruntime que pour son VAD : `vad_filter=False` et segments de parole passés par `clip_timestamps` ;
  ne jamais utiliser `BatchedInferencePipeline` (VAD activé par défaut).
- Tout le reste s'installe en 3.15 (78 paquets, imports vérifiés) : torch 2.15 (index CPU sous Linux), ctranslate2 4.8, faster-whisper 1.2.1,
  silero-vad 6.2.3, open_clip 3.3, opencv(-contrib)-python-headless 5.0 (abi3), scenedetect-headless 0.7.2, numpy 2.5, scipy 1.18,
  Pillow 13, yt-dlp 2026.10.26. llama-cpp-python 0.3.36 n'existe qu'en sources (compilation vérifiée) ; PyYAML tourne en pur Python.
- Silero VAD sans onnxruntime : `load_silero_vad(onnx=False)`, audio 16 kHz décodé par FFmpeg (pas `read_audio`, pas l'extra `[audio]`).
- Sons : **sherpa-onnx** (wheels cp315, moteur ONNX embarqué) avec les modèles CED (Apache-2.0) plutôt que `panns-inference`
  (abandonné, téléchargement par `wget`, tire librosa et matplotlib). https://github.com/k2-fsa/sherpa-onnx
- Vision : CLIP ViT-B-32 `datacomp_xl_s13b_b90k` (meilleur score moyen, MIT) ; YuNet `face_detection_yunet_2026may.onnx` via `cv2.FaceDetectorYN`.
- PySceneDetect 0.7.2 : `detect(path, AdaptiveDetector(), backend="opencv")` ; défauts d'`AdaptiveDetector` inchangés en 0.7.2
  (`adaptive_threshold=3.0`, `min_scene_len=15`, `window_width=2`, `min_content_val=15.0`), à fixer explicitement.
- Modèle de langage : `create_chat_completion(response_format={"type": "json_object", "schema": …})` (point « à vérifier » de la spec résolu).
  Candidats : Qwen3-4B-Instruct-2507 Q4_K_M (≈ 3,1 Go, Apache-2.0, sans mode réflexion), SmolLM3-3B (≈ 2,3 Go, `/no_think`),
  repli Qwen3-1.7B (≈ 1,5 Go).
- Image Docker : `python:3.15-slim-trixie` épinglée par digest ; FFmpeg Debian 7.1.5 (libass et libx264 présents) ; llama-cpp-python compilé
  dans une étape de construction (AVX2, FMA, F16C, BMI2, sans AVX-512 : CPU Coffee Lake « T ») ; `libgomp1` à l'exécution.
- Mémoire mesurée (pic) : Whisper small int8 ≈ 1,0 Go, base ≈ 0,8 Go, CLIP ≈ 0,8 à 1,5 Go, Silero 0,24 Go, YuNet 0,05 Go ; modèle 4B ≈ 3,1 Go.
  Proposition : conteneur limité à 6 Go, un modèle lourd à la fois, 4 threads.
- Synthèse vocale (module avancé) : sherpa-onnx avec **Supertonic 3** (français et anglais, code MIT, poids OpenRAIL-M) ; repli Kokoro en anglais.
  kokoro, pocket-tts et coqui-tts déclarent `requires-python < 3.15`. Pas d'horodatage par mot : l'audio généré est retranscrit par faster-whisper.
  À écarter pour licence : F5-TTS (poids non commerciaux), XTTS (CPML), Ultralytics (AGPL).
- Recadrage des cartoons : problème à une dimension (position horizontale), plan par plan ; carte d'importance mouvement (flux optique,
  panoramique global retiré) + saillance OpenCV ; recadrage fixe si une fenêtre couvre au moins 80 % de l'importance, sinon panoramique lissé
  (zone morte, vitesse maximale) ; CLIP sur 1 à 2 images clés pour les plans ambigus ; repli selon D3. Google AutoFlip n'a plus d'API Python.
- Licences des poids : toutes compatibles avec des comptes monétisés (attribution pour les poids CC-BY).

### YouTube avec yt-dlp (sondes)
- yt-dlp ≥ 2025.11.12 exige un **runtime JavaScript** (Deno) **et** `yt-dlp-ejs`, même pour les métadonnées ; sans eux : « This video is not
  available ». Paquet : `yt-dlp[default,curl-cffi]` + Deno. https://github.com/yt-dlp/yt-dlp/wiki/EJS
- **Vidéos les plus vues d'une chaîne** : le filtre « Populaires » et `?sort=p` ne sont pas gérés ; la playlist `UULP` + identifiant de chaîne
  sans « UC » donne les 200 vidéos les plus vues, déjà triées (sondé sur @oggy). Repli : lire l'onglet Vidéos et trier par `view_count`.
- **Chaînes officielles** : la recherche de chaînes (`results?search_query=…&sp=EgIQAg%3D%3D`) renvoie en une requête le badge vérifié et
  le nombre d'abonnés (sondé : @oggy, vérifié, 14 M). Les comptes TikTok officiels se trouvent dans les descriptions des vidéos officielles.
- La **heatmap** manque souvent (absente sur Oggy et sur une vidéo récente de Squeezie) ; les vidéos « conçues pour les enfants » ont les
  **commentaires désactivés** : les moments forts des cartoons reposent surtout sur l'analyse locale (niveaux 2 et 3, R43).
- Flux RSS des chaînes : fonctionne (15 dernières vidéos, vues, likes). SponsorBlock : API publique directe, `actionTypes` doit contenir `poi`.
- Paramètres indispensables en mode métadonnées : `skip_download`, `ignore_no_formats_error=True`, **`check_formats=False`** (sinon yt-dlp
  télécharge un morceau pour tester un format), `sleep_interval_requests`. Invité : environ 1 000 requêtes par heure ; le wiki signale un
  risque de blocage du compte Google utilisé avec yt-dlp.

### TikTok et Instagram en lecture publique (sondes)
- `tiktok:user` fonctionne sans cookies avec **curl-cffi et imitation de navigateur globale** (`impersonate` chrome) : vues, likes,
  commentaires, partages, enregistrements par vidéo ; 1 requête + 1 par tranche de 15 vidéos ; chiffres arrondis par TikTok ; vidéos épinglées
  en tête. La page publique `tiktok.com/embed/@compte` donne badge, abonnés et 13 vidéos sans imitation.
- Pas de lecture des pages de hashtag (`tiktok:tag` cassé), pas de recherche TikTok, Creative Center non lisible sans connexion : le panel
  de l'étude de marché se construit à partir des comptes officiels et des @mentions des descriptions ; les hashtags qui marchent sont
  calculés sur les descriptions du panel.
- Instagram : profils publics illisibles sans connexion ; l'export de Meta Business Suite a des en-têtes **traduits dans la langue de
  l'interface** ; aucune colonne de durée moyenne regardée garantie.

### Publication et plateformes
- Instagram, 2026-04-30 : un compte dont une part significative des posts sur 30 jours glissants est du contenu d'autrui perd les
  recommandations ; sous-titres, bordure, filigrane, crédit et changement de vitesse ne suffisent pas ; texte de contexte, mème, voix off,
  réaction comptent. Visible dans le Statut du compte. Application réelle non documentée.
  https://creators.instagram.com/blog/rewarding-original-creators-on-instagram
- TikTok : contenu reproduit ou « combiné de plusieurs sources avec peu d'apport » non éligible au fil Pour toi ni à Creator Rewards ;
  signalé vidéo par vidéo. https://www.tiktok.com/creator-academy/article/tiktok-originality-policy
- Instagram : 5 hashtags au maximum (déc. 2025) ; Reels de 3 min au plus recommandés ; programmation dans l'appli jusqu'à environ 75 jours.
- TikTok : la programmation passe par l'appli TikTok Studio (10 à 30 jours) ; disponibilité sur un compte personnel non confirmée, à tester ;
  sinon brouillons dans l'appli (stockés sur l'appareil).
- Page vers téléphone : HTTPS, `navigator.share({files})` (« Enregistrer la vidéo »), bouton « Copier la légende », lien de téléchargement
  en secours. Fichier : H.264 High 8 bits, yuv420p, 30 i/s constants, AAC 48 kHz, `+faststart`.
- ntfy : `binwiederhier/ntfy` derrière le reverse proxy, `auth-default-access: deny-all`, jeton pour rushes ; sur iPhone,
  `upstream-base-url: https://ntfy.sh` pour des notifications immédiates.

### Code tiers et exploitation
- AI-Youtube-Shorts-Generator : MIT seulement depuis le commit `9c7a33e` (2026-09-10). Les parties réutilisables sans LLM sont petites et
  défectueuses : découpage faux au-delà de 30 min, cache de transcription par nom de fichier, recadrage en MPEG-4 Part 2. Recommandation :
  réimplémenter (à trancher par Louis, touche R40 et la tâche 23).
- Minuteurs : services systemd `oneshot` qui lancent `docker compose run --rm -T` ; `Nice` de systemd ne s'applique pas au conteneur, donc
  limites `cpus`/`mem_limit` dans `compose.yaml` et `nice` au démarrage du conteneur.
- Verrou : `fcntl.flock` sur le volume local, libéré à la mort du processus ; voie express par deux verrous (le rendu cède entre deux posts).
- Sauvegarde : `VACUUM INTO`, `PRAGMA integrity_check` sur la copie, rotation, copie hors du RAID, test de restauration hebdomadaire.
- Mise à jour de yt-dlp : image candidate, tests sans réseau, bascule de tags `current`/`prev` seulement si tout passe ; `uv.lock` reste dans
  le dépôt (le diff est proposé à Louis).
- Secrets : `secrets:` de Compose (fichiers 0400) plutôt que `env_file` (visible dans `docker inspect`) ; yt-dlp réécrit le fichier de
  cookies à la fermeture, donc copie de travail en mémoire à chaque run.
- `INDEX.html` sur le téléphone : serveur statique derrière le reverse proxy existant avec authentification (recommandé) ; Syncthing n'a pas
  de client iOS officiel.

## 2026-11-01 — Rédaction du plan de la phase 2 (choix faits par moi, à relire)

| # | Sujet | Choix | Touche |
| --- | --- | --- | --- |
| P1 | Valeurs recalables | `approval_threshold`, `slots`, `families.*.share` et `layouts.vertical_share` deviennent facultatives. Absentes : rushes part du défaut de la spec et le recale (tâche 37). Écrites : elles l'emportent toujours (forçage). Sinon, une valeur écrite dans la config d'exemple aurait rendu le recalage inopérant (la config l'emporte sur la valeur déduite). La config d'exemple les laisse en commentaire | Configuration, tâches 33, 15, 16b, 34, 37 |
| P2 | Découpage | Tâche 10 → 10a (découverte) et 10b (téléchargement selon le stock) ; tâche 16 → 16a (rotation) et 16b (file, expiration, écarter) | Plan |
| P3 | R19 dans le pipeline | Chaque modèle lourd a sa propre étape, qui traite tous les éléments : `vision`, `sounds`, `transcribe`, `hooks`, `reframe`, `commentary`, `voice`. Les facettes d'analyse d'un épisode sont suivies séparément. Deux étapes peuvent charger le même modèle l'une après l'autre : R19 se lit « une fois par étape, jamais par élément » (L23) | Analyse, rendu |
| P4 | R15 | Un épisode communautaire n'est jamais découpé ni rendu. Ses passages retrouvés dans un épisode officiel (pHash à 2 i/s, précision 0,5 s) deviennent des signaux de niveau 1 du best-of officiel. Créer une partie concurrente sur l'épisode officiel violerait R2, puisque ses propres parties couvrent déjà ce passage | Tâches 10b, 13, 15 |
| P5 | R34 | Un passage d'épisode à logo incrusté passe en `rejected`, pas en `needs_review`, car aucune revue ne peut le débloquer | Tâche 15 |
| P6 | Compilations officielles | Redécoupées en épisodes enfants : même fichier source, portion propre, même propriétaire que le parent (unicité d'une vidéo source), `sha256` = `<sha parent>:<k>` comme clé d'idempotence. Parent au statut `split` | Tâche 11 |
| P7 | Mise en scène (module avancé) | ~~Image figée d'une seconde juste avant la chute~~ : abandonnée par Louis, remplacée par la carte de fin « Abonne-toi » (L10) | Tâche 35 |
| P8 | Rotation générée | Périodes de 21 jours ancrées au lundi du lancement (L6, remplace l'ancre fixe du 2026-11-16) ; série j → marque (j + période) mod nombre de marques. Avec 2 séries et 2 marques, on retrouve exactement l'exemple de la spec | Tâche 16a |
| P9 | File | Les posts `ready` gardent leur créneau (Louis les a peut-être déjà programmés) ; seuls les posts non empaquetés sont replacés. La « performance apprise » vaut 0,5 jusqu'à la tâche 18 | Tâche 16b |
| P10 | Score d'approbation | Moyenne pondérée de cinq composantes, chacune entre 0 et 1 : bornes, action, son, début, niveau de signal. Calibrage sur les épisodes réels : part approuvée entre 50 et 90 %. Seuls les poids par défaut s'ajustent, jamais une règle | Tâche 15 |
| P11 | Téléchargement | Piloté par le stock : un compte sous 14 jours de stock déclenche le téléchargement des meilleures vidéos officielles de ses séries, 6 par run au plus. Le budget `showcase` ne s'applique qu'aux candidats de la prospection (tâche 38), pas aux séries de la config, même sans licence | Tâche 10b |

## 2026-11-01 — Rédaction du plan de la phase 3 (choix faits par moi, à relire)

| # | Sujet | Choix | Touche |
| --- | --- | --- | --- |
| P12 | Migrations | Numérotées dans l'ordre d'exécution des tâches (la chaîne Alembic est linéaire) : 0008 tâche 36, 0009 tâche 17, 0010 tâche 18, 0011 tâche 20, 0012 tâche 25, 0013 tâche 26, 0014 tâche 27, 0015 tâche 31 | `00-master.md` §5.10 |
| P13 | « Accroche 2/3 » | `serve` ne rend rien (léger, sans FFmpeg) : il enregistre le choix et dépose une demande dans `data/requests/refresh/` ; une unité systemd `.path` lance aussitôt `rushes refresh` (rendu, métadonnées et paquet de ces seuls posts). La page affiche « Nouveau rendu en cours » et bloque le téléchargement jusqu'au nouveau paquet | Tâches 36, 20 |
| P14 | Actions de la page | Idempotentes (deuxième clic sans effet) ; conflit (écarter un post publié) refusé avec un message | Tâche 36 |
| P15 | Détection | Un post `expired` reconnu sur le profil public repasse `published` et reprend son passage si le registre l'accepte (Louis l'avait peut-être programmé avant l'expiration) ; une vidéo publique sans post est listée dans `status` et le rapport, jamais ignorée ; légende comparée sur ses 40 premiers caractères (similarité ≥ 90) | Tâche 17 |
| P16 | Résultat d'un post | Jugé une seule fois, 72 h après publication, contre la médiane du compte sur 60 jours (au moins 5 posts) ; succès = rétention à 3 s au-dessus de la médiane si disponible, sinon vues au-dessus de la médiane ; écarté = échec (R37) ; expiré = non compté | Tâche 18 |
| P17 | Bandit des mises en page | Seulement entre `square` et `vertical` (`portrait` et `full` sont des replis, pas des choix) ; ses bras fixent chaque semaine la part de vertical, entre 5 et 50 % | Tâches 18, 37 |
| P18 | Exploration | 10 % des accroches de tête prises au hasard parmi les types ; 25 % des créneaux décalés de 1 ou 2 h, pour que le recalage voie d'autres heures | Tâches 18, 37 |
| P19 | Recalage | Créneaux : meilleure heure voisine si elle fait au moins 10 % de mieux (après 3 semaines et 15 posts) ; part `long` : 10 % en exploration, puis selon son ratio, entre 5 et 30 % ; seuil d'approbation : piloté par le stock (10 à 21 jours visés, bornes 0,5–0,9) ; une valeur forcée par la config est quand même calculée et notée dans le rapport | Tâche 37 |
| P20 | Exploitation | Profils `rushes run --profile night/watch/stats/weekly/maintenance/refresh` ; sauvegarde chaque nuit (spec : « sauvegarde nocturne ») et vérification chaque lundi ; alertes émises par les étapes (`Message.alert`), envoyées et dédoublonnées sur 24 h en fin de commande, sans jamais faire échouer une commande | Tâche 20 |
| P21 | Image de test | Cible Docker `test` (dépendances de dev et `tests/`) : `pytest` ne tourne jamais dans l'image de production ; le script de mise à jour de yt-dlp (R38) teste l'image candidate par cette cible | Tâches 9, 20 |
| P22 | Porte de la phase 3 | Les unités systemd et ntfy sont installés provisoirement à la tâche 20 (la spec exige 3 jours de minuteurs actifs avant la phase 4) ; la tâche 39 refait l'installation de façon reproductible | Tâches 20, 39 |
| P23 | Secrets | Convention `<VARIABLE>_FILE` pour lire un secret Compose (fichier 0400) plutôt qu'une variable d'environnement | Tâche 20, §5.13 |

## 2026-11-01 — Rédaction du plan de la phase 4 (choix faits par moi, à relire)

| # | Sujet | Choix | Touche |
| --- | --- | --- | --- |
| P24 | Voie express | Propose tout de suite le best-of provisoire et les parties 1 et 2 (seuls posts publiables aussitôt, les parties sortant dans l'ordre) ; les autres parties suivent la nuit. Second passage à 24 h et 72 h : best-of recalculé et remplacé s'il n'est pas encore empaqueté (ancien post retiré sans échec de bandit) ; les parties ne sont jamais redécoupées (R5 évaluée sur les pics connus au découpage) | Tâche 28 |
| P25 | Créateurs suivis | Tous les créateurs de la config, autorisés ou non (vitrine, comme P11 pour les séries), passent par le catalogue et la veille ; seuls les prospectés consomment le budget `showcase` | Tâches 27, 28 |
| P26 | Chaîne d'un créateur | `youtube_channel` devient facultatif (amorce) ; sans valeur, rushes la trouve par la recherche de chaînes (autonomie) | Tâche 27 |
| P27 | Mise en page des créateurs | Même règle que les cartoons (R8 : carré par défaut, vertical en test, part recalée par le bandit). Le tableau de la spec (« Recadrage 9:16 ») est lu à la lumière de R8 et R26 ; confirmé par Louis le 2026-11-02 (L1) | Tâche 25 |
| P28 | SponsorBlock et autocut | Segments SponsorBlock retirés de `clean.mp4`, y compris au milieu (créateurs seulement), correspondance source ↔ clean par morceaux ; autocut au rendu ; durées des familles vérifiées après autocut | Tâche 24 |
| P29 | Accroche citation | Citation de 3 à 7 mots, prise dans les 60 premiers pour cent du passage, placée en tête de l'ordre a priori ; le bandit décide ensuite | Tâche 22 |
| P30 | Crédit | Toujours le nom du créateur (L19, remplace la mention de son compte) ; son compte Instagram sert seulement à l'identifier ; R23 prime sur la longueur visée de 150 caractères ; campagne modifiée → posts non publiés refaits | Tâche 26 |
| P31 | Langue d'une vidéo | Transcription d'au moins 50 mots dont la langue devinée diffère de la marque → aucun passage, avertissement (R21) | Tâche 24 |
| P32 | Prospection | Score de dynamique (vues/jour récentes contre médiane, rythme, croissance des abonnés) ; langue devinée sur les titres ; un YAML créé pour un prospect rattache ses lignes par la chaîne | Tâche 27 |
| P33 | Migrations | 0015 pour la tâche 28, l'étude de marché passe à 0016 (complète P12) | `00-master.md` §5.10 |
| P34 | Correction de la phase 1 | Accroche placée sous le bandeau (`Rect(left, 460, …, 200)`) : la position 1 520 px violait la marge basse de 480 px (R9) testée par la même tâche ; le bas de l'écran va aux sous-titres | Tâches 7b, 25 |
| P35 | Soumission des campagnes | Bouton « Soumis » sur la page de `serve` (L16) ; `rushes campaign submitted <post>` reste en secours | Tâche 26 |

## 2026-11-02 — Rédaction du plan de la phase 5 (choix faits par moi, à relire)

| # | Sujet | Choix | Touche |
| --- | --- | --- | --- |
| P36 | Panel TikTok | Semences : comptes officiels (descriptions YouTube), amorces de la config, compte du créateur ; élargi par les @mentions ; chaque candidat vérifié par la page publique `embed` (public, au moins 10 000 abonnés, au moins 30 % de descriptions qui citent le sujet, sauf comptes officiels) ; 10 à 30 comptes | Tâche 31 |
| P37 | Hashtags | Réserve = 8 hashtags à écart de vues positif (vus dans au moins 10 vidéos du panel), hors obligatoires, interdits, `#fyp` et hashtag du sujet ; retiré si nos posts qui l'utilisent (au moins 5) font moins que la médiane ; hashtag nostalgie d'Instagram choisi dans une liste fixe d'après le panel TikTok (Instagram illisible sans connexion) ; données maigres ou collecte en panne : la dernière valeur reste | Tâche 31 |
| P38 | Compilations thématiques | Score = pertinence CLIP × (0,5 + 0,5 × popularité) × gag complet ; compte à rebours « #N » → « #1 » (le plus fort en dernier) ; un thème revient sur un compte après 14 jours ; suggestions écrites dans `data/proposals/themes/` avec `active: false`, à copier dans `config/themes/` | Tâche 30 |
| P39 | Revue | Page construite depuis `web/` (shadcn, Tailwind) en un seul fichier, ouverte hors ligne ; décisions exportées en JSON ; `review apply` refait les contrôles : Louis peut approuver sous le seuil de score, jamais contre une règle (R1, R2, R5, R6, R24, R15, R34) ; élément modifié depuis la page sauté | Tâche 32 |
| P40 | Séries candidates | Noms extraits des titres de la semaine, des titres de nos vidéos retenues et des hashtags du panel ; validées seulement par une chaîne officielle ; 3 nouvelles par semaine au plus ; audio provisoire `dialogue_en`, puis `nonverbal` si peu de mots, abandon si le dialogue n'est pas anglais | Tâche 38 |
| P41 | Tableau des licences | Priorité = 0,5 abonnés de la chaîne officielle + 0,3 vues par jour des vidéos populaires + 0,2 mentions (log normalisés) ; alerte `unlicensed_top` à partir de 0,8 ; export CSV | Tâche 38 |

## 2026-11-02 — Rédaction du plan de la phase 6 (choix faits par moi, à relire)

| # | Sujet | Choix | Touche |
| --- | --- | --- | --- |
| P42 | Déploiement | `scripts/deploy.sh` : image candidate testée sans réseau, minuteurs arrêtés, sauvegarde `pre-deploy`, promotion, migration, `models fetch`, `doctor` ; tout échec après la promotion remet l'image précédente **et** la base d'avant le déploiement, relance les minuteurs et envoie `deploy_failed`. Le serveur ne fait que `git pull` | Tâche 39 |
| P43 | `rushes doctor` | Onze contrôles indépendants (config, révision, modèles, secrets, disque local pour `flock`, sauvegarde hors RAID sur un autre disque, espace, verrou, FFmpeg, runtime yt-dlp, fraîcheur de la sauvegarde), chacun avec sa piste | Tâche 39 |
| P44 | GoDoxy et ntfy | Étiquettes de départ `proxy.idle_timeout: 15m`, `proxy.wake_timeout: 30s`, `proxy.stop_method: stop`, `proxy.middlewares.oidc` (utilisateur de Louis), à confirmer dans la doc de la version installée (docs.godoxy.dev, Idle Sleep). URL publique de ntfy par `NTFY_BASE_URL` plutôt qu'écrite dans `server.yml` | Tâche 39 |
| P45 | Bascule des licences | `is_test` était figé à l'attribution : une licence obtenue laissait la vitrine dans `outbox_test/`, une licence retirée laissait des posts dans `outbox/`. Étape `licence_sync` (tête de `RUN_ORDER` et du profil `watch`, et `rushes rights sync`) : posts non publiés dont le statut change remis en file et réempaquetés au bon endroit ; licence retirée sur un post prêt → alerte `licence_revoked` seulement, le post reste dans `outbox/` (L9) | Tâche 40a, R14, R22, R32 |
| P46 | Audit des règles | `rushes audit` chaque nuit : registre (R1, R2, R27, R44), approbation (R3), fichiers (R6, R7), légendes et hashtags (R12, R13), dossiers de `outbox/` (R14, R22, R32), traçabilité (R17), crédit (R23), expiration (R35) ; violation → alerte `rule_violation` de priorité 5. `pair_verdict` partagé avec `can_assign` | Tâche 40a |
| P47 | Runs | Migration 0017 : origine du run (`timer`, `path`, `manual`) et mémoire de pointe (cgroup v2 `memory.peak`) ; une ligne `running` orpheline est close « interrompu » au run suivant | Tâche 40a |
| P48 | Répétition | Commence un lundi ; calendrier d'exercices (arrêt brutal, voie express, ntfy coupé, bascule de licence, retour arrière, redémarrage) ; bilan calculé par `rushes rehearsal report` ; tout échec → correctif et nouvelle semaine complète | Tâche 40b |
| P49 | Lancement et rodage | `rushes launch start` enregistre la date (`ops.launched_at`) ; critères du rodage calculés par compte (`rushes burnin status`), 21 jours au moins. Le critère de détection mesure le rattachement de 9 des 10 premiers posts publiés à leur vidéo publique (TikTok sous 48 h, Instagram au premier export importé), quelle que soit l'origine du statut « publié » | Tâche 41 |

## 2026-11-02 — Réponses de Louis sur les phases 4 à 6

| # | Question | Réponse de Louis | Touche |
| --- | --- | --- | --- |
| L1 | P27 : mise en page des créateurs | Plutôt carré, avec du vertical de temps en temps : même règle R8 que les cartoons (part de vertical recalée par le bandit). La ligne « Cadrage » du tableau des verticales de la spec est mise à jour en ce sens | Tâche 25, R8, R26, spec |
| L2 | P49 : clic « Publié » pendant le rodage | Louis clique « Publié » la plupart du temps ; la détection sert de secours. Le rodage mesure donc le rattachement de chaque post à sa vidéo publique, pas l'origine du clic | Tâche 41 |

## 2026-11-02 — Relecture à froid du plan complet (étape 6) : points tranchés par moi

Un agent neuf a relu la spec, les décisions et les sept fichiers du plan : 26 remarques. Corrigées dans les plans :

| # | Remarque | Correction |
| --- | --- | --- |
| C1 | `test_rules_coverage` refusait une règle à la fois testée et en attente, alors que plusieurs tâches couvrent une règle en partie | Une règle en partie couverte reste en attente jusqu'à la tâche qui la termine ; seul « ni testée ni en attente » échoue |
| C2 | `rushes serve` ne pouvait pas réutiliser `build_upload` sans importer `rushes.models` | `Word` et `SoundEvent` dans `rushes/core/types.py` (léger) ; `build_upload(config, paths, session, post_id)` sans `Context` |
| C3 | `creators.channel_id` NOT NULL alors que `youtube_channel` devient facultatif (P26) | Colonne rendue nullable par la migration 0014 ; `sync` écrit l'identifiant valide, sinon la chaîne résolue, sinon NULL |
| C4 | pHash : la spec dit « à moins du seuil » | Lu « à une distance au plus égale au seuil » (`<= phash_max`, 10 refusé, 11 accepté), comme le test de la tâche 3 |
| C5 | Traçabilité R17 impossible pour les parties (pas de niveau de signal) et indéfinie pour best-of et compilations | `signal_level` exigé pour le best-of seulement ; `PostTrace.items` trace chaque passage |
| C6 | Noms des secrets ntfy | `NTFY_TOPIC_FILE`, `NTFY_TOKEN_FILE` (tirés de `topic_env`/`token_env` + `_FILE`) |
| C7 | Détection tardive d'un post expiré dont le passage a été réattribué : deux posts du même passage possibles (R2) | Le nouveau post non publié est écarté (origine `system`, jamais compté comme échec par le bandit) et l'ancien reprend l'attribution ; s'ils sont publiés tous deux : alerte `rule_violation` |
| C8 | Parties tirées dans la famille `long` | Parties toujours `monetizable` : la spec réserve `long` au best-of et aux compilations ; le recalage de la part `long` s'applique à leur tirage |
| C9 | « Part des familles respectée » et « hashtags en hausse » absents de la file | Bonus de déficit de famille sur 14 jours (16b) ; tendance complétée par la part des hashtags de la réserve (31) |
| C10 | Export Instagram exigé à la tâche 17 avant toute publication | Un export vide suffit ; à défaut, fixtures bâties sur les en-têtes documentés, confirmées au premier export du rodage |
| C11 | Petits manques | `head_revision` (20), `rekey_subject` (27), `FILTERABLE_STEPS` (22), statuts de `compilations` contraints en 0004, routes de `serve` alignées, `Paths.imports` = `data/import/`, express « priorité basse » = `nice` du conteneur, tests 22 et 26 qui insèrent eux-mêmes les valeurs déduites de la tâche 27 |

R2 « deux posts simples » : aucun type de post à passage unique n'existe en dehors des parties ; si un jour il en existe, il relève du même contrôle que deux parties (`same_account_part`).

## 2026-11-02 — Réponses de Louis après la relecture à froid

| # | Question | Réponse de Louis | Touche |
| --- | --- | --- | --- |
| L3 | Libellé des parties pour une marque francophone | « Partie N » (carte de fin « Partie N+1 ensuite ») ; les marques anglaises gardent « Part N ». Aucun total, comme avant | Spec (Numérotation), tâche 7b |
| L4 | Montée du rythme, du lancement à la cible | Louis la fait lui-même dans `posts_per_day` ; rushes ne la change jamais et signale dans le rapport hebdomadaire un compte qui pourrait monter d'un palier (stock au-dessus de 14 jours depuis 14 jours, vues stables, aucune chute ouverte) | Spec (Volumes), tâche 37 |
| L5 | Exécution du plan | Pas tout de suite ; le moment venu, par sous-agents (`superpowers:subagent-driven-development`) | Plan |

## 2026-11-02 — Réponses de Louis sur mes choix P1 à P49

| # | Question | Réponse de Louis | Touche |
| --- | --- | --- | --- |
| L6 | P8 : ancre de la rotation | Périodes de 21 jours ancrées au lancement, pas à une date fixe. Avant le lancement, ancre provisoire au lundi du premier run (`rotation.epoch`, source `provisional`) ; `rushes launch start` la recale une fois sur le lundi du lancement. Le stock déjà produit garde son propriétaire (R4, R44) : seuls les épisodes ingérés ensuite suivent la nouvelle ancre | Spec (Comptes et rotation), tâches 16a, 41 |
| L7 | P24 : voie express | Confirmé : best-of provisoire + parties 1 et 2 tout de suite | Tâche 28 |
| L8 | P16 : jugement d'un post | Confirmé : une seule fois, à 72 h | Tâche 18 |
| L9 | P45 : licence retirée sur un post prêt | Alerte seulement : le post reste dans `outbox/`, Louis l'écarte ou le publie sous sa responsabilité ; rappel quotidien tant qu'il reste prêt. Les posts pas encore empaquetés partent en `outbox_test/`. L'audit R14 compare le dossier au statut au moment de l'empaquetage, donc pas de violation | Tâche 40a |
| L10 | P7 : mise en scène | Pas d'image figée. La carte de fin de chaque post montre la photo de profil de la marque (`config/avatars/`, fournie par Louis), le @ du compte et « Follow » / « Abonne-toi », au-dessus de « Part N+1 next » pour une partie ; aucune seconde ajoutée. Le module avancé se réduit à la voix off et à ses sous-titres | Spec (Numérotation, Originalité), tâches 7b, 35 |
| L11 | P48 : échec pendant la répétition | Confirmé : correctif, puis nouvelle semaine complète | Tâche 40b |
| L12 | P42 : échec du déploiement | Confirmé : image et base restaurées automatiquement | Tâche 39 |
| L13 | P46 : violation trouvée par l'audit | Confirmé : alerte seulement, rien n'est touché | Tâche 40a |
| L14 | P13, P11, P38 | Confirmés : nouveau rendu aussitôt après « Accroche 2/3 » ; 6 vidéos téléchargées par run sous 14 jours de stock ; compilations en compte à rebours #N → #1 | Tâches 36, 10b, 30 |

## 2026-11-02 — Réponses de Louis sur mes autres choix (questionnaire en 4 tours)

| # | Question | Réponse de Louis | Touche |
| --- | --- | --- | --- |
| L15 | P10 : part approuvée visée au calibrage | Confirmé : entre 50 et 90 % | Tâche 15 |
| L16 | P35 : soumission d'un post à une campagne | Bouton « Soumis » sur la page de `serve`, dans une section « À soumettre » par compte (posts publiés non soumis) ; la commande CLI reste en secours. La page écrit donc quatre décisions | Spec (Exploitation, `serve`), tâche 26 |
| L17 | P9, P15, P6 | Confirmés : un post prêt garde son créneau ; un post expiré retrouvé publié repasse `published` ; les compilations officielles sont redécoupées en épisodes | Tâches 16b, 17, 11 |
| L18 | P17, P18, P19 | Confirmés : part de vertical entre 5 et 50 % ; exploration de 10 % des accroches et 25 % des créneaux ; créneau déplacé si l'heure voisine fait au moins 10 % de mieux après 3 semaines et 15 posts | Tâches 18, 37 |
| L19 | P30 : crédit d'un créateur | Toujours son nom (`credit_template: "🎥 {name}"`), jamais son @ dans la légende. Son compte Instagram, s'il est connu, sert seulement à l'identifier sur le post, comme l'exige la spec. Les mentions exigées par une campagne restent obligatoires (R23) | Spec (Titres, légendes), tâche 26 |
| L20 | P28, P29, P25 | Confirmés : segments SponsorBlock retirés partout ; citation de 3 à 7 mots dans les 60 premiers pour cent, placée en tête ; créateurs non autorisés suivis en vitrine | Tâches 24, 22, 27, 28 |
| L21 | P36, P37, P40, P44 | Confirmés : panel de 10 à 30 comptes d'au moins 10 000 abonnés ; hashtag retiré quand nos posts qui l'utilisent font moins que la médiane ; 3 séries candidates par semaine au plus ; `serve` en veille après 15 minutes | Tâches 31, 38, 39 |
| L22 | P39, P41, P20, P32 | Confirmés : revue qui peut approuver sous le seuil, jamais contre une règle ; alerte `unlicensed_top` à partir de 0,8 ; sauvegarde chaque nuit, vérifiée le lundi ; prospection sur la dynamique récente | Tâches 32, 38, 20, 27 |

Restent tels quels, sans question, parce qu'ils sont techniques ou imposés par une règle : P1 (la config l'emporte, règle d'autonomie), P2, P3 (R19), P4 (R15, R2), P5 (R34), P12, P14, P21, P22, P23, P26 (autonomie), P31 (R21), P33, P34 (R9), P43, P47.

## 2026-11-02 — Relecture à froid n°2 : réponses de Louis

Quatre relecteurs neufs ont relu la spec, ces décisions et les sept plans : phases 1-2, 3-4, 5-6, et les jonctions
entre tâches. Ils ont fait 138 constats. Les corrections techniques sont appliquées dans les plans ; les arbitrages
du coordinateur sont listés dans la section suivante. Les six points qui changent le produit ont été posés à Louis :

| # | Question | Réponse de Louis | Touche |
| --- | --- | --- | --- |
| L23 | R19 contre l'ordre du pipeline (le modèle de langage sert aux accroches puis au commentaire, Whisper à la transcription puis à la voix) | R19 précisée : « chaque modèle est chargé une fois par étape, jamais par élément ; un seul modèle lourd en mémoire à la fois ». Le pipeline ne change pas | Spec (R19), P3, master §5.5, tâches 5, 35 |
| L24 | Posts de vitrine expirés (jamais publiés) | Comportement actuel conservé : expirés puis réattribués et rendus de nouveau | Tâche 16b |
| L25 | Compilation thématique avec plusieurs séries ou créateurs | Non : un seul sujet par compilation (thème × sujet × compte). Nom, `#série`, crédit et statut de test sont ceux de ce sujet | Spec (Compilations), tâche 30 |
| L26 | Alimentation de Peak Moments, qui n'a aucun créateur dans la config | Louis demande d'abord une étude de marché : ce qui marche dans les best-of TikTok et Instagram de créateurs anglophones. Étude faite (`docs/research/2026-11-peak-moments-etude-marche.md`) ; choix en L30 et L31 | Config, tâche 40b |
| L27 | Nouvelle vidéo de créateur alors que la semaine est pleine | Créneau en plus : la voie express obtient un créneau supplémentaire le jour même ou le lendemain, au-delà du rythme, au plus un post de plus par jour et par compte. Les posts prêts ne bougent pas | Tâches 16b, 28 |
| L28 | Arrivée de l'export CSV Instagram sur le serveur | Dépôt sur la page de `serve` : zone « Déposer l'export Instagram », fichier `.csv` seulement, 5 Mo au plus, écrit dans `data/inbox/`. La page écrit donc cinq décisions | Spec (Exploitation, `serve`), tâches 17, 36 |

## 2026-11-02 — Relecture à froid n°2 : arbitrages techniques (coordinateur)

Ces choix ne changent pas le produit ; quand deux relecteurs proposaient deux corrections différentes, j'ai tranché
ainsi :

| # | Sujet | Choix | Touche |
| --- | --- | --- | --- |
| C12 | Valeurs fictives de la config (`UC...`, `@...`, fichier `refs/` absent) | `ConfigError` au chargement, qui nomme la clé et le fichier ; `tom_and_jerry.yaml` reste minimal, l'exemple complet de la spec y est en commentaire | Tâche 33 |
| C13 | Voie express, `refresh` et verrous | La commande tient `express_intent` puis le verrou lourd et le passe à `run_pipeline(..., lock=hl, yield_to_express=False)` ; `refresh` suit le même schéma et ne retire ses demandes qu'une fois le verrou obtenu ; l'unité systemd lance `rushes refresh` (plus de profil `refresh`) | Tâches 2, 20, 28, 36 |
| C14 | Codes de sortie 4 et 5 sous systemd | `SuccessExitStatus=4 5` partout : seules les vraies pannes déclenchent `timer_failed` ; la répétition ne compte comme échec que `runs.status='failed'` | Tâches 20, 39, 40b |
| C15 | Écarts et bandit | `discard_post` journalise lui-même l'écart ; un écart d'origine `system` ne rejette pas le passage et n'est jamais un échec du bandit ; les posts de test sont ignorés par le bandit ; le rodage ne compte que les résultats postérieurs à `ops.launched_at` | Tâches 16b, 17, 18, 36, 41 |
| C16 | Audit R14 et R35 | R14 : faute seulement pour un dossier de post de test ou inconnu dans `outbox/` (publiés en rétention, `_ecartes/` et `INDEX.html` admis) ; R35 : marge de 6 h au-delà de `expiry_days` | Tâche 40a |
| C17 | Créneaux | Indexés par jour local absolu, stables d'un run à l'autre ; quota compté par jour ; défauts par fuseau (Paris : TikTok 18:00 et 20:00, Instagram 19:00) | Tâches 8b, 16b, 33 |
| C18 | Compilations à plusieurs sources | `render_plate_multi` prend une liste (source, passage), chaque élément mis à l'échelle avant le fondu ; un recadrage par élément ; graine des thèmes `derive_seed(theme, compte, sujet, date, "theme")` | Tâches 13, 30, 34 |
| C19 | Famille d'une compilation thématique | Celle du YAML du thème (défaut `monetizable`), sans tirage ; le tirage par `families.share` ne concerne que les best-of | Tâches 13, 30, 37 |
| C20 | Cookies YouTube (R18) | Seuls la découverte YouTube et le téléchargement (L32) peuvent passer en secours avec cookies ; veille, catalogue, prospection et extractions de la voie express jamais | Tâches 10a, 10b, 27, 28 |
| C21 | Heure de publication | Au rapprochement, `published_at` prend l'heure réelle de la plateforme (le clic reste journalisé) ; le recalage des créneaux n'utilise que les posts rapprochés | Tâches 17, 37 |
| C22 | Santé des collecteurs | Une mise à jour par run et par collecteur ; compteurs distincts `tiktok`, `tiktok_market`, `tiktok_embed` | Tâches 17, 31, 41 |
| C23 | Étude de marché TikTok (spec étape 0) | Les mots-clés des vidéos qui surperforment (`market.keywords`) enrichissent la prospection YouTube ; les comptes TikTok eux-mêmes ne sont pas prospectés (rushes a besoin d'une source YouTube). P41 : les mentions suivent une échelle linéaire (`max_mentions` = maximum du run) | Tâches 27, 31, 38 |
| C24 | Déploiement et restauration | Retour arrière : sauvegarde `pre-rollback`, la base n'est restaurée que si `prev` ne connaît pas sa révision ; restauration avec `wal_checkpoint(TRUNCATE)` et `serve` arrêté ; détection de la première installation ; refus de redescendre yt-dlp sans `--allow-downgrade` ; script hôte `scripts/rushes` | Tâche 39 |
| C25 | Dépôt de l'export Instagram (L28) | Corps brut avec l'en-tête `X-Filename`, sans multipart ; idempotent par empreinte | Tâche 36 |

| # | Question | Réponse de Louis | Touche |
| --- | --- | --- | --- |
| L29 | Format court (20–45 s) suggéré par l'étude de marché pour les clips de créateurs anglophones | Non : toutes les familles restent au-dessus d'une minute (Creator Rewards) ; le bandit et le recalage jugeront sur les vrais chiffres | Spec (Familles de durée) |
| L30 | Critère de choix des créateurs de Peak Moments | Les campagnes de clipping n'entrent pas en compte : Louis gère lui-même les licences. Le choix se fait sur la performance des clips (étude `docs/research/2026-11-peak-moments-etude-marche.md`) | L26, config |
| L31 | Créateurs de Peak Moments (L26) | Beta Squad, CaseOh et Dude Perfect, les trois premiers du classement de l'étude (vues, croissance, densité de pics, faible saturation, image de marque). Non autorisés au départ : vitrine jusqu'à l'accord de Louis | Config (tâche 33), tâche 40b |

## 2026-11-02 — Relecture des derniers choix par Louis

| # | Question | Réponse de Louis | Touche |
| --- | --- | --- | --- |
| L32 | Téléchargement bloqué par YouTube (« confirmez que vous n'êtes pas un robot ») | R18 étendue : le téléchargement des vidéos retenues (6 au plus par run) peut passer en secours avec les cookies, avec alerte, comme la découverte | Spec (R18), tâches 10b, 28 |
| L33 | Alerte de chute des vues | Réglage prudent : médiane des 21 derniers jours contre les 28 précédents, au moins 5 posts de chaque côté | Tâche 37 |
| L34 | Confirmés | Posts prêts intouchables (seuls accroche 2/3, numéro, licence ou campagne les refont) ; passage « à revoir » s'il a moins de 3 accroches conformes ; prospection TikTok limitée aux mots-clés ; créneaux par défaut (New York : TikTok 15:00 et 18:00, Instagram 17:00 ; Paris : 18:00 et 20:00, 19:00) ; retour arrière qui garde la base quand la structure n'a pas changé (précise L12) ; vitrine placée après les posts publiables | Tâches 7a, 14, 16b, 31, 33, 39 |
| L35 | Charte, polices, nom | Couleurs relevées sur les avatars (fond du bandeau, texte, accent, barre) : Saturday Toons #3B1F0E, #FFF4DC, #FFB627, #FF6B35 ; Toon Tape #1E1B2E, #FFFFFF, #FF4FA3, #FFD23F ; Instant Culte #121316, #F5F1E8, #E63946, #E63946 ; Peak Moments #0E1A2B, #F5F1E8, #F2A541, #F2A541. Polices : Luckiest Guy (cartoons, Apache 2.0) et Bebas Neue (créateurs, OFL). Nom du paquet : rushes | Tâches 7b, 33 |
| L36 | Gabarits génériques d'accroches | Rédigés à la tâche 14 (≥ 8 par type et par langue), relus et corrigés par Louis avant le lancement | Tâche 14, checklist de lancement |
| L37 | Export Instagram réel | Louis fournit un export Meta Business Suite avant la tâche 17 ; le repli sur les en-têtes documentés (C10) ne sert que si Meta n'en produit aucun | Tâche 17 |
| L38 | Choix techniques C12 à C25 | Validés en bloc ; famille des compilations thématiques fixée par leur YAML (C19) confirmée | Toutes phases |
| L39 | « Accroche 2/3 » pendant un run | Le run cède la main entre deux posts ; une demande qui attend plus de 2 h est abandonnée avec une alerte et refaite au run suivant | Tâches 20, 36 |

## 2026-11-04 — Exécution de la tâche 33 : arbitrages du coordinateur

Écarts constatés pendant l'implémentation et relecture à froid ; ils ne changent pas le produit.

| # | Sujet | Choix | Touche |
| --- | --- | --- | --- |
| C26 | Statut des droits | La config est la seule source de `rights.status` (« repris de la config », spec) : un statut modifié seulement en base est écrasé à la synchro suivante ; `priority` et `showcase_posts` ne le sont jamais. Aucune tâche n'écrit ce statut en base | Tâche 33, 38 |
| C27 | `TIKTOK_HANDLE` | Resserré à la source : `^@(?=[\w.]*\w)[\w.]+$` (un caractère de mot au moins), pour que `@...` soit refusé partout, y compris par la tâche 27 | Tâches 33, 27 |
| C28 | Test R48 | « postiz » n'est toléré que dans `db/migrations/versions/` (colonnes de 0001) et sur les lignes de clés héritées de `config.py` (message D1, table des clés) ; les marqueurs réseau s'appliquent partout ; trois auto-contrôles prouvent que le balayage voit un client ajouté | Tâche 33 |
| C29 | Migration 0002 | `downgrade()` lève `NotImplementedError` (les colonnes Postiz supprimées ne reviennent pas) ; le retour arrière se fait par restauration de la sauvegarde (C24). `rotation` reste active dans l'`accounts.yaml` d'exemple | Tâche 33 |
| C30 | Liste de hashtags obligatoires vide | La spec est muette ; une liste vide désactiverait R13 sans bruit (faute de frappe) : refusée au chargement (« au moins un hashtag obligatoire est requis (R13) », à `required_hashtags.<verticale>.<plateforme>`). Réversible si Louis veut une plateforme sans hashtag obligatoire | Tâche 33 |

## 2026-11-xx — Essai Python 3.15 sur le serveur

> **Remplacé par la décision du 2026-11-05** (« Repli en Python 3.14 (décision de Louis) », en fin de fichier). Entrée conservée comme trace datée de l'essai en 3.15 ; ses résultats serveur sont l'historique « Essai 3.15 ».

Statut : **partiel**. La partie Mac (verrouillage, imports, script) est faite le 2026-11-04 ; la construction de l'image et le lancement sur le serveur restent à faire par Louis (tâche 42, étapes 5 et 6). Aucun repli en 3.14 n'est envisagé sans son accord.

### Résultat sur le Mac (macOS arm64, Python 3.15.2)

- `uv add` (commande de l'étape 1) : résolution et installation sans erreur, `uv lock` puis `uv sync` verts (100 paquets, 99 après la correction ci-dessous). L'override `onnxruntime; python_version < '3.15'` fonctionne : `uv tree | grep onnxruntime` ne renvoie rien.
- **Un seul fournisseur de `cv2`** : `scenedetect-headless` exige `opencv-python-headless`, qui écrase les fichiers de `opencv-contrib-python-headless` dans `cv2/` (désinstaller l'un casse l'autre : après suppression, `cv2` perdait ses constantes). Second override `"opencv-python-headless; python_version < '3.15'"` dans `[tool.uv]` : seul `opencv-contrib-python-headless` reste (lock à 99 paquets, `uv tree | grep opencv-python-headless` vide, `scenedetect` s'importe et `AdaptiveDetector` aussi). Le spike `yunet` vérifie `hasattr(cv2, "saliency")` (module contrib d'OpenCV 5, vérifié sur le Mac). Un venv existant doit être resynchronisé avec `uv sync --reinstall-package opencv-contrib-python-headless` ; une image neuve n'est pas concernée.
- Import de toute la pile (`import torch, open_clip, faster_whisper, sherpa_onnx, llama_cpp, cv2, scenedetect, silero_vad, imagehash, scipy, PIL, fontTools, jinja2, rapidfuzz, yt_dlp, curl_cffi`) : `ok`.
- Wheels et sources (lues dans `uv.lock`) : tous les paquets lourds ont un wheel cp315 ou abi3 pour linux x86_64 et macOS arm64 (torch 2.15.0 : `+cpu` depuis l'index PyTorch sous Linux, wheel PyPI sous macOS ; sherpa-onnx 2.0.1 ; opencv 5.0.0.93 ; ctranslate2 4.8.5 ; curl-cffi 0.17.0). **Sans wheel, donc compilés à l'installation : `llama-cpp-python` 0.3.36 (cmake, compilé sans erreur sur le Mac) et `pyyaml` 6.0.3 (sdist, repli pur Python).** Pas de paquet `nvidia-*` ni `triton` dans le verrou.
- Versions résolues : numpy 2.5.4, scipy 1.18.1, pillow 13.0.0, fonttools 4.68.0, imagehash 4.3.2, jinja2 3.1.6, rapidfuzz 3.14.6, opencv-contrib-python-headless 5.0.0.93, scenedetect-headless 0.7.2, torch 2.15.0, open-clip-torch 3.3.0, silero-vad 6.2.3, faster-whisper 1.2.1, sherpa-onnx 2.0.1, llama-cpp-python 0.3.36, yt-dlp 2026.10.26, yt-dlp-ejs 0.8.0, curl-cffi 0.17.0. Bornes `>=x.y,<x+1` dans `pyproject.toml` (`<0.4` pour llama-cpp-python, `<0.9` pour yt-dlp-ejs, `<2028` pour yt-dlp). **À signaler à Louis** : yt-dlp suit un calendrier (2026.10.26) et casse souvent avec les sites ; la borne `<2028` laisse la mise à jour libre pendant plus d'un an, mais yt-dlp doit rester mis à jour régulièrement (`uv lock --upgrade-package yt-dlp`).
- Constats : (1) voir plus haut pour OpenCV ; (2) sur macOS, `av` et `cv2` embarquent chacun `libavdevice` (avertissement Objective-C, propre au Mac, sans effet sur Linux) ; (3) **torch / torchvision (corrigé après le premier essai serveur)** : torchvision est une dépendance transitive d'open-clip ; `[tool.uv.sources]` ne s'applique qu'aux dépendances directes, donc torch venait de l'index CPU et torchvision de PyPI. Le wheel PyPI Linux de torchvision 0.29.2 est construit pour CUDA (il embarque `libcudart`) : sur le serveur, `import open_clip` échouait avec `RuntimeError: operator torchvision::nms does not exist` (bibliothèque d'opérateurs non chargée). Correctif : `torchvision>=0.29.2,<0.30` devient une dépendance **directe**, avec la même source que torch (`pytorch-cpu`, marqueur `sys_platform == 'linux'`) ; le verrou résout alors `torch 2.15.0+cpu` et `torchvision 0.29.2+cpu` sous Linux (wheel `py3-none` sans `libcudart`, `torch>=2.14.0` exigé), PyPI sous macOS ; aucun paquet `nvidia-*` ni `triton`. **Pourquoi pas torch 2.14.x** (piste évoquée) : l'index CPU a bien un torchvision 0.29.2+cpu compatible avec torch 2.15 (les métadonnées des wheels 0.29.x exigent seulement `torch>=2.14.0`, et ils embarquent `_C_stable.so`, bibliothèque à ABI stable), donc aucun retour en arrière n'est nécessaire ; de plus torch 2.14.1 n'a pas de wheel cp315 pour macOS sur PyPI (la résolution échoue). Vérifié sur le Mac (`torch 2.15.0`, `torchvision 0.29.2`, `from torchvision.ops import nms`, spikes `clip` et `silero` verts) ; **non vérifié sur Linux** : à confirmer en relançant `clip` sur le serveur (décision réversible ; en cas de souci, essayer `torchvision 0.29.1+cpu`, aussi présent sur l'index CPU) ; (4) `torch.jit.load` (Silero) émet un `FutureWarning` « not supported in Python 3.14+ » mais fonctionne.
- Le script `scripts/spike_models.py` a aussi été lancé sur le Mac (hors serveur, simple validation du script) : les 10 modèles passent. Deux corrections issues de ce test : le modèle Kokoro `en-v0_19` ne fonctionne plus avec sherpa-onnx 2.x (lexique exigé) → `kokoro-int8-multi-lang-v1_0` avec `lexicon-us-en.txt` ; un schéma JSON sans bornes laisse Qwen3-1.7B divaguer jusqu'à `max_tokens` → `minItems`/`maxItems`/`maxLength` dans le schéma.
- Écart au texte de l'étape 4 : le pic mémoire est lu par le parent avec `os.wait4()` (l'`ru_maxrss` propre à chaque enfant), pas avec `getrusage(RUSAGE_CHILDREN)` qui est un maximum cumulé sur tous les enfants et ne distingue pas les modèles ; mêmes unités (octets sur macOS, Kio sur Linux, gérées). Chaque enfant a un délai de 1800 s (`"error": "timeout"` au dépassement).
- **Pour la tâche 9** : les étiquettes de release de sherpa-onnx (`tts-models`, `audio-tagging-models`) sont mobiles (des fichiers peuvent être ajoutés ou remplacés) ; l'épinglage réel est le sha256 du manifeste, à vérifier après téléchargement et avant extraction.

### Modèles et révisions épinglées (sha256 relevés sur le Mac, à confirmer sur le serveur)

| Nom | Source (révision figée) | Octets | sha256 |
| --- | --- | --- | --- |
| `silero` | poids `silero_vad.jit` du paquet silero-vad 6.2.3 | 2 272 526 | `e1122837f4154c511485fe0b9c64455f7b929c96fbb8d79fbdb336383ebd3720` |
| `whisper_small` | `Systran/faster-whisper-small` @ `536b0662742c` (`model.bin`) | 483 546 902 | `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671` |
| `whisper_base` | `Systran/faster-whisper-base` @ `ebe41f70d5b6` (`model.bin`) | 145 217 532 | `d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9` |
| `clip` | `laion/CLIP-ViT-B-32-DataComp.XL-s13B-b90K` @ `f0e2ffa09cbadab3db6a261ec1ec56407ce42912` (`open_clip_model.safetensors`) | 605 143 284 | `3c00043509d2e3f35ec62bd85a643a393883dcda624d8208d034cc390c708297` |
| `ced` | sherpa-onnx, release `audio-tagging-models`, `sherpa-onnx-ced-mini-audio-tagging-2024-04-19.tar.bz2` | 47 658 399 | `bec8bfa0af2c20ec3a9e7c6dd6c92d0fb6e96d6b25370ebe7a48e7283a60a02c` |
| `yunet` | opencv_zoo @ `eb9f4f2d4b79`, `face_detection_yunet_2026may.onnx` | 229 738 | `ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0` |
| `qwen4b` | `unsloth/Qwen3-4B-Instruct-2507-GGUF` @ `a06e946bb6b6` (`Q4_K_M`) | 2 497 281 120 | `3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597` |
| `qwen1_7b` | `unsloth/Qwen3-1.7B-GGUF` @ `d7f544eead69` (`Q4_K_M`) | 1 107 409 472 | `b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897` |
| `supertonic` | sherpa-onnx, release `tts-models`, `sherpa-onnx-supertonic-3-tts-int8-2026-05-11.tar.bz2` | 128 774 318 | `82fa96f91c4ef8abaae3a14a3f4153facf88bed821d1f7331cec2700f432c427` |
| `kokoro` | sherpa-onnx, release `tts-models`, `kokoro-int8-multi-lang-v1_0.tar.bz2` | 132 353 410 | `72c347230107ef8a31098ec4d468a605244beef5f1ed00db97efa13518800653` |

Écart à signaler : le dépôt officiel `Qwen/Qwen3-4B-Instruct-2507-GGUF` n'est pas accessible, et `Qwen/Qwen3-1.7B-GGUF` n'offre que du Q8_0 ; les GGUF Q4_K_M viennent de `unsloth` (Apache-2.0).

### Essai 3.15 (historique) : résultat du serveur (tâche 42, étape 5, lancé par Louis)

Contrôle CPU : `avx2 bmi2 f16c fma` présents, `avx512f` absent (comme attendu pour le CPU Coffee Lake « T »). Image `python:3.15-slim-trixie`, `llama-cpp-python` compilé avec AVX2/FMA/F16C/BMI2.

| Nom | ok | load_s | infer_s | pic RSS (Mo) | octets | sha256 (identique au Mac) | erreur |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `silero` | oui | 0.058 | 0.165 | 268.1 | 2 272 526 | `e1122837f4154c511485fe0b9c64455f7b929c96fbb8d79fbdb336383ebd3720` | — |
| `whisper_small` | oui | 0.836 | 1.443 | 976.8 | 483 546 902 | `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671` | — |
| `whisper_base` | oui | 0.321 | 0.493 | 428.0 | 145 217 532 | `d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9` | — |
| `clip` | **ÉCHEC** (corrigé, à relancer sur le serveur) | — | — | 537.1 | 605 143 284 | `3c00043509d2e3f35ec62bd85a643a393883dcda624d8208d034cc390c708297` | RuntimeError: operator torchvision::nms does not exist |
| `ced` | oui | 0.094 | 0.055 | 92.9 | 47 658 399 | `bec8bfa0af2c20ec3a9e7c6dd6c92d0fb6e96d6b25370ebe7a48e7283a60a02c` | — |
| `yunet` | oui | 0.156 | 0.019 | 85.8 | 229 738 | `ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0` | — |
| `qwen4b` | oui | 2.013 | 9.514 | 4594.5 | 2 497 281 120 | `3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597` | — |
| `qwen1_7b` | oui | 0.969 | 5.812 | 2204.3 | 1 107 409 472 | `b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897` | — |
| `supertonic` | oui | 0.707 | 1.371 | 293.0 | 128 774 318 | `82fa96f91c4ef8abaae3a14a3f4153facf88bed821d1f7331cec2700f432c427` | — |
| `kokoro` | oui | 1.396 | 2.78 | 426.1 | 132 353 410 | `72c347230107ef8a31098ec4d468a605244beef5f1ed00db97efa13518800653` | — |

Les 10 sha256 sont identiques à ceux relevés sur le Mac. Données brutes : `.superpowers/sdd/phase-1-squelette/task-42-server-spike.jsonl`.

| Mesure | Valeur |
| --- | --- |
| Taille de l'image `rushes:spike` | 3,71 Go sur disque (890 Mo de contenu) |
| Durée de `docker build` (sans cache) | à communiquer par Louis |
| Statut de `clip` | ÉCHEC au premier essai, correctif appliqué (voir le constat torch / torchvision plus haut) ; **CORRIGÉ, À RELANCER** : non vérifié sur le serveur |
| Verdict go / no-go | **EN ATTENTE** : 9 modèles sur 10 passent ; pas de verdict final tant que `clip` n'a pas passé sur le serveur après reconstruction de l'image |

Observations sur la mémoire (pic RSS mesuré sur le serveur, un modèle à la fois) : `qwen4b` est le plus lourd avec 4,6 Go (4 594,5 Mo), puis `qwen1_7b` 2,2 Go, `whisper_small` 1,0 Go, `whisper_base` 0,43 Go, `silero` 0,27 Go, `kokoro` 0,43 Go, `supertonic` 0,29 Go, `ced` 0,09 Go, `yunet` 0,09 Go ; le pic de `clip` n'est pas significatif (échec à l'import). Avec `qwen4b` chargé, la pile Postiz et le reste du serveur de 16 Go, la marge mémoire est faible ; le conteneur limité à 6 Go (étape 5) a suffi pour chaque modèle pris isolément.

Si un modèle échoue ensuite : s'arrêter et proposer à Louis un contournement (autre version épinglée, compilation, repli prévu par la spec : PANNs réécrit pour les sons, Whisper base, Qwen3-1.7B). Les sha256 servent au manifeste de la tâche 9.

## 2026-11-05 — Repli en Python 3.14 (décision de Louis)

Décision (Louis, 2026-11-05) : « 3.15 n'apporte pas grand chose au vu de toutes les implications ». Le projet passe de Python 3.15 à **Python 3.14** (`requires-python = ">=3.14,<3.15"`, `.python-version` = 3.14, ruff `py314`, mypy `3.14`, image `python:3.14-slim-trixie@sha256:af8a5aa14780025dbfbcfbc81bd8a91ebd87711dd5b0f48ed71090949dd7cb1d`). Cette décision lève la règle « pas de 3.14 sans l'accord de Louis » de la tâche 42 et remplace « Python 3.15 au lieu de 3.12 » (2026-11-01). Aucune règle R1 à R50 n'est touchée.

| # | Sujet | Décision | Touche |
| --- | --- | --- | --- |
| C31 | Version de Python | 3.14 (voir ci-dessus) ; l'essai 3.15 reste documenté, daté, plus haut | Spec, plans, `pyproject.toml`, Dockerfile, tâches 42 et 9 |

### Ce que l'essai en 3.15 a montré

- Sur le serveur, 9 modèles sur 10 passaient en 3.15 (sha256 identiques à ceux du Mac). Seul `clip` échouait (`operator torchvision::nms does not exist`).
- Cause : le wheel PyPI Linux de torchvision est construit pour CUDA (il embarque `libcudart`) et ne se charge pas à côté de torch `+cpu`. C'est **indépendant de la version de Python** ; le correctif (torchvision en dépendance directe, source `pytorch-cpu` sous Linux) reste nécessaire en 3.14.

### Compromis propres à 3.15 (abandonnés) et compromis qui restent

| Sujet | En 3.15 | En 3.14 |
| --- | --- | --- |
| onnxruntime | aucun wheel ; écarté par `override-dependencies` | override **supprimé** ; installé normalement (1.31.1) comme dépendance de `silero-vad` |
| Silero VAD | `load_silero_vad(onnx=False)` (TorchScript, `torch.jit.load` avertit « not supported in Python 3.14+ ») | `load_silero_vad(onnx=True)` : chemin ONNX, sans avertissement (vérifié sur le Mac) ; poids `silero_vad.onnx`, sha256 `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3` (2 327 524 octets) |
| faster-whisper | `vad_filter=False` faute d'onnxruntime | `vad_filter=False` conservé par choix de conception (le VAD du projet est Silero, segments passés par `clip_timestamps`) ; onnxruntime n'est plus un obstacle |
| OpenCV en double | override `opencv-python-headless; python_version < '3.15'` | **reste nécessaire** (ne dépend pas de Python) ; marqueur changé en `python_version < '3'` (jamais vrai : l'ancien marqueur serait devenu vrai en 3.14 et aurait forcé l'installation) |
| torch / torchvision | index `pytorch-cpu` sous Linux | inchangé ; torch 2.15.0(+cpu), torchvision 0.29.2(+cpu) |
| llama-cpp-python | compilé (sdist) | compilé (sdist), seul paquet sans wheel du verrou (PyYAML a maintenant un wheel cp314) |

Nouveau constat propre à 3.14 : le verrou d'`uv` n'enregistrait pas la dépendance `sherpa-onnx-core` du wheel cp314 de `sherpa-onnx` 2.0.1 (le wheel Mac sans `libonnxruntime.dylib` échouait à l'import : `Library not loaded: @rpath/libonnxruntime.dylib`, ce qui aurait aussi cassé le serveur). Correctif : `sherpa-onnx-core>=2.0.1,<3` ajouté en dépendance directe de `pyproject.toml`. À retirer si une version ultérieure de sherpa-onnx corrige ses métadonnées.

Autres constats : `uv pip check` signale que `scenedetect-headless` « requiert » `opencv-python-headless` non installé : c'est l'effet voulu de l'override. Python 3.14.1 est exclu par torchvision (`!=3.14.1`) ; 3.14.10 est utilisé. Les paquets TTS qui déclaraient `requires-python < 3.15` (kokoro, pocket-tts, coqui-tts) redeviennent installables en 3.14 ; non utilisés (sherpa-onnx reste le moteur retenu).

Résultats sur le Mac en 3.14 (macOS arm64, Python 3.14.10), les 10 modèles du spike passent : silero (ONNX) 262 Mo, whisper_small 914, whisper_base 490, clip 1 535, ced 95, yunet 90, qwen4b 4 947, qwen1_7b 2 595, supertonic 360, kokoro 512 (pic RSS en Mo, Mac). Les sha256 de tous les modèles sont inchangés, sauf `silero` (poids ONNX au lieu de TorchScript). 103 paquets au verrou ; wheels cp314 ou abi3 pour linux x86_64 et macOS arm64 pour tous les paquets lourds.

### Conséquences pour les tâches suivantes

Tout texte qui disait « Silero via torch » dit désormais « chemin ONNX (`load_silero_vad(onnx=True)`) » : corrigé dans `docs/spec.md` (lignes sur la pile Audio, la fiche faster-whisper, la fiche Silero VAD), `docs/plans/00-master.md` (règle transverse onnxruntime, ligne `vad_silero.py`) et `docs/plans/phase-1-squelette.md` (pile, ligne `silero` du spike). Les mesures mémoire Silero de la tâche 9 (0,24 Go, chemin TorchScript) sont à refaire avec le chemin ONNX (≈ 0,26 Go mesuré dans le spike). Le test `test_whisper_runs_without_onnxruntime` (phase 2) garde son sens (faster-whisper n'importe pas onnxruntime avec `vad_filter=False`) mais onnxruntime est maintenant installé pour Silero.

### Essai 3.14 : résultat du serveur (tâche 42, étape 5, lancé par Louis le 2026-11-05/06) — GO

Image `rushes:spike` (id `e19bb9c97d72`) construite sur `python:3.14-slim-trixie`, une instance par modèle, limites du conteneur de l'étape 5. Contrôle CPU du serveur (même hôte que l'essai 3.15) : `avx2 bmi2 f16c fma` présents, `avx512f` absent.

| Nom | ok | load_s | infer_s | pic RSS (Mo) | octets | sha256 | erreur |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `silero` (ONNX) | oui | 0.112 | 0.072 | 289.4 | 2 327 524 | `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3` | — |
| `whisper_small` | oui | 0.907 | 1.644 | 758.5 | 483 546 902 | `3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671` | — |
| `whisper_base` | oui | 0.374 | 0.672 | 351.8 | 145 217 532 | `d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9` | — |
| `clip` | oui | 3.949 | 0.143 | 1555.4 | 605 143 284 | `3c00043509d2e3f35ec62bd85a643a393883dcda624d8208d034cc390c708297` | — |
| `ced` | oui | 0.115 | 0.05 | 90.5 | 47 658 399 | `bec8bfa0af2c20ec3a9e7c6dd6c92d0fb6e96d6b25370ebe7a48e7283a60a02c` | — |
| `yunet` | oui | 0.032 | 0.014 | 83.9 | 229 738 | `ebafce4e3c118d6554634be5c27ab333b4c047a9a8c3faf1d7cf93101c22f0f0` | — |
| `qwen4b` | oui | 2.109 | 11.23 | 4543.6 | 2 497 281 120 | `3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597` | — |
| `qwen1_7b` | oui | 1.113 | 6.876 | 2121.3 | 1 107 409 472 | `b139949c5bd74937ad8ed8c8cf3d9ffb1e99c866c823204dc42c0d91fa181897` | — |
| `supertonic` | oui | 0.756 | 1.638 | 287.4 | 128 774 318 | `82fa96f91c4ef8abaae3a14a3f4153facf88bed821d1f7331cec2700f432c427` | — |
| `kokoro` | oui | 1.734 | 3.154 | 421.7 | 132 353 410 | `72c347230107ef8a31098ec4d468a605244beef5f1ed00db97efa13518800653` | — |

Données brutes : `.superpowers/sdd/phase-1-squelette/task-42-server-spike314.jsonl`. Le message OpenCV « setPreferableTarget Targets are not supported by the new graph engine for now » est un avertissement sans effet ; Silero (chemin ONNX) n'émet plus de `FutureWarning`.

| Mesure | Valeur |
| --- | --- |
| Taille de l'image `rushes:spike` (3.14) | 3,78 Go sur disque (911 Mo de contenu) |
| Durée de `docker build` | 2 min 48,95 s (`time docker build`) ; **le cache de construction Docker des essais 3.15 a pu être en partie réutilisé** : chiffre à lire comme un build à cache chaud ; un build à froid n'a pas été mesuré |
| Verdict go / no-go | **GO pour Python 3.14** : 10 modèles sur 10 passent sur le serveur |

Comparaison :
- Avec le Mac (3.14) : les 10 sha256 sont identiques, y compris `silero` (poids ONNX, `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`). Les pics mémoire du serveur sont du même ordre que ceux du Mac (sauf `qwen4b` 4,5 Go contre 4,9 Go sur le Mac).
- Avec l'essai 3.15 du serveur : tous les sha256 sont identiques sauf `silero` (TorchScript puis ONNX). `clip` est corrigé (échec à l'import en 3.15, cause : torchvision PyPI construit pour CUDA ; ici ok, pic 1 555 Mo, le pic de 537 Mo de l'ancien échec n'avait aucun sens). `ced`, `supertonic` et `kokoro` passent comme en 3.15. Pics : `whisper_small` 0,76 Go (0,98 Go en 3.15), `whisper_base` 0,35 Go (0,43), `qwen1_7b` 2,1 Go (2,2), `qwen4b` 4,5 Go (4,6, quasi inchangé), `silero` 0,29 Go (ONNX ; 0,27 Go en TorchScript).
- Le modèle le plus lourd est `qwen4b` (4,5 Go de pic), puis `qwen1_7b` (2,1 Go) ; avec `qwen4b` chargé et le reste de la pile du serveur de 16 Go, la marge mémoire reste faible (constat factuel, déjà noté pour l'essai 3.15).

### Points restants pour les tâches suivantes

- **Tâche 9** : mettre à jour le tableau mémoire du plan maître (Silero ≈ 0,29 Go sur le chemin ONNX, mesuré sur le serveur, contre 0,24 Go ; le plan maître garde l'ancienne valeur) ; construire le manifeste des modèles avec les sha256 de ce tableau ; mesurer la durée d'un **build à froid** (sans cache) ; finaliser le Dockerfile v0.
- `sherpa-onnx-core>=2.0.1,<3` est une dépendance directe de contournement (le verrou ne recopie pas la dépendance du wheel cp314 de sherpa-onnx) : à retirer quand sherpa-onnx corrigera ses métadonnées (tâche 9 : retester s'il est encore nécessaire ; le verrou actuel enregistre déjà cette arête, la relecture n'a pas pu reproduire le défaut hors du cache d'`uv`).
- torch et torchvision restent sur l'index `pytorch-cpu` sous Linux (le wheel PyPI Linux de torchvision est construit pour CUDA) ; l'override `opencv-python-headless; python_version < '3'` reste nécessaire.
- La mémoire de pointe et les temps réels du serveur (inférences sur le Coffee Lake) restent à confirmer en conditions réelles par la tâche 9 (ligne correspondante de « Points à vérifier » de la spec).

## 2026-11-06 — Exécution de la tâche 3 : arbitrages du coordinateur

| # | Sujet | Choix | Touche |
| --- | --- | --- | --- |
| C32 | Atomicité de `can_assign` + `assign` | Un moteur ordinaire reste celui de la tâche 2 (contrôle de transaction par défaut de pysqlite : aucun `BEGIN` avant la première écriture, donc une transaction qui lit puis écrit attend `busy_timeout` au lieu d'échouer, et les lecteurs WAL ne sont jamais bloqués). Seule l'attribution ouvre `BEGIN IMMEDIATE`, via `db.engine.write_session(engine)` (`expire_on_commit=False` : lire après le `commit` ne reprend pas le verrou ; fermer la session aussitôt) : le pilote passe en `isolation_level=None` le temps de cette transaction, puis revient au défaut au `commit`, au `rollback` ou au retour au pool. `assign` refuse (`RegistryRefused`) toute autre session, car le verrou d'écriture n'y est pas pris avant le premier `SELECT` du contrôle ; `can_assign` seul reste utilisable partout (simulation R33). Un autre écrivain attend `busy_timeout` (5 s) puis échoue. Règle pour toute tâche : ne jamais garder une transaction ouverte pendant un travail long ni pendant une cession du verrou lourd ; lire, fermer, travailler, puis écrire dans une transaction courte | Appelants de `registry.assign` : tâches 8a (`AssignStep`), 13 (attribution des compilations, une seule `write_session`), 17 (`detect-published`, ré-attribution d'un post expiré), 30 (compilations thématiques) ; règle de transaction : toute tâche qui lit ou écrit la base (36 `serve`, 18, 20…) |
| C33 | Passage déjà attribué | Déjà attribué à un autre compte : refus `overlap` (chevauchement avec lui-même) ; déjà attribué au même compte : refus `same_account_part` (le code est fixé par le contrat de la tâche 3, le message dit « déjà attribué à ce compte ») | Tâche 3 |
| C34 | Portée du pHash | Comparé à tout passage attribué à un autre compte, de n'importe quel épisode (« même gag venu d'une autre source ») ; le chevauchement temporel ne vaut que dans le même épisode | Tâche 3 |

## 2026-11-06 — Exécution de la tâche 4 : arbitrages du coordinateur

| # | Sujet | Choix | Touche |
| --- | --- | --- | --- |
| C36 | Carton de titre du générateur de vidéos synthétiques | Le FFmpeg du Mac (9.0.2, Homebrew) n'a pas le filtre `drawtext` (pas de libfreetype) et l'image du serveur peut ne pas l'avoir non plus. Le générateur n'utilise donc jamais `drawtext` : `title_card_ms` est un bloc d'images statiques (fond uni et motif `drawbox`, sans texte) sur l'intervalle donné ; `freezedetect` n'exige qu'une image fixe. Seuls `color`, `testsrc2`, `sine`, `anoisesrc`, `drawbox`, `overlay`, `volume`, `amix`, et les détecteurs `blackdetect`, `silencedetect`, `freezedetect` servent. `docs/plans/00-master.md` §6 et la tâche 4 de `phase-1-squelette.md` sont corrigés. Les phases suivantes qui prévoient `drawtext` dans le générateur (`shot_labels`, phase 2) devront utiliser un autre moyen (par exemple des motifs `drawbox` distincts par plan) | Tâche 4 ; générateur (`tests/fixtures/generator.py`) pour toutes les tâches de test média |
| C37 | Rotation couvrante sans la série | Le choix de repli sur toutes les marques de la verticale ne vaut que lorsqu'**aucune** période de `rotation` ne couvre le jour (« sans période »). Si une période couvre le jour mais qu'aucune marque n'y liste la série, il n'y a aucun compte candidat : `choose_owner` lève `RushesError` (indice : ajouter la série à la rotation en cours), l'élément d'ingestion échoue (code 4) et le fichier reste dans `import/` (R4 : un compte ne reçoit que les séries que la rotation lui assigne ; la propriété est définitive, R44). La rotation générée (tâche 16a) appliquera la même règle | Tâches 4, 16a |
| C38 | Retraits chaînés depuis les bords | Un noir ou un silence de plus de 2 s, un carton figé (début dans les 20 premières secondes), des génériques trouvés par images de référence comptent s'ils touchent un bord **ou une zone déjà retirée depuis ce bord**, quel que soit l'ordre de détection : `cartoon_removed` répète noirs et silences, génériques puis carton jusqu'à stabilité de l'ensemble retiré (5 passes au plus). Les noirs et gels sont comparés à la fin du flux vidéo, avec une image de tolérance en plus de 100 ms ; la durée d'un noir ou d'un silence se mesure sur la plage détectée, avant d'être ramenée au bord | Tâche 4 ; tâche 24 pour les créateurs |
| C39 | Ingestion locale : sous-dossiers et copies en cours | Un sous-dossier de `import/<id>/` (hors noms commençant par `_` ou `.`) donne un élément `warned` à chaque exécution tant qu'il n'est pas retiré ou vidé : c'est voulu, Louis doit le voir. Un fichier modifié depuis moins de 60 s (`SETTLE_S`, horloge du contexte) est `skipped` (« copie en cours ») sans calcul de sha256 ; il reste dans `import/` et la prochaine exécution le reprend. Limites acceptées : un fichier dont la date de modification est dans le futur (dérive d'horloge, exFAT) reste ignoré jusqu'à ce que l'horloge le rattrape ; une copie arrêtée plus de 60 s sans changement de date peut être ingérée à moitié écrite, et son sha256 (celui des octets partiels) est alors enregistré comme celui d'un épisode. Aucun contrôle de taille stable n'est ajouté (il retarderait chaque fichier d'au moins une seconde) | Tâche 4 |

## 2026-11-07 — Exécution de la tâche 5 : arbitrages du coordinateur

| # | Sujet | Choix | Touche |
| --- | --- | --- | --- |
| C40 | Image clé et lignes `scenes` | L'image clé est `episodes/<e_key>/keyframes/<shot_id>.jpg`, où `shot_id` est l'index 0-based du plan dans `scenes.json` (`Shot.id`), et non `<scene_id>` comme le disait la spec : l'id SQL change quand les lignes `scenes` sont recréées (reprise après plantage). Les lignes `scenes` sont insérées dans l'ordre des plans ; toutes les durées de `scenes.json` et `audio.json` sont en temps de `clean.mp4` (0 = son début). Conséquence pour la tâche 12 (vision) : retrouver le plan d'une ligne `scenes` par `start_ms` (égal à `Shot.start_ms`), puis l'image clé par `Shot.id`, jamais par l'id SQL. `Shot.keyframe_ms` est le milieu du plan, sauf pour le dernier plan quand le son dépasse l'image (le plan est étiré jusqu'à la durée du conteneur) : l'instant est alors borné à `video_duration_ms` moins 2 images et peut être avant le milieu (jamais avant le début du plan) ; les tâches 6 et 12a lisent `keyframe_ms`, pas le milieu | Tâches 5, 6, 12a |
| C41 | Bruit de `speech_like_ms` et seuils de `FakeVad` | Le brief de la tâche 5 fixait `FakeVad` à RMS > 0,25 et taux de passages par zéro > 0,2, et le générateur à un bruit **rose** à 0,5. Mesuré : le bruit rose d'amplitude 0,5 a un RMS de 0,08 à 0,12 et un taux de 0,13 à 0,21, proche des bips à 1000 Hz (taux 0,125), donc inséparable. `speech_like_ms` devient un bruit **blanc** à 0,5 (`anoisesrc=c=white`, même amplitude), qui a un RMS de 0,155 après rééchantillonnage à 16 kHz et un taux voisin de 0,5 ; le seuil RMS de `FakeVad` passe à 0,1 (le ton de fond a un RMS de 0,07, les bips un taux de 0,125 : tous deux restent ignorés). Le bruit est semé (`anoisesrc=...:seed=1`) donc reproductible : le pic mesuré est à -1,06 dB (AAC compris). Seul le fichier de test `test_synth_audio_levels_follow_the_contract` change : borne haute `< 2,0` dB, soit environ 3 dB au-dessus du pic mesuré. Aucune règle ni spec touchée : `FakeVad` est une doublure | Tâches 4 (générateur), 5 ; doublures de la tâche 12b |
| C42 | Sens du temps de `LoudPoint` | `LoudPoint.t_ms` est la **fin** de la fenêtre de 3 s du court terme (ebur128 imprime `t` en fin de fenêtre) : la valeur décrit le son de [t_ms - 3000, t_ms]. `analyze` n'applique aucun décalage (la loudness d'un plan est la moyenne des points dont `t_ms` tombe dans le plan) ; c'est à la tâche 6 de choisir (par exemple décaler de -1500 ms pour situer les creux), à signaler dans son plan | Tâches 5, 6 |
| C43 | Fichiers d'analyse réutilisés, `release()` | Un épisode `cleaned` dont `scenes.json`, `audio.json` et les images clés sont valides est reconstruit sans recalcul (lignes `scenes` refaites depuis `scenes.json`), à condition que `scenes.duration_ms` égale `episodes.duration_ms` (posé par `clean` d'après `clean.mp4`) et que `audio.json` (écrit en dernier, marqueur) soit plus récent que `clean.mp4`. `_compute` supprime `audio.json` avant d'écrire quoi que ce soit, et `clean` supprime `scenes.json`, `audio.json` et `keyframes/` quand il réécrit `clean.mp4`. `ModelHub.release()` décharge **tous** les modèles (légers compris), puis `gc.collect()` | Tâches 5, 4 (clean), 12 |

## 2026-11-07 — Exécution de la tâche 6 : arbitrages du coordinateur

| # | Sujet | Choix | Touche |
| --- | --- | --- | --- |
| C44 | Découpe en parties : temps, parole, mouvement, idempotence | (1) `LoudPoint.t_ms` finit la fenêtre de 3 s (C42) : l'étape `cut` passe à `cut_point_costs` des paires `(t_ms − 1500, lufs)` (temps du centre de la fenêtre), et `cut_point_costs` prend pour un point la valeur la plus proche (bornée aux extrémités), z-score sur l'épisode ; sans point de loudness, le terme vaut 0. (2) `Shot.keyframe_ms` n'est pas lu par `cut`. (3) La parole de `audio.json` est élargie de `speech_margin_ms` de chaque côté et l'intervalle élargi est **fermé** : un point y est interdit (inf), bornes comprises (parole 60 000–75 000 ms, marge 150 : aucune borne de partie dans [59 850 ; 75 150]) ; un pic interdit les points de `[pic − peak_guard_ms ; pic[`. Le premier et le dernier point coûtent toujours 0. (4) `motion_after(p)` = mouvement moyen des plans qui chevauchent `[p, p + 2 000]`, pondéré par le chevauchement (0 sans plan). (5) `phash_json` d'une partie = liste JSON des `phash` des plans qu'elle chevauche, dans l'ordre ; famille toujours `monetizable`, bornes lues dans `accounts.yaml` (`families.monetizable`), jamais écrites en dur. (6) Idempotence : `select` ne renvoie que les épisodes `analyzed` sans segment `part` ; avec `--force` les épisodes avec parties sont aussi sélectionnés, mais une partie qui n'est plus `proposed` ou qui est référencée (attribution, compilation, accroche, post) rend l'élément `skipped` (« déjà attribué », master §5.3) ; sinon les parties sont supprimées puis réécrites (mêmes tirages, donc mêmes bornes) dans **une** transaction courte, avec contrôles de `rowcount` (C32). Un épisode trop court ou sans partition donne `warned` à chaque exécution tant qu'il reste sans partie. Avec `--force`, si plus aucune partition n'existe (bornes de la famille changées, épisode trop court) et qu'aucune ancienne partie n'est utilisée, les anciennes parties `proposed` sont supprimées dans la même transaction gardée avant le `warned` (R6 : une partie hors bornes n'est jamais conservée) ; si une partie est utilisée, l'élément reste `skipped` (« déjà attribué »). (7) Pics « Les plus revus » : `peaks_ms = []` en phase 1, point d'accroche `# task 13` dans `cut/parts.py`. (8) Tests de l'étape : l'état analysé est construit directement (`tests/helpers.analyzed_episode`, sans ffmpeg) ; R5, R6 et R43 restent dans `rules_pending.toml` | Tâches 6, 13 (pics), 14 (accroches : les parties avec accroche deviennent intouchables), 36 |

## 2026-11-08 — Exécution de la tâche 7a : arbitrages du coordinateur

| # | Sujet | Choix | Touche |
| --- | --- | --- | --- |
| C45 | Rendu de la plaque (7a) : encodage, son, ordre fichiers/base | (1) `-x264-params 8x8dct=1` en plus des arguments du plan : avec `-preset ultrafast`, `-profile:v high` n'est qu'un plafond et x264 écrit « Constrained Baseline » ; `8x8dct=1` suffit pour High, vérifié à ffprobe aux cinq préréglages (`cabac=1` seul ne donne que Main). (2) La mise en page par défaut vient de `accounts.yaml` (`layouts.preference[0]`, pas de `settings.layouts`) ; une liste vide donne `square` (R8). (3) Son : source sans piste audio → piste `anullsrc` stéréo 48 kHz ; mesure non finie ou ≤ −70 LUFS (silence numérique) → pas de gain, simple `aresample=48000` ; dans les deux cas le rendu aboutit et `render_plate` renvoie `None`. (4) Durée : le son passe par `apad` puis `-shortest` et `-t` (borne dure) ; la durée rendue est comparée à celle du passage (±150 ms), sinon `MediaError` (un passage qui dépasse la fin de `clean.mp4`). (5) Sous `--force`, un post `ready` est sélectionné pour être `skipped` avec un message (« déjà prêt »), jamais re-rendu. (6) R3 s'applique aussi au re-rendu d'un post `rendered` : son passage doit rester `approved`. (7) Loudness (R7) : la 2e passe imprime `print_format=json` (option ajoutée par `render_plate`, la chaîne de `second_pass_filter` du plan restant la valeur par défaut) ; `output_i` hors de −14 ± 1 LU → `MediaError` en français, post inchangé et aucun fichier gardé ; `normalization_type == dynamic` (loudnorm a refusé le gain linéaire) dans la tolérance → `done` avec « (loudnorm dynamique) » dans le message. `check_delivery` ne mesure toujours pas la loudness. (8) Ordre (C32) : plaque et vidéo finale sont écrites dans des temporaires (`fsutil.staged`) ; dans la transaction courte, l'`UPDATE` gardé passe d'abord, puis les deux `os.replace`, puis le `commit` ; garde refusée (post écarté, `ready` ou passage retiré entre-temps) → temporaires jetés, élément `skipped` « n'est plus à rendre » ; un arrêt entre les renommages et le `commit` laisse des fichiers neufs avec l'ancienne ligne, et le run suivant les refait (convergence). Même convergence si le second `os.replace` échoue (plaque neuve, vidéo ancienne, ligne ancienne, aucun temporaire ; testé). Coin non refermé : un `--force` sur un post déjà à la version courante dont le second renommage échoue laisse la plaque neuve et la vidéo ancienne avec la ligne inchangée, donc rien ne la refait sans nouveau `--force` ; sans effet visible en 7a (la vidéo est une copie octet pour octet de la plaque), à traiter en 7b quand les incrustations rendront la vidéo différente de la plaque. `Staged.commit` refuse (`RushesError`) un temporaire jamais écrit, avant tout renommage. (9) `check_delivery` lit la taille affichée (rapport de pixel et rotation), la marque `ftyp` (liste blanche isom/iso2/iso4/iso5/iso6/mp41/mp42/avc1/M4V ; QuickTime refusé) et ne lève jamais d'erreur sur un atome tronqué (« atome tronqué »). (10) `crop_x` (recadrage, tâche 34) : virgules échappées par `plate_filter`, `:` `;` `[` `]` quotes, antislash et espaces refusés (`ValueError`). (11) Messages avec « x » et non « × » (RUF001). (12) R7 et R8 restent dans `rules_pending.toml` (R7 jusqu'à la tâche 35, R8 jusqu'à la 34) : ils sont testés en partie seulement | Tâches 7a, 7b (la copie de la vidéo finale devient la passe d'incrustations, dans les mêmes temporaires), 34 (`crop_x`), 35 |
