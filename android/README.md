# Aske

Application Android écrite en Python avec [Kivy], pour regarder ou écouter des vidéos YouTube
via `yout-ube.com`, les garder sur le téléphone (vidéo, ou MP3), et les écouter en
arrière-plan pendant qu'on utilise une autre application, téléchargées ou non.

## Les onglets

- **Vidéos** : le champ du haut sert à deux choses.
  - Des mots-clés (par exemple `livre audio`) : les 100 premiers résultats de YouTube
    (miniature, durée, titre, chaîne, vues, date).
  - Le `@nom` d'une chaîne (ou son adresse youtube.com, ou son ID `UC…`) : ses 10 dernières
    vidéos. « Tout lire » les lit à la suite ; un appui sur une vidéo la lit, puis les suivantes.
- **Chaînes** : les chaînes auxquelles vous êtes abonné (bouton « S'abonner », sur la page
  d'une chaîne ou sous une vidéo).
- **Playlists** : vos playlists, créées ici ou depuis une vidéo. Dans une playlist :
  - « Tout lire » lit les vidéos à la suite ;
  - « Écouter » écoute toute la playlist à la suite, en arrière-plan, téléchargée ou non
    (voir plus bas) ;
  - « Tout en MP3 » télécharge en MP3 les vidéos qui ne le sont pas encore. Sous le nom de la
    playlist, Aske indique combien de MP3 sont prêts et combien sont en cours.
- **Historique** : les vidéos regardées, avec l'endroit où vous vous êtes arrêté (barre rouge
  sous la miniature).
- **Téléchargés** : les vidéos et les MP3 gardés sur le téléphone, lisibles sans connexion.
  Un appui sur un MP3 l'écoute, puis les MP3 suivants de la liste.

La roue dentée, en haut à droite, ouvre les **Paramètres** : lecture automatique, qualité des
MP3, créateur et version.

## La page de lecture

Un appui sur une vidéo ouvre sa page de lecture : la vidéo en haut (en plein écran si vous
tournez le téléphone), et dessous :

- **le nom de la chaîne** : un appui l'affiche, et « retour » ramène à la vidéo ;
  « S'abonner » l'ajoute à l'onglet Chaînes ;
- **Écouter** : ferme la vidéo et passe à l'écoute en arrière-plan (voir plus bas) ;
- **MP3** ou **Vidéo** : télécharge seulement le son, en MP3, ou la vidéo entière ;
- **Playlist** : l'enregistre dans une ou plusieurs playlists (cochez-les), ou dans une
  nouvelle (« + Nouvelle playlist »).

« Retour » ferme la page de lecture.

## Reprise et lecture enchaînée

Aske retient l'endroit où vous vous êtes arrêté dans chaque vidéo, même si vous quittez
l'application, et la reprend là (3 secondes plus tôt pour retrouver le fil). La barre
« Reprendre », en bas de l'écran, relance la dernière vidéo commencée et pas terminée.

Une vidéo s'arrête à la fin (yout-ube.com la ferait recommencer en boucle : Aske retire cette
option, voir `java/…/PlayerClient.java`). Dans une playlist ou sur une chaîne (« Tout lire »),
la vidéo suivante démarre alors toute seule, même écran éteint. Le réglage *Lecture
automatique* (Paramètres) le désactive : le bouton « Suivante » passe à la vidéo suivante.

## Écoute écran éteint

Éteignez l'écran pendant une vidéo : le son continue. Passer à une autre application met la
vidéo en pause ; elle reprend au retour dans Aske. Pour écouter en naviguant sur Chrome, par
exemple, appuyez sur « Écouter » sous la vidéo.

## Écoute en arrière-plan

« Écouter » (dans une playlist, ou sous une vidéo) ou un appui sur un MP3 de l'onglet
Téléchargés lance l'écoute : les pistes s'enchaînent, et le son continue quand vous passez à
une autre application ou éteignez l'écran.

