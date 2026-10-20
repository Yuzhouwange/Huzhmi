package com.huzhmi.app;

import android.app.Activity;
import android.content.ContentValues;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import com.google.zxing.BarcodeFormat;
import com.google.zxing.EncodeHintType;
import com.google.zxing.common.BitMatrix;
import com.google.zxing.qrcode.QRCodeWriter;

import org.json.JSONObject;

import java.io.OutputStream;
import java.util.EnumMap;
import java.util.HashMap;
import java.util.Map;

public class QrLoginActivity extends Activity {
    private static final int BG = 0xFF0F1115;
    private static final int CARD = 0xFF181B22;
    private static final int TEXT = 0xFFF2F4F8;
    private static final int MUTED = 0xFF9AA3B2;

    private WebView web;
    private ImageView qrView;
    private TextView status;
    private LinearLayout overlay;
    private LinearLayout codeBox;
    private android.widget.EditText codeInput;
    private Bitmap qrBitmap;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private boolean finished;
    private boolean douyin;
    private boolean codeWatcherStarted;
    private String platform;
    private String baseUrl;
    private String cookieUrl;
    private String biliKey = "";
    private String biliCookies = "";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(BG);
        getWindow().setNavigationBarColor(BG);
        platform = getIntent().getStringExtra("platform");
        douyin = !"bilibili".equals(platform);
        baseUrl = douyin ? "https://creator.douyin.com/" : "https://passport.bilibili.com/login";
        cookieUrl = douyin ? "https://creator.douyin.com" : "https://www.bilibili.com";

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(BG);

