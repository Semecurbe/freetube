"""Téléchargements pour regarder ou écouter hors ligne, avec yt-dlp.

YouTube ne fournit plus de fichier contenant à la fois l'image et le son : pour une vidéo,
Aske télécharge les deux séparément puis les assemble avec Android (java/…/Muxer.java).
Le son seul donne un fichier M4A. Les fichiers restent dans le stockage privé d'Aske.
"""

import errno
import glob
import os
import re
import threading
import time

from kivy.utils import platform

from history import JsonFile

if platform == "android":
    from jnius import autoclass, detach

    # Chargées ici, dans le fil principal : depuis un autre fil, Java ne trouverait pas
    # les classes d'Aske.
    DownloadService = autoclass("fr.perso.freetube.DownloadService")
    Muxer = autoclass("fr.perso.freetube.Muxer")
    PythonActivity = autoclass("org.kivy.android.PythonActivity")

WATCH_PAGE = "https://www.youtube.com/watch?v={}"
# Son AAC (M4A) : lisible partout, et le seul qu'Android sache assembler avec l'image en MP4.
AUDIO_FORMAT = "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]"
# Image H.264 jusqu'en 720p : nette sur un téléphone, sans fichiers énormes.
VIDEO_FORMAT = "bestvideo[vcodec^=avc1][height<=720]/bestvideo[vcodec^=avc1]"


class Downloads(JsonFile):
    """{id: {"video", "kind", "status", "progress", "file", "size", "error", "added"}}

    kind : "video" ou "audio". status : "attente", "cours", "fini" ou "echec".
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

    def local_file(self, video_id):
        """Chemin du fichier téléchargé, s'il est complet (sinon None)."""
        entry = self.store.data.get(video_id)
        if entry and entry["status"] == "fini":
            path = os.path.join(self.folder, entry["file"])
            if os.path.exists(path):
                return path
        return None

    def thumbnail(self, video_id):
        return os.path.join(self.folder, video_id + ".jpg")

    def total_size(self):
        return sum(entry["size"] for entry in self.entries() if entry["status"] == "fini")

    def add(self, video, kind):
        """Ajoute une vidéo à la file : kind vaut "video" (image et son) ou "audio" (son seul)."""
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
        """Supprime le téléchargement et ses fichiers (et l'arrête s'il est en cours)."""
        with self.store.lock:
            entry = self.store.data.pop(video_id, None)
            if entry and entry["status"] == "cours":
                self.cancelled.add(video_id)  # le fil l'arrête puis efface les fichiers
            else:
                self._delete_files(video_id)
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
            entry.update(status="cours", progress=0, error="")
            self.store.save()
        self.on_change()
        return entry

    def _download(self, entry):
        import yt_dlp  # lourd : chargé seulement au premier téléchargement

        video_id = entry["video"]["id"]
        self._notify(entry)
        try:
            self._save_thumbnail(entry["video"])
            if entry["kind"] == "audio":
                sound = self._fetch(entry, AUDIO_FORMAT, "son", 0, 100)
                final = os.path.join(self.folder, video_id + os.path.splitext(sound)[1])
                os.replace(sound, final)
            else:
                image = self._fetch(entry, VIDEO_FORMAT, "image", 0, 80)
                sound = self._fetch(entry, AUDIO_FORMAT, "son", 80, 98)
                final = os.path.join(self.folder, video_id + ".mp4")
                mux(image, sound, final)
                os.remove(image)
                os.remove(sound)
        except yt_dlp.utils.DownloadCancelled:
            final = None
        except Exception as error:  # réseau, vidéo indisponible, place insuffisante…
            with self.store.lock:
                entry.update(status="echec", error=describe(error))
                self.store.save()
            self.on_change()
            return
        with self.store.lock:
            if video_id in self.cancelled or self.store.data.get(video_id) is not entry:
                self.cancelled.discard(video_id)
                self._delete_files(video_id)
                return
            entry.update(status="fini", progress=100, file=os.path.basename(final),
                         size=os.path.getsize(final))
            self.store.save()
        self.on_change()

    def _fetch(self, entry, selector, part, low, high):
        """Télécharge une piste avec yt-dlp ; l'avancement va de low à high %."""
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
        }
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(WATCH_PAGE.format(video_id), download=True)
        return info["requested_downloads"][0]["filepath"]

    def _progress(self, entry, percent):
        percent = int(percent)
        if percent != entry["progress"]:
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
