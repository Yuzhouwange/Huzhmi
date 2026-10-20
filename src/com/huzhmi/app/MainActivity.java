package com.huzhmi.app;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.HorizontalScrollView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.MediaController;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import android.widget.VideoView;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class MainActivity extends Activity {
    private static final int BG = 0xFF0F1115;
    private static final int CARD = 0xFF181B22;
    private static final int TEXT = 0xFFF2F4F8;
    private static final int MUTED = 0xFF9AA3B2;
    private static final int ACCENT = 0xFFE84C6A;

    private SharedPreferences prefs;
    private LinearLayout side;
    private android.widget.FrameLayout content;
    private TextView headerTitle;
    private final List<View> pages = new ArrayList<>();
    private final List<Button> tabButtons = new ArrayList<>();
    private Section douyin;
    private Section bilibili;
    private int current = 0;

    private static final class Entry {
        final Downloader.Preview preview;
        boolean selected = true;

        Entry(Downloader.Preview preview) {
            this.preview = preview;
        }
    }

    private static final class Section {
        String platform;
        EditText input;
        LinearLayout results;
        TextView status;
        TextView cookieState;
        Button parseButton;
        Button downloadButton;
        ProgressBar progress;
        final List<Entry> entries = new ArrayList<>();
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences("huzhmi", MODE_PRIVATE);
        Window window = getWindow();
        window.setStatusBarColor(BG);
        window.setNavigationBarColor(BG);
        window.setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(BG);

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setBackgroundColor(0xFF0B0D11);
        header.setPadding(dp(8), dp(6), dp(12), dp(6));

        Button menu = new Button(this);
        menu.setText("☰");
        menu.setTextSize(22);
        menu.setTextColor(TEXT);
        menu.setAllCaps(false);
        menu.setBackground(round(0x00000000, dp(10), 0));
        menu.setOnClickListener(v -> toggleSidebar());
        header.addView(menu, new LinearLayout.LayoutParams(dp(48), dp(44)));

        headerTitle = new TextView(this);
        headerTitle.setText("Huzhmi");
        headerTitle.setTextColor(TEXT);
        headerTitle.setTextSize(17);
        headerTitle.setTypeface(Typeface.DEFAULT_BOLD);
        headerTitle.setPadding(dp(4), 0, 0, 0);
        header.addView(headerTitle, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        root.addView(header);

        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.HORIZONTAL);
        body.setBackgroundColor(BG);

        side = buildSidebar();
        side.setVisibility(View.GONE);
        body.addView(side, new LinearLayout.LayoutParams(dp(86),
                ViewGroup.LayoutParams.MATCH_PARENT));

        content = new android.widget.FrameLayout(this);
        content.setBackgroundColor(BG);
        aboutPage();
        douyin = section("douyin", "抖音 / TikTok",
                "粘贴作品链接或整段分享文案，支持多行、多条");
        bilibili = section("bilibili", "B 站",
                "粘贴 BV / av / 分 P 链接，支持多条");
        body.addView(content, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.MATCH_PARENT, 1));
        root.addView(body, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));

        setContentView(root);
        switchTab(0);
    }

    @Override
    protected void onResume() {
        super.onResume();
        refreshCookieState();
    }

    // ------------------------------------------------------------------ 左侧分层

    private LinearLayout buildSidebar() {
        LinearLayout bar = new LinearLayout(this);
        bar.setOrientation(LinearLayout.VERTICAL);
        bar.setBackgroundColor(0xFF0B0D11);
        bar.setPadding(dp(8), dp(22), dp(8), dp(16));
        bar.setGravity(Gravity.CENTER_HORIZONTAL);

        TextView logo = new TextView(this);
        logo.setText("H");
        logo.setTextColor(ACCENT);
        logo.setTextSize(26);
        logo.setTypeface(Typeface.DEFAULT_BOLD);
        logo.setGravity(Gravity.CENTER);
        bar.addView(logo);

        TextView name = new TextView(this);
        name.setText("Huzhmi");
        name.setTextColor(MUTED);
        name.setTextSize(10);
        name.setGravity(Gravity.CENTER);
        name.setPadding(0, 0, 0, dp(22));
        bar.addView(name);

        bar.addView(tab("介绍", 0));
        bar.addView(tab("抖音", 1));
        bar.addView(tab("B 站", 2));
        return bar;
    }

    private Button tab(String text, int index) {
        Button button = new Button(this);
        button.setText(text);
        button.setTextSize(13);
        button.setAllCaps(false);
        button.setTextColor(MUTED);
        button.setBackground(round(0x00000000, dp(12), 0));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(48));
        params.bottomMargin = dp(6);
        button.setLayoutParams(params);
        button.setOnClickListener(v -> switchTab(index));
        tabButtons.add(button);
        return button;
    }

    private void switchTab(int index) {
        current = index;
        for (int i = 0; i < pages.size(); i++) {
            pages.get(i).setVisibility(i == index ? View.VISIBLE : View.GONE);
        }
        for (int i = 0; i < tabButtons.size(); i++) {
            Button button = tabButtons.get(i);
            boolean active = i == index;
            button.setTextColor(active ? Color.WHITE : MUTED);
            button.setBackground(round(active ? 0xFF2A1B22 : 0x00000000,
                    dp(12), active ? ACCENT : 0));
        }
        if (side != null) {
            side.setVisibility(View.GONE);
        }
        if (headerTitle != null) {
            String[] titles = {"介绍", "抖音 / TikTok", "B 站"};
            headerTitle.setText(titles[Math.max(0, Math.min(index, titles.length - 1))]);
        }
        refreshCookieState();
    }

    private void toggleSidebar() {
        if (side == null) {
            return;
        }
        side.setVisibility(side.getVisibility() == View.VISIBLE
                ? View.GONE : View.VISIBLE);
    }

    // ------------------------------------------------------------------ 介绍

    private void aboutPage() {
        ScrollView scroll = new ScrollView(this);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(20), dp(28), dp(20), dp(24));
        scroll.addView(box, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView title = new TextView(this);
        title.setText("Huzhmi");
        title.setTextColor(TEXT);
        title.setTextSize(30);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        box.addView(title);

        TextView version = new TextView(this);
        version.setText("Android 1.15");
        version.setTextColor(ACCENT);
        version.setTextSize(13);
        version.setPadding(0, dp(2), 0, dp(18));
        box.addView(version);

        TextView intro = new TextView(this);
        intro.setText("抖音 / TikTok / B 站 视频与图集下载工具。\n\n"
                + "支持链接和整段分享文案，自动识别多条作品，"
                + "解析后列出视频或图片，可选择下载。\n\n"
                + "支持抖音、B 站官方网页扫码登录并提取 Cookie。\n"
                + "视频保存到 电影/Huzhmi，图片保存到 图片/Huzhmi。");
        intro.setTextColor(MUTED);
        intro.setTextSize(14);
        intro.setLineSpacing(dp(4), 1f);
        box.addView(intro);

        TextView sourceNote = new TextView(this);
        sourceNote.setText("源码说明：仓库只包含应用源码，不包含签名密钥、Cookie、"
                + "用户数据或本机路径。");
        sourceNote.setTextColor(MUTED);
        sourceNote.setTextSize(12);
        sourceNote.setLineSpacing(dp(3), 1f);
        sourceNote.setPadding(0, dp(22), 0, 0);
        box.addView(sourceNote);

        pages.add(scroll);
        content.addView(scroll, new android.widget.FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
    }

    // ------------------------------------------------------------------ 下载页

    private Section section(String platform, String title, String subtitle) {
        Section section = new Section();
        section.platform = platform;

        ScrollView scroll = new ScrollView(this);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(16), dp(22), dp(16), dp(24));
        scroll.addView(box, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));

        TextView heading = new TextView(this);
        heading.setText(title);
        heading.setTextColor(TEXT);
        heading.setTextSize(23);
        heading.setTypeface(Typeface.DEFAULT_BOLD);
        box.addView(heading);

        TextView hint = new TextView(this);
        hint.setText(subtitle);
        hint.setTextColor(MUTED);
        hint.setTextSize(12);
        hint.setPadding(0, dp(4), 0, dp(12));
        box.addView(hint);

        section.input = new EditText(this);
        section.input.setHint("每行一条链接，或直接粘贴整段分享文案");
        section.input.setHintTextColor(0xFF6B7280);
        section.input.setTextColor(TEXT);
        section.input.setTextSize(14);
        section.input.setGravity(Gravity.TOP | Gravity.START);
        section.input.setMinLines(4);
        section.input.setMaxLines(8);
        section.input.setInputType(android.text.InputType.TYPE_CLASS_TEXT
                | android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        section.input.setBackground(round(CARD, dp(14), 0));
        section.input.setPadding(dp(12), dp(10), dp(12), dp(10));
        box.addView(section.input);

        LinearLayout tools = new LinearLayout(this);
        tools.setOrientation(LinearLayout.HORIZONTAL);
        tools.setGravity(Gravity.END);
        Button paste = smallButton("粘贴");
        paste.setOnClickListener(v -> pasteInto(section.input));
        Button clearInput = smallButton("清空输入");
        clearInput.setOnClickListener(v -> section.input.setText(""));
        tools.addView(paste);
        tools.addView(clearInput);
        box.addView(tools);

        section.parseButton = actionButton("自动识别并解析", ACCENT);
        section.parseButton.setOnClickListener(v -> parseSection(section));
        LinearLayout.LayoutParams parseParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(48));
        parseParams.topMargin = dp(6);
        box.addView(section.parseButton, parseParams);

        section.progress = new ProgressBar(this, null,
                android.R.attr.progressBarStyleHorizontal);
        section.progress.setIndeterminate(true);
        section.progress.setVisibility(View.GONE);
        LinearLayout.LayoutParams progressParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(5));
        progressParams.topMargin = dp(10);
        box.addView(section.progress, progressParams);

        section.status = new TextView(this);
        section.status.setText("等待链接");
        section.status.setTextColor(MUTED);
        section.status.setTextSize(12);
        section.status.setPadding(0, dp(8), 0, dp(8));
        box.addView(section.status);

        section.results = new LinearLayout(this);
        section.results.setOrientation(LinearLayout.VERTICAL);
        box.addView(section.results);

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        actions.setPadding(0, dp(6), 0, 0);
        Button selectAll = smallButton("全选");
        selectAll.setOnClickListener(v -> setAll(section, true));
        Button selectNone = smallButton("全不选");
        selectNone.setOnClickListener(v -> setAll(section, false));
        Button clear = smallButton("清空列表");
        clear.setOnClickListener(v -> {
            section.entries.clear();
            section.results.removeAllViews();
            section.status.setText("已清空");
        });
        actions.addView(selectAll, weight());
        actions.addView(selectNone, weight());
        actions.addView(clear, weight());
        box.addView(actions);

        section.downloadButton = actionButton("下载所选", ACCENT);
        section.downloadButton.setEnabled(false);
        section.downloadButton.setOnClickListener(v -> downloadSection(section));
        LinearLayout.LayoutParams downloadParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(48));
        downloadParams.topMargin = dp(8);
        box.addView(section.downloadButton, downloadParams);

        LinearLayout account = new LinearLayout(this);
        account.setOrientation(LinearLayout.HORIZONTAL);
        account.setPadding(0, dp(12), 0, 0);
        Button login = smallButton("扫码登录");
        login.setOnClickListener(v -> openLogin(section.platform));
        Button manual = smallButton("填 Cookie");
        manual.setOnClickListener(v -> manualCookie(section.platform));
        account.addView(login, weight());
        account.addView(manual, weight());
        box.addView(account);

        section.cookieState = new TextView(this);
        section.cookieState.setTextColor(MUTED);
        section.cookieState.setTextSize(11);
        section.cookieState.setPadding(0, dp(6), 0, 0);
        box.addView(section.cookieState);

        pages.add(scroll);
        content.addView(scroll, new android.widget.FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        return section;
    }

    private void parseSection(Section section) {
        List<String> urls = extractUrls(section.input.getText().toString());
        if (urls.isEmpty()) {
            toast("没有识别到链接");
            return;
        }
        section.entries.clear();
        section.results.removeAllViews();
        section.parseButton.setEnabled(false);
        section.parseButton.setText("解析中…");
        section.downloadButton.setEnabled(false);
        section.progress.setVisibility(View.VISIBLE);
        section.status.setTextColor(MUTED);
        section.status.setText("识别到 " + urls.size() + " 条链接，正在解析…");

        new Thread(() -> {
            List<Downloader.Preview> parsed = new ArrayList<>();
            List<String> errors = new ArrayList<>();
            for (String url : urls) {
                try {
                    parsed.add(Downloader.parseUrl(url,
                            prefs.getString("douyin_cookie", ""),
                            prefs.getString("bili_cookie", "")));
                } catch (Exception exc) {
                    errors.add(exc.getMessage());
                }
            }
            runOnUiThread(() -> {
                section.progress.setVisibility(View.GONE);
                section.parseButton.setEnabled(true);
                section.parseButton.setText("自动识别并解析");
                for (Downloader.Preview preview : parsed) {
                    Entry entry = new Entry(preview);
                    section.entries.add(entry);
                    section.results.addView(entryRow(section, entry));
                }
                section.downloadButton.setEnabled(!section.entries.isEmpty());
                if (parsed.isEmpty()) {
                    section.status.setTextColor(0xFFFF7B8A);
                    section.status.setText("解析失败：" + (errors.isEmpty()
                            ? "没有可用作品" : errors.get(0)));
                } else {
                    section.status.setTextColor(0xFF6EE7A8);
                    section.status.setText("解析成功 " + parsed.size() + " 条"
                            + (errors.isEmpty() ? "" : "，失败 " + errors.size() + " 条"));
                }
            });
        }).start();
    }

    private View entryRow(Section section, Entry entry) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(0, dp(9), 0, dp(9));

        ImageView thumb = new ImageView(this);
        thumb.setScaleType(ImageView.ScaleType.CENTER_CROP);
        thumb.setBackgroundColor(CARD);
        String cover = entry.preview.cover;
        if ((cover == null || cover.isEmpty())
                && !entry.preview.items.isEmpty()) {
            cover = entry.preview.items.get(0).url;
        }
        if (cover != null && !cover.isEmpty()) {
            ImageLoader.load(thumb, cover, entry.preview.referer,
                    entry.preview.cookie);
        } else {
            thumb.setImageResource(android.R.drawable.ic_menu_gallery);
        }
        if ("images".equals(entry.preview.kind)) {
            thumb.setOnClickListener(v -> showImagePicker(entry));
        }
        LinearLayout.LayoutParams thumbParams = new LinearLayout.LayoutParams(
                dp(88), dp(66));
        thumbParams.rightMargin = dp(10);
        row.addView(thumb, thumbParams);

        LinearLayout text = new LinearLayout(this);
        text.setOrientation(LinearLayout.VERTICAL);
        TextView title = new TextView(this);
        title.setText(entry.preview.title);
        title.setTextColor(TEXT);
        title.setTextSize(13);
        title.setMaxLines(2);
        TextView meta = new TextView(this);
        String kind = "images".equals(entry.preview.kind)
                ? "图集 · " + entry.preview.items.size() + " 张"
                : "视频";
        meta.setText(kind + (entry.preview.author.isEmpty()
                ? "" : " · " + entry.preview.author));
        meta.setTextColor(MUTED);
        meta.setTextSize(11);
        meta.setPadding(0, dp(3), 0, 0);
        text.addView(title);
        text.addView(meta);
        row.addView(text, new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        CheckBox box = new CheckBox(this);
        box.setChecked(entry.selected);
        box.setButtonTintList(android.content.res.ColorStateList.valueOf(ACCENT));
        box.setOnCheckedChangeListener((button, checked) -> entry.selected = checked);
        row.addView(box);
        return row;
    }

    private void showImagePicker(Entry entry) {
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(10), dp(10), dp(10), dp(6));

        TextView tip = new TextView(this);
        tip.setText("点选要下载的图片，共 " + entry.preview.items.size() + " 张");
        tip.setTextColor(MUTED);
        tip.setTextSize(12);
        tip.setPadding(0, 0, 0, dp(8));
        panel.addView(tip);

        HorizontalScrollView scroll = new HorizontalScrollView(this);
        scroll.setHorizontalScrollBarEnabled(false);
        LinearLayout images = new LinearLayout(this);
        images.setOrientation(LinearLayout.HORIZONTAL);
        scroll.addView(images);
        List<CheckBox> boxes = new ArrayList<>();
        for (int i = 0; i < entry.preview.items.size(); i++) {
            Downloader.Item item = entry.preview.items.get(i);
            LinearLayout card = new LinearLayout(this);
            card.setOrientation(LinearLayout.VERTICAL);
            card.setGravity(Gravity.CENTER_HORIZONTAL);
            card.setPadding(dp(4), 0, dp(4), 0);

            ImageView image = new ImageView(this);
            image.setScaleType(ImageView.ScaleType.CENTER_CROP);
            image.setBackgroundColor(CARD);
            ImageLoader.load(image, item.url, entry.preview.referer,
                    entry.preview.cookie);
            card.addView(image, new LinearLayout.LayoutParams(dp(132), dp(132)));

            CheckBox box = new CheckBox(this);
            box.setChecked(item.selected);
            box.setButtonTintList(android.content.res.ColorStateList.valueOf(ACCENT));
            box.setOnCheckedChangeListener((button, checked) -> item.selected = checked);
            card.addView(box);
            boxes.add(box);
            images.addView(card);
        }
        panel.addView(scroll);

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        Button all = smallButton("全选");
        all.setOnClickListener(v -> {
            for (CheckBox box : boxes) {
                box.setChecked(true);
            }
        });
        Button none = smallButton("全不选");
        none.setOnClickListener(v -> {
            for (CheckBox box : boxes) {
                box.setChecked(false);
            }
        });
        actions.addView(all, weight());
        actions.addView(none, weight());
        panel.addView(actions);

        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle(entry.preview.title)
                .setView(panel)
                .setNegativeButton("关闭", null)
                .create();
        Button download = actionButton("下载选中图片", ACCENT);
        download.setOnClickListener(v -> {
            List<Downloader.Item> selected = new ArrayList<>();
            for (Downloader.Item item : entry.preview.items) {
                if (item.selected) {
                    selected.add(item);
                }
            }
            if (selected.isEmpty()) {
                toast("至少选一张");
                return;
            }
            download.setEnabled(false);
            download.setText("保存中…");
            new Thread(() -> {
                int count = 0;
                String error = "";
                for (Downloader.Item item : selected) {
                    try {
                        Downloader.downloadImage(this, entry.preview, item);
                        count++;
                    } catch (Exception exc) {
                        error = exc.getMessage();
                    }
                }
                int finalCount = count;
                String finalError = error;
                runOnUiThread(() -> {
                    if (finalCount > 0) {
                        toast("已保存 " + finalCount + " 张到 图片/Huzhmi");
                        dialog.dismiss();
                    } else {
                        download.setEnabled(true);
                        download.setText("下载选中图片");
                        toast("保存失败：" + finalError);
                    }
                });
            }).start();
        });
        LinearLayout.LayoutParams downloadParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(44));
        downloadParams.topMargin = dp(6);
        panel.addView(download, downloadParams);
        dialog.show();
    }

    private void downloadSection(Section section) {
        List<Entry> selected = new ArrayList<>();
        for (Entry entry : section.entries) {
            if (entry.selected) {
                selected.add(entry);
            }
        }
        if (selected.isEmpty()) {
            toast("至少选一条");
            return;
        }
        section.downloadButton.setEnabled(false);
        section.downloadButton.setText("下载中…");
        section.progress.setVisibility(View.VISIBLE);
        section.status.setTextColor(MUTED);
        section.status.setText("开始下载 " + selected.size() + " 条…");

        new Thread(() -> {
            int done = 0;
            int images = 0;
            String error = "";
            Downloader.Saved[] lastVideo = new Downloader.Saved[1];
            for (Entry entry : selected) {
                try {
                    if ("images".equals(entry.preview.kind)) {
                        for (Downloader.Item item : entry.preview.items) {
                            Downloader.downloadImage(this, entry.preview, item);
                            images++;
                        }
                    } else {
                        lastVideo[0] = Downloader.downloadVideo(this,
                                entry.preview, null);
                    }
                    done++;
                } catch (Exception exc) {
                    error = exc.getMessage();
                }
            }
            int finalDone = done;
            int finalImages = images;
            String finalError = error;
            Downloader.Saved video = selected.size() == 1 ? lastVideo[0] : null;
            runOnUiThread(() -> {
                section.progress.setVisibility(View.GONE);
                section.downloadButton.setEnabled(true);
                section.downloadButton.setText("下载所选");
                if (finalDone == 0) {
                    section.status.setTextColor(0xFFFF7B8A);
                    section.status.setText("下载失败：" + finalError);
                } else {
                    section.status.setTextColor(0xFF6EE7A8);
                    section.status.setText("完成 " + finalDone + " 条"
                            + (finalImages > 0 ? "，保存 " + finalImages + " 张图片" : "")
                            + "，已存到 电影/Huzhmi 或 图片/Huzhmi");
                    if (video != null) {
                        showVideoDialog(video);
                    }
                }
            });
        }).start();
    }

    private void showVideoDialog(Downloader.Saved saved) {
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(10), dp(8), dp(10), dp(6));

        VideoView video = new VideoView(this);
        video.setVideoURI(saved.uri != null ? saved.uri : Uri.fromFile(saved.file));
        MediaController controller = new MediaController(this);
        controller.setAnchorView(video);
        video.setMediaController(controller);
        panel.addView(video, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(230)));

        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("视频预览")
                .setView(panel)
                .setNegativeButton("关闭", null)
                .create();
        Button delete = smallButton("视频不对，删掉");
        delete.setTextColor(0xFFFF7B8A);
        delete.setOnClickListener(v -> {
            video.stopPlayback();
            saved.delete(this);
            dialog.dismiss();
            toast("已删除");
        });
        LinearLayout.LayoutParams deleteParams = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        deleteParams.topMargin = dp(6);
        panel.addView(delete, deleteParams);
        dialog.setOnDismissListener(d -> video.stopPlayback());
        dialog.show();
        video.start();
    }

    private void setAll(Section section, boolean checked) {
        for (Entry entry : section.entries) {
            entry.selected = checked;
        }
        section.results.removeAllViews();
        for (Entry entry : section.entries) {
            section.results.addView(entryRow(section, entry));
        }
    }

    private List<String> extractUrls(String text) {
        Set<String> result = new LinkedHashSet<>();
        Matcher matcher = Pattern.compile("https?://[^\\s]+").matcher(
                text == null ? "" : text);
        while (matcher.find()) {
            String url = matcher.group();
            url = url.replaceAll("[),.;，。；、）】]+$", "");
            if (!url.isEmpty()) {
                result.add(url);
            }
        }
        return new ArrayList<>(result);
    }

    private void pasteInto(EditText target) {
        ClipboardManager manager = (ClipboardManager)
                getSystemService(Context.CLIPBOARD_SERVICE);
        ClipData clip = manager.getPrimaryClip();
        if (clip != null && clip.getItemCount() > 0) {
            String current = target.getText().toString();
            String pasted = clip.getItemAt(0).coerceToText(this).toString();
            target.setText(current.isEmpty() ? pasted : current + "\n" + pasted);
            target.setSelection(target.length());
        }
    }

    private void openLogin(String platform) {
        Intent intent = new Intent(this, QrLoginActivity.class);
        intent.putExtra("platform", platform);
        startActivity(intent);
    }

    private void manualCookie(String platform) {
        boolean douyin = !"bilibili".equals(platform);
        String key = douyin ? "douyin_cookie" : "bili_cookie";
        EditText edit = new EditText(this);
        edit.setText(prefs.getString(key, ""));
        edit.setHint("粘贴完整 Cookie");
        edit.setTextSize(13);
        edit.setMinLines(5);
        edit.setMaxLines(10);
        edit.setInputType(android.text.InputType.TYPE_CLASS_TEXT
                | android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        new AlertDialog.Builder(this)
                .setTitle(douyin ? "抖音 Cookie" : "B 站 Cookie")
                .setView(edit)
                .setPositiveButton("保存", (d, w) -> {
                    prefs.edit().putString(key,
                            edit.getText().toString().trim()).apply();
                    refreshCookieState();
                })
                .setNegativeButton("取消", null)
                .show();
    }

    private void refreshCookieState() {
        boolean douyin = !prefs.getString("douyin_cookie", "").isEmpty();
        boolean bili = !prefs.getString("bili_cookie", "").isEmpty();
        if (this.douyin != null && this.douyin.cookieState != null) {
            this.douyin.cookieState.setText("抖音 Cookie："
                    + (douyin ? "已保存" : "未登录"));
        }
        if (this.bilibili != null && this.bilibili.cookieState != null) {
            this.bilibili.cookieState.setText("B 站 Cookie："
                    + (bili ? "已保存" : "未登录"));
        }
    }

    private LinearLayout.LayoutParams weight() {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        params.rightMargin = dp(4);
        return params;
    }

    private Button actionButton(String text, int color) {
        Button button = new Button(this);
        button.setText(text);
        button.setTextColor(Color.WHITE);
        button.setTextSize(15);
        button.setAllCaps(false);
        button.setBackground(round(color, dp(14), 0));
        return button;
    }

    private Button smallButton(String text) {
        Button button = new Button(this);
        button.setText(text);
        button.setTextColor(TEXT);
        button.setTextSize(12);
        button.setAllCaps(false);
        button.setBackground(round(CARD, dp(11), 0xFF2A2F3A));
        return button;
    }

    private GradientDrawable round(int color, int radius, int stroke) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(radius);
        if (stroke != 0) {
            drawable.setStroke(dp(1), stroke);
        }
        return drawable;
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void toast(String message) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show();
    }
}


