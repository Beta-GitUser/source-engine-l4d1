package com.valvesoftware;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.RandomAccessFile;

/**
 * Debug shell for the L4D1 port.
 *
 * The point of this activity is to answer, on the device, the questions that
 * cannot be answered anywhere else: will bionic load our modules, do they
 * resolve their dependencies, and does CreateInterface hand back the versions
 * this SDK advertises?
 *
 * It exists because an unrooted phone cannot run a .so by itself - the dynamic
 * linker only maps libraries into a process it creates - so an installed package
 * is the only carrier.
 *
 * Two design decisions worth knowing:
 *
 *  - every step is written and flushed to a file before it runs, and the file is
 *    shown on the next launch. Without adb there is no logcat, and a probe that
 *    dies silently is worse than no probe.
 *  - the native surface is deliberately tiny (load a library, ask it for an
 *    interface, report the last dlopen/dlsym error), so a crash inside the
 *    engine cannot take the reporting machinery with it.
 */
public class ValveActivity2 extends Activity {
    private static final String TAG = "L4D1Debug";
    private static final String LOG_FILE = "probe.log";

    /** Libraries to load, in dependency order. Missing ones are not fatal. */
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

    /** Interfaces to ask each module for. Both our SDK's and retail's versions. */
    private static final String[][] INTERFACES = {
        {"libclient.so", "VClient017", "VClient016"},
        {"libserver.so", "ServerGameDLL010", "ServerGameDLL009", "ServerGameDLL005"},
        {"libserver.so", "ServerGameClients004", "ServerGameClients003"},
    };

    private TextView output;
    private final StringBuilder transcript = new StringBuilder();
    private File logFile;

    // Implemented in launcher/android/main.cpp.
    private static native int setenv(String name, String value, int overwrite);
    private static native void setArgs(String args);
    private static native void nativeOnActivityResult();

    private static native long nativeLoadLibrary(String path);
    private static native long nativeCreateInterface(long handle, String name);
    private static native String nativeLastError();

    /** False when liblauncher.so itself would not load, so the probe is skipped. */
    private static boolean nativeReady;
    private static String nativeLoadError;

    static {
        // Loaded by the platform from the APK's lib/ directory; the module under
        // test is loaded by path in nativeLoadLibrary.
        //
        // The try/catch is not defensive habit: without adb there is no logcat,
        // so an UnsatisfiedLinkError escaping class initialisation becomes a
        // "keeps stopping" dialog with no explanation. Caught here, the message
        // - which names the dependency that failed to resolve - lands on screen.
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
        output.setGravity(Gravity.START);
        output.setPadding(24, 24, 24, 24);
        output.setTextIsSelectable(true);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(output);
        setContentView(scroll);

        logFile = new File(getFilesDir(), LOG_FILE);

        StringBuilder start = new StringBuilder();
        String previous = readPreviousLog();
        if (previous != null && previous.length() > 0) {
            start.append("=== previous run (ended ");
            start.append(previous.endsWith("\n") ? "abruptly" : "cleanly");
            start.append(") ===\n");
            start.append(previous);
            start.append("\n");
        }
        start.append("=== new run ===\n");
        transcript.append(start);
        writeLog();

        final String nativeDir = getApplicationInfo().nativeLibraryDir;
        log("nativeLibraryDir: " + nativeDir);
        // Print what is actually packaged. A library name that does not match
        // LIBRARIES is then obvious from the transcript instead of showing up as
        // a silent "skip".
        String[] packaged = new File(nativeDir).list();
        if (packaged != null) {
            java.util.Arrays.sort(packaged);
            for (String name : packaged) {
                log("  packaged: " + name);
            }
        }
        log("filesDir: " + getFilesDir());
        log("device: " + android.os.Build.MODEL + " / " + android.os.Build.SUPPORTED_ABIS[0]
                + " / API " + android.os.Build.VERSION.SDK_INT);

        if (!nativeReady) {
            log("");
            log("FAIL  liblauncher.so did not load:");
            log("      " + nativeLoadError);
            log("");
            log("Nothing else can be tested in this process. The message above");
            log("names what the linker could not resolve: usually a library that");
            log("was not packaged (check the 'packaged:' lines) or a dependency of");
            log("liblauncher.so that did not get built for aarch64.");
            writeLog();
            return;
        }

        new Thread(new Runnable() {
            @Override
            public void run() {
                probe(nativeDir);
                new Handler(Looper.getMainLooper()).post(new Runnable() {
                    @Override
                    public void run() {
                        log("=== done ===");
                        writeLog();
                    }
                });
            }
        }).start();
    }

    private void probe(String nativeDir) {
        log("");
        log("--- loading modules ---");
        for (String library : LIBRARIES) {
            File file = new File(nativeDir, library);
            if (!file.exists()) {
                log("skip  " + library + " (not packaged)");
                continue;
            }
            long handle = nativeLoadLibrary(file.getAbsolutePath());
            if (handle == 0) {
                log("FAIL  " + library + ": " + nativeLastError());
            } else {
                log("ok    " + library);
            }
        }

        log("");
        log("--- CreateInterface ---");
        for (String[] request : INTERFACES) {
            String library = request[0];
            File file = new File(nativeDir, library);
            if (!file.exists()) {
                continue;
            }
            // Re-open rather than caching handles in Java: the handle is just a
            // number to this side, and dlopen is reference counted.
            long handle = nativeLoadLibrary(file.getAbsolutePath());
            if (handle == 0) {
                continue;
            }
            for (int i = 1; i < request.length; i++) {
                long pointer = nativeCreateInterface(handle, request[i]);
                if (pointer != 0) {
                    log("ok    " + library + " -> " + request[i]);
                } else {
                    log("none  " + library + " -> " + request[i]);
                }
            }
        }

        log("");
        log("If every module loaded and an interface came back, the port's");
        log("foundation works on this device: bionic loaded our aarch64 code,");
        log("its dependencies resolved, and the factory exported.");
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

    /** Truncate-and-write, so a crash leaves the last completed step on disk. */
    private synchronized void writeLog() {
        FileOutputStream stream = null;
        try {
            stream = new FileOutputStream(logFile, false);
            stream.write(transcript.toString().getBytes("UTF-8"));
            stream.flush();
        } catch (IOException ignored) {
            // Nothing useful to do: the screen still has the transcript.
        } finally {
            if (stream != null) {
                try {
                    stream.close();
                } catch (IOException ignored) {
                }
            }
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
