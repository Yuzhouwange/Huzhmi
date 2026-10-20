package com.huzhmi.app;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

final class Downloader {
    interface Progress {
        void update(long done, long total);
    }

    static final class Item {
        final String url;
        final String type;
        boolean selected = true;

        Item(String url, String type) {
            this.url = url;
            this.type = type;
        }
    }

    static final class Preview {
        String title = "";
        String desc = "";
        String author = "";
        String kind = "video";
        String referer = "";
        String cookie = "";
        String cover = "";
        final List<Item> items = new ArrayList<>();
    }

    static final class Saved {
        Uri uri;
        File file;
        String name;
        String kind;

        void delete(Context context) {
            try {
                if (uri != null) {
                    context.getContentResolver().delete(uri, null, null);
                } else if (file != null) {
                    //noinspection ResultOfMethodCallIgnored
                    file.delete();
                }
            } catch (Exception ignored) {
            }
        }
    }

    static Preview parse(String raw, String douyinCookie, String biliCookie)
            throws Exception {
        String text = raw == null ? "" : raw.trim();
        Matcher matcher = Pattern.compile("https?://[^\\s]+").matcher(text);
        if (!matcher.find()) {
            throw new IOException("没有找到链接");
        }
        return parseUrl(matcher.group(), douyinCookie, biliCookie);
    }

    static Preview parseUrl(String url, String douyinCookie, String biliCookie)
            throws Exception {
        String target = url == null ? "" : url.trim();
        if (target.contains("b23.tv") || target.contains("bili2233.cn")
                || target.contains("v.douyin.com")
                || target.contains("vm.tiktok.com") || target.contains("vt.tiktok.com")) {
            target = Http.resolve(target);
        }
        if (target.contains("douyin.com") || target.contains("iesdouyin.com")) {
            return douyin(target, douyinCookie);
        }
        if (target.contains("tiktok.com")) {
            return tiktok(target);
        }
        if (target.contains("bilibili.com")) {
            return bilibili(target, biliCookie);
        }
        throw new IOException("只支持抖音 / TikTok / B 站链接");
    }

    // ------------------------------------------------------------------ 抖音

    private static Preview douyin(String url, String cookie) throws Exception {
        Matcher idMatcher = Pattern.compile("(?:/video/|/note/|modal_id=)(\\d{15,25})")
                .matcher(url);
        if (!idMatcher.find()) {
            throw new IOException("没认出抖音作品 ID");
        }
        String id = idMatcher.group(1);
        Map<String, String> headers = new HashMap<>();
        headers.put("User-Agent", Http.MOBILE_UA);
        headers.put("Referer", "https://www.douyin.com/");
        if (cookie != null && !cookie.isEmpty()) {
            headers.put("Cookie", cookie);
        }
        Http.Response response = Http.get(
                "https://www.iesdouyin.com/share/video/" + id + "/", headers);
        int marker = response.body.indexOf("window._ROUTER_DATA");
        if (marker < 0) {
            throw new IOException("抖音分享页没有数据，可能需要登录或 Cookie 已过期");
        }
        String json = objectAt(response.body, response.body.indexOf('{', marker));
        JSONObject root = new JSONObject(unescape(json));
        JSONObject loader = root.optJSONObject("loaderData");
        if (loader == null) {
            throw new IOException("抖音数据格式变化");
        }
        JSONObject item = null;
        Iterator<String> keys = loader.keys();
        while (keys.hasNext()) {
            JSONObject page = loader.optJSONObject(keys.next());
            if (page == null) {
                continue;
            }
            JSONObject info = page.optJSONObject("videoInfoRes");
            if (info == null) {
                info = page.optJSONObject("videoInfo");
            }
            JSONArray items = info == null ? null : info.optJSONArray("item_list");
            if (items != null && items.length() > 0) {
                item = items.optJSONObject(0);
                break;
            }
        }
        if (item == null) {
            throw new IOException("抖音没有返回作品数据");
        }
        Preview preview = new Preview();
        preview.title = item.optString("desc", "douyin_" + id);
        preview.desc = preview.title;
        JSONObject author = item.optJSONObject("author");
        preview.author = author == null ? "" : author.optString("nickname", "");
        preview.referer = "https://www.douyin.com/";
        preview.cookie = merge(cookie, response.cookies);
        JSONArray images = item.optJSONArray("images");
        JSONObject video = item.optJSONObject("video");
        if (images != null && images.length() > 0) {
            preview.kind = "images";
            for (int i = 0; i < images.length(); i++) {
                JSONObject image = images.optJSONObject(i);
                String imageUrl = imageUrl(image == null ? null
                        : image.optJSONArray("url_list"));
                if (!imageUrl.isEmpty()) {
                    preview.items.add(new Item(imageUrl, "image"));
                }
            }
            if (!preview.items.isEmpty()) {
                preview.cover = preview.items.get(0).url;
            }
        } else {
            String play = video == null ? "" : playUrl(video);
            if (!play.isEmpty()) {
                preview.items.add(new Item(play, "video"));
            }
            if (video != null) {
                preview.cover = videoCover(video);
            }
        }
        if (preview.items.isEmpty()) {
            throw new IOException("这条作品没有可下载的媒体");
        }
        return preview;
    }

