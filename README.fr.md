# Plooum CamRecorder

*[English version](README.md)*

Une intégration Home Assistant qui **prend des photos et des vidéos de n'importe quelle caméra et les enregistre sur le serveur Home Assistant**, depuis un tableau de bord comme depuis n'importe quelle automatisation ou script. Une vidéo peut avoir une durée fixe, ou être démarrée et arrêtée à la main. Les fichiers sont rangés par caméra et par jour, apparaissent dans le navigateur multimédia et sont purgés automatiquement.

Elle complète `camera.snapshot` et `camera.record` de Home Assistant : vidéos qu'on peut arrêter, noms de fichiers rangés, rétention, état « en cours d'enregistrement », et actions qui disent quel fichier elles ont écrit.

## Installation

HACS → Intégrations → ⋮ → Dépôts personnalisés → `https://github.com/plooum/HA-Plooum-CamRecorder` (catégorie *Intégration*), installe **Plooum CamRecorder**, redémarre Home Assistant. Puis **Paramètres → Appareils et services → Ajouter une intégration → Plooum CamRecorder**.

## Actions

Chaque action marche avec **n'importe quelle entité caméra** (cible : entités, appareils ou pièces ; plusieurs caméras à la fois).

| Action | Rôle |
| :--- | :--- |
| `plooum_camrecorder.snapshot` | Enregistre une photo. |
| `plooum_camrecorder.record` | Enregistre une vidéo de `duration` secondes (30 par défaut), plus `lookback` secondes avant l'appel (0 par défaut, jusqu'à 30). Se termine une fois la vidéo écrite. |
| `plooum_camrecorder.start_recording` | Démarre une vidéo et rend la main tout de suite. Elle s'arrête avec `stop_recording`, ou d'elle-même après `max_duration` secondes (600 par défaut). Accepte aussi `lookback`. |
| `plooum_camrecorder.stop_recording` | Arrête la vidéo et se termine une fois qu'elle est écrite. |

`snapshot`, `record` et `stop_recording` répondent avec les fichiers enregistrés, qu'un script peut utiliser :

```yaml
- action: plooum_camrecorder.snapshot
  target:
    entity_id: camera.jardin
  response_variable: photo
- action: notify.mobile_app_telephone
  data:
    message: Quelqu'un au portail
    data:
      image: "{{ photo.files[0].media_content_id }}"
```

Chaque fichier est décrit par `camera_entity`, `kind` (`photo` ou `video`), `path`, `media_content_id`, `start` et `end` (vidéos). L'événement `plooum_camrecorder_saved` porte les mêmes données à chaque fichier enregistré.

Les vidéos demandent une caméra avec un flux (une caméra qui se lit en direct dans Home Assistant par l'intégration `stream`, en RTSP par exemple) ; les autres ne peuvent prendre que des photos. Une caméra enregistre une vidéo à la fois ; plusieurs caméras peuvent enregistrer ensemble.

Le **retour en arrière** (`lookback`) vient des dernières secondes que le flux de la caméra garde en mémoire : il ne marche que si ce flux tourne déjà, parce que quelqu'un regarde la caméra ou parce que **Précharger le flux** est activé dans les réglages de la caméra. Active-le pour les caméras qui enregistrent sur détection de mouvement.

La longueur d'une vidéo suit les images clés de la caméra : elle peut dépasser de quelques secondes ce qui est demandé (un intervalle d'images clés à chaque bout).

## Entités

Les caméras choisies dans les options reçoivent un appareil avec :

| Entité | Rôle |
| :--- | :--- |
| `button.<caméra>_take_photo` | Prend une photo. |
| `switch.<caméra>_recording` | Allumé tant que la caméra enregistre une vidéo, quel que soit celui qui l'a lancée. L'allumer démarre une vidéo (arrêtée au plus tard après 10 minutes), l'éteindre l'arrête. Attribut `started`. |
| `sensor.<caméra>_last_photo` / `sensor.<caméra>_last_video` | Heure de la dernière photo / vidéo, avec son `path` et son `media_content_id` (et `end` pour les vidéos). Retrouvés sur le disque après un redémarrage. |

Toutes portent l'attribut `camera_entity` : une carte retrouve ainsi l'enregistreur de sa caméra.

## Fichiers

```
<dossier>/<caméra>/photos/<AAAA-MM-JJ>/<HH-MM-SS>.jpg
<dossier>/<caméra>/videos/<AAAA-MM-JJ>/<début>-<fin>.mp4
```

`<caméra>` est l'identifiant de l'entité sans `camera.`, dates et heures sont dans le fuseau de Home Assistant, et `<début>` / `<fin>` d'une vidéo sont des horodatages Unix. La [carte galerie de HA-Plooum-Cards](https://github.com/plooum/HA-Plooum-Cards/blob/main/docs/gallery.md) lit ce rangement tel quel :

```yaml
type: custom:ha-plooum-gallery-card
media_source: media-source://media_source/local/plooum_camrecorder/jardin/videos
```

Pendant l'enregistrement, le fichier s'appelle `<début>-recording.mp4` ; il prend son nom définitif à la fin. Quand Home Assistant s'arrête, les vidéos en cours sont d'abord arrêtées et écrites.

## Options

**Paramètres → Appareils et services → Plooum CamRecorder → Configurer** :

| Option | Défaut | Rôle |
| :--- | :--- | :--- |
| Dossier | `<dossier multimédia>/plooum_camrecorder` (`/media/plooum_camrecorder` sous Home Assistant OS) | Où les fichiers sont enregistrés. Doit être un dossier multimédia (pour apparaître dans le navigateur multimédia) ou un dossier listé dans `allowlist_external_dirs`. |
| Conserver pendant | 30 jours | Les fichiers plus vieux sont supprimés. 0 : conservés pour toujours. |
| Taille maximale | 0 (pas de limite) | Au-delà, les fichiers les plus anciens sont supprimés. |
| Caméras avec entités | toutes les caméras à l'installation | Les caméras qui reçoivent les entités ci-dessus. Les actions marchent de toute façon avec toutes les caméras. |

La rétention tourne toutes les heures et après chaque fichier enregistré. Elle ne supprime que les fichiers nommés comme ci-dessus (et les dossiers de jour qu'elle vide), jamais rien d'autre dans le dossier. Les vidéos inachevées laissées par un plantage sont supprimées au bout d'un jour.

## Développement

`dev/ha.sh start` lance un Home Assistant local (sur 127.0.0.1:8130, avec l'environnement virtuel de HA-Plooum-Cards, ou `HA_VENV`) avec deux caméras de test : `camera.live` diffuse une vidéo HLS en direct produite par ffmpeg, `camera.still` n'a pas de flux. `dev/scenario.py` y vérifie photos, vidéos, arrêt anticipé, retour en arrière, interrupteur et événements ; `python -m unittest discover tests` vérifie le rangement des fichiers et la rétention. La conception est dans [docs/CONCEPTION.md](docs/CONCEPTION.md).
