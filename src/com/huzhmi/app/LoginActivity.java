package com.huzhmi.app;

import android.app.Activity;
import android.content.ContentValues;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.io.OutputStream;

public class LoginActivity extends Activity {
    private static final int BG = 0xFF0F1115;
    private static final int CARD = 0xFF181B22;
    private static final int TEXT = 0xFFF2F4F8;
    private static final int MUTED = 0xFF9AA3B2;
    private static final int ACCENT = 0xFFE84C6A;

    private WebView web;
    private String platform;
    private String baseUrl;
    private TextView hint;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        platform = getIntent().getStringExtra("platform");
        if (platform == null) {
            platform = "douyin";
        }
        baseUrl = "douyin".equals(platform)
                ? "https://sso.douyin.com/"
                : "https://passport.bilibili.com/login";
        String title = "douyin".equals(platform) ? "抖音登录" : "B 站登录";

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(BG);

        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(dp(16), dp(10), dp(12), dp(10));
        bar.setBackgroundColor(CARD);

        TextView label = new TextView(this);
        label.setText(title);
        label.setTextColor(TEXT);
        label.setTextSize(18);
        label.setTypeface(Typeface.DEFAULT_BOLD);
        bar.addView(label, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        Button save = smallButton("保存二维码");
        save.setOnClickListener(v -> saveScreenshot());
        bar.addView(save);

        Button extract = smallButton("提取 Cookie");
        extract.setOnClickListener(v -> extractCookie());
        bar.addView(extract);

        Button close = smallButton("关闭");
        close.setOnClickListener(v -> finish());
        LinearLayout.LayoutParams closeParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        closeParams.leftMargin = dp(8);
        bar.addView(close, closeParams);
        root.addView(bar);

        hint = new TextView(this);
        hint.setText("在下面官方页面登录；二维码可用另一台设备扫。登录成功后点右上角「提取 Cookie」。");
        hint.setTextColor(MUTED);
        hint.setTextSize(12);
        hint.setPadding(dp(16), dp(8), dp(16), dp(8));
        root.addView(hint);

        web = new WebView(this);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setJavaScriptCanOpenWindowsAutomatically(true);
        settings.setSupportMultipleWindows(false);
        settings.setUserAgentString(Http.DESKTOP_UA);
        web.setBackgroundColor(BG);
        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(web, true);
        web.setWebChromeClient(new WebChromeClient());
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                if (url != null && !url.contains("about:blank")) {
                    hint.setText("当前页面：" + url);
                }
            }
        });
        web.loadUrl(baseUrl);
        root.addView(web, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));

        setContentView(root);
    }

    private void saveScreenshot() {
        if (web == null || web.getWidth() == 0 || web.getHeight() == 0) {
            toast("页面还没加载好");
            return;
        }
        if (Build.VERSION.SDK_INT < 29) {
            toast("Android 10 以下暂不支持保存截图");
            return;
        }
        try {
            Bitmap bitmap = Bitmap.createBitmap(web.getWidth(), web.getHeight(),
                    Bitmap.Config.ARGB_8888);
            Canvas canvas = new Canvas(bitmap);
            web.draw(canvas);
            String name = "Huzhmi-二维码-" + System.currentTimeMillis() + ".png";
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
                bitmap.compress(Bitmap.CompressFormat.PNG, 100, output);
            }
            ContentValues done = new ContentValues();
            done.put(MediaStore.Images.Media.IS_PENDING, 0);
            getContentResolver().update(uri, done, null, null);
            bitmap.recycle();
            toast("已保存到 图片/Huzhmi，可在相册打开");
        } catch (Exception exc) {
            toast("保存截图失败：" + exc.getMessage());
        }
    }

    private void extractCookie() {
        CookieManager manager = CookieManager.getInstance();
        String cookie = manager.getCookie(baseUrl);
        if (cookie == null || cookie.isEmpty()) {
            cookie = manager.getCookie("https://www.douyin.com");
        }
        if (cookie == null || cookie.isEmpty()) {
            cookie = manager.getCookie("https://www.bilibili.com");
        }
        if (cookie == null || cookie.isEmpty()) {
            toast("没有读到 Cookie，先在页面里完成登录");
            return;
        }
        boolean douyin = "douyin".equals(platform);
        boolean ok = douyin
                ? (cookie.contains("sessionid=") || cookie.contains("sid_tt="))
                : cookie.contains("SESSDATA=");
        if (!ok) {
            toast(douyin ? "还没登录成功：缺少 sessionid / sid_tt"
                    : "还没登录成功：缺少 SESSDATA");
            return;
        }
        SharedPreferences prefs = getSharedPreferences("huzhmi", MODE_PRIVATE);
        prefs.edit()
                .putString(douyin ? "douyin_cookie" : "bili_cookie", cookie)
                .apply();
        toast("Cookie 已保存");
        setResult(RESULT_OK);
        finish();
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
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
        button.setTextSize(13);
        button.setAllCaps(false);
        GradientDrawable background = new GradientDrawable();
        background.setColor(0xFF2A2F3A);
        background.setCornerRadius(dp(10));
        button.setBackground(background);
        return button;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void toast(String message) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show();
    }
}