    // ------------------------------------------------------------------ TikTok

    private static Preview tiktok(String url) throws Exception {
        Map<String, String> headers = new HashMap<>();
        headers.put("User-Agent", Http.TIKTOK_UA);
        headers.put("Accept", "text/html,application/xhtml+xml");
        Http.Response response = Http.get(url, headers);
        Matcher matcher = Pattern.compile(
                "id=\"__UNIVERSAL_DATA_FOR_REHYDRATION__\"[^>]*>(.*?)</script>",
                Pattern.DOTALL).matcher(response.body);
        if (!matcher.find()) {
            throw new IOException("TikTok 页面没有数据，可能需要验证或作品不可见");
        }
        JSONObject root = new JSONObject(unescape(matcher.group(1)));
        JSONObject scope = root.optJSONObject("__DEFAULT_SCOPE__");
        JSONObject detail = scope == null ? null
                : scope.optJSONObject("webapp.video-detail");
        JSONObject info = detail == null ? null : detail.optJSONObject("itemInfo");
        JSONObject item = info == null ? null : info.optJSONObject("itemStruct");
        if (item == null) {
            throw new IOException("TikTok 没有返回作品数据");
        }
        Preview preview = new Preview();
        preview.title = item.optString("desc", "tiktok");
        preview.desc = preview.title;
        JSONObject author = item.optJSONObject("author");
        preview.author = author == null ? "" : author.optString("nickname", "");
        preview.referer = "https://www.tiktok.com/";
        preview.cookie = response.cookies;
        JSONObject imagePost = item.optJSONObject("imagePost");
        JSONArray images = imagePost == null ? null : imagePost.optJSONArray("images");
        if (images != null && images.length() > 0) {
            preview.kind = "images";
            for (int i = 0; i < images.length(); i++) {
                JSONObject image = images.optJSONObject(i);
                JSONObject imageURL = image == null ? null
                        : image.optJSONObject("imageURL");
                String imageUrl = imageUrl(imageURL == null ? null
                        : imageURL.optJSONArray("urlList"));
                if (!imageUrl.isEmpty()) {
                    preview.items.add(new Item(imageUrl, "image"));
                }
            }
            if (!preview.items.isEmpty()) {
                preview.cover = preview.items.get(0).url;
            }
        } else {
            JSONObject video = item.optJSONObject("video");
            String play = video == null ? "" : video.optString("playAddr", "");
            if (play.isEmpty() && video != null) {
                play = video.optString("downloadAddr", "");
            }
            if (!play.isEmpty()) {
                preview.items.add(new Item(play, "video"));
            }
            if (video != null) {
                preview.cover = videoCover(video);
            }
        }
        if (preview.items.isEmpty()) {
            throw new IOException("TikTok 没有可下载的媒体");
        }
        return preview;
    }

    // ------------------------------------------------------------------ B 站

