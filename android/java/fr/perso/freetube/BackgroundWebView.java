package fr.perso.freetube;

import android.content.Context;
import android.view.View;
import android.webkit.WebView;

/**
 * WebView qui continue de se croire affichée quand l'écran s'éteint.
 *
 * Sans cela, le moteur de la WebView (Chromium) met la vidéo en pause dès que la fenêtre
 * d'Aske devient invisible. La pause quand on passe à une autre application est gérée
 * à part, dans player.py.
 */
public class BackgroundWebView extends WebView {
    public BackgroundWebView(Context context) {
        super(context);
    }

    @Override
    protected void onWindowVisibilityChanged(int visibility) {
        if (visibility != View.GONE) {
            super.onWindowVisibilityChanged(View.VISIBLE);
        }
    }
}
