"""Lecteur vidéo intégré (Android) : une WebView par-dessus l'interface Kivy.

En portrait, la vidéo occupe le haut de l'écran et la page de lecture de Kivy s'affiche
dessous ; en paysage, elle passe en plein écran. Toutes les 2 secondes, la position de
lecture est relevée (pour reprendre la vidéo plus tard) et la fin de la vidéo signalée
(pour enchaîner la suivante d'une playlist).

Écran éteint, la vidéo continue : BackgroundWebView ne se laisse pas mettre en pause quand
la fenêtre devient invisible, et le service MediaService garde Aske actif (dossier java/).
"""

import json

from android.runnable import run_on_ui_thread
from jnius import PythonJavaClass, autoclass, cast, java_method

ActivityInfo = autoclass("android.content.pm.ActivityInfo")
BackgroundWebView = autoclass("fr.perso.freetube.BackgroundWebView")
BuildVersion = autoclass("android.os.Build$VERSION")
Color = autoclass("android.graphics.Color")
Context = autoclass("android.content.Context")
FrameParams = autoclass("android.widget.FrameLayout$LayoutParams")
Gravity = autoclass("android.view.Gravity")
Handler = autoclass("android.os.Handler")
KeyEvent = autoclass("android.view.KeyEvent")
Looper = autoclass("android.os.Looper")
MediaService = autoclass("fr.perso.freetube.MediaService")
PlayerClient = autoclass("fr.perso.freetube.PlayerClient")
PythonActivity = autoclass("org.kivy.android.PythonActivity")
View = autoclass("android.view.View")
WebChromeClient = autoclass("android.webkit.WebChromeClient")

MATCH_PARENT = FrameParams.MATCH_PARENT
POLL_INTERVAL_MS = 2000
PAUSE_VIDEOS = "document.querySelectorAll('video').forEach(v => v.pause())"
# Renvoie [position, durée, finie, adresse de la page et de la vidéo]. « finie » reste vrai
# après la fin, jusqu'à ce que la vidéo soit relancée.
READ_POSITION = """(function () {
  var v = document.querySelector('video');
  if (!v) return null;
  if (!v.askeWatch) {
    v.askeWatch = true;
    v.addEventListener('ended', function () { v.askeEnded = true; });
    v.addEventListener('play', function () { v.askeEnded = false; });
  }
  return [v.currentTime, isFinite(v.duration) ? v.duration : 0, !!(v.ended || v.askeEnded),
          location.href + ' ' + (v.getAttribute('src') || '')];
})()"""


class BackKeyListener(PythonJavaClass):
    """Reçoit le bouton (ou geste) « retour » quand le lecteur a le focus."""

    __javainterfaces__ = ["android/view/View$OnKeyListener"]
    __javacontext__ = "app"

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    @java_method("(Landroid/view/View;ILandroid/view/KeyEvent;)Z")
    def onKey(self, view, key_code, event):
        if key_code != KeyEvent.KEYCODE_BACK:
            return False
        if event.getAction() == KeyEvent.ACTION_UP:
            self.callback()
        return True


class JsResult(PythonJavaClass):
    """Reçoit le résultat d'un script exécuté dans la page (evaluateJavascript), en JSON."""

    __javainterfaces__ = ["android/webkit/ValueCallback"]
    __javacontext__ = "app"

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    @java_method("(Ljava/lang/Object;)V")
    def onReceiveValue(self, value):
        if value is not None:
            self.callback(value if isinstance(value, str) else value.toString())


class Repeat(PythonJavaClass):
    """Appelle une fonction toutes les `interval` ms sur le fil d'Android.

    Ce fil tourne même quand Kivy est à l'arrêt (écran éteint, Aske en arrière-plan).
    """

    __javainterfaces__ = ["java/lang/Runnable"]
    __javacontext__ = "app"

    def __init__(self, function, interval):
        super().__init__()
        self.function = function
        self.interval = interval
        self.handler = Handler(Looper.getMainLooper())

    def start(self):
        self.handler.removeCallbacks(self)
        self.handler.postDelayed(self, self.interval)

    def stop(self):
        self.handler.removeCallbacks(self)

    @java_method("()V")
    def run(self):
        self.function()
        self.handler.postDelayed(self, self.interval)


