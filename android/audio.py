"""Écoute des MP3 en arrière-plan, piste après piste.

Sur Android, le son est joué par le service AudioService (java/…/AudioService.java) : il
continue quand on passe à une autre application ou qu'on éteint l'écran, et enchaîne les pistes
tout seul. Aske lui confie la liste, puis relève toutes les secondes où il en est, pour
l'afficher et retenir l'endroit atteint dans chaque piste (historique), même en arrière-plan.

Sur PC, le son passe par Kivy : de quoi essayer l'interface.
"""

import time

from kivy.clock import Clock
from kivy.utils import platform

if platform == "android":
    from android.runnable import run_on_ui_thread
    from jnius import autoclass

    from player import Repeat

    AudioService = autoclass("fr.perso.freetube.AudioService")
    PythonActivity = autoclass("org.kivy.android.PythonActivity")
else:
    def run_on_ui_thread(function):  # AndroidAudio n'est utilisée que sur Android
        return function

POLL_INTERVAL = 1  # secondes entre deux relevés
START_TIMEOUT = 5  # secondes laissées au service pour démarrer
REWIND = 10  # secondes : boutons « reculer » et « avancer »
FORWARD = 30


class AndroidAudio:
    """Commande AudioService. on_state(état) est appelée toutes les secondes (depuis le fil
    d'Android) tant qu'une liste est chargée, puis une dernière fois avec None à l'arrêt.

    état : {"queue", "index", "position", "duration", "playing"}, positions en secondes ;
    queue est l'objet donné à play, pour savoir à quelle liste se rapporte le relevé.
    """

    def __init__(self, on_state):
        self.on_state = on_state
        self.queue = None
        self.starting = 0  # heure de démarrage du service, tant qu'il n'a pas démarré
        # Gardé côté Python : sinon le ramasse-miettes le détruirait.
        self.poller = Repeat(self._poll, POLL_INTERVAL * 1000)

    @run_on_ui_thread
    def play(self, queue, tracks, index):
        """tracks : [{"uri", "title", "artist", "artwork", "start"}], start en secondes."""
        self.queue = queue
        if not AudioService.isActive():
            self.starting = time.time()
        # Les M4A d'Aske 1.4 sont désignés par leur chemin, les MP3 par une adresse content://.
        AudioService.play(PythonActivity.mActivity,
                          [track["uri"] if "://" in track["uri"] else "file://" + track["uri"]
                           for track in tracks],
                          [track["title"] for track in tracks],
                          [track["artist"] for track in tracks],
                          [track["artwork"] for track in tracks],
                          [int(track["start"] * 1000) for track in tracks], index)
        self.poller.start()

    @run_on_ui_thread
    def _command(self, action):
        AudioService.command(action)
        self._poll()

    def toggle(self):
        self._command(AudioService.TOGGLE)

    def pause(self):
        self._command(AudioService.PAUSE)

    def next(self):
        self._command(AudioService.NEXT)

    def previous(self):
        self._command(AudioService.PREVIOUS)

    def rewind(self):
        self._command(AudioService.REWIND)

    def forward(self):
        self._command(AudioService.FORWARD)

    def stop(self):
        self._command(AudioService.STOP)

    @run_on_ui_thread
    def seek(self, seconds):
        AudioService.seekTo(int(seconds * 1000))
        self._poll()

    @run_on_ui_thread
    def jump(self, index):
        AudioService.jump(index)
        self._poll()

    def _poll(self):
        if AudioService.isActive():
            self.starting = 0
            self.on_state({"queue": self.queue, "index": AudioService.getIndex(),
                           "position": AudioService.getPosition() / 1000,
                           "duration": AudioService.getDuration() / 1000,
                           "playing": AudioService.isPlaying()})
        elif self.starting and time.time() - self.starting < START_TIMEOUT:
            return  # le service démarre
        elif self.queue is not None:
            # Fin de la liste, ou « Arrêter » (dans Aske ou dans la notification).
            self.queue = None
            self.starting = 0
            self.poller.stop()
            self.on_state(None)


