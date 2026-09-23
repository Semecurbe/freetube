# FreeTube pour Android

Application Android écrite en Python avec [Kivy]. Elle affiche les 10 dernières vidéos d'une
chaîne YouTube (miniature, titre, date, vues, description). Un appui sur une vidéo l'ouvre sur
`yout-ube.com` dans le navigateur du téléphone.

Pas de serveur ni de clé API : l'application lit directement le flux RSS public de YouTube.
Elle retient la dernière chaîne affichée et la recharge à chaque ouverture.

## Installer sur le téléphone

1. Sur le téléphone, activez le débogage USB : *Paramètres → À propos du téléphone*, appuyez
   7 fois sur *Numéro de build*, puis *Options pour les développeurs → Débogage USB*.
2. Branchez le téléphone en USB et acceptez l'autorisation qui s'affiche.
3. Installez l'APK (`adb` a été téléchargé avec le SDK Android lors de la compilation) :

   ```bash
   ~/.buildozer/android/platform/android-sdk/platform-tools/adb install -r bin/freetube-1.0-arm64-v8a-debug.apk
   ```

Sans câble : copiez le fichier `.apk` sur le téléphone et ouvrez-le. Android demandera
d'autoriser l'installation d'applications de cette source.

## Recompiler l'APK après une modification

Depuis ce dossier :

```bash
docker run --rm -v "$HOME/.buildozer":/home/user/.buildozer -v "$PWD":/home/user/hostcwd kivy/buildozer android debug
```

L'APK est créé dans `bin/`. La première compilation télécharge le SDK et le NDK Android dans
`~/.buildozer` ; les suivantes ne prennent que quelques minutes.

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
