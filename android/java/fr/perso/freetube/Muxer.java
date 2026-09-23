package fr.perso.freetube;

import android.media.MediaCodec;
import android.media.MediaExtractor;
import android.media.MediaMuxer;

import java.io.IOException;
import java.nio.ByteBuffer;

/**
 * Assemble l'image et le son d'une vidéo en un seul fichier MP4, sans réencodage.
 *
 * YouTube ne fournit plus de fichier contenant les deux : Aske télécharge l'image (MP4 sans
 * son) et le son (M4A) séparément, puis les réunit ici avec les outils d'Android.
 */
public class Muxer {
    public static void mux(String videoPath, String audioPath, String outputPath) throws IOException {
        MediaExtractor video = open(videoPath);
        MediaExtractor audio = open(audioPath);
        MediaMuxer muxer = new MediaMuxer(outputPath, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);
        try {
            int videoTrack = muxer.addTrack(video.getTrackFormat(0));
            int audioTrack = muxer.addTrack(audio.getTrackFormat(0));
            muxer.start();
            ByteBuffer buffer = ByteBuffer.allocate(8 * 1024 * 1024);
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            boolean videoDone = false;
            boolean audioDone = false;
            while (!videoDone || !audioDone) {
                // Toujours l'échantillon le plus ancien des deux : image et son restent
                // entrelacés, sans tout garder en mémoire.
                boolean fromVideo = !videoDone
                        && (audioDone || video.getSampleTime() <= audio.getSampleTime());
                MediaExtractor source = fromVideo ? video : audio;
                int size = source.readSampleData(buffer, 0);
                if (size < 0) {
                    if (fromVideo) {
                        videoDone = true;
                    } else {
                        audioDone = true;
                    }
                    continue;
                }
                int flags = (source.getSampleFlags() & MediaExtractor.SAMPLE_FLAG_SYNC) != 0
                        ? MediaCodec.BUFFER_FLAG_KEY_FRAME : 0;
                info.set(0, size, source.getSampleTime(), flags);
                muxer.writeSampleData(fromVideo ? videoTrack : audioTrack, buffer, info);
                source.advance();
            }
            muxer.stop();
        } finally {
            muxer.release();
            video.release();
            audio.release();
        }
    }

    private static MediaExtractor open(String path) throws IOException {
        MediaExtractor extractor = new MediaExtractor();
        extractor.setDataSource(path);
        extractor.selectTrack(0);
        return extractor;
    }
}
