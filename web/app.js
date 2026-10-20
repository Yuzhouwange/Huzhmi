/* Huzhmi 前端：轮询 /api/bootstrap，按 id 增量更新 DOM */

const $ = (id) => document.getElementById(id);
const rows = new Map(); // job_id -> {el, refs}

/* 平台由后端下发（/api/bootstrap 的 platforms），前端不再硬编码哪个平台
   对应什么链接、单页多少条。新增平台不用改这里。 */
let PLATFORMS = [];
const VIEWS = {
  douyin: { title: "抖音 / TikTok 下载", queue: "jobs", empty: "empty" },
  discover: { title: "抖音发现", queue: "discover-grid", empty: "hot-empty" },
  archive: { title: "作品档案", queue: "archive-list", empty: "archive-empty" },
  bilibili: { title: "B站下载", queue: "jobs-bili", empty: "empty-bili" },
  audio: { title: "视频转音频", queue: "jobs-audio", empty: "audio-queue-empty" },
  convert: { title: "音频转换", queue: "cv-queue", empty: "cv-empty" },
  musicdl: { title: "音乐下载", queue: "md-queue", empty: "md-queue-empty" },
  music: { title: "音乐体检", queue: "music-bad", empty: "music-summary" },
};
let currentView = "douyin";
let appConfig = null;      // 最近一次 /api/bootstrap 的 config
let appCapabilities = [];  // 转换能力（哪些可用）
const PLATFORM_FALLBACK = "douyin";

function platformInfo(name) {
  return PLATFORMS.find((item) => item.name === name) || null;
}

function platformLabel(name) {
  const info = platformInfo(name);
  return info ? info.label : name;
}

function pageSize(name) {
  const info = platformInfo(name);
  return info ? info.page_size : 50;
}

function platformOf(url) {
  for (const item of PLATFORMS) {
    try {
      if (new RegExp(item.pattern, "i").test(url || "")) return item.name;
    } catch (err) {
      // 后端给了坏正则也不能让界面挂掉
      console.warn("platform pattern invalid", item.name, err);
    }
  }
  return PLATFORM_FALLBACK;
}

function platformProfileUrl(platform, authorId) {
  const info = platformInfo(platform);
  if (!info || !info.profile_url || !authorId) return "";
  return info.profile_url.replace("{id}", authorId);
}

const STATUS_TEXT = {
  queued: "排队",
  running: "下载中",
  paused: "已暂停",
  done: "完成",
  failed: "失败",
  cancelled: "已取消",
};

