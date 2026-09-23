"""Historique des vidéos regardées, avec la position atteinte dans chacune pour la reprendre."""

import json
import os
import threading
import time

MAX_VIDEOS = 200
SAVE_INTERVAL = 10  # secondes entre deux enregistrements de la position pendant la lecture
MIN_RESUME = 10  # moins de 10 s regardées : la vidéo repart du début
REWIND = 3  # la reprise commence 3 s plus tôt, pour retrouver le fil


class JsonFile:
    """Dictionnaire enregistré dans un fichier JSON, partagé entre plusieurs fils d'exécution.

    L'écriture passe par un fichier temporaire : même arrêtée brutalement, Aske ne laisse
    jamais un fichier à moitié écrit.
    """

    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        try:
            with open(path, encoding="utf-8") as file:
                self.data = json.load(file)
        except (OSError, ValueError):
            self.data = {}

    def save(self):
        with self.lock:
            temporary = self.path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as file:
                json.dump(self.data, file, ensure_ascii=False)
            os.replace(temporary, self.path)


class History(JsonFile):
    """Vidéos regardées : {id: {"video", "position", "duration", "finished", "watched"}}.

    La position est relevée pendant la lecture, y compris écran éteint (par le fil
    d'Android) : d'où le verrou de JsonFile.
    """

    saved_at = 0

    def start(self, video):
        """Une vidéo commence : la place en tête de l'historique et renvoie la seconde
        où la reprendre (0 : depuis le début)."""
        with self.lock:
            entry = self.data.pop(video["id"], {})
            # Vue en entier la dernière fois (ou direct) : elle repart du début.
            position = 0 if entry.get("finished") or video.get("live") else entry.get("position", 0)
            entry.update(video=video, watched=time.time(), finished=False, position=position)
            self.data[video["id"]] = entry
            for old in sorted(self.data, key=lambda key: self.data[key]["watched"])[:-MAX_VIDEOS]:
                del self.data[old]
            self.save()
        return max(0, int(position) - REWIND) if position >= MIN_RESUME else 0

    def update(self, video_id, position, duration):
        """Position relevée pendant la lecture, enregistrée au plus toutes les SAVE_INTERVAL s."""
        # Juste après l'ouverture, le lecteur indique 0 avant de sauter à la reprise :
        # l'enregistrer effacerait l'endroit où l'on en était.
        if position < 1:
            return
        with self.lock:
            entry = self.data.get(video_id)
            if entry is None or entry["finished"] or entry["video"].get("live"):
                return
            entry["position"] = position
            if duration:
                entry["duration"] = duration
                # Moins de 20 s restantes (ou 10 % pour une vidéo courte) : vue en entier.
                entry["finished"] = position >= duration - min(20, duration / 10)
            if entry["finished"] or time.time() - self.saved_at >= SAVE_INTERVAL:
                self.saved_at = time.time()
                self.save()

    def progress(self, video_id):
        """Part de la vidéo déjà vue, de 0 à 1 : la barre rouge sous sa miniature."""
        entry = self.data.get(video_id)
        if not entry:
            return 0
        if entry["finished"]:
            return 1
        duration = entry.get("duration") or 0
        return min(1, entry["position"] / duration) if duration else 0

    def recent(self):
        """Les vidéos de la plus récemment regardée à la plus ancienne."""
        with self.lock:
            return sorted(self.data.values(), key=lambda entry: entry["watched"], reverse=True)

    def to_resume(self):
        """Dernière vidéo commencée et pas terminée : pour la barre « Reprendre »."""
        for entry in self.recent():
            if (not entry["finished"] and entry["position"] >= MIN_RESUME
                    and not entry["video"].get("live")):
                return entry
        return None

    def remove(self, video_id):
        with self.lock:
            self.data.pop(video_id, None)
            self.save()

    def clear(self):
        with self.lock:
            self.data.clear()
            self.save()