class DesktopAudio:
    """Même rôle qu'AndroidAudio, avec le son de Kivy (sur PC, pour essayer l'interface).

    Le son SDL2 de Kivy ne sait ni donner sa position ni avancer : la position est calculée
    avec l'horloge, et le son repart toujours du début de la piste.
    """

    def __init__(self, on_state):
        self.on_state = on_state
        self.queue = None
        self.tracks = []
        self.index = 0
        self.sound = None
        self.offset = 0  # position de reprise, en secondes
        self.started = 0  # pendant la lecture : heure qui correspond au début de la piste
        self.playing = False
        self.event = None

    def play(self, queue, tracks, index):
        self.queue = queue
        self.tracks = [dict(track) for track in tracks]
        if self.event is None:
            self.event = Clock.schedule_interval(lambda dt: self._poll(), POLL_INTERVAL)
        self._load(index)
        self._poll()

    def _load(self, index):
        from kivy.core.audio import SoundLoader

        self._release()
        self.index = index
        self.offset = self.tracks[index]["start"]
        self.sound = SoundLoader.load(self.tracks[index]["uri"])
        self.playing = False
        if self.sound is None:
            return self._finished()  # fichier supprimé ou illisible : piste suivante
        self._start()

    def _start(self):
        self.playing = True
        self.sound.play()
        if self.offset:
            self.sound.seek(self.offset)
        self.started = time.time() - self.offset

    def _release(self):
        if self.sound is not None:
            self.tracks[self.index]["start"] = self.position()
            self.sound.stop()
            self.sound.unload()
            self.sound = None

    def _finished(self):
        # Écoutée jusqu'au bout : elle repartira du début (_release ne garde pas la fin).
        if self.sound is not None:
            self.sound.unload()
            self.sound = None
        self.tracks[self.index]["start"] = 0
        if self.index + 1 < len(self.tracks):
            self._load(self.index + 1)
        else:
            self.stop()

    def position(self):
        if self.sound is None:
            return 0
        if not self.playing:
            return self.offset
        return self.sound.get_pos() or time.time() - self.started

    def toggle(self):
        if self.sound is None:
            return
        if self.playing:
            self.pause()
        else:
            self._start()
        self._poll()

    def pause(self):
        if self.playing:
            self.offset = self.position()
            self.playing = False
            self.sound.stop()
            self._poll()

    def next(self):
        if self.index + 1 < len(self.tracks):
            self._load(self.index + 1)
        self._poll()

    def previous(self):
        if self.index == 0 or self.position() > 5:
            self.seek(0)
        else:
            self._load(self.index - 1)
            self._poll()

    def rewind(self):
        self.seek(self.position() - REWIND)

    def forward(self):
        self.seek(self.position() + FORWARD)

    def seek(self, seconds):
        if self.sound is None:
            return
        seconds = max(0, min(seconds, (self.sound.length or 0) - 1))
        if self.playing:
            self.sound.seek(seconds)
            self.started = time.time() - seconds
        else:
            self.offset = seconds
        self._poll()

    def jump(self, index):
        self._load(index)
        self._poll()

    def stop(self):
        self._release()
        self.playing = False
        if self.event is not None:
            self.event.cancel()
            self.event = None
        if self.queue is not None:
            self.queue = None
            self.on_state(None)

    def _poll(self):
        if self.queue is None:
            return
        if self.playing and self.sound is not None and (
                self.sound.state == "stop" or self.position() >= (self.sound.length or float("inf"))):
            self._finished()  # fin de la piste
            if self.queue is None:
                return
        self.on_state({"queue": self.queue, "index": self.index, "position": self.position(),
                       "duration": self.sound.length if self.sound else 0,
                       "playing": self.playing})


AudioPlayer = AndroidAudio if platform == "android" else DesktopAudio
