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
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;

import org.kivy.android.PythonActivity;

/**
 * Lecteur des MP3 : joue une liste de pistes à la suite, en arrière-plan.
 *
 * Contrairement aux vidéos (lues par une WebView, dans l'écran d'Aske), le son continue quand
 * on passe à une autre application ou qu'on éteint l'écran. La notification, l'écran de
 * verrouillage et les boutons d'un casque le commandent (MediaSession).
 *
 * Aske (audio.py) lui confie la liste avec play, le commande avec command, seekTo et jump, et
 * relève où il en est avec les get… : tout cela depuis le fil principal d'Android, celui du
 * service. L'enchaînement des pistes se fait ici, sans attendre Aske (en arrière-plan, Kivy
 * est à l'arrêt).
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
    private static final AudioAttributes ATTRIBUTES = new AudioAttributes.Builder()
            .setUsage(AudioAttributes.USAGE_MEDIA)
            .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
            .build();

    // Liste confiée par Aske. starts : où (re)prendre chaque piste, en ms ; mis à jour quand
    // on quitte une piste, pour y revenir au même endroit.
    private static String[] uris = new String[0];
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
    private boolean noisyRegistered;
    private boolean foreground;
    private Bitmap artwork;
    private MediaSession session;
    private AudioManager audioManager;
    private AudioFocusRequest focusRequest;

    // --- Commandes d'Aske (fil principal d'Android) ------------------------------------------

    /** Lit la liste à partir de la piste n° first. */
    public static void play(Context context, String[] uris, String[] titles, String[] artists,
                            String[] artworks, int[] starts, int first) {
        if (instance != null) {
            // L'ancienne piste s'arrête avant que la liste change : ses relevés ne se
            // mélangent pas avec ceux de la nouvelle.
            instance.releasePlayer();
        }
        AudioService.uris = uris;
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
        index = track;
        playing = play;
        artwork = loadArtwork(artworks[track]);
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
            // Écoutée jusqu'au bout : elle repartira du début (releasePlayer ne garde pas la fin).
            prepared = false;
            starts[index] = 0;
            if (index + 1 < uris.length) {
                load(index + 1, true);
            } else {
                finish();
            }
        });
        // Fichier supprimé depuis une autre application, ou illisible : on passe au suivant.
        mediaPlayer.setOnErrorListener((mp, what, extra) -> {
            skipBroken();
            return true;
        });
        try {
            mediaPlayer.setDataSource(this, Uri.parse(uris[track]));
            mediaPlayer.prepareAsync();
        } catch (Exception error) {
            handler.post(this::skipBroken);
        }
        refresh();
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
        int state = !playing ? PlaybackState.STATE_PAUSED
                : prepared ? PlaybackState.STATE_PLAYING : PlaybackState.STATE_BUFFERING;
        session.setPlaybackState(new PlaybackState.Builder()
                .setActions(actions)
                .setState(state, position(), playing && prepared ? 1f : 0f,
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
