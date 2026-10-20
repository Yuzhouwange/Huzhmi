package com.huzhmi.app;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Handler;
import android.os.Looper;
import android.widget.ImageView;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

final class ImageLoader {
    private static final int MAX = 80;
    private static final Map<String, Bitmap> CACHE =
            new LinkedHashMap<String, Bitmap>(MAX, 0.75f, true) {
                @Override
                protected boolean removeEldestEntry(Map.Entry<String, Bitmap> eldest) {
                    return size() > MAX;
                }
            };
    private static final ExecutorService POOL = Executors.newFixedThreadPool(3);
    private static final Handler MAIN = new Handler(Looper.getMainLooper());

    static void load(ImageView view, String url, String referer, String cookie) {
        Bitmap cached = CACHE.get(url);
        if (cached != null) {
            view.setImageBitmap(cached);
            return;
        }
        view.setImageDrawable(null);
        POOL.execute(() -> {
            Bitmap bitmap = fetch(url, referer, cookie);
            if (bitmap != null) {
                synchronized (CACHE) {
                    CACHE.put(url, bitmap);
                }
                MAIN.post(() -> view.setImageBitmap(bitmap));
            }
        });
    }

    private static Bitmap fetch(String address, String referer, String cookie) {
        try {
            HttpURLConnection conn = (HttpURLConnection) new URL(address).openConnection();
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(25000);
            conn.setRequestProperty("User-Agent",
                    referer != null && referer.contains("douyin")
                            ? Http.MOBILE_UA
                            : referer != null && referer.contains("tiktok")
                            ? Http.TIKTOK_UA : Http.DESKTOP_UA);
            conn.setRequestProperty("Referer", referer == null ? "" : referer);
            conn.setRequestProperty("Accept", "image/*");
            if (cookie != null && !cookie.isEmpty()) {
                conn.setRequestProperty("Cookie", cookie);
            }
            if (conn.getResponseCode() >= 400) {
                return null;
            }
            try (InputStream input = conn.getInputStream();
                 ByteArrayOutputStream out = new ByteArrayOutputStream()) {
                byte[] buffer = new byte[32768];
                int read;
                while ((read = input.read(buffer)) != -1) {
                    out.write(buffer, 0, read);
                    if (out.size() > 12 * 1024 * 1024) {
                        return null;
                    }
                }
                byte[] data = out.toByteArray();
                BitmapFactory.Options bounds = new BitmapFactory.Options();
                bounds.inJustDecodeBounds = true;
                BitmapFactory.decodeByteArray(data, 0, data.length, bounds);
                BitmapFactory.Options options = new BitmapFactory.Options();
                options.inSampleSize = 1;
                int edge = Math.max(bounds.outWidth, bounds.outHeight);
                while (edge / options.inSampleSize > 480) {
                    options.inSampleSize *= 2;
                }
                return BitmapFactory.decodeByteArray(data, 0, data.length, options);
            }
        } catch (Exception ignored) {
            return null;
        }
    }

    private ImageLoader() {
    }
}
