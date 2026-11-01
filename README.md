# kindle-journal

Fabrication quotidienne d'un journal au format EPUB pour une liseuse Kindle
(Paperwhite sous KOReader, avec le plugin « Journal »). Chaque nuit, une
routine Claude Code lit des flux RSS, choisit une douzaine d'articles, rédige
titres et chapeaux en français, assemble un EPUB (couverture datée, sommaire
par rubrique, un chapitre par article, images en niveaux de gris) et le dépose
ici sous `editions/AAAA-MM-JJ.epub` et `latest.epub`.

La liseuse télécharge `latest.epub` chaque matin à 7 h à l'adresse :

```
https://raw.githubusercontent.com/<compte>/kindle-journal/main/latest.epub
```

## Contenu du dépôt

| Fichier | Rôle |
| --- | --- |
| `sources.yaml` | Flux RSS, rubriques, exclusions, quotas et ligne éditoriale. C'est le fichier à modifier pour changer le journal. |
| `build_journal.py` | Script de fabrication : `collect` (lecture des flux), `build` (assemblage de l'EPUB), `prune` (ménage). |
| `CLAUDE.md` | Consignes suivies par l'agent de la routine : procédure et format de `work/edition.json`. |
| `editions/` | Les trente dernières éditions, une par jour. |
| `latest.epub` | Copie de l'édition la plus récente, lue par la Kindle. |
| `work/` | Fichiers de travail (candidats, sélection), non versionnés. |

## Comment une édition est fabriquée

1. `build_journal.py collect` lit tous les flux de `sources.yaml`,
   écarte les articles trop anciens ou exclus (directs, vidéos, sport…),
   dédoublonne et écrit `work/candidates.json`.
2. L'agent lit les candidats et la `ligne_editoriale`, choisit les articles,
   les range par rubrique, réécrit les titres et rédige un chapeau de deux
   phrases par article dans `work/edition.json`. Aucun appel d'API : c'est
   l'agent de la routine qui fait ce travail.
3. `build_journal.py build` récupère le texte complet de chaque
   article retenu (trafilatura), télécharge une image par article, la passe
   en niveaux de gris et la réduit pour l'écran 758 × 1024, puis écrit l'EPUB
   avec ebooklib. L'édition est copiée dans `latest.epub` et les éditions au
   delà de `editions_conservees` sont supprimées.
4. La routine committe et pousse `editions/` et `latest.epub` sur `main`.

Quand une page refuse le téléchargement (Ouest-France, articles réservés aux
abonnés), le chapitre contient le résumé du flux et une note le signale.

## Fabriquer une édition à la main

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python build_journal.py collect
# écrire work/edition.json (format dans CLAUDE.md)
.venv/bin/python build_journal.py build
```

`build --sans-telechargement` assemble un EPUB à partir des seuls résumés RSS,
pratique pour tester vite.

## Régler le journal

Tout se passe dans `sources.yaml` :

- `flux` : ajouter ou retirer des flux RSS, chacun avec une rubrique par défaut.
- `rubriques` : l'ordre d'apparition dans le journal.
- `articles_par_jour` et `quota_par_rubrique` : la taille de l'édition.
- `ligne_editoriale` : les goûts du lecteur, en français, lus par l'agent.
- `exclusions` : expressions régulières appliquées aux titres et aux liens.
- `max_age_heures` : fraîcheur maximale des articles collectés.

## Réglage côté Kindle

Dans KOReader : Outils → Journal → Adresse, saisir l'adresse de `latest.epub`
ci-dessus. Le fichier est servi avec les en-têtes `ETag` et `Last-Modified`,
que le plugin utilise pour ne télécharger que les nouvelles éditions.

Si le dépôt est privé, créer sur GitHub un jeton d'accès (*fine-grained*,
lecture seule du contenu de ce dépôt) et le copier, seul sur une ligne, dans
`koreader/settings/journal_token.txt` sur la liseuse. Le plugin l'envoie dans
l'en-tête `Authorization: token …`.
