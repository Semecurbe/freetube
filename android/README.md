# Aske

Application Android écrite en Python avec [Kivy], pour regarder ou écouter des vidéos YouTube
via `yout-ube.com`, et les garder sur le téléphone.

## Les onglets

- **Vidéos** : le champ du haut sert à deux choses.
  - Des mots-clés (par exemple `livre audio`) : les 100 premiers résultats de YouTube
    (miniature, durée, titre, chaîne, vues, date).
  - Le `@nom` d'une chaîne (ou son adresse youtube.com, ou son ID `UC…`) : ses 10 dernières
    vidéos. « Tout lire » les lit à la suite ; un appui sur une vidéo la lit, puis les suivantes.
- **Chaînes** : les chaînes auxquelles vous êtes abonné (bouton « S'abonner », sur la page
  d'une chaîne ou sous une vidéo).
- **Playlists** : vos playlists, créées ici ou depuis une vidéo. « Tout lire » les lit à la
  suite.
- **Historique** : les vidéos regardées, avec l'endroit où vous vous êtes arrêté (barre rouge
  sous la miniature).
- **Téléchargés** : les vidéos gardées sur le téléphone, lisibles sans connexion.

La roue dentée, en haut à droite, ouvre les **Paramètres** : lecture automatique, créateur et
version.

## La page de lecture

Un appui sur une vidéo ouvre sa page de lecture : la vidéo en haut (en plein écran si vous
tournez le téléphone), et dessous :

- **le nom de la chaîne** : un appui l'affiche, et « retour » ramène à la vidéo ;
  « S'abonner » l'ajoute à l'onglet Chaînes ;
- **Vidéo** ou **Son** : télécharge la vidéo entière, ou seulement le son (un livre audio de
  8 heures fait environ 470 Mo) ;
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
vidéo en pause ; elle reprend au retour dans Aske.

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
fichiers restent dans le stockage privé d'Aske et disparaissent si vous la désinstallez.

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
   ~/.buildozer/android/platform/android-sdk/platform-tools/adb install -r bin/freetube-1.4-arm64-v8a-debug.apk
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
fonctionne pas (l'assemblage de l'image et du son passe par Android) ; celui du son, si.

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
[Material Icons]: https://github.com/google/material-design-icons
