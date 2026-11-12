package com.valvesoftware;

import android.app.Activity;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ContentValues;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.io.RandomAccessFile;

/**
 * Debug shell for the port: load the engine modules and report how far it gets.
 *
 * Three constraints shaped this:
 *
 *  - An unrooted phone cannot run a bare .so; the linker only maps libraries
 *    into a process it creates. So the package is the carrier.
 *
 *  - Loading the modules on a device where the engine has never run is likely to
 *    segfault inside a static initialiser. That is the interesting result, and a
 *    crash in this process would destroy it. Each probe therefore runs in a forked
 *    child (see nativeProbeLibrary in launcher/android/main.cpp) and comes back as
 *    a verdict - "ok", "CRASHED in dlopen: SIGSEGV (11)", "failed ..." - while
 *    this process lives to print it.
 *
 *  - There is no adb and no readable logcat, and the app's internal files are
 *    unreachable from a file manager. So the transcript is on screen, copyable,
 *    shareable, and mirrored to Documents/l4d1-probe.txt through MediaStore -
 *    which needs no storage permission on Android 10+.
 */
public class ValveActivity2 extends Activity {
    private static final String TAG = "L4D1Debug";

    /** Kept for replaying the previous session. Unreachable from a file manager. */
    private static final String LOG_FILE = "probe.log";

    /** The copy a human (or a file manager) can actually find. */
    private static final String PUBLIC_LOG_NAME = "l4d1-probe.txt";

    /** Library names to try. A name this build did not produce is skipped. */
    private static final String[] LIBRARIES = {
        "libtier0.so",
        "libtier1.so",
        "libvstdlib.so",
        "libmathlib.so",
        "libtier2.so",
        "libengine.so",
        "libmaterialsystem.so",
        "libvphysics.so",
        "libclient.so",
        "libserver.so",
    };

    /** Interfaces to ask for: this SDK's versions and retail's. */
    private static final String[][] INTERFACES = {
        {"libclient.so", "VClient017", "VClient016"},
        {"libserver.so", "ServerGameDLL010", "ServerGameDLL009", "ServerGameDLL005"},
        {"libserver.so", "ServerGameClients004", "ServerGameClients003"},
    };

    private TextView output;
    private final StringBuilder transcript = new StringBuilder();
    private File logFile;
    private String nativeDir;
    private volatile boolean probing;

    // Implemented in launcher/android/main.cpp.
    private static native int setenv(String name, String value, int overwrite);
    private static native void setArgs(String args);
    private static native void nativeOnActivityResult();
    private static native String nativeProbeLibrary(String path, String interfaceName);

    private static boolean nativeReady;
    private static String nativeLoadError;

    static {
        // Caught rather than left to escape: an UnsatisfiedLinkError out of a
        // static initialiser is a "keeps stopping" dialog with no explanation,
        // and the message names the dependency that failed to resolve.
        try {
            System.loadLibrary("launcher");
            nativeReady = true;
        } catch (Throwable failure) {
            nativeLoadError = failure.toString();
        }
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        output = new TextView(this);
        output.setBackgroundColor(Color.BLACK);
        output.setTextColor(Color.parseColor("#B0FFB0"));
        output.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11);
        output.setTypeface(Typeface.MONOSPACE);
        output.setGravity(Gravity.START);
        output.setPadding(24, 24, 24, 24);
        output.setTextIsSelectable(true);

        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setBackgroundColor(Color.BLACK);
        page.addView(buttonRow());

        ScrollView scroll = new ScrollView(this);
        scroll.addView(output);
        page.addView(scroll);
        setContentView(page);

        logFile = new File(getFilesDir(), LOG_FILE);
        nativeDir = getApplicationInfo().nativeLibraryDir;

        String previous = readPreviousLog();
        if (previous != null && previous.length() > 0) {
            transcript.append("=== previous run ===\n").append(previous).append("\n");
        }
        transcript.append("=== new run ===\n");
        writeLog();

        log("nativeLibraryDir: " + nativeDir);
        String[] packaged = new File(nativeDir).list();
        if (packaged != null) {
            java.util.Arrays.sort(packaged);
            for (String name : packaged) {
                log("  packaged: " + name);
            }
        }
        log("device: " + Build.MODEL + " / " + Build.SUPPORTED_ABIS[0]
                + " / API " + Build.VERSION.SDK_INT);

        if (!nativeReady) {
            log("");
            log("FAIL  liblauncher.so did not load:");
            log("      " + nativeLoadError);
            log("");
            log("Nothing can be probed in this process. The message names what the");
            log("linker could not resolve - usually a dependency that is not in the");
            log("packaged: list above.");
            finishRun();
            return;
        }

