"""FreeTube : les 10 dernières vidéos d'une chaîne YouTube, à regarder sur yout-ube.com."""

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from functools import lru_cache
from urllib.parse import quote

import requests
from flask import Flask, render_template, request

# Chaîne affichée quand aucune n'est demandée (URL, @handle ou ID « UC… ») — facultatif.
DEFAULT_CHANNEL = os.environ.get("YOUTUBE_CHANNEL", "")
MAX_VIDEOS = 10
WATCH_URL = "https://www.yout-ube.com/watch?v={}"
THUMBNAIL_URL = "https://i.ytimg.com/vi/{}/hqdefault.jpg"
FEED_URL = "https://www.youtube.com/feeds/videos.xml"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
}
# Évite la page de consentement aux cookies que YouTube affiche en Europe.
COOKIES = {"SOCS": "CAI"}

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

MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
          "août", "septembre", "octobre", "novembre", "décembre"]

app = Flask(__name__)


class ChannelError(Exception):
    """Erreur dont le message est affiché tel quel dans la page."""


@lru_cache(maxsize=128)
def resolve_channel_id(query):
    """Transforme une URL de chaîne, un @handle ou un ID en ID de chaîne « UC… »."""
    match = ID_IN_QUERY.search(query)
    if match:
        return match.group(1)

    if "youtube.com/" in query:  # https://www.youtube.com/@nom, /c/nom, /user/nom…
        path = query.split("youtube.com", 1)[1]
    else:  # @nom ou nom
        path = "/@" + quote(query.removeprefix("@"), safe="")

    try:
        response = requests.get("https://www.youtube.com" + path,
                                headers=HEADERS, cookies=COOKIES, timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        raise ChannelError(f"Chaîne introuvable : « {query} ».") from None

    match = ID_IN_PAGE.search(response.text)
    if not match:
        raise ChannelError(f"Impossible de trouver l'identifiant de la chaîne « {query} ».")
    return match.group(1) or match.group(2)


def fetch_videos(channel_id):
    """Lit le flux RSS de la chaîne : renvoie son nom et ses dernières vidéos."""
    try:
        response = requests.get(FEED_URL, params={"channel_id": channel_id},
                                headers=HEADERS, cookies=COOKIES, timeout=10)
        response.raise_for_status()
        feed = ET.fromstring(response.content)
    except (requests.RequestException, ET.ParseError):
        raise ChannelError("Impossible de récupérer les vidéos de cette chaîne.") from None

    videos = []
    for entry in feed.findall("atom:entry", NS)[:MAX_VIDEOS]:
        video_id = entry.findtext("yt:videoId", namespaces=NS)
        stats = entry.find("media:group/media:community/media:statistics", NS)
        videos.append({
            "url": WATCH_URL.format(video_id),
            "thumbnail": THUMBNAIL_URL.format(video_id),
            "title": entry.findtext("atom:title", namespaces=NS),
            "description": entry.findtext("media:group/media:description", "", NS).strip(),
            "published": datetime.fromisoformat(entry.findtext("atom:published", namespaces=NS)),
            "views": int(stats.get("views", 0)) if stats is not None else None,
        })
    return feed.findtext("atom:title", "", NS), videos


@app.template_filter("date_fr")
def date_fr(value):
    """datetime → « 1er septembre 2026 », à l'heure locale."""
    value = value.astimezone()
    day = "1er" if value.day == 1 else value.day
    return f"{day} {MONTHS[value.month - 1]} {value.year}"


@app.template_filter("views_fr")
def views_fr(count):
    """232545 → « 232 545 vues »."""
    return f"{count:,}".replace(",", "\u202f") + (" vue" if count < 2 else " vues")


@app.route("/")
def index():
    query = request.args.get("chaine", DEFAULT_CHANNEL).strip()
    channel_name, videos, error = "", [], None
    if query:
        try:
            channel_name, videos = fetch_videos(resolve_channel_id(query))
        except ChannelError as exc:
            error = str(exc)
    return render_template("index.html", query=query, channel_name=channel_name,
                           videos=videos, error=error)


if __name__ == "__main__":
    app.run()
