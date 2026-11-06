# rushes
Pipeline Python personnel qui prépare des vidéos courtes : cartoons pour TikTok et Instagram,
extraits et best-of de créateurs YouTube, eux aussi pour TikTok et Instagram. La référence unique est docs/spec.md.
Le logiciel est autonome : il trouve seul les séries et créateurs en tendance, les chaînes officielles et leurs
vidéos les plus vues, les hashtags et tendances du secteur, la rotation, les créneaux. La config ne donne que des
amorces et les décisions de Louis. Rien n'est publié par le logiciel : Louis publie toujours à la main.

## Commandes
- uv run pytest -m "not slow"   # tests rapides, avant chaque commit
- uv run ruff check . && uv run mypy src/rushes/core src/rushes/db

## Règles
- When working with Python, invoke the relevant `/astral:<skill>` for `uv`, `ty`, and `ruff` to ensure best practices are followed."
- UI : composants shadcn, Tailwind v4, `frontend-design` pour toute nouvelle page ; lancer `break-ui` et `web-design-guidelines` avant de livrer."
- Pour explorer le code, utiliser l'outil CodeGraph avant de lire des fichiers.
- Utiliser CodeGraph spontanément (explore, callers, impact) pour éviter de relire des fichiers entiers, y compris dans les sous-agents.
- `codegraph sync` régulièrement : début de session, après une série d'éditions, avant toute exploration si le code a changé.
- `codegraph sync` après chaque tâche terminée (avant de rendre le résultat et de proposer le commit), y compris dans les sous-agents implémenteurs.
- Code et identifiants en anglais, commentaires courts.
- Une étape = un module sous src/rushes/ ; idempotente ; état dans SQLite.
- Jamais de chemin en dur : rushes.paths et la config.
- Autonomie : ne jamais exiger en config ce que le logiciel peut déduire (chaînes, requêtes, hashtags,
  créneaux, seuils) ; ces valeurs sont des amorces, et une valeur de la config l'emporte sur la valeur déduite.
  Restent à Louis, jamais déduits ni par défaut : licensed, authorized, hashtags obligatoires, marques, langues, comptes.
- Toute attribution passe par db.registry.can_assign.
- Les étapes émettent des événements ; seul rushes.ui écrit sur la console.
- Tests : jamais de réseau ni de data/ ; tmp_path et vidéos synthétiques.
- Tests avant le code ; le nom du test cite la règle (test_r9_...).
- Règles R1 à R50 de docs/spec.md (R31 et R42 supprimées) : ne jamais en changer une pour faire passer un test.
- Aucun LLM distant ni API payante (R39) ; code tiers seulement dans vendor/ (R40), aucun pour l'instant.
- Plan d'implémentation : docs/plans/ (00-master.md puis un fichier par phase).
- Spec muette ou ambiguë : demander, puis noter la réponse dans docs/decisions.md.
