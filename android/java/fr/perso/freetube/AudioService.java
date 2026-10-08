package fr.perso.freetube;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ServiceInfo;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.drawable.Icon;
import android.media.AudioAttributes;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.media.MediaMetadata;
import android.media.MediaPlayer;
import android.media.session.MediaSession;
import android.media.session.PlaybackState;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;

import org.json.JSONException;
import org.json.JSONObject;
import org.kivy.android.PythonActivity;

import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;

/**
 * Lecteur audio : joue une liste de pistes à la suite, en arrière-plan.
 *
 * Contrairement aux vidéos (lues par une WebView, dans l'écran d'Aske), le son continue quand
 * on passe à une autre application ou qu'on éteint l'écran. La notification, l'écran de
 * verrouillage et les boutons d'un casque le commandent (MediaSession).
 *
 * Une piste est un MP3 du téléphone, ou le son d'une vidéo lu directement sur YouTube. Pour
 * celle-ci, l'adresse est d'abord vide : le service attend qu'Aske la trouve (avec yt-dlp) et
 * la lui donne avec provide. Elle expire au bout de quelques heures : en cas d'erreur, le
 * service la vide et attend une nouvelle adresse, puis reprend au même endroit.
 *
 * Aske (audio.py) lui confie la liste avec play, le commande avec command, seekTo, jump et
 * provide, et relève où il en est avec les get… : tout cela depuis le fil principal d'Android,
 * celui du service. L'enchaînement des pistes se fait ici, sans attendre Aske (en arrière-plan,
 * Kivy est à l'arrêt ; Aske continue de relever où en est le lecteur, voir audio.py).
 */
public class AudioService extends Service {
    public static final String TOGGLE = "lecture";
    public static final String PAUSE = "pause";
    public static final String NEXT = "suivante";
    public static final String PREVIOUS = "precedente";
    public static final String REWIND = "recul";
    public static final String FORWARD = "avance";
    public static final String STOP = "arret";
    private static final String PLAY = "liste";

    private static final String CHANNEL_ID = "ecoute";
    private static final int NOTIFICATION_ID = 3;
    private static final int REWIND_MS = 10_000;
    private static final int FORWARD_MS = 30_000;
    /** « Précédente » après 5 s de lecture : retour au début de la piste. */
    private static final int RESTART_MS = 5_000;
    /** Erreurs de lecture en ligne tolérées sur une piste (nouvelle adresse à chaque fois). */
    private static final int MAX_FAILURES = 2;
    /** Fin « annoncée » plus de 10 s avant la vraie fin d'une piste en ligne : coupure. */
    private static final int END_MARGIN_MS = 10_000;
    /** Le temps qu'Aske trouve une adresse, écran éteint. */
    private static final long WAIT_LOCK_MS = 60_000;
    private static final AudioAttributes ATTRIBUTES = new AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
            .build();

    // Liste confiée par Aske. uris : content://, file://, https:// ou "" (adresse à trouver) ;
    // headers : en-têtes HTTP à envoyer avec une adresse https (en JSON). starts : où
    // (re)prendre chaque piste, en ms ; mis à jour quand on quitte une piste, pour y revenir
    // au même endroit.
    private static String[] uris = new String[0];
    private static String[] headers;
    private static String[] titles;
    private static String[] artists;
    private static String[] artworks;
    private static int[] starts;
    private static int index;
    private static AudioService instance;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private MediaPlayer player;
    private boolean prepared;
    /** Lecture demandée : la piste joue, ou jouera dès qu'elle sera prête. */
    private boolean playing;
    /** Mis en pause par un appel ou une autre application : reprendre ensuite. */
    private boolean resumeOnFocus;
    /** Piste en ligne dont on attend l'adresse (provide). */
    private boolean waiting;
    /** Lecture en ligne arrêtée le temps de recevoir la suite. */
    private boolean buffering;
    /** Erreurs de lecture en ligne sur la piste en cours. */
    private int failures;
    private WifiManager.WifiLock wifiLock;
    private PowerManager.WakeLock waitLock;
    private boolean noisyRegistered;
    private boolean foreground;
    private Bitmap artwork;
    private MediaSession session;
    private AudioManager audioManager;
    private AudioFocusRequest focusRequest;

