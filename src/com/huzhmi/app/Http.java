package com.huzhmi.app;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;

final class Http {
    static final String DESKTOP_UA =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    + "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36";
    static final String MOBILE_UA =
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    + "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                    + "Mobile/15E148 Safari/604.1";
    static final String TIKTOK_UA =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    + "Chrome/139.0.0.0 Safari/537.36";

    static final class Response {
        final String body;
        final String url;
        final String cookies;
        final int code;

        Response(String body, String url, String cookies, int code) {
            this.body = body;
            this.url = url;
            this.cookies = cookies;
            this.code = code;
        }
    }

    static Response get(String address, Map<String, String> headers) throws IOException {
        HttpURLConnection conn = (HttpURLConnection) new URL(address).openConnection();
        conn.setInstanceFollowRedirects(true);
        conn.setConnectTimeout(20000);
        conn.setReadTimeout(30000);
        conn.setRequestMethod("GET");
        conn.setRequestProperty("Accept-Encoding", "identity");
        conn.setRequestProperty("Accept", "*/*");
        String ua = headers == null ? DESKTOP_UA : headers.get("User-Agent");
        conn.setRequestProperty("User-Agent", ua == null ? DESKTOP_UA : ua);
        if (headers != null) {
            for (Map.Entry<String, String> entry : headers.entrySet()) {
                if (!"User-Agent".equalsIgnoreCase(entry.getKey())
                        && entry.getValue() != null) {
                    conn.setRequestProperty(entry.getKey(), entry.getValue());
                }
            }
        }
        int code = conn.getResponseCode();
        InputStream input = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
        String body = "";
        if (input != null) {
            try (InputStream in = input;
                 ByteArrayOutputStream out = new ByteArrayOutputStream()) {
                byte[] buffer = new byte[65536];
                int read;
                while ((read = in.read(buffer)) != -1) {
                    out.write(buffer, 0, read);
                }
                body = out.toString(StandardCharsets.UTF_8.name());
            }
        }
        return new Response(body, conn.getURL().toString(), cookies(conn), code);
    }

    static String resolve(String address) throws IOException {
        HttpURLConnection conn = (HttpURLConnection) new URL(address).openConnection();
        conn.setInstanceFollowRedirects(true);
        conn.setConnectTimeout(20000);
        conn.setReadTimeout(20000);
        conn.setRequestProperty("User-Agent", MOBILE_UA);
        conn.setRequestProperty("Accept-Encoding", "identity");
        conn.getResponseCode();
        String finalUrl = conn.getURL().toString();
        conn.disconnect();
        return finalUrl;
    }

    private static String cookies(HttpURLConnection conn) {
        StringBuilder result = new StringBuilder();
        Map<String, List<String>> fields = conn.getHeaderFields();
        for (Map.Entry<String, List<String>> entry : fields.entrySet()) {
            if (entry.getKey() == null
                    || !"set-cookie".equalsIgnoreCase(entry.getKey())) {
                continue;
            }
            for (String raw : entry.getValue()) {
                String pair = raw.split(";", 2)[0].trim();
                if (!pair.isEmpty()) {
                    if (result.length() > 0) {
                        result.append("; ");
                    }
                    result.append(pair);
                }
            }
        }
        return result.toString();
    }

    private Http() {
    }
}
