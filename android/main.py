"""Aske : YouTube via yout-ube.com — chaînes, recherche, playlists, historique, téléchargements,
et écoute des MP3 en arrière-plan."""

# Lu aussi par Buildozer (version.regex dans buildozer.spec) : c'est la version de l'APK.
__version__ = "1.6"

import gzip
import http.client
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from functools import partial

import certifi
from kivy.app import App
from kivy.clock import Clock, mainthread
from kivy.core.image import Image as CoreImage
from kivy.core.window import Window
from kivy.graphics.texture import Texture
from kivy.loader import Loader
from kivy.properties import BooleanProperty, DictProperty, NumericProperty, StringProperty
from kivy.storage.jsonstore import JsonStore
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.modalview import ModalView
from kivy.uix.recycleview import RecycleView
from kivy.uix.widget import Widget
from kivy.utils import get_color_from_hex, platform

from audio import AudioPlayer
from downloads import AUDIO_KINDS, DownloadManager, cache_folder, size_fr, stream_url
from history import History
from icons import ICONS
from playlists import Playlists

if platform == "android":
    from player import AndroidPlayer

MAX_VIDEOS = 10
MAX_RESULTS = 100
DESCRIPTION_LENGTH = 300
WATCH_URL = "https://www.yout-ube.com/watch?v={}"
THUMBNAIL_URL = "https://i.ytimg.com/vi/{}/hqdefault.jpg"
FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
# API interne qu'utilise la page de recherche du site YouTube : pas besoin de clé.
SEARCH_URL = "https://www.youtube.com/youtubei/v1/search?prettyPrint=false"
# Client « web » en français : durées, vues et dates arrivent déjà rédigées en français.
SEARCH_CLIENT = {"clientName": "WEB", "clientVersion": "2.20260918.01.00", "hl": "fr", "gl": "FR"}
# Filtre « Type : Vidéo » : ni chaînes ni playlists parmi les résultats.
VIDEOS_ONLY = "EgIQAQ%3D%3D"
# Page qui lit une vidéo téléchargée ; « #t= » la reprend à la seconde voulue.
LOCAL_PLAYER = """<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<style>html, body {{ margin: 0; height: 100%; background: #000; }}
video {{ width: 100%; height: 100%; }}</style></head>
<body><video src="{media}#t={start}" poster="{poster}" controls autoplay playsinline></video></body></html>"""

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36",
    # Évite la page de consentement aux cookies que YouTube affiche en Europe.
    "Cookie": "SOCS=CAI",
    # Réponses compressées : environ 10 fois moins de données à télécharger.
    "Accept-Encoding": "gzip",
}
# Android ne fournit pas les certificats racine à Python : on utilise ceux de certifi.
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

# Espaces de noms XML du flux RSS (Atom) de YouTube.
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

ID_IN_QUERY = re.compile(r"(?:^|/channel/)(UC[\w-]{22})(?=$|[/?#&])")
ID_IN_PAGE = re.compile(
    r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"'
    r'|"externalId":"(UC[\w-]{22})"'
)
# Émojis et symboles absents de la police Roboto : ils s'afficheraient comme des carrés.
UNSUPPORTED_CHARS = re.compile("[\U00010000-\U0010FFFF\u2600-\u27BF\uFE0E\uFE0F\u200D]")

TIME_UNITS = [("an", 31_536_000), ("mois", 2_592_000), ("semaine", 604_800),
              ("jour", 86_400), ("heure", 3_600), ("minute", 60)]
# Pour l'historique, où la place manque : « il y a 15 min ».
SHORT_UNITS = [("an", 31_536_000), ("mois", 2_592_000), ("sem.", 604_800),
               ("j", 86_400), ("h", 3_600), ("min", 60)]
DOWNLOAD_KINDS = {"video": "Vidéo", "mp3": "MP3", "audio": "Son"}


class YouTubeError(Exception):
    """Erreur dont le message est affiché tel quel à l'écran."""


def http_get(url, payload=None):
    """Télécharge une adresse (en y envoyant payload en JSON, s'il est donné) ;
    une panne de réseau devient une YouTubeError."""
    headers = dict(HEADERS)
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15, context=SSL_CONTEXT) as response:
            body = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return body
    except urllib.error.HTTPError:
        raise  # YouTube a répondu (404…) : l'appelant choisit le message
    except (OSError, EOFError, http.client.HTTPException):  # dont les réponses coupées
        raise YouTubeError("Impossible de joindre YouTube. Vérifiez la connexion Internet.") from None


def is_channel(query):
    """@nom, adresse youtube.com ou ID « UC… » : une chaîne. Tout autre texte : une recherche."""
    return query.startswith("@") or "youtube.com/" in query or bool(ID_IN_QUERY.search(query))


def resolve_channel_id(query):
    """Transforme une URL de chaîne, un @handle ou un ID en ID de chaîne « UC… »."""
    match = ID_IN_QUERY.search(query)
    if match:
        return match.group(1)

    if "youtube.com/" in query:  # https://www.youtube.com/@nom, /c/nom, /user/nom…
        path = query.split("youtube.com", 1)[1]
    else:  # @nom ou nom
        path = "/@" + urllib.parse.quote(query.removeprefix("@"), safe="")

    try:
        page = http_get("https://www.youtube.com" + path).decode("utf-8", "replace")
    except urllib.error.HTTPError:
        raise YouTubeError(f"Chaîne introuvable\u00a0: «\u00a0{query}\u00a0».") from None

    match = ID_IN_PAGE.search(page)
    if not match:
        raise YouTubeError(f"Impossible de trouver l'identifiant de la chaîne «\u00a0{query}\u00a0».")
    return match.group(1) or match.group(2)


def fetch_videos(channel_id):
    """Lit le flux RSS de la chaîne : renvoie son nom et ses dernières vidéos."""
    try:
        feed = ET.fromstring(http_get(FEED_URL.format(channel_id)))
    except (urllib.error.HTTPError, ET.ParseError):
        raise YouTubeError("Impossible de récupérer les vidéos de cette chaîne.") from None

    videos = []
    for entry in feed.findall("atom:entry", NS)[:MAX_VIDEOS]:
        video_id = entry.findtext("yt:videoId", namespaces=NS)
        published = datetime.fromisoformat(entry.findtext("atom:published", namespaces=NS))
        stats = entry.find("media:group/media:community/media:statistics", NS)
        meta = time_ago(published)
        if stats is not None:
            meta += " · " + views_fr(int(stats.get("views", 0)))
        description = entry.findtext("media:group/media:description", "", NS)
        videos.append({
            "id": video_id,
            "thumbnail": THUMBNAIL_URL.format(video_id),
            "title": clean(entry.findtext("atom:title", "", NS)),
            "meta": meta,
            # Aperçu sans lignes vides, coupé proprement.
            "description": shorten(clean(re.sub(r"\s*\n\s*", "\n", description))),
        })
    return clean(feed.findtext("atom:title", "", NS)), videos