    // --- Commandes d'Aske (fil principal d'Android) ------------------------------------------

    /** Lit la liste à partir de la piste n° first. */
    public static void play(Context context, String[] uris, String[] headers, String[] titles,
                            String[] artists, String[] artworks, int[] starts, int first) {
        if (instance != null) {
            // L'ancienne piste s'arrête avant que la liste change : ses relevés ne se
            // mélangent pas avec ceux de la nouvelle.
            instance.releasePlayer();
            instance.failures = 0;
        }
        AudioService.uris = uris;
        AudioService.headers = headers;
        AudioService.titles = titles;
        AudioService.artists = artists;
        AudioService.artworks = artworks;
        AudioService.starts = starts;
        AudioService.index = first;
        if (instance != null) {
            instance.load(first, true);
            return;
        }
        Intent intent = new Intent(context, AudioService.class).setAction(PLAY);
        if (Build.VERSION.SDK_INT >= 26) {
            context.startForegroundService(intent);
        } else {
            context.startService(intent);
        }
    }

    /** TOGGLE, PAUSE, NEXT, PREVIOUS, REWIND, FORWARD ou STOP. */
    public static void command(String action) {
        if (instance != null) {
            instance.handle(action);
        }
    }

    public static void seekTo(int ms) {
        if (instance != null) {
            instance.seek(ms);
        }
    }

    public static void jump(int track) {
        if (instance != null && track >= 0 && track < uris.length) {
            instance.load(track, true);
        }
    }

    /**
     * Adresse en ligne de la piste n° track, trouvée par Aske, avec ses en-têtes HTTP (JSON) et
     * sa pochette. Adresse vide : la piste est illisible (vidéo retirée, pas de connexion…).
     */
    public static void provide(int track, String uri, String header, String artwork) {
        if (track < 0 || track >= uris.length) {
            return;
        }
        boolean current = instance != null && track == index && instance.waiting;
        if (uri == null || uri.isEmpty()) {
            if (current) {
                instance.skipBroken();
            }
            return;
        }
        uris[track] = uri;
        headers[track] = header;
        if (artwork != null && !artwork.isEmpty()) {
            artworks[track] = artwork;
        }
        if (current) {
            instance.load(track, instance.playing);
        }
    }

    /** Piste dont le service attend l'adresse (provide), ou -1. */
    public static int getWaiting() {
        return instance != null && instance.waiting ? index : -1;
    }

    /** Lecture demandée mais pas encore de son : adresse, préparation ou réception en cours. */
    public static boolean isLoading() {
        return instance != null && instance.playing
                && (instance.waiting || instance.buffering || !instance.prepared);
    }

    public static boolean isActive() {
        return instance != null;
    }

    public static boolean isPlaying() {
        return instance != null && instance.playing;
    }

    public static int getIndex() {
        return index;
    }

    /** Position dans la piste, en ms (0 tant qu'elle se prépare). */
    public static int getPosition() {
        return instance != null ? instance.position() : 0;
    }

    /** Durée de la piste, en ms (0 si elle n'est pas encore connue). */
    public static int getDuration() {
        return instance != null && instance.prepared ? instance.player.getDuration() : 0;
    }

    // --- Service --------------------------------------------------------------------------

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent != null ? intent.getAction() : null;
        if (instance == null && !PLAY.equals(action)) {
            // Bouton d'une ancienne notification : il n'y a plus rien à lire.
            stopSelf();
            return START_NOT_STICKY;
        }
        instance = this;
        if (session == null) {
            audioManager = (AudioManager) getSystemService(AUDIO_SERVICE);
            session = new MediaSession(this, "Aske");
            session.setCallback(new SessionCallback());
            session.setSessionActivity(openAske());
            session.setActive(true);
        }
        // Android exige la notification dans les 5 s qui suivent le démarrage du service. Une
        // seule fois : Android 12 et plus le refuserait quand Aske n'est plus affichée.
        if (!foreground) {
            if (Build.VERSION.SDK_INT >= 29) {
                startForeground(NOTIFICATION_ID, buildNotification(),
                        ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
            } else {
                startForeground(NOTIFICATION_ID, buildNotification());
            }
            foreground = true;
        }
        if (PLAY.equals(action)) {
            load(index, true);
        } else {
            handle(action);
        }
        return START_NOT_STICKY;
    }

