# Aske

Application Android écrite en Python avec [Kivy]. Elle affiche les 10 dernières vidéos d'une
chaîne YouTube (miniature, titre, date, vues, description). Un appui sur une vidéo la lit
directement dans l'application, en plein écran, via `yout-ube.com` : tournez le téléphone pour
la regarder en paysage, et utilisez « retour » pour revenir à la liste.

- **Mes chaînes** : le bouton « + Ajouter », à côté du nom de la chaîne affichée, l'enregistre
  dans l'onglet « Mes chaînes ». Un appui sur une chaîne de cet onglet affiche ses vidéos.
- **Écoute écran éteint** : éteignez l'écran pendant une vidéo, le son continue. Passer à une
  autre application met la vidéo en pause ; elle reprend au retour dans Aske.

Pas de serveur ni de clé API : l'application lit directement le flux RSS public de YouTube.
Elle retient la dernière chaîne affichée et la recharge à chaque ouverture.

Pendant la lecture, Android affiche une notification « Aske » : c'est elle qui permet de
continuer écran éteint. Sur Android 13 et plus, elle n'apparaît que si les notifications
d'Aske sont autorisées (*Paramètres → Applications → Aske → Notifications*) ; la lecture
écran éteint fonctionne dans les deux cas.

## Installer sur le téléphone

1. Sur le téléphone, activez le débogage USB : *Paramètres → À propos du téléphone*, appuyez
   7 fois sur *Numéro de build*, puis *Options pour les développeurs → Débogage USB*.
2. Branchez le téléphone en USB et acceptez l'autorisation qui s'affiche.
3. Installez l'APK (`adb` a été téléchargé avec le SDK Android lors de la compilation) :

   ```bash
   ~/.buildozer/android/platform/android-sdk/platform-tools/adb install -r bin/freetube-1.2-arm64-v8a-debug.apk
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

Le dossier `~/.android` monté dans le conteneur conserve la clé de signature
(`debug.keystore`). Sans lui, chaque compilation serait signée avec une nouvelle clé, et Android
refuserait d'installer la nouvelle version par-dessus l'ancienne. Ne supprimez donc pas ce fichier.

## Tester sur PC

Kivy fonctionne aussi sur ordinateur (il faut Python 3.13 au plus, Kivy 2.3 n'existant pas
encore pour Python 3.14) :

```bash
python3.13 -m venv .venv && .venv/bin/pip install kivy certifi
.venv/bin/python main.py
```

## Libérer l'espace disque

Les outils de compilation occupent plusieurs Go. Pour tout supprimer (l'APK dans `bin/` est
conservé) :

```bash
rm -rf ~/.buildozer .buildozer
docker rmi kivy/buildozer
```

[Kivy]: https://kivy.org
