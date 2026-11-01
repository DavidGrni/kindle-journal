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
5. Assembler : `.venv/bin/python build_journal.py build`
   Le script récupère le texte complet et une image par article, écrit
   `editions/AAAA-MM-JJ.epub`, copie le fichier dans `latest.epub` et supprime
   les éditions au-delà de `editions_conservees`.
6. Vérifier que `latest.epub` existe, pèse plus de 50 Ko et que `unzip -l latest.epub`
   liste bien un fichier `artNN.xhtml` par article.
7. Committer et pousser sur `main` : `editions/AAAA-MM-JJ.epub`, `latest.epub`
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
  ]
}
```

- `id` est l'identifiant donné par `collect`.
- `date` est la date du jour (fuseau Europe/Paris), celle de `date_edition`
  dans `work/candidates.json`.
- Les noms de rubriques sont ceux de `sources.yaml`, dans le même ordre ;
  une rubrique sans article est omise.