async function api(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch (err) {
    throw new Error("无法连接 Huzhmi 服务，请刷新页面或重启应用");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function fmtSize(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function toast(message) {
  let el;
  if (currentView === "douyin") el = $("toast");
  else if (currentView === "bilibili") el = $("toast-bili");
  else el = $("toast-global");
  el = el || $("toast") || $("toast-global");
  if (!el) return;
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => (el.hidden = true), 6000);
}

function firstUrl(text) {
  const match = String(text).match(/https?:\/\/[A-Za-z0-9\-._~:/?#\[\]@!$&*+,;=%]+/);
  return match ? match[0].replace(/[.,;:!?)]+$/, "") : "";
}

/* ---------------- 任务列表 ---------------- */

function createRow(job, view = "douyin") {
  const el = document.createElement("div");
  el.className = "job";

  const top = document.createElement("div");
  top.className = "job-top";

  const title = document.createElement("div");
  title.className = "job-title";

  const chip = document.createElement("span");
  chip.className = "chip";

  const actions = document.createElement("div");
  actions.className = "job-actions";

  const retry = document.createElement("button");
  retry.className = "ghost small";
  retry.textContent = "重试";
  retry.onclick = async () => {
    try {
      await api(`/api/jobs/${job.id}/retry`, { method: "POST" });
      refresh();
    } catch (err) {
      toast(`重试失败：${err.message}`);
    }
  };

  const pause = document.createElement("button");
  pause.className = "ghost small";
  pause.textContent = "暂停";
  pause.onclick = async () => {
    try {
      await api(`/api/jobs/${job.id}/pause`, { method: "POST" });
      toast("已暂停，点「继续」可以从断点接着下");
      refresh();
    } catch (err) {
      toast(`暂停失败：${err.message}`);
    }
  };

  const resume = document.createElement("button");
  resume.className = "primary small";
  resume.textContent = "继续";
  resume.onclick = async () => {
    try {
      await api(`/api/jobs/${job.id}/resume`, { method: "POST" });
      refresh();
    } catch (err) {
      toast(`继续失败：${err.message}`);
    }
  };

  const extract = document.createElement("button");
  extract.className = "ghost small";
  extract.textContent = "提取音频";
  extract.onclick = async () => {
    const preset = (appConfig && appConfig.audio_preset) || "audio_m4a";
    try {
      const data = await api(`/api/jobs/${job.id}/transform`, {
        method: "POST",
        body: JSON.stringify({ op: preset }),
      });
      toast(`已加入转换队列：${data.job_id}`);
      refresh();
    } catch (err) {
      toast(`提取失败：${err.message}`);
    }
  };

  const cancel = document.createElement("button");
  cancel.className = "ghost small danger";
  cancel.textContent = "取消";
  cancel.onclick = async () => {
    try {
      await api(`/api/jobs/${job.id}/cancel`, { method: "POST" });
      toast("已取消，未下完的部分已删除");
      refresh();
    } catch (err) {
      toast(`取消失败：${err.message}`);
    }
  };

  const open = document.createElement("button");
  open.className = "ghost small";
  open.textContent = "打开目录";
  open.onclick = async () => {
    try {
      await api("/api/reveal", {
        method: "POST",
        body: JSON.stringify({ path: job.out_dir || "" }),
      });
    } catch (err) {
      toast(`打不开：${err.message}`);
    }
  };

  const author = document.createElement("button");
  author.className = "ghost small";
  author.textContent = "该作者的作品";
  author.onclick = () => {
    const url = platformProfileUrl(job.platform, job.author_id);
    if (!url) return;
    $("profile-panel").hidden = false;
    $("profile-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    loadProfile(url);
  };

  actions.append(author, extract, cancel, pause, resume, retry, open);
  top.append(title, chip, actions);

  const meta = document.createElement("div");
  meta.className = "job-meta";

  const bar = document.createElement("div");
  bar.className = "bar";
  const fill = document.createElement("i");
  bar.append(fill);

  const err = document.createElement("div");
  err.className = "err";
  err.hidden = true;

  el.append(top, meta, bar, err);
  return { el, view, title, chip, meta, bar, fill, err, retry, extract, cancel,
           pause, resume, open, author };
}

function updateRow(refs, job) {
  refs.title.textContent = job.title || job.url;
  refs.title.title = job.url;

  refs.chip.textContent = STATUS_TEXT[job.status] || job.status;
  refs.chip.className = `chip ${job.status}`;

  const parts = [];
  if (job.platform) parts.push(platformLabel(job.platform));
  if (job.author) parts.push(job.author);
  if (job.quality) parts.push(job.quality);
  if (job.total_assets) parts.push(`${job.done_assets}/${job.total_assets} 个文件`);
  if (job.bytes_done) parts.push(fmtSize(job.bytes_done));
  if (job.out_dir) parts.push(job.out_dir);
  refs.meta.textContent = parts.join("  ·  ");

  // 优先按真实字节算进度（DASH 合成任务只有一个输出文件，按文件数会一直 0%）
  const total = job.total_assets || 0;
  const done = job.done_assets || 0;
  let pct = 0;
  if (job.status === "done") {
    pct = 100;
  } else if (job.bytes_total > 0) {
    pct = Math.min(99, ((job.bytes_done || 0) / job.bytes_total) * 100);
  } else if (total) {
    pct = Math.min(99, (done / total) * 100);
  }
  refs.fill.style.width = `${pct}%`;
  refs.bar.className = `bar ${job.status === "done" ? "done" : ""}${
    job.status === "failed" ? " failed" : ""
  }`;

  // 合成 / 降级阶段单独显示，避免用户以为卡住
  const stage = job.status === "running" && job.stage ? job.stage : "";
  const showError = job.status === "failed" && job.error;
  const showStage = !showError && stage;
  refs.err.hidden = !showError && !showStage;
  refs.err.textContent = showError ? job.error : stage;
  refs.err.classList.toggle("stage", Boolean(showStage));

  refs.retry.style.display =
    job.status === "failed" || job.status === "cancelled" ? "" : "none";
  refs.extract.style.display =
    job.status === "done" && job.done_assets > 0 ? "" : "none";
  refs.cancel.style.display =
    ["queued", "running", "paused"].includes(job.status) ? "" : "none";
  refs.pause.style.display = job.status === "running" ? "" : "none";
  refs.resume.style.display = job.status === "paused" ? "" : "none";
  refs.open.style.display = job.out_dir ? "" : "none";
  refs.author.style.display = job.author_id ? "" : "none";
}

function switchView(name) {
  if (!VIEWS[name]) return;
  currentView = name;
  on("btn-music-convert", async () => {
    const root = ($("music-path").value || "").trim();
    if (!root) {
      toast("先填一个目录");
      return;
    }
    const format = $("music-format").value;
    const button = $("btn-music-convert");
    button.disabled = true;
    try {
      const plan = await api("/api/music/plan", {
        method: "POST",
        body: JSON.stringify({ path: root, format }),
      });
      renderAlbums(plan);
      const started = await api("/api/music/convert", {
        method: "POST",
        body: JSON.stringify({ path: root, format }),
      });
      toast(`专辑 ${plan.albums.length} 张，已提交 ${started.count} 首（共 ${plan.total}，坏 ${plan.broken}）`);
      refresh();
    } catch (err) {
      toast(`转换失败：${err.message}`);
    } finally {
      button.disabled = false;
    }
  });
  on("btn-music-scan", async () => {
    const root = ($("music-path").value || "").trim();
    if (!root) {
      toast("先填一个目录");
      return;
    }
    const button = $("btn-music-scan");
    button.disabled = true;
    try {
      const data = await api("/api/music/scan", {
        method: "POST",
        body: JSON.stringify({ path: root }),
      });
      renderMusic(data);
      toast(`扫完：${data.total} 个文件，${data.broken} 个有问题`);
    } catch (err) {
      toast(`扫描失败：${err.message}`);
    } finally {
      button.disabled = false;
    }
  });
  $("audio-format")?.addEventListener("change", async (event) => {
    const op = event.target.value;
    try {
      await api("/api/config", {
        method: "POST",
        body: JSON.stringify({ audio_preset: op }),
      });
      if (appConfig) appConfig.audio_preset = op;
      toast("已切换提取格式");
      refresh();
    } catch (err) {
      toast(`保存失败：${err.message}`);
    }
  });
  for (const button of document.querySelectorAll(".nav-item")) {
    button.classList.toggle("active", button.dataset.view === name);
  }
  for (const panel of document.querySelectorAll("[data-view-panel]")) {
    panel.hidden = panel.dataset.viewPanel !== name;
  }
  $("view-title").textContent = VIEWS[name].title;
  if (latestJobs.length) render(latestJobs);
  if (name === "audio") renderAudio(latestJobs);
  if (name === "musicdl") {
    mdFillRanks();
    mdUpdateCollectionHint();
    if (mdState.items.length) mdRenderResults();
    mdLibLoad();
  }
  if (name === "discover") loadHot();
  if (name === "archive") archiveLoad(false);
}

function isTransform(job) {
  return String(job.url || "").startsWith("transform:");
}

function extractAudio(jobId) {
  const preset = (appConfig && appConfig.audio_preset) || "audio_m4a";
  return api(`/api/jobs/${jobId}/transform`, {
    method: "POST",
    body: JSON.stringify({ op: preset }),
  });
}

let latestJobs = [];

function renderAudio(jobs) {
  const sources = jobs.filter(
    (job) => !isTransform(job) && job.status === "done" && job.done_assets > 0
  );
  const box = $("audio-sources");
  if (!box) return;
  box.innerHTML = "";

  for (const job of sources) {
    const row = document.createElement("div");
    row.className = "job";

    const top = document.createElement("div");
    top.className = "job-top";
    const title = document.createElement("div");
    title.className = "job-title";
    title.textContent = job.title || job.url;
    const actions = document.createElement("div");
    actions.className = "job-actions";

    const meta = document.createElement("button");
    meta.className = "ghost small";
    meta.textContent = "打开目录";
    meta.onclick = async () => {
      try {
        await api("/api/reveal", {
          method: "POST",
          body: JSON.stringify({ path: job.out_dir || "" }),
        });
      } catch (err) {
        toast(`打不开：${err.message}`);
      }
    };

    const go = document.createElement("button");
    go.className = "primary small";
    go.textContent = "提取音频";
    go.onclick = async () => {
      try {
        const data = await extractAudio(job.id);
        toast(`已加入转换队列：${data.job_id}`);
        refresh();
      } catch (err) {
        toast(`提取失败：${err.message}`);
      }
    };

    actions.append(meta, go);
    top.append(title, actions);

    const sub = document.createElement("div");
    sub.className = "job-meta";
    sub.textContent = [platformLabel(job.platform), job.quality, job.out_dir]
      .filter(Boolean).join("  ·  ");

    row.append(top, sub);
    box.append(row);
  }

  const label = (appConfig && appConfig.audio_preset) || "audio_m4a";
  $("audio-empty").hidden = sources.length > 0;
  const all = $("btn-audio-all");
  all.textContent = `全部提取 (${sources.length})`;
  all.disabled = sources.length === 0;
  all.onclick = async () => {
    try {
      let queued = 0;
      for (const job of sources) {
        try {
          await extractAudio(job.id);
          queued += 1;
        } catch (err) {
          console.warn("提取失败", job.id, err);
        }
      }
      toast(`已提交 ${queued} 个提取任务`);
      refresh();
    } catch (err) {
      toast(`批量提取失败：${err.message}`);
    }
  };
  const select = $("audio-format");
  if (select && !select.dataset.filled && appCapabilities.length) {
    select.innerHTML = "";
    for (const cap of appCapabilities) {
      const option = document.createElement("option");
      option.value = cap.name;
      option.textContent = cap.available ? cap.label : `${cap.label}（${cap.reason}）`;
      option.disabled = !cap.available;
      select.append(option);
    }
    select.dataset.filled = "1";
  }
  if (select && appConfig) select.value = appConfig.audio_preset || "audio_m4a";

  $("audio-hint").textContent = label.startsWith("audio_m4a")
    ? "当前格式：无损 m4a（不重编码、不需要 ffmpeg）"
    : "当前格式需要 ffmpeg：设置里填 ffmpeg.exe 的路径，或把它加进 PATH";
}

function renderAlbums(plan) {
  const box = $("music-albums");
  if (!box) return;
  box.innerHTML = "";
  for (const album of plan.albums || []) {
    const row = document.createElement("div");
    row.className = "job";
    const top = document.createElement("div");
    top.className = "job-top";
    const title = document.createElement("div");
    title.className = "job-title";
    title.textContent = `${album.artist} · ${album.album}`;
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = `${album.count} 首`;
    top.append(title, chip);
    const meta = document.createElement("div");
    meta.className = "job-meta";
    const first = album.tracks[0] || {};
    meta.textContent = first.source ? first.source.replace(/[^\\/]+$/, "") : "";
    row.append(top, meta);
    box.append(row);
  }
}

function renderMusic(result) {
  const box = $("music-bad");
  if (!box) return;
  box.innerHTML = "";
  const kinds = Object.entries(result.kinds || {})
    .map(([kind, count]) => `${kind} ${count}`).join(" · ");
  $("music-summary").textContent =
    `共 ${result.total} 个：正常 ${result.ok}，有问题 ${result.broken}` +
    (kinds ? `（${kinds}）` : "");

  for (const item of result.bad_files || []) {
    const row = document.createElement("div");
    row.className = "job";
    const top = document.createElement("div");
    top.className = "job-top";
    const title = document.createElement("div");
    title.className = "job-title";
    title.textContent = item.path.split(/[\\/]/).pop();
    title.title = item.path;
    const chip = document.createElement("span");
    chip.className = "chip failed";
    chip.textContent = item.kind === "html" ? "错误页" : "损坏";
    top.append(title, chip);

    const meta = document.createElement("div");
    meta.className = "job-meta";
    meta.textContent = `${item.reason}  ·  ${Math.round(item.size / 1024)} KB`;
    row.append(top, meta);
    box.append(row);
  }
  $("music-summary").hidden = false;
}

function render(jobs) {
  latestJobs = jobs;

  const buckets = { douyin: [], bilibili: [], audio: [] };
  for (const job of jobs) {
    if (isTransform(job)) buckets.audio.push(job);
    else if (job.platform === "bilibili") buckets.bilibili.push(job);
    else buckets.douyin.push(job);
  }

  for (const [view, list] of Object.entries(buckets)) {
    const container = $(VIEWS[view].queue);
    if (!container) continue;

    const ids = new Set(list.map((job) => job.id));
    for (const [id, refs] of rows) {
      if (refs.view === view && !ids.has(id)) {
        refs.el.remove();
        rows.delete(id);
      }
    }

    const ordered = [];
    for (const job of list) {
      let refs = rows.get(job.id);
      if (!refs) {
        refs = createRow(job, view);
        rows.set(job.id, refs);
      }
      updateRow(refs, job);
      ordered.push(refs.el);
    }

    ordered.forEach((el, index) => {
      if (container.children[index] !== el) {
        container.insertBefore(el, container.children[index] || null);
      }
    });

    const empty = $(VIEWS[view].empty);
    if (empty) empty.hidden = list.length > 0;
  }

  renderAudio(jobs);
}

/* ---------------- 主页多选 ---------------- */

let profile = null; // {sec_uid, nickname, items, cursor, has_more}
const selected = new Set();

function updateSelectCount() {
  const button = $("btn-download-selected");
  button.textContent = `下载所选 (${selected.size})`;
  button.disabled = selected.size === 0;
}

function profileFiltered() {
  const items = (profile && profile.items) || [];
  const from = $("profile-date-from")?.value || "";
  const to = $("profile-date-to")?.value || "";
  if (!from && !to) return items;
  const start = from ? new Date(`${from}T00:00:00`).getTime() / 1000 : 0;
  const end = to ? new Date(`${to}T23:59:59`).getTime() / 1000 : Infinity;
  return items.filter((item) => {
    if (!item.create_time) return true;
    return item.create_time >= start && item.create_time <= end;
  });
}

function renderProfile() {
  const grid = $("profile-grid");
  grid.innerHTML = "";

  const items = profileFiltered();
  const total = profile.total || items.length;
  $("profile-title").textContent =
    `${profile.nickname || platformLabel(profile.platform)} · ` +
    `${items.length}${total > items.length ? ` / 共 ${total}` : ""} 个作品`;

  for (const item of items) {
    const card = document.createElement("div");
    card.className = "card-item" + (selected.has(item.aweme_id) ? " selected" : "");
    card.dataset.id = item.aweme_id;

    if (item.cover) {
      const img = document.createElement("img");
      img.loading = "lazy";
      // 走本地代理：B 站图床有防盗链，直连会被 403
      img.src = `/api/thumb?url=${encodeURIComponent(item.cover)}`;
      img.alt = "";
      img.onerror = () => {
        img.replaceWith(placeholder());
      };
      card.append(img);
    } else {
      card.append(placeholder());
    }

    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = item.kind === "images" ? "图集" : "视频";
    card.append(badge);

    const tick = document.createElement("span");
    tick.className = "tick";
    tick.textContent = "✓";
    card.append(tick);

    const cap = document.createElement("div");
    cap.className = "cap";
    cap.textContent = item.desc || item.aweme_id;
    card.append(cap);

    card.onclick = () => {
      if (selected.has(item.aweme_id)) {
        selected.delete(item.aweme_id);
        card.classList.remove("selected");
      } else {
        selected.add(item.aweme_id);
        card.classList.add("selected");
      }
      updateSelectCount();
    };

    grid.append(card);
  }

  const blocked = Boolean(profile.needs_login);
  $("btn-load-more").hidden = blocked || !(profile.has_more && profile.cursor);
  $("btn-profile-login").hidden = !blocked;

  let hint = `已加载 ${items.length} 个作品`;
  if (blocked) {
    hint += profile.total ? `（共 ${profile.total} 个）` : "";
    hint += profile.has_cookie
      ? " · 登录状态可能已过期：抖音只给了第一页，重新扫码登录后可加载全部"
      : " · 抖音只对未登录用户显示第一页，扫码登录后才能加载全部；也可以先把这 " +
        `${items.length} 条下走`;
  } else if (profile.has_more) {
    hint += "，还有更多";
  }
  $("profile-hint").textContent = `${hint} · 点封面多选，可只下其中几条`;
  updateSelectCount();
}

function placeholder() {
  const div = document.createElement("div");
  div.className = "placeholder";
  div.textContent = "无封面";
  return div;
}

async function loadProfile(url) {
  const platform = platformOf(url);
  $("profile-panel").hidden = false;
  // 面板在页面上方，滚上来免得用户以为「点了没反应」
  $("profile-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  $("profile-hint").textContent = `正在读取 ${platformLabel(platform)} 主页…`;
  $("profile-grid").innerHTML = "";
  selected.clear();
  updateSelectCount();

  try {
    const data = await api("/api/profile", {
      method: "POST",
      body: JSON.stringify({
        url,
        platform,
        count: pageSize(platform),
      }),
    });
    profile = {
      sec_uid: data.profile.sec_uid,
      nickname: data.profile.nickname,
      platform: data.profile.platform || platform,
      items: data.items || [],
      cursor: data.max_cursor,
      has_more: data.has_more,
      total: data.profile.total_works || data.total || 0,
      needs_login: Boolean(data.profile.needs_login || data.needs_login),
      has_cookie: Boolean(data.profile.has_cookie),
    };
    renderProfile();
  } catch (err) {
    $("profile-hint").textContent = `读取失败：${err.message}`;
  }
}

async function loadMore() {
  if (!profile || !profile.cursor) return;
  const button = $("btn-load-more");
  button.disabled = true;
  button.textContent = "加载中…";
  try {
    const data = await api("/api/profile/more", {
      method: "POST",
      body: JSON.stringify({
        sec_uid: profile.sec_uid,
        cursor: profile.cursor,
        count: pageSize(profile.platform),
        platform: profile.platform,
      }),
    });
    const known = new Set(profile.items.map((i) => i.aweme_id));
    for (const item of data.items || []) {
      if (!known.has(item.aweme_id)) profile.items.push(item);
    }
    profile.cursor = data.max_cursor;
    profile.has_more = data.has_more;
    // 翻到底了但抖音不给更多：记下来，界面改成「登录后加载全部」
    if (data.needs_login && !(data.items || []).length) {
      profile.needs_login = true;
    }
    renderProfile();
  } catch (err) {
    toast(`加载更多失败：${err.message}`);
  } finally {
    button.disabled = false;
    button.textContent = "加载更多";
  }
}

async function downloadSelected() {
  if (!profile || selected.size === 0) return;
  const button = $("btn-download-selected");
  button.disabled = true;
  const chosen = (profile.items || []).filter((i) => selected.has(i.aweme_id));
  try {
    const data = await api("/api/select", {
      method: "POST",
      body: JSON.stringify({
        items: chosen,
        out_dir: $("profile-path").value.trim(),
        platform: profile.platform,
      }),
    });
    const queued = (data.results || []).filter((r) => r.status === "queued").length;
    const skipped = (data.results || []).filter((r) => r.status === "skipped").length;
    toast(`已加入 ${queued} 个${skipped ? `，跳过 ${skipped} 个已下载` : ""}`);
    selected.clear();
    renderProfile();
    refresh();
  } catch (err) {
    toast(`提交失败：${err.message}`);
  } finally {
    button.disabled = selected.size === 0;
    updateSelectCount();
  }
}

/* ---------------- 抖音发现：热榜 + 搜索 ---------------- */

const discoverState = {
  items: [], selected: new Set(), keyword: "",
  hotLoaded: false, cursor: 0, hasMore: false,
};

function fmtHeat(value) {
  const number = Number(value || 0);
  if (number >= 10000) return `${(number / 10000).toFixed(1)} 万`;
  return String(number);
}

async function loadHot(force = false) {
  if (discoverState.hotLoaded && !force) return;
  const box = $("hot-list");
  const empty = $("hot-empty");
  box.innerHTML = "";
  empty.hidden = false;
  empty.textContent = "正在读取热榜…";
  try {
    const data = await api("/api/discover/hot?limit=50");
    const rows = data.items || [];
    discoverState.hotLoaded = true;
    rows.forEach((row, index) => {
      const el = document.createElement("div");
      el.className = "hot-row";
      const rank = document.createElement("div");
      rank.className = "hot-rank" + (index < 3 ? " top" : "");
      rank.textContent = String(index + 1);
      const word = document.createElement("div");
      word.className = "hot-word";
      word.textContent = row.word || "";
      word.title = row.word || "";
      const heat = document.createElement("div");
      heat.className = "hot-heat";
      heat.textContent = fmtHeat(row.hot_value);
      const go = document.createElement("button");
      go.className = "ghost small";
      go.textContent = "搜";
      go.onclick = () => {
        $("discover-keyword").value = row.word || "";
        discoverSearch();
      };
      el.append(rank, word, heat, go);
      box.append(el);
    });
    empty.hidden = rows.length > 0;
    if (!rows.length) empty.textContent = "热榜暂时没有数据";
  } catch (err) {
    empty.hidden = false;
    empty.textContent = `热榜加载失败：${err.message}`;
  }
}

async function discoverSearch() {
  const keyword = ($("discover-keyword").value || "").trim();
  if (!keyword) {
    toast("先填搜索词");
    return;
  }
  const button = $("btn-discover-search");
  button.disabled = true;
  $("discover-hint").textContent = "搜索中（需要登录态）…";
  try {
    const data = await api("/api/discover/search", {
      method: "POST",
      body: JSON.stringify({ keyword, count: 30 }),
    });
    discoverState.items = data.items || [];
    discoverState.selected.clear();
    discoverState.keyword = keyword;
    discoverState.cursor = Number(data.cursor || 0);
    discoverState.hasMore = Boolean(data.has_more);
    renderDiscover();
    const count = discoverState.items.length;
    $("discover-hint").textContent = count
      ? `“${keyword}” 找到 ${count} 个作品 · 点封面多选`
      : `“${keyword}” 没有结果`;
    $("discover-hint").textContent += data.has_more ? "，还有更多" : "";
  } catch (err) {
    discoverState.items = [];
    discoverState.selected.clear();
    discoverState.cursor = 0;
    discoverState.hasMore = false;
    renderDiscover();
    $("discover-hint").textContent =
      `${err.message}。搜索接口要求登录：点上面「扫码登录」后再试`;
    toast(`搜索失败：${err.message}`);
  } finally {
    button.disabled = false;
  }
}

async function discoverMore() {
  if (!discoverState.keyword || !discoverState.hasMore) return;
  const button = $("btn-discover-more");
  button.disabled = true;
  try {
    const data = await api("/api/discover/search", {
      method: "POST",
      body: JSON.stringify({
        keyword: discoverState.keyword,
        offset: discoverState.cursor,
        count: 30,
      }),
    });
    const seen = new Set(discoverState.items.map((item) => item.aweme_id));
    for (const item of data.items || []) {
      if (!seen.has(item.aweme_id)) {
        discoverState.items.push(item);
        seen.add(item.aweme_id);
      }
    }
    discoverState.cursor = Number(data.cursor || 0);
    discoverState.hasMore = Boolean(data.has_more);
    renderDiscover();
    $("discover-hint").textContent =
      `“${discoverState.keyword}” 已加载 ${discoverState.items.length} 个作品`;
  } catch (err) {
    toast(`加载更多失败：${err.message}`);
  } finally {
    button.disabled = false;
  }
}

function updateDiscoverCount() {
  const button = $("btn-discover-download");
  if (!button) return;
  button.textContent = `下载所选 (${discoverState.selected.size})`;
  button.disabled = discoverState.selected.size === 0;
}

function renderDiscover() {
  const grid = $("discover-grid");
  if (!grid) return;
  grid.innerHTML = "";
  for (const item of discoverState.items) {
    const card = document.createElement("div");
    card.className = "card-item" +
      (discoverState.selected.has(item.aweme_id) ? " selected" : "");
    if (item.cover) {
      const img = document.createElement("img");
      img.loading = "lazy";
      img.src = `/api/thumb?url=${encodeURIComponent(item.cover)}`;
      img.alt = "";
      img.onerror = () => img.replaceWith(placeholder());
      card.append(img);
    } else {
      card.append(placeholder());
    }
    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = item.kind === "images" ? "图集" : "视频";
    card.append(badge);
    const tick = document.createElement("span");
    tick.className = "tick";
    tick.textContent = "✓";
    card.append(tick);
    const cap = document.createElement("div");
    cap.className = "cap";
    cap.textContent = item.desc || item.aweme_id;
    card.append(cap);
    card.onclick = () => {
      if (discoverState.selected.has(item.aweme_id)) {
        discoverState.selected.delete(item.aweme_id);
        card.classList.remove("selected");
      } else {
        discoverState.selected.add(item.aweme_id);
        card.classList.add("selected");
      }
      updateDiscoverCount();
    };
    grid.append(card);
  }
  updateDiscoverCount();
  const more = $("btn-discover-more");
  if (more) more.hidden = !discoverState.hasMore;
}

async function downloadDiscover() {
  const items = discoverState.items.filter((item) =>
    discoverState.selected.has(item.aweme_id));
  if (!items.length) return;
  try {
    const data = await api("/api/select", {
      method: "POST",
      body: JSON.stringify({ items, platform: "douyin" }),
    });
    const queued = (data.results || []).filter((row) => row.status === "queued").length;
    toast(`已加入 ${queued} 个作品`);
    discoverState.selected.clear();
    renderDiscover();
    refresh();
  } catch (err) {
    toast(`提交失败：${err.message}`);
  }
}

/* ---------------- 本地作品档案 ---------------- */

const archiveState = {
  items: [], loaded: false, timer: null, offset: 0, hasMore: false, query: "",
};

async function archiveLoad(more = false) {
  const q = ($("archive-search")?.value || "").trim();
  try {
    if (!more || q !== archiveState.query) {
      archiveState.query = q;
      archiveState.offset = 0;
      archiveState.items = [];
    }
    const data = await api(
      `/api/archive?q=${encodeURIComponent(q)}&limit=200&offset=${archiveState.offset}`);
    archiveState.items = archiveState.items.concat(data.items || []);
    archiveState.offset += (data.items || []).length;
    archiveState.hasMore = Boolean(data.has_more);
    archiveState.loaded = true;
    $("archive-hint").textContent =
      `${data.root} · 共 ${data.total} 个文件，已显示 ${archiveState.items.length}` +
      (q ? ` · 筛选“${q}”` : "");
    renderArchive();
  } catch (err) {
    toast(`扫描失败：${err.message}`);
  }
}

function renderArchive() {
  const box = $("archive-list");
  if (!box) return;
  box.innerHTML = "";
  for (const item of archiveState.items) {
    const row = document.createElement("div");
    row.className = "archive-row";

    if (item.kind === "image") {
      const img = document.createElement("img");
      img.className = "archive-thumb";
      img.loading = "lazy";
      img.src = `/api/archive/file?path=${encodeURIComponent(item.path)}`;
      img.alt = "";
      row.append(img);
    } else {
      const icon = document.createElement("div");
      icon.className = "archive-thumb";
      icon.textContent = item.ext.toUpperCase();
      row.append(icon);
    }

    const info = document.createElement("div");
    const title = document.createElement("div");
    title.className = "archive-title";
    title.textContent = item.title || item.name;
    title.title = item.path;
    const meta = document.createElement("div");
    meta.className = "archive-meta";
    meta.textContent = [item.parent, new Date(item.mtime * 1000).toLocaleDateString()]
      .filter(Boolean).join(" · ");
    info.append(title, meta);

    const size = document.createElement("div");
    size.className = "archive-meta";
    size.textContent = `${item.kind === "video" ? "视频" : "图片"} · ${fmtSize(item.size)}`;

    const actions = document.createElement("div");
    actions.className = "archive-actions";
    const play = document.createElement("button");
    play.className = "ghost small";
    play.textContent = item.kind === "video" ? "播放" : "预览";
    play.onclick = () => archivePreview(item);
    const open = document.createElement("button");
    open.className = "ghost small";
    open.textContent = "打开";
    open.onclick = async () => {
      try {
        await api("/api/reveal", { method: "POST", body: JSON.stringify({ path: item.path }) });
      } catch (err) {
        toast(`打不开：${err.message}`);
      }
    };
    const remove = document.createElement("button");
    remove.className = "ghost small danger";
    remove.textContent = "删除";
    remove.onclick = () => archiveDelete(item);
    actions.append(play, open, remove);

    row.append(info, size, actions);
    box.append(row);
  }
  const more = $("archive-more");
  if (more) more.hidden = !archiveState.hasMore;
  $("archive-empty").hidden = archiveState.items.length > 0;
}

function archivePreview(item) {
  const modal = $("archive-modal");
  const body = $("archive-body");
  $("archive-title").textContent = item.title || item.name;
  body.innerHTML = "";
  if (item.kind === "video") {
    const video = document.createElement("video");
    video.controls = true;
    video.autoplay = true;
    video.src = `/api/archive/file?path=${encodeURIComponent(item.path)}`;
    body.append(video);
  } else {
    const img = document.createElement("img");
    img.src = `/api/archive/file?path=${encodeURIComponent(item.path)}`;
    img.alt = "";
    body.append(img);
  }
  modal.hidden = false;
}

function closeArchive() {
  $("archive-body").innerHTML = "";
  $("archive-modal").hidden = true;
}

async function archiveDelete(item) {
  if (!window.confirm(`删除文件？\n${item.path}`)) return;
  try {
    await api("/api/archive/delete", {
      method: "POST",
      body: JSON.stringify({ path: item.path }),
    });
    toast("已删除");
    archiveLoad(false);
  } catch (err) {
    toast(`删除失败：${err.message}`);
  }
}

/* ---------------- 选集（分 P / 合集） ---------------- */

let episodeData = null; // {title, author, current, sourceUrl, episodes:[{key,label,duration,url}]}
const episodeSelected = new Set();

function fmtDuration(seconds) {
  const total = Number(seconds) || 0;
  if (!total) return "";
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

function updateEpisodeButton() {
  const button = $("btn-episodes-download");
  button.textContent = `下载所选 (${episodeSelected.size})`;
  button.disabled = episodeSelected.size === 0;
}

function renderEpisodes() {
  const list = $("episodes-list");
  list.innerHTML = "";

  for (const ep of episodeData.episodes) {
    const chosen = episodeSelected.has(ep.key);
    const row = document.createElement("div");
    row.className = "episode" + (chosen ? " selected" : "");

    const box = document.createElement("span");
    box.className = "box";
    box.textContent = chosen ? "✓" : "";

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = ep.label || ep.key;
    name.title = ep.label || ep.key;

    row.append(box, name);
    if (ep.index === episodeData.current) {
      const tag = document.createElement("span");
      tag.className = "current";
      tag.textContent = "当前";
      row.append(tag);
    }
    const duration = fmtDuration(ep.duration);
    if (duration) {
      const cell = document.createElement("span");
      cell.className = "dur";
      cell.textContent = duration;
      row.append(cell);
    }

    row.onclick = () => {
      if (episodeSelected.has(ep.key)) episodeSelected.delete(ep.key);
      else episodeSelected.add(ep.key);
      renderEpisodes();
    };
    list.append(row);
  }

  const source = { bangumi: "番剧", season: "合集" }[episodeData.source] || "分 P";
  $("episodes-hint").textContent =
    `${source} · 共 ${episodeData.episodes.length} 集 · 默认只勾了当前这一集，` +
    `想整部下就点「全选」再「下载所选」`;
  updateEpisodeButton();
}

async function loadEpisodes(url) {
  try {
    const data = await api("/api/episodes", {
      method: "POST",
      body: JSON.stringify({ url }),
    });
    if (!data.has_multi) return false;

    episodeData = { ...data, sourceUrl: url };
    episodeSelected.clear();
    const current = (data.episodes || []).find((ep) => ep.index === data.current);
    if (current) episodeSelected.add(current.key);

    $("episodes-panel").hidden = false;
    $("episodes-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    $("episodes-title").textContent =
      `${data.title || "选集"}${data.author ? ` · ${data.author}` : ""} · ` +
      `${data.episodes.length} 集`;
    renderEpisodes();
    return true;
  } catch (err) {
    // 选集识别失败：说明原因再退回单条，别让人以为「这视频没有选集」
    console.warn("选集识别失败，按单条处理", err);
    toast(`选集识别失败：${err.message}（先按单条下载）`);
    return false;
  }
}

function closeEpisodes() {
  $("episodes-panel").hidden = true;
  episodeData = null;
  episodeSelected.clear();
}

async function submitUrls(urls, label) {
  if (!urls.length) return;
  try {
    const data = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({ text: urls.join("\n") }),
    });
    const results = data.results || [];
    const queued = results.filter((r) => r.status === "queued").length;
    const skipped = results.filter((r) => r.status === "skipped").length;
    toast(`${label}：已加入 ${queued} 个${skipped ? `，跳过 ${skipped} 个已下载` : ""}`);
    refresh();
  } catch (err) {
    toast(`提交失败：${err.message}`);
  }
}

async function downloadEpisodes() {
  if (!episodeData || episodeSelected.size === 0) return;
  const chosen = episodeData.episodes.filter((ep) => episodeSelected.has(ep.key));
  await submitUrls(chosen.map((ep) => ep.url), `已选 ${chosen.length} 集`);
  closeEpisodes();
}

async function downloadCurrentEpisode() {
  if (!episodeData) return;
  await submitUrls([episodeData.sourceUrl], "当前这一集");
  closeEpisodes();
}

/* ---------------- 队列暂停 ---------------- */

let queuePaused = false;

function updateQueueButton() {
  // 顶栏和队列标题栏两个入口，状态同步
  for (const id of ["btn-queue-pause", "btn-global-pause"]) {
    const button = $(id);
    if (button) button.textContent = queuePaused ? "继续全部" : "暂停全部";
  }
}
async function toggleQueue() {
  try {
    const data = await api("/api/queue", {
      method: "POST",
      body: JSON.stringify({ paused: !queuePaused }),
    });
    queuePaused = Boolean(data.paused);
    updateQueueButton();
    toast(queuePaused
      ? "已暂停队列：正在下载的也停了，点「继续队列」接着下"
      : "队列已继续");
    refresh();
  } catch (err) {
    toast(`操作失败：${err.message}`);
  }
}

/* ---------------- 音乐下载 ---------------- */

const mdState = {
  items: [],
  selected: new Set(),
  title: "搜索结果",
  sub: "",
  cover: "",
  provider: "netease",
  queue: [],
  providers: [],
  playlist: [],
  playIndex: -1,
  mode: "list",
};

function mdKey(item) {
  return `${item.provider}:${item.id}`;
}

function mdProviderLabel(name) {
  const found = mdState.providers.find((p) => p.name === name);
  return found ? found.label : name;
}

function mdFillRanks() {
  const rank = $("md-rank");
  const select = $("md-provider");
  if (!rank || !select) return;
  const info = mdState.providers.find((p) => p.name === select.value);
  const current = rank.value;
  rank.innerHTML = '<option value="">排行榜…</option>';
  for (const row of (info && info.ranks) || []) {
    const option = document.createElement("option");
    option.value = row.id;
    option.textContent = row.label;
    rank.append(option);
  }
  if ([...rank.options].some((option) => option.value === current)) rank.value = current;
}

function mdInitProviders(list) {
  if (!Array.isArray(list) || !list.length) return;
  mdState.providers = list;
  const select = $("md-provider");
  if (!select) return;
  const signature = list.map((p) => p.name).join(",");
  if (select.dataset.filled !== signature) {
    select.innerHTML = "";
    for (const provider of list) {
      const option = document.createElement("option");
      option.value = provider.name;
      option.textContent = provider.label;
      select.append(option);
    }
    select.dataset.filled = signature;
    select.value = mdState.provider;
    select.onchange = () => {
      mdState.provider = select.value;
      mdFillRanks();
      mdUpdateCollectionHint();
    };
  }
  mdFillRanks();
}

function mdUpdateCollectionHint() {
  const select = $("md-provider");
  const kind = $("md-kind");
  const button = $("md-load-collection");
  const hint = $("md-hint");
  if (!select || !kind || !button) return;
  if (hint && !hint.dataset.default) hint.dataset.default = hint.textContent;
  const info = mdState.providers.find((p) => p.name === select.value);
  const supported = info ? (kind.value === "album" ? info.album : info.playlist) : true;
  button.disabled = !supported;
  if (hint) {
    hint.textContent = supported
      ? hint.dataset.default
      : `${info.label} 暂不提供专辑/歌单读取（搜索、歌词、下载可用）；老接口需要网页会话，不硬凑`;
  }
}

function mdRenderResults() {
  const card = $("md-result-card");
  if (!card) return;
  card.hidden = false;
  $("md-title").textContent = mdState.title;
  $("md-sub").textContent = mdState.sub;
  const cover = $("md-cover");
  if (mdState.cover) {
    cover.src = mdState.cover;
    cover.hidden = false;
  } else {
    cover.hidden = true;
    cover.removeAttribute("src");
  }

  const list = $("md-list");
  list.innerHTML = "";
  const head = document.createElement("div");
  head.className = "md-track head";
  head.innerHTML = "<span></span><span>歌曲</span><span>歌手</span>"
    + "<span class='md-album'>专辑</span><span>时长</span><span></span>";
  list.append(head);

  for (const item of mdState.items) {
    const row = document.createElement("div");
    row.className = "md-track";
    const key = mdKey(item);
    if (mdState.selected.has(key)) row.classList.add("selected");

    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = mdState.selected.has(key);
    check.onchange = () => {
      if (check.checked) mdState.selected.add(key);
      else mdState.selected.delete(key);
      row.classList.toggle("selected", check.checked);
      mdUpdateDownloadCount();
    };

    const name = document.createElement("div");
    name.className = "md-name";
    name.textContent = item.title || "未命名";
    name.title = item.title || "";
    if (item.available === false) {
      const badge = document.createElement("span");
      badge.className = "md-badge bad";
      badge.textContent = "无版权";
      name.append(" ", badge);
    }

    const artist = document.createElement("div");
    artist.className = "md-dim";
    artist.textContent = item.artist || "";

    const album = document.createElement("div");
    album.className = "md-dim md-album";
    album.textContent = item.album || "";

    const duration = document.createElement("div");
    duration.className = "md-dim";
    duration.textContent = item.duration ? mdFormatTime(item.duration) : "-";

    const actions = document.createElement("div");
    actions.className = "md-actions";
    const lyrics = document.createElement("button");
    lyrics.className = "ghost small";
    lyrics.textContent = "歌词";
    lyrics.onclick = () => mdShowLyrics(item);
    const comments = document.createElement("button");
    comments.className = "ghost small";
    comments.textContent = "评论";
    comments.onclick = () => mdShowComments(item);
    const single = document.createElement("button");
    single.className = "ghost small";
    single.textContent = "下载";
    single.onclick = () => mdDownload([item]);
    actions.append(lyrics, comments, single);

    row.append(check, name, artist, album, duration, actions);
    list.append(row);
  }
  $("md-empty").hidden = mdState.items.length > 0;
  mdUpdateDownloadCount();
}

function mdUpdateDownloadCount() {
  const button = $("md-download");
  if (!button) return;
  button.textContent = `下载所选 (${mdState.selected.size})`;
  button.disabled = mdState.selected.size === 0;
}

function mdSelectedItems() {
  return mdState.items.filter((item) => mdState.selected.has(mdKey(item)));
}

async function mdSearch() {
  const keyword = ($("md-keyword").value || "").trim();
  if (!keyword) return;
  const provider = $("md-provider").value || "netease";
  const limit = Number($("md-limit").value || 30);
  const button = $("md-search");
  button.disabled = true;
  button.textContent = "搜索中…";
  try {
    const data = await api("/api/music/search", {
      method: "POST",
      body: JSON.stringify({ provider, keyword, limit }),
    });
    mdState.items = data.items || [];
    mdState.selected.clear();
    mdState.title = `${keyword} · ${mdProviderLabel(provider)}`;
    mdState.sub = `${mdState.items.length} 首 · 勾选后下载`;
    mdState.cover = "";
    mdRenderResults();
    toast(mdState.items.length ? `找到 ${mdState.items.length} 首` : "没有结果，换个关键词");
  } catch (err) {
    toast(`搜索失败：${err.message}`);
  } finally {
    button.disabled = false;
    button.textContent = "搜索";
  }
}

async function mdLoadCollection() {
  const provider = $("md-provider").value || "netease";
  const kind = $("md-kind").value;
  const id = ($("md-collection").value || "").trim();
  if (!id) {
    toast("先填专辑 / 歌单 ID 或链接");
    return;
  }
  const button = $("md-load-collection");
  button.disabled = true;
  try {
    const data = await api("/api/music/collection", {
      method: "POST",
      body: JSON.stringify({ provider, kind, id }),
    });
    mdState.items = data.items || [];
    mdState.selected.clear();
    mdState.title = data.title || "专辑";
    mdState.sub = `${data.count || mdState.items.length} 首`
      + (data.artist ? ` · ${data.artist}` : "");
    mdState.cover = data.cover || "";
    mdRenderResults();
    toast(`${mdState.title}：${mdState.items.length} 首`);
  } catch (err) {
    toast(`读取失败：${err.message}`);
  } finally {
    button.disabled = false;
  }
}

async function mdLoadRank() {
  const provider = $("md-provider").value || "netease";
  const id = $("md-rank").value;
  if (!id) {
    toast("先选一个榜单");
    return;
  }
  const button = $("md-load-rank");
  button.disabled = true;
  try {
    const data = await api("/api/music/collection", {
      method: "POST",
      body: JSON.stringify({ provider, kind: "rank", id }),
    });
    mdState.items = data.items || [];
    mdState.selected.clear();
    mdState.title = data.title || "排行榜";
    mdState.sub = `${data.count || mdState.items.length} 首`;
    mdState.cover = data.cover || "";
    mdRenderResults();
  } catch (err) {
    toast(`榜单加载失败：${err.message}`);
  } finally {
    button.disabled = false;
  }
}

async function mdDownload(items) {
  if (!items || !items.length) return;
  const format = $("md-format").value;
  const out = ($("md-out").value || "").trim();
  const lyrics = $("md-lyrics").checked;
  try {
    const data = await api("/api/music/download", {
      method: "POST",
      body: JSON.stringify({ items, format, out, lyrics, quality: $("md-quality")?.value || "high" }),
    });
    const skipped = data.skipped ? `，跳过 ${data.skipped} 首（已存在/排队中）` : "";
    toast(`已加入 ${data.count} 首${skipped}`);
    refresh();
  } catch (err) {
    toast(`提交失败：${err.message}`);
  }
}

async function mdExport() {
  if (!mdState.items.length) {
    toast("没有可导出的歌曲");
    return;
  }
  const rows = [["歌曲", "歌手", "专辑", "时长", "平台", "音质", "版权"]];
  for (const item of mdState.items) {
    rows.push([item.title || "", item.artist || "", item.album || "",
      item.duration ? mdFormatTime(item.duration) : "", mdProviderLabel(item.provider),
      item.quality || "", item.available === false ? item.reason || "无版权" : "可下载"]);
  }
  try {
    const data = await api("/api/music/export", {
      method: "POST",
      body: JSON.stringify({ rows }),
    });
    toast(`已导出：${data.path}`);
    // 直接把生成的 xlsx 交给系统默认表格软件打开
    api("/api/reveal", { method: "POST", body: JSON.stringify({ path: data.path }) })
      .catch(() => {});
  } catch (err) {
    toast(`导出失败：${err.message}`);
  }
}

async function mdShowLyrics(item) {
  mdOpenModal(`歌词 · ${item.title}`);
  $("md-modal-body").textContent = "加载中…";
  try {
    const data = await api("/api/music/lyrics", {
      method: "POST",
      body: JSON.stringify({ song: item }),
    });
    $("md-modal-body").textContent = data.lrc || "（没有歌词）";
  } catch (err) {
    $("md-modal-body").textContent = `拿不到歌词：${err.message}`;
  }
}

async function mdShowComments(item) {
  mdOpenModal(`评论 · ${item.title}`);
  $("md-modal-body").textContent = "加载中…";
  try {
    const data = await api("/api/music/comments", {
      method: "POST",
      body: JSON.stringify({ song: item }),
    });
    if (!data.items || !data.items.length) {
      $("md-modal-body").textContent = data.note || "还没有评论";
      return;
    }
    const lines = data.items.map((row) =>
      `${row.user}  ${row.likes ? `👍 ${row.likes}` : ""}\n${row.content}\n`);
    $("md-modal-body").textContent = `共 ${data.total || data.items.length} 条\n\n`
      + lines.join("\n");
  } catch (err) {
    $("md-modal-body").textContent = `拿不到评论：${err.message}`;
  }
}

function mdOpenModal(title) {
  $("md-modal-title").textContent = title;
  $("md-modal-body").textContent = "";
  $("md-modal").hidden = false;
}

function mdCloseModal() {
  $("md-modal").hidden = true;
}

function mdStatusText(job) {
  if (job.status === "running") return job.stage || "处理中";
  if (job.status === "queued") return "排队";
  if (job.status === "paused") return "已暂停";
  if (job.status === "done") return "完成";
  if (job.status === "failed") return job.error || "失败";
  if (job.status === "cancelled") return "已取消";
  return job.status || "";
}

function mdRenderQueue(snapshot) {
  if (!snapshot) return;
  const jobs = snapshot.jobs || [];
  mdState.queue = jobs;
  const box = $("md-queue");
  if (!box) return;
  box.innerHTML = "";
  for (const job of jobs) {
    const row = document.createElement("div");
    row.className = `md-qrow ${job.status}`;
    const title = document.createElement("div");
    title.className = "md-qtitle";
    const name = document.createElement("b");
    name.textContent = job.title || "未命名";
    const meta = document.createElement("div");
    meta.className = "md-qmeta";
    meta.textContent = [job.artist, job.album, job.format && job.format.toUpperCase()]
      .filter(Boolean).join(" · ");
    meta.title = job.target || "";
    title.append(name, meta);

    const status = document.createElement("div");
    status.className = "md-qstatus";
    if (job.status === "running" || job.status === "queued") {
      const progress = document.createElement("progress");
      progress.max = 1;
      progress.value = Number(job.progress || 0);
      progress.style.width = "90%";
      status.append(progress, document.createElement("div"));
      status.lastChild.textContent = mdStatusText(job);
    } else {
      status.textContent = mdStatusText(job);
      status.title = job.error || "";
    }

    const actions = document.createElement("div");
    actions.className = "md-qactions";
    if (job.status === "done" && job.target) {
      const play = document.createElement("button");
      play.className = "ghost small";
      play.textContent = "播放";
      play.onclick = () => mdPlayJob(job);
      actions.append(play);
      const ext = (job.target.split(".").pop() || "").toLowerCase();
      for (const fmt of ["mp3", "wav"]) {
        if (ext === fmt) continue;
        const convert = document.createElement("button");
        convert.className = "ghost small";
        convert.textContent = `转${fmt.toUpperCase()}`;
        convert.onclick = () => mdConvertJob(job, fmt);
        actions.append(convert);
      }
    }
    if (["queued", "running"].includes(job.status)) {
      const pause = document.createElement("button");
      pause.className = "ghost small";
      pause.textContent = "暂停";
      pause.onclick = async () => {
        try {
          await api("/api/music/queue/pause", {
            method: "POST", body: JSON.stringify({ id: job.id }),
          });
          refresh();
        } catch (err) {
          toast(`暂停失败：${err.message}`);
        }
      };
      const cancel = document.createElement("button");
      cancel.className = "ghost small danger";
      cancel.textContent = "取消";
      cancel.onclick = async () => {
        try {
          await api("/api/music/queue/cancel", {
            method: "POST", body: JSON.stringify({ id: job.id }),
          });
          refresh();
        } catch (err) {
          toast(`取消失败：${err.message}`);
        }
      };
      actions.append(pause, cancel);
    }
    if (job.status === "paused") {
      const resume = document.createElement("button");
      resume.className = "ghost small";
      resume.textContent = "继续";
      resume.onclick = async () => {
        try {
          await api("/api/music/queue/resume", {
            method: "POST", body: JSON.stringify({ id: job.id }),
          });
          refresh();
        } catch (err) {
          toast(`继续失败：${err.message}`);
        }
      };
      const cancel = document.createElement("button");
      cancel.className = "ghost small danger";
      cancel.textContent = "取消";
      cancel.onclick = async () => {
        try {
          await api("/api/music/queue/cancel", {
            method: "POST", body: JSON.stringify({ id: job.id }),
          });
          refresh();
        } catch (err) {
          toast(`取消失败：${err.message}`);
        }
      };
      actions.append(resume, cancel);
    }
    if (["failed", "cancelled"].includes(job.status)) {
      const retry = document.createElement("button");
      retry.className = "ghost small";
      retry.textContent = "重试";
      retry.onclick = async () => {
        try {
          await api("/api/music/queue/retry", {
            method: "POST", body: JSON.stringify({ id: job.id }),
          });
          refresh();
        } catch (err) {
          toast(`重试失败：${err.message}`);
        }
      };
      actions.append(retry);
    }
    row.append(title, status, actions);
    box.append(row);
  }
  $("md-queue-empty").hidden = jobs.length > 0;
}

function mdDoneJobs() {
  return mdState.queue.filter((job) => job.status === "done" && job.target);
}

function mdJobEntries() {
  return mdDoneJobs().map((job) => ({
    kind: "job", id: job.id, path: job.target,
    title: job.title, artist: job.artist, album: job.album,
  }));
}

function mdLibEntries() {
  return mdLibItems.map((item) => ({
    kind: "file", path: item.path, title: item.title,
    artist: item.artist, album: item.album,
  }));
}

function mdPlayJob(job) {
  const list = mdJobEntries();
  const index = list.findIndex((entry) => entry.id === job.id);
  mdPlayList(index >= 0 ? index : 0, list);
}

function mdPlayLocal(item) {
  const list = mdLibEntries();
  const index = list.findIndex((entry) => entry.path === item.path);
  mdPlayList(index >= 0 ? index : 0, list);
}

function mdPlayList(index, list) {
  if (!list || !list.length) return;
  const pos = ((index % list.length) + list.length) % list.length;
  const entry = list[pos];
  mdState.playlist = list;
  mdState.playIndex = pos;
  const audio = $("md-audio");
  audio.src = entry.kind === "job"
    ? `/api/music/stream?id=${encodeURIComponent(entry.id)}`
    : `/api/music/stream?path=${encodeURIComponent(entry.path)}`;
  audio.play().catch((err) => toast(`播放失败：${err.message}`));
  $("md-player").hidden = false;
  $("md-now").textContent = entry.title || "未在播放";
  $("md-now-sub").textContent = [entry.artist, entry.album].filter(Boolean).join(" · ");
  $("md-play").textContent = "暂停";
  mdUpdateModeLabel();
  mdLoadLyrics(entry.path);
}

function mdTogglePlay() {
  const audio = $("md-audio");
  if (!audio.src) {
    const list = mdState.playlist.length ? mdState.playlist : mdJobEntries();
    if (list.length) mdPlayList(0, list);
    return;
  }
  if (audio.paused) {
    audio.play().catch((err) => toast(`播放失败：${err.message}`));
    $("md-play").textContent = "暂停";
  } else {
    audio.pause();
    $("md-play").textContent = "播放";
  }
}

function mdStep(delta) {
  const list = mdState.playlist.length ? mdState.playlist : mdJobEntries();
  if (!list.length) return;
  mdPlayList(mdState.playIndex + delta, list);
}

let mdLyrics = [];
let mdLyricIndex = -1;

function mdParseLrc(text) {
  const lines = [];
  const pattern = /\[(\d{1,2}):(\d{1,2}(?:\.\d+)?)\](.*)/g;
  let match;
  while ((match = pattern.exec(text || ""))) {
    const seconds = Number(match[1]) * 60 + Number(match[2]);
    const content = (match[3] || "").trim();
    lines.push({ t: seconds, text: content || " " });
  }
  lines.sort((a, b) => a.t - b.t);
  return lines;
}

function mdSetLyrics(lines) {
  mdLyrics = lines || [];
  mdLyricIndex = -1;
  const box = $("md-lyrics-lines");
  if (!box) return;
  box.innerHTML = "";
  if (!mdLyrics.length) {
    const empty = document.createElement("div");
    empty.className = "md-lyric-line";
    empty.textContent = "没有找到歌词（同名 .lrc）";
    box.append(empty);
    return;
  }
  mdLyrics.forEach((line, index) => {
    const el = document.createElement("div");
    el.className = "md-lyric-line";
    el.dataset.index = String(index);
    el.textContent = line.text;
    box.append(el);
  });
}

async function mdLoadLyrics(path) {
  if (!path) {
    mdSetLyrics([]);
    return;
  }
  try {
    const data = await api(`/api/music/library/lyrics?path=${encodeURIComponent(path)}`);
    mdSetLyrics(mdParseLrc((data.lrc || "").replace(/^\uFEFF/, "")));
  } catch (err) {
    mdSetLyrics([]);
  }
}

function mdUpdateLyric(time) {
  if (!mdLyrics.length) return;
  let index = -1;
  for (let i = 0; i < mdLyrics.length; i += 1) {
    if (mdLyrics[i].t <= time + 0.15) index = i;
    else break;
  }
  if (index === mdLyricIndex) return;
  mdLyricIndex = index;
  const box = $("md-lyrics-lines");
  if (!box) return;
  for (const el of box.children) el.classList.remove("active");
  if ($("md-lyrics-panel").hidden) return;
  const active = box.children[index];
  if (active) {
    active.classList.add("active");
    active.scrollIntoView({ block: "nearest" });
  }
}

function mdToggleLyrics(force) {
  const panel = $("md-lyrics-panel");
  if (!panel) return;
  const show = force === true ? true : force === false ? false : panel.hidden;
  panel.hidden = !show;
}

let mdLibItems = [];
let mdLibTimer = null;
const mdLibState = { offset: 0, hasMore: false, query: "" };

async function mdLibLoad(more = false) {
  const q = ($("md-lib-search")?.value || "").trim();
  try {
    if (!more || q !== mdLibState.query) {
      mdLibState.query = q;
      mdLibState.offset = 0;
      mdLibItems = [];
    }
    const data = await api(
      `/api/music/library?q=${encodeURIComponent(q)}&limit=200&offset=${mdLibState.offset}`);
    mdLibItems = mdLibItems.concat(data.items || []);
    mdLibState.offset += (data.items || []).length;
    mdLibState.hasMore = Boolean(data.has_more);
    $("md-lib-hint").textContent =
      `${data.root} · 共 ${data.total} 首，已显示 ${mdLibItems.length}` +
      (q ? ` · 筛选“${q}”` : "");
    mdRenderLib();
  } catch (err) {
    toast(`扫描失败：${err.message}`);
  }
}

function mdRenderLib() {
  const box = $("md-lib-list");
  if (!box) return;
  box.innerHTML = "";
  const head = document.createElement("div");
  head.className = "md-lib-row head";
  head.innerHTML = "<span></span><span>歌曲</span><span>歌手</span><span>时长</span><span></span>";
  box.append(head);
  for (const item of mdLibItems) {
    const row = document.createElement("div");
    row.className = "md-lib-row";

    const play = document.createElement("button");
    play.className = "ghost small";
    play.textContent = "▶";
    play.title = "播放";
    play.onclick = () => mdPlayLocal(item);

    const title = document.createElement("div");
    title.className = "md-name";
    title.textContent = item.title || item.name;
    title.title = item.path;

    const artist = document.createElement("div");
    artist.className = "md-dim";
    artist.textContent = [item.artist, item.album].filter(Boolean).join(" · ");

    const duration = document.createElement("div");
    duration.className = "md-dim";
    duration.textContent = item.duration ? mdFormatTime(item.duration) : "-";

    const actions = document.createElement("div");
    actions.className = "md-actions";
    for (const fmt of ["mp3", "wav"]) {
      if (item.ext === fmt) continue;
      const convert = document.createElement("button");
      convert.className = "ghost small";
      convert.textContent = `转${fmt.toUpperCase()}`;
      convert.onclick = () => mdLibConvert(item, fmt);
      actions.append(convert);
    }
    const lyrics = document.createElement("button");
    lyrics.className = "ghost small";
    lyrics.textContent = "词";
    lyrics.onclick = async () => {
      await mdLoadLyrics(item.path);
      mdToggleLyrics(true);
    };
    const remove = document.createElement("button");
    remove.className = "ghost small danger";
    remove.textContent = "删除";
    remove.onclick = () => mdLibDelete(item);
    actions.append(lyrics, remove);

    row.append(play, title, artist, duration, actions);
    box.append(row);
  }
  $("md-lib-empty").hidden = mdLibItems.length > 0;
  const more = $("md-lib-more");
  if (more) more.hidden = !mdLibState.hasMore;
}

async function mdLibConvert(item, fmt) {
  try {
    const data = await api("/api/music/convert-local", {
      method: "POST",
      body: JSON.stringify({ path: item.path, format: fmt }),
    });
    toast(`已加入 ${data.count} 个转换任务`);
    refresh();
  } catch (err) {
    toast(`转换失败：${err.message}`);
  }
}

async function mdLibDelete(item) {
  if (!window.confirm(`删除文件？\n${item.path}\n\n同名 .lrc 也会一起删除。`)) return;
  try {
    await api("/api/music/library/delete", {
      method: "POST",
      body: JSON.stringify({ path: item.path }),
    });
    toast("已删除");
    mdLibLoad();
  } catch (err) {
    toast(`删除失败：${err.message}`);
  }
}

async function mdLibM3u() {
  try {
    const q = ($("md-lib-search")?.value || "").trim();
    const paths = (!q && mdLibState.hasMore)
      ? null
      : mdLibItems.map((item) => item.path);
    const data = await api("/api/music/library/m3u", {
      method: "POST",
      body: JSON.stringify({ paths }),
    });
    toast(`已导出：${data.path}`);
    api("/api/reveal", { method: "POST", body: JSON.stringify({ path: data.path }) })
      .catch(() => {});
  } catch (err) {
    toast(`导出失败：${err.message}`);
  }
}

function mdUpdateModeLabel() {
  const labels = { list: "列表循环", single: "单曲循环", shuffle: "随机播放" };
  $("md-mode").textContent = labels[mdState.mode] || "列表循环";
}

function mdFormatTime(seconds) {
  if (!Number.isFinite(seconds)) return "00:00";
  const total = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function mdBindPlayer() {
  const audio = $("md-audio");
  if (!audio || audio.dataset.bound) return;
  audio.dataset.bound = "1";
  audio.addEventListener("timeupdate", () => {
    const seek = $("md-seek");
    if (!seek || !Number.isFinite(audio.duration) || !audio.duration) return;
    if (!seek.dataset.dragging) seek.value = String((audio.currentTime / audio.duration) * 1000);
    $("md-time").textContent = `${mdFormatTime(audio.currentTime)} / ${mdFormatTime(audio.duration)}`;
    mdUpdateLyric(audio.currentTime);
  });
  audio.addEventListener("ended", () => {
    if (mdState.mode === "single") {
      audio.currentTime = 0;
      audio.play().catch(() => {});
      return;
    }
    const list = mdState.playlist.length ? mdState.playlist : mdJobEntries();
    if (!list.length) return;
    const next = mdState.mode === "shuffle"
      ? Math.floor(Math.random() * list.length)
      : mdState.playIndex + 1;
    mdPlayList(next, list);
  });
  const seek = $("md-seek");
  seek?.addEventListener("input", () => { seek.dataset.dragging = "1"; });
  seek?.addEventListener("change", () => {
    delete seek.dataset.dragging;
    if (Number.isFinite(audio.duration)) {
      audio.currentTime = (Number(seek.value) / 1000) * audio.duration;
    }
  });
}

function mdCycleTheme() {
  const order = ["", "teal", "violet"];
  const current = document.body.dataset.theme || "";
  const next = order[(order.indexOf(current) + 1) % order.length];
  if (next) document.body.dataset.theme = next;
  else delete document.body.dataset.theme;
  try { localStorage.setItem("md-theme", next); } catch (err) { /* 忽略隐私模式 */ }
  toast(`换肤：${next === "teal" ? "青绿" : next === "violet" ? "紫罗兰" : "默认"}`);
}

function mdRestoreTheme() {
  try {
    const saved = localStorage.getItem("md-theme") || "";
    if (saved) document.body.dataset.theme = saved;
  } catch (err) { /* 忽略 */ }
}

let mdLoginTimer = null;
let mdLoginKey = "";

function mdRenderLogin(info) {
  if (!info) return;
  const badge = $("md-login-badge");
  const name = $("md-login-name");
  if (!badge || !name) return;
  badge.classList.remove("ok", "vip");
  if (info.logged_in) {
    badge.textContent = info.vip ? "会员" : "已登录";
    badge.classList.add(info.vip ? "vip" : "ok");
    name.textContent = info.nickname || "已登录";
  } else {
    badge.textContent = "未登录";
    name.textContent = "扫码登录后，会员曲 / 320k / 无损可下";
  }
}

async function mdStartLogin() {
  const button = $("md-login");
  button.disabled = true;
  try {
    const data = await api("/api/music/login/start", {
      method: "POST",
      body: "{}",
    });
    mdLoginKey = data.key;
    $("md-qr").hidden = false;
    $("md-qr-code").innerHTML = "";
    // 本地渲染二维码，不把 key 发给第三方图床
    new QRCode($("md-qr-code"), {
      text: data.qr,
      width: 220,
      height: 220,
      correctLevel: QRCode.CorrectLevel.M,
    });
    $("md-qr-status").textContent = "打开网易云音乐 App → 扫一扫";
    mdPollLogin();
  } catch (err) {
    toast(`二维码获取失败：${err.message}`);
  } finally {
    button.disabled = false;
  }
}

function mdStopLogin() {
  if (mdLoginTimer) {
    clearTimeout(mdLoginTimer);
    mdLoginTimer = null;
  }
  mdLoginKey = "";
  $("md-qr").hidden = true;
}

async function mdPollLogin() {
  if (!mdLoginKey) return;
  try {
    const data = await api("/api/music/login/poll", {
      method: "POST",
      body: JSON.stringify({ key: mdLoginKey }),
    });
    if (data.code === 803) {
      const nickname = data.account?.nickname || data.nickname || "";
      $("md-qr-status").textContent = `登录成功：${nickname}`;
      toast(data.account?.vip ? "网易云会员已登录，会员曲 / 无损可下" : "网易云登录成功");
      setTimeout(() => {
        mdStopLogin();
        refresh();
      }, 900);
      return;
    }
    if (data.code === 800) {
      $("md-qr-status").textContent = "二维码过期，请重新点「网易云扫码登录」";
      return;
    }
    if (data.code === 802) {
      $("md-qr-status").textContent = "已扫描，请在手机上确认";
    } else {
      $("md-qr-status").textContent = data.message || "等待扫码…";
    }
    mdLoginTimer = setTimeout(mdPollLogin, 2000);
  } catch (err) {
    $("md-qr-status").textContent = `轮询失败：${err.message}`;
    mdLoginTimer = setTimeout(mdPollLogin, 3000);
  }
}

async function mdLogout() {
  try {
    await api("/api/music/login/logout", {
      method: "POST",
      body: JSON.stringify({ provider: "netease" }),
    });
    toast("已退出网易云");
    refresh();
  } catch (err) {
    toast(`退出失败：${err.message}`);
  }
}

async function mdImportNcm() {
  const path = ($("md-ncm-path").value || "").trim();
  if (!path) {
    toast("先填 NCM 文件或文件夹路径");
    return;
  }
  const format = $("md-format").value;
  const out = ($("md-out").value || "").trim();
  try {
    const data = await api("/api/music/ncm", {
      method: "POST",
      body: JSON.stringify({ path, format, out, quality: $("md-quality")?.value || "high" }),
    });
    toast(`已加入 ${data.count} 个 NCM（共 ${data.total}，跳过 ${data.skipped}）`);
    refresh();
  } catch (err) {
    toast(`导入失败：${err.message}`);
  }
}

async function mdConvertJob(job, fmt) {
  try {
    const data = await api("/api/music/convert", {
      method: "POST",
      body: JSON.stringify({
        id: job.id, format: fmt, quality: $("md-quality")?.value || "high",
      }),
    });
    toast(data.count
      ? `已加入转换：${job.title || ""} → ${fmt.toUpperCase()}`
      : `这个格式已经在队列里了`);
    refresh();
  } catch (err) {
    toast(`转换失败：${err.message}`);
  }
}

async function mdConvertLocal() {
  const path = ($("md-convert-path").value || "").trim();
  if (!path) {
    toast("先填音频文件或文件夹路径");
    return;
  }
  const format = $("md-convert-format").value;
  const out = ($("md-convert-out").value || "").trim();
  const button = $("md-convert-go");
  button.disabled = true;
  try {
    const data = await api("/api/music/convert-local", {
      method: "POST",
      body: JSON.stringify({ path, format, out }),
    });
    toast(`已加入 ${data.count} 个转换任务（共 ${data.total}，跳过 ${data.skipped}）`);
    refresh();
  } catch (err) {
    toast(`转换失败：${err.message}`);
  } finally {
    button.disabled = false;
  }
}

/* ---------------- 音频转换 ---------------- */

const cvState = { formats: [] };

function cvInitFormats(list) {
  if (!Array.isArray(list) || !list.length) return;
  cvState.formats = list;
  const signature = list.map((item) => item.name).join(",");
  for (const id of ["cv-ncm-format", "cv-format"]) {
    const select = $(id);
    if (!select || select.dataset.filled === signature) continue;
    select.innerHTML = "";
    for (const item of list) {
      const option = document.createElement("option");
      option.value = item.name;
      option.textContent = item.label;
      select.append(option);
    }
    select.dataset.filled = signature;
    select.value = "mp3";
  }
}

async function cvStartNcm() {
  const path = ($("cv-ncm-path").value || "").trim();
  if (!path) {
    toast("先填 NCM 文件或文件夹路径");
    return;
  }
  const format = $("cv-ncm-format").value;
  const out = ($("cv-ncm-out").value || "").trim();
  const quality = $("cv-ncm-quality")?.value || "high";
  const button = $("cv-ncm-go");
  button.disabled = true;
  try {
    const data = await api("/api/music/ncm", {
      method: "POST",
      body: JSON.stringify({ path, format, out, quality }),
    });
    toast(`已加入 ${data.count} 个 NCM（共 ${data.total}，跳过 ${data.skipped}）`);
    refresh();
  } catch (err) {
    toast(`解密失败：${err.message}`);
  } finally {
    button.disabled = false;
  }
}

async function cvStartConvert() {
  const path = ($("cv-path").value || "").trim();
  if (!path) {
    toast("先填音频文件或文件夹路径");
    return;
  }
  const format = $("cv-format").value;
  const out = ($("cv-out").value || "").trim();
  const quality = $("cv-quality")?.value || "high";
  const button = $("cv-go");
  button.disabled = true;
  try {
    const data = await api("/api/music/convert-local", {
      method: "POST",
      body: JSON.stringify({ path, format, out, quality }),
    });
    toast(`已加入 ${data.count} 个转换任务（共 ${data.total}，跳过 ${data.skipped}）`);
    refresh();
  } catch (err) {
    toast(`转换失败：${err.message}`);
  } finally {
    button.disabled = false;
  }
}

function cvRenderQueue(snapshot) {
  const box = $("cv-queue");
  if (!box) return;
  const jobs = (snapshot?.jobs || []).filter(
    (job) => job.kind === "ncm" || job.kind === "convert");
  box.innerHTML = "";
  for (const job of jobs) {
    const row = document.createElement("div");
    row.className = `md-qrow ${job.status}`;
    const title = document.createElement("div");
    title.className = "md-qtitle";
    const name = document.createElement("b");
    name.textContent = job.title || "未命名";
    const meta = document.createElement("div");
    meta.className = "md-qmeta";
    meta.textContent = [job.artist, job.format && job.format.toUpperCase()]
      .filter(Boolean).join(" · ");
    meta.title = job.target || "";
    title.append(name, meta);

    const status = document.createElement("div");
    status.className = "md-qstatus";
    if (job.status === "running" || job.status === "queued") {
      const progress = document.createElement("progress");
      progress.max = 1;
      progress.value = Number(job.progress || 0);
      progress.style.width = "90%";
      status.append(progress, document.createElement("div"));
      status.lastChild.textContent = mdStatusText(job);
    } else {
      status.textContent = mdStatusText(job);
      status.title = job.error || "";
    }

    const actions = document.createElement("div");
    actions.className = "md-qactions";
    if (["queued", "running"].includes(job.status)) {
      const cancel = document.createElement("button");
      cancel.className = "ghost small danger";
      cancel.textContent = "取消";
      cancel.onclick = async () => {
        try {
          await api("/api/music/queue/cancel", {
            method: "POST", body: JSON.stringify({ id: job.id }),
          });
          refresh();
        } catch (err) {
          toast(`取消失败：${err.message}`);
        }
      };
      actions.append(cancel);
    }
    if (["failed", "cancelled"].includes(job.status)) {
      const retry = document.createElement("button");
      retry.className = "ghost small";
      retry.textContent = "重试";
      retry.onclick = async () => {
        try {
          await api("/api/music/queue/retry", {
            method: "POST", body: JSON.stringify({ id: job.id }),
          });
          refresh();
        } catch (err) {
          toast(`重试失败：${err.message}`);
        }
      };
      actions.append(retry);
    }
    row.append(title, status, actions);
    box.append(row);
  }
  $("cv-empty").hidden = jobs.length > 0;
}

/* ---------------- 刷新 ---------------- */

let refreshing = false;

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const data = await api("/api/bootstrap");
    // 平台描述由后端下发：名字、显示名、单页条数、识别正则、作者主页模板
    if (Array.isArray(data.platforms) && data.platforms.length) {
      PLATFORMS = data.platforms;
    }
    $("ver").textContent = `v${data.version}`;
    $("c-running").textContent = data.counts.running;
    $("c-queued").textContent = data.counts.queued;
    $("c-done").textContent = data.counts.done;
    $("c-failed").textContent = data.counts.failed;
    $("c-paused").textContent = data.counts.paused || 0;
    const cancelAll = $("btn-cancel-all");
    if (cancelAll) {
      const active = (data.counts.queued || 0) + (data.counts.running || 0)
        + (data.counts.paused || 0);
      cancelAll.hidden = !active;
      cancelAll.textContent = `取消全部 (${active})`;
    }
    const retryAll = $("btn-retry-failed");
    if (retryAll) {
      retryAll.hidden = !data.counts.failed;
      retryAll.textContent = `重试失败 (${data.counts.failed})`;
    }
    $("free").textContent = data.free_gb ? `剩余空间 ${data.free_gb} GB` : "";
    render(data.jobs);
    fillConfig(data.config);
    appConfig = data.config;
    appCapabilities = data.capabilities || [];
    queuePaused = Boolean(data.queue_paused);
    updateQueueButton();
    // 音乐：平台能力 + 下载队列
    if (Array.isArray(data.music_providers)) mdInitProviders(data.music_providers);
    if (data.music) mdRenderQueue(data.music);
    if (data.music_login) mdRenderLogin(data.music_login);
    if (data.rules && $("rules-status")) {
      const stamp = Number(data.rules.updated_at || 0);
      $("rules-status").textContent = stamp
        ? `规则已更新：${new Date(stamp * 1000).toLocaleString()}`
        : "当前使用内置规则";
    }
    cvInitFormats(data.music_convert_formats || []);
    if (data.music) cvRenderQueue(data.music);
  } catch (err) {
    console.error(err);
  } finally {
    refreshing = false;
  }
}

function setValue(id, value) {
  const el = document.getElementById(id);
  if (el) el.value = value;
}

let configFilled = false;
function fillConfig(cfg) {
  if (!cfg) return;
  if (configFilled && !$("drawer").hidden) return;
  setValue("cfg-output", cfg.output_dir || "");
  setValue("cfg-template", cfg.folder_template || "{platform}/{author}");
  setValue("cfg-file-template", cfg.file_template || "{title}");
  setValue("cfg-concurrency", cfg.concurrency ?? 3);
  setValue("cfg-retries", cfg.retries ?? 3);
  setValue("cfg-bili-qn", String(cfg.bili_qn ?? 0));
  setValue("cfg-cookie", cfg.cookie || "");
  setValue("cfg-bili-cookie", cfg.bilibili_cookie || "");
  setValue("cfg-proxy", cfg.proxy || "");
  setValue("cfg-audio-preset", cfg.audio_preset || "audio_m4a");
  setValue("cfg-ffmpeg", cfg.ffmpeg_path || "");
  setValue("cfg-live", cfg.live_photo || "video");
  const clearHistory = $("cfg-clear-history");
  if (clearHistory) clearHistory.checked = cfg.clear_history_on_exit !== false;
  const autoFallback = $("cfg-auto-fallback");
  if (autoFallback) autoFallback.checked = cfg.auto_fallback !== false;
  setValue("cfg-rules-url", cfg.rules_url || "");
  setValue("cfg-music-dir", cfg.music_dir || "");
  setValue("cfg-music-format", cfg.music_format || "mp3");
  setValue("cfg-music-concurrency", cfg.music_concurrency ?? 3);
  const cookieProvider = ($("cfg-music-cookie-provider") || {}).value || "netease";
  setValue("cfg-music-cookie", cfg[`music_cookie_${cookieProvider}`] || "");
  if (!$("md-out").value) setValue("md-out", cfg.music_dir || "");
  if (!$("md-format").dataset.filled) {
    $("md-format").value = cfg.music_format || "mp3";
    $("md-format").dataset.filled = "1";
  }
  configFilled = true;
}

/* ---------------- 提交 ---------------- */

async function submit(fromId = "input") {
  const box = $(fromId);
  if (!box) return;
  const text = box.value.trim();
  if (!text) return;
  const url = firstUrl(text);

  // 是主页还是单条作品、要不要展开短链，全问后端：前端不认识任何平台规则
  if (url) {
    try {
      const resolved = await api("/api/resolve", {
        method: "POST",
        body: JSON.stringify({ url }),
      });
      if (resolved.kind === "profile") {
        box.value = "";
        if (resolved.platform === "bilibili") switchView("bilibili");
        loadProfile(resolved.url || url);
        return;
      }
      // 单条链接先看看有没有选集；一次粘好几条时不打扰
      const count = (text.match(/https?:\/\/[^\s]+/g) || []).length;
      if (count === 1 && (await loadEpisodes(resolved.url || url))) {
        box.value = "";
        switchView("bilibili");
        return;
      }
    } catch (err) {
      console.warn("链接识别失败，按作品提交", err);
    }
  }

  const button = $(fromId === "input-bili" ? "btn-submit-bili" : "btn-submit");
  button.disabled = true;
  try {
    const outEl = $(fromId === "input-bili" ? "out-dir-bili" : "out-dir");
    const out_dir = outEl ? outEl.value.trim() : "";
    const forceEl = $(fromId === "input-bili" ? "force-redownload-bili" : "force-redownload");
    const data = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({ text, out_dir, force: Boolean(forceEl && forceEl.checked) }),
    });
    const results = data.results || [];
    const queued = results.filter((r) => r.status === "queued").length;
    const skipped = results.filter((r) => r.status === "skipped").length;
    const errors = results.filter((r) => r.status === "error");
    const bits = [];
    if (queued) bits.push(`已加入 ${queued} 个`);
    if (skipped) bits.push(`跳过 ${skipped} 个已下载`);
    if (errors.length) bits.push(errors.map((e) => e.reason).join("；"));
    toast(bits.join("，") || "没有可处理的内容");
    if (queued) box.value = "";
    refresh();
  } catch (err) {
    toast(`提交失败：${err.message}`);
  } finally {
    button.disabled = false;
  }
}

/* 设置里的两行账号（抖音 / B 站）对应的平台名。接口一律走通用的
   /api/login/<平台>，不再为每个平台写一套路由。 */
const LOGIN_ROWS = { dy: "douyin", bili: "bilibili" };

function loginTargets() {
  const targets = {};
  for (const [kind, platform] of Object.entries(LOGIN_ROWS)) {
    targets[kind] = {
      platform,
      start: `/api/login/${platform}`,
      status: `/api/login/${platform}/status`,
      logout: `/api/logout/${platform}`,
      stateId: `${kind}-login-state`,
      btnId: `btn-${kind}-login`,
    };
  }
  return targets;
}

const LOGIN_TARGETS = loginTargets();

function loginKindFor(platform) {
  const found = Object.entries(LOGIN_ROWS).find(([, name]) => name === platform);
  return found ? found[0] : null;
}

function renderLoginState(kind, state) {
  const el = document.getElementById(LOGIN_TARGETS[kind].stateId);
  if (!el || !state) return;
  if (state.status === "waiting") {
    el.textContent = "⏳ 等待扫码…";
  } else if (state.status === "ok") {
    el.textContent = `✅ ${state.message}`;
  } else if (state.status === "error" || state.status === "timeout") {
    el.textContent = `⚠️ ${state.message}`;
  } else if (state.has_cookie) {
    el.textContent = "✅ 已保存 Cookie";
  } else {
    el.textContent = "未登录";
  }
}

async function startLogin(kind) {
  const target = LOGIN_TARGETS[kind];
  const button = document.getElementById(target.btnId);
  const el = document.getElementById(target.stateId);
  if (button) button.disabled = true;
  if (el) el.textContent = "正在打开浏览器…";
  try {
    const state = await api(target.start, { method: "POST", body: "{}" });
    renderLoginState(kind, state);
    pollLogin(kind);
  } catch (err) {
    if (el) el.textContent = `登录失败：${err.message}`;
  } finally {
    if (button) button.disabled = false;
  }
}

function pollLogin(kind) {
  const target = LOGIN_TARGETS[kind];
  clearInterval(pollLogin._timers?.[kind]);
  pollLogin._timers = pollLogin._timers || {};
  pollLogin._timers[kind] = setInterval(async () => {
    try {
      const state = await api(target.status);
      renderLoginState(kind, state);
      if (state.status !== "waiting") {
        clearInterval(pollLogin._timers[kind]);
        refresh();
        // 登录后重新拉一次当前主页：抖音要登录态才给第二页及之后的作品
        if (state.status === "ok" && profile && profile.platform === target.platform) {
          const url = platformProfileUrl(profile.platform, profile.sec_uid);
          if (url) loadProfile(url);
        }
      }
    } catch (err) {
      console.error(err);
    }
  }, 1500);
}

async function logoutLogin(kind) {
  const target = LOGIN_TARGETS[kind];
  try {
    renderLoginState(kind, await api(target.logout, { method: "POST", body: "{}" }));
    refresh();
  } catch (err) {
    const el = document.getElementById(target.stateId);
    if (el) el.textContent = `退出失败：${err.message}`;
  }
}

async function refreshLogin(kind) {
  try {
    renderLoginState(kind, await api(LOGIN_TARGETS[kind].status));
  } catch (err) {
    console.error(err);
  }
}

async function refreshLoginStates() {
  await Promise.all(Object.keys(LOGIN_TARGETS).map((kind) => refreshLogin(kind)));
}

async function saveConfig() {
  const patch = {
    output_dir: ($("cfg-output")?.value || "").trim(),
    folder_template:
      ($("cfg-template")?.value || "").trim() || "{platform}/{author}",
    file_template: ($("cfg-file-template")?.value || "").trim() || "{title}",
    concurrency: Number($("cfg-concurrency")?.value) || 3,
    retries: Number($("cfg-retries")?.value) || 0,
    bili_qn: Number($("cfg-bili-qn")?.value) || 0,
    audio_preset: $("cfg-audio-preset")?.value || "audio_m4a",
    ffmpeg_path: ($("cfg-ffmpeg")?.value || "").trim(),
    live_photo: $("cfg-live")?.value || "video",
    clear_history_on_exit: Boolean($("cfg-clear-history")?.checked),
    auto_fallback: Boolean($("cfg-auto-fallback")?.checked),
    rules_url: ($("cfg-rules-url")?.value || "").trim(),
    music_dir: ($("cfg-music-dir")?.value || "").trim(),
    music_format: $("cfg-music-format")?.value || "mp3",
    music_concurrency: Number($("cfg-music-concurrency")?.value) || 3,
    cookie: ($("cfg-cookie")?.value || "").trim(),
    bilibili_cookie: ($("cfg-bili-cookie")?.value || "").trim(),
    proxy: ($("cfg-proxy")?.value || "").trim(),
  };
  const cookieProvider = $("cfg-music-cookie-provider")?.value || "netease";
  patch[`music_cookie_${cookieProvider}`] =
    ($("cfg-music-cookie")?.value || "").trim();
  try {
    await api("/api/config", { method: "POST", body: JSON.stringify(patch) });
    toast("设置已保存");
    configFilled = false;
    refresh();
  } catch (err) {
    toast(`保存失败：${err.message}`);
  }
}

async function clearFinishedJobs() {
  if (!window.confirm("清空已结束（完成 / 失败 / 已取消）的任务记录？\n只删记录，已经下载的文件不动。")) {
    return;
  }
  try {
    const data = await api("/api/jobs/clear-finished", { method: "POST" });
    toast(`已清空 ${data.count} 条任务记录`);
    refresh();
  } catch (err) {
    toast(`清空失败：${err.message}`);
  }
}

function on(id, handler) {
  const el = document.getElementById(id);
  if (el) el.onclick = handler;
  else console.warn(`[ui] 缺少元素 #${id}，跳过绑定`);
}

function bind() {
  on("btn-submit", () => submit("input"));
  on("archive-refresh", () => archiveLoad(false));
  on("archive-more", () => archiveLoad(true));
  on("archive-open", async () => {
    try {
      await api("/api/reveal", { method: "POST", body: "{}" });
    } catch (err) {
      toast(`打不开：${err.message}`);
    }
  });
  on("archive-close", closeArchive);
  $("archive-modal")?.addEventListener("click", (event) => {
    if (event.target.id === "archive-modal") closeArchive();
  });
  $("archive-search")?.addEventListener("input", () => {
    clearTimeout(archiveState.timer);
    archiveState.timer = setTimeout(() => archiveLoad(false), 300);
  });
  on("btn-hot-refresh", () => loadHot(true));
  on("btn-discover-login", () => startLogin("dy"));
  on("btn-discover-search", discoverSearch);
  $("discover-keyword")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      discoverSearch();
    }
  });
  on("btn-discover-all", () => {
    discoverState.items.forEach((item) => discoverState.selected.add(item.aweme_id));
    renderDiscover();
  });
  on("btn-discover-none", () => {
    discoverState.selected.clear();
    renderDiscover();
  });
  on("btn-discover-download", downloadDiscover);
  on("btn-discover-more", discoverMore);
  on("btn-submit-bili", () => submit("input-bili"));
  for (const id of ["input", "input-bili"]) {
    $(id)?.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        submit(id);
      }
    });
  }
  for (const button of document.querySelectorAll(".nav-item")) {
    button.onclick = () => switchView(button.dataset.view);
  }
  on("btn-select-all", () => {
    if (!profile) return;
    profileFiltered().forEach((i) => selected.add(i.aweme_id));
    renderProfile();
  });
  on("btn-select-none", () => {
    selected.clear();
    renderProfile();
  });
  for (const id of ["profile-date-from", "profile-date-to"]) {
    $(id)?.addEventListener("change", renderProfile);
  }
  on("btn-load-more", loadMore);
  on("btn-episodes-all", () => {
    if (!episodeData) return;
    episodeData.episodes.forEach((ep) => episodeSelected.add(ep.key));
    renderEpisodes();
  });
  on("btn-episodes-none", () => {
    episodeSelected.clear();
    renderEpisodes();
  });
  on("btn-episodes-close", closeEpisodes);
  on("btn-episodes-download", downloadEpisodes);
  on("btn-episodes-current", downloadCurrentEpisode);
  on("btn-profile-login", () => {
    // 在主页面板里直接发起扫码：扫完 pollLogin 会自动重新加载这个主页
    const kind = loginKindFor(profile && profile.platform);
    if (!kind) {
      toast("当前平台不支持登录");
      return;
    }
    toast(`正在打开浏览器，用 ${platformLabel(profile.platform)} 扫码登录…`);
    startLogin(kind);
  });
  on("btn-download-selected", downloadSelected);
  on("btn-close-profile", () => {
    $("profile-panel").hidden = true;
    profile = null;
    selected.clear();
  });
  on("btn-settings", () => {
    configFilled = false;
    $("drawer").hidden = false;
    refresh();
    refreshLoginStates();
  });
  on("btn-dy-login", () => startLogin("dy"));
  on("btn-dy-logout", () => logoutLogin("dy"));
  on("btn-bili-login", () => startLogin("bili"));
  on("btn-bili-logout", () => logoutLogin("bili"));
  on("btn-close", () => {
    $("drawer").hidden = true;
    configFilled = false;
  });
  $("cfg-music-cookie-provider")?.addEventListener("change", () => {
    const provider = $("cfg-music-cookie-provider").value;
    const key = `music_cookie_${provider}`;
    $("cfg-music-cookie").value = (appConfig && appConfig[key]) || "";
  });
  on("btn-save", saveConfig);
  on("btn-rules-update", async () => {
    const url = ($("cfg-rules-url")?.value || "").trim();
    if (!url) {
      toast("先填规则地址");
      return;
    }
    try {
      await api("/api/rules/fetch", {
        method: "POST",
        body: JSON.stringify({ url }),
      });
      toast("规则已更新，下一次解析立即生效");
      refresh();
    } catch (err) {
      toast(`规则更新失败：${err.message}`);
    }
  });
  on("btn-restart", async () => {
    if (!window.confirm("重启应用？\n正在下载的任务会中断，重新打开后可继续。")) return;
    try {
      await api("/api/restart", { method: "POST", body: "{}" });
      toast("正在重启，请稍候…");
      setTimeout(() => location.reload(), 4000);
    } catch (err) {
      toast(`重启失败：${err.message}`);
    }
  });
  on("btn-queue-pause", toggleQueue);
  on("btn-global-pause", toggleQueue);
  on("btn-cancel-all", async () => {
    if (!window.confirm("取消所有未完成的任务？已下载的部分会被删除。")) return;
    try {
      const data = await api("/api/jobs/cancel-all", { method: "POST" });
      toast(`已取消 ${data.count} 个任务，未下完的文件已删除`);
      refresh();
    } catch (err) {
      toast(`取消失败：${err.message}`);
    }
  });
  on("btn-retry-failed", async () => {
    try {
      const data = await api("/api/jobs/retry-failed", { method: "POST" });
      toast(`已重新排队 ${data.count} 个失败任务`);
      refresh();
    } catch (err) {
      toast(`重试失败：${err.message}`);
    }
  });
  on("btn-clear-finished", clearFinishedJobs);
  on("btn-clear-finished-bili", clearFinishedJobs);
  // 记住上次填的保存路径
  for (const [elementId, storageKey] of [
    ["out-dir", "douyin-out"],
    ["out-dir-bili", "bili-out"],
    ["md-out", "music-out"],
  ]) {
    const input = $(elementId);
    if (!input) continue;
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved && !input.value) input.value = saved;
    } catch (err) { /* 隐私模式忽略 */ }
    input.addEventListener("change", () => {
      try {
        localStorage.setItem(storageKey, input.value.trim());
      } catch (err) { /* 忽略 */ }
    });
  }
  on("btn-open-bili", async () => {
    try {
      await api("/api/reveal", { method: "POST", body: "{}" });
    } catch (err) {
      toast(`打不开：${err.message}`);
    }
  });
  on("btn-audio-open", async () => {
    try {
      await api("/api/reveal", { method: "POST", body: "{}" });
    } catch (err) {
      toast(`打不开：${err.message}`);
    }
  });
  on("btn-open", async () => {
    try {
      await api("/api/reveal", { method: "POST", body: "{}" });
    } catch (err) {
      toast(`打不开：${err.message}`);
    }
  });
  // 音乐下载
  on("md-search", mdSearch);
  $("md-keyword")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      mdSearch();
    }
  });
  on("md-load-collection", mdLoadCollection);
  $("md-kind")?.addEventListener("change", mdUpdateCollectionHint);
  on("md-load-rank", mdLoadRank);
  on("md-select-all", () => {
    mdState.items.forEach((item) => mdState.selected.add(mdKey(item)));
    mdRenderResults();
  });
  on("md-select-none", () => {
    mdState.selected.clear();
    mdRenderResults();
  });
  on("md-download", () => mdDownload(mdSelectedItems()));
  on("md-download-all", () => mdDownload(mdState.items));
  on("md-export", mdExport);
  on("md-retry-failed", async () => {
    try {
      const data = await api("/api/music/queue/retry-failed", { method: "POST" });
      toast(`已重新排队 ${data.count} 首`);
      refresh();
    } catch (err) {
      toast(`重试失败：${err.message}`);
    }
  });
  on("md-clear", async () => {
    try {
      const data = await api("/api/music/queue/clear", { method: "POST" });
      toast(`清掉 ${data.count} 条已结束任务`);
      refresh();
    } catch (err) {
      toast(`清空失败：${err.message}`);
    }
  });
  on("md-open", async () => {
    try {
      const path = (appConfig && appConfig.music_dir) || "";
      await api("/api/reveal", { method: "POST", body: JSON.stringify({ path }) });
    } catch (err) {
      toast(`打不开：${err.message}`);
    }
  });
  on("md-theme", mdCycleTheme);
  on("md-login", mdStartLogin);
  on("md-logout", mdLogout);
  on("md-qr-close", mdStopLogin);
  on("md-ncm-toggle", () => {
    const input = $("md-ncm-path");
    const go = $("md-ncm-go");
    input.hidden = !input.hidden;
    go.hidden = input.hidden;
    if (!input.hidden) input.focus();
  });
  on("md-ncm-go", mdImportNcm);
  on("md-convert-go", mdConvertLocal);
  on("cv-ncm-go", cvStartNcm);
  on("cv-go", cvStartConvert);
  on("cv-retry-failed", async () => {
    try {
      const data = await api("/api/music/queue/retry-failed", { method: "POST" });
      toast(`已重新排队 ${data.count} 个任务`);
      refresh();
    } catch (err) {
      toast(`重试失败：${err.message}`);
    }
  });
  on("cv-clear", async () => {
    try {
      const data = await api("/api/music/queue/clear", { method: "POST" });
      toast(`清掉 ${data.count} 条已结束任务`);
      refresh();
    } catch (err) {
      toast(`清空失败：${err.message}`);
    }
  });
  on("md-lib-refresh", () => mdLibLoad(false));
  on("md-lib-more", () => mdLibLoad(true));
  on("md-lib-m3u", mdLibM3u);
  on("md-lib-open", async () => {
    try {
      const path = (appConfig && appConfig.music_dir) || "";
      await api("/api/reveal", { method: "POST", body: JSON.stringify({ path }) });
    } catch (err) {
      toast(`打不开：${err.message}`);
    }
  });
  on("md-lyric-toggle", () => mdToggleLyrics());
  $("md-lib-search")?.addEventListener("input", () => {
    clearTimeout(mdLibTimer);
    mdLibTimer = setTimeout(mdLibLoad, 300);
  });
  on("md-modal-close", mdCloseModal);
  $("md-modal")?.addEventListener("click", (event) => {
    if (event.target.id === "md-modal") mdCloseModal();
  });
  on("md-play", mdTogglePlay);
  on("md-prev", () => mdStep(-1));
  on("md-next", () => mdStep(1));
  on("md-mode", () => {
    const order = ["list", "single", "shuffle"];
    mdState.mode = order[(order.indexOf(mdState.mode) + 1) % order.length];
    mdUpdateModeLabel();
  });
  mdBindPlayer();
  mdRestoreTheme();

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      $("drawer").hidden = true;
      configFilled = false;
      mdCloseModal();
      mdStopLogin();
      closeArchive();
    }
  });
}

bind();
refresh();
const initialView = (location.hash || "").replace(/^#/, "");
if (VIEWS[initialView]) switchView(initialView);
setInterval(() => {
  if (!document.hidden) refresh();
}, 1500);

setInterval(() => {
  fetch("/api/ping", { cache: "no-store" }).catch(() => {});
}, 5000);