def search_videos(query):
    """Recherche des vidéos sur YouTube : produit les résultats page par page
    (une vingtaine à chaque fois), jusqu'à MAX_RESULTS."""
    body = {"query": query, "params": VIDEOS_ONLY}
    seen = set()
    while body and len(seen) < MAX_RESULTS:
        try:
            response = json.loads(http_get(SEARCH_URL, {"context": {"client": SEARCH_CLIENT}, **body}))
        except (urllib.error.HTTPError, ValueError):
            raise YouTubeError("La recherche YouTube a échoué. Réessayez plus tard.") from None

        found = {"videoRenderer": [], "continuationItemRenderer": []}
        collect(response, found)
        videos = []
        for renderer in found["videoRenderer"]:
            video_id = renderer.get("videoId")
            # Ni les diffusions à venir (rien à lire), ni les vidéos déjà données par une autre page.
            if (video_id and video_id not in seen and "upcomingEventData" not in renderer
                    and len(seen) < MAX_RESULTS):
                seen.add(video_id)
                videos.append(search_result(video_id, renderer))
        if videos:
            yield videos

        # Jeton de la page suivante (absent à la dernière page).
        commands = {"continuationCommand": []}
        collect(found["continuationItemRenderer"], commands)
        token = next((command["token"] for command in commands["continuationCommand"]
                      if "token" in command), None)
        body = {"continuation": token} if token else None


def search_result(video_id, renderer):
    """Fiche d'une vidéo trouvée par la recherche."""
    byline = renderer.get("longBylineText") or renderer.get("ownerText") or {}
    # Vidéo commune à plusieurs chaînes : le lien mène à la première.
    channel_id, address = find_channel(byline) or ("", "")
    live = any(badge.get("metadataBadgeRenderer", {}).get("style") == "BADGE_STYLE_TYPE_LIVE_NOW"
               for badge in renderer.get("badges", []))
    views = text_of(renderer.get("shortViewCountText") or renderer.get("viewCountText"))
    return {
        "id": video_id,
        "thumbnail": THUMBNAIL_URL.format(video_id),
        "title": clean(text_of(renderer.get("title"))),
        "duration": "EN DIRECT" if live else text_of(renderer.get("lengthText")),
        "live": live,
        # Espaces insécables : si la ligne est trop longue, elle se coupe au « · ».
        "meta": " · ".join(part.replace(" ", "\u00a0") for part in
                           (views, text_of(renderer.get("publishedTimeText"))) if part),
        "channel": clean(text_of(byline)),
        "channel_id": channel_id,
        # Ce qu'affichera le champ de recherche : le @nom de la chaîne, à défaut son ID.
        "channel_query": urllib.parse.unquote(address[1:]) if address.startswith("/@") else channel_id,
    }


def collect(node, found):
    """Parcourt une réponse JSON de YouTube et range dans found[clé] chaque objet
    rangé sous l'une des clés de found."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in found:
                found[key].append(value)
            else:
                collect(value, found)
    elif isinstance(node, list):
        for value in node:
            collect(value, found)


def find_channel(node):
    """Premier lien vers une chaîne dans une partie de la réponse : (ID, adresse) ou None."""
    endpoints = {"browseEndpoint": []}
    collect(node, endpoints)
    for endpoint in endpoints["browseEndpoint"]:
        if endpoint.get("browseId", "").startswith("UC"):
            return endpoint["browseId"], endpoint.get("canonicalBaseUrl", "")
    return None


def text_of(field):
    """Texte d'un champ de l'API YouTube : {"simpleText": …} ou {"runs": [{"text": …}, …]}."""
    if not field:
        return ""
    return field.get("simpleText") or "".join(run.get("text", "") for run in field.get("runs", []))


def clean(text):
    return UNSUPPORTED_CHARS.sub("", text).strip()


def shorten(text, limit=DESCRIPTION_LENGTH):
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def time_ago(published):
    """datetime → « il y a 3 jours »."""
    seconds = (datetime.now(timezone.utc) - published).total_seconds()
    for unit, size in TIME_UNITS:
        count = int(seconds // size)
        if count >= 1:
            plural = "s" if count > 1 and not unit.endswith("s") else ""
            return f"il y a {count} {unit}{plural}"
    return "à l'instant"


def ago_short(timestamp):
    """Horodatage → « il y a 15 min », « il y a 2 h », « il y a 3 j »…"""
    seconds = time.time() - timestamp
    for unit, size in SHORT_UNITS:
        count = int(seconds // size)
        if count >= 1:
            return f"il y a {count}\u00a0{unit}" + ("s" if unit == "an" and count > 1 else "")
    return "à l'instant"


def views_fr(count):
    """232545 → « 232 545 vues »."""
    return f"{count:,}".replace(",", "\u00a0") + (" vue" if count < 2 else " vues")


def clock(seconds):
    """3723 → « 1:02:03 » ; 83 → « 1:23 »."""
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def count_fr(count):
    """5 → « 5 vidéos »."""
    return f"{count} vidéo" + ("s" if count > 1 else "")


def download_status(entry):
    """État d'un téléchargement, en clair : « Téléchargement… 45 % », « Vidéo · 145 Mo »…"""
    kind = DOWNLOAD_KINDS[entry["kind"]]
    if entry["status"] == "attente":
        return f"{kind} · en attente"
    if entry["status"] == "cours":
        step = "Conversion en MP3" if entry.get("step") == "conversion" else "Téléchargement"
        return f"{step} {entry['progress']}\u00a0%"
    if entry["status"] == "echec":
        return f"Échec, touchez pour réessayer\u00a0: {entry['error']}"
    return f"{kind} · {size_fr(entry['size'])}"


class VideoCard(ButtonBehavior, BoxLayout):
    """Vignette d'une vidéo de la chaîne affichée : un appui la lit, puis les vidéos
    suivantes de la chaîne à la suite."""

    video = DictProperty()
    # Copies des champs de video affichés : Kivy ne suit pas les video.get(…) écrits en KV.
    thumbnail = StringProperty()
    title = StringProperty()
    meta = StringProperty()
    description = StringProperty()
    progress = NumericProperty(0)  # part déjà vue (barre rouge sous la miniature)

    def on_release(self):
        App.get_running_app().play_channel(self.video["id"])


class VideoRow(ButtonBehavior, BoxLayout):
    """Une vidéo dans une liste (résultats, historique, téléchargements) : un appui ouvre
    sa page de lecture, un appui sur le nom de sa chaîne affiche celle-ci."""

    video = DictProperty()
    # Champs affichés (voir _row) : Kivy ne suit pas les video.get(…) écrits en KV.
    thumbnail = StringProperty()
    title = StringProperty()
    duration = StringProperty()
    live = BooleanProperty(False)
    channel = StringProperty()
    channel_id = StringProperty()
    meta = StringProperty()
    progress = NumericProperty(0)  # part déjà vue (barre rouge sous la miniature)
    mode = StringProperty()  # liste d'origine : "recherche", "historique" ou "telechargement"
    trailing = StringProperty()  # icône du bouton à droite de la ligne ("" : aucun)

    def on_release(self):
        App.get_running_app().row_pressed(self)

    def open_channel(self):
        App.get_running_app().open_channel_of(self.video, self.mode)

    def trailing_pressed(self):
        App.get_running_app().row_action(self)


class ResultList(RecycleView):
    """Liste de vidéos : seules les lignes visibles à l'écran existent, même pour 100 vidéos.

    Kivy repère la position de la liste en proportion de sa longueur : sans correction, elle
    sauterait à chaque arrivée de nouveaux résultats en bas. On garde donc la distance au haut.
    """

    offset = None  # distance entre le haut de la liste et le haut de l'écran, en pixels

    def save_viewport(self):
        if self.offset is None:
            self.offset = (1 - self.scroll_y) * max(0, self.layout_manager.height - self.height)

    def restore_viewport(self):
        offset, self.offset = self.offset, None
        scrollable = self.layout_manager.height - self.height
        if offset is not None and scrollable > 0:
            self.scroll_y = min(1, max(0, 1 - offset / scrollable))
            # Recale aussi le défilement en cours (doigt posé ou élan d'un lancer), sinon
            # il repartirait de l'ancienne position.
            self._update_effect_y_bounds()


