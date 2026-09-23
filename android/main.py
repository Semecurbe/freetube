"""FreeTube : les 10 dernières vidéos d'une chaîne YouTube, à regarder sur yout-ube.com."""

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
from kivy.factory import Factory
from kivy.graphics.texture import Texture
from kivy.loader import Loader
from kivy.properties import StringProperty
from kivy.storage.jsonstore import JsonStore
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.utils import get_color_from_hex, platform

if platform == "android":
    from player import AndroidPlayer

MAX_VIDEOS = 10
DESCRIPTION_LENGTH = 300
WATCH_URL = "https://www.yout-ube.com/watch?v={}"
THUMBNAIL_URL = "https://i.ytimg.com/vi/{}/hqdefault.jpg"
FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36",
    # Évite la page de consentement aux cookies que YouTube affiche en Europe.
    "Cookie": "SOCS=CAI",
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


class ChannelError(Exception):
    """Erreur dont le message est affiché tel quel à l'écran."""


def http_get(url):
    """Télécharge une adresse ; une panne de réseau devient une ChannelError."""
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=15, context=SSL_CONTEXT) as response:
            return response.read()
    except urllib.error.HTTPError:
        raise  # YouTube a répondu (404…) : l'appelant choisit le message
    except OSError:
        raise ChannelError("Impossible de joindre YouTube. Vérifiez la connexion Internet.") from None


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
        raise ChannelError(f"Chaîne introuvable\u00a0: «\u00a0{query}\u00a0».") from None

    match = ID_IN_PAGE.search(page)
    if not match:
        raise ChannelError(f"Impossible de trouver l'identifiant de la chaîne «\u00a0{query}\u00a0».")
    return match.group(1) or match.group(2)


def fetch_videos(channel_id):
    """Lit le flux RSS de la chaîne : renvoie son nom et ses dernières vidéos."""
    try:
        feed = ET.fromstring(http_get(FEED_URL.format(channel_id)))
    except (urllib.error.HTTPError, ET.ParseError):
        raise ChannelError("Impossible de récupérer les vidéos de cette chaîne.") from None

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
        App.get_running_app().play(self.url)


class FreeTubeApp(App):
    status = StringProperty()

    def build(self):
        Window.clearcolor = get_color_from_hex("#0f0f0f")
        # Miniatures vides pendant le chargement ou en cas d'échec,
        # au lieu des images par défaut de Kivy.
        blank = Texture.create(size=(1, 1))
        blank.blit_buffer(bytes(4), colorfmt="rgba", bufferfmt="ubyte")
        Loader.loading_image = Loader.error_image = CoreImage(blank)
        Loader.num_workers = 4

        self.store = JsonStore(os.path.join(self.user_data_dir, "reglages.json"))
        self.pending = None
        # ID des chaînes déjà trouvées, pour ne pas retélécharger leur page (lourde).
        self.channel_ids = {}
        self.player = AndroidPlayer() if platform == "android" else None
        Window.bind(on_keyboard=self.on_keyboard)
        # L'interface est décrite dans freetube.kv, chargé automatiquement par Kivy.

    def play(self, url):
        """Lit la vidéo dans l'application (Android) ou dans le navigateur (ordinateur)."""
        if self.player:
            self.player.open(url)
        else:
            webbrowser.open(url)

    def on_keyboard(self, window, key, *args):
        # « Retour » d'Android (touche Échap pour Kivy) : referme d'abord le lecteur.
        if key == 27 and self.player and self.player.is_open:
            self.player.close()
            return True
        return False

    def on_pause(self):
        if self.player:
            self.player.pause()
        return True

    def on_resume(self):
        if self.player:
            self.player.resume()

    def on_start(self):
        if self.store.exists("chaine"):
            saved = self.store.get("chaine")
            self.channel_ids[saved["query"]] = saved["channel_id"]
            self.root.ids.query.text = saved["query"]
            self.load(saved["query"])
        else:
            self.status = "Entrez une chaîne YouTube pour afficher ses 10 dernières vidéos."

    def load(self, query):
        """Charge en arrière-plan les vidéos de la chaîne demandée."""
        query = query.strip()
        if not query:
            return
        self.pending = query
        self.status = "Chargement…"
        threading.Thread(target=self._fetch, args=(query,), daemon=True).start()

    def _fetch(self, query):
        try:
            channel_id = self.channel_ids.get(query) or resolve_channel_id(query)
            channel, videos = fetch_videos(channel_id)
        except ChannelError as exc:
            self._show_error(query, str(exc))
        else:
            self._show_videos(query, channel_id, channel, videos)

    @mainthread
    def _show_videos(self, query, channel_id, channel, videos):
        if query != self.pending:  # une autre chaîne a été demandée entre-temps
            return
        self.channel_ids[query] = channel_id
        self.store.put("chaine", query=query, channel_id=channel_id)
        box = self.root.ids.videos
        box.clear_widgets()
        box.add_widget(Factory.ChannelTitle(text=channel))
        for video in videos:
            box.add_widget(VideoCard(**video))
        self.root.ids.scroll.scroll_y = 1
        self.status = "" if videos else "Cette chaîne n'a publié aucune vidéo."

    @mainthread
    def _show_error(self, query, message):
        if query != self.pending:
            return
        self.root.ids.videos.clear_widgets()
        self.status = message


if __name__ == "__main__":
    FreeTubeApp().run()
