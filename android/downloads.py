"""Téléchargements pour regarder ou écouter hors ligne, avec yt-dlp.

YouTube ne fournit plus de fichier contenant à la fois l'image et le son : pour une vidéo,
Aske télécharge les deux séparément puis les assemble avec Android (java/…/Muxer.java).
Les vidéos restent dans le stockage privé d'Aske.

Pour un MP3, Aske télécharge le son (M4A), le convertit avec ffmpeg (livré avec Aske :
recipes/ffmpeg_mp3), avec la miniature en pochette, puis le range dans le dossier Musique/Aske
du téléphone (java/…/MusicStore.java), où les autres applications le voient aussi.
Les « Son » d'Aske 1.4 (M4A, dans le stockage privé) restent lisibles.
"""

import errno
import glob
import os
import re
import shutil
import subprocess
import threading
import time

from kivy.utils import platform

from history import JsonFile

if platform == "android":
    from jnius import autoclass, detach

    # Chargées ici, dans le fil principal : depuis un autre fil, Java ne trouverait pas
    # les classes d'Aske.
    DownloadService = autoclass("fr.perso.freetube.DownloadService")
    MusicStore = autoclass("fr.perso.freetube.MusicStore")
    Muxer = autoclass("fr.perso.freetube.Muxer")
    PythonActivity = autoclass("org.kivy.android.PythonActivity")

WATCH_PAGE = "https://www.youtube.com/watch?v={}"
# Son AAC (M4A) : lisible partout, et le seul qu'Android sache assembler avec l'image en MP4.
AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]"
# Image H.264 jusqu'en 720p : nette sur un téléphone, sans fichiers énormes.
VIDEO_FORMAT = "bestvideo[vcodec^=avc1][height<=720]/bestvideo[vcodec^=avc1]"
# Types de téléchargement qui ne contiennent que du son (« audio » : M4A d'Aske 1.4).
AUDIO_KINDS = ("mp3", "audio")
# Lignes de l'avancement de ffmpeg (-progress) : « clé=valeur ». Les autres sont ses erreurs.
PROGRESS_LINE = re.compile(r"^[\w.]+=")


class Downloads(JsonFile):
    """{id: {"video", "kind", "status", "progress", "step", "file", "uri", "size", "error",
    "added"}}

    kind : "video", "mp3" ou "audio" (Aske 1.4). status : "attente", "cours", "fini" ou "echec".
    step : "conversion" pendant la conversion en MP3. file : fichier dans le stockage privé ;
    uri : adresse d'un MP3 dans le dossier Musique.
    """


class Silent:
    """Journal de yt-dlp : ses messages sont ignorés, ses erreurs arrivent en exceptions."""

    def debug(self, message):
        pass

    info = warning = error = debug