class PlaylistRow(ButtonBehavior, BoxLayout):
    """Une playlist de l'onglet Playlists : un appui l'ouvre."""

    playlist_id = StringProperty()
    name = StringProperty()
    info = StringProperty()

    def on_release(self):
        App.get_running_app().open_playlist(self.playlist_id)


class PlaylistToggle(ButtonBehavior, BoxLayout):
    """Case d'une playlist sous la vidéo en cours : un appui l'y ajoute ou l'en retire."""

    playlist_id = StringProperty()
    name = StringProperty()
    checked = BooleanProperty(False)

    def on_release(self):
        App.get_running_app().toggle_watch_playlist(self.playlist_id)


class PlaylistSection(BoxLayout):
    """Sous la vidéo en cours : nouvelle playlist, et une case par playlist (PlaylistToggle)."""


class QueueRow(ButtonBehavior, BoxLayout):
    """Une piste de l'écran d'écoute : un appui la lance."""

    index = NumericProperty(0)
    title = StringProperty()
    channel = StringProperty()
    current = BooleanProperty(False)  # piste en cours
    online = BooleanProperty(False)  # pas de MP3 : son lu en ligne

    def on_release(self):
        App.get_running_app().audio.jump(self.index)


class SeekBar(Widget):
    """Barre de l'écran d'écoute : un appui, ou un glissement du doigt, déplace la lecture.

    value : part de la piste déjà écoutée, de 0 à 1.
    """

    value = NumericProperty(0)
    dragging = BooleanProperty(False)  # doigt posé : les relevés du lecteur ne la bougent plus

    def on_touch_down(self, touch):
        if self.disabled or not self.collide_point(*touch.pos):
            return super().on_touch_down(touch)
        touch.grab(self)
        self.dragging = True
        self._follow(touch)
        return True

    def on_touch_move(self, touch):
        if touch.grab_current is not self:
            return super().on_touch_move(touch)
        self._follow(touch)
        return True

    def on_touch_up(self, touch):
        if touch.grab_current is not self:
            return super().on_touch_up(touch)
        touch.ungrab(self)
        self._follow(touch)
        self.dragging = False
        App.get_running_app().audio_seek(self.value)
        return True

    def _follow(self, touch):
        self.value = min(1, max(0, (touch.x - self.x) / self.width)) if self.width else 0


class ChannelHeader(BoxLayout):
    """Nom de la chaîne affichée, avec le bouton pour s'y abonner (« Mes chaînes »)."""

    text = StringProperty()


class ChannelRow(ButtonBehavior, BoxLayout):
    """Une chaîne de l'onglet « Mes chaînes » : un appui affiche ses vidéos."""

    channel_id = StringProperty()
    name = StringProperty()
    query = StringProperty()

    def on_release(self):
        App.get_running_app().open_channel(self.query, self.channel_id)


class ConfirmDialog(ModalView):
    """Demande de confirmation avant une action qu'on ne peut pas annuler (ou simple message,
    sans bouton « Annuler », si cancel est vide)."""

    message = StringProperty()
    action = StringProperty()
    cancel = StringProperty("Annuler")

    def __init__(self, on_confirm, **kwargs):
        super().__init__(**kwargs)
        self.on_confirm = on_confirm

    def confirm(self):
        self.dismiss()
        self.on_confirm()