Une piste téléchargée en MP3 est lue depuis le téléphone. Les autres sont lues **en ligne** :
il faut une connexion, et une heure d'écoute consomme environ 60 Mo de données. L'écran
d'écoute les marque « en ligne », et affiche « Chargement… » le temps que le son arrive (une
ou deux secondes ; la piste suivante est préparée un peu avant la fin de celle en cours). Sans
connexion, les pistes en ligne sont passées et seuls les MP3 sont joués. Les directs ne
s'écoutent pas.

- En bas de l'écran, le **mini-lecteur** montre la piste en cours : lecture ou pause, piste
  suivante, arrêt. Un appui dessus ouvre l'**écran d'écoute** : pochette, barre de lecture
  (touchez-la ou faites-la glisser), piste précédente, 10 secondes en arrière, 30 secondes en
  avant, piste suivante, et toutes les pistes de la liste (un appui en lance une). Le bouton
  « Playlist » y range la piste en cours dans vos playlists.
- Hors d'Aske, la **notification**, l'écran de verrouillage et les boutons d'un casque ou
  d'une enceinte Bluetooth commandent l'écoute.
- L'écoute se met en pause quand une autre application joue du son (une vidéo dans Chrome…) ou
  quand on débranche le casque ; pendant un appel, elle reprend ensuite toute seule.
- Comme pour les vidéos, Aske retient où vous en êtes dans chaque piste et la reprend là. La
  barre « Reprendre » relance le MP3 plutôt que la vidéo, s'il y en a un.
- YouTube ne garde l'adresse du son valable que quelques heures : si elle expire pendant un
  long livre audio (ou après une longue pause), Aske en demande une nouvelle et reprend au même
  endroit.
- À la fin de la liste, ou avec « Arrêter », l'écoute s'arrête et la notification disparaît.

