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
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.io.OutputStream;

public class DouyinLoginActivity extends Activity {
    private static final int BG = 0xFF0F1115;
    private static final int CARD = 0xFF181B22;
    private static final int TEXT = 0xFFF2F4F8;
    private static final int MUTED = 0xFF9AA3B2;
    private static final int ACCENT = 0xFFE84C6A;

    private WebView web;
    private ImageView qrView;
    private TextView status;
    private LinearLayout overlay;
    private Bitmap qrBitmap;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private boolean finished;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(BG);
        getWindow().setNavigationBarColor(BG);
        SharedPreferences prefs = getSharedPreferences("huzhmi", MODE_PRIVATE);

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
                clickLogin();
                handler.postDelayed(DouyinLoginActivity.this::clickLogin, 1500);
                handler.postDelayed(() -> extractQr(0), 2200);
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
        title.setText("抖音扫码登录");
        title.setTextColor(TEXT);
        title.setTextSize(20);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        overlay.addView(title);

        TextView tip = new TextView(this);
        tip.setText("用抖音 App 扫描下方二维码");
        tip.setTextColor(MUTED);
        tip.setTextSize(12);
        tip.setPadding(0, dp(6), 0, dp(12));
        overlay.addView(tip);

        qrView = new ImageView(this);
        qrView.setBackgroundColor(Color.WHITE);
        qrView.setScaleType(ImageView.ScaleType.FIT_CENTER);
        overlay.addView(qrView, new LinearLayout.LayoutParams(dp(250), dp(250)));

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

        if (hasCookie(prefs)) {
            status.setText("已保存 Cookie，可直接关闭");
        }
        web.loadUrl("https://sso.douyin.com/");
        handler.postDelayed(this::pollCookie, 2500);
    }

    private void clickLogin() {
        if (web == null) {
            return;
        }
        String js = "(function(){var es=Array.from(document.querySelectorAll('div,button,span,a'));"
                + "for(var i=0;i<es.length;i++){var e=es[i];"
                + "if(e.children.length===0&&(e.textContent||'').trim()==='登录'&&e.offsetParent){e.click();return '1';}}"
                + "return '0';})()";
        web.evaluateJavascript(js, null);
    }

    private void extractQr(int attempt) {
        if (web == null || finished) {
            return;
        }
        String js = "(function(){var is=Array.from(document.querySelectorAll('img'));"
                + "for(var i=0;i<is.length;i++){var im=is[i];"
                + "if(im.src&&im.src.indexOf('data:image/png;base64,')===0&&im.width>=120){return im.src;}}"
                + "return '';})()";
        web.evaluateJavascript(js, value -> {
            String data = value == null ? "" : value;
            if (data.startsWith("\"") && data.endsWith("\"") && data.length() > 2) {
                data = data.substring(1, data.length() - 1);
            }
            int comma = data.indexOf("base64,");
            if (comma > 0) {
                try {
                    byte[] bytes = Base64.decode(data.substring(comma + 7), Base64.DEFAULT);
                    qrBitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
                    if (qrBitmap != null) {
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
        web.loadUrl("https://sso.douyin.com/");
        handler.postDelayed(() -> extractQr(0), 2500);
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
        String cookie = manager.getCookie("https://sso.douyin.com");
        if (cookie == null || !cookie.contains("sessionid=")) {
            cookie = manager.getCookie("https://www.douyin.com");
        }
        if (cookie != null && (cookie.contains("sessionid=") || cookie.contains("sid_tt="))) {
            prefs.edit().putString("douyin_cookie", cookie).apply();
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
        String cookie = prefs.getString("douyin_cookie", "");
        return cookie.contains("sessionid=") || cookie.contains("sid_tt=");
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
            String name = "Huzhmi-抖音二维码-" + System.currentTimeMillis() + ".png";
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