    private static Preview bilibili(String url, String cookie) throws Exception {
        Matcher bv = Pattern.compile("(BV[0-9A-Za-z]{10})").matcher(url);
        String bvid = bv.find() ? bv.group(1) : "";
        Matcher av = Pattern.compile("/av(\\d+)", Pattern.CASE_INSENSITIVE).matcher(url);
        String aid = av.find() ? av.group(1) : "";
        if (bvid.isEmpty() && aid.isEmpty()) {
            throw new IOException("没认出 B 站 BV 号");
        }
        int page = 1;
        Matcher p = Pattern.compile("[?&]p=(\\d+)").matcher(url);
        if (p.find()) {
            page = Math.max(1, Integer.parseInt(p.group(1)));
        }
        Map<String, String> headers = new HashMap<>();
        headers.put("User-Agent", Http.DESKTOP_UA);
        headers.put("Referer", "https://www.bilibili.com/");
        if (cookie != null && !cookie.isEmpty()) {
            headers.put("Cookie", cookie);
        }
        String key = bvid.isEmpty() ? "aid=" + aid : "bvid=" + bvid;
        JSONObject view = new JSONObject(Http.get(
                "https://api.bilibili.com/x/web-interface/view?" + key, headers).body);
        if (view.optInt("code") != 0) {
            throw new IOException("B 站接口失败：" + view.optString("message"));
        }
        JSONObject data = view.getJSONObject("data");
        Preview preview = new Preview();
        preview.title = data.optString("title", "bilibili");
        preview.desc = data.optString("desc", "");
        JSONObject owner = data.optJSONObject("owner");
        preview.author = owner == null ? "" : owner.optString("name", "");
        preview.referer = "https://www.bilibili.com/";
        preview.cookie = cookie;
        preview.cover = data.optString("pic", "");
        String cid = data.optString("cid", "");
        JSONArray pages = data.optJSONArray("pages");
        if (pages != null && pages.length() >= page) {
            JSONObject pageInfo = pages.optJSONObject(page - 1);
            cid = pageInfo == null ? cid : pageInfo.optString("cid", cid);
        }
        if (cid.isEmpty()) {
            throw new IOException("B 站没有找到 cid");
        }
        JSONObject play = new JSONObject(Http.get(
                "https://api.bilibili.com/x/player/playurl?" + key
                        + "&cid=" + cid + "&qn=64&fnval=1&fourk=0", headers).body);
        if (play.optInt("code") != 0) {
            throw new IOException("B 站取流失败：" + play.optString("message"));
        }
        JSONObject playData = play.getJSONObject("data");
        JSONArray durl = playData.optJSONArray("durl");
        if (durl == null || durl.length() == 0) {
            throw new IOException("该视频没有整段 MP4，需要登录或换较低画质");
        }
        String mediaUrl = durl.optJSONObject(0).optString("url", "");
        if (mediaUrl.isEmpty()) {
            throw new IOException("B 站没有返回播放地址");
        }
        if (pages != null && pages.length() > 1) {
            preview.title = preview.title + "_P" + page;
        }
        preview.kind = "video";
        preview.items.add(new Item(mediaUrl, "video"));
        return preview;
    }

    // ------------------------------------------------------------------ 保存

    static Saved downloadVideo(Context context, Preview preview, Progress progress)
            throws Exception {
        return download(context, preview, preview.items.get(0), progress);
    }

    static Saved downloadImage(Context context, Preview preview, Item item)
            throws Exception {
        return download(context, preview, item, null);
    }

    private static Saved download(Context context, Preview preview, Item item,
                                  Progress progress) throws Exception {
        boolean image = "image".equals(item.type);
        String extension = image ? ".jpg" : ".mp4";
        String name = sanitize(preview.title) + extension;
        if (image && preview.items.size() > 1) {
            int index = preview.items.indexOf(item) + 1;
            name = sanitize(preview.title) + "_" + index + extension;
        }
        String mediaUrl = item.url.replace("&amp;", "&").replace("\\/", "/");
        HttpURLConnection conn = null;
        int code = 0;
        IOException lastError = null;
        for (int attempt = 0; attempt < 3; attempt++) {
            try {
                conn = (HttpURLConnection) new URL(mediaUrl).openConnection();
                conn.setConnectTimeout(20000);
                conn.setReadTimeout(60000);
                conn.setInstanceFollowRedirects(true);
                conn.setRequestProperty("User-Agent", attempt >= 2
                        ? Http.DESKTOP_UA
                        : preview.referer.contains("douyin")
                        ? Http.MOBILE_UA : Http.DESKTOP_UA);
                conn.setRequestProperty("Referer", preview.referer);
                conn.setRequestProperty("Accept", image
                        ? "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"
                        : "*/*");
                conn.setRequestProperty("Accept-Encoding", "identity");
                conn.setRequestProperty("Connection", "close");
                if (attempt == 0 && preview.cookie != null
                        && !preview.cookie.isEmpty()) {
                    conn.setRequestProperty("Cookie", preview.cookie);
                }
                code = conn.getResponseCode();
                if (code < 400) {
                    break;
                }
                lastError = new IOException("HTTP " + code);
            } catch (IOException exc) {
                lastError = exc;
                conn = null;
            }
            if (attempt < 2) {
                try {
                    Thread.sleep(350);
                } catch (InterruptedException ignored) {
                }
            }
        }
        if (conn == null || code >= 400) {
            throw new IOException("下载失败："
                    + (lastError == null ? "网络错误" : lastError.getMessage())
                    + "，已重试 3 次");
        }
        long total = conn.getContentLengthLong();
        ContentResolver resolver = context.getContentResolver();
        OutputStream output;
        Uri uri = null;
        File file = null;
        if (Build.VERSION.SDK_INT >= 29) {
            ContentValues values = new ContentValues();
            values.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
            values.put(MediaStore.MediaColumns.MIME_TYPE,
                    image ? "image/jpeg" : "video/mp4");
            values.put(MediaStore.MediaColumns.RELATIVE_PATH, image
                    ? Environment.DIRECTORY_PICTURES + "/Huzhmi"
                    : Environment.DIRECTORY_MOVIES + "/Huzhmi");
            values.put(MediaStore.MediaColumns.IS_PENDING, 1);
            uri = resolver.insert(image
                    ? MediaStore.Images.Media.EXTERNAL_CONTENT_URI
                    : MediaStore.Video.Media.EXTERNAL_CONTENT_URI, values);
            if (uri == null) {
                throw new IOException("无法创建媒体文件");
            }
            output = resolver.openOutputStream(uri);
        } else {
            File dir = new File(Environment.getExternalStoragePublicDirectory(
                    image ? Environment.DIRECTORY_PICTURES
                            : Environment.DIRECTORY_MOVIES), "Huzhmi");
            if (!dir.exists() && !dir.mkdirs()) {
                throw new IOException("无法创建下载目录");
            }
            file = new File(dir, name);
            output = new FileOutputStream(file);
        }
        if (output == null) {
            throw new IOException("无法写入媒体文件");
        }
        long done = 0;
        try (InputStream input = conn.getInputStream(); OutputStream out = output) {
            byte[] buffer = new byte[65536];
            int read;
            while ((read = input.read(buffer)) != -1) {
                out.write(buffer, 0, read);
                done += read;
                if (progress != null) {
                    progress.update(done, total);
                }
            }
        }
        if (Build.VERSION.SDK_INT >= 29 && uri != null) {
            ContentValues doneValues = new ContentValues();
            doneValues.put(MediaStore.MediaColumns.IS_PENDING, 0);
            resolver.update(uri, doneValues, null, null);
        }
        Saved saved = new Saved();
        saved.uri = uri;
        saved.file = file;
        saved.name = name;
        saved.kind = item.type;
        return saved;
    }