class DownloadManager:
    """File de téléchargements, traités un par un par un fil d'exécution à part."""

    def __init__(self, folder, store_path, on_change, fetch):
        os.makedirs(folder, exist_ok=True)
        self.folder = folder
        self.store = Downloads(store_path)
        self.on_change = on_change  # appelée après chaque changement, depuis n'importe quel fil
        self.fetch = fetch  # télécharge une petite adresse (la miniature) et renvoie son contenu
        self.worker = None
        self.cancelled = set()
        self.mp3_bitrate = 128  # kbit/s, réglé dans les Paramètres
        self.ffmpeg = find_ffmpeg()
        self.encoder = None  # encodeur MP3 de ce ffmpeg, cherché à la première conversion
        # Téléchargements interrompus par l'arrêt d'Aske : ils reprennent où ils en étaient.
        for entry in self.store.data.values():
            if entry["status"] == "cours":
                entry["status"] = "attente"

    def get(self, video_id):
        return self.store.data.get(video_id)

    def entries(self):
        """Les téléchargements, du plus récent au plus ancien."""
        with self.store.lock:
            return sorted(self.store.data.values(), key=lambda entry: entry["added"], reverse=True)

    def local_video(self, video_id):
        """Chemin de la vidéo téléchargée, si elle est complète (sinon None)."""
        return self._local(video_id, ("video",))

    def local_audio(self, video_id):
        """Adresse du MP3 (ou du M4A d'Aske 1.4), s'il est complet et toujours là (sinon None)."""
        return self._local(video_id, AUDIO_KINDS)

    def _local(self, video_id, kinds):
        entry = self.store.data.get(video_id)
        if not entry or entry["status"] != "fini" or entry["kind"] not in kinds:
            return None
        if entry.get("uri"):
            return entry["uri"] if music_exists(entry["uri"]) else None
        path = os.path.join(self.folder, entry["file"])
        return path if os.path.exists(path) else None

    def thumbnail(self, video_id):
        return os.path.join(self.folder, video_id + ".jpg")

    def total_size(self):
        return sum(entry["size"] for entry in self.entries() if entry["status"] == "fini")

    def add(self, video, kind):
        """Ajoute une vidéo à la file : kind vaut "video" (image et son) ou "mp3" (son seul)."""
        with self.store.lock:
            self.store.data[video["id"]] = {
                "video": video, "kind": kind, "status": "attente", "progress": 0,
                "file": "", "size": 0, "error": "", "added": time.time(),
            }
            self.store.save()
        self.on_change()
        self.start()

    def retry(self, video_id):
        with self.store.lock:
            entry = self.store.data.get(video_id)
            if entry and entry["status"] == "echec":
                entry.update(status="attente", error="")
                self.store.save()
        self.on_change()
        self.start()

    def remove(self, video_id):
        """Supprime le téléchargement et ses fichiers, MP3 du dossier Musique compris (et
        l'arrête s'il est en cours)."""
        with self.store.lock:
            entry = self.store.data.pop(video_id, None)
            if entry and entry["status"] == "cours":
                self.cancelled.add(video_id)  # le fil l'arrête puis efface les fichiers
            else:
                self._delete_files(video_id)
                if entry and entry.get("uri"):
                    delete_music(entry["uri"])
            self.store.save()
        self.on_change()

    def start(self):
        """Lance le fil de téléchargement s'il y a du travail.

        À appeler depuis l'application affichée : Android n'autorise le démarrage du
        service de premier plan que dans ce cas.
        """
        with self.store.lock:
            waiting = [entry for entry in self.store.data.values() if entry["status"] == "attente"]
            if self.worker is not None or not waiting:
                return
            self.worker = threading.Thread(target=self._work, daemon=True)
            self.worker.start()
            if platform == "android":
                DownloadService.start(PythonActivity.mActivity, waiting[0]["video"]["title"])

    def _work(self):
        try:
            while True:
                entry = self._next()
                if entry is None:
                    return
                self._download(entry)
        finally:
            if platform == "android":
                detach()  # obligatoire avant la fin d'un fil qui a appelé Java

    def _next(self):
        """Prend le plus ancien téléchargement en attente ; s'il n'y en a plus, arrête le fil."""
        with self.store.lock:
            waiting = [entry for entry in self.store.data.values() if entry["status"] == "attente"]
            if not waiting:
                self.worker = None
                if platform == "android":
                    DownloadService.stop(PythonActivity.mActivity)
                return None
            entry = min(waiting, key=lambda entry: entry["added"])
            entry.update(status="cours", progress=0, step="", error="")
            self.store.save()
        self.on_change()
        return entry

    def _download(self, entry):
        import yt_dlp  # lourd : chargé seulement au premier téléchargement

        video_id = entry["video"]["id"]
        self._notify(entry)
        result = {}
        try:
            self._save_thumbnail(entry["video"])
            if entry["kind"] == "mp3":
                # Le téléchargement est rapide ; la conversion prend plus de temps.
                sound, duration = self._fetch(entry, AUDIO_FORMAT, "son", 0, 40)
                mp3 = os.path.join(self.folder, video_id + ".mp3")
                self._convert(entry, sound, mp3, duration, 40, 99)
                os.remove(sound)
                result = {"file": "", "size": os.path.getsize(mp3),
                          "uri": save_to_music(mp3, music_name(entry["video"]["title"]))}
                os.remove(mp3)
            elif entry["kind"] == "audio":  # Aske 1.4 ; plus proposé, mais repris s'il était en cours
                sound, duration = self._fetch(entry, AUDIO_FORMAT, "son", 0, 100)
                final = os.path.join(self.folder, video_id + os.path.splitext(sound)[1])
                os.replace(sound, final)
                result = {"file": os.path.basename(final), "size": os.path.getsize(final)}
            else:
                image, duration = self._fetch(entry, VIDEO_FORMAT, "image", 0, 80)
                sound, duration = self._fetch(entry, AUDIO_FORMAT, "son", 80, 98)
                final = os.path.join(self.folder, video_id + ".mp4")
                mux(image, sound, final)
                os.remove(image)
                os.remove(sound)
                result = {"file": os.path.basename(final), "size": os.path.getsize(final)}
        except yt_dlp.utils.DownloadCancelled:
            pass
        except Exception as error:  # réseau, vidéo indisponible, place insuffisante…
            with self.store.lock:
                entry.update(status="echec", step="", error=describe(error))
                self.store.save()
            self.on_change()
            return
        with self.store.lock:
            if video_id in self.cancelled or self.store.data.get(video_id) is not entry:
                self.cancelled.discard(video_id)
                self._delete_files(video_id)
                if result.get("uri"):
                    delete_music(result["uri"])
                return
            entry.update(status="fini", progress=100, step="", **result)
            self.store.save()
        self.on_change()

    def _fetch(self, entry, selector, part, low, high):
        """Télécharge une piste avec yt-dlp ; l'avancement va de low à high %.
        Renvoie le chemin du fichier et la durée de la vidéo en secondes (None si inconnue)."""
        import yt_dlp

        video_id = entry["video"]["id"]

        def hook(status):
            if video_id in self.cancelled:
                raise yt_dlp.utils.DownloadCancelled()
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            if status["status"] == "downloading" and total:
                self._progress(entry, low + (high - low) * status["downloaded_bytes"] / total)

        options = {
            "format": selector,
            "outtmpl": os.path.join(self.folder, f"{video_id}.{part}.%(ext)s"),
            "progress_hooks": [hook],
            "logger": Silent(),
            "noprogress": True,
            "noplaylist": True,
            "cachedir": False,
            "retries": 10,
            "fragment_retries": 10,
            # Fichier gardé tel quel, sans retouche par ffmpeg : comme sur Android, où yt-dlp ne
            # trouve pas le ffmpeg d'Aske. Le M4A de YouTube se lit et se convertit très bien.
            "fixup": "never",
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(WATCH_PAGE.format(video_id), download=True)
        return info["requested_downloads"][0]["filepath"], info.get("duration")

    def _convert(self, entry, sound, output, duration, low, high):
        """Convertit le son en MP3 avec ffmpeg, avec le titre, la chaîne et la miniature
        (pochette) ; l'avancement va de low à high %."""
        import yt_dlp

        video = entry["video"]
        if not self.ffmpeg:
            raise RuntimeError("ffmpeg est introuvable\u00a0: impossible de créer le MP3.")
        if self.encoder is None:
            self.encoder = mp3_encoder(self.ffmpeg)
        # Les fichiers lus d'abord (-i), puis ce qu'on en fait.
        command = [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", sound]
        cover = self.thumbnail(video["id"])
        if os.path.exists(cover):
            command += ["-i", cover, "-map", "0:a", "-map", "1:v", "-c:v", "copy",
                        "-disposition:v", "attached_pic", "-metadata:s:v", "comment=Cover (front)"]
        else:
            command += ["-map", "0:a"]
        command += ["-map_metadata", "-1", "-c:a", self.encoder, "-b:a", f"{self.mp3_bitrate}k",
                    "-id3v2_version", "3", "-metadata", "title=" + video["title"],
                    "-metadata", "artist=" + video.get("channel", ""),
                    "-progress", "pipe:1", "-nostats", output]
        with self.store.lock:
            entry["step"] = "conversion"
        self._progress(entry, low, force=True)
        # stderr mêlé à stdout : un seul tuyau à lire, sans risque de blocage.
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, errors="replace", env=ffmpeg_env())
        errors = []
        reached = 0  # secondes déjà converties
        try:
            for line in process.stdout:
                if video["id"] in self.cancelled:
                    raise yt_dlp.utils.DownloadCancelled()
                line = line.strip()
                if not PROGRESS_LINE.match(line):
                    errors.append(line)
                elif line.startswith("out_time_us=") and line[12:].isdigit() and duration:
                    # Le dernier relevé donne la durée de la pochette (0,04 s) : on garde le maximum.
                    reached = max(reached, int(line[12:]) / 1_000_000)
                    self._progress(entry, low + (high - low) * min(1, reached / duration))
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
        if process.returncode:
            detail = next((line for line in reversed(errors) if line), "")
            raise RuntimeError("Conversion en MP3 impossible" + (f"\u00a0: {detail}" if detail else "."))

    def _progress(self, entry, percent, force=False):
        percent = int(percent)
        if percent != entry["progress"] or force:
            entry["progress"] = percent
            self._notify(entry)
            self.on_change()

    def _notify(self, entry):
        if platform == "android":
            DownloadService.update(PythonActivity.mActivity, entry["video"]["title"], entry["progress"])

    def _save_thumbnail(self, video):
        """Garde la miniature : la liste des téléchargements s'affiche aussi hors ligne."""
        path = self.thumbnail(video["id"])
        if not os.path.exists(path):
            try:
                data = self.fetch(video["thumbnail"])
            except Exception:
                return  # la liste affichera un cadre vide à la place
            with open(path, "wb") as file:
                file.write(data)

    def _delete_files(self, video_id):
        for path in glob.glob(os.path.join(self.folder, glob.escape(video_id) + ".*")):
            os.remove(path)


def stream_url(video_id):
    """Adresse du son d'une vidéo, pour l'écouter sans la télécharger, et les en-têtes HTTP à
    envoyer avec (le lecteur d'Android lit cette adresse directement). Elle expire au bout de
    quelques heures. Lève une exception si la vidéo est introuvable ou sans connexion."""
    import yt_dlp

    options = {"format": AUDIO_FORMAT, "logger": Silent(), "noplaylist": True, "cachedir": False}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(WATCH_PAGE.format(video_id), download=False)
    return info["url"], info.get("http_headers") or {}


def cache_folder():
    """Dossier des fichiers jetables (pochettes des pistes en ligne) : Android peut le vider."""
    if platform == "android":
        folder = PythonActivity.mActivity.getCacheDir().getAbsolutePath()
    else:
        import tempfile
        folder = os.path.join(tempfile.gettempdir(), "aske")
    os.makedirs(folder, exist_ok=True)
    return folder


def find_ffmpeg():
    """Chemin du programme ffmpeg : celui livré avec Aske sur Android, celui du système sur PC."""
    if platform == "android":
        return os.path.join(native_folder(), "libffmpeg.so")
    return shutil.which("ffmpeg")


def native_folder():
    """Dossier des bibliothèques d'Aske : le seul où Android permet de lancer un programme."""
    return PythonActivity.mActivity.getApplicationInfo().nativeLibraryDir


def ffmpeg_env():
    """Sur Android, ffmpeg trouve l'encodeur MP3 (libshine.so) dans le dossier des bibliothèques."""
    if platform == "android":
        return dict(os.environ, LD_LIBRARY_PATH=native_folder())
    return None


def mp3_encoder(program):
    """libmp3lame, le meilleur encodeur MP3, s'il est là (ffmpeg du PC) ; sinon libshine, celui
    du ffmpeg d'Aske."""
    try:
        encoders = subprocess.run([program, "-hide_banner", "-encoders"], capture_output=True,
                                  text=True, errors="replace", timeout=20, env=ffmpeg_env()).stdout
    except (OSError, subprocess.SubprocessError):
        return "libshine"
    return "libmp3lame" if " libmp3lame " in encoders else "libshine"


def music_name(title):
    """Nom du MP3 dans le dossier Musique : le titre de la vidéo, sans les caractères interdits."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", title)
    name = re.sub(r"\s+", " ", name).strip(" .")[:120].strip(" .")
    return (name or "Aske") + ".mp3"


def save_to_music(path, name):
    """Copie le MP3 dans le dossier Musique/Aske ; renvoie son adresse (content://… sur
    Android, chemin sur PC)."""
    if platform == "android":
        return MusicStore.save(PythonActivity.mActivity, path, name)
    folder = os.path.join(music_folder(), "Aske")
    os.makedirs(folder, exist_ok=True)
    base, extension = os.path.splitext(name)
    target = os.path.join(folder, name)
    number = 2
    while os.path.exists(target):
        target = os.path.join(folder, f"{base} ({number}){extension}")
        number += 1
    shutil.copyfile(path, target)
    return target


def music_folder():
    """Dossier Musique du PC (~/Musique en français)."""
    try:
        folder = subprocess.run(["xdg-user-dir", "MUSIC"], capture_output=True, text=True).stdout.strip()
    except OSError:
        folder = ""
    return folder or os.path.expanduser("~/Music")


def music_exists(address):
    if platform == "android":
        return MusicStore.exists(PythonActivity.mActivity, address)
    return os.path.exists(address)


def delete_music(address):
    if platform == "android":
        MusicStore.delete(PythonActivity.mActivity, address)
    elif os.path.exists(address):
        os.remove(address)


def mux(image, sound, output):
    """Assemble l'image et le son en un MP4, avec Android (MediaMuxer, dans Muxer.java)."""
    if platform != "android":
        raise RuntimeError("l'assemblage de l'image et du son ne se fait que sur Android.")
    Muxer.mux(image, sound, output)


def describe(error):
    """Message d'erreur lisible, pour la liste des téléchargements."""
    if isinstance(error, OSError) and error.errno == errno.ENOSPC:
        return "Plus assez de place sur le téléphone."
    # « ERROR: [youtube] abc123: Video unavailable » → « Video unavailable »
    message = re.sub(r"^(ERROR: )?(\[\w+\] [\w-]+: )?", "", str(error)).strip()
    return message[:200] or type(error).__name__


def size_fr(size):
    """1234567890 → « 1,2 Go » ; 45678901 → « 44 Mo »."""
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.1f}".replace(".", ",") + "\u00a0Go"
    return f"{max(1, round(size / 1024 ** 2))}\u00a0Mo"
