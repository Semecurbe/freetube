"""Playlists : listes de vidéos créées dans Aske, lues l'une après l'autre."""

import time
import uuid

from history import JsonFile


class Playlists(JsonFile):
    """{"listes": [{"id", "name", "videos": [vidéo, …], "updated"}]}"""

    def all(self):
        """Les playlists, de la plus récemment modifiée à la plus ancienne."""
        return sorted(self.data.setdefault("listes", []), key=lambda playlist: playlist["updated"],
                      reverse=True)

    def get(self, playlist_id):
        return next((playlist for playlist in self.all() if playlist["id"] == playlist_id), None)

    def create(self, name):
        playlist = {"id": uuid.uuid4().hex, "name": name, "videos": [], "updated": time.time()}
        self.data.setdefault("listes", []).append(playlist)
        self.save()
        return playlist

    def delete(self, playlist_id):
        self.data["listes"] = [playlist for playlist in self.all() if playlist["id"] != playlist_id]
        self.save()

    def toggle(self, playlist_id, video):
        """Ajoute la vidéo à la fin de la playlist, ou l'en retire si elle y est déjà."""
        playlist = self.get(playlist_id)
        if any(item["id"] == video["id"] for item in playlist["videos"]):
            self.remove_video(playlist_id, video["id"])
        else:
            playlist["videos"].append(video)
            playlist["updated"] = time.time()
            self.save()

    def remove_video(self, playlist_id, video_id):
        playlist = self.get(playlist_id)
        playlist["videos"] = [item for item in playlist["videos"] if item["id"] != video_id]
        playlist["updated"] = time.time()
        self.save()

    def containing(self, video_id):
        """ID des playlists qui contiennent la vidéo."""
        return {playlist["id"] for playlist in self.all()
                if any(item["id"] == video_id for item in playlist["videos"])}