        web = new WebView(this);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setUserAgentString(Http.DESKTOP_UA);
        web.setBackgroundColor(BG);
        web.setAlpha(0.01f);
        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(web, true);
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                if (douyin) {
                    clickLogin();
                    handler.postDelayed(QrLoginActivity.this::clickLogin, 1500);
                    handler.postDelayed(() -> {
                        if (!codeWatcherStarted) {
                            codeWatcherStarted = true;
                            detectCode();
                        }
                    }, 2500);
                }
                handler.postDelayed(() -> extractQr(0), douyin ? 2200 : 1200);
            }
        });
        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));

        overlay = new LinearLayout(this);
        overlay.setOrientation(LinearLayout.VERTICAL);
        overlay.setGravity(Gravity.CENTER_HORIZONTAL);
        overlay.setPadding(dp(22), dp(18), dp(22), dp(18));
        overlay.setBackground(round(CARD, dp(20)));

        TextView title = new TextView(this);
        title.setText(douyin ? "抖音扫码登录" : "B 站扫码登录");
        title.setTextColor(TEXT);
        title.setTextSize(20);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        overlay.addView(title);

        TextView tip = new TextView(this);
        tip.setText(douyin ? "用抖音 App 扫描下方二维码" : "用哔哩哔哩 App 扫描下方二维码");
        tip.setTextColor(MUTED);
        tip.setTextSize(12);
        tip.setPadding(0, dp(6), 0, dp(12));
        overlay.addView(tip);

        qrView = new ImageView(this);
        qrView.setBackgroundColor(Color.WHITE);
        qrView.setScaleType(ImageView.ScaleType.FIT_CENTER);
        overlay.addView(qrView, new LinearLayout.LayoutParams(dp(250), dp(250)));

        codeBox = new LinearLayout(this);
        codeBox.setOrientation(LinearLayout.VERTICAL);
        codeBox.setVisibility(View.GONE);
        codeBox.setPadding(0, dp(8), 0, 0);

        TextView codeTip = new TextView(this);
        codeTip.setText("需要短信验证码");
        codeTip.setTextColor(MUTED);
        codeTip.setTextSize(11);
        codeBox.addView(codeTip);

        LinearLayout codeRow = new LinearLayout(this);
        codeRow.setOrientation(LinearLayout.HORIZONTAL);
        codeInput = new EditText(this);
        codeInput.setHint("输入验证码");
        codeInput.setHintTextColor(0xFF6B7280);
        codeInput.setTextColor(TEXT);
        codeInput.setTextSize(14);
        codeInput.setSingleLine(true);
        codeInput.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        codeInput.setBackground(round(0xFF2A2F3A, dp(8)));
        codeInput.setPadding(dp(8), dp(4), dp(8), dp(4));
        codeRow.addView(codeInput, new LinearLayout.LayoutParams(0,
                dp(44), 1));
        Button send = smallButton("发送");
        send.setOnClickListener(v -> clickSendCode());
        codeRow.addView(send);
        Button submit = smallButton("提交");
        submit.setOnClickListener(v -> submitCode());
        codeRow.addView(submit);
        codeBox.addView(codeRow);
        overlay.addView(codeBox);

        status = new TextView(this);
        status.setText("正在获取二维码…");
        status.setTextColor(MUTED);
        status.setTextSize(12);
        status.setPadding(0, dp(10), 0, dp(6));
        overlay.addView(status);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER);
        Button refresh = smallButton("刷新");
        refresh.setOnClickListener(v -> reload());
        Button save = smallButton("保存二维码");
        save.setOnClickListener(v -> saveQr());
        Button manual = smallButton("提取 Cookie");
        manual.setOnClickListener(v -> checkCookie(true));
        row.addView(refresh);
        row.addView(save);
        row.addView(manual);
        overlay.addView(row);

        LinearLayout row2 = new LinearLayout(this);
        row2.setOrientation(LinearLayout.HORIZONTAL);
        row2.setGravity(Gravity.CENTER);
        Button webLogin = smallButton("网页登录");
        webLogin.setOnClickListener(v -> {
            overlay.setVisibility(View.GONE);
            web.setAlpha(1f);
            if (!douyin) {
                web.loadUrl(baseUrl);
            }
        });
        Button close = smallButton("关闭");
        close.setOnClickListener(v -> finish());
        row2.addView(webLogin);
        row2.addView(close);
        overlay.addView(row2);

        FrameLayout.LayoutParams overlayParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.CENTER);
        root.addView(overlay, overlayParams);
        setContentView(root);

        SharedPreferences prefs = getSharedPreferences("huzhmi", MODE_PRIVATE);
        if (hasCookie(prefs)) {
            status.setText("已保存 Cookie，可直接关闭");
        }
        if (douyin) {
            web.loadUrl(baseUrl);
            handler.postDelayed(this::pollCookie, 2500);
        } else {
            startBiliApi();
        }
    }

    private void clickLogin() {
        if (!douyin || web == null) {
            return;
        }
        String js = "(function(){var es=Array.from(document.querySelectorAll('div,button,span,a'));"
                + "for(var i=0;i<es.length;i++){var e=es[i];"
                + "if(e.children.length===0&&(e.textContent||'').trim()==='登录'&&e.offsetParent){e.click();return '1';}}"
                + "return '0';})()";
        web.evaluateJavascript(js, null);
    }

    private void detectCode() {
        if (!douyin || web == null || finished) {
            return;
        }
        String js = "(function(){var inputs=Array.from(document.querySelectorAll('input')).filter(function(i){"
                + "return i.offsetParent&&/验证码|code|sms/i.test((i.placeholder||'')+(i.name||'')+(i.id||''));});"
                + "if(inputs.length)return 'true';"
                + "var send=Array.from(document.querySelectorAll('button,div,span')).some(function(b){"
                + "return b.offsetParent&&/发送验证码|获取验证码/.test((b.textContent||'').trim());});"
                + "var t=document.body.innerText||'';"
                + "return (send&&/验证码/.test(t))?'true':'false';})()";
        web.evaluateJavascript(js, value -> {
            if ("\"true\"".equals(value) || "true".equals(value)) {
                codeBox.setVisibility(View.VISIBLE);
                status.setText("需要验证码，请在下方输入");
            }
            handler.postDelayed(this::detectCode, 1500);
        });
    }

    private void clickSendCode() {
        if (web == null) {
            return;
        }
        String js = "(function(){var es=Array.from(document.querySelectorAll('button,div,span,a'));"
                + "for(var i=0;i<es.length;i++){var t=(es[i].textContent||'').trim();"
                + "if(es[i].offsetParent&&(t==='发送验证码'||t==='获取验证码')){es[i].click();return '1';}}"
                + "return '0';})()";
        web.evaluateJavascript(js, value -> {
            if ("\"0\"".equals(value)) {
                toast("页面上没找到发送按钮");
            }
        });
    }

    private void submitCode() {
        String code = codeInput.getText().toString().trim();
        if (code.isEmpty()) {
            toast("先输入验证码");
            return;
        }
        if (web == null) {
            return;
        }
        String js = "(function(code){"
                + "var inputs=Array.from(document.querySelectorAll('input')).filter(function(i){"
                + "return i.offsetParent&&/验证码|code|sms/i.test((i.placeholder||'')+(i.name||'')+(i.id||''));});"
                + "if(!inputs.length)return 'noinput';"
                + "var el=inputs[0];var setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;"
                + "setter.call(el,code);el.dispatchEvent(new Event('input',{bubbles:true}));"
                + "el.dispatchEvent(new Event('change',{bubbles:true}));"
                + "var bs=Array.from(document.querySelectorAll('button,div,span')).filter(function(b){"
                + "var t=(b.textContent||'').trim();return b.offsetParent&&t.length<=8&&/验证|提交|下一步|确定/.test(t);});"
                + "if(bs.length){bs[0].click();return 'ok';}return 'nobutton';})("
                + org.json.JSONObject.quote(code) + ")";
        web.evaluateJavascript(js, value -> {
            if (value != null && value.contains("noinput")) {
                toast("页面上没有验证码输入框");
            } else if (value != null && value.contains("nobutton")) {
                toast("没找到提交按钮，请用「网页登录」");
            } else {
                toast("验证码已提交");
                codeBox.setVisibility(View.GONE);
            }
        });
    }

    private void extractQr(int attempt) {
        if (web == null || finished) {
            return;
        }
        String js = "(function(){var out=[];"
                + "Array.from(document.querySelectorAll('img')).forEach(function(im){"
                + "if(im.src&&im.src.indexOf('data:image/png;base64,')===0"
                + "&&im.width>=140&&im.height>=140&&Math.abs(im.width-im.height)<=20){out.push(im.src);}});"
                + "return out.join('|');})()";
        web.evaluateJavascript(js, value -> {
            String data = value == null ? "" : value;
            if (data.startsWith("\"") && data.endsWith("\"") && data.length() > 2) {
                data = data.substring(1, data.length() - 1);
            }
            for (String part : data.split("\\|")) {
                int comma = part.indexOf("base64,");
                if (comma <= 0) {
                    continue;
                }
                try {
                    byte[] bytes = Base64.decode(part.substring(comma + 7), Base64.DEFAULT);
                    Bitmap candidate = BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
                    if (candidate != null && !isBlank(candidate)) {
                        qrBitmap = candidate;
                        qrView.setImageBitmap(qrBitmap);
                        status.setText("等待扫码");
                        return;
                    }
                } catch (Exception ignored) {
                }
            }
            if (attempt < 10) {
                handler.postDelayed(() -> extractQr(attempt + 1), 800);
            } else {
                status.setText("二维码没加载出来，点「网页登录」");
            }
        });
    }

    private void reload() {
        qrView.setImageDrawable(null);
        qrBitmap = null;
        status.setText("正在获取二维码…");
        overlay.setVisibility(View.VISIBLE);
        web.setAlpha(0.01f);
        if (douyin) {
            web.loadUrl(baseUrl);
            handler.postDelayed(() -> extractQr(0), 2500);
        } else {
            startBiliApi();
        }
    }

    private void startBiliApi() {
        status.setText("正在获取 B 站二维码…");
        new Thread(() -> {
            try {
                Http.Response response = Http.get(
                        "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
                        biliHeaders());
                JSONObject root = new JSONObject(response.body);
                if (root.optInt("code") != 0) {
                    throw new Exception(root.optString("message", "接口失败"));
                }
                JSONObject data = root.getJSONObject("data");
                biliKey = data.getString("qrcode_key");
                biliCookies = response.cookies;
                Bitmap bitmap = encodeQr(data.getString("url"), 600);
                runOnUiThread(() -> {
                    qrBitmap = bitmap;
                    qrView.setImageBitmap(bitmap);
                    status.setText("等待扫码");
                });
                handler.postDelayed(this::pollBili, 1500);
            } catch (Exception exc) {
                runOnUiThread(() -> status.setText(
                        "B 站二维码失败：" + exc.getMessage() + "，可点「网页登录」"));
            }
        }).start();
    }

    private void pollBili() {
        if (finished || biliKey.isEmpty()) {
            return;
        }
        new Thread(() -> {
            try {
                Map<String, String> headers = biliHeaders();
                Http.Response response = Http.get(
                        "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
                                + "?qrcode_key=" + java.net.URLEncoder.encode(biliKey, "UTF-8"),
                        headers);
                JSONObject root = new JSONObject(response.body);
                JSONObject data = root.optJSONObject("data");
                if (data == null) {
                    throw new Exception(root.optString("message", "轮询失败"));
                }
                biliCookies = mergeCookies(biliCookies, response.cookies);
                int code = data.optInt("code");
                if (code == 0) {
                    String redirect = data.optString("url", "");
                    if (!biliCookies.contains("SESSDATA=") && !redirect.isEmpty()) {
                        Map<String, String> follow = biliHeaders();
                        follow.put("Cookie", biliCookies);
                        Http.Response finalPage = Http.get(redirect, follow);
                        biliCookies = mergeCookies(biliCookies, finalPage.cookies);
                    }
                    if (biliCookies.contains("SESSDATA=")) {
                        saveBiliCookie(biliCookies);
                    } else {
                        runOnUiThread(() -> status.setText("登录成功但没拿到 SESSDATA"));
                    }
                    return;
                }
                if (code == 86038) {
                    runOnUiThread(() -> status.setText("二维码已过期，点「刷新」"));
                    return;
                }
                if (code == 86090) {
                    runOnUiThread(() -> status.setText("已扫码，请在手机确认"));
                }
                handler.postDelayed(this::pollBili, 2000);
            } catch (Exception exc) {
                runOnUiThread(() -> status.setText("B 站轮询失败：" + exc.getMessage()));
                handler.postDelayed(this::pollBili, 2500);
            }
        }).start();
    }

    private void saveBiliCookie(String cookie) {
        getSharedPreferences("huzhmi", MODE_PRIVATE).edit()
                .putString("bili_cookie", cookie).apply();
        finished = true;
        runOnUiThread(() -> {
            status.setText("登录成功");
            toast("Cookie 已保存");
            setResult(RESULT_OK);
            finish();
        });
    }

    private Map<String, String> biliHeaders() {
        Map<String, String> headers = new HashMap<>();
        headers.put("User-Agent", Http.DESKTOP_UA);
        headers.put("Referer", "https://www.bilibili.com/");
        if (!biliCookies.isEmpty()) {
            headers.put("Cookie", biliCookies);
        }
        return headers;
    }

    private static String mergeCookies(String first, String second) {
        if (second == null || second.isEmpty()) {
            return first == null ? "" : first;
        }
        if (first == null || first.isEmpty()) {
            return second;
        }
        return first + "; " + second;
    }

    private static Bitmap encodeQr(String text, int size) throws Exception {
        Map<EncodeHintType, Object> hints = new EnumMap<>(EncodeHintType.class);
        hints.put(EncodeHintType.CHARACTER_SET, "UTF-8");
        hints.put(EncodeHintType.MARGIN, 1);
        BitMatrix matrix = new QRCodeWriter().encode(
                text, BarcodeFormat.QR_CODE, size, size, hints);
        int[] pixels = new int[size * size];
        for (int y = 0; y < size; y++) {
            for (int x = 0; x < size; x++) {
                pixels[y * size + x] = matrix.get(x, y)
                        ? 0xFF000000 : 0xFFFFFFFF;
            }
        }
        Bitmap bitmap = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888);
        bitmap.setPixels(pixels, 0, size, 0, 0, size, size);
        return bitmap;
    }

    private void pollCookie() {
        if (finished) {
            return;
        }
        if (checkCookie(false)) {
            return;
        }
        handler.postDelayed(this::pollCookie, 2000);
    }

    private boolean checkCookie(boolean toast) {
        SharedPreferences prefs = getSharedPreferences("huzhmi", MODE_PRIVATE);
        CookieManager manager = CookieManager.getInstance();
        String cookie = manager.getCookie(cookieUrl);
        if (!douyin && (cookie == null || !cookie.contains("SESSDATA="))) {
            cookie = manager.getCookie("https://passport.bilibili.com");
        }
        if (douyin && (cookie == null || !cookie.contains("sessionid="))) {
            cookie = manager.getCookie("https://www.douyin.com");
        }
        boolean ok = douyin
                ? cookie != null && (cookie.contains("sessionid=") || cookie.contains("sid_tt="))
                : cookie != null && cookie.contains("SESSDATA=");
        if (ok) {
            prefs.edit().putString(douyin ? "douyin_cookie" : "bili_cookie", cookie).apply();
            finished = true;
            status.setText("登录成功");
            toast("Cookie 已保存");
            setResult(RESULT_OK);
            finish();
            return true;
        }
        if (toast) {
            toast("还没登录成功");
        }
        return false;
    }

    private boolean hasCookie(SharedPreferences prefs) {
        String cookie = prefs.getString(douyin ? "douyin_cookie" : "bili_cookie", "");
        return douyin
                ? cookie.contains("sessionid=") || cookie.contains("sid_tt=")
                : cookie.contains("SESSDATA=");
    }

    private static boolean isBlank(Bitmap bitmap) {
        if (bitmap == null || bitmap.getWidth() < 50 || bitmap.getHeight() < 50) {
            return true;
        }
        int first = bitmap.getPixel(0, 0);
        int total = 0;
        int different = 0;
        int stepX = Math.max(1, bitmap.getWidth() / 30);
        int stepY = Math.max(1, bitmap.getHeight() / 30);
        for (int y = 0; y < bitmap.getHeight(); y += stepY) {
            for (int x = 0; x < bitmap.getWidth(); x += stepX) {
                int color = bitmap.getPixel(x, y);
                if (total == 0) {
                    first = color;
                } else if (color != first) {
                    different++;
                }
                total++;
            }
        }
        return total == 0 || different < total * 0.05;
    }

    private void saveQr() {
        if (qrBitmap == null) {
            toast("二维码还没加载出来");
            return;
        }
        if (Build.VERSION.SDK_INT < 29) {
            toast("Android 10 以下暂不支持保存");
            return;
        }
        try {
            String name = "Huzhmi-" + (douyin ? "抖音" : "B站")
                    + "二维码-" + System.currentTimeMillis() + ".png";
            ContentValues values = new ContentValues();
            values.put(MediaStore.Images.Media.DISPLAY_NAME, name);
            values.put(MediaStore.Images.Media.MIME_TYPE, "image/png");
            values.put(MediaStore.Images.Media.RELATIVE_PATH,
                    Environment.DIRECTORY_PICTURES + "/Huzhmi");
            values.put(MediaStore.Images.Media.IS_PENDING, 1);
            Uri uri = getContentResolver().insert(
                    MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values);
            if (uri == null) {
                toast("保存失败");
                return;
            }
            try (OutputStream output = getContentResolver().openOutputStream(uri)) {
                if (output == null) {
                    toast("保存失败");
                    return;
                }
                qrBitmap.compress(Bitmap.CompressFormat.PNG, 100, output);
            }
            ContentValues done = new ContentValues();
            done.put(MediaStore.Images.Media.IS_PENDING, 0);
            getContentResolver().update(uri, done, null, null);
            toast("二维码已保存到 图片/Huzhmi");
        } catch (Exception exc) {
            toast("保存失败：" + exc.getMessage());
        }
    }

    @Override
    protected void onDestroy() {
        finished = true;
        handler.removeCallbacksAndMessages(null);
        if (web != null) {
            web.loadUrl("about:blank");
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }

    private Button smallButton(String text) {
        Button button = new Button(this);
        button.setText(text);
        button.setTextColor(TEXT);
        button.setTextSize(12);
        button.setAllCaps(false);
        GradientDrawable background = new GradientDrawable();
        background.setColor(0xFF2A2F3A);
        background.setCornerRadius(dp(10));
        button.setBackground(background);
        return button;
    }

    private GradientDrawable round(int color, int radius) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(radius);
        return drawable;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void toast(String message) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show();
    }
}
