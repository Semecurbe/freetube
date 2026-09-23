package fr.perso.freetube;

import android.net.Uri;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.util.Collections;

/**
 * Garde la navigation dans le lecteur, et empêche yout-ube.com de lire les vidéos en boucle.
 *
 * yout-ube.com redirige vers le lecteur de YouTube avec « loop=1&playlist=ID » : la vidéo
 * recommencerait sans fin. Sans ces paramètres, elle s'arrête à la fin, et Aske peut enchaîner
 * la suivante d'une playlist. Le Referer de yout-ube.com est gardé : sans lui, YouTube refuse
 * de lire la vidéo (erreur 153).
 */
public class PlayerClient extends WebViewClient {
    @Override
    public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
        Uri uri = request.getUrl();
        String path = uri.getPath();
        if (!request.isForMainFrame() || path == null || !path.startsWith("/embed/")
                || uri.getQueryParameter("loop") == null) {
            return false;
        }
        Uri.Builder builder = uri.buildUpon().clearQuery();
        for (String name : uri.getQueryParameterNames()) {
            if (!name.equals("loop") && !name.equals("playlist")) {
                builder.appendQueryParameter(name, uri.getQueryParameter(name));
            }
        }
        view.loadUrl(builder.build().toString(),
                Collections.singletonMap("Referer", "https://www.yout-ube.com/"));
        return true;
    }
}