    private static String videoCover(JSONObject video) {
        for (String key : new String[]{"cover", "origin_cover", "dynamic_cover"}) {
            JSONObject object = video.optJSONObject(key);
            String url = object == null ? ""
                    : imageUrl(object.optJSONArray("url_list"));
            if (url.isEmpty()) {
                String direct = video.optString(key, "");
                if (direct.startsWith("http")) {
                    url = direct;
                }
            }
            if (!url.isEmpty()) {
                return url;
            }
        }
        return "";
    }

    private static String playUrl(JSONObject video) {
        String[] keys = {"play_addr_h264", "play_addr_265", "play_addr"};
        for (String key : keys) {
            String url = imageUrl(video.optJSONObject(key) == null ? null
                    : video.optJSONObject(key).optJSONArray("url_list"));
            if (!url.isEmpty()) {
                return url.replace("/playwm/", "/play/").replace("playwm", "play");
            }
        }
        return "";
    }

    private static String imageUrl(JSONArray urls) {
        if (urls == null) {
            return "";
        }
        String fallback = "";
        for (int i = 0; i < urls.length(); i++) {
            String value = urls.optString(i, "");
            if (value.isEmpty()) {
                continue;
            }
            String lower = value.toLowerCase();
            if (lower.contains(".jpeg") || lower.contains(".jpg")) {
                return value;
            }
            if (fallback.isEmpty()) {
                fallback = value;
            }
        }
        return fallback;
    }

    private static String objectAt(String text, int start) throws IOException {
        if (start < 0) {
            throw new IOException("没有 JSON 数据");
        }
        int depth = 0;
        boolean quoted = false;
        boolean escaped = false;
        for (int i = start; i < text.length(); i++) {
            char ch = text.charAt(i);
            if (quoted) {
                if (escaped) {
                    escaped = false;
                } else if (ch == '\\') {
                    escaped = true;
                } else if (ch == '"') {
                    quoted = false;
                }
                continue;
            }
            if (ch == '"') {
                quoted = true;
            } else if (ch == '{') {
                depth++;
            } else if (ch == '}') {
                depth--;
                if (depth == 0) {
                    return text.substring(start, i + 1);
                }
            }
        }
        throw new IOException("JSON 不完整");
    }

    private static String merge(String user, String page) {
        StringBuilder result = new StringBuilder();
        if (user != null && !user.isEmpty()) {
            result.append(user);
        }
        if (page != null && !page.isEmpty()) {
            if (result.length() > 0) {
                result.append("; ");
            }
            result.append(page);
        }
        return result.toString();
    }

    private static String sanitize(String title) {
        String value = title == null ? "video" : title;
        value = value.replaceAll("[\\\\/:*?\"<>|\\r\\n]+", "_").trim();
        if (value.isEmpty()) {
            value = "video";
        }
        return value.length() > 80 ? value.substring(0, 80) : value;
    }

    private static String unescape(String value) {
        return value.replace("&quot;", "\"")
                .replace("&#34;", "\"")
                .replace("&#39;", "'")
                .replace("&apos;", "'")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .replace("&amp;", "&");
    }

    private Downloader() {
    }
}
