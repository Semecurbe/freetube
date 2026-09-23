package fr.perso.freetube;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

import org.kivy.android.PythonActivity;

/**
 * Service « au premier plan » actif pendant la lecture d'une vidéo.
 *
 * Il empêche Android de mettre Aske en veille (processus arrêté, réseau coupé) quand
 * l'écran s'éteint : la vidéo continue de jouer. Il affiche une notification
 * qui ramène à l'application.
 */
public class MediaService extends Service {
    private static final String CHANNEL_ID = "lecture";
    private static final long MAX_WAKE_LOCK_MS = 4 * 60 * 60 * 1000L;

    private PowerManager.WakeLock wakeLock;

    public static void start(Context context, String title) {
        Intent intent = new Intent(context, MediaService.class).putExtra("title", title);
        if (Build.VERSION.SDK_INT >= 26) {
            context.startForegroundService(intent);
        } else {
            context.startService(intent);
        }
    }

    public static void stop(Context context) {
        context.stopService(new Intent(context, MediaService.class));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String title = intent != null ? intent.getStringExtra("title") : null;
        Notification notification = buildNotification(title != null ? title : "Lecture en cours");
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
        } else {
            startForeground(1, notification);
        }
        // Garde le processeur actif écran éteint, le temps que la vidéo se charge et se joue.
        if (wakeLock == null) {
            PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
            wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "aske:lecture");
            wakeLock.acquire(MAX_WAKE_LOCK_MS);
        }
        return START_NOT_STICKY;
    }

    private Notification buildNotification(String title) {
        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationManager manager = getSystemService(NotificationManager.class);
            manager.createNotificationChannel(new NotificationChannel(
                    CHANNEL_ID, "Lecture", NotificationManager.IMPORTANCE_LOW));
            builder = new Notification.Builder(this, CHANNEL_ID);
        } else {
            builder = new Notification.Builder(this);
        }
        // Un appui sur la notification ramène Aske au premier plan, comme son icône.
        Intent open = new Intent(this, PythonActivity.class)
                .setAction(Intent.ACTION_MAIN)
                .addCategory(Intent.CATEGORY_LAUNCHER)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
        PendingIntent pending = PendingIntent.getActivity(
                this, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return builder
                .setSmallIcon(android.R.drawable.ic_media_play)
                .setContentTitle("Aske")
                .setContentText(title)
                .setContentIntent(pending)
                .setOngoing(true)
                .build();
    }

    @Override
    public void onDestroy() {
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