class AskeApp(App):
    version = __version__
    on_android = platform == "android"
    status = StringProperty()
    tab = StringProperty("videos")
    favorite_count = NumericProperty(0)
    # La chaîne affichée dans l'onglet Vidéos fait-elle partie de « Mes chaînes » ?
    is_favorite = BooleanProperty(False)
    # Page de lecture : la vidéo ouverte ({} : aucune), et ce qui s'affiche sous elle.
    watching = DictProperty()
    watch_title = StringProperty()
    watch_thumbnail = StringProperty()
    watch_channel = StringProperty()
    watch_channel_id = StringProperty()
    watch_description = StringProperty()
    watch_meta = StringProperty()
    watch_subscribed = BooleanProperty(False)
    watch_download = StringProperty()  # état de son téléchargement ("" : pas téléchargée)
    watch_download_icon = StringProperty()
    can_download = BooleanProperty(False)  # ni téléchargée, ni en direct
    watch_can_listen = BooleanProperty(False)  # pas en direct : bouton « Écouter »
    # Barre « Reprendre » : dernière vidéo commencée ("" : barre cachée).
    resume_title = StringProperty()
    resume_meta = StringProperty()
    history_count = NumericProperty(0)
    downloads_summary = StringProperty()
    # Playlist en cours de lecture (« Playlist « X » · 2 sur 5 ») et vidéo suivante.
    watch_queue = StringProperty()
    watch_has_next = BooleanProperty(False)
    watch_playlists_open = BooleanProperty(False)  # section « Enregistrer dans une playlist »
    naming_playlist = BooleanProperty(False)  # champ du nom d'une nouvelle playlist, en haut
    # Onglet Playlists : nombre de playlists, et playlist ouverte (nombre de vidéos, de MP3
    # prêts, et de vidéos encore à télécharger en MP3).
    playlist_count = NumericProperty(0)
    playlist_name = StringProperty()
    playlist_info = StringProperty()
    playlist_size = NumericProperty(0)
    playlist_listenable = NumericProperty(0)  # vidéos à écouter (tout sauf les directs)
    playlist_missing = NumericProperty(0)
    # Paramètres : enchaîner les vidéos d'une playlist ; MP3 à 192 kbit/s au lieu de 128.
    autoplay = BooleanProperty(True)
    mp3_high_quality = BooleanProperty(False)
    # Écoute des MP3 : mini-lecteur en bas de l'écran, et écran d'écoute.
    audio_active = BooleanProperty(False)
    audio_playing = BooleanProperty(False)
    audio_title = StringProperty()
    audio_channel = StringProperty()
    audio_thumbnail = StringProperty()
    audio_label = StringProperty()  # « Playlist « X » · 2 sur 5 »
    audio_elapsed = StringProperty("0:00")
    audio_total = StringProperty()
    audio_progress = NumericProperty(0)  # part de la piste déjà écoutée
    audio_has_next = BooleanProperty(False)
    audio_loading = BooleanProperty(False)  # piste en ligne pas encore arrivée

    def build(self):
        Window.clearcolor = get_color_from_hex("#0f0f0f")
        # Miniatures vides pendant le chargement ou en cas d'échec,
        # au lieu des images par défaut de Kivy.
        blank = Texture.create(size=(1, 1))
        blank.blit_buffer(bytes(4), colorfmt="rgba", bufferfmt="ubyte")
        Loader.loading_image = Loader.error_image = CoreImage(blank)
        Loader.num_workers = 4

        self.store = JsonStore(os.path.join(self.user_data_dir, "reglages.json"))
        self.history = History(os.path.join(self.user_data_dir, "historique.json"))
        self.downloads = DownloadManager(
            os.path.join(self.user_data_dir, "telechargements"),
            os.path.join(self.user_data_dir, "telechargements.json"),
            on_change=Clock.create_trigger(self.refresh_downloads), fetch=http_get)
        self.playlists = Playlists(os.path.join(self.user_data_dir, "playlists.json"))
        self.open_playlist_id = None
        self.autoplay = self.store.get("lecture")["auto"] if self.store.exists("lecture") else True
        self.mp3_high_quality = self.store.exists("mp3") and self.store.get("mp3")["haute_qualite"]
        self.downloads.mp3_bitrate = 192 if self.mp3_high_quality else 128
        # Écoute des MP3 : dernier relevé du lecteur (voir _audio_state), piste en cours, et
        # liste dont les pistes sont affichées dans l'écran d'écoute.
        self.audio = AudioPlayer(self._audio_state, self._find_stream)
        self.cache_folder = cache_folder()  # pochettes des pistes en ligne
        self.audio_state = None
        self.audio_video = {}
        self.audio_rows_queue = None
        self._audio_changed = Clock.create_trigger(self._show_audio_state)
        # Chaîne ou recherche attendue à l'écran : les réponses arrivées trop tard sont ignorées.
        self.pending = None
        # Numéro de la recherche en cours : ses pages suivantes s'ajoutent à la liste.
        self.search_number = 0
        # Ce que « retour » doit rouvrir (résultats d'une recherche, page de lecture…),
        # empilé à chaque fois qu'on ouvre une chaîne depuis une liste ou une vidéo.
        self.back_stack = []
        self.previous_tab = "videos"
        # ID des chaînes déjà trouvées, pour ne pas retélécharger leur page (lourde).
        self.channel_ids = {}
        # Chaîne affichée, et « Mes chaînes » : fiches {"id", "name", "query"}.
        self.current = None
        self.channel_videos = []  # vidéos de la chaîne affichée, pour les lire à la suite
        self.favorites = self.store.get("favoris")["chaines"] if self.store.exists("favoris") else []
        self.resume_video = None
        self.playing_id = None
        # Vidéos lues à la suite (playlist ou chaîne) : {"label", "videos", "index"}, ou None.
        self.queue = None
        # Lecteur ouvert ? Le verrou évite que le fil d'Android, en enchaînant les vidéos
        # d'une playlist, ne rouvre le lecteur au moment où on le referme.
        self.watch_lock = threading.Lock()
        self.watch_open = False
        # Hauteur de la vidéo en portrait (16:9), et téléphone tourné en paysage ?
        self.player_height = int(Window.width * 9 / 16)
        self.landscape = Window.width > Window.height
        if platform == "android":
            self.player = AndroidPlayer(on_back=self._back_from_player, on_position=self._position)
        else:
            self.player = None
        Window.bind(on_keyboard=self.on_keyboard, size=self._window_resized)
        # Le clavier d'Android ne doit pas cacher le champ où l'on tape : l'écran remonte.
        Window.softinput_mode = "below_target"
        # L'interface est décrite dans aske.kv, chargé automatiquement par Kivy.

    # --- Navigation -------------------------------------------------------------------------

    def on_keyboard(self, window, key, *args):
        # « Retour » d'Android (touche Échap pour Kivy).
        return self.go_back() if key == 27 else False

    def go_back(self):
        """Ferme la page de lecture ou d'écoute, puis les Paramètres, revient à l'onglet Vidéos,
        puis à ce qu'on a quitté en ouvrant une chaîne ; renvoie False quand il n'y a plus rien
        à fermer (Android quitte alors l'application)."""
        if self.naming_playlist:
            self.naming_playlist = False
        elif self.root.current == "ecoute":
            self.close_listening()
        elif self.watching:
            self.close_watch()
        elif self.tab == "parametres":
            self.tab = self.previous_tab
        elif self.tab == "playlists" and self.open_playlist_id:
            self.close_playlist()
        elif self.tab != "videos":
            self.tab = "videos"
        elif self.back_stack:
            self.back_stack.pop()()
        else:
            return False
        return True

    @mainthread
    def _back_from_player(self):
        self.go_back()

    def on_tab(self, app, tab):
        self.root.ids.screens.current = tab

    def open_settings(self):
        if self.tab != "parametres":
            self.previous_tab = self.tab
            self.tab = "parametres"

    # --- Mes chaînes ------------------------------------------------------------------------

    def toggle_favorite(self, channel=None):
        """Abonne à la chaîne (la chaîne affichée par défaut), ou désabonne si elle y est déjà."""
        channel = channel or self.current
        if not channel:
            return
        if any(fav["id"] == channel["id"] for fav in self.favorites):
            self.remove_favorite(channel["id"])
        else:
            self.favorites.append(dict(channel))
            self.save_favorites()

    def remove_favorite(self, channel_id):
        self.favorites = [fav for fav in self.favorites if fav["id"] != channel_id]
        self.save_favorites()

    def save_favorites(self):
        self.favorites.sort(key=lambda fav: fav["name"].casefold())
        self.store.put("favoris", chaines=self.favorites)
        self.refresh_favorites()

    def refresh_favorites(self):
        box = self.root.ids.favorites
        box.clear_widgets()
        for fav in self.favorites:
            box.add_widget(ChannelRow(channel_id=fav["id"], name=fav["name"], query=fav["query"]))
        self.favorite_count = len(self.favorites)
        ids = {fav["id"] for fav in self.favorites}
        self.is_favorite = bool(self.current) and self.current["id"] in ids
        self.watch_subscribed = self.watching.get("channel_id") in ids

    def open_channel(self, query, channel_id):
        """Chaîne choisie dans « Mes chaînes » : « retour » ramènera à cet onglet."""
        self.back_stack.append(partial(setattr, self, "tab", "chaines"))
        self._show_channel(query, channel_id)

    def open_channel_of(self, video, mode):
        """Chaîne d'une vidéo d'une liste : « retour » ramènera à cette liste."""
        if not video.get("channel_id"):
            return
        if mode == "recherche":
            self.back_stack.append(partial(self._back_to_search, self.pending))
        else:
            self.back_stack.append(partial(setattr, self, "tab", self.tab))
        self._show_channel(video["channel_query"], video["channel_id"])

    def _show_channel(self, query, channel_id):
        self.channel_ids[query] = channel_id
        self.root.ids.query.text = query
        self.tab = "videos"
        self._load_channel(query)

    # --- Page de lecture --------------------------------------------------------------------

    def watch(self, video, queue=None):
        """Ouvre la page de lecture d'une vidéo, reprise là où elle s'était arrêtée (la version
        téléchargée en priorité). queue : vidéos à lire à la suite (playlist ou chaîne)."""
        video = dict(video)
        if self.audio_active:
            self.audio.pause()  # un seul son à la fois
        with self.watch_lock:
            self.queue = queue
            self.watch_open = True
            start, local = self._play(video)
        self._show_watch(video, start, local)

    def _play(self, video):
        """Lance la lecture ; appelée par Kivy, ou par le fil d'Android pour enchaîner une playlist."""
        start = self.history.start(video)
        local = self.downloads.local_video(video["id"])
        self.playing_id = video["id"]
        url = WATCH_URL.format(video["id"]) + (f"&t={start}" if start else "")
        if not self.player:
            webbrowser.open("file://" + local if local else url)
        elif local:
            html = LOCAL_PLAYER.format(media=os.path.basename(local), start=start,
                                       poster=os.path.basename(self.downloads.thumbnail(video["id"])))
            self.player.open(video["title"], self.player_height, self.landscape,
                             html=html, base_url="file://" + self.downloads.folder + "/")
        else:
            self.player.open(video["title"], self.player_height, self.landscape, url=url)
        return start, local

    def _show_watch(self, video, start, local):
        """Page de lecture : titre, chaîne, téléchargement et playlist de la vidéo."""
        self.watching = video
        self.watch_title = video["title"]
        self.watch_thumbnail = video["thumbnail"]
        self.watch_channel = video.get("channel", "")
        self.watch_channel_id = video.get("channel_id", "")
        self.watch_description = video.get("description", "")
        self.watch_meta = " · ".join(filter(None, [
            video.get("meta", ""),
            f"reprise à {clock(start)}" if start else "",
            "lecture hors ligne" if local else "",
        ]))
        queue = self.queue
        if queue:
            self.watch_queue = f"{queue['label']} · {queue['index'] + 1} sur {len(queue['videos'])}"
            self.watch_has_next = queue["index"] + 1 < len(queue["videos"])
        else:
            self.watch_queue = ""
            self.watch_has_next = False
        self.watch_playlists_open = False
        self.naming_playlist = False
        self.root.ids.playlist_section.clear_widgets()
        self.refresh_favorites()
        self._refresh_watch_download()
        self.root.ids.watch_scroll.scroll_y = 1
        self.root.current = "lecture"

    @mainthread
    def _show_advanced(self, video, start, local):
        # Vidéo suivante lancée par le fil d'Android : la page suit (dès que Kivy tourne).
        if self.watch_open and self.playing_id == video["id"]:
            self._show_watch(video, start, local)

    def close_watch(self):
        with self.watch_lock:
            self.watch_open = False
            self.queue = None
            self.playing_id = None
            if self.player:
                self.player.close()
        self.watching = {}
        self.history.save()
        self.root.current = "main"
        self.refresh_history()
        self._refresh_progress()

    def _position(self, position, duration, ended, where):
        """Relevé du lecteur toutes les 2 s (depuis le fil d'Android) : position atteinte, et
        vidéo suivante de la playlist quand celle-ci est finie."""
        video_id = self.playing_id
        if not video_id or video_id not in where:
            return  # page de la vidéo précédente, pas encore remplacée
        self.history.update(video_id, position, duration)
        if ended and self.autoplay:
            self._advance(video_id)

    def _advance(self, video_id):
        with self.watch_lock:
            queue = self.queue
            if (not self.watch_open or not queue or self.playing_id != video_id
                    or queue["index"] + 1 >= len(queue["videos"])):
                return
            queue["index"] += 1
            video = queue["videos"][queue["index"]]
            start, local = self._play(video)
        self._show_advanced(video, start, local)

    def next_video(self):
        """Bouton « Suivante » de la page de lecture (playlist ou chaîne)."""
        queue = self.queue
        if queue and queue["index"] + 1 < len(queue["videos"]):
            queue = dict(queue, index=queue["index"] + 1)
            self.watch(queue["videos"][queue["index"]], queue)

    def _window_resized(self, window, size):
        self.landscape = size[0] > size[1]
        if not self.landscape:
            self.player_height = int(size[0] * 9 / 16)
        # Téléphone tourné pendant la lecture : plein écran en paysage.
        if self.watching and self.player:
            self.player.set_fullscreen(self.landscape)

    def toggle_watch_subscription(self):
        video = self.watching
        if video.get("channel_id"):
            self.toggle_favorite({"id": video["channel_id"], "name": video["channel"],
                                  "query": video["channel_query"]})

    def open_watch_channel(self):
        """Chaîne de la vidéo en cours : « retour » rouvrira la vidéo, là où elle en était, par-dessus
        l'écran d'où on l'avait ouverte."""
        video = dict(self.watching)
        if not video.get("channel_id"):
            return
        queue = dict(self.queue) if self.queue else None
        view = self._current_view()
        self.close_watch()
        self.back_stack.append(partial(self._reopen, video, queue, view))
        self._show_channel(video["channel_query"], video["channel_id"])

    def _current_view(self):
        """Ce qu'affiche l'écran principal : (onglet, requête, ID de la chaîne affichée)."""
        if self.tab != "videos":
            return self.tab, None, None
        if self.root.ids.views.current == "chaine" and self.current:
            return "videos", self.current["query"], self.current["id"]
        return "videos", self.pending, None

    def _reopen(self, video, queue, view):
        tab, query, channel_id = view
        self.tab = tab
        if channel_id:
            self._show_channel(query, channel_id)
        elif tab == "videos" and query:
            self._back_to_search(query)
        self.watch(video, queue)

    def download(self, kind):
        self._ask_permissions(kind)
        self.downloads.add(dict(self.watching), kind)

    def _ask_permissions(self, kind):
        """Autorisations demandées au premier téléchargement (la question n'est posée qu'une fois)."""
        if platform != "android":
            return
        from android.permissions import request_permissions
        # Pour suivre l'avancement dans la notification (demandée depuis Android 13).
        permissions = ["android.permission.POST_NOTIFICATIONS"]
        if kind == "mp3":
            # Pour ranger le MP3 dans le dossier Musique (demandée jusqu'à Android 9).
            permissions.append("android.permission.WRITE_EXTERNAL_STORAGE")
        request_permissions(permissions)

    def _refresh_watch_download(self):
        entry = self.downloads.get(self.watching["id"]) if self.watching else None
        self.watch_download = download_status(entry) if entry else ""
        status = entry["status"] if entry else ""
        self.watch_download_icon = ICONS[{"fini": "downloaded", "echec": "error"}.get(status, "download")]
        self.can_download = bool(self.watching) and not entry and not self.watching.get("live")
        self.watch_can_listen = bool(self.watching) and not self.watching.get("live")

    def listen_watching(self):
        """« Écouter », sous la vidéo : elle se ferme, et l'écoute en arrière-plan s'ouvre (son
        MP3 s'il est sur le téléphone, sinon son son en ligne)."""
        video = dict(self.watching)
        self.close_watch()
        if self.listen([video]):
            self.open_listening()

    def _has_mp3(self, video_id):
        entry = self.downloads.get(video_id)
        return bool(entry) and entry["status"] == "fini" and entry["kind"] in AUDIO_KINDS

    def _mp3_pending(self, video_id):
        entry = self.downloads.get(video_id)
        return bool(entry) and entry["status"] in ("attente", "cours") and entry["kind"] in AUDIO_KINDS

    def _can_download(self, video):
        return not self.downloads.get(video["id"]) and not video.get("live")

    # --- Playlists --------------------------------------------------------------------------

    def refresh_playlists(self):
        playlists = self.playlists.all()
        self.playlist_count = len(playlists)
        box = self.root.ids.playlist_rows
        box.clear_widgets()
        for playlist in playlists:
            box.add_widget(PlaylistRow(playlist_id=playlist["id"], name=playlist["name"],
                                       info=count_fr(len(playlist["videos"]))))
        if self.open_playlist_id:
            self._show_playlist_content()
        self._refresh_watch_playlists()

    def _playlist_page(self):
        """Page affichée (lecture d'une vidéo, ou écoute) : sa vidéo, l'endroit où s'affichent ses
        playlists, le champ du nom d'une nouvelle playlist, et ce qui défile."""
        ids = self.root.ids
        if self.root.current == "ecoute":
            return self.audio_video, ids.audio_playlist_section, ids.audio_playlist_name, ids.audio_scroll
        return self.watching, ids.playlist_section, ids.playlist_name, ids.watch_scroll

    def create_playlist(self, field, add_watching=False):
        """Crée une playlist au nom tapé dans field ; add_watching : y met la vidéo (ou la piste)
        en cours."""
        name = field.text.strip()
        if not name:
            return
        field.text = ""
        field.focus = False
        playlist = self.playlists.create(name)
        video = self._playlist_page()[0]
        if add_watching and video:
            self.playlists.toggle(playlist["id"], dict(video))
        self.naming_playlist = False
        self.refresh_playlists()

    def start_naming(self):
        """« + Nouvelle playlist » sous la vidéo : le champ du nom s'ouvre en haut de la page,
        juste sous la vidéo, pour rester visible au-dessus du clavier d'Android."""
        self.naming_playlist = True
        field, scroll = self._playlist_page()[2:]
        scroll.scroll_y = 1
        Clock.schedule_once(lambda dt: setattr(field, "focus", True), 0.1)

    def confirm_delete_playlist(self, playlist_id):
        name = self.playlists.get(playlist_id)["name"]
        ConfirmDialog(partial(self._delete_playlist, playlist_id), action="Supprimer",
                      message=f"Supprimer la playlist «\u00a0{name}\u00a0»\u00a0?").open()

    def _delete_playlist(self, playlist_id):
        self.playlists.delete(playlist_id)
        if self.open_playlist_id == playlist_id:
            self.close_playlist()
        self.refresh_playlists()

    def open_playlist(self, playlist_id):
        self.open_playlist_id = playlist_id
        self._show_playlist_content()
        self.root.ids.playlist_list.scroll_y = 1
        self.root.ids.playlist_views.current = "contenu"

    def close_playlist(self):
        self.open_playlist_id = None
        self.root.ids.playlist_views.current = "listes"

    def _show_playlist_content(self):
        playlist = self.playlists.get(self.open_playlist_id)
        if playlist is None:
            self.close_playlist()
            return
        videos = playlist["videos"]
        self.playlist_name = playlist["name"]
        self.playlist_size = len(videos)
        self.playlist_listenable = sum(not video.get("live") for video in videos)
        self.playlist_missing = sum(self._can_download(video) for video in videos)
        mp3 = sum(self._has_mp3(video["id"]) for video in videos)
        pending = sum(self._mp3_pending(video["id"]) for video in videos)
        self.playlist_info = " · ".join(filter(None, [
            count_fr(len(videos)), f"{mp3} MP3" if mp3 else "",
            f"{pending} en cours" if pending else ""]))
        self.root.ids.playlist_list.data = [self._row(video, "playlist") for video in videos]

    def play_playlist(self, playlist_id=None, index=0):
        """Lit la playlist (ouverte par défaut) à partir de sa vidéo n° index."""
        playlist = self.playlists.get(playlist_id or self.open_playlist_id)
        if playlist and playlist["videos"]:
            queue = {"label": f"Playlist «\u00a0{playlist['name']}\u00a0»",
                     "videos": list(playlist["videos"]), "index": index}
            self.watch(queue["videos"][index], queue)

    def listen_playlist(self):
        """« Écouter » : la playlist ouverte, à la suite, en arrière-plan (les MP3 quand ils sont
        sur le téléphone, le son en ligne sinon)."""
        playlist = self.playlists.get(self.open_playlist_id)
        if playlist and self.listen(playlist["videos"], 0, f"Playlist «\u00a0{playlist['name']}\u00a0»"):
            self.open_listening()

    def confirm_download_playlist(self):
        """« MP3 » : télécharge en MP3 les vidéos de la playlist ouverte qui ne le sont pas."""
        playlist = self.playlists.get(self.open_playlist_id)
        videos = [video for video in playlist["videos"] if self._can_download(video)] if playlist else []
        if not videos:
            return
        which = ("la vidéo de la playlist qui ne l'est pas" if len(videos) == 1
                 else f"les {len(videos)} vidéos de la playlist qui ne le sont pas")
        ConfirmDialog(partial(self._download_mp3, videos), action="Télécharger",
                      message=f"Télécharger en MP3 {which} encore\u00a0?\n"
                              "Les MP3 iront dans le dossier Musique/Aske du téléphone.").open()

    def _download_mp3(self, videos):
        self._ask_permissions("mp3")
        for video in videos:
            if self._can_download(video):
                self.downloads.add(dict(video), "mp3")

    def play_channel(self, video_id=None):
        """Lit les vidéos de la chaîne affichée à la suite, depuis video_id (sinon la plus récente)."""
        videos = self.channel_videos
        if videos:
            index = next((index for index, video in enumerate(videos) if video["id"] == video_id), 0)
            queue = {"label": f"Chaîne «\u00a0{self.current['name']}\u00a0»",
                     "videos": list(videos), "index": index}
            self.watch(videos[index], queue)

    def toggle_playlists_section(self):
        """Ouvre ou ferme, sous la vidéo (ou la piste) en cours, les playlists où l'enregistrer."""
        holder = self._playlist_page()[1]
        holder.clear_widgets()
        self.watch_playlists_open = not self.watch_playlists_open
        if self.watch_playlists_open:
            holder.add_widget(PlaylistSection())
            self._refresh_watch_playlists()

    def toggle_watch_playlist(self, playlist_id):
        self.playlists.toggle(playlist_id, dict(self._playlist_page()[0]))
        self.refresh_playlists()

    def _refresh_watch_playlists(self):
        video, holder = self._playlist_page()[:2]
        sections = holder.children
        if not sections or not video:
            return
        box = sections[0].ids.toggles
        box.clear_widgets()
        inside = self.playlists.containing(video["id"])
        for playlist in self.playlists.all():
            box.add_widget(PlaylistToggle(playlist_id=playlist["id"], name=playlist["name"],
                                          checked=playlist["id"] in inside))

    def toggle_autoplay(self):
        self.autoplay = not self.autoplay
        self.store.put("lecture", auto=self.autoplay)

    def toggle_mp3_quality(self):
        self.mp3_high_quality = not self.mp3_high_quality
        self.downloads.mp3_bitrate = 192 if self.mp3_high_quality else 128
        self.store.put("mp3", haute_qualite=self.mp3_high_quality)

    # --- Écoute en arrière-plan ---------------------------------------------------------------

    def listen(self, videos, index=0, label=""):
        """Écoute à la suite, en arrière-plan, les vidéos de videos à partir de videos[index] :
        leur MP3 s'il est sur le téléphone, sinon leur son en ligne. Les directs sont passés.
        Renvoie False s'il n'y a rien à écouter."""
        kept, tracks, first = [], [], None
        for number, video in enumerate(videos):
            if video.get("live"):
                continue
            address = self.downloads.local_audio(video["id"]) or ""
            if first is None and number >= index:
                first = len(kept)
            thumbnail = self.downloads.thumbnail(video["id"])
            kept.append(dict(video))
            tracks.append({"uri": address, "online": not address, "video": kept[-1],
                           "title": video["title"], "artist": video.get("channel", ""),
                           "artwork": thumbnail if os.path.exists(thumbnail) else "",
                           "start": self.history.resume_at(video)})
        if not kept:
            ConfirmDialog(lambda: None, action="OK", cancel="",
                          message="Rien à écouter\u00a0: un direct ne s'écoute pas en arrière-plan.").open()
            return False
        first = first or 0
        if self.watching:
            self.close_watch()
        # « seen » : dernière piste inscrite dans l'historique (voir _audio_state).
        queue = {"label": label, "videos": kept, "online": [track["online"] for track in tracks],
                 "seen": None}
        # Affichage immédiat, sans attendre le premier relevé du lecteur (qui le remplacera :
        # d'où cet ordre).
        self.audio_state = {"queue": queue, "index": first, "position": tracks[first]["start"],
                            "duration": 0, "playing": True, "loading": tracks[first]["online"]}
        self._show_audio_state()
        self.audio.play(queue, tracks, first)
        return True

    def _find_stream(self, track):
        """Adresse du son en ligne d'une piste, ses en-têtes HTTP (en JSON), et sa pochette, gardée
        dans le dossier jetable d'Aske. Appelée par le lecteur, dans un autre fil."""
        video = track["video"]
        uri, headers = stream_url(video["id"])
        artwork = os.path.join(self.cache_folder, video["id"] + ".jpg")
        if not os.path.exists(artwork):
            try:
                data = http_get(video["thumbnail"])
                with open(artwork + ".tmp", "wb") as file:
                    file.write(data)
                os.replace(artwork + ".tmp", artwork)
            except (YouTubeError, urllib.error.HTTPError, OSError):
                artwork = ""  # pas de pochette, mais le son est là
        return uri, json.dumps(headers), artwork

    def _audio_state(self, state):
        """Relevé du lecteur audio, toutes les secondes, même Aske en arrière-plan (depuis le fil
        d'Android) : retient où l'on en est dans la piste, puis prévient l'affichage."""
        if state:
            queue, index = state["queue"], state["index"]
            if 0 <= index < len(queue["videos"]):
                video = queue["videos"][index]
                if queue["seen"] != index:  # nouvelle piste : en tête de l'historique
                    queue["seen"] = index
                    self.history.start(video)
                self.history.update(video["id"], state["position"], state["duration"])
        else:
            self.history.save()
        self.audio_state = state
        self._audio_changed()

    def _show_audio_state(self, *args):
        """Mini-lecteur et écran d'écoute, d'après le dernier relevé du lecteur."""
        state = self.audio_state
        if not state:
            if self.audio_active:  # fin de la liste, ou « Arrêter »
                self.audio_active = False
                self.audio_playing = False
                self.audio_video = {}
                if self.root.current == "ecoute":
                    self.close_listening()
                self.refresh_history()
                self._refresh_progress()
            return
        queue, index = state["queue"], state["index"]
        if not 0 <= index < len(queue["videos"]):
            return
        video = queue["videos"][index]
        self.audio_active = True
        self.audio_playing = state["playing"]
        self.audio_loading = state["loading"]
        if video["id"] != self.audio_video.get("id") or queue is not self.audio_rows_queue:
            self.audio_video = video
            self.audio_title = video["title"]
            self.audio_channel = video.get("channel", "")
            thumbnail = self.downloads.thumbnail(video["id"])
            self.audio_thumbnail = thumbnail if os.path.exists(thumbnail) else video["thumbnail"]
            count = len(queue["videos"])
            self.audio_label = " · ".join(filter(None, [queue["label"],
                                                        f"{index + 1} sur {count}" if count > 1 else ""]))
            self.audio_has_next = index + 1 < count
            self._show_queue(queue, index)
            self._refresh_watch_playlists()  # cases de la section Playlist, pour la nouvelle piste
            self.refresh_history()
        duration = state["duration"]
        self.audio_elapsed = clock(state["position"])
        self.audio_total = clock(duration) if duration else ""
        if not self.root.ids.seek.dragging:
            self.audio_progress = min(1, state["position"] / duration) if duration else 0

    def _show_queue(self, queue, index):
        """Pistes de l'écran d'écoute ; celle en cours est en rouge."""
        box = self.root.ids.audio_queue
        if queue is not self.audio_rows_queue:
            self.audio_rows_queue = queue
            box.clear_widgets()
            for number, video in enumerate(queue["videos"]):
                box.add_widget(QueueRow(index=number, title=video["title"],
                                        channel=video.get("channel", ""),
                                        online=queue["online"][number]))
        for row in box.children:
            row.current = row.index == index

    def audio_seek(self, fraction):
        """Barre de l'écran d'écoute lâchée à fraction (de 0 à 1) de la piste."""
        state = self.audio_state
        if state and state["duration"]:
            self.audio_progress = fraction
            self.audio.seek(fraction * state["duration"])

    def open_listening(self):
        """Écran d'écoute : pochette, commandes, et pistes de la liste."""
        if not self.audio_active:
            return
        self.watch_playlists_open = False
        self.naming_playlist = False
        self.root.ids.audio_playlist_section.clear_widgets()
        self.root.ids.audio_scroll.scroll_y = 1
        self.root.current = "ecoute"

    def close_listening(self):
        self.naming_playlist = False
        self.root.current = "main"

    # --- Historique, « Reprendre » et téléchargements --------------------------------------

    def refresh_history(self):
        entries = self.history.recent()
        self.history_count = len(entries)
        self.root.ids.history_list.data = [self._row(entry["video"], "historique", entry)
                                           for entry in entries]
        resume = self.history.to_resume()
        dismissed = self.store.get("reprise")["id"] if self.store.exists("reprise") else None
        if resume and resume["video"]["id"] != dismissed:
            self.resume_video = resume["video"]
            self.resume_title = resume["video"]["title"]
            where = clock(resume["position"])
            if resume.get("duration"):
                where += " sur " + clock(resume["duration"])
            self.resume_meta = "Reprendre à " + where
        else:
            self.resume_video = None
            self.resume_title = ""

    def resume_last(self):
        """Barre « Reprendre » : la dernière vidéo commencée, ou son MP3 s'il y en a un."""
        if not self.resume_video:
            return
        if self.downloads.local_audio(self.resume_video["id"]):
            self.listen([self.resume_video])
        else:
            self.watch(self.resume_video)

    def dismiss_resume(self):
        """Cache la barre « Reprendre » jusqu'à la prochaine vidéo commencée."""
        if self.resume_video:
            self.store.put("reprise", id=self.resume_video["id"])
        self.refresh_history()

    def confirm_clear_history(self):
        ConfirmDialog(self._clear_history, message="Effacer tout l'historique\u00a0?",
                      action="Effacer").open()

    def _clear_history(self):
        self.history.clear()
        self.refresh_history()
        self._refresh_progress()

    def refresh_downloads(self, *args):
        entries = self.downloads.entries()
        self.root.ids.downloads_list.data = [self._row(entry["video"], "telechargement", entry)
                                             for entry in entries]
        if entries:
            count = f"{len(entries)} téléchargement" + ("s" if len(entries) > 1 else "")
            self.downloads_summary = f"{count} · {size_fr(self.downloads.total_size())}"
        else:
            self.downloads_summary = ""
        self._refresh_watch_download()
        if self.open_playlist_id:  # état des MP3 de la playlist ouverte
            self._show_playlist_content()

    def row_pressed(self, row):
        if row.mode == "playlist":
            videos = self.playlists.get(self.open_playlist_id)["videos"]
            self.play_playlist(self.open_playlist_id,
                               next(index for index, video in enumerate(videos)
                                    if video["id"] == row.video["id"]))
            return
        entry = self.downloads.get(row.video["id"]) if row.mode == "telechargement" else None
        if entry and entry["status"] == "echec":
            self.downloads.retry(row.video["id"])
        elif entry and entry["status"] == "fini" and entry["kind"] in AUDIO_KINDS:
            # Un MP3 : il est écouté, puis les suivants de la liste.
            videos = [entry["video"] for entry in self.downloads.entries()
                      if entry["status"] == "fini" and entry["kind"] in AUDIO_KINDS]
            self.listen(videos, next(index for index, video in enumerate(videos)
                                     if video["id"] == row.video["id"]), "Téléchargés")
        else:
            self.watch(row.video)

    def row_action(self, row):
        """Bouton à droite d'une ligne : retirer de l'historique ou de la playlist, ou supprimer
        un téléchargement."""
        video_id = row.video["id"]
        if row.mode == "playlist":
            self.playlists.remove_video(self.open_playlist_id, video_id)
            self.refresh_playlists()
        elif row.mode == "historique":
            self.history.remove(video_id)
            self.refresh_history()
            self._refresh_progress()
        elif row.mode == "telechargement":
            entry = self.downloads.get(video_id)
            where = ("\nLe MP3 sera aussi effacé du dossier Musique/Aske."
                     if entry and entry.get("uri") else "")
            ConfirmDialog(partial(self.downloads.remove, video_id), action="Supprimer",
                          message=f"Supprimer le téléchargement de «\u00a0{row.video['title']}\u00a0»\u00a0?"
                                  + where).open()

    def _row(self, video, mode, entry=None):
        """Données d'une ligne de liste (VideoRow) pour une vidéo."""
        row = {"video": video, "mode": mode, "thumbnail": video["thumbnail"], "title": video["title"],
               "duration": video.get("duration", ""), "live": bool(video.get("live")),
               "channel": video.get("channel", ""), "channel_id": video.get("channel_id", ""),
               "meta": video.get("meta", ""), "progress": self.history.progress(video["id"]),
               "trailing": ""}
        if mode == "historique":
            # Deux lignes courtes : quand, et où l'on en est (« 1:40 / 9:45:05 »).
            if entry["finished"]:
                seen = "vue en entier"
            elif entry.get("duration"):
                seen = f"{clock(entry['position'])} / {clock(entry['duration'])}"
            else:
                seen = ""
            row.update(meta="\n".join(filter(None, [ago_short(entry["watched"]), seen])),
                       trailing=ICONS["close"])
        elif mode == "telechargement":
            thumbnail = self.downloads.thumbnail(video["id"])
            row.update(meta=download_status(entry), trailing=ICONS["delete"],
                       thumbnail=thumbnail if os.path.exists(thumbnail) else video["thumbnail"])
        elif mode == "playlist":
            # Téléchargée : son état (« MP3 · 45 Mo », « Téléchargement 30 % »…).
            download = self.downloads.get(video["id"])
            if download:
                row.update(meta=download_status(download))
            row.update(trailing=ICONS["close"])
        return row

    def _refresh_progress(self):
        """Barres de progression des listes, après avoir regardé une vidéo."""
        for name in ("results", "playlist_list", "downloads_list"):
            rows = self.root.ids[name]
            for item in rows.data:
                item["progress"] = self.history.progress(item["video"]["id"])
            rows.refresh_from_data()
        for card in self.root.ids.videos.children:
            if isinstance(card, VideoCard):
                card.progress = self.history.progress(card.video["id"])

    # --- Cycle de vie -----------------------------------------------------------------------

    def on_pause(self):
        self.history.save()
        if self.player:
            self.player.pause()
        return True

    def on_resume(self):
        if self.player:
            self.player.resume()
        self._audio_changed()  # l'écoute a pu avancer pendant qu'Aske était cachée

    def on_start(self):
        self.refresh_favorites()
        self.refresh_history()
        self.refresh_downloads()
        self.refresh_playlists()
        self.downloads.start()  # téléchargements interrompus à la dernière fermeture
        # Réaffiche la dernière chaîne ou la dernière recherche (« chaine » : clé d'Aske 1.2).
        key = next((key for key in ("affichage", "chaine") if self.store.exists(key)), None)
        if not key:
            self.status = ("Tapez des mots-clés (par exemple\u00a0: livre audio) pour chercher des "
                           "vidéos, ou une @chaîne pour afficher ses 10 dernières vidéos.")
            return
        saved = self.store.get(key)
        if saved["channel_id"]:
            self._show_channel(saved["query"], saved["channel_id"])
        else:
            self.root.ids.query.text = saved["query"]
            self.load(saved["query"])

    # --- Chaînes et recherche ---------------------------------------------------------------

    def load(self, text):
        """Lance ce qui a été tapé : l'affichage d'une chaîne, ou une recherche de vidéos."""
        query = text.strip()
        if not query:
            return
        self.back_stack.clear()
        if is_channel(query):
            self._load_channel(query)
        else:
            self._load_search(query)

    def _load_channel(self, query):
        self.pending = query
        self.status = "Chargement…"
        threading.Thread(target=self._fetch_channel, args=(query,), daemon=True).start()

    def _load_search(self, query):
        self.pending = query
        self.search_number += 1
        self.status = "Recherche…"
        threading.Thread(target=self._fetch_search, args=(query, self.search_number), daemon=True).start()

    def _fetch_channel(self, query):
        try:
            channel_id = self.channel_ids.get(query) or resolve_channel_id(query)
            channel, videos = fetch_videos(channel_id)
        except YouTubeError as exc:
            self._show_error(query, str(exc), "chaine")
        else:
            self._show_videos(query, channel_id, channel, videos)

    def _fetch_search(self, query, number):
        found = 0
        try:
            for videos in search_videos(query):
                if number != self.search_number:
                    return  # une nouvelle recherche a remplacé celle-ci
                self._add_results(query, number, videos, first=not found)
                found += len(videos)
        except YouTubeError as exc:
            if not found:  # sinon, on garde les résultats déjà affichés
                self._show_error(query, str(exc), "resultats")
            return
        if not found:
            self._show_error(query, f"Aucune vidéo trouvée pour «\u00a0{query}\u00a0».", "resultats")

    @mainthread
    def _show_videos(self, query, channel_id, channel, videos):
        if query != self.pending:  # autre chose a été demandé entre-temps
            return
        self.channel_ids[query] = channel_id
        self.store.put("affichage", query=query, channel_id=channel_id)
        self.current = {"id": channel_id, "name": channel, "query": query}
        self.is_favorite = any(fav["id"] == channel_id for fav in self.favorites)
        self.channel_videos = videos
        box = self.root.ids.videos
        box.clear_widgets()
        box.add_widget(ChannelHeader(text=channel))
        for video in videos:
            video.update(channel=channel, channel_id=channel_id, channel_query=query)
            box.add_widget(VideoCard(video=video, thumbnail=video["thumbnail"], title=video["title"],
                                     meta=video["meta"], description=video["description"],
                                     progress=self.history.progress(video["id"])))
        self.root.ids.scroll.scroll_y = 1
        self.root.ids.views.current = "chaine"
        self.status = "" if videos else "Cette chaîne n'a publié aucune vidéo."

    @mainthread
    def _add_results(self, query, number, videos, first):
        """Affiche la première page de résultats, ou ajoute les suivantes en bas de la liste."""
        if number != self.search_number:
            return
        results = self.root.ids.results
        rows = [self._row(video, "recherche") for video in videos]
        if not first:
            results.data.extend(rows)
            return
        results.scroll_y = 1
        results.data = rows
        if query == self.pending:  # sinon, une chaîne a été ouverte entre-temps
            self.store.put("affichage", query=query, channel_id="")
            self.root.ids.views.current = "resultats"
            self.status = ""

    def _back_to_search(self, query):
        self.pending = query
        self.store.put("affichage", query=query, channel_id="")
        self.root.ids.query.text = query
        self.root.ids.views.current = "resultats"
        self.status = ""

    @mainthread
    def _show_error(self, query, message, view):
        if query != self.pending:
            return
        if view == "chaine":
            self.current = None
            self.channel_videos = []
            self.is_favorite = False
            self.root.ids.videos.clear_widgets()
        else:
            self.root.ids.results.data = []
        self.root.ids.views.current = view
        self.status = message


if __name__ == "__main__":
    AskeApp().run()
