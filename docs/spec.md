# rushes — Cahier des charges final

Version du 1er novembre 2026.

## Mission et périmètre

`rushes` est un pipeline Python personnel, utilisé par une seule personne (Louis), qui prépare en continu des vidéos courtes prêtes à publier pour 4 marques. Ce document est la seule référence : il remplace tous les documents précédents. En cas de contradiction avec un ancien texte, c'est ce document qui fait foi.

| Verticale | Marques | Plateformes | Contenu |
| --- | --- | --- | --- |
| `cartoons` | Saturday Toons (@saturdaytoons), Toon Tape (@toontape) | TikTok et Instagram, un compte par plateforme et par marque | Extraits de dessins animés français et américains connus à l'international, sans paroles de préférence, sinon en version anglaise |
| `creators` | Instant Culte (@instantculte, français), Peak Moments (@peakmoments, anglais) | TikTok et Instagram, un compte par plateforme et par marque | Extraits courts et best-of de vidéos de créateurs YouTube populaires, republiés en vertical (côté français : Squeezie, Byilhan…) |

**Le principe** : le logiciel fait tout le travail mécanique (trouver, récupérer, nettoyer, découper, mettre en forme, écrire, classer, mesurer). La seule action récurrente de Louis est de publier, toujours à la main : deux fois par semaine, il ouvre sur son Mac la page des posts prêts, écarte ce qui ne lui plaît pas ou change d'accroche d'un clic, envoie vidéos et textes à son iPhone par AirDrop, puis programme les posts dans les applis (ou les garde en brouillon quand l'appli ne programme pas).

**L'autonomie** : rushes doit être vraiment autonome. Il trouve lui-même les séries et créateurs en tendance, les chaînes officielles d'une série et leurs vidéos les plus vues, les requêtes de recherche, les hashtags qui marchent d'après les tendances et les vidéos du même secteur, la rotation des séries, les créneaux et les seuils. Le but final : suivre seul des tendances qui changent, sans que Louis ait à suivre ni analyser le marché. La config ne donne que des amorces facultatives et les décisions qui reviennent à Louis (voir la décision « Autonomie » ci-dessous).

**La vitrine** : une série ou un créateur sans licence ni accord passe quand même par tout le pipeline, en mode test. Le contenu fini sert de vitrine à présenter aux ayants droit, et un tableau de suivi des licences liste les demandes à faire.

**La contrainte qui oriente les choix** : TikTok et Instagram déclassent les contenus republiés sans apport éditorial (Instagram depuis le 30 avril 2026 : un compte dont une part significative des posts reprend le contenu d'autrui perd les recommandations ; TikTok : contenu reproduit ou « combiné de plusieurs sources avec peu d'apport » exclu du fil Pour toi et de Creator Rewards). D'où le module d'originalité (de base, puis avancé), les accroches, les best-of, les compilations, l'unicité stricte des passages entre comptes, et le fait que rien ne part sans l'action de Louis.

| Le logiciel fait | Le logiciel ne fait pas |
| --- | --- |
| Chercher et classer des vidéos sur YouTube avec yt-dlp, lire métadonnées, courbe « Les plus revus », commentaires, SponsorBlock | Utiliser l'API YouTube Data |
| Télécharger les vidéos des sources suivies | Contourner une protection, un DRM ou une limite d'accès |
| Nettoyer, découper, recadrer intelligemment, mettre en forme, sous-titrer, normaliser le son, ajouter une voix off locale | Recadrer à l'aveugle, sans suivre l'action |
| Approuver automatiquement au-dessus d'un seuil, classer une file par compte | Programmer ou publier quoi que ce soit : seul Louis publie, à la main |
| Préparer `outbox/` et une page servie sur le réseau de Louis, avec des boutons pour écarter un post ou changer d'accroche | Envoyer quoi que ce soit à une plateforme ou à un outil de publication |
| Lire les statistiques publiques des comptes, sans connexion | Utiliser les cookies ou identifiants des comptes de publication |
| Tourner en local, sur CPU, en conteneur | Appeler une API payante ou un service d'IA distant |

Non-objectifs pour l'instant : application web, multi-utilisateur, authentification, file de tâches distribuée, base de données serveur.

## Décisions arrêtées et glossaire

Ces choix sont tranchés. Ne pas les remettre en cause dans le code ; si l'un d'eux semble poser problème, le signaler à Louis.

| Sujet | Décision |
| --- | --- |
| Langage et outillage | Python 3.14, un seul paquet, uv, CLI Typer, config YAML validée par Pydantic v2, état dans SQLite |
| Machine | Serveur HP EliteDesk 800 G4 mini sous Debian, sans carte graphique, qui héberge d'autres services ; tout tourne sur CPU, en conteneur. Louis travaille depuis un Mac et un téléphone |
| IA | Aucune API payante ni service distant. Petits modèles locaux uniquement. Un seul modèle de langage (Qwen3-4B-Instruct Q4, repli Qwen3-1.7B), pour les accroches et le commentaire de la voix off, désactivable |
| Récupération | yt-dlp utilisé comme bibliothèque, sans cookies par défaut ; les cookies d'un compte Google (idéalement dédié) ne servent qu'en secours, si YouTube exige une connexion, avec alerte ; aucune API |
| Statistiques | Sans clé : lecture publique déconnectée avec yt-dlp (TikTok), export CSV de Meta Business Suite (Instagram) ; API officielles en lecture seule plus tard, en option |
| Publication | Toujours manuelle. rushes prépare chaque post dans `outbox/` (vidéo, couverture, fiche `upload.json`) ; Louis le consulte sur son Mac, l'envoie à son iPhone par AirDrop et le publie ou le programme dans l'appli (TikTok Studio si la programmation existe pour un compte personnel, sinon brouillon). Pas de Postiz ni d'API de publication : les règles de TikTok refusent l'audit d'un outil qui publie sur ses propres comptes |
| Validation | Approbation automatique au-dessus d'un seuil ; les autres passages attendent une revue facultative. Louis valide en publiant |
| Accroches | Moteur de règles + petit modèle local ; 3 propositions, la mieux classée est appliquée, les 2 autres restent disponibles |
| Originalité | Deux modules, tous deux codés et prêts en production, choisis par compte (réglage global par défaut). Module de base : accroche, légende, bandeau « Part N » ou titre du mini best-of au-dessus de la vidéo. Module avancé : voix off locale (commentaire de 1 à 3 phrases écrit par le modèle de langage, son original atténué) et sous-titres calés sur la voix, activé si les plateformes signalent le contenu comme non original |
| Mise en page | Vidéo recadrée qui suit l'action : carré 1:1 par défaut, vertical 9:16 de temps en temps (test), repli 3:4 ; image entière sur fond flou en tout dernier recours. Le classement apprend quelle mise en page marche le mieux par compte |
| Langue | Une langue par marque : anglais pour Saturday Toons, Toon Tape et Peak Moments, français pour Instant Culte |
| Unicité | Une vidéo source et tout ce qui en dérive vont sur un seul compte ; un passage n'est jamais publié sur deux comptes |
| Contenus | Chaque vidéo source donne toujours une vidéo découpée en parties et un best-of, dans l'ordre chronologique |
| Numérotation | « Part N » seul (« Partie N » pour une marque francophone, comme Instant Culte) ; le nombre total de parties n'est jamais affiché |
| Autonomie | Le logiciel déduit lui-même tout ce qui peut l'être : séries et créateurs en tendance, chaînes officielles et vidéos les plus vues d'une série, comptes TikTok officiels, requêtes de recherche, panel de comptes TikTok, hashtags tournants (d'après les tendances et les vidéos du même secteur), rotation des séries, créneaux, parts de familles, mises en page, seuils. La config ne contient que des amorces facultatives : une valeur écrite dans la config l'emporte sur la valeur déduite (correction manuelle). Les valeurs déduites sont stockées en base avec leur origine, leur date et leur confiance. Ne sont jamais déduits ni définis par défaut, car ils relèvent d'une décision de Louis : `licensed` (R14), `authorized` (R22, R32), les hashtags obligatoires (R13), les marques, les langues et les comptes |
| Durées | Toute vidéo dure plus d'une minute (au moins 65 s), pour que chacune ait un potentiel de monétisation ; il n'y a pas de famille courte |
| Interface | Sortie console soignée (Rich) ; page des posts prêts servie par `rushes serve` sur l'EliteDesk, derrière le reverse proxy et le SSO PocketID de Louis, consultée depuis le Mac ; tableau de bord compatible téléphone plus tard, hors périmètre |
| Notifications | ntfy auto-hébergé, notifications reçues sur iPhone |
| Code tiers | Aucun pour l'instant : les idées utiles d'AI-Youtube-Shorts-Generator (découpage en morceaux, déduplication, cache de transcription, recadrage) sont réécrites, sans obligation de les reprendre si elles sont inadaptées. R40 et R41 restent valables pour tout futur code tiers |
| Autorisations | Une série (`licensed`) ou un créateur (`authorized`) non autorisé ne produit que des posts de test, jamais publiables, qui servent de vitrine pour les demandes de licence ; un tableau de suivi des licences liste ces demandes |
| Plateformes | TikTok et Instagram pour les 4 marques. Rien n'est publié sur YouTube, qui ne sert que de source |
| Types de compte | TikTok : comptes personnels, jamais Entreprise (seuls les comptes personnels sont éligibles à Creator Rewards). Instagram : compte professionnel, catégorie Créateur (statistiques, export, programmation dans l'appli) |
| Catalogue de départ | Tom et Jerry et Oggy (cartoons), Squeezie et Byilhan (créateurs), non autorisés au départ ; rushes en trouve d'autres seul dans les tendances |

**Glossaire.** Chaque terme a un sens unique ; les noms anglais sont ceux du code.

| Terme | Code | Définition |
| --- | --- | --- |
| Marque | `brand` | Identité commune aux comptes d'un même nom ; a une verticale, une langue, un fuseau |
| Verticale | `vertical` | `cartoons` ou `creators` ; une marque n'en a qu'une |
| Compte | `account` | Une marque sur une plateforme (Saturday Toons sur TikTok…) |
| Série | `series` | Un dessin animé, décrit par un fichier YAML |
| Créateur | `creator` | Une chaîne YouTube suivie, décrite par un fichier YAML |
| Vidéo source | `source_video` | Une vidéo repérée sur YouTube, avant téléchargement |
| Épisode | `episode` | Un fichier vidéo ingéré (épisode de série ou vidéo de créateur), rattaché à une série ou un créateur |
| Provenance | `provenance` | `official`, `community` ou `local` (fichier fourni à la main) |
| Propriétaire | `owner_account_id` | Le compte auquel un épisode et tous ses contenus dérivés appartiennent |
| Plan | `shot` | Intervalle entre deux changements de plan |
| Scène | `scene` | Entrée de l'index : un plan enrichi (images clés, étiquettes, sons, personnages, popularité) |
| Chute | `punchline` | Moment fort d'un gag (pic sonore ou pic « Les plus revus ») |
| Moment fort | `highlight` | Instant repéré par un signal, autour duquel on construit un passage |
| Passage | `segment` | Intervalle [début, fin] d'un épisode ; unité du registre |
| Partie | `part` | Passage consécutif d'une vidéo découpée, publié en série (« Part 2 ») |
| Best-of | `bestof` | Vidéo qui enchaîne 4 à 8 moments d'un même épisode, dans l'ordre |
| Compilation thématique | `compilation` | Vidéo qui enchaîne 4 à 8 passages de plusieurs épisodes d'un même compte, autour d'un thème |
| Famille | `family` | Fourchette de durée : `monetizable`, `long` (toutes deux de plus d'une minute) |
| Accroche | `hook` | Texte de moins de 8 mots affiché au début de la vidéo et en tête de légende |
| Légende | `caption` | Texte du post : accroche, nom, situation, numéro de partie, question, crédit, hashtags |
| Post | `post` | Une vidéo préparée pour un compte, de sa planification à sa publication |
| Registre | `assignments` | Table qui lie chaque passage à un seul compte |
| File | `queue` | Classement des posts candidats d'un compte, recalculé toutes les 6 h |
| Campagne | `campaign` | Programme par lequel un créateur autorise les clips, avec ses règles (mentions, hashtags) |
| Mise en page | `layout` | Cadre de la vidéo dans le canevas 1080×1920 : `square` (1:1), `vertical` (9:16), `portrait` (3:4), `full` (image entière sur fond flou) |
| Module d'originalité | `originality` | Couche éditoriale ajoutée au rendu : `base` (accroche, bandeau, titre) ou `advanced` (voix off, sous-titres de la voix) ; choisi par compte |
| Vitrine | `showcase` | Contenu produit en mode test pour une série ou un créateur non autorisé, à présenter aux ayants droit |
| Suivi des licences | `rights` | Tableau des séries et créateurs qui ont besoin d'une licence ou d'un accord, avec l'état de la demande |

## Comptes, formats et règles de publication

Toutes les valeurs de cette section vivent dans la config, jamais en dur : les plateformes les changent souvent.

### Comptes et rotation

- **Cartoons** : 2 marques, chacune avec un compte TikTok et un compte Instagram, soit 4 comptes. Un 3e compte par plateforme est possible plus tard ; il recevrait ses propres épisodes (R44), pas seulement des compilations.
- **Créateurs** : 2 marques, chacune avec un compte TikTok et un compte Instagram, soit 4 comptes. Les créateurs sont répartis par langue : français pour Instant Culte, anglais pour Peak Moments.
- **Plan croisé (cartoons)** : chaque compte reçoit des séries pour une période de 3 semaines, puis les séries sont permutées entre comptes avec de nouveaux épisodes, pour comparer les séries indépendamment des comptes. rushes génère ce plan seul à partir des séries actives, en périodes de 21 jours comptées à partir du lundi du lancement (avant le lancement, une ancre provisoire sert à la répétition générale) ; une `rotation` écrite dans `accounts.yaml` l'emporte (correction manuelle). La rotation ne décide que des nouveaux épisodes : ceux déjà ingérés restent chez leur compte propriétaire (R4, R44), et une série garde son créneau fixe tant que ce compte a du stock d'elle.
- **Propriété** : à l'ingestion, chaque épisode reçoit un compte propriétaire, choisi parmi les comptes à qui la rotation assigne la série (ou le créateur), en équilibrant le stock.

### Familles de durée

La durée n'est jamais fixe : une cible est tirée au hasard dans la fourchette (graine enregistrée), puis le découpage coupe au meilleur point proche.

| Famille | Plateformes | Fourchette | Part de départ | Rôle |
| --- | --- | --- | --- | --- |
| `monetizable` | TikTok, Instagram | 65 à 100 s | 100 % sur chaque plateforme | Format courant, éligible à Creator Rewards (plus d'une minute) |
| `long` | TikTok, Instagram | 120 à 180 s | Ponctuel | Best-of ou compilation qui tient la rétention (3 min au plus sur Instagram) |

La « part de départ » est la part de chaque famille dans les posts d'une plateforme (`share` dans la config). Creator Rewards ne concerne que TikTok : sur Instagram, `monetizable` n'est qu'un format de durée.

### Volumes et créneaux

| Compte | Au lancement | Cible |
| --- | --- | --- |
| TikTok (chacun des 4 comptes) | 1 par jour | 2 à 3 par jour |
| Instagram (chacun des 4 comptes) | 3 à 5 par semaine | 1 par jour |

Au lancement, environ 7 vidéos par jour pour les 8 comptes ; 16 au maximum à la cible. Louis monte le rythme lui-même (`posts_per_day` dans la config) ; le rapport hebdomadaire signale un compte qui pourrait passer au palier suivant. Créneaux de départ dans le fuseau de la marque : cartoons et Peak Moments en soirée heure de New York (14 h à 18 h, soit environ 20 h à minuit à Paris), Instant Culte en soirée heure de Paris. À recaler après 3 semaines sur les heures d'activité réelles.

### Parties, ordre et numérotation

- Les parties d'un épisode sortent dans l'ordre sur le compte propriétaire : la partie k+1 n'est jamais programmée avant la partie k.
- Le numéro est attribué au rendu, dans l'ordre de publication réel : si une partie est écartée, les suivantes non publiées sont renumérotées (nouveau rendu léger du bandeau). Le spectateur ne voit jamais de trou.
- Bandeau « Série — Part N » ; carte de fin « Part N+1 next » (pas de jour précis) ; en français « Partie N » et « Partie N+1 ensuite ». La carte de fin de chaque post montre aussi la photo de profil de la marque, le @ du compte et « Follow » (« Abonne-toi » en français). La dernière partie n'annonce pas de suite et renvoie vers une autre série du compte. Aucun total, nulle part.
- Le best-of et les compilations n'ont pas de numéro de partie.
- Une série garde un créneau fixe sur un compte ; les séries alternent d'un créneau à l'autre.

### Titres, légendes, hashtags, couvertures

| Élément | TikTok | Instagram |
| --- | --- | --- |
| Légende | 50 à 150 caractères ; nom de la série ou du créateur dans les 80 premiers | Nom dans les 125 premiers |
| Hashtags | 5 au maximum. Cartoons : `#fyp #nostalgiatok #cartoons #<nom anglais de la série>` + 1 tournant. Créateurs : `#fyp #<créateur>` + 3 tournants. Jamais #viral ni #foryou | 5 au maximum, légende et commentaires confondus ; pas de #fyp. Cartoons : `#cartoons` + 1 hashtag nostalgie choisi par l'étude de marché + 3 tournants. Créateurs : `#<créateur>` + 4 tournants |
| Couverture | 1080×1920, choisie parmi 3 candidates | 1080×1920, titre au centre (recadrage 4:5 et 3:4) |
| Crédit (créateurs) | Nom du créateur (jamais son @) et exigences de la campagne | Idem, plus le compte du créateur identifié s'il en a un |

- Structure d'une légende : accroche, nom de la série ou du créateur, situation, « Part N » le cas échéant, une question, crédit et mentions de campagne (créateurs), hashtags.
- Exemple : « Tom really thought this plan would work 😭 Tom and Jerry, Part 2. Who wins? 👇 #fyp #nostalgiatok #cartoons #tomandjerry #slapstick ».
- `#<créateur>` est le nom du créateur de contenu (`#squeezie`) ; `#<nom anglais de la série>` est le titre anglais sans espaces (`#tomandjerry`).
- Couverture : image expressive, 2 à 4 mots en gros, « Part N » s'il y a lieu, même gabarit sur tout le compte.
- Gabarits Jinja2 par série ou créateur. Les hashtags obligatoires sont fixés ci-dessus (décision de Louis) ; les hashtags tournants viennent de l'étude de marché, des tendances du secteur, du contenu de la vidéo et des statistiques du compte, pas de la config, qui n'offre que des amorces.

## Architecture

rushes est un seul paquet Python dans une seule image, avec SQLite comme unique état et FFmpeg pour toute la vidéo. La même image tourne de deux façons : les commandes du pipeline, lancées par les minuteurs, et `rushes serve`, petit serveur web toujours actif pour la page des posts prêts. Un service séparé, facultatif, s'y ajoute : ntfy (notifications).

### Stack

| Domaine | Choix |
| --- | --- |
| Projet | uv (dépendances, lockfile), Python 3.14 |
| CLI et console | Typer ; Rich pour l'affichage (seul le module `ui` l'importe) |
| Config | YAML validé par Pydantic v2 |
| Base | SQLite via SQLAlchemy 2.1 et Alembic ; migrations versionnées ; vecteurs en BLOB, recherche en numpy |
| Vidéo | FFmpeg et ffprobe en sous-processus (FFmpeg 7.1 de Debian trixie, avec libass et libx264), version épinglée dans l'image |
| Plans | PySceneDetect 0.7.2, paquet `scenedetect-headless`, paramètres du détecteur fixés explicitement |
| Téléchargement | yt-dlp comme bibliothèque, avec `yt-dlp-ejs`, Deno (exigé par YouTube) et curl-cffi (exigé par TikTok) |
| Images | Pillow (incrustations, couvertures), OpenCV contrib headless (netteté, mouvement, flux optique, saillance, visages YuNet), imagehash (pHash) |
| Vision | open\_clip, CLIP ViT-B/32 (`datacomp_xl_s13b_b90k`) |
| Audio | Silero VAD (chemin ONNX, onnxruntime), faster-whisper (base ou small, int8, VAD interne désactivé), sherpa-onnx avec CED pour les événements sonores |
| Voix off | sherpa-onnx avec Supertonic 3 (français et anglais), repli Kokoro (anglais) |
| Modèle de langage | llama-cpp-python dans le même processus, Qwen3-4B-Instruct-2507 Q4\_K\_M, repli Qwen3-1.7B |
| Serveur web | Petit serveur (`rushes serve`, FastAPI, page React + shadcn/ui) pour la page des posts prêts et ses boutons, derrière le reverse proxy GoDoxy et le SSO PocketID de Louis |
| Textes | Jinja2, rapidfuzz |
| Qualité | pytest, ruff, mypy strict sur `core/` et `db/`, pre-commit |

onnxruntime est installé normalement (dépendance de `silero-vad`, chemin ONNX). `override-dependencies` dans `pyproject.toml` n'écarte plus que `opencv-python-headless` (tiré par `scenedetect-headless`, il écraserait `cv2/` d'OpenCV contrib) ; torch et torchvision viennent de l'index CPU de PyTorch sous Linux (voir `docs/decisions.md`).

**Règles d'architecture**

- Chaque étape lit la base et des fichiers, écrit ses fichiers, puis met à jour la base dans une transaction. Relancer une étape déjà faite ne change rien, sauf avec `--force`.
- Les modèles sont chargés une fois par étape (R19), jamais par élément, derrière une interface dans `models/` ; les tests utilisent des doublures.
- Aucun chemin en dur : tout passe par la config et `rushes.paths`.
- Les modules `core/` ne dépendent ni de la CLI, ni des modèles, ni de FFmpeg.
- Les étapes émettent des événements (`ui.step`, `ui.progress`, `ui.result`, `ui.warn`) ; elles n'écrivent jamais directement sur la console.

### Arborescence

```text
rushes/
├── CLAUDE.md
├── pyproject.toml
├── config/
│   ├── settings.yaml          # seuils, modèles, originalité, cookies, vitrine, serve, ntfy
│   ├── accounts.yaml          # marques, comptes, rotation, familles, créneaux
│   ├── series/<id>.yaml
│   ├── creators/<id>.yaml
│   ├── themes/<id>.yaml       # compilations thématiques
│   ├── charter.yaml           # charte de chaque marque (police, couleurs, zones sûres, photo de profil)
│   ├── avatars/<marque>.png   # photo de profil affichée sur la carte de fin
│   └── hooks/rules.yaml
├── docs/                      # spec.md (ce document), decisions.md
├── src/rushes/
│   ├── cli.py  config.py  paths.py  ui.py
│   ├── core/                  # intervalles, timecodes, scores, tirages
│   ├── db/                    # modèles, migrations, registry.py
│   ├── models/                # interfaces CLIP, Whisper, VAD, sons, LLM, visages, synthèse vocale
│   ├── discover/              # YouTube : séries en tendance, chaînes officielles, métadonnées, heatmap, commentaires, score
│   ├── market/                # étude TikTok hebdomadaire
│   ├── rights/                # tableau de suivi des licences
│   ├── creators/              # prospection, instantanés, veille, voie express
│   ├── ingest/                # import, SponsorBlock
│   ├── clean/                 # génériques, noirs, silences, découpe des compilations
│   ├── analyze/               # plans, audio, parole, mots, index des scènes
│   ├── highlights/            # échelle de signaux
│   ├── cut/                   # parties, best-of
│   ├── compile/               # compilations thématiques
│   ├── hooks/                 # règles, petit modèle, bandit
│   ├── approve/               # approbation automatique, logos, re-sourçage
│   ├── assign/                # propriété, registre
│   ├── queue/                 # classement par compte, expiration
│   ├── render/                # FFmpeg, canevas, mises en page, bandeau, autocut
│   ├── reframe/               # recadrage intelligent (action des cartoons, visage des créateurs)
│   ├── originality/           # modules base et avancé (voix off, sous-titres)
│   ├── captions/              # sous-titres ASS mot par mot
│   ├── meta/                  # légendes, hashtags, couvertures
│   ├── package/               # outbox/, upload.json, INDEX.html
│   ├── serve/                 # page des posts prêts et ses boutons (rushes serve)
│   └── stats/                 # collecteurs, détection de publication
├── tests/                     # fixtures synthétiques uniquement
└── docker/  (Dockerfile, compose.yaml)
```

### Configuration

Exemples indicatifs, à affiner en codant. La base ne contient que l'état et les résultats.

```yaml
# config/accounts.yaml
brands:
  - {id: saturday_toons, vertical: cartoons, language: en, timezone: America/New_York}
  - {id: toon_tape,      vertical: cartoons, language: en, timezone: America/New_York}
  - {id: instant_culte,  vertical: creators, language: fr, timezone: Europe/Paris}
  - {id: peak_moments,   vertical: creators, language: en, timezone: America/New_York}
accounts:                           # originality : base (défaut) ou advanced, par compte
  - {id: st_tt, brand: saturday_toons, platform: tiktok,    handle: "@saturdaytoons"}
  - {id: st_ig, brand: saturday_toons, platform: instagram, handle: "@saturdaytoons"}
  - {id: tt_tt, brand: toon_tape,      platform: tiktok,    handle: "@toontape", originality: advanced}
  - {id: tt_ig, brand: toon_tape,      platform: instagram, handle: "@toontape"}
  - {id: ic_tt, brand: instant_culte,  platform: tiktok,    handle: "@instantculte"}
  - {id: ic_ig, brand: instant_culte,  platform: instagram, handle: "@instantculte"}
  - {id: pm_tt, brand: peak_moments,   platform: tiktok,    handle: "@peakmoments"}
  - {id: pm_ig, brand: peak_moments,   platform: instagram, handle: "@peakmoments"}
# rotation:                         # facultatif : rushes génère le plan croisé ; une rotation écrite ici l'emporte
#   - {from: 2026-11-16, to: 2026-12-06, saturday_toons: [tom_and_jerry], toon_tape: [oggy]}
creators_by_brand:                  # créateurs : répartition par langue
  instant_culte: [squeezie, byilhan]
  peak_moments: []                  # alimenté par la prospection ; publication seulement après authorized: true
families:                           # bornes : décision de Louis (R6) ; share facultatif (recalé par rushes, écrit = forcé)
  monetizable: {min_s: 65,  max_s: 100}   # part de départ : 1.0 sur chaque plateforme
  long:        {min_s: 120, max_s: 180}   # part de départ : 0.0 (ponctuel)
required_hashtags:                  # décision de Louis (R13) ; {series} et {creator} remplacés au rendu
  cartoons:  {tiktok: ["#fyp", "#nostalgiatok", "#cartoons", "{series}"], instagram: ["#cartoons", "{nostalgia}"]}
  creators:  {tiktok: ["#fyp", "{creator}"], instagram: ["{creator}"]}
layouts:                            # amorce ; rushes ajuste les parts d'après les résultats
  preference: [square, portrait, full]   # ordre de repli quand le recadrage échoue
  # vertical_share: 0.15                 # part de 9:16 tentée en test ; décommenter pour forcer
posts_per_day: {tiktok: 1, instagram: 0.6}
# slots:                            # heure locale de la marque ; sans cette clé, rushes part de ces valeurs et les
#   tiktok: ["15:00", "18:00"]      # recale sur l'activité réelle ; décommenter pour forcer
#   instagram: ["17:00"]
upload_defaults:                    # recopiés dans upload.json
  tiktok: {visibility: public, allow_comments: true, allow_duet: false, allow_stitch: false, ai_generated_label: false}
  instagram: {share_to_feed: true}
```

```yaml
# config/series/tom_and_jerry.yaml
# Seuls id, title, audio et licensed sont obligatoires. Le reste (chaînes, comptes TikTok, requêtes,
# personnages, hashtags tournants) est une amorce ou une correction facultative : rushes le déduit seul.
id: tom_and_jerry
title: "Tom and Jerry"              # titre anglais : donne aussi le hashtag obligatoire #tomandjerry
audio: nonverbal                    # nonverbal | dialogue_en
licensed: false                     # true = publiable (R14) ; false = vitrine
rights:                             # facultatif : suivi de la demande de licence
  holder: "Warner Bros. Discovery"
  status: to_request                # to_request | requested | granted | refused
  requested_at: null
  notes: ""
sources:
  youtube_channels: [{id: "UC...", kind: official}]
  tiktok_accounts: ["@..."]         # amorces du panel de l'étude de marché
  search_queries: ["tom and jerry classic"]
intro_outro:
  reference_frames: ["refs/tom_and_jerry_intro.png"]
  theme_audio: "refs/tom_and_jerry_theme.wav"   # empreinte audio du générique ; une liste donne une variante par époque
characters:
  - {id: tom, refs: ["refs/tom_1.png"]}
  - {id: jerry, refs: ["refs/jerry_1.png"]}
branding:
  display_name: "Tom and Jerry"
  hashtags_pool: ["#slapstick", "#classiccartoons"]
```

Une série ou un créateur trouvé par la prospection n'a pas de fichier YAML : il vit en base comme candidat, passe par tout le pipeline en vitrine, et apparaît dans le tableau de suivi des licences. Louis crée le fichier YAML (et met `licensed: true` ou `authorized: true`) quand il obtient la licence ou l'accord.

```yaml
# config/creators/<id>.yaml
id: createur_exemple
language: fr
youtube_channel: "UC..."
authorized: false                   # true seulement avec campagne ou accord direct (R22)
rights: {status: to_request}        # facultatif, comme pour les séries
campaign: {url: "https://...", required_mentions: ["@createur"], required_hashtags: ["#createur"]}
credit_template: "🎥 {name}"       # toujours le nom du créateur
hashtag: "#createur"                # facultatif : par défaut, nom du créateur sans espaces (R13)
```

```yaml
# config/settings.yaml (extrait)
rate_limit_s: 2
# approval_threshold: {cartoons: 0.7, creators: 0.7}   # sans cette clé : 0,7 recalé par rushes ; décommenter pour forcer
registry: {overlap_tolerance_s: 2, phash_max_distance: 10}
llm: {enabled: true, model: "qwen3-4b-instruct-2507-q4_k_m.gguf", fallback: "qwen3-1.7b-q4_k_m.gguf", threads: 4}
originality:
  default: base                     # base | advanced ; surchargé par compte dans accounts.yaml
  voice: {engine: supertonic, duck_db: -12}
youtube: {cookies: fallback}        # never | fallback (défaut) | always
showcase: {max_episodes_per_week: 4}   # budget de calcul de la vitrine (séries et créateurs non autorisés)
serve: {host: 0.0.0.0, port: 8080, public_origin: "https://rushes.<domaine>"}   # auth : GoDoxy + PocketID
ntfy: {url: "https://ntfy.<domaine>", topic_env: NTFY_TOPIC, token_env: NTFY_TOKEN}
```

Les thèmes de compilation (`themes/`) déclarent des requêtes en texte libre, des filtres (personnages, événements sonores), 4 à 8 passages et une famille. Les règles d'accroche (`hooks/rules.yaml`) déclarent une condition, des gabarits et un type.

### Stockage des fichiers

```text
data/                                  # hors dépôt, monté dans le conteneur, sur le RAID
├── rushes.db
├── cache/models/                      # poids téléchargés une fois
├── cookies/youtube.txt                # compte Google personnel, lecture seule
├── sources/<source_video_id>.mp4
├── episodes/<episode_id>/
│   ├── clean.mp4  scenes.json  audio.json  words.json
│   └── keyframes/<shot_id>.jpg
├── renders/<post_id>.mp4
├── inbox/                             # exports Instagram déposés par Louis
├── outbox/
│   ├── INDEX.html
│   ├── _ecartes/
│   └── <account>/<post_id>/ video.mp4 cover.jpg caption.txt hook.txt hook_2.txt hook_3.txt upload.json
├── outbox_test/                       # posts non publiables (série ou créateur non autorisé)
└── reports/<date>.txt
```

Noms de fichiers par identifiant, jamais par titre. Rétention : `sources/` et `renders/` des épisodes entièrement publiés sont effacés après 30 jours.

## Modèle de données

Une base SQLite unique. Les tables `brands`, `accounts`, `series` et `creators` sont des miroirs de la config, rechargés à chaque commande. Colonnes indicatives ; l'unité est dans le suffixe : les positions et durées internes sont en millisecondes entières (`_ms`) ; les statistiques fournies par les plateformes (`avg_watch_s`, durée d'une vidéo de l'étude de marché) restent en secondes (`_s`). La config est en secondes, convertie au chargement.

| Table | Colonnes clés | Rôle |
| --- | --- | --- |
| `brands` | id, vertical, language, timezone | Miroir de la config |
| `accounts` | id, brand\_id, platform, handle, originality (base / advanced) | Miroir de la config |
| `series` | id, title, audio, licensed, origin (`config` / `prospect`), status (`candidate` / `active` / `dropped`) | Miroir de la config, plus les séries candidates trouvées par la prospection |
| `creators` | id, language, channel\_id, authorized | Miroir de la config |
| `rights` | subject (série ou créateur), holder, status (`to_request` / `requested` / `granted` / `refused`), requested\_at, priority, showcase\_posts | Suivi des licences ; état repris de la config, priorité calculée |
| `derived_values` | key, scope, value\_json, source, confidence, computed\_at | Valeurs déduites par le logiciel (chaînes, panel, hashtags, créneaux, parts, seuils, rotation) |
| `channels` | id, creator\_id, platform\_channel\_id, country, status (`candidate` / `followed` / `authorized` / `rejected`), momentum\_score, discovered\_via | Prospection |
| `channel_snapshots` | channel\_id, taken\_at, subscribers, total\_views, video\_count | Dynamique des chaînes |
| `source_videos` | id, platform, url, channel\_id, kind (`official` / `community`), series\_id ou creator\_id, views, likes, comments, duration\_ms, published\_at, heatmap\_json, sponsorblock\_json, score, status | Découverte |
| `video_signals` | source\_video\_id, kind (`heatmap` / `comment_ts` / `sponsor_highlight` / `laughter` / `quote`), t\_ms, weight, collected\_at | Moments forts, recalculés à chaque passage |
| `episodes` | id, series\_id ou creator\_id, source\_video\_id, provenance, owner\_account\_id, file\_path, sha256, duration\_ms, status (`imported` / `cleaned` / `analyzed`) | Vidéos ingérées ; une compilation officielle est redécoupée en plusieurs épisodes |
| `scenes` | id, episode\_id, start\_ms, end\_ms, loudness, motion, speech, tags\_json, characters\_json, sound\_events\_json, popularity | Index des scènes |
| `scene_vectors` | scene\_id, embedding (BLOB) | Vecteurs CLIP |
| `words` | episode\_id, start\_ms, end\_ms, word, sentence\_id | Transcription mot par mot |
| `face_tracks` | episode\_id, t\_ms, x, y, w, h | Recadrage (créateurs) |
| `segments` | id, episode\_id, kind (`part` / `bestof_item` / `compilation_item`), start\_ms, end\_ms, family, target\_ms, seed, signal\_level, score, status (`proposed` / `approved` / `needs_review` / `rejected`), phash\_json | Passages candidats |
| `assignments` | segment\_id, account\_id, assigned\_at | Registre d'attribution |
| `compilations` | id, kind (`bestof` / `theme`), episode\_id ou theme\_id, account\_id, status | Vidéos composées de plusieurs passages |
| `compilation_items` | compilation\_id, segment\_id, position | Ordre des passages |
| `hooks` | id, segment\_id ou compilation\_id, text, type, engine (`rules` / `llm`), rank, chosen | Propositions d'accroche |
| `posts` | id, account\_id, segment\_id ou compilation\_id, part\_number, family, layout, originality, status, is\_test, rank\_score, video\_path, caption, hashtags, hook\_id, render\_version, planned\_at, published\_at, platform\_url | Un post, de la planification à la publication |
| `metrics` | post\_id, collected\_at, views, likes, comments, shares, avg\_watch\_s, retention\_3s | Séries temporelles |
| `market_videos` | id, account\_handle, views, likes, shares, duration\_s, caption, hashtags, posted\_at, collected\_at | Étude de marché TikTok |
| `hook_stats` | account\_id, hook\_type, alpha, beta | Bandit des accroches |
| `campaign_posts` | post\_id, campaign\_url, submitted\_at | Suivi des soumissions, faites à la main |
| `runs` | id, command, started\_at, ended\_at, status, summary\_json | Historique pour `status` et le rapport |

**Statuts d'un post** : `planned` (attribué, pas rendu) → `rendered` → `ready` (empaqueté) → `published` (détecté par la lecture publique, ou marqué par Louis d'un clic). Sorties possibles : `discarded` (écarté par Louis) et `expired` (7 jours sans publication). Un post de test porte `is_test = 1` : son cycle s'arrête à `ready` et il ne quitte jamais `outbox_test/` (R14, R22).

**Contrainte du registre** : SQLite ne sait pas interdire le chevauchement d'intervalles. `db.registry.can_assign(segment, account)` est l'unique point de contrôle, appelé dans la transaction d'attribution :

- refus si l'épisode appartient à un autre compte (R44) ;
- refus si un passage du même épisode attribué à un autre compte le chevauche de plus de 2 s ;
- refus si le pHash de ses images clés est à moins du seuil d'un passage attribué à un autre compte (même gag venu d'une autre source) ;
- sur le même compte, un passage peut apparaître dans une partie et dans un best-of ou une compilation, jamais dans deux parties ni deux posts simples (R2).

## Pipeline, étape par étape

Le pipeline est commun aux deux verticales ; la section suivante liste ce qui change pour les créateurs. Chaque commande accepte des filtres (`--series`, `--creator`, `--episode`, `--account`, `--limit`) et `--force`. `rushes run` enchaîne tout ce qui est prêt.

| # | Commande | Lit | Produit |
| --- | --- | --- | --- |
| 0 | `discover series` | Tendances YouTube et TikTok du secteur | Séries candidates, tableau de suivi des licences (hebdomadaire) |
| 1 | `discover youtube` | Séries actives et candidates | Chaînes officielles, `source_videos` (métadonnées, heatmap, score) |
| 2 | `market collect` / `market analyze` | Panel de comptes TikTok | `market_videos`, réserve de hashtags par série (hebdomadaire) |
| 3 | `ingest` | `source_videos` retenues, fichiers locaux | `episodes` (`imported`), propriétaire choisi, SponsorBlock lu |
| 4 | `clean` | Épisodes `imported` | `clean.mp4`, compilations redécoupées en épisodes, statut `cleaned` |
| 5 | `analyze` | Épisodes `cleaned` | `scenes`, `scene_vectors`, `words`, `audio.json`, statut `analyzed` |
| 6 | `cut` | Épisodes `analyzed` | `segments` de parties et de best-of, `compilations` de type `bestof` |
| 7 | `compile themes` | Thèmes, index des scènes | `compilations` de type `theme` |
| 8 | `hooks propose` | Segments et compilations | 3 `hooks` chacun |
| 9 | `approve` | Propositions | `approved` ou `needs_review` |
| 10 | `assign` | Approuvés | `assignments`, `posts` en `planned` |
| 11 | `queue rank` | Posts `planned` et `ready` | `rank_score`, liste à rendre, expirations |
| 12 | `render` | Posts à rendre | `renders/<post_id>.mp4` (mise en page, module d'originalité du compte), statut `rendered` |
| 13 | `meta` | Posts `rendered` | Légende, hashtags, couverture |
| 14 | `package` | Posts complets | `outbox/`, `upload.json`, `INDEX.html`, statut `ready` |
| 15 | `serve` (service permanent) | `outbox/`, base | Page des posts prêts ; boutons « Publié », « Écarter », « Accroche 2/3 », « Soumis » (campagnes) ; dépôt de l'export Instagram |
| 16 | `stats collect` / `posts detect-published` | Profils publics, `inbox/` | `metrics`, posts `published`, bandits |

### 0. Séries et créateurs en tendance

- Chaque semaine, rushes cherche seul de nouvelles séries de dessins animés connus à l'international (thème nostalgie) et de nouveaux créateurs : vidéos et chaînes qui montent sur YouTube, hashtags et comptes qui surperforment dans le panel TikTok, séries citées dans les descriptions et titres de compilations qui marchent.
- Une série ou un créateur trouvé devient un candidat en base, sans fichier YAML. Il passe par tout le pipeline en vitrine (mode test), dans la limite d'un budget de calcul hebdomadaire, et apparaît dans le tableau de suivi des licences, classé par potentiel.
- Aucun candidat ne devient publiable sans que Louis crée son fichier YAML avec `licensed: true` ou `authorized: true` (R14, R22, R32).

### 1. Découverte YouTube

- **Chaînes officielles trouvées par le logiciel.** Pour une série, rushes cherche seul sa ou ses chaînes officielles sur YouTube à partir de son titre : une recherche de chaînes (`results?search_query=…&sp=EgIQAg%3D%3D`) donne en une requête le badge de vérification et le nombre d'abonnés ; la cohérence du contenu départage. La liste `sources.youtube_channels` de la série n'est qu'une amorce ou une correction facultative. Chaque chaîne reconnue est enregistrée avec son degré de confiance ; en cas de doute, elle reste `community` (R15) tant que Louis ne la confirme pas dans la config. Les comptes TikTok officiels se lisent dans les descriptions des vidéos officielles.
- **Vidéos les plus populaires d'abord.** Pour chaque chaîne officielle, rushes lit la playlist « Vidéos populaires » (`UULP` + identifiant de chaîne sans « UC » : les 200 vidéos les plus vues, déjà triées), puis ajoute les chaînes communautaires et une recherche par mots-clés. Les mots-clés sont eux aussi produits par le logiciel (titre de la série, personnages, requêtes qui remontent les vidéos les plus vues). Sans téléchargement (`skip_download`, `check_formats` désactivé), sans cookies, 2 s entre requêtes ; les cookies ne servent qu'en secours (R18).
- Métadonnées : vues, likes, commentaires, durée, date, chapitres, `heatmap`. La heatmap et les commentaires manquent souvent, en particulier sur les dessins animés « conçus pour les enfants » (commentaires désactivés) : leur absence est normale.
- Score : voir Algorithmes. Les vidéos au-dessus d'un seuil sont retenues pour l'ingestion.
- Une vidéo communautaire sert de signal (popularité d'un gag) et de matériau de test ; pour la publication, le passage doit être retrouvé dans un épisode officiel (R15).

### 2. Étude de marché TikTok

- Panel de 10 à 30 comptes par série (comptes officiels et comptes de clips), **constitué par le logiciel** et relu chaque semaine avec yt-dlp (curl-cffi, imitation de navigateur), sans cookies, à petit débit. TikTok n'offre ni recherche ni page de hashtag lisible : le panel part des comptes officiels (trouvés dans les descriptions YouTube) et des amorces facultatives de la config, s'élargit par les @mentions des descriptions, et chaque candidat est vérifié par sa page publique `tiktok.com/embed/@compte` (badge, abonnés, compte public, sujet). Même démarche pour un créateur, avec les vidéos du même secteur.
- Les hashtags viennent donc des tendances et de ce qui marche sur des vidéos du même secteur, pas de la config. Jusqu'à la première étude : amorces de la config si elles existent, sinon hashtags tirés du nom de la série ou du créateur.
- Analyse : hashtags et mots-clés des vidéos qui surperforment par rapport à la médiane de leur compte, longueur de légende, durée, heure ; saturation par série.
- Sortie : une réserve de hashtags tournants par série ou créateur, et le hashtag nostalgie d'Instagram ; les hashtags obligatoires (R13) ne viennent pas de l'étude. Les statistiques des propres comptes priment : un hashtag qui ne donne rien chez nous sort de la réserve.
- Un échec de collecte est normal (l'extraction TikTok casse souvent) : journalisé, jamais bloquant.

### 3. Ingestion

- Téléchargement, sha256 (clé d'idempotence), ffprobe, provenance.
- Choix du compte propriétaire selon la rotation (R4, R44).
- Lecture des segments SponsorBlock pour les créateurs (R46).

### 4. Nettoyage

- **Génériques et récapitulatifs** : images de référence par série et empreinte audio du thème musical (corrélation sur le spectre). Les génériques trouvés au milieu d'une compilation officielle servent à la redécouper en épisodes.
- **Cartons de titre** : plans fixes avec texte en début d'épisode.
- **Noirs et silences** (`blackdetect`, `silencedetect`) : pour les cartoons, seulement ceux de plus de 2 s en bordure ; jamais à l'intérieur (les pauses font le rythme d'un gag). Pour les créateurs, voir Autocut.
- **Logo incrusté** : zone d'angle quasi constante sur toute la durée → épisode marqué, ses passages ne seront jamais approuvés.

### 5. Analyse

- Plans (PySceneDetect, `AdaptiveDetector`), loudness à court terme (ebur128), mouvement (différence d'images), parole (Silero VAD).
- Images clés par plan → vecteurs CLIP, étiquettes en zéro-shot (chase, fall, explosion, trap…), personnages par images de référence.
- Événements sonores (PANNs ou YAMNet) regroupés en : impact, explosion, splash, scream, laughter.
- Transcription mot par mot (faster-whisper) pour les séries dialoguées et tous les créateurs, dans la langue de la marque.
- Popularité : pics « Les plus revus » rattachés aux scènes.

### 6. Découpage : parties et best-of

- **Parties** : partition de l'épisode nettoyé en parties consécutives (programmation dynamique, voir Algorithmes), sauf un reliquat trop court abandonné. Une partie couvre toujours des gags entiers, donc les parties sont indépendantes : une partie écartée n'est pas régénérée, les suivantes sont renumérotées.
- **Best-of** : 4 à 8 moments choisis par l'échelle de signaux, remis dans l'ordre chronologique, élargis jusqu'aux bornes du gag, enchaînés avec une transition courte ; durée selon la famille. Le niveau de signal atteint est enregistré.
- Les deux sont toujours produits, même sans aucun signal externe (R43).

### 7. Compilations thématiques

- Un thème (YAML) est cherché dans les scènes des épisodes d'un même compte (R44) et d'un seul sujet (une série ou un créateur) : nom, `#série`, crédit et statut de test sont ceux de ce sujet. Score = pertinence CLIP × popularité × gag complet.
- 4 à 8 gags du plus faible au plus fort, compteur « #3 » incrusté, famille `monetizable`.
- Le programme propose des thèmes en regroupant les scènes proches (clustering des vecteurs) et en relevant les titres de compilations qui marchent ; Louis les nomme et les active.

### 8. Accroches

- Entrées : série ou créateur, personnages, étiquettes, position de la chute, transcription, famille, accroches récentes du compte.
- Types : curiosité, conflit, défi, prédiction, situation, citation (dialogues et créateurs).
- Règles d'abord, puis petit modèle local (voir Algorithmes) ; filtres R11 ; 3 propositions de types différents, classées par le bandit. La première est appliquée, les deux autres restent en `hook_2.txt` et `hook_3.txt`.

### 9. Approbation

- Approuvé si le score dépasse le seuil de la verticale et si R1, R5, R6, R24 et R34 sont respectées ; sinon `needs_review`, jamais rendu. R1 est contrôlée ici en simulation (`can_assign` sans rien écrire) ; l'attribution refait le contrôle dans sa transaction (étape 10).
- Revue facultative de `needs_review` : `review build` génère une page HTML statique (aperçus, bornes, accroches), `review apply` relit le fichier de décisions JSON exporté par la page.
- Une série non `licensed` ou un créateur non `authorized` ne bloque pas l'approbation : ses posts sont produits en mode test (R14, R22).

### 10. Attribution

- Chaque passage approuvé passe par `can_assign` vers le compte propriétaire de son épisode, puis un post `planned` est créé. Refus explicite et journalisé si le registre bloque.

### 11. File de publication

- Toutes les 6 h, score par post candidat et par compte (voir Algorithmes).
- Contraintes : parties d'un même épisode dans l'ordre, série fixe par créneau, part des familles respectée.
- Seuls les posts nécessaires pour couvrir les créneaux des 7 prochains jours, plus 50 % de marge, sont rendus.
- Un post `ready` non publié après 7 jours passe `expired` : rendu supprimé, attribution libérée (R35).

### 12. Rendu

- Canevas 9:16 1080×1920. La vidéo y est placée selon la mise en page du post (R8) :
  - `square` (par défaut) : zone 1:1 recadrée qui suit l'action, sous le bandeau ;
  - `vertical` (de temps en temps, en test) : recadrage 9:16 plein cadre qui suit l'action ;
  - `portrait` : zone 3:4 recadrée, repli quand le carré coupe l'essentiel ;
  - `full` : image entière en pleine largeur sur une copie agrandie et floutée, en tout dernier recours.
- Recadrage intelligent (`reframe/`), plan par plan, sans jamais panoramiquer à travers une coupe : carte d'importance (mouvement par flux optique, panoramique global retiré, saillance, visages) ; fenêtre fixe quand elle couvre l'essentiel de l'action, sinon panoramique lissé (zone morte, vitesse maximale) ; CLIP sur une ou deux images clés pour départager ; repli à la mise en page suivante quand aucune fenêtre ne tient.
- Bandeau « Série — Part N » ou titre du mini best-of au-dessus de la vidéo, accroche pendant les 2 à 3 premières secondes, barre de progression, carte de fin, zones sûres respectées (R9).
- Module d'originalité du compte : `base` (ci-dessus) ou `advanced` (en plus : voix off de 1 à 3 phrases écrites par le modèle de langage et dites par la synthèse vocale locale, son original atténué pendant la voix, sous-titres calés sur la voix). Les deux modules sont toujours prêts ; changer de module ne demande qu'un réglage.
- Sous-titres sur les passages dialogués (cartoons) ou toujours (créateurs) ; `loudnorm` en deux passes vers -14 LUFS ; H.264 High 8 bits yuv420p, 30 images/s constantes, AAC 48 kHz, `+faststart` (R7).
- Le numéro de partie est fixé au rendu ; un changement d'accroche ou de numéro relance un rendu léger (incrustations seulement).
- Clé d'idempotence : post + version du gabarit de rendu.

### 13 et 14. Métadonnées et paquet

- Légende et hashtags par gabarit (R12, R13, R23) ; 3 couvertures candidates (netteté, expression, mouvement), texte ajouté.
- `outbox/<account>/<post_id>/` : vidéo, couverture, légende, accroches, `upload.json` (la traçabilité, R17, reste en base). `INDEX.html` liste les posts par compte et par rang ; c'est aussi un fichier statique lisible sans serveur.
- Sur la page servie par `rushes serve`, chaque post a ses boutons : « Télécharger » (vidéo et couverture, pour l'AirDrop vers l'iPhone), « Copier » par champ, « Publié », « Écarter » (R37), « Accroche 2 » et « Accroche 3 » (nouveau rendu léger). Les posts de créateurs en campagne déjà publiés apparaissent dans une section « À soumettre » avec un bouton « Soumis ». Sans le serveur, les gestes de secours restent : déplacer le dossier dans `outbox/_ecartes/`, renommer `hook_2.txt` en `hook.txt` ; le run suivant le détecte.

### 15 et 16. Publication et statistiques

Voir la section Automatisation.

## Verticale créateurs

Même code, mêmes tables : une vidéo de créateur est ingérée comme un `episode` rattaché à un `creator`. Les étapes choisissent leur stratégie selon la verticale de la marque propriétaire.

| Étape | Cartoons | Créateurs |
| --- | --- | --- |
| Sources | Chaînes des séries | Chaînes des créateurs `authorized` ; vieilles vidéos (catalogue) et nouvelles (veille) |
| Nettoyage | Génériques, cartons, noirs et silences longs en bordure | SponsorBlock (`sponsor`, `selfpromo`, `interaction`, `intro`, `outro`), puis autocut au rendu |
| Moments forts | Pics sonores, action, « Les plus revus » | « Les plus revus », commentaires horodatés, `poi_highlight`, rires, exclamations, phrases fortes |
| Coupe | Fin de gag, creux sonore, jamais pendant la parole | Uniquement en fin de phrase (horodatage mot par mot) ; contexte inclus : du début de la phrase qui pose la situation à la fin de celle du moment fort |
| Cadrage | Mise en page selon R8 (carré par défaut, vertical de temps en temps), recadrage qui suit l'action | Mise en page selon R8 (carré par défaut, vertical de temps en temps, décision de Louis du 2026-11-02), recadrage qui suit le visage (OpenCV YuNet, 2 échantillons par seconde, trajectoire lissée) ; sans visage, recadrage qui suit l'action (R26). |
| Sous-titres | Passages dialogués | Toujours : ASS mot par mot, mot courant surligné, 2 lignes au plus |
| Accroche | Règles et étiquettes | Citation courte de la transcription en priorité, puis règles et petit modèle, dans la langue de la marque |
| Légende | Gabarit par série | Gabarit par créateur, crédit et mentions de campagne ajoutés d'office |
| Formats | Parties, best-of, compilations thématiques | Parties, best-of et compilations, avec les mêmes familles et les mêmes règles TikTok et Instagram que les cartoons |

**Autocut** : intervalles sans parole de plus de 0,6 s (Silero VAD + mots), retirés en gardant 0,15 s de marge de chaque côté, jamais au milieu d'une phrase. Il s'applique au rendu, après le choix des passages, pour que les timecodes du registre restent ceux de la vidéo source. Fait en interne (auto-editor n'est plus publié sur pip).

### Prospection, catalogue et veille

| Commande | Fréquence | Effet |
| --- | --- | --- |
| `creators prospect` | Hebdomadaire | Part de la liste de départ (config), élargit par les invités et chaînes cités dans les titres et descriptions, et par des recherches YouTube de la semaine ; calcule un score de dynamique ; nouvelles chaînes en `candidate` |
| `creators snapshot` | Hebdomadaire | `channel_snapshots` des chaînes candidates et suivies |
| `creators backfill` | Chaque nuit, par lots | Classe les anciennes vidéos des chaînes `authorized` (vues par jour, surperformance) ; leurs pics « Les plus revus » sont stables ; ingère les meilleures |
| `creators watch` | Toutes les 6 h | Détecte les nouvelles vidéos : flux RSS de la chaîne d'abord, liste yt-dlp si le flux échoue ou reste muet plus de 24 h |
| `creators express <video>` | À la détection | Ingestion, transcription, moments forts provisoires (200 commentaires les plus aimés, rires, phrases fortes), 2 ou 3 extraits proposés ; priorité processeur basse, une vidéo à la fois |
| `creators revisit` | 24 h et 72 h après la mise en ligne | Relit la courbe et les commentaires, propose de meilleurs passages et le best-of |

- Une chaîne `candidate` est suivie et analysée d'office si son score de dynamique dépasse le seuil (`followed`), mais n'est jamais publiée sans que Louis la passe `authorized` en éditant le YAML du créateur, source de vérité unique ; `channels.status` n'en est que le miroir et aucune commande ne l'autorise (R32).
- La page Tendances de YouTube n'existe plus depuis juillet 2025 : la prospection ne s'appuie pas dessus. Classements publics (HypeAuditor, vidIQ) consultés à la main seulement.
- La vitesse fait l'intérêt de la veille : la voie express doit proposer ses extraits en moins d'une heure de calcul pour une vidéo de 20 min.
- Une vidéo de la voie express obtient un créneau en plus le jour même ou le lendemain, au-delà du rythme du compte (un post de plus par jour et par compte au plus) ; les posts prêts gardent leur créneau.

### Campagnes de clipping

- Le crédit et les mentions et hashtags exigés par la campagne sont ajoutés à chaque légende ; un post sans eux est refusé (R23).
- Chaque post publié pour une campagne est listé avec son URL dans le rapport hebdomadaire ; la soumission reste manuelle (R28).

**Monétisation** : des extraits de créateurs ont peu de chances d'être jugés originaux par Creator Rewards ; les campagnes de clipping, payées à la vue, sont la source de revenus visée pour cette verticale.

## Algorithmes clés

Tous sont simples, déterministes à graine fixe et testables sans modèle lourd. Poids et seuils sont dans la config, à calibrer sur des données réelles.

### Partition en parties

1. **Points de coupe candidats** : changements de plan (cartoons) ou fins de phrase (créateurs), plus le début et la fin de l'épisode nettoyé.
2. **Coût d'un point** : faible dans un creux de loudness, élevé dans un événement sonore ; interdit pendant la parole et dans les 1,5 s avant un pic « Les plus revus » (R5).
3. **Durée cible** de chaque partie tirée dans la fourchette de sa famille, graine enregistrée.
4. **Programmation dynamique** qui minimise la somme des coûts de coupe plus l'écart au carré à la cible ; bornes dures de la famille (R6). Le dernier morceau trop court est fusionné avec le précédent si la borne haute le permet, sinon abandonné.
5. **Début accrocheur** : bonus si les 2 premières secondes contiennent du mouvement ; sinon décalage au plan suivant si la durée le permet.

### Échelle de signaux du best-of

| Niveau | Signaux | Score d'un moment |
| --- | --- | --- |
| 1 | « Les plus revus », commentaires horodatés, `poi_highlight` SponsorBlock | Somme pondérée des signaux autour du moment |
| 2 | Loudness à court terme, densité de changements de plan, mouvement, rires, exclamations, phrases courtes | Somme pondérée des z-scores calculés sur la vidéo |
| 3 | Aucun signal exploitable | La vidéo est divisée en k tranches égales ; dans chacune, le passage aux bornes les plus propres |

Un niveau est exploitable si au moins un moment dépasse moyenne + 1,5 écart-type ; sinon on descend d'un niveau. Les moments retenus sont élargis jusqu'aux bornes du gag ou de la phrase, puis dédoublonnés (chevauchement de plus de 50 % → on garde le meilleur).

### Score de découverte et de dynamique

- Vidéo : moyenne pondérée des logarithmes de vues par jour (vues / jours depuis la publication, minimum 1), surperformance (vues / médiane des 50 dernières vidéos de la chaîne) et engagement ((likes + 2 × commentaires) / vues).
- Chaîne : vues par jour des 10 dernières vidéos rapportées à sa médiane historique, rythme de publication, évolution hebdomadaire des abonnés.

### Hashtags qui surperforment

Pour chaque hashtag présent dans au moins 10 vidéos du panel : écart moyen de log(vues / médiane du compte) entre les vidéos qui l'utilisent et les autres. Classé par écart, avec le nombre d'occurrences.

### Accroches

1. Caractéristiques du passage (voir Pipeline, étape 8).
2. Règles évaluées dans l'ordre du fichier ; chacune remplit son gabarit si sa condition est vraie.
3. Petit modèle local : reçoit caractéristiques, propositions des règles, types autorisés, contraintes et langue ; sortie JSON contrainte par schéma (`[{text, type}]`), température basse, mode réflexion désactivé, validation Pydantic. Si la sortie est invalide ou trop lente : règles seules.
4. Filtres : moins de 8 mots, liste de mots interdits, ne révèle pas la chute, similarité sous 0,85 (rapidfuzz) avec les 30 dernières accroches du compte.
5. 3 propositions de types différents, ordonnées par le bandit.

### Bandit des types d'accroche

Un couple (alpha, beta) par compte et par type, tirage de Thompson. Succès d'un post : rétention à 3 s au-dessus de la médiane du compte quand une API la fournit ; à défaut, vues rapportées à la médiane du compte au-dessus de 1. Un post écarté compte comme échec (R37).

### File de publication

```text
score = 0.35 × qualité + 0.25 × fraîcheur + 0.15 × tendance + 0.25 × performance apprise − pénalités de diversité
fraîcheur (créateurs) = exp(−âge_source_en_heures / 48) ; 1 pour les cartoons
```

- Qualité : score du passage, niveau de signal (niveau 3 pénalisé), accroche.
- Tendance : vitesse de vues de la vidéo source sur 24 h, hashtags en hausse.
- Performance apprise : résultats du compte par série ou créateur, famille, type de contenu (partie, best-of, compilation), type d'accroche.
- Diversité : pénalité si la même série, le même créateur ou la même famille vient de passer.
- Contrainte dure : une partie n'est jamais classée avant la partie précédente du même épisode.

### Re-sourçage d'un passage communautaire

pHash des images clés du passage comparé par fenêtre glissante aux images clés des épisodes officiels de la série ; trouvé à 0,5 s près → le passage est recréé sur l'épisode officiel ; introuvable → il reste un signal de popularité.

### Empreinte audio du générique

Spectrogramme du thème de référence corrélé en glissement avec la piste de l'épisode ; un pic au-dessus du seuil marque un générique, confirmé par les images de référence quand elles existent.

## Automatisation et exploitation

Le serveur tourne seul ; Louis n'intervient que pour publier, deux fois par semaine, et sur alerte.

### Minuteurs systemd

| Minuteur | Fréquence | Commandes |
| --- | --- | --- |
| `rushes-night` | Chaque nuit à 2 h (heure de Paris) | `run` : découverte, `creators backfill`, ingestion, nettoyage, analyse, découpage, accroches, approbation, attribution, file, rendu, métadonnées, paquet |
| `rushes-watch` | Toutes les 6 h | `creators watch`, `express`, `revisit`, `queue rank` |
| `rushes-stats` | Toutes les 6 h | `stats collect`, `posts detect-published`, mise à jour des bandits |
| `rushes-weekly` | Dimanche 3 h | `discover series`, `creators prospect`, `creators snapshot`, `market collect`, `market analyze`, recalage des créneaux, parts et seuils, tableau de suivi des licences, rapport hebdomadaire |
| `rushes-maintenance` | Lundi 4 h | Mise à jour de yt-dlp, `yt-dlp-ejs` et curl-cffi dans une image candidate, suite de tests sans réseau, bascule seulement si tout passe (R38) ; sauvegarde SQLite (`VACUUM INTO`, contrôle d'intégrité, rotation, copie hors du RAID) ; rétention et nettoyage du disque |

- Chaque minuteur est un service systemd `oneshot` qui lance `docker compose run --rm -T rushes rushes <commande>` ; `OnFailure=` envoie une alerte. `rushes serve` est un service Compose permanent.
- La mise à jour de yt-dlp est aussi déclenchée hors calendrier quand un collecteur YouTube ou TikTok échoue deux fois de suite. Le serveur ne modifie pas le dépôt : le nouveau `uv.lock` est proposé à Louis dans le rapport.
- Un verrou fichier (`flock`) empêche deux commandes lourdes de tourner en même temps ; la voie express signale son attente et le rendu en cours lui cède la place entre deux posts.
- Limites CPU et mémoire dans `compose.yaml` (`cpus`, `mem_limit` de 6 Go, `cpu_shares`) et `nice` au démarrage du conteneur, pour que les autres services du serveur restent fluides ; un seul modèle lourd chargé à la fois, 4 threads. Le `Nice=` de systemd ne s'applique pas au conteneur.

### Charge du serveur

Le volume est modeste : 16 vidéos courtes par jour au maximum pour les 8 comptes. Ordres de grandeur à mesurer dès la phase 1 sur le processeur réel (6 cœurs, sans GPU) :

| Travail | Estimation par unité | Par jour |
| --- | --- | --- |
| Rendu d'une vidéo de 1 min (x264, fond flou) | 1 à 3 min | Moins d'une heure |
| Transcription d'une vidéo de créateur de 20 min (Whisper small int8) | 5 à 15 min | 1 à 2 h selon la veille |
| Analyse d'un épisode de cartoon de 7 min (plans, CLIP, sons) | Quelques minutes | Faible |
| 3 accroches avec un modèle 3B quantifié | 10 à 30 s | Quelques minutes |

Ce sont des estimations, pas des mesures : la tâche de mesure (phase 1) les remplace par de vrais chiffres. Si un poste déborde : Whisper base au lieu de small, modèle de langage 1,7B, preset x264 plus rapide.

### Publication manuelle

Tous les posts sont publiés à la main par Louis, pour tous les comptes et pour toujours (R48). Rien n'est envoyé à une plateforme ni à un outil de publication.

- Chaque post est préparé dans `outbox/<account>/<post_id>/` : la vidéo, la couverture et `upload.json`, qui contient tout ce qu'il faut saisir dans l'appli, dans l'ordre de l'écran, avec le créneau conseillé.
- Deux fois par semaine, Louis ouvre sur son Mac la page servie par `rushes serve` (derrière son reverse proxy et le SSO PocketID). Il écarte ou change d'accroche d'un clic, télécharge vidéos et couvertures, les envoie à son iPhone par AirDrop avec les textes, puis programme les posts dans les applis : Instagram (programmation dans l'appli, jusqu'à environ 75 jours) et TikTok (TikTok Studio si la programmation est disponible sur un compte personnel, sinon brouillon publié au créneau).
- La publication est détectée automatiquement (`posts detect-published`) ; le bouton « Publié » sert seulement à l'accélérer.
- Les parties d'un épisode doivent être programmées dans l'ordre : la page les présente dans cet ordre et `upload.json` le rappelle (`order_note`).

```json
{
  "post_id": "p_000123",
  "platform": "tiktok",
  "account": "@saturdaytoons",
  "publish_at": {"brand_local": "2026-11-16 15:00 America/New_York", "paris": "2026-11-16 21:00"},
  "order_note": "Publier après la partie 1 (p_000122)",
  "files": {"video": "video.mp4", "cover": "cover.jpg", "cover_frame_s": 3.2},
  "caption": "Tom really thought this plan would work 😭 Tom and Jerry, Part 2. Who wins? 👇 #fyp #nostalgiatok #cartoons #tomandjerry #slapstick",
  "hashtags": ["#fyp", "#nostalgiatok", "#cartoons", "#tomandjerry", "#slapstick"],
  "hook": {"applied": "Wait for the ending 😭", "alternatives": ["Tom vs Jerry: who wins?", "He did NOT see that coming"]},
  "settings": {"visibility": "public", "allow_comments": true, "allow_duet": false, "allow_stitch": false, "ai_generated_label": false, "branded_content": false},
  "mentions": [],
  "campaign": null,
  "checklist": ["Couverture : image à 3,2 s ou cover.jpg", "Vérifier que la légende tient avant « plus »"]
}
```

Pour Instagram, `settings` contient `share_to_feed`, et `mentions` le compte du créateur à identifier. Pour un post de campagne, `campaign` reprend l'URL et les mentions exigées ; l'étiquette de contenu de marque reste à vérifier selon les règles de chaque campagne. Quand le module avancé a ajouté une voix de synthèse, `settings.ai_generated_label` est vrai et la checklist rappelle d'activer l'étiquette « contenu généré par IA » si l'appli la demande.

- La page de `rushes serve` ne fait que lire `outbox/` et la base, et écrire cinq décisions de Louis : publié, écarté, accroche choisie, campagne soumise (posts de créateurs en campagne), export Instagram déposé (fichier CSV seulement, 5 Mo au plus, rangé dans `data/inbox/`). Elle ne publie rien. L'authentification est entièrement assurée par le reverse proxy de Louis (GoDoxy, SSO PocketID) ; `serve` ne vérifie aucune identité, et n'est donc joignable que par le proxy (aucun port publié sur l'hôte). GoDoxy met le conteneur en veille au repos et le réveille à la première requête : `serve` démarre en moins de 2 s, sans charger de modèle. Les actions sont protégées contre les requêtes croisées (`Origin`).
- Fichier vidéo pensé pour le transfert vers l'iPhone : H.264 High 8 bits, yuv420p, 30 images/s constantes, AAC 48 kHz, `+faststart`.

### Statistiques et détection de publication

| Plateforme | Par défaut, sans clé | Plus tard, en option |
| --- | --- | --- |
| TikTok | yt-dlp lit le profil public, sans cookies (curl-cffi, imitation de navigateur) : vues, likes, commentaires, partages, enregistrements par vidéo (chiffres arrondis par TikTok) | Display API (`video.list`) |
| Instagram | Export CSV hebdomadaire de Meta Business Suite déposé par Louis sur la page de `serve` (rangé dans `inbox/`), reconnu par ses en-têtes (traduits dans la langue de l'interface : français et anglais acceptés) | API Instagram : durée regardée, taux de sortie à 3 s |

- `posts detect-published` rapproche une vidéo publique d'un post `ready` du même compte si sa date tombe dans les 48 h autour du créneau et si sa description commence comme la légende ; sinon elle est signalée dans le rapport, jamais ignorée.
- Import d'un export : un adaptateur par format, reconnu par ses colonnes ; format inconnu → refus avec la liste des colonnes trouvées. CSV manuel de secours : `post_url, date, views, likes, comments, shares, avg_watch_s, retention_3s`.
- Originalité : le rapport hebdomadaire rappelle de vérifier le Statut du compte Instagram et les vidéos « non éligibles au fil Pour toi » sur TikTok ; une chute brutale des vues d'un compte par rapport à sa médiane déclenche une alerte, pour que Louis décide de passer ce compte au module avancé.
- Les jetons d'API futurs sont chiffrés en base ; jamais de cookies ni d'identifiants des comptes de publication (R36).

### Notifications ntfy

- Résumé quotidien : posts prêts par compte, jours de stock restants.
- Alertes : YouTube exige une connexion (bascule sur les cookies de secours) ou cookies expirés, collecteur en échec deux fois de suite, mise à jour de yt-dlp refusée par les tests, disque au-dessus de 85 %, chute des vues d'un compte, série ou créateur très bien classé mais non autorisé (avec sa ligne du tableau de suivi des licences), mise à jour d'un projet tiers (R41).
- Sur iPhone, le serveur ntfy relaie par `upstream-base-url: https://ntfy.sh` pour que les notifications arrivent tout de suite ; rushes publie avec un jeton (`auth-default-access: deny-all`).
- Événement : nouvelle vidéo d'un créateur suivi, avec ses premiers extraits prêts.

### Sortie console

Deux modes choisis automatiquement : affichage soigné (Rich) dans un terminal, JSON ligne par ligne sous systemd ou Docker (`sys.stdout.isatty()`, forçable par `--plain` ou `--json`).

| Élément | Rendu dans le terminal |
| --- | --- |
| En-tête | Panneau d'une ligne : commande, compte ou série, mode (`--test` en rouge « NE PAS PUBLIER »), version et commit |
| Étapes | Une ligne par étape avec état (✓ terminé, ↷ déjà fait, ⚠ avertissement, ✗ erreur) et durée |
| Travaux longs | Barres de progression : une globale, une par fichier en cours, vitesse et temps restant ; progression FFmpeg lue sur `-progress pipe:1` |
| Résultats | Tableaux : file par compte (rang, série, partie, durée, accroche, créneau), prospects, statistiques |
| Erreurs | Message court en français avec la piste de solution ; trace complète avec `--verbose`, toujours dans le journal |
| Fin | Récapitulatif : posts prêts par compte, avertissements, durée totale, chemin de `INDEX.html` |

- Couleurs à sens fixe (vert succès, jaune avertissement, rouge erreur ou test) ; `NO_COLOR` respecté ; lisible en SSH depuis un téléphone.
- `rushes status` : derniers runs de chaque minuteur, file par compte, alertes, espace disque, en un écran.
- Chaque nuit, un rapport texte dans `reports/<date>.txt` (posts prêts, erreurs, refus du registre, posts de campagne à soumettre).

### Plus tard : tableau de bord

Un tableau de bord complet et compatible téléphone, qui étendra la page de `rushes serve` : une colonne par compte, aperçu, accroche et alternatives, onglet `needs_review`, onglet santé, suivi des licences. Hors périmètre pour l'instant.

## Code tiers et mises à jour

Aucune évolution d'un projet tiers n'est appliquée automatiquement, sauf yt-dlp (avec `yt-dlp-ejs` et curl-cffi) quand toute la suite de tests passe.

| Projet | Usage | Règle |
| --- | --- | --- |
| [AI-Youtube-Shorts-Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator) (MIT) | Source d'idées seulement : découpage des longues vidéos en morceaux qui se chevauchent, déduplication des candidats, cache de transcription, recadrage lissé. Réécrits dans rushes, avec tests, et seulement s'ils sont adaptés | Aucun code copié : pas de `vendor/` pour l'instant (son code est défectueux et n'est sous MIT que depuis le commit `9c7a33e`) |
| yt-dlp, `yt-dlp-ejs`, curl-cffi | Dépendances Python ; Deno dans l'image | Mise à jour hebdomadaire, ou anticipée après deux échecs de collecte, si les tests passent (R38) |
| Autres dépendances | Versions verrouillées par uv | Mises à jour à la main, quand c'est utile |

Si du code tiers est copié un jour, R40 et R41 s'appliquent : `vendor/`, `VENDOR.md`, commit figé, et une commande `vendor check` à créer alors.

## Règles R1 à R50 (R31 et R42 supprimées)

Chaque règle a au moins un test automatique, dont le nom cite la règle (`test_r1_...`). Si un test échoue, c'est le code ou le test qui change, jamais la règle, sauf décision explicite de Louis.

**Unicité et attribution**

- **R1** : un passage attribué à un compte ne peut pas l'être à un autre : refus si un passage du même épisode attribué ailleurs le chevauche de plus de 2 s, ou si son pHash est proche (seuil configurable) de celui d'un passage attribué ailleurs. Seule `db.registry.can_assign` fait ce contrôle, dans la transaction d'attribution.
- **R2** : sur un même compte, un passage peut apparaître dans une partie et dans un best-of ou une compilation, jamais dans deux parties ni dans deux posts simples.
- **R3** : rien n'est attribué, rendu ni empaqueté sans statut `approved` (automatique ou par Louis) ; rien n'est programmé ni publié sans action de Louis.
- **R4** : un compte ne reçoit de nouveaux épisodes que des séries que la rotation lui assigne pour la période en cours. La propriété d'un épisode déjà ingéré est définitive (R44) : il se termine sur son compte, même après une rotation.
- **R44** : un épisode et tous ses contenus dérivés (parties, best-of) appartiennent à un seul compte ; les compilations d'un compte ne puisent que dans ses épisodes ; R1 reste le garde-fou par passage.
- **R27** : R1 et R44 s'appliquent à toutes les marques, cartoons comme créateurs.

**Découpage et durée**

- **R5** : jamais de coupe pendant la parole détectée, ni dans les 1,5 s avant un pic « Les plus revus ».
- **R6** : durées en secondes, bornes incluses, définies par famille dans la config ; un passage hors bornes n'est jamais proposé ; cible tirée au hasard avec graine enregistrée. Toute famille a `min_s` > 60 : la config qui en déclare une plus courte est refusée.
- **R24** : pour les créateurs, coupes uniquement en fin de phrase ; jamais au milieu d'un mot ni d'une phrase.
- **R43** : chaque épisode produit toujours des parties consécutives et un best-of, dans l'ordre chronologique ; le best-of descend l'échelle de signaux et enregistre le niveau atteint.
- **R45** : autocut des silences réservé aux créateurs (plus de 0,6 s sans parole, marge 0,15 s, jamais au milieu d'une phrase). Pour les cartoons, seuls génériques, récapitulatifs, cartons, et noirs ou silences de plus de 2 s en bordure sont retirés.
- **R46** : quand SponsorBlock a des données, `sponsor`, `selfpromo`, `interaction`, `intro` et `outro` sont retirés et `poi_highlight` sert de signal ; leur absence n'est jamais une erreur.

**Rendu**

- **R7** : MP4 1080×1920, H.264 High 8 bits yuv420p, 30 images/s constantes, AAC 48 kHz, `+faststart`, loudness intégrée -14 LUFS ± 1 LU.
- **R8** : la vidéo est placée selon une mise en page parmi `square` (1:1, par défaut), `vertical` (9:16, en test), `portrait` (3:4, repli) et `full` (image entière sur fond flou, tout dernier recours) ; tout recadrage suit l'action (cartoons) ou le visage (créateurs), plan par plan ; quand aucune fenêtre ne garde l'action, on passe à la mise en page suivante, jamais à un recadrage qui la coupe.
- **R9** : bandeau « Série — Part N » dans le tiers haut ; le nombre total de parties n'apparaît nulle part (bandeau, titre, légende, couverture, carte de fin) ; numérotation sans trou dans l'ordre de publication ; aucun texte important en bas ni le long du bord droit (marges en config).
- **R10** : sous-titres dans la langue de la marque, incrustés sur les passages dialogués des cartoons.
- **R25** : sous-titres obligatoires sur toute vidéo de créateur : mot courant surligné, 2 lignes au plus, zone sûre.
- **R26** : pour les créateurs, le recadrage suit le visage ; sans visage détecté, il suit l'action comme pour les cartoons, avec le même ordre de repli (R8).
- **R31** : supprimée : rien n'est publié sur YouTube.

**Textes**

- **R11** : accroche dans la langue de la marque, moins de 8 mots, ne révèle pas la chute, similarité inférieure à 0,85 avec les 30 dernières du compte ; 3 propositions de types différents.
- **R12** : nom de la série ou du créateur dans les 80 premiers caractères sur TikTok, les 125 premiers sur Instagram.
- **R13** : 5 hashtags au maximum sur les deux plateformes ; jamais #viral ni #foryou ; pas de #fyp sur Instagram. Hashtags obligatoires (décision de Louis, dans la config) : TikTok cartoons `#fyp #nostalgiatok #cartoons #<nom anglais de la série>` ; Instagram cartoons `#cartoons` + un hashtag nostalgie choisi par l'étude de marché ; TikTok créateurs `#fyp #<créateur>` ; Instagram créateurs `#<créateur>`. Le reste vient du contenu et des tendances.
- **R21** : une marque a une seule langue ; transcription, sous-titres, accroches et légendes sont dans cette langue, sans mélange.
- **R23** : chaque légende de créateur contient son crédit et toutes les mentions et hashtags exigés par sa campagne ; sinon refus.

**Autorisations et provenance**

- **R14** : `package` n'écrit dans `outbox/` que les posts d'une série `licensed: true` ; les autres (séries sans licence et séries candidates) vont dans `outbox_test/` avec « NE PAS PUBLIER » dans la légende, servent de vitrine et figurent dans le tableau de suivi des licences.
- **R22** : même règle pour un créateur non `authorized`.
- **R15** : un passage `community` n'est jamais rendu tel quel ; il est re-sourcé vers l'épisode officiel, sinon il reste un signal.
- **R34** : un logo incrusté détecté bloque le passage, quelle que soit sa provenance.
- **R28** : un post de campagne est enregistré avec son URL pour la soumission ; le logiciel ne soumet rien.
- **R32** : une chaîne ou une série découverte par la prospection n'est jamais publiée sans que Louis crée son fichier YAML avec `authorized: true` ou `licensed: true`.

**Automatisation**

- **R29** : chaque chaîne `authorized` est contrôlée au moins toutes les 6 h ; une nouvelle vidéo entre en voie express dans l'heure.
- **R30** : la voie express ne saute aucune étape : approbation (R3, R33) et registre (R1) comme les autres.
- **R33** : approbation automatique seulement si le score dépasse le seuil de la verticale et si R1 (contrôlée en simulation par `can_assign`, refaite à l'attribution), R5, R6, R24 et R34 sont respectées ; sinon `needs_review`, jamais rendu.
- **R35** : un post `ready` non publié après 7 jours passe `expired` : rendu supprimé, attribution libérée.
- **R37** : un post écarté (bouton « Écarter » ou dossier déplacé dans `outbox/_ecartes/`) libère son passage, compte comme échec pour le bandit, et ce passage n'est plus proposé au même compte.
- **R42** : supprimée : il n'y a plus de Postiz.
- **R48** : rien n'est jamais envoyé à une plateforme, à Postiz ni à une API de publication ; tous les posts sont préparés dans `outbox/` avec `upload.json` et publiés à la main par Louis.
- **R49** : les comptes TikTok sont des comptes personnels ; aucune fonction du logiciel n'exige un compte Entreprise.
- **R50** : le module d'originalité (`base` ou `advanced`) se choisit par compte dans la config ; les deux modules sont testés et prêts, et changer de module ne demande aucune modification du code.

**Exploitation et sécurité**

- **R16** : relancer une commande sans `--force` ne modifie rien de ce qui est déjà fait.
- **R17** : chaque post garde sa traçabilité : épisode, provenance, timecodes, graine, niveau de signal, version du gabarit de rendu, type et moteur de l'accroche.
- **R18** : collecte à petit débit (2 s entre requêtes par défaut), en mode métadonnées sans aucun téléchargement de média (`skip_download`, `check_formats` désactivé) ; pas de cookies par défaut ; les cookies d'un compte Google ne servent qu'en secours, à la découverte YouTube et au téléchargement des vidéos retenues (6 au plus par run), et leur usage déclenche une alerte.
- **R36** : statistiques et détection de publication par lecture publique déconnectée ou export fourni par Louis ; jamais les cookies ni les identifiants des comptes de publication.
- **R19** : chaque modèle est chargé une fois par étape, jamais par élément ; un seul modèle lourd en mémoire à la fois.
- **R39** : aucune API payante ni service d'IA distant ; aucun modèle de langage pour classer les moments forts ; le seul modèle de langage est celui des accroches et du commentaire de la voix off, local et désactivable (sans lui : accroches par règles, module avancé sans voix off).
- **R40** : le code tiers vit dans `vendor/`, figé sur un commit, avec sa licence et `VENDOR.md`.
- **R41** : une évolution d'un projet tiers suivi est signalée, jamais appliquée seule.
- **R38** : une mise à jour de dépendance n'est déployée que si toute la suite de tests passe ; sinon alerte et retour à la version précédente.
- **R47** : console Rich dans un terminal, JSON ligne par ligne sinon ; seul `rushes.ui` importe Rich (l'aide `--help` rendue par Typer en est exemptée) ; aucun secret (clé, cookie, jeton) affiché ou journalisé, même tronqué.
- **R20** : les tests n'accèdent ni au réseau ni à `data/` ; `tmp_path` et vidéos synthétiques uniquement.

## Plateformes et bibliothèques

Valeurs relevées en octobre 2026, à garder dans la config.

| Élément | TikTok | Instagram Reels |
| --- | --- | --- |
| Format | 9:16, 1080×1920 | 9:16, 1080×1920 |
| Durée maximale | Bien au-delà de 3 min | 3 min |
| Texte | Légende 4 000 caractères, environ 80 visibles | Légende 2 200, environ 125 visibles |
| Hashtags | Pas de limite stricte ; 5 au maximum chez nous (R13) | 5 au maximum (depuis décembre 2025), légende et premier commentaire confondus |
| Couverture | Choisie à l'upload | Recadrée 4:5 dans le fil, 3:4 sur la grille |
| Type de compte | Personnel (seul éligible à Creator Rewards) | Professionnel, catégorie Créateur |
| Monétisation | Creator Rewards : 18 ans, compte personnel, vidéos de plus d'une minute, contenu original, 10 000 abonnés, 100 000 vues sur 30 jours ; France éligible (sources tierces) | Hors périmètre |
| Originalité | Contenu reproduit ou « combiné de plusieurs sources avec peu d'apport » exclu du fil Pour toi et de Creator Rewards ; signalé vidéo par vidéo | Depuis le 30 avril 2026 : perte des recommandations si une part significative des posts sur 30 jours reprend le contenu d'autrui ; visible dans le Statut du compte |
| Programmation | Appli TikTok Studio (10 à 30 jours) ; disponibilité sur compte personnel à tester ; sinon brouillons dans l'appli | Dans l'appli, jusqu'à environ 75 jours |
| Comptes par téléphone | Environ 3 dans le sélecteur (non officiel) | 5 |

| Bibliothèque | À savoir |
| --- | --- |
| PySceneDetect | Version 0.7.2 (octobre 2026) : refonte avec ruptures ; `video_manager` supprimé, modules renommés (`scene_detector` → `detector`, `video_splitter` → `output.video`). La plupart des tutoriels en ligne sont périmés. La 0.7.2 a changé des seuils par défaut d'autres détecteurs : fixer les paramètres et épingler la version. API : `detect(chemin, AdaptiveDetector(adaptive_threshold=3.0, min_scene_len=15, window_width=2, min_content_val=15.0), backend="opencv")` |
| yt-dlp | Comme bibliothèque : `YoutubeDL(opts).extract_info(url, download=False)` avec `skip_download`, `check_formats=False`, `ignore_no_formats_error=True`, `sleep_interval_requests`. Exige Deno et `yt-dlp-ejs` pour YouTube, curl-cffi et `impersonate` (chrome) pour TikTok. Vidéos les plus vues d'une chaîne : playlist `UULP…` ; recherche de chaînes : filtre `sp=EgIQAg%3D%3D`. `heatmap` et commentaires souvent absents ; erreurs levées en `DownloadError`. L'extraction TikTok casse régulièrement : un échec de collecte est journalisé, jamais bloquant |
| SponsorBlock | API publique directe (`/api/skipSegments`), `actionTypes` avec `poi` pour obtenir `poi_highlight` |
| FFmpeg | `loudnorm` en deux passes (`linear=true`, lire `input_i`, `input_tp`, `input_lra`, `input_thresh`, `target_offset`) ; `blackdetect`, `silencedetect`, `ebur128` ; fond flou calculé à un quart de la résolution ; sous-titres ASS via `ass` (libass), un événement par mot pour le surlignage ; progression par `-progress pipe:1` |
| open\_clip | ViT-B-32 `datacomp_xl_s13b_b90k` (safetensors) ; vecteurs normalisés ; images par lots ; zéro-shot avec des phrases du type « a cartoon character falling down » |
| faster-whisper | CPU : base ou small, `compute_type="int8"`, langue forcée à celle de la marque, horodatage par mot ; `vad_filter=False` et segments de parole de Silero passés par `clip_timestamps` (le VAD interne de faster-whisper reste désactivé : le VAD du projet est celui de Silero) ; jamais `BatchedInferencePipeline` |
| Silero VAD | Paquet `silero-vad`, `load_silero_vad(onnx=True)` (chemin ONNX, sans `torch.jit.load`), entrée mono 16 kHz décodée par FFmpeg |
| Sons | sherpa-onnx avec un modèle CED (AudioSet) ; étiquettes regroupées en événements du projet ; repli PANNs CNN14 réécrit |
| Visages | OpenCV YuNet (`face_detection_yunet_2026may.onnx`), léger sur CPU |
| Recadrage | Flux optique Farneback et saillance (OpenCV contrib), lissage de trajectoire par `scipy` ; CLIP sur images clés pour départager |
| Synthèse vocale | sherpa-onnx avec Supertonic 3 (français et anglais) ; repli Kokoro (anglais) ; pas d'horodatage par mot : l'audio généré est retranscrit par faster-whisper |
| Modèle de langage | llama-cpp-python dans le processus (compilé avec AVX2, sans AVX-512) ; Qwen3-4B-Instruct-2507 Q4\_K\_M, repli Qwen3-1.7B ; sortie contrainte par `response_format={"type": "json_object", "schema": …}`, validation Pydantic. La contrainte garantit la syntaxe, pas le contenu |
| Recherche vectorielle | numpy (cosinus) sur quelques milliers de scènes ; sqlite-vec seulement si nécessaire |
| Divers | `imagehash` (pHash), `rapidfuzz` (similarité des accroches), Pydantic v2, Typer, Rich, Jinja2 |

## Plan de développement

Six phases ; on ne passe à la suivante que si sa porte est franchie. Tout est terminé avant le premier post (décision de Louis). Une tâche par session Claude Code, chacune avec un critère vérifiable par un test ou une commande. Le plan d'implémentation détaillé est dans `docs/plans/` (`00-master.md`, puis un fichier par phase).

| Phase | Objectif | Porte de sortie |
| --- | --- | --- |
| 1. Squelette de bout en bout | Un épisode local → parties rendues dans `outbox/`, sans modèle | `rushes run` produit un `outbox/` complet sur la vidéo synthétique et sur 2 vrais épisodes ; temps mesurés sur le serveur |
| 2. Contenu intelligent | Découverte, best-of, analyse complète, accroches, approbation, file, recadrage, module avancé | Une semaine de stock produite seule pour les 4 comptes cartoons, dans les deux modules d'originalité |
| 3. Publication et mesure | Page des posts prêts, statistiques, minuteurs, notifications, recalage | Avant le lancement : collecteurs verts sur données enregistrées, minuteurs actifs 3 jours sur le serveur sans intervention, page utilisable depuis le Mac. Les critères qui exigent de vrais posts sont vérifiés pendant le rodage (tâche 41) |
| 4. Créateurs | Toute la verticale créateurs | Une nouvelle vidéo d'un créateur donne des extraits prêts en moins d'une heure |
| 5. Enrichissements | Compilations thématiques, étude de marché, revue, découverte des séries en tendance, suivi des licences | Panel et hashtags générés seuls ; une série candidate trouvée seule produit sa vitrine |
| 6. Mise en production | Déploiement, répétition générale, lancement, rodage | Répétition générale de 7 jours sans violation des règles ; checklist de lancement cochée ; critères du rodage vérifiés sur les vrais posts |

| # | Phase | Tâche | Critère d'acceptation |
| --- | --- | --- | --- |
| 1 | 1 | Squelette : uv, Typer, config Pydantic, SQLite et migrations, `paths` | `rushes --help` liste les commandes ; une config invalide est refusée avec un message clair |
| 2 | 1 | `ui` (Rich et JSON) et `status` | Même commande : affichage soigné en terminal, JSON valide sinon ; aucun secret dans aucune sortie (R47) |
| 3 | 1 | `core` (intervalles, timecodes, tirages) et `db.registry` | Tests R1, R2, R44 : chevauchement, tolérance, propriétaire, même compte |
| 4 | 1 | `ingest` et `clean` (ffprobe, sha256, noirs, silences, images de référence) | La vidéo synthétique est nettoyée aux instants attendus ; rien d'intérieur retiré pour un cartoon (R45) |
| 5 | 1 | `analyze` v1 : plans, loudness, mouvement, parole | `scenes.json` et `audio.json` conformes au schéma |
| 6 | 1 | `cut` parties : programmation dynamique et familles | Tests de référence verts ; aucune coupe pendant la parole (R5, R6) |
| 7 | 1 | `render` v1 : canevas, mises en page `full` et `square` centrée, bandeau « Part N » ou titre du best-of, barre, carte de fin, loudnorm, module d'originalité de base | Contrôles ffprobe verts (R7, R8) ; aucun total affiché (R9) |
| 8 | 1 | `meta`, `package`, `INDEX.html`, `assign` simple, `run` | `outbox/` complet ; posts non autorisés dans `outbox_test/` (R14) ; hashtags obligatoires présents (R13) |
| 9 | 1 | Image Docker et mesure des temps sur le serveur | Image construite (Deno, FFmpeg, llama-cpp compilé) ; rapport : temps d'analyse, de rendu et de transcription par minute de vidéo, mémoire de pointe |
| 33 | 1 | Config v2 : Postiz retiré, hashtags obligatoires, mises en page, module d'originalité par compte, suivi des licences, cookies de secours, vitrine, `serve` | Config d'exemple valide ; ancienne clé `postiz` refusée avec un message clair ; migration de la base sans perte |
| 42 | 1 | Essai de faisabilité Python 3.14 (d'abord essayé en 3.15), juste après la tâche 33 : image avec toutes les dépendances lourdes, une inférence par modèle sur le serveur | Rapport go/no-go par bibliothèque (llama-cpp-python, open_clip, faster-whisper, sherpa-onnx, Silero, OpenCV) avec le pic mémoire de chaque modèle ; contournement décidé pour tout échec avant la tâche 2 |
| 10 | 2 | `discover youtube` : chaîne officielle trouvée seule, vidéos les plus vues, métadonnées, heatmap, commentaires, score | Classement reproductible sur des métadonnées enregistrées ; chaîne officielle identifiée sur des résultats de recherche enregistrés |
| 11 | 2 | Nettoyage v2 : empreinte audio du générique, découpe des compilations officielles, logos | Une compilation de test est redécoupée en ses épisodes ; un logo simulé est détecté |
| 12 | 2 | `analyze` v2 : CLIP, événements sonores, transcription, index | Recherche en texte libre qui retrouve un gag connu dans le top 5 |
| 13 | 2 | Best-of et échelle de signaux | Sans signal : best-of de niveau 3 ; avec pics simulés : niveau 1 (R43) |
| 14 | 2 | Accroches : règles, petit modèle local, filtres | Sortie JSON toujours valide sur 100 essais ; filtres R11 respectés ; repli sur les règles si le modèle est désactivé |
| 15 | 2 | Approbation automatique et re-sourçage communautaire | Aucun passage avec logo ou hors règle approuvé (R33, R34) ; passage retrouvé à 0,5 s près (R15) |
| 16 | 2 | Rotation générée, propriété, file, rendu à la demande, renumérotation, expiration | Rotation reproductible et surchargée par la config ; classement reproductible ; parties dans l'ordre ; une partie écartée renumérote les suivantes ; un post expiré libère son passage (R35) |
| 34 | 2 | Recadrage intelligent et mises en page `square`, `vertical`, `portrait` | Sur des vidéos synthétiques à cible mobile : cible dans le cadre 95 % du temps, aucun panoramique à travers une coupe, repli dans l'ordre de R8 |
| 35 | 2 | Module d'originalité avancé : commentaire écrit par le modèle, voix off locale, son atténué, sous-titres calés | Bascule d'un compte par la config seule (R50) ; voix dans la langue de la marque (R21) ; loudness conforme (R7) ; repli sans voix si le modèle est désactivé |
| 17 | 3 | Collecteurs TikTok et Instagram, et `detect-published` | Tests sur métadonnées enregistrées et un vrai export Instagram (en-têtes français et anglais) ; un post publié est reconnu seul |
| 18 | 3 | Bandit des accroches et des mises en page | Les types et mises en page gagnants passent devant sur des données simulées, les autres restent testés |
| 19 | 3 | Supprimée (Postiz abandonné) | — |
| 20 | 3 | Minuteurs, verrou, maintenance, sauvegarde, ntfy | Une mise à jour de yt-dlp qui casse les tests n'est pas déployée (R38) ; les alertes arrivent sur l'iPhone |
| 21 | 3 | Supprimée (plus de code tiers copié) | — |
| 36 | 3 | `rushes serve` : page des posts prêts, téléchargement, copie, boutons « Publié », « Écarter », « Accroche 2/3 » | Aucun port de `serve` publié sur l'hôte ; démarrage sous 2 s sans modèle ; requête croisée refusée ; « Écarter » applique R37 ; « Accroche 2 » déclenche un rendu léger |
| 37 | 3 | Recalage autonome des créneaux, parts de familles, seuils et mises en page ; alerte de chute des vues | Sur des statistiques simulées : valeurs recalées enregistrées avec leur origine ; une valeur de la config l'emporte |
| 22 | 4 | Verticales : stratégies créateurs, langue par marque, filtres | Les commandes filtrent par verticale (R21) |
| 23 | 4 | Supprimée (réécriture à la place de la copie dans `vendor/`) | — |
| 24 | 4 | Mots et phrases, SponsorBlock, autocut | Aucune coupe hors fin de phrase (R24) ; autocut jamais appliqué à un cartoon (R45, R46) |
| 25 | 4 | Recadrage visage (sur la base de la tâche 34) et sous-titres ASS | Visage dans le cadre 95 % du temps, aucun saut de plus de 5 % de la largeur ; sous-titres conformes à R25 |
| 26 | 4 | Crédits et campagnes | Un post sans crédit ou mention requise est refusé (R23) ; URL listées dans le rapport (R28) |
| 27 | 4 | Prospection et instantanés | Classement reproductible ; aucune chaîne n'est publiable sans fichier YAML `authorized: true` (R32) |
| 28 | 4 | Veille 6 h, voie express, second passage | Nouvelle vidéo simulée détectée ; panne RSS → yt-dlp ; extraits proposés en moins d'une heure pour 20 min (R29, R30) |
| 29 | 4 | Supprimée (Postiz abandonné) | — |
| 30 | 5 | Compilations thématiques | 4 à 8 gags d'épisodes du même compte, registre respecté |
| 31 | 5 | Étude de marché TikTok | Panel constitué automatiquement depuis des comptes et descriptions enregistrés ; réserve de hashtags tournants et hashtag nostalgie générés |
| 32 | 5 | Revue facultative (`review build` et `apply`) | Une décision prise dans la page modifie la base après `apply` |
| 38 | 5 | `discover series` : séries et créateurs en tendance, vitrine, tableau de suivi des licences | Sur des données enregistrées : candidat trouvé, vitrine produite dans `outbox_test/` dans la limite du budget, ligne ajoutée au tableau ; aucun candidat publiable (R14, R32) |
| 39 | 6 | Déploiement : `compose.yaml`, unités systemd, `rushes serve` derrière GoDoxy (veille du conteneur) et PocketID, ntfy, secrets, sauvegarde hors RAID, procédure d'exploitation | Installation reproductible depuis le dépôt ; restauration d'une sauvegarde testée ; limites mémoire vérifiées par `docker inspect` |
| 40 | 6 | Répétition générale de 7 jours | Stock produit pour les 8 comptes, aucune violation des règles, rapports et alertes reçus, mémoire sous le budget |
| 41 | 6 | Lancement et rodage | Checklist de lancement cochée ; sur les vrais posts : publication détectée seule, statistiques collectées, bandits et recalage actifs après 3 semaines |

### CLAUDE.md

Le `CLAUDE.md` à la racine du dépôt est l'unique version des instructions de projet (il fait foi) ; il n'est pas dupliqué ici.

### Prompt de la première session

```text
Tu travailles sur rushes. Lis docs/spec.md en entier.

Tâche 1 du plan (squelette : uv, Typer, config Pydantic, SQLite et migrations, paths).
Critère : « rushes --help » liste les commandes prévues (vides pour l'instant) et une
config invalide est refusée avec un message clair.

Avant tout : liste les contradictions, les zones floues et les questions que tu vois dans
la spec. Puis, en mode plan, propose l'arborescence, les dépendances avec leurs versions,
le schéma initial de la base et la liste des tests. N'écris aucun code avant que je valide.
```

**Pour les tâches suivantes** : « Tâche N du plan : intitulé. Sections à relire : … ; règles concernées : … ; critère : …. Mode plan d'abord, tests avant le code, questions avant les suppositions. »

**Pendant le développement**

- Une tâche par session ; relire le diff et committer à la fin.
- Toute décision prise en route va dans `docs/decisions.md`, et dans ce document si elle change une décision arrêtée.
- Les vrais épisodes restent hors du dépôt.

## Points à vérifier

À confirmer pendant le développement ; chaque réponse va dans `docs/decisions.md`.

**À trancher par Louis**

- [x] Rythme de départ des comptes créateurs : le même que les cartoons (choix par défaut, `decisions.md`).
- [x] Séries et créateurs de départ : Tom et Jerry, Oggy, Squeezie, Byilhan, non autorisés au départ (Q6).
- [x] Charte de chaque marque : proposée par rushes, validée par Louis sur des rendus d'exemple (Q10, tâche 7).
- [x] Hashtags français pour Instant Culte : caducs, l'étude de marché les produit (autonomie) ; `#<créateur>` obligatoire (R13).
- [x] Accès à `outbox/` depuis le Mac : page de `rushes serve` derrière le reverse proxy et PocketID (Q3, Q4).
- [x] Nom du paquet : rushes, confirmé par Louis (L35).
- [ ] Licences : demandes à faire auprès des ayants droit, suivies dans le tableau de suivi des licences (avant le lancement, pour publier autre chose que la vitrine).
- [ ] Exemple réel d'export Instagram de Meta Business Suite, à fournir avant la tâche 17.

**À vérifier en codant**

- [x] Paramètre de llama-cpp-python pour la sortie par schéma JSON : `response_format={"type": "json_object", "schema": …}`.
- [x] Postiz : sans objet (abandonné).
- [ ] Format réel de l'export Instagram : en-têtes traduits selon la langue de l'interface ; colonnes connues en anglais dans `decisions.md` ; confirmer sur l'exemple réel (tâche 17).
- [ ] Seuil d'apparition de la courbe « Les plus revus » : non documenté ; absente sur les vidéos sondées (dessin animé pour enfants, vidéo de créateur de 3 semaines) ; son absence est traitée comme normale (tâches 10 et 13).
- [x] Flux RSS des chaînes YouTube : fonctionne (15 dernières vidéos, vues, likes).
- [ ] Seuils à calibrer : approbation automatique, distance pHash, tolérance de chevauchement, poids de la file (recalage automatique, tâche 37 ; rodage, tâche 41).
- [ ] Temps réels et mémoire de pointe sur le serveur (tâche 9).
- [x] yt-dlp : vidéos les plus vues par la playlist `UULP…` ; chaîne officielle par la recherche de chaînes (badge et abonnés en une requête).
- [x] Source des tendances de hashtags : descriptions du panel lues avec yt-dlp ; pages de hashtag TikTok et Creative Center inutilisables sans connexion.
- [x] Table des valeurs déduites : `derived_values` (clé, portée, valeur, source, confiance, date).
- [x] Python 3.14 (décision de Louis du 2026-11-05, après un essai en 3.15) : toute la pile s'installe, onnxruntime inclus ; image `python:3.14-slim-trixie` ; essai de faisabilité sur le serveur : 10 modèles sur 10 passent (GO, `decisions.md`).
- [ ] TikTok Studio : programmation disponible ou non sur un compte personnel (test de Louis sur un compte).
- [ ] Imitation de navigateur pour TikTok (curl-cffi) : non documentée, peut casser ; surveillée par les alertes de collecte.
- [ ] Vitesses réelles sur le Coffee Lake : modèle de langage, CLIP, synthèse vocale, recadrage (tâche 9, puis tâches 12, 14, 34, 35).
- [ ] sherpa-onnx : horodatage par mot de la synthèse vocale (sinon retranscription par faster-whisper, tâche 35).
