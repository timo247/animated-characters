# Episode Builder — éditeur visuel de episode-settings.json

## Installation

Copie le dossier `episode_builder/` **directement à la racine** de ton projet,
à côté de `characters/` et `episodes/` :

```
~/scripts/animated_characters/
├── characters/
├── episodes/
└── episode_builder/        <- ici
    ├── server.py
    ├── index.html
    └── README.md
```

## Lancement

```bash
cd ~/scripts/animated_characters
python3 episode_builder/server.py
```

Puis ouvre **http://localhost:8765** dans ton navigateur (Chrome, Firefox, peu importe).

Aucune dépendance à installer : le serveur n'utilise que la bibliothèque
standard de Python 3.

## Utilisation

1. En haut : renseigne `episode_id`, le nom du fichier de sortie, la durée,
   le fps, la résolution, choisis la **background image** (liste tirée de
   `episodes/images/`) et l'audio (`episodes/audios/`).
2. À gauche : la liste des éléments — la **Caméra** et les **personnages**.
   Un personnage par défaut est ajouté automatiquement. Clique sur
   "+ Ajouter un personnage" pour en ajouter d'autres (liste tirée de
   `characters/`).
3. Clique sur un élément pour l'éditer :
   - **Caméra** : chaque "keyframe" définit un cadrage (zoom/x/y) à un
     instant donné. Glisse le rectangle bleu sur le canvas pour le
     déplacer, tire son coin bas-droit pour zoomer/dézoomer. Ajoute
     autant de keyframes que nécessaire.
   - **Personnage** : choisis le personnage, sa position de base (tirée de
     son `character-settings.json`), fais glisser son sprite (frame idle)
     pour définir sa position initiale à l'écran. Coche `flip_x` si besoin.
     Ajoute des **mouvements** (bouton "+ Ajouter un mouvement"), puis dans
     chaque mouvement des **segments** ("+ Ajouter un segment") : choisis la
     position, fais glisser le sprite pour fixer le point d'arrivée, règle
     la durée, `flip_x`, `reverse`, `skip_transition`, `gaze`, puis clique
     "Enregistrer le segment".
4. En bas : "Générer episode-settings.json" ouvre une fenêtre pour choisir
   le nom du fichier. Il est enregistré dans `episodes/episodes-settings/`.

## Limites connues (MVP)

- L'aperçu sur le canvas affiche uniquement le sprite de base (idle),
  sans composition des calques yeux/bouche — suffisant pour positionner,
  pas pour prévisualiser l'expression finale.
- Les dimensions d'affichage du sprite sur le canvas sont approximatives
  (pas de lecture des dimensions réelles du PNG) ; seules les coordonnées
  x/y exportées sont exactes.
- `dialogue` est toujours exporté à `null` — pas encore d'éditeur dédié.
- Pas d'annulation (undo) : ferme sans sauvegarder pour repartir de zéro.
