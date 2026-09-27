package fr.perso.freetube;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.media.MediaScannerConnection;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * Les MP3 d'Aske, rangés dans le dossier Musique/Aske du téléphone.
 *
 * Ils y sont visibles des autres applications (lecteurs de musique, gestionnaire de fichiers)
 * et y restent si Aske est désinstallée. Depuis Android 10, ils passent par MediaStore, sans
 * autorisation ; avant, par leur chemin, avec l'autorisation WRITE_EXTERNAL_STORAGE.
 * Chaque MP3 est désigné par son adresse : content://… (MediaStore) ou file://….
 */
public class MusicStore {
    private static final String FOLDER = "Aske";

    /** Copie le fichier source dans Musique/Aske, sous le nom name ; renvoie son adresse. */
    public static String save(Context context, String source, String name) throws IOException {
        if (Build.VERSION.SDK_INT >= 29) {
            ContentResolver resolver = context.getContentResolver();
            ContentValues values = new ContentValues();
            values.put(MediaStore.Audio.Media.DISPLAY_NAME, name);
            values.put(MediaStore.Audio.Media.MIME_TYPE, "audio/mpeg");
            values.put(MediaStore.Audio.Media.RELATIVE_PATH, Environment.DIRECTORY_MUSIC + "/" + FOLDER);
            // Caché des autres applications tant que la copie n'est pas terminée.
            values.put(MediaStore.Audio.Media.IS_PENDING, 1);
            Uri uri = resolver.insert(
                    MediaStore.Audio.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY), values);
            if (uri == null) {
                throw new IOException("impossible de créer le fichier dans le dossier Musique");
            }
            try (InputStream in = new FileInputStream(source);
                 OutputStream out = resolver.openOutputStream(uri)) {
                if (out == null) {
                    throw new IOException("impossible d'écrire dans le dossier Musique");
                }
                copy(in, out);
            } catch (IOException | RuntimeException error) {
                resolver.delete(uri, null, null);
                throw error;
            }
            values.clear();
            values.put(MediaStore.Audio.Media.IS_PENDING, 0);
            resolver.update(uri, values, null, null);
            return uri.toString();
        }

        File folder = new File(
                Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_MUSIC), FOLDER);
        if (!folder.isDirectory() && !folder.mkdirs()) {
            throw new IOException("impossible de créer le dossier Musique/" + FOLDER);
        }
        File file = unused(folder, name);
        try (InputStream in = new FileInputStream(source);
             OutputStream out = new FileOutputStream(file)) {
            copy(in, out);
        } catch (IOException error) {
            file.delete();
            throw error;
        }
        // Signale le fichier aux lecteurs de musique.
        MediaScannerConnection.scanFile(
                context, new String[] {file.getPath()}, new String[] {"audio/mpeg"}, null);
        return Uri.fromFile(file).toString();
    }

    /** Le MP3 est-il toujours là ? (Il a pu être supprimé depuis une autre application.) */
    public static boolean exists(Context context, String address) {
        Uri uri = Uri.parse(address);
        if ("file".equals(uri.getScheme())) {
            return new File(uri.getPath()).isFile();
        }
        try (Cursor cursor = context.getContentResolver().query(
                uri, new String[] {MediaStore.MediaColumns._ID}, null, null, null)) {
            return cursor != null && cursor.moveToFirst();
        } catch (RuntimeException error) {
            return false;
        }
    }

    public static void delete(Context context, String address) {
        Uri uri = Uri.parse(address);
        if ("file".equals(uri.getScheme())) {
            File file = new File(uri.getPath());
            if (file.delete()) {
                MediaScannerConnection.scanFile(context, new String[] {file.getPath()}, null, null);
            }
            return;
        }
        try {
            context.getContentResolver().delete(uri, null, null);
        } catch (RuntimeException error) {
            // Déjà supprimé depuis une autre application : rien à faire.
        }
    }

    /** « Titre.mp3 », ou « Titre (2).mp3 » si le nom est déjà pris. */
    private static File unused(File folder, String name) {
        File file = new File(folder, name);
        int dot = name.lastIndexOf('.');
        String base = dot > 0 ? name.substring(0, dot) : name;
        String extension = dot > 0 ? name.substring(dot) : "";
        for (int number = 2; file.exists(); number++) {
            file = new File(folder, base + " (" + number + ")" + extension);
        }
        return file;
    }

    private static void copy(InputStream in, OutputStream out) throws IOException {
        byte[] buffer = new byte[256 * 1024];
        int count;
        while ((count = in.read(buffer)) > 0) {
            out.write(buffer, 0, count);
        }
    }
}
