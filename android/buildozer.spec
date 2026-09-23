[app]

# Nom affiché sous l'icône, et identifiant unique de l'application
# (l'identifiant fr.perso.freetube est gardé : les mises à jour s'installent par-dessus)
title = Aske
package.name = freetube
package.domain = fr.perso
# Version lue dans main.py (__version__), qui l'affiche aussi dans Paramètres.
version.regex = __version__ = ['"](.*)['"]
version.filename = %(source.dir)s/main.py

source.dir = .
source.include_exts = py,kv,png,ttf
source.exclude_dirs = bin, java

# certifi : certificats racine pour les connexions HTTPS
# yt-dlp : téléchargements hors ligne (version figée : YouTube change souvent, une nouvelle
# version de yt-dlp demandera de recompiler l'APK)
requirements = python3,kivy,certifi,yt-dlp==2026.8.19

icon.filename = %(source.dir)s/images/icon.png
icon.adaptive_foreground.filename = %(source.dir)s/images/icon_fg.png
icon.adaptive_background.filename = %(source.dir)s/images/icon_bg.png
presplash.filename = %(source.dir)s/images/presplash.png
android.presplash_color = #0f0f0f

orientation = portrait
fullscreen = 0

android.permissions = INTERNET, WAKE_LOCK, FOREGROUND_SERVICE, FOREGROUND_SERVICE_MEDIA_PLAYBACK, FOREGROUND_SERVICE_DATA_SYNC, POST_NOTIFICATIONS

# Services qui gardent Aske active écran éteint, pendant la lecture (MediaService) et les
# téléchargements (DownloadService) : voir java/fr/perso/freetube/. Ils tournent dans le
# processus de l'application, là où se trouvent le lecteur et les téléchargements.
android.add_src = java
p4a.extra_args = --extra-manifest-application-xml='<service android:name="fr.perso.freetube.MediaService" android:foregroundServiceType="mediaPlayback" android:exported="false" android:stopWithTask="true" /><service android:name="fr.perso.freetube.DownloadService" android:foregroundServiceType="dataSync" android:exported="false" android:stopWithTask="true" />'

# Android 14 : viser Android 15 obligerait l'application à dessiner sous la barre d'état.
android.api = 34
android.minapi = 24
# Téléphones 64 bits (quasiment tous depuis 2017). Ajoutez armeabi-v7a pour les très anciens.
android.archs = arm64-v8a
android.accept_sdk_license = True
android.logcat_filters = *:S python:D

# python-for-android 2026.05.09 échoue à installer les paquets précompilés pour Android
# (ici charset_normalizer, tiré par Kivy via requests). Corrigé dans develop (#3366) :
# on fige un commit de cette branche en attendant la prochaine version.
p4a.branch = develop
p4a.commit = e772ad93f20a61c0bbe1cf8955e073cfb41062e1

[buildozer]
log_level = 2
warn_on_root = 1
