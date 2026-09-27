"""FFmpeg réduit à ce dont Aske a besoin : convertir le son de YouTube (AAC, dans un M4A) en MP3,
avec la miniature de la vidéo en pochette.

La recette « ffmpeg » de python-for-android sait aussi produire des MP3, mais elle embarque alors
x264, libvpx et tous les formats (plus de 20 Mo). Celle-ci ne garde que le décodeur AAC, l'encodeur
MP3 libshine (recette « libshine ») et la lecture des images JPEG : le programme pèse moins de 2 Mo.

Le programme ffmpeg est livré sous le nom libffmpeg.so : sur Android, seul le dossier des
bibliothèques d'une application peut contenir des fichiers exécutables (voir downloads.py).
"""

from multiprocessing import cpu_count
from os import makedirs
from os.path import realpath
from shutil import copyfile

import sh
from pythonforandroid.logger import shprint
from pythonforandroid.recipe import Recipe
from pythonforandroid.util import current_directory


class FFmpegMp3Recipe(Recipe):
    version = "8.0.1"
    url = "https://www.ffmpeg.org/releases/ffmpeg-{version}.tar.xz"
    depends = ["libshine"]
    # Trouve libshine sans pkg-config (absent de la compilation pour Android).
    patches = ["libshine.patch"]
    built_libraries = {"libffmpeg.so": "lib"}

    def build_arch(self, arch):
        shine = Recipe.get_recipe("libshine", self.ctx).get_build_dir(arch.arch)
        ndk = self.ctx.ndk
        with current_directory(self.get_build_dir(arch.arch)):
            env = arch.get_env()
            env["CFLAGS"] += f" -I{shine}/include"
            env["LDFLAGS"] += f" -L{shine}/lib"

            flags = [
                # Rien d'autre que ce qu'on active ensuite, et aucune bibliothèque du système.
                "--disable-everything",
                "--disable-autodetect",
                "--disable-network",
                "--disable-avdevice",
                "--disable-swscale",
                "--disable-doc",
                "--disable-debug",
                "--disable-programs",
                "--enable-ffmpeg",
                "--enable-small",
                # Bibliothèques de FFmpeg intégrées au programme : un seul fichier à livrer.
                "--enable-static",
                "--disable-shared",
                "--enable-pic",
                # M4A (AAC) → MP3, avec la miniature JPEG en pochette.
                "--enable-libshine",
                "--enable-encoder=libshine",
                "--enable-decoder=aac,mjpeg",
                "--enable-parser=aac,mjpeg,mpegaudio",
                "--enable-demuxer=mov,image2",
                "--enable-muxer=mp3",
                # pipe : l'avancement de la conversion est lu par Aske (-progress pipe:1).
                "--enable-protocol=file,pipe",
                "--enable-filter=abuffer,abuffersink,anull,aformat,aresample",
            ]

            if "arm64" in arch.arch:
                arch_flag = "aarch64"
            elif "x86" in arch.arch:
                arch_flag = "x86"
                flags.append("--disable-asm")  # nasm n'est pas installé pour la compilation
            else:
                arch_flag = "arm"

            flags += [
                "--target-os=android",
                "--enable-cross-compile",
                f"--arch={arch_flag}",
                # Pour Android, FFmpeg utilise clang : aarch64-linux-android24-clang, du NDK.
                f"--cross-prefix={arch.target}-",
                f"--ar={ndk.llvm_ar}",
                f"--ranlib={ndk.llvm_ranlib}",
                f"--nm={ndk.llvm_nm}",
                f"--strip={ndk.llvm_strip}",
                f"--sysroot={ndk.sysroot}",
                f"--prefix={realpath('.')}",
            ]

            shprint(sh.Command("./configure"), *flags, _env=env)
            shprint(sh.make, "-j", str(cpu_count()), _env=env)
            makedirs("lib", exist_ok=True)
            copyfile("ffmpeg", "lib/libffmpeg.so")


recipe = FFmpegMp3Recipe()
