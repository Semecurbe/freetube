"""Lecteur vidéo intégré (Android) : une WebView plein écran par-dessus l'interface Kivy.

Écran éteint, la vidéo continue : BackgroundWebView ne se laisse pas mettre en pause quand
la fenêtre devient invisible, et le service MediaService garde Aske actif (dossier java/).
"""

from android.runnable import run_on_ui_thread
from jnius import PythonJavaClass, autoclass, cast, java_method

ActivityInfo = autoclass("android.content.pm.ActivityInfo")
BackgroundWebView = autoclass("fr.perso.freetube.BackgroundWebView")
BuildVersion = autoclass("android.os.Build$VERSION")
Color = autoclass("android.graphics.Color")
Context = autoclass("android.content.Context")
KeyEvent = autoclass("android.view.KeyEvent")
LayoutParams = autoclass("android.view.ViewGroup$LayoutParams")
MediaService = autoclass("fr.perso.freetube.MediaService")
PythonActivity = autoclass("org.kivy.android.PythonActivity")
View = autoclass("android.view.View")
WebChromeClient = autoclass("android.webkit.WebChromeClient")
WebViewClient = autoclass("android.webkit.WebViewClient")

PAUSE_VIDEOS = "document.querySelectorAll('video').forEach(v => v.pause())"


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


class AndroidPlayer:
    """Ouvre une vidéo en plein écran dans l'application ; « retour » la referme."""

    def __init__(self):
        self.webview = None
        self.title = ""
        # Référence gardée côté Python : sinon l'écouteur serait détruit par le ramasse-miettes.
        self.back_listener = BackKeyListener(self.close)

    @property
    def is_open(self):
        return self.webview is not None

    @run_on_ui_thread
    def open(self, url, title):
        activity = PythonActivity.mActivity
        self.title = title
        MediaService.start(activity, title)
        if self.webview:
            self.webview.loadUrl(url)
            return
        webview = BackgroundWebView(activity)
        settings = webview.getSettings()
        settings.setJavaScriptEnabled(True)
        settings.setDomStorageEnabled(True)
        settings.setMediaPlaybackRequiresUserGesture(False)
        # Sans WebViewClient, la redirection de yout-ube.com partirait dans le navigateur.
        webview.setWebViewClient(WebViewClient())
        webview.setWebChromeClient(WebChromeClient())
        webview.setBackgroundColor(Color.BLACK)
        webview.setOnKeyListener(self.back_listener)
        activity.addContentView(webview, LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT))
        webview.requestFocus()
        webview.loadUrl(url)
        # La vidéo suit l'orientation du téléphone, même si la rotation automatique est bloquée.
        activity.setRequestedOrientation(ActivityInfo.SCREEN_ORIENTATION_SENSOR)
        self._show_system_bars(False)
        self.webview = webview

    @run_on_ui_thread
    def close(self):
        if not self.webview:
            return
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
            self.webview.evaluateJavascript(PAUSE_VIDEOS, None)
            self.webview.onPause()
            MediaService.stop(activity)

    @run_on_ui_thread
    def resume(self):
        if self.webview:
            self.webview.onResume()
            MediaService.start(PythonActivity.mActivity, self.title)

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
