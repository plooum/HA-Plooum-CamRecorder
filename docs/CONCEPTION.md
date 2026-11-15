# Conception — Plooum CamRecorder

## Besoin

Prendre des photos et des vidéos de n'importe quelle caméra de Home Assistant et les **enregistrer sur le serveur**, depuis une carte (bouton photo, bouton vidéo démarrer / arrêter) comme depuis n'importe quelle automatisation ou script. Les fichiers doivent être rangés, consultables dans le navigateur multimédia de HA et dans la carte galerie de HA-Plooum-Cards, et purgés automatiquement.

La future carte caméra de HA-Plooum-Cards (flux en direct, zoom, repli sur le flux SD puis sur l'aperçu de HA-Plooum-CamPreview) s'appuie dessus : elle n'affiche ses boutons photo / vidéo que si l'intégration est installée.

## Ce que HA fait déjà, et ce qui manque

- `camera.snapshot` et `camera.record` existent, mais `camera.record` a une **durée fixe** : aucun moyen d'arrêter un enregistrement lancé à la main.
- Ni l'un ni l'autre ne range les fichiers, ne purge, ne publie d'état (« en cours d'enregistrement ») ni ne dit quel fichier a été écrit.

## Choix

- **Dépôt séparé de CamPreview** : aperçu filtré et enregistrement sont deux métiers ; on peut vouloir l'un sans l'autre.
- **Les actions marchent pour toutes les caméras**, configurées ou non : `snapshot`, `record`, `start_recording`, `stop_recording` prennent n'importe quelle entité `camera.*`. Les **caméras choisies** dans les options reçoivent en plus des entités (bouton, interrupteur, capteurs) pour s'en servir sans écrire de YAML.
- **Une seule entrée de configuration**, réglages dans les options : dossier, rétention, caméras à entités. Pas de réglage par caméra, donc pas de sous-entrées (contrairement à CamPreview).
- **Vidéo par l'enregistreur de l'intégration `stream` de HA** : copie du flux sans réencodage (léger, même sur un Raspberry Pi), `lookback` gratuit quand le flux HLS de la caméra tourne déjà (option « Précharger le flux » de la caméra, ou quelqu'un qui regarde). Une caméra sans flux (`stream_source`) ne peut que prendre des photos.
- **Arrêt avant la fin** : l'enregistreur de `stream` s'arrête quand son minuteur d'inactivité expire (c'est ainsi que se termine la durée de `camera.record`) : il quitte alors le flux, ce qui le réveille, écrit ce qu'il a et ferme le fichier. `stop_recording` le retire du flux tout de suite (`Stream.remove_provider`) : même chemin qu'une fin normale. `start_recording` lance donc un enregistrement de `max_duration` (10 min par défaut) qui s'arrête au plus tard là. C'est une API interne de `stream` (`Stream.outputs()`, `remove_provider`) : à revérifier aux mises à jour de HA ; si elle change, `stop_recording` lève une erreur claire.
- **Course à l'arrêt du flux** : quand une vidéo finit, le flux n'a plus de sortie et s'arrête ; à la fin de son fil, HA retire toutes les sorties qu'il a alors. Une vidéo démarrée entre-temps serait coupée aussitôt (« captured nothing » ; `camera.record` a le même défaut). Avant de démarrer, on attend donc la fin d'un arrêt en cours (le verrou start / stop du flux, plus un court délai).
- **Nom d'après la durée réelle** : l'enregistreur écrit des segments entiers (d'image clé à image clé) et ajoute toujours un segment de retour en arrière quand le flux tourne déjà. La vidéo dure donc quelques secondes de plus que demandé ; son nom `<début>-<fin>` est calculé à partir de sa durée réelle (lue avec PyAV) et de l'heure de fin, pour que la galerie la place juste.
- **Arrêt de HA** : HA annule les tâches de fond avant l'évènement `homeassistant_stop` et `stream` arrête alors ses flux : les vidéos en cours sont arrêtées et écrites dans une *tâche d'arrêt* (`async_add_shutdown_job`, étape 1 de l'arrêt), avant tout ça.
- **Une seule vidéo à la fois par caméra** (limite de `stream`), plusieurs caméras en parallèle sans problème. Une photo pendant une vidéo marche.
- **Photo comme `camera.snapshot`** : l'image de la caméra, ou celle de son flux (en attendant la prochaine image clé) quand la caméra prend ses images fixes dans son flux.
- **Les actions qui produisent un fichier renvoient sa description** (réponse d'action : `path`, `media_content_id`, `start`, `end`) et émettent l'événement `plooum_camrecorder_saved`, pour enchaîner une notification avec la photo par exemple. `record` et `stop_recording` attendent que la vidéo soit écrite ; `start_recording` rend la main tout de suite.

## Rangement des fichiers

Dossier par défaut : `<dossier multimédia local>/plooum_camrecorder` (`/media/plooum_camrecorder` sous HA OS), toujours dans un dossier autorisé (`allowlist_external_dirs`, dont font partie les `media_dirs`).

```
<dossier>/<caméra>/photos/<AAAA-MM-JJ>/<HH-MM-SS>.jpg
<dossier>/<caméra>/videos/<AAAA-MM-JJ>/<début epoch>-<fin epoch>.mp4
```

- `<caméra>` : l'`object_id` de l'entité (`camera.jardin` → `jardin`).
- Jour et heure dans le fuseau de HA. Pas de `:` dans les noms (partages Samba / Windows).
- Les vidéos suivent le format que la **carte galerie** lit déjà (epoch dans un dossier de jour) : `media_source: media-source://media_source/local/plooum_camrecorder/jardin/videos` suffit.
- Pendant l'enregistrement, le fichier s'appelle `<début>-recording.mp4` (ignoré par la galerie), renommé à la fin. Deux photos dans la même seconde : suffixe `-2`, `-3`…

## Rétention

Options `keep_days` (30 jours par défaut, 0 = toujours) et `max_size_gb` (0 = pas de limite) : les fichiers trop vieux sont supprimés, puis les plus anciens jusqu'à repasser sous la taille. Purge toutes les heures et après chaque enregistrement. Elle ne touche que les fichiers au format ci-dessus (et les dossiers de jour vidés), jamais le reste du dossier ; les restes d'enregistrements interrompus (`*-recording.mp4*`) de plus d'un jour sont supprimés.

## Entités (caméras choisies)

Pour `camera.jardin`, appareil « Jardin recorder » :

| Entité | Rôle |
| :--- | :--- |
| `button.jardin_take_photo` | prend une photo |
| `switch.jardin_recording` | allumé pendant une vidéo (lancée par n'importe quel moyen) ; l'allumer démarre une vidéo, l'éteindre l'arrête |
| `sensor.jardin_last_photo` / `sensor.jardin_last_video` | horodatage du dernier fichier, attributs `path`, `media_content_id` |

Toutes portent l'attribut `camera_entity` : la carte retrouve l'interrupteur de sa caméra sans configuration.

## Tests

- `tests/test_files.py` : nommage, analyse des noms, choix des fichiers à purger (pur, sans HA).
- `dev/` : un HA local (le venv de HA-Plooum-Cards) avec une caméra de test dont le flux est un HLS en direct produit par ffmpeg ; `dev/scenario.py` y teste photo, vidéo, arrêt anticipé, lookback, interrupteur et événements. Le poste de dev n'a pas `libturbojpeg` : la caméra de test prend ses images fixes avec ffmpeg plutôt que dans le flux de HA, la branche « image du flux » de la photo n'y est donc pas couverte.

## Suite

- Carte caméra dans HA-Plooum-Cards.
- Éventuellement : repli ffmpeg pour les caméras sans `stream` (MJPEG), miniatures des vidéos.
