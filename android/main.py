"""Aske : vos chaînes YouTube et la recherche de vidéos, lues via yout-ube.com."""

# Lu aussi par Buildozer (version.regex dans buildozer.spec) : c'est la version de l'APK.
__version__ = "1.3"

import gzip
import http.client
import json
import os
import re
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import certifi
from kivy.app import App
from kivy.clock import mainthread
from kivy.core.image import Image as CoreImage
from kivy.core.window import Window
from kivy.graphics.texture import Texture
from kivy.loader import Loader
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.storage.jsonstore import JsonStore
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.recycleview import RecycleView
from kivy.utils import get_color_from_hex, platform

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
            "url": WATCH_URL.format(video_id),
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
    """Fiche d'une vidéo trouvée, prête pour la liste des résultats (SearchResult)."""
    byline = renderer.get("longBylineText") or renderer.get("ownerText") or {}
    # Vidéo commune à plusieurs chaînes : le lien mène à la première.
    channel_id, address = find_channel(byline) or ("", "")
    live = any(badge.get("metadataBadgeRenderer", {}).get("style") == "BADGE_STYLE_TYPE_LIVE_NOW"
               for badge in renderer.get("badges", []))
    views = text_of(renderer.get("shortViewCountText") or renderer.get("viewCountText"))
    return {
        "url": WATCH_URL.format(video_id),
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


def views_fr(count):
    """232545 → « 232 545 vues »."""
    return f"{count:,}".replace(",", "\u00a0") + (" vue" if count < 2 else " vues")


class VideoCard(ButtonBehavior, BoxLayout):
    """Vignette d'une vidéo : un appui la lance."""

    url = StringProperty()
    thumbnail = StringProperty()
    title = StringProperty()
    meta = StringProperty()
    description = StringProperty()

    def on_release(self):
        App.get_running_app().play(self.url, self.title)


class SearchResult(ButtonBehavior, BoxLayout):
    """Une vidéo trouvée par la recherche : un appui la lance ; un appui sur le nom
    de sa chaîne affiche les vidéos de celle-ci."""

    url = StringProperty()
    thumbnail = StringProperty()
    title = StringProperty()
    duration = StringProperty()
    live = BooleanProperty(False)
    meta = StringProperty()
    channel = StringProperty()
    channel_id = StringProperty()
    channel_query = StringProperty()

    def on_release(self):
        App.get_running_app().play(self.url, self.title)

    def open_channel(self):
        if self.channel_id:
            App.get_running_app().open_channel(self.channel_query, self.channel_id, from_search=True)


class ResultList(RecycleView):
    """Liste des résultats : seules les lignes visibles à l'écran existent, même pour 100 vidéos.

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


class ChannelHeader(BoxLayout):
    """Nom de la chaîne affichée, avec le bouton pour l'ajouter à « Mes chaînes »."""

    text = StringProperty()


class ChannelRow(ButtonBehavior, BoxLayout):
    """Une chaîne de l'onglet « Mes chaînes » : un appui affiche ses vidéos."""

    channel_id = StringProperty()
    name = StringProperty()
    query = StringProperty()

    def on_release(self):
        App.get_running_app().open_channel(self.query, self.channel_id)


class AskeApp(App):
    version = __version__
    status = StringProperty()
    tab = StringProperty("videos")
    favorite_count = NumericProperty(0)
    # La chaîne affichée fait-elle partie de « Mes chaînes » ?
    is_favorite = BooleanProperty(False)

    def build(self):
        Window.clearcolor = get_color_from_hex("#0f0f0f")
        # Miniatures vides pendant le chargement ou en cas d'échec,
        # au lieu des images par défaut de Kivy.
        blank = Texture.create(size=(1, 1))
        blank.blit_buffer(bytes(4), colorfmt="rgba", bufferfmt="ubyte")
        Loader.loading_image = Loader.error_image = CoreImage(blank)
        Loader.num_workers = 4

        self.store = JsonStore(os.path.join(self.user_data_dir, "reglages.json"))
        # Chaîne ou recherche attendue à l'écran : les réponses arrivées trop tard sont ignorées.
        self.pending = None
        # Numéro de la recherche en cours : ses pages suivantes s'ajoutent à la liste.
        self.search_number = 0
        # Recherche à réafficher avec « retour », après avoir ouvert la chaîne d'un résultat.
        self.previous_search = None
        # ID des chaînes déjà trouvées, pour ne pas retélécharger leur page (lourde).
        self.channel_ids = {}
        # Chaîne affichée, et « Mes chaînes » : fiches {"id", "name", "query"}.
        self.current = None
        self.favorites = self.store.get("favoris")["chaines"] if self.store.exists("favoris") else []
        self.player = AndroidPlayer() if platform == "android" else None
        Window.bind(on_keyboard=self.on_keyboard)
        # L'interface est décrite dans aske.kv, chargé automatiquement par Kivy.

    def play(self, url, title):
        """Lit la vidéo dans l'application (Android) ou dans le navigateur (ordinateur)."""
        if self.player:
            self.player.open(url, title)
        else:
            webbrowser.open(url)

    def on_keyboard(self, window, key, *args):
        # « Retour » d'Android (touche Échap pour Kivy) : referme d'abord le lecteur, puis
        # revient à l'onglet Vidéos, puis aux résultats de recherche d'où l'on a ouvert une
        # chaîne ; sinon, quitte l'application.
        if key != 27:
            return False
        if self.player and self.player.is_open:
            self.player.close()
            return True
        if self.tab != "videos":
            self.tab = "videos"
            return True
        if self.previous_search:
            self._back_to_search()
            return True
        return False

    def on_tab(self, app, tab):
        self.root.ids.screens.current = tab

    def toggle_favorite(self):
        """Ajoute la chaîne affichée à « Mes chaînes », ou l'en retire."""
        if not self.current:
            return
        if self.is_favorite:
            self.remove_favorite(self.current["id"])
        else:
            self.favorites.append(dict(self.current))
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
        self.is_favorite = bool(self.current) and any(
            fav["id"] == self.current["id"] for fav in self.favorites)

    def open_channel(self, query, channel_id, from_search=False):
        """Affiche une chaîne choisie dans « Mes chaînes » ou sous un résultat de recherche."""
        self.previous_search = self.pending if from_search else None
        self.channel_ids[query] = channel_id
        self.root.ids.query.text = query
        self.tab = "videos"
        self._load_channel(query)

    def on_pause(self):
        if self.player:
            self.player.pause()
        return True

    def on_resume(self):
        if self.player:
            self.player.resume()

    def on_start(self):
        self.refresh_favorites()
        # Réaffiche la dernière chaîne ou la dernière recherche (« chaine » : clé d'Aske 1.2).
        key = next((key for key in ("affichage", "chaine") if self.store.exists(key)), None)
        if not key:
            self.status = ("Tapez des mots-clés (par exemple\u00a0: livre audio) pour chercher des "
                           "vidéos, ou une @chaîne pour afficher ses 10 dernières vidéos.")
            return
        saved = self.store.get(key)
        if saved["channel_id"]:
            self.open_channel(saved["query"], saved["channel_id"])
        else:
            self.root.ids.query.text = saved["query"]
            self.load(saved["query"])

    def load(self, text):
        """Lance ce qui a été tapé : l'affichage d'une chaîne, ou une recherche de vidéos."""
        query = text.strip()
        if not query:
            return
        self.previous_search = None
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
        box = self.root.ids.videos
        box.clear_widgets()
        box.add_widget(ChannelHeader(text=channel))
        for video in videos:
            box.add_widget(VideoCard(**video))
        self.root.ids.scroll.scroll_y = 1
        self.root.ids.views.current = "chaine"
        self.status = "" if videos else "Cette chaîne n'a publié aucune vidéo."

    @mainthread
    def _add_results(self, query, number, videos, first):
        """Affiche la première page de résultats, ou ajoute les suivantes en bas de la liste."""
        if number != self.search_number:
            return
        results = self.root.ids.results
        if not first:
            results.data.extend(videos)
            return
        results.scroll_y = 1
        results.data = videos
        if query == self.pending:  # sinon, une chaîne a été ouverte entre-temps
            self.store.put("affichage", query=query, channel_id="")
            self.root.ids.views.current = "resultats"
            self.status = ""

    def _back_to_search(self):
        query, self.previous_search = self.previous_search, None
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
            self.is_favorite = False
            self.root.ids.videos.clear_widgets()
        else:
            self.root.ids.results.data = []
        self.root.ids.views.current = view
        self.status = message


if __name__ == "__main__":
    AskeApp().run()
