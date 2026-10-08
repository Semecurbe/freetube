"""Écoute en arrière-plan, piste après piste : MP3 du téléphone, ou son des vidéos en ligne.

Sur Android, le son est joué par le service AudioService (java/…/AudioService.java) : il
continue quand on passe à une autre application ou qu'on éteint l'écran, et enchaîne les pistes
tout seul. Aske lui confie la liste, puis relève toutes les secondes où il en est, pour
l'afficher et retenir l'endroit atteint dans chaque piste (historique), même en arrière-plan.
Pour une piste en ligne, Aske trouve l'adresse du son (avec yt-dlp) quand le service la
demande, ou un peu avant la fin de la piste précédente pour enchaîner sans attendre.

Sur PC, le son passe par Kivy : de quoi essayer l'interface (les pistes en ligne sont passées).
"""

import threading
import time

from kivy.clock import Clock
from kivy.utils import platform

if platform == "android":
    from android.runnable import run_on_ui_thread
    from jnius import autoclass, detach

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
PREFETCH = 30  # secondes avant la fin d'une piste : on cherche l'adresse de la suivante


class AndroidAudio:
    """Commande AudioService. on_state(état) est appelée toutes les secondes (depuis le fil
    d'Android) tant qu'une liste est chargée, puis une dernière fois avec None à l'arrêt.

    état : {"queue", "index", "position", "duration", "playing", "loading"}, positions en
    secondes ; queue est l'objet donné à play, pour savoir à quelle liste se rapporte le
    relevé ; loading : lecture demandée, mais pas encore de son (piste en ligne qui arrive).

    resolve(piste) renvoie l'adresse en ligne d'une piste, ses en-têtes HTTP (JSON) et sa
    pochette ; elle est appelée dans un autre fil, et lève une exception en cas d'échec.
    """

    def __init__(self, on_state, resolve):
        self.on_state = on_state
        self.resolve = resolve
        self.queue = None
        self.tracks = []
        self.generation = 0  # numéro de la liste : les adresses d'une ancienne liste sont ignorées
        self.resolving = set()  # pistes dont on cherche l'adresse
        self.provided = set()  # pistes dont l'adresse a été donnée au service
        self.failed = set()  # pistes dont l'adresse n'a pas été trouvée (pas de nouvel essai d'avance)
        self.starting = 0  # heure de démarrage du service, tant qu'il n'a pas démarré
        # Gardé côté Python : sinon le ramasse-miettes le détruirait.
        self.poller = Repeat(self._poll, POLL_INTERVAL * 1000)

    @run_on_ui_thread
    def play(self, queue, tracks, index):
        """tracks : [{"uri", "title", "artist", "artwork", "start", "online", "video"}], start en
        secondes ; uri vide pour une piste en ligne (online), dont l'adresse sera cherchée."""
        self.queue = queue
        self.tracks = tracks
        self.generation += 1
        self.resolving.clear()
        self.provided.clear()
        self.failed.clear()
        if not AudioService.isActive():
            self.starting = time.time()
        # Les M4A d'Aske 1.4 sont désignés par leur chemin, les MP3 par une adresse content://.
        AudioService.play(PythonActivity.mActivity,
                          [track["uri"] if not track["uri"] or "://" in track["uri"]
                           else "file://" + track["uri"] for track in tracks],
                          ["" for track in tracks],
                          [track["title"] for track in tracks],
                          [track["artist"] for track in tracks],
                          [track["artwork"] for track in tracks],
                          [int(track["start"] * 1000) for track in tracks], index)
        if tracks[index]["online"]:
            self._resolve(index)  # sans attendre que le service la demande
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
            index = AudioService.getIndex()
            position = AudioService.getPosition() / 1000
            duration = AudioService.getDuration() / 1000
            playing = AudioService.isPlaying()
            waiting = AudioService.getWaiting()
            following = index + 1
            if waiting >= 0:
                self._resolve(waiting)
            elif (playing and duration and duration - position < PREFETCH
                  and following < len(self.tracks) and self.tracks[following]["online"]
                  and following not in self.provided and following not in self.failed):
                self._resolve(following)  # pour enchaîner sans attendre
            self.on_state({"queue": self.queue, "index": index, "position": position,
                           "duration": duration, "playing": playing,
                           "loading": AudioService.isLoading()})
        elif self.starting and time.time() - self.starting < START_TIMEOUT:
            return  # le service démarre
        elif self.queue is not None:
            # Fin de la liste, ou « Arrêter » (dans Aske ou dans la notification).
            self.queue = None
            self.starting = 0
            self.poller.stop()
            self.on_state(None)

    def _resolve(self, number):
        """Cherche l'adresse de la piste n° number dans un autre fil (yt-dlp met une seconde ou
        deux, et ne doit pas bloquer Android)."""
        if number in self.resolving:
            return
        self.resolving.add(number)
        threading.Thread(target=self._find, daemon=True,
                         args=(self.generation, number, self.tracks[number])).start()

    def _find(self, generation, number, track):
        try:
            try:
                uri, headers, artwork = self.resolve(track)
            except Exception:  # vidéo retirée, pas de connexion… : le service passera la piste
                uri, headers, artwork = "", "", ""
            self._provide(generation, number, uri, headers, artwork)
        finally:
            detach()  # obligatoire avant la fin d'un fil qui a appelé Java

    @run_on_ui_thread
    def _provide(self, generation, number, uri, headers, artwork):
        if generation != self.generation:
            return  # une autre liste a été lancée entre-temps
        self.resolving.discard(number)
        (self.provided if uri else self.failed).add(number)
        AudioService.provide(number, uri, headers, artwork)
        self._poll()


class DesktopAudio:
    """Même rôle qu'AndroidAudio, avec le son de Kivy (sur PC, pour essayer l'interface).

    Le son SDL2 de Kivy ne sait ni donner sa position ni avancer : la position est calculée
    avec l'horloge, et le son repart toujours du début de la piste. Il ne lit pas en ligne :
    les pistes non téléchargées sont passées.
    """

    def __init__(self, on_state, resolve=None):
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
        uri = self.tracks[index]["uri"]
        self.sound = SoundLoader.load(uri) if uri else None
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
                       "playing": self.playing, "loading": False})


AudioPlayer = AndroidAudio if platform == "android" else DesktopAudio