    private void handle(String action) {
        if (TOGGLE.equals(action)) {
            if (playing) {
                pause();
                abandonFocus();
            } else {
                resume();
            }
        } else if (PAUSE.equals(action)) {
            if (playing) {
                pause();
                abandonFocus();
            }
        } else if (NEXT.equals(action)) {
            if (index + 1 < uris.length) {
                load(index + 1, true);
            }
        } else if (PREVIOUS.equals(action)) {
            if (index == 0 || position() > RESTART_MS) {
                seek(0);
            } else {
                load(index - 1, true);
            }
        } else if (REWIND.equals(action)) {
            seek(position() - REWIND_MS);
        } else if (FORWARD.equals(action)) {
            seek(position() + FORWARD_MS);
        } else if (STOP.equals(action)) {
            finish();
        }
    }

    // --- Lecture --------------------------------------------------------------------------

    /** Prépare la piste n° track, et la lance si play. */
    private void load(int track, boolean play) {
        releasePlayer();
        if (track != index) {
            failures = 0;
        }
        index = track;
        playing = play;
        waiting = false;
        buffering = false;
        artwork = loadArtwork(artworks[track]);
        if (uris[track].isEmpty()) {
            // Piste en ligne : Aske cherche son adresse (provide). Processeur et Wi-Fi restent
            // actifs pendant ce temps, même écran éteint.
            waiting = true;
            holdWakeLock();
            holdNetwork(true);
            refresh();
            return;
        }
        releaseWakeLock();
        holdNetwork(playing && isOnline());
        final MediaPlayer mediaPlayer = new MediaPlayer();
        player = mediaPlayer;
        mediaPlayer.setAudioAttributes(ATTRIBUTES);
        // Garde le processeur actif écran éteint, pendant la lecture seulement.
        mediaPlayer.setWakeMode(this, PowerManager.PARTIAL_WAKE_LOCK);
        mediaPlayer.setOnPreparedListener(mp -> {
            prepared = true;
            if (starts[index] > 0 && starts[index] < mp.getDuration()) {
                mp.seekTo(starts[index]);
            }
            if (playing) {
                resume();
            } else {
                refresh();
            }
        });
        mediaPlayer.setOnCompletionListener(mp -> {
            if (isOnline() && mp.getCurrentPosition() < mp.getDuration() - END_MARGIN_MS) {
                // Lecture en ligne coupée bien avant la fin (réseau perdu) : on reprend ici.
                failed();
                return;
            }
            // Écoutée jusqu'au bout : elle repartira du début (releasePlayer ne garde pas la fin).
            prepared = false;
            starts[index] = 0;
            if (index + 1 < uris.length) {
                load(index + 1, true);
            } else {
                finish();
            }
        });
        mediaPlayer.setOnErrorListener((mp, what, extra) -> {
            failed();
            return true;
        });
        // Lecture en ligne : le son s'interrompt le temps de recevoir la suite.
        mediaPlayer.setOnInfoListener((mp, what, extra) -> {
            if (what == MediaPlayer.MEDIA_INFO_BUFFERING_START
                    || what == MediaPlayer.MEDIA_INFO_BUFFERING_END) {
                buffering = what == MediaPlayer.MEDIA_INFO_BUFFERING_START;
                refresh();
            }
            return false;
        });
        try {
            mediaPlayer.setDataSource(this, Uri.parse(uris[track]), headersOf(headers[track]));
            mediaPlayer.prepareAsync();
        } catch (Exception error) {
            handler.post(() -> {
                if (player == mediaPlayer) {  // pas déjà passé à une autre piste
                    failed();
                }
            });
        }
        refresh();
    }