Pendant la lecture et les téléchargements, Android affiche une notification « Aske » : c'est
elle qui permet de continuer écran éteint, et elle montre l'avancement des téléchargements.
Aske demande l'autorisation d'afficher des notifications au premier téléchargement ; si vous
refusez, tout fonctionne quand même, sans notification (*Paramètres → Applications → Aske →
Notifications* pour changer d'avis).

## Téléchargements

Ils se font en arrière-plan, écran éteint compris. Un téléchargement interrompu (Aske
fermée) reprend au lancement suivant. Une vidéo déjà téléchargée est toujours lue depuis le
téléphone, même avec une connexion.

YouTube ne fournit plus de fichier contenant à la fois l'image (720p au plus) et le son : Aske
télécharge les deux avec [yt-dlp], puis les assemble avec Android, sans perte de qualité. Les
vidéos restent dans le stockage privé d'Aske et disparaissent si vous la désinstallez.

Pour un **MP3**, Aske télécharge le son, puis le convertit sur le téléphone avec [FFmpeg], en
y mettant le titre, la chaîne et la miniature (pochette). La conversion prend plus de temps que
le téléchargement (environ 6 minutes pour 8 heures de son sur un Galaxy XCover Pro 2) ; la liste
et la notification en montrent l'avancement. Les MP3 sont rangés dans
le dossier **Musique/Aske** du téléphone : les autres applications (lecteur de musique,
gestionnaire de fichiers) les voient, et ils y restent si vous désinstallez Aske. Supprimer un
MP3 dans Aske l'efface aussi de ce dossier. Sur Android 9 et plus ancien, Aske demande
l'autorisation d'y écrire au premier MP3.

Les MP3 sont à 128 kbit/s (environ 55 Mo par heure), ce qui suffit pour la parole ; le réglage
*Qualité supérieure* (Paramètres) passe à 192 kbit/s (82 Mo par heure). Les « Son » téléchargés
avec Aske 1.4 (fichiers M4A, dans le stockage privé) restent dans l'onglet Téléchargés et
s'écoutent de la même façon.

yt-dlp suit les changements de YouTube : si les téléchargements cessent de fonctionner, mettez
sa version à jour dans `buildozer.spec` (ligne `requirements`), puis recompilez l'APK.

## Sans serveur ni clé d'API

Aske lit le flux RSS public des chaînes et interroge la recherche de YouTube comme le fait
son site. À chaque ouverture, elle réaffiche la dernière chaîne ou la dernière recherche.

## Installer sur le téléphone

1. Sur le téléphone, activez le débogage USB : *Paramètres → À propos du téléphone*, appuyez
   7 fois sur *Numéro de build*, puis *Options pour les développeurs → Débogage USB*.
2. Branchez le téléphone en USB et acceptez l'autorisation qui s'affiche.
3. Installez l'APK (`adb` a été téléchargé avec le SDK Android lors de la compilation) :

   ```bash
   ~/.buildozer/android/platform/android-sdk/platform-tools/adb install -r bin/freetube-1.6-arm64-v8a-debug.apk
   ```

Sans câble : copiez le fichier `.apk` sur le téléphone et ouvrez-le. Android demandera
d'autoriser l'installation d'applications de cette source.

## Recompiler l'APK après une modification

Depuis ce dossier :

```bash
docker run --rm -e ANDROID_USER_HOME=/home/user/.android -v "$HOME/.android":/home/user/.android \
  -v "$HOME/.buildozer":/home/user/.buildozer -v "$PWD":/home/user/hostcwd kivy/buildozer android debug
```

L'APK est créé dans `bin/`. La première compilation télécharge le SDK et le NDK Android dans
`~/.buildozer` ; les suivantes ne prennent que quelques minutes.

Pour une nouvelle version, changez `__version__` en haut de `main.py` : c'est à la fois le
numéro de l'APK et celui affiché dans Paramètres.

FFmpeg est compilé par une recette propre à Aske, `recipes/ffmpeg_mp3/` : elle ne garde que le
décodeur du son de YouTube, l'encodeur MP3 [libshine] et la lecture des pochettes JPEG (moins de
2 Mo dans l'APK). Pour changer de version de FFmpeg, modifiez `version` dans cette recette.

Le dossier `~/.android` monté dans le conteneur conserve la clé de signature
(`debug.keystore`). Sans lui, chaque compilation serait signée avec une nouvelle clé, et Android
refuserait d'installer la nouvelle version par-dessus l'ancienne. Ne supprimez donc pas ce fichier.

## Tester sur PC

Kivy fonctionne aussi sur ordinateur (il faut Python 3.13 au plus, Kivy 2.3 n'existant pas
encore pour Python 3.14) :

```bash
python3.13 -m venv .venv && .venv/bin/pip install kivy certifi yt-dlp
.venv/bin/python main.py
```

Sur PC, les vidéos s'ouvrent dans le navigateur, et le téléchargement de vidéos entières ne
fonctionne pas (l'assemblage de l'image et du son passe par Android). Les MP3, si : il faut
FFmpeg (`sudo apt install ffmpeg`), et ils vont dans votre dossier Musique/Aske. Leur écoute
passe par le son de Kivy, qui repart toujours du début de la piste et ne lit pas en ligne (les
pistes non téléchargées sont passées) : de quoi essayer l'interface.

## Icônes

`fonts/icons.ttf` est un extrait de la police [Material Icons] de Google (licence Apache 2.0),
réduit aux icônes listées dans `icons.py`. Pour en ajouter une, cherchez son code dans
`MaterialIcons-Regular.codepoints`, puis régénérez l'extrait avec
[fonttools](https://pypi.org/project/fonttools/) :

```bash
pyftsubset MaterialIcons-Regular.ttf --unicodes=U+E8B6,U+E064,… --output-file=fonts/icons.ttf
```

## Libérer l'espace disque

Les outils de compilation occupent plusieurs Go. Pour tout supprimer (l'APK dans `bin/` est
conservé) :

```bash
rm -rf ~/.buildozer .buildozer
docker rmi kivy/buildozer
```

[Kivy]: https://kivy.org
[yt-dlp]: https://github.com/yt-dlp/yt-dlp
[FFmpeg]: https://ffmpeg.org
[libshine]: https://github.com/toots/shine
[Material Icons]: https://github.com/google/material-design-icons