class AndroidPlayer:
    """Lit une vidéo dans la page de lecture ; on_back est appelée par « retour »,
    on_position(position, durée, finie, adresse) toutes les 2 s (depuis le fil d'Android)."""

    def __init__(self, on_back, on_position):
        self.webview = None
        self.title = ""
        self.height = 0
        self.on_position = on_position
        # Références gardées côté Python : sinon le ramasse-miettes les détruirait.
        self.back_listener = BackKeyListener(on_back)
        self.position_result = JsResult(self._position_received)
        self.poller = Repeat(self._poll, POLL_INTERVAL_MS)

    @property
    def is_open(self):
        return self.webview is not None

    @run_on_ui_thread
    def open(self, title, height, fullscreen, url=None, html=None, base_url=None):
        """Ouvre une adresse (url), ou une page locale (html) pour un fichier téléchargé.

        height : hauteur de la vidéo en portrait, en pixels.
        """
        activity = PythonActivity.mActivity
        self.title = title
        self.height = height
        MediaService.start(activity, title)
        webview = self.webview
        if webview is None:
            webview = BackgroundWebView(activity)
            settings = webview.getSettings()
            settings.setJavaScriptEnabled(True)
            settings.setDomStorageEnabled(True)
            settings.setMediaPlaybackRequiresUserGesture(False)
            # Garde la redirection de yout-ube.com dans le lecteur (sinon elle partirait dans le
            # navigateur), sans la lecture en boucle qu'elle impose : voir PlayerClient.java.
            webview.setWebViewClient(PlayerClient())
            webview.setWebChromeClient(WebChromeClient())
            webview.setBackgroundColor(Color.BLACK)
            webview.setOnKeyListener(self.back_listener)
            activity.addContentView(webview, FrameParams(MATCH_PARENT, height, Gravity.TOP))
            webview.requestFocus()
            self.webview = webview
        # Les fichiers téléchargés sont lus depuis le stockage d'Aske (file://).
        webview.getSettings().setAllowFileAccess(html is not None)
        if html is not None:
            webview.loadDataWithBaseURL(base_url, html, "text/html", "utf-8", None)
        else:
            webview.loadUrl(url)
        # La vidéo suit l'orientation du téléphone, même si la rotation automatique est bloquée.
        activity.setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_SENSOR)
        self._layout(fullscreen)
        self.poller.start()

    @run_on_ui_thread
    def set_fullscreen(self, fullscreen):
        if self.webview:
            self._layout(fullscreen)

    @run_on_ui_thread
    def close(self):
        if not self.webview:
            return
        self.poller.stop()
        activity = PythonActivity.mActivity
        webview, self.webview = self.webview, None
        cast("android.view.ViewGroup", webview.getParent()).removeView(webview)
        webview.destroy()
        MediaService.stop(activity)
        self._show_system_bars(True)
        activity.setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_PORTRAIT)

    @run_on_ui_thread
    def pause(self):
        """Aske passe en arrière-plan : la lecture continue seulement si l'écran s'est éteint."""
        if not self.webview:
            return
        activity = PythonActivity.mActivity
        power = cast("android.os.PowerManager", activity.getSystemService(Context.POWER_SERVICE))
        if power.isInteractive():
            # Écran allumé : on est passé à une autre application, on met en pause.
            self._poll()
            self.webview.evaluateJavascript(PAUSE_VIDEOS, None)
            self.webview.onPause()
            MediaService.stop(activity)

    @run_on_ui_thread
    def resume(self):
        if self.webview:
            self.webview.onResume()
            MediaService.start(PythonActivity.mActivity, self.title)

    def _poll(self):
        if self.webview:
            self.webview.evaluateJavascript(READ_POSITION, self.position_result)

    def _position_received(self, value):
        try:
            position, duration, ended, where = json.loads(value)
        except (TypeError, ValueError):
            return  # pas encore de vidéo dans la page
        self.on_position(position, duration, ended, where)

    def _layout(self, fullscreen):
        """Plein écran (paysage), ou vidéo en haut de la page de lecture (portrait)."""
        height = MATCH_PARENT if fullscreen else self.height
        self.webview.setLayoutParams(FrameParams(MATCH_PARENT, height, Gravity.TOP))
        self._show_system_bars(not fullscreen)

    def _show_system_bars(self, show):
        """Affiche ou masque la barre d'état et la barre de navigation (mode immersif).

        Barres masquées, la fenêtre s'étend aussi sous leur emplacement : la vidéo occupe
        tout l'écran au lieu de laisser deux bandes vides.
        """
        window = PythonActivity.mActivity.getWindow()
        # Fond noir, comme les bandes d'une vidéo, si une zone reste découverte (encoche…).
        window.getDecorView().setBackgroundColor(Color.BLACK)
        if BuildVersion.SDK_INT >= 30:
            insets_type = autoclass("android.view.WindowInsets$Type")
            controller = window.getInsetsController()
            window.setDecorFitsSystemWindows(show)
            if show:
                controller.show(insets_type.systemBars())
            else:
                behavior = autoclass("android.view.WindowInsetsController")
                controller.setSystemBarsBehavior(behavior.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE)
                controller.hide(insets_type.systemBars())
        else:
            flags = 0 if show else (View.SYSTEM_UI_FLAG_LAYOUT_STABLE | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                                    | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_FULLSCREEN
                                    | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY)
            window.getDecorView().setSystemUiVisibility(flags)