    /** La piste ne se lit pas (ou plus). */
    private void failed() {
        if (isOnline() && failures < MAX_FAILURES) {
            // Adresse expirée ou coupure du réseau : Aske en cherche une nouvelle, et la
            // lecture reprendra au même endroit (releasePlayer garde la position).
            failures++;
            releasePlayer();
            uris[index] = "";
            load(index, playing);
        } else {
            // Fichier supprimé depuis une autre application, vidéo retirée… : piste suivante.
            skipBroken();
        }
    }

    private boolean isOnline() {
        return uris[index].startsWith("http");
    }

    private void skipBroken() {
        if (index + 1 < uris.length) {
            load(index + 1, playing);
        } else {
            finish();
        }
    }

    private void resume() {
        // Priorité refusée (appel en cours…) : on reste en pause.
        playing = !prepared || requestFocus();
        holdNetwork(playing && (waiting || isOnline()));
        if (prepared && playing) {
            player.start();
            if (!noisyRegistered) {
                registerReceiver(noisy, new IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY));
                noisyRegistered = true;
            }
        }
        refresh();
    }

    private void pause() {
        playing = false;
        if (prepared && player.isPlaying()) {
            player.pause();
        }
        unregisterNoisy();
        holdNetwork(waiting);
        refresh();
    }

    private void seek(int ms) {
        if (prepared) {
            player.seekTo(Math.max(0, Math.min(ms, player.getDuration() - 1000)));
            refresh();
        }
    }

    private int position() {
        return prepared ? player.getCurrentPosition() : 0;
    }

    private void releasePlayer() {
        if (player != null) {
            // Pour revenir à cet endroit si l'on revient à cette piste.
            if (prepared && index < starts.length) {
                starts[index] = player.getCurrentPosition();
            }
            player.release();
            player = null;
        }
        prepared = false;
        unregisterNoisy();
    }

    /** Fin de la liste, ou arrêt demandé : le service s'arrête et sa notification disparaît. */
    private void finish() {
        releasePlayer();
        playing = false;
        waiting = false;
        holdNetwork(false);
        releaseWakeLock();
        abandonFocus();
        instance = null;
        uris = new String[0];
        if (session != null) {
            session.setActive(false);
            session.release();
            session = null;
        }
        stopForeground(true);
        foreground = false;
        stopSelf();
    }

    @Override
    public void onDestroy() {
        if (instance == this) {
            finish();
        }
        super.onDestroy();
    }

    /** Wi-Fi actif écran éteint, pendant la lecture en ligne. */
    private void holdNetwork(boolean hold) {
        if (hold) {
            if (wifiLock == null) {
                WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE);
                wifiLock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "aske:ecoute");
                wifiLock.setReferenceCounted(false);
            }
            wifiLock.acquire();
        } else if (wifiLock != null && wifiLock.isHeld()) {
            wifiLock.release();
        }
    }

    /** Processeur actif le temps qu'Aske trouve une adresse (au plus une minute). */
    private void holdWakeLock() {
        if (waitLock == null) {
            PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
            waitLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "aske:adresse");
            waitLock.setReferenceCounted(false);
        }
        waitLock.acquire(WAIT_LOCK_MS);
    }

    private void releaseWakeLock() {
        if (waitLock != null && waitLock.isHeld()) {
            waitLock.release();
        }
    }

    /** En-têtes HTTP donnés par Aske en JSON ({"User-Agent": …}), ou null. */
    private static Map<String, String> headersOf(String json) {
        if (json == null || json.isEmpty()) {
            return null;
        }
        Map<String, String> map = new HashMap<>();
        try {
            JSONObject object = new JSONObject(json);
            for (Iterator<String> keys = object.keys(); keys.hasNext(); ) {
                String key = keys.next();
                map.put(key, object.getString(key));
            }
        } catch (JSONException error) {
            return null;
        }
        return map;
    }

    // --- Priorité du son (appels, autres applications) et débranchement du casque ----------

    private final AudioManager.OnAudioFocusChangeListener focusListener = change -> {
        if (change == AudioManager.AUDIOFOCUS_LOSS) {
            // Une autre application joue (vidéo dans Chrome…) : pause jusqu'à nouvel ordre.
            resumeOnFocus = false;
            pause();
            abandonFocus();
        } else if (change == AudioManager.AUDIOFOCUS_LOSS_TRANSIENT) {
            // Appel, message vocal… : on reprendra ensuite.
            resumeOnFocus = playing;
            pause();
        } else if (change == AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK) {
            // Notification sonore, GPS… : le son baisse (Android 8 et plus le fait tout seul).
            if (prepared) {
                player.setVolume(0.2f, 0.2f);
            }
        } else if (change == AudioManager.AUDIOFOCUS_GAIN) {
            if (prepared) {
                player.setVolume(1f, 1f);
            }
            if (resumeOnFocus) {
                resumeOnFocus = false;
                resume();
            }
        }
    };

    private boolean requestFocus() {
        int result;
        if (Build.VERSION.SDK_INT >= 26) {
            if (focusRequest == null) {
                focusRequest = new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
                        .setAudioAttributes(ATTRIBUTES)
                        .setOnAudioFocusChangeListener(focusListener, handler)
                        .build();
            }
            result = audioManager.requestAudioFocus(focusRequest);
        } else {
            result = audioManager.requestAudioFocus(
                    focusListener, AudioManager.STREAM_MUSIC, AudioManager.AUDIOFOCUS_GAIN);
        }
        return result == AudioManager.AUDIOFOCUS_REQUEST_GRANTED;
    }

    private void abandonFocus() {
        if (audioManager == null) {
            return;
        }
        if (Build.VERSION.SDK_INT >= 26) {
            if (focusRequest != null) {
                audioManager.abandonAudioFocusRequest(focusRequest);
            }
        } else {
            audioManager.abandonAudioFocus(focusListener);
        }
    }

    /** Casque débranché (ou Bluetooth coupé) : pause, pour ne pas jouer sur le haut-parleur. */
    private final BroadcastReceiver noisy = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            pause();
        }
    };

    private void unregisterNoisy() {
        if (noisyRegistered) {
            unregisterReceiver(noisy);
            noisyRegistered = false;
        }
    }

    // --- Notification, écran de verrouillage, boutons du casque -----------------------------

    private class SessionCallback extends MediaSession.Callback {
        @Override
        public void onPlay() {
            resume();
        }

        @Override
        public void onPause() {
            pause();
            abandonFocus();
        }

        @Override
        public void onSkipToNext() {
            handle(NEXT);
        }

        @Override
        public void onSkipToPrevious() {
            handle(PREVIOUS);
        }

        @Override
        public void onFastForward() {
            handle(FORWARD);
        }

        @Override
        public void onRewind() {
            handle(REWIND);
        }

        @Override
        public void onSeekTo(long ms) {
            seek((int) ms);
        }

        @Override
        public void onStop() {
            finish();
        }

        @Override
        public void onCustomAction(String action, android.os.Bundle extras) {
            handle(action);
        }
    }

    /** Met à jour la notification et ce que montrent l'écran de verrouillage et Android. */
    private void refresh() {
        if (session == null || instance != this) {
            return;
        }
        long actions = PlaybackState.ACTION_PLAY | PlaybackState.ACTION_PAUSE
                | PlaybackState.ACTION_PLAY_PAUSE | PlaybackState.ACTION_SKIP_TO_PREVIOUS
                | PlaybackState.ACTION_REWIND | PlaybackState.ACTION_FAST_FORWARD
                | PlaybackState.ACTION_SEEK_TO | PlaybackState.ACTION_STOP;
        if (index + 1 < uris.length) {
            actions |= PlaybackState.ACTION_SKIP_TO_NEXT;
        }
        boolean sounding = prepared && !buffering && !waiting;
        int state = !playing ? PlaybackState.STATE_PAUSED
                : sounding ? PlaybackState.STATE_PLAYING : PlaybackState.STATE_BUFFERING;
        session.setPlaybackState(new PlaybackState.Builder()
                .setActions(actions)
                .setState(state, position(), playing && sounding ? 1f : 0f,
                        SystemClock.elapsedRealtime())
                // Android 13 et plus : bouton « Arrêter » dans les commandes du son.
                .addCustomAction(new PlaybackState.CustomAction.Builder(
                        STOP, "Arrêter", android.R.drawable.ic_menu_close_clear_cancel).build())
                .build());
        session.setMetadata(new MediaMetadata.Builder()
                .putString(MediaMetadata.METADATA_KEY_TITLE, titles[index])
                .putString(MediaMetadata.METADATA_KEY_ARTIST, artists[index])
                .putLong(MediaMetadata.METADATA_KEY_DURATION, getDuration())
                .putBitmap(MediaMetadata.METADATA_KEY_ALBUM_ART, artwork)
                .build());
        NotificationManager manager =
                (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        manager.notify(NOTIFICATION_ID, buildNotification());
    }

    private Notification buildNotification() {
        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationManager manager = getSystemService(NotificationManager.class);
            manager.createNotificationChannel(new NotificationChannel(
                    CHANNEL_ID, "Écoute", NotificationManager.IMPORTANCE_LOW));
            builder = new Notification.Builder(this, CHANNEL_ID);
        } else {
            builder = new Notification.Builder(this);
        }
        boolean hasTrack = index < uris.length;
        builder.setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle(hasTrack ? titles[index] : "Aske")
                .setContentText(hasTrack ? artists[index] : "")
                .setSubText(uris.length > 1 ? (index + 1) + " sur " + uris.length : null)
                .setLargeIcon(artwork)
                .setContentIntent(openAske())
                .setDeleteIntent(action(STOP, 4))
                .setOngoing(playing)
                .setShowWhen(false)
                .setOnlyAlertOnce(true)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .addAction(button(android.R.drawable.ic_media_previous, "Précédente", PREVIOUS, 0))
                .addAction(playing
                        ? button(android.R.drawable.ic_media_pause, "Pause", TOGGLE, 1)
                        : button(android.R.drawable.ic_media_play, "Lecture", TOGGLE, 1))
                .addAction(button(android.R.drawable.ic_media_next, "Suivante", NEXT, 2))
                .addAction(button(android.R.drawable.ic_menu_close_clear_cancel, "Arrêter", STOP, 3));
        if (session != null) {
            builder.setStyle(new Notification.MediaStyle()
                    .setMediaSession(session.getSessionToken())
                    .setShowActionsInCompactView(0, 1, 2));
        }
        return builder.build();
    }

    private Notification.Action button(int icon, String label, String action, int code) {
        return new Notification.Action.Builder(
                Icon.createWithResource(this, icon), label, action(action, code)).build();
    }

    private PendingIntent action(String action, int code) {
        Intent intent = new Intent(this, AudioService.class).setAction(action);
        return PendingIntent.getService(this, code, intent,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    /** Un appui sur la notification ramène Aske au premier plan, comme son icône. */
    private PendingIntent openAske() {
        Intent open = new Intent(this, PythonActivity.class)
                .setAction(Intent.ACTION_MAIN)
                .addCategory(Intent.CATEGORY_LAUNCHER)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
        return PendingIntent.getActivity(
                this, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    /** Miniature de la vidéo (4:3, avec bandes noires) recadrée en 16:9, ou null. */
    private static Bitmap loadArtwork(String path) {
        if (path == null || path.isEmpty()) {
            return null;
        }
        Bitmap bitmap = BitmapFactory.decodeFile(path);
        if (bitmap == null) {
            return null;
        }
        int width = bitmap.getWidth();
        int height = width * 9 / 16;
        if (height >= bitmap.getHeight()) {
            return bitmap;
        }
        return Bitmap.createBitmap(bitmap, 0, (bitmap.getHeight() - height) / 2, width, height);
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
