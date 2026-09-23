[app]

# Nom affiché sous l'icône, et identifiant unique de l'application
title = FreeTube
package.name = freetube
package.domain = fr.perso
version = 1.0

source.dir = .
source.include_exts = py,kv,png
source.exclude_dirs = bin

# certifi : certificats racine pour les connexions HTTPS
requirements = python3,kivy,certifi

icon.filename = %(source.dir)s/images/icon.png
icon.adaptive_foreground.filename = %(source.dir)s/images/icon_fg.png
icon.adaptive_background.filename = %(source.dir)s/images/icon_bg.png
presplash.filename = %(source.dir)s/images/presplash.png
android.presplash_color = #0f0f0f

orientation = portrait
fullscreen = 0

android.permissions = INTERNET
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