        startProbe();
    }

    private LinearLayout buttonRow() {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);

        Button share = new Button(this);
        share.setText("Share log");
        share.setOnClickListener(new android.view.View.OnClickListener() {
            @Override
            public void onClick(android.view.View view) {
                shareTranscript();
            }
        });
        row.addView(share);

        Button copy = new Button(this);
        copy.setText("Copy");
        copy.setOnClickListener(new android.view.View.OnClickListener() {
            @Override
            public void onClick(android.view.View view) {
                copyTranscript();
            }
        });
        row.addView(copy);

        Button again = new Button(this);
        again.setText("Probe again");
        again.setOnClickListener(new android.view.View.OnClickListener() {
            @Override
            public void onClick(android.view.View view) {
                startProbe();
            }
        });
        row.addView(again);

        return row;
    }

    private void shareTranscript() {
        android.content.Intent send = new android.content.Intent(android.content.Intent.ACTION_SEND);
        send.setType("text/plain");
        send.putExtra(android.content.Intent.EXTRA_SUBJECT, "L4D1 debug probe");
        send.putExtra(android.content.Intent.EXTRA_TEXT, transcript.toString());
        startActivity(android.content.Intent.createChooser(send, "Share the probe log"));
    }

    private void copyTranscript() {
        ClipboardManager clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
        if (clipboard != null) {
            clipboard.setPrimaryClip(ClipData.newPlainText("L4D1 probe", transcript.toString()));
            log("(transcript copied to the clipboard)");
        }
    }

    private void startProbe() {
        if (probing) {
            return;
        }
        probing = true;
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    runProbe();
                } catch (Throwable failure) {
                    log("the probe itself threw: " + failure);
                } finally {
                    finishRun();
                    probing = false;
                }
            }
        }).start();
    }

    private void finishRun() {
        log("");
        log("=== done ===");
        log("Share or copy this, or read Documents/" + PUBLIC_LOG_NAME + ".");
        writeLog();
        publishToDocuments();
    }

    private void runProbe() {
        log("");
        log("--- loading modules (each in a forked child) ---");
        int loaded = 0;
        for (String library : LIBRARIES) {
            File file = new File(nativeDir, library);
            if (!file.exists()) {
                log("skip   " + library + " (not packaged) - see the packaged: list");
                continue;
            }
            String verdict = nativeProbeLibrary(file.getAbsolutePath(), null);
            log(pad(library) + verdict);
            if (verdict != null && verdict.startsWith("ok")) {
                loaded++;
            }
        }

        log("");
        log("--- CreateInterface (still one child per question) ---");
        for (String[] request : INTERFACES) {
            File file = new File(nativeDir, request[0]);
            if (!file.exists()) {
                continue;
            }
            for (int i = 1; i < request.length; i++) {
                String verdict = nativeProbeLibrary(file.getAbsolutePath(), request[i]);
                log(pad(request[0] + " -> " + request[i]) + verdict);
            }
        }

        log("");
        log(loaded + " of " + LIBRARIES.length + " modules loaded.");
        log("A line saying CRASHED is a result, not a dead end: it names the");
        log("library, the phase, and the signal. That is the finding.");
    }

    private static String pad(String text) {
        StringBuilder padded = new StringBuilder(text);
        while (padded.length() < 34) {
            padded.append(' ');
        }
        return padded.toString();
    }

    private synchronized void log(String line) {
        Log.i(TAG, line);
        transcript.append(line).append('\n');
        final String text = transcript.toString();
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                output.setText(text);
            }
        });
        writeLog();
    }

    /** Truncate-and-write, so a kill leaves the last completed step on disk. */
    private synchronized void writeLog() {
        FileOutputStream stream = null;
        try {
            stream = new FileOutputStream(logFile, false);
            stream.write(transcript.toString().getBytes("UTF-8"));
            stream.flush();
        } catch (IOException ignored) {
            // The screen still has it, and Share does not touch the filesystem.
        } finally {
            if (stream != null) {
                try {
                    stream.close();
                } catch (IOException ignored) {
                }
            }
        }
    }

    /**
     * Mirror the transcript to Documents, where any file manager can open it.
     *
     * On Android 10+ this is a MediaStore insert, which needs no storage
     * permission: writing to a public directory by path would need
     * MANAGE_EXTERNAL_STORAGE, and no diagnostic is worth that permission.
     */
    private void publishToDocuments() {
        final String text = transcript.toString();

        if (Build.VERSION.SDK_INT >= 29) {
            Uri created = null;
            try {
                ContentValues values = new ContentValues();
                values.put(MediaStore.MediaColumns.DISPLAY_NAME, PUBLIC_LOG_NAME);
                values.put(MediaStore.MediaColumns.MIME_TYPE, "text/plain");
                values.put(MediaStore.MediaColumns.RELATIVE_PATH, Environment.DIRECTORY_DOCUMENTS);
                Uri collection = MediaStore.Files.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY);
                created = getContentResolver().insert(collection, values);
                if (created != null) {
                    OutputStream stream = getContentResolver().openOutputStream(created);
                    if (stream != null) {
                        stream.write(text.getBytes("UTF-8"));
                        stream.close();
                    }
                    log("(also written to Documents/" + PUBLIC_LOG_NAME + ")");
                    return;
                }
            } catch (Throwable failure) {
                log("(could not write to Documents: " + failure + ")");
                if (created != null) {
                    try {
                        getContentResolver().delete(created, null, null);
                    } catch (Throwable ignored) {
                    }
                }
            }
        }

        // Android 9 and older: the plain path works with WRITE_EXTERNAL_STORAGE,
        // declared in the manifest for exactly these versions.
        try {
            File documents = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOCUMENTS);
            if (!documents.exists() && !documents.mkdirs()) {
                throw new IOException("no Documents directory");
            }
            FileOutputStream stream = new FileOutputStream(new File(documents, PUBLIC_LOG_NAME));
            stream.write(text.getBytes("UTF-8"));
            stream.close();
            log("(also written to Documents/" + PUBLIC_LOG_NAME + ")");
        } catch (Throwable failure) {
            log("(could not write to Documents: " + failure + ")");
            log("(Share or Copy still work)");
        }
    }

    private String readPreviousLog() {
        RandomAccessFile file = null;
        try {
            file = new RandomAccessFile(logFile, "r");
            byte[] data = new byte[(int) Math.min(file.length(), 64 * 1024)];
            file.readFully(data);
            return new String(data, "UTF-8");
        } catch (IOException missing) {
            return null;
        } finally {
            if (file != null) {
                try {
                    file.close();
                } catch (IOException ignored) {
                }
            }
        }
    }
}
