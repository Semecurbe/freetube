package fr.perso.freetube;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

import org.kivy.android.PythonActivity;

/**
 * Service « au premier plan » actif pendant les téléchargements.
 *
 * Comme MediaService pour la lecture, il empêche Android d'endormir Aske écran éteint :
 * un long livre audio peut se télécharger téléphone posé. Sa notification montre
 * l'avancement (mise à jour par update, sans relancer le service).
 */
public class DownloadService extends Service {
    private static final String CHANNEL_ID = "telechargements";
    private static final int NOTIFICATION_ID = 2;
    private static final long MAX_LOCK_MS = 3 * 60 * 60 * 1000L;

    private PowerManager.WakeLock wakeLock;
    private WifiManager.WifiLock wifiLock;

    /** Démarre le service : à appeler depuis l'application affichée (règle d'Android 12). */
    public static void start(Context context, String title) {
        Intent intent = new Intent(context, DownloadService.class).putExtra("title", title);
        if (Build.VERSION.SDK_INT >= 26) {
            context.startForegroundService(intent);
        } else {
            context.startService(intent);
        }
    }

    /** Met à jour la notification : titre du téléchargement et pourcentage (0 : inconnu). */
    public static void update(Context context, String title, int percent) {
        NotificationManager manager =
                (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        manager.notify(NOTIFICATION_ID, buildNotification(context, title, percent));
    }

    public static void stop(Context context) {
        context.stopService(new Intent(context, DownloadService.class));
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String title = intent != null ? intent.getStringExtra("title") : null;
        Notification notification = buildNotification(this, title, 0);
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
        // Processeur et Wi-Fi restent actifs écran éteint, le temps des téléchargements.
        if (wakeLock == null) {
            PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
            wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "aske:telechargement");
            wakeLock.acquire(MAX_LOCK_MS);
            WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE);
            wifiLock = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "aske:telechargement");
            wifiLock.acquire();
        }
        return START_NOT_STICKY;
    }

    private static Notification buildNotification(Context context, String title, int percent) {
        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationManager manager = context.getSystemService(NotificationManager.class);
            manager.createNotificationChannel(new NotificationChannel(
                    CHANNEL_ID, "Téléchargements", NotificationManager.IMPORTANCE_LOW));
            builder = new Notification.Builder(context, CHANNEL_ID);
        } else {
            builder = new Notification.Builder(context);
        }
        // Un appui sur la notification ramène Aske au premier plan, comme son icône.
        Intent open = new Intent(context, PythonActivity.class)
                .setAction(Intent.ACTION_MAIN)
                .addCategory(Intent.CATEGORY_LAUNCHER)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
        PendingIntent pending = PendingIntent.getActivity(
                context, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        return builder
                .setSmallIcon(android.R.drawable.stat_sys_download)
                .setContentTitle("Téléchargement")
                .setContentText(title != null ? title : "")
                .setProgress(100, percent, percent <= 0)
                .setContentIntent(pending)
                .setOnlyAlertOnce(true)
                .setOngoing(true)
                .build();
    }

    @Override
    public void onDestroy() {
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }
        if (wifiLock != null && wifiLock.isHeld()) {
            wifiLock.release();
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
