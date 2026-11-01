# Fabrication du journal Kindle — consignes pour l'agent

Ce dépôt fabrique chaque matin un journal au format EPUB pour une liseuse
Kindle. Tu es l'éditeur : le script collecte, tu choisis et rédiges, le script
assemble. Tout se fait en français.

## Procédure

1. Installer les dépendances dans un environnement virtuel (les paquets
   système de l'environnement cloud peuvent être cassés, Pillow notamment) :
   `python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt`
   Utiliser ensuite `.venv/bin/python` pour toutes les commandes du script.
2. Collecter les articles récents :
   `.venv/bin/python build_journal.py collect`
   Le script écrit `work/candidates.json` et affiche la liste
   `[id] (source / rubrique) titre`. Lis le fichier JSON pour les résumés.
3. Lire `sources.yaml` : la clé `ligne_editoriale` donne les goûts du lecteur,
   `articles_par_jour` le nombre d'articles visé, `quota_par_rubrique` les
   bornes par rubrique, `rubriques` l'ordre des rubriques.
4. Composer l'édition dans `work/edition.json` (format ci-dessous) : choisir
   les articles (`articles_par_jour` à deux près, jamais plus de
   `articles_par_jour` + 2), les ranger par rubrique, réécrire chaque titre et écrire un
   chapeau de deux phrases à partir du résumé fourni. Ne jamais inventer un
   fait absent du résumé. Un article n'apparaît qu'une fois.
5. Point bourse : `collect` a aussi écrit `work/marche.json` (indices avec
   variations, actualité des marchés). Lire `bourse.consignes` dans
   `sources.yaml`, choisir la valeur à suivre et remplir la clé `bourse` de
   `work/edition.json` (format ci-dessous). Le symbole est au format CNBC
   (voir les exemples dans `sources.yaml`) ; `build` récupère les cotations.
6. Assembler : `.venv/bin/python build_journal.py build`
   Le script récupère le texte complet et une image par article, écrit
   `editions/AAAA-MM-JJ.epub`, copie le fichier dans `latest.epub` et supprime
   les éditions au-delà de `editions_conservees`.
7. Vérifier que `latest.epub` existe, pèse plus de 50 Ko et que `unzip -l latest.epub`
   liste bien un fichier `artNN.xhtml` par article et `bourse.xhtml`.
8. Committer et pousser sur `main` : `editions/AAAA-MM-JJ.epub`, `latest.epub`
   et les éventuelles suppressions. Message : `Édition du AAAA-MM-JJ (N articles)`.
   Ne pas committer le dossier `work/`.

Si la collecte ne ramène aucun candidat ou si l'assemblage échoue, ne rien
pousser et terminer en expliquant la cause.

## Format de `work/edition.json`

```json
{
  "date": "2026-11-02",
  "edito": "Deux ou trois phrases d'ouverture, facultatif.",
  "rubriques": [
    {
      "nom": "À la une",
      "articles": [
        {
          "id": "f1441606bd",
          "titre": "Titre réécrit, informatif, 90 caractères au plus",
          "chapeau": "Première phrase : le fait. Seconde phrase : pourquoi il compte."
        }
      ]
    }
  ],
  "bourse": {
    "commentaire": "Deux ou trois phrases sur la séance et la tendance, à partir des chiffres de work/marche.json et de l'actualité.",
    "valeur": {
      "symbole": "TTE-FR",
      "nom": "TotalEnergies",
      "pourquoi": "Quatre à six phrases : le fait déclencheur, ce que le marché attend dans les mois à venir, les risques.",
      "a_surveiller": "Une ou deux phrases : le prochain rendez-vous concret."
    }
  }
}
```

- `bourse.commentaire` et `bourse.valeur` sont obligatoires quand `sources.yaml`
  contient une section `bourse`. Les chiffres (cours, variations, PER) sont
  ajoutés par `build` : ne pas les recopier dans les textes.

- `id` est l'identifiant donné par `collect`.
- `date` est la date du jour (fuseau Europe/Paris), celle de `date_edition`
  dans `work/candidates.json`.
- Les noms de rubriques sont ceux de `sources.yaml`, dans le même ordre ;
  une rubrique sans article est omise.
