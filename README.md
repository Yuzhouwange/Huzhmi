# DouyinKit

抖音 / TikTok / B 站视频下载器 + 音乐下载（网易云 / QQ / 酷狗 / 酷我 / NCM），本地桌面应用。核心逻辑纯 Python 标准库，**不需要 pip 安装任何东西**。

## 音乐库：体检 + 按专辑批量转换

侧栏「音乐体检」两件事：

1. **体检**：揪出「有后缀但其实不能播」的文件（HTML 错误页、0 字节、截断、无帧头）
2. **按专辑转换**：读标签（ID3v2/ID3v1/FLAC/MP4）里的艺术家 / 专辑 / 轨号，
   批量转成 **MP3 320k / WAV / FLAC**，产物落在
   `输出目录/艺术家/专辑/01 标题.mp3`；每首转完都会校验可解码 + 时长正常，
   坏的一律判失败并删掉

ffmpeg 放 `data/tools/ffmpeg.exe` 会自动被发现（也支持设置里手填路径、PATH）。
体检页只处理**明文音频**；NCM 解密与平台下载在「音乐下载」页（见下文）。

## 音乐体检（不下载，只保证「能播」）

侧栏「音乐体检」：填一个目录 → 递归检查 mp3 / wav / flac / m4a / ogg，
把**有后缀但其实不能播**的文件揪出来：

| 判死条件 | 说明 |
|---|---|
| 内容是 HTML / JSON | 平台错误页被存成了 `.mp3`（最常见的坏法） |
| 0 字节 / 小于 8 KB | 空文件或试听片段 |
| 没有帧头 / 容器结构坏 | 后缀对，内容不对 |
| WAV 数据段比文件大 | 下载中断，被截断 |
| 时长 < 1 秒 | 截断 |

转换任务的产物也会自动过这一关：体检不过就判失败、删掉坏文件，不留半成品。

## 音乐下载：搜索 / 专辑 / 批量 / 转 MP3·WAV

侧栏「音乐下载」对标 Music_Download 的那一套，并且补上了它没有的 NCM 解密：

| 能力 | 说明 |
|---|---|
| 多平台搜索 | 网易云 / QQ 音乐 / 酷狗 / 酷我，按歌名或歌手搜 |
| 扫码登录 | 网易云 App 扫码拿 `MUSIC_U`；登录后按账号权限下 320k / 无损 / 会员曲 |
| 会员曲 | 有会员 Cookie 时优先无损 → 320k → 192k → 128k；没权限会明确说原因 |
| 本地音乐库 | 扫音乐目录，按标签列出；直接播放、转 MP3/WAV、删文件、导出 M3U |
| 封面 / 标签 | 下载时抓封面写进 MP3/FLAC，并补标题 / 歌手 / 专辑 |
| 同步歌词 | 播放时自动载入同名 `.lrc`，按时间高亮当前行 |
| 专辑 / 歌单 | 填 ID 或整条链接：`36412633`、`music.163.com/album?id=…`、`y.qq.com/n/ryqq/playlist/…` 都认 |
| 排行榜 | 网易云（飙升/新歌/热歌/原创）、QQ（热歌/新歌/飙升/原创）、酷狗（TOP500/飙升/新歌） |
| 批量下载 | 勾选任意多首 → 一个队列、最多 6 并发；失败单条重试，坏文件自动删 |
| 暂停 / 继续 | 下载中或排队中都能「暂停」，已下部分留在 `.part`；继续时按 HTTP Range 续传 |
| 取消 / 清空 | 任意未结束任务可取消（连带删 `.part`）；「清空已结束」只删记录，不动文件 |
| 自定义目录 | 「保存到」随便填；留空用设置默认目录，上次填的自动记住 |
| 自动转码 | 下载后统一转 **MP3 320k / WAV / FLAC**，也可「保留原格式」；转完再体检一次 |
| 自行转码 | 已下载完成的歌行上有「转MP3 / 转WAV」；另有「本地转换」卡片，填文件或文件夹即可批量转，**原文件保留** |
| 歌词 / 评论 | 行内「歌词」「评论」弹窗；下载时可选把 `.lrc` 一起存到歌曲旁边 |
| 导出 Excel | 当前搜索结果一键导出 `.xlsx`（表头、冻结首行、列宽都排好，不依赖 openpyxl） |
| NCM 解密 | 内置纯 Python NCM 解码（等价 [`ncmc`](https://github.com/magic-akari/ncmc)），选文件夹批量解密并转码；也可在设置里指定外部 `ncmc.exe` |
| 本地播放器 | 下载完成的歌可直接播：上一首/下一首/单曲循环/随机，支持拖动进度（HTTP Range） |
| 换肤 | 顶栏「换肤」循环：默认 / 青绿 / 紫罗兰 |

**落盘规则**：`音乐目录/歌手/专辑/歌名.mp3`；没有专辑就少一层。目标已存在且能播则跳过，
不会重复下载，也不会覆盖你后来改过的文件。自行转码的产物默认落在源文件旁边（或你指定的
输出目录），**源文件不动**。

**NCM 解码**（`douyinkit/music/ncm.py`）：读完头部两把 AES-128-ECB 密钥，解出
RC4 风格 keybox 与歌曲元数据，再按 `keybox[(keybox[j] + keybox[(keybox[j]+j)&0xff]) & 0xff]`
流式异或音频体（1 MB 分块，块长是 256 的整数倍，相位不会错）。密钥块同时兼容
「前缀在加密前」和「前缀在加密后」两种客户端写法；封面区按 `image_space / image_size`
正确跳过对齐填充——这两点是最容易把 MP3 解成噪声的地方。

**扫码登录 / 会员曲**：网易云走官方二维码接口——`unikey` → 前端本地画二维码
（`web/vendor/qrcode.min.js`，不把 key 发给第三方）→ 每 2 秒轮询 `client/login`，
803 时从 `Set-Cookie` 收下 `MUSIC_U` 写进配置。之后解析直链会按
`lossless → exhigh → higher → standard` 依次尝试，有会员就拿到 FLAC / 320k；
QQ 音乐在 vkey 请求里带上 `uin` 与 `qqmusic_key`，会员曲同样能出直链。
QQ / 酷狗 / 酷我没有通用扫码接口，设置里选平台粘贴浏览器 Cookie 即可，保存后立即生效。

**本地音乐库 / 封面 / 歌词**：音乐库直接扫 `music_dir`（按标签，不依赖任务记录），
支持筛选、播放、转 MP3/WAV、删除、导出 `.m3u8`；播放器支持列表/单曲/随机。
下载时会顺手抓 600×600 封面存成 `cover.jpg`，并写进 MP3（ID3 APIC）或 FLAC
（attached_pic）；不转码的产物用 `-c:a copy` 补标签，不重编码。播放时读同名
`.lrc`，按时间轴高亮当前行；没有歌词就显示提示，不会留空面板。

**各平台现实情况**（不是程序 bug）：

- 免费/可试听曲能直接拿到直链；**会员曲、无版权曲平台本来就不发直链**，任务会失败并写明原因，
  不会写一个 0 字节的假文件。要下 320k / 会员曲，用「网易云扫码登录」，或在设置里填对应平台
  Cookie（`data/config.json` 的 `music_cookie_netease` / `..._qq` / `..._kugou` / `..._kuwo`）
- 网易云会对密集请求回 `-462`，适配器自动退避重试 4 次，并改用 `/api/v1/album/…` 这种更稳的接口
- 酷我公开接口只稳定提供搜索 / 歌词 / 直链；专辑、歌单、榜单要网页会话，界面会直接说明，
  不硬凑一个假列表

## 界面

左侧分栏切换工作区，任务按类型分流到各自的列表里，互不干扰：

| 分栏 | 内容 |
|---|---|
| **抖音下载** | 粘链接 / 主页多选，只显示抖音任务 |
| **抖音发现** | 热榜（匿名可用）+ 关键词搜索（需登录），结果可勾选下载 |
| **B站下载** | 视频、分P、番剧（先弹选集）、UP 主主页，只显示 B 站任务 |
| **作品档案** | 扫描下载目录里的视频 / 图片，预览、打开、删除、筛选 |
| **视频转音频** | 列出已下载的视频，一键提取音轨（格式可选） |
| **音频转换** | NCM → MP3/WAV/FLAC/…；音频互转 MP3/WAV/FLAC/M4A/AAC/OGG/Opus/WMA/AIFF |
| **音乐下载** | 搜索、扫码、专辑/歌单、批量下载、转码、本地音乐库 |
| **音乐体检** | 查出有后缀但播不了的坏文件，按专辑批量转换 |

### 音频转换（单独工作区）

- **NCM 解密并转换**：选文件或整个文件夹，输出 MP3 320k / WAV / FLAC / M4A / AAC /
  OGG / Opus / WMA / AIFF；内置解码，不需要 `ncmc.exe`
- **音频互转**：上面这些格式任意互转，也可以处理 `.ape` / `.wma` 输入
- 原文件保留，产物写源文件旁或指定输出目录；同格式自动跳过
- 批量任务进同一个队列，可取消 / 重试 / 清空；每首转完都过一遍音频体检
- 队列只显示转换类任务，不会和下载任务混在一起

### 抖音发现 / 作品档案

- **热榜**：拉抖音公开热榜（50 条），点「搜」直接按词搜索；热榜本身不需要登录
- **关键词搜索**：走真实浏览器页面里的搜索接口；搜索需要登录态，没登录会明确提示先扫码
- **结果多选**：搜索结果复用封面色块，勾选后直接加入下载队列
- **作品档案**：递归扫描下载目录，视频可站内播放（Range 拖动）、图片可预览；
  支持按标题 / 作者目录筛选，打开文件、删除文件
- **主页日期筛选**：主页多选面板加了「从 / 到」日期，只显示和全选这个区间的作品
- **重新下载**：抖音 / B 站提交区新增「重新下载已存在」，勾上后绕过 SQLite 去重，
  用来覆盖旧文件或补下坏文件

## 启动

```bash
python run.py
```

默认打开一个**独立窗口**（Edge/Chrome 的 `--app` 模式：没有地址栏、没有标签页，
任务栏里就是一个应用窗口）。关掉窗口 = 退出程序。

其他方式：

```bash
python run.py --no-window    # 退回默认浏览器
python run.py --no-browser   # 只起服务（调试 / 远程访问本机）
python run.py --port 9000    # 换端口
```

命令行模式：

```bash
python -m douyinkit.cli "https://v.douyin.com/xxxxxxx/"
python -m douyinkit.cli links.txt -o D:\Videos\Douyin
```

## 功能

**单条下载**
- 无水印视频 + 图集，自动识别类型
- 批量粘贴、并发下载（默认 3）、失败重试
- SQLite 去重，同一作品重复提交自动跳过

**TikTok**
- 单条公开视频 / 图集：`tiktok.com/@user/video/...`、`vm.tiktok.com` / `vt.tiktok.com` 短链
- 解析页面里的 `__UNIVERSAL_DATA_FOR_REHYDRATION__`，取无水印 `playAddr`
- 页面 Cookie（`ttwid` / `tt_chain_token`）会随媒体请求带上，避免 CDN 403
- 主页批量暂不支持（需要签名接口），会明确提示先粘单条

**主页批量**（本次新增）
- 粘贴博主主页链接（或整段分享文案），自动识别 `sec_uid`
- 拉取作品列表，封面九宫格展示，**点封面多选**，支持全选 / 清空 / 加载更多
- **每条任务可选自定义下载目录**，留空则用设置里的默认目录
- 主页数据走真实浏览器页面，不受签名算法变更影响
- 未登录时单页最多约 43 条；**抖音只对未登录用户开放第一页**，扫码登录后可加载全部，
  面板会直接写明「已加载 43 / 共 529」并给出登录入口

**B 站**（本次新增）
- 支持 `BV` / `av` / `b23.tv` 短链，自动识别分 P（`?p=3`）
- **番剧 / 影视**：贴 `bilibili.com/bangumi/play/ss…` 或 `ep…`，整季选集一键勾选（只下账号本来就能播的集）
- **UP 主主页可视化多选**：贴主页链接（space.bilibili.com/<mid>、m.bilibili.com/space/<mid>、www.bilibili.com/space/<mid> 都认），封面网格里点选要下的几条
- **从任意一条视频进作者主页**：任务行上的「该作者的作品」，和抖音那边一致
- **选集识别**：粘一条多 P 视频或合集里的视频，会弹出选集清单（每集标题 + 时长），
  默认只勾当前这一集，可「全选」整部下，也可「只下当前这一集」；单集视频不会打扰
- **账号扫码登录**：点「B 站扫码登录」，用 App 扫一下，Cookie 自动收进配置
- **DASH 高清**：走 `fnval=4048` 拿 DASH 流，匿名常见 1080P，登录后可到 1080P60 / 4K / HDR
- **音视频自动合成**：DASH 的视频流和音频流分开下载，**用纯 Python 重新装箱成普通 MP4**，不需要装 ffmpeg
- **画质上限可调**：设置里可选「最高可用 / 1080P+ / 1080P / 720P / 480P / 360P」
- **自动挑档位**：DASH 与整段 MP4 都会问一遍，谁的实际档位高就用谁（匿名时整段 MP4 常常比 DASH 高一档）
- **不转码**：合成是重新封装，画面和音轨字节原样保留，画质零损失
- **失败自动降级**：DASH 直链被 CDN 拒了会退回整段 MP4，不会让你一个文件都拿不到

**归档分类**（本次新增）
- 默认按 `{platform}/{author}/{title}` 分层：平台 → 作者 → 作品，**一个作品一个独立目录**
- 模板可在设置里改，支持 `{platform}` `{author}` `{title}` `{kind}` `{id}` `{date}`
- 想让视频和图集彻底分开放，就用 `{platform}/{kind}/{author}/{title}`
- 模板每一段单独做文件名清洗，`..`、空段、未知占位符会被丢弃

**通用**
- 文件名自动清洗：非法字符转 `_`、去掉控制符/零宽字符、首尾点和空格、规避 `CON` 之类保留名；emoji 保留
- **自定义保存路径**：抖音 / B 站输入框下面都有「下载到」，填哪下哪；留空用设置默认目录，
  上次填的会记在浏览器里；主页多选面板还有自己独立的目录
- **清空已结束**：抖音 / B 站 / 音乐队列都有一键清空（只删记录，文件不动）
- **重复链接不重复下**：同一作品已在排队 / 下载中时再粘一次会跳过；
  勾「重新下载已存在」可绕过
- **只接受本机页面**：HTTP 层校验 Host / Origin，挡住 DNS rebinding 和外部网页调用
- **单实例**：命名互斥量保证只跑一个；重复双击会提示使用已打开的窗口
- **重启应用**：设置里有「重启应用」，先释放端口/数据库，再拉新进程
- **关闭即清历史**：默认退出时清掉已完成 / 失败 / 已取消的任务记录（文件保留，
  暂停中的不动）；设置里可关，代码注释在 `config.py` 的 `clear_history_on_exit`
- **长列表分页**：作品档案 / 本地音乐库每页 200 条，点「加载更多」追加，不再一次性渲染几千行
- **音质档位**：转码 / 音乐下载可选「高质量 / 标准 / 省空间」，
  MP3 320/192/128k、AAC 320/256/128k、OGG q8/q6/q3、Opus 192/160/96k
- **规则热更新**：`data/rules.json` 可覆盖抖音分享页链路；设置里填 JSON 地址即可热更新。
  分享页三条链路全失败时，按开关自动改用浏览器页面桥兜底
- **暂停 / 继续**：单条任务随时暂停，已下的分片留在 `.part`，点「继续」从断点接着下
- **一键暂停**：顶栏和任务队列各有一个「暂停全部」，点一下新任务不开始、正在下的也一起停；
  再点变「继续全部」
- **取消任意任务**：排队 / 下载中 / 已暂停 都能取消（每行一个「取消」）；
  正在下载的会**连已经下好的部分一起删掉**，不留半截文件
- **一键取消全部**：任务队列标题栏的「取消全部 (N)」，把没结束的任务全部取消并清理
- **图集不会变成背景音乐**：抖音给图集作品也带一个 `video` 字段（自动生成的
  「图文 + BGM」视频）。程序只在**没有图片**时才用它，图集一律下原图
- **目录不重复套**：「下载到」里已经写了 `平台/作者` 时不会再叠一层
- **实况图（Live Photo）**：抖音的实况照片是「一张图 + 一段几秒的 mp4」，
  默认只存那段 **mp4**（设置里可切成「图片 + 视频」或「只图片」）；
  分享页没有实况信息，程序会借页面会话再取一次详情
- **提取音频**：已完成的任务行上点「提取音频」，把视频里的音轨单独存成 `.m4a`——
  **纯 Python 解复用，不重编码**（音质和原视频逐样本一致），不需要 ffmpeg；
  设置里可切 MP3 320k / MP3 192k / FLAC / WAV（这几个要 ffmpeg）
- **重启自动续跑**：关窗口时没跑完的任务，下次启动自动回到队列继续下；
  已暂停的保持暂停，等你点「继续」
- 可填 Cookie / 代理应对风控
- 只监听 `127.0.0.1`，不对外暴露

## 三条解析路径

### 抖音单条：移动分享页

### 抖音单条：移动分享页

抖音桌面页和 `iesdouyin` 分享页现在都只返回 JS 挑战壳（6 KB / 72 KB，无数据），
`aweme/detail` 缺 `a_bogus` 签名会 403。实测能拿到数据的是：

> `https://m.douyin.com/share/<video|note>/<aweme_id>` + iPhone UA + 先访问 `douyin.com` 拿 `__ac_nonce`

连续 8 次实测 100% 成功，每次 1.3–1.7 秒。解析器按
`m.douyin.com`（移动 UA）→ `iesdouyin`（移动 UA）→ `iesdouyin`（桌面 UA）依次降级。

### 主页：浏览器页面桥

主页列表没有捷径：老 API `web/api/v2/aweme/post/` 已返回空，用户分享页同样是挑战页。
纯 Python 签名也不够——除了 `a_bogus`，接口还校验 `x-secsdk-web-signature`（Argus SDK）和 `uifid`：

```
403 Blocked by ArgusSecurityPlugin Uifid Not Found
```

这两样只有页面 JS 算得出来。所以主页功能走 **页面桥**：

1. 起一个 headless Edge/Chrome（`--remote-debugging-port`）
2. **先打开这个作者的主页**（预热：页面会装上 `uifid` / Argus 签名环境，直接 fetch 会 403）
3. 用标准库实现的 WebSocket + CDP，在页面上下文里 `fetch('/aweme/v1/web/aweme/post/')`
4. **签名由页面自己补**，我们只取 JSON；翻页用 `max_cursor` 原样回传
5. 扫码登录拿到的 Cookie 会写回这个浏览器（`Storage.setCookies`），否则永远是「未登录视角」

实测（529 条作品的账号）：

| 场景 | 结果 |
|---|---|
| 只访问首页就 fetch | `403 Blocked by ArgusSecurityPlugin Uifid Not Found` |
| 预热主页后 fetch | 200，`count=20` → 23 条 |
| `count=50` | **43 条**（未登录单页上限），`has_more=1` |
| 带 `max_cursor` 翻第二页 | 200，但 `aweme_list` 为空 → 抖音的登录闸门 |
| 主页正文 | 显示「作品 529」+「登录后查看更多作品」 |

所以这里有两个必须做对的地方：**预热**（否则 403）、**复用登录 Cookie**（否则永远只有第一页）。
程序现在会把「43 / 共 529」和登录入口直接显示出来，而不是让你以为作者只有 43 条作品。

### B 站 UP 主：WBI 签名接口

UP 主投稿列表走 `x/space/wbi/arc/search`，需要 WBI 签名：

1. `x/web-interface/nav` 取 `img_key` / `sub_key`
2. 按固定混淆表重排后取前 32 位作为 `mixin_key`
3. 参数排序 + `wts` 时间戳，`w_rid = md5(query + mixin_key)`

实测：**490 个作品、每页 30 条、0.8 秒**；翻页用 `pn`（页码），不重复。

两个风控坑，都踩过：

| 现象 | 原因 | 处理 |
|---|---|---|
| `code=-352` 风控校验失败 | 缺 `buvid3` | 先访问首页，再调 `x/frontend/finger/spi` 拿 `b_3`/`b_4` 写进 Cookie |
| `HTTP 412 Precondition Failed` | 请求头太「脚本」 | 补 `Origin` / `Sec-Fetch-*`，并复用同一会话（buvid3 每次变会被判爬虫） |

仍有概率性拦截，代码里做了 3 次重试（换会话 + 退避），连续 6 次实测成功 5 次；填 B 站 Cookie 后更稳。

### B 站：DASH 音视频分离 → 纯 Python 合成

`playurl` 用 `fnval=4048`（DASH + HDR + 4K + 杜比 + 8K + AV1）请求时，B 站返回的是
**音视频分离**的两路 fMP4（`.m4s`），播放器直接打不开。主流做法是调 ffmpeg 合并，
本项目不想把 ffmpeg 变成硬依赖，于是自己做了一个 fMP4 → 普通 MP4 的重新装箱器
（`douyinkit/mp4mux.py`，纯标准库）：

1. 读两路各自的 `moov`：拿 timescale、`stsd`（编码参数原样保留）、`elst`（片头预滚）
2. 扫所有 `moof`，把 `trun` 里每个 sample 的（偏移、大小、时长、flags、合成时间）摊成样本表
3. 按时间戳把两路分片交错排序，一个分片 = 输出文件里的一「块」
4. 写 `ftyp` + `moov`（`stsd`/`stts`/`ctts`/`stss`/`stsc`/`stsz`/`stco`）+ `mdat`

三个曾经踩到的坑，都在代码里留了注释：

| 现象 | 原因 | 处理 |
|---|---|---|
| 播放器显示时长为空、只播一秒就结束 | 照抄了源文件 `elst` 的 `segment_duration=0` | 按自己的时长重写 `elst`，只保留 `media_time` |
| 两个轨冲突、ffmpeg 读 151 个包就 EOF | 视频/音频来自两个文件，`track_id` 都是 1 | 输出时重排成 1、2 |
| 声音和画面对不上 100ms | 丢了 `elst` 的预滚偏移 | 保留 `media_time`（1600 tick = 100ms） |

实测（同一对 `.m4s`，138 秒 480P）：

```
$ ffmpeg -i out.mp4 -map 0:v -f framemd5  # 与 ffmpeg 自己的 -c copy 重封装逐帧对比
视频 4127 帧：哈希全部一致，PTS 完全一致
音频 5925 帧：哈希全部一致，PTS 完全一致
时长 00:02:17.58（与 ffmpeg 一致）；Windows 资源管理器也正确显示 2:17
```

### 封面防盗链

B 站图床 `i*.hdslb.com` 直接引用会 403（Referer 变成 `127.0.0.1`）。所以前端封面统一走本地代理
`/api/thumb?url=`，服务端带正确 Referer 取图。代理只允许 `hdslb.com` / `douyinpic.com` 等白名单域名，
避免退化成任意 URL 转发器（SSRF）。

抖音封面是**带签名的 URL**（`x-expires` / `x-signature`），这里有个坑：`parse_qs` 已经把参数解码过
一次，代理里再 `unquote` 一次会把签名改坏，CDN 回 403 —— 表现就是整页封面全是「无封面」。
现在只解码一次，实测 12 条封面 0 失败。

## 账号登录（扫码，自动提取 Cookie）

设置里有「账号」区块，抖音和 B 站各一行：

| 平台 | 按钮 | 判定登录成功的 Cookie |
|---|---|---|
| 抖音 | 扫码登录 | `sessionid` 或 `sid_guard` |
| B 站 | 扫码登录 | `SESSDATA` |

点「扫码登录」会弹出一个**有界面**的浏览器打开对应登录页：

1. 用手机 App 扫码（抖音若没自动弹登录框，点右上角「登录」）
2. 程序每 1.5 秒从浏览器里读一次 Cookie
3. 检测到登录态后，把关键 Cookie 拼成字符串存进 `data/config.json`（`cookie` / `bilibili_cookie`）
4. 浏览器自动关闭，界面显示「✅ 已登录」

用有头浏览器而不是自己画二维码，是为了把二维码、风控、二次验证全交给平台自己处理，
我们只在登录成功后取 Cookie——不需要复刻登录协议，也不会把凭据发给第三方。

登录后能得到：更高画质档位、UP 主列表更稳（少触发 -352 / 412）、下载时带账号 Cookie。
「退出」会清掉配置里的 Cookie，并取消正在等待的登录、关掉浏览器。

### 番剧 / 影视：支持，但不破解

`bilibili.com/bangumi/play/ss…`（整季）和 `ep…`（单集）现在可以解析了：

1. `pgc/view/web/season` 取季度信息和全部集数（`episodes[].id/cid/title/long_title`）
2. 粘 `ss` 链接会弹选集清单，可整季勾选；粘 `ep` 链接只勾那一集
3. 取流走 `pgc/player/web/playurl`（`fnval=4048`），同样走 DASH + 合成

**边界很清楚：只下当前账号本来就能播的集。** 接口给什么档位就用什么档位：
匿名一般 480P，登录后能到 1080P，大会员账号能拿到更高——但程序不会去伪造会员状态。
遇到没有权限的集会直接报错（`87007 充电专属` / `87008 需要大会员` / `-10403 地区限制`），
不尝试绕过。

`bilibili.com/bangumi/media/md…` 是「媒体页」，不是可播放地址，粘了会提示你点进具体一集。

## 三种进入「可视化多选」的方式

1. **直接粘主页链接**：`https://www.douyin.com/user/<sec_uid>`、`https://space.bilibili.com/<mid>`
2. **从任何一条视频进**：先下这条视频，任务行上会出现「该作者的作品」，点一下就能挑他/她的其它视频
3. **粘短链也行**：`v.douyin.com/xxx`、`b23.tv/xxx` 提交时会先展开（`/api/resolve`），是主页就进多选，是单条就照常下

面板会自己滚到视野里，避免「点了没反应」的错觉。选中后可以填自定义目录再下载。

## 归档目录模板

设置里有两个模板：

- **目录模板**（默认 `{platform}/{author}`）：决定视频放进哪个文件夹
- **文件名模板**（默认 `{title}`）：决定视频文件叫什么

默认不再给每条作品单开一层文件夹 —— 作者目录下就是视频文件：

```
E:\视频\
└─ 抖音\
   └─ 可爱的光头强\      ← 作者目录，一眼看出是谁的
      ├─ 开局一条狗.mp4
      └─ 大结局.mp4
```

想连播直接选中作者目录即可，不用一层层点进去。

> 老版本默认是 `{platform}/{author}/{title}/01.mp4`。升级时会自动把这条**默认值**
> 换成新的；你自己改过的模板不会被动。

| 占位符 | 含义 | 示例 |
|---|---|---|
| `{platform}` | 平台 | 抖音 / B站 |
| `{author}` | 作者昵称 | 言吾言_ |
| `{title}` | 作品标题 | 科学家们用物理智斗爱因斯坦？ |
| `{kind}` | 内容类型 | 视频 / 图集 |
| `{id}` | 作品 ID | BV1fZJ26mEiY |
| `{date}` | 发布日期（取不到就用今天） | 2026-10-18 |
| `{index}` | 第几个文件（只用于文件名模板） | 02 |

常用搭配：

| 想要的效果 | 目录模板 | 文件名模板 |
|---|---|---|
| 默认：按作者分文件夹 | `{platform}/{author}` | `{title}` |
| 文件名里也带作者 | `{platform}/{author}` | `{author} - {title}` |
| 全部平铺到保存目录 | 留空 | `{author} - {title}` |
| 作者 + 日期分组 | `{platform}/{author}/{date}` | `{title}` |
| 视频 / 图集分开 | `{platform}/{kind}/{author}` | `{title}` |

- 文件名重名自动加 ` (2)`、` (3)`；**重试同一个任务会复用原来的名字**，不会越重试越多副本
- 一条作品有多个文件（图集多图）时自动补 ` - 01`、` - 02`，除非模板里自己写了 `{index}`
- 多选时在面板里填的「下载到」会覆盖保存目录，两个模板仍然生效

模板每一段/每一处都单独清洗：`..` 会被丢弃、非法字符变 `_`、未知占位符整个删掉，
所以不会出现越级路径或半截花括号目录。

### 把老库拍平

老版本已经按「标题文件夹」存好的，用脚本搬到上一层（先演练、再动手）：

```bash
python flatten_downloads.py "E:\视频\抖音\可爱的光头强"           # 只打印计划
python flatten_downloads.py "E:\视频\抖音\可爱的光头强" --apply   # 真的移动
```

```
标题 A\01.mp4        -> 标题 A.mp4
图集\01.jpg, 02.jpg  -> 图集 - 01.jpg, 图集 - 02.jpg
```

名字冲突自动加 ` (2)`；只删除**搬空的**子目录，有内容的目录不碰。

## 目录

```
douyin-kit/
├─ run.py                 启动入口
├─ build_exe.py           打包成 exe
├─ flatten_downloads.py   把老库的「标题文件夹」拍平
├─ douyinkit/
│  ├─ platforms/          平台适配器（每个平台一个模块，互不 import）
│  │  ├─ base.py          接口：matches / normalize / extract / profile / media_headers…
│  │  ├─ douyin_platform.py   抖音：单条走 douyin.py，主页走 bridge.py
│  │  ├─ bilibili_platform.py B 站：view + playurl + DASH 选流 + UP 主列表
│  │  └─ registry.py      按链接识别平台、按名字取平台、给前端下发描述
│  ├─ net.py              公用零件：UA、文本解码、扩展名推断、PlatformError
│  ├─ douyin.py           抖音单条解析（分享页 _ROUTER_DATA）
│  ├─ bilibili.py         B 站解析（view + playurl，DASH 选流，BV/av/短链）
│  ├─ mp4mux.py           DASH 音视频合成（fMP4 → 普通 MP4，纯标准库）
│  ├─ bridge.py           抖音主页批量（页面桥 + aweme/post 接口）
│  ├─ cdp.py              极简 CDP 客户端（WebSocket + Runtime.evaluate）
│  ├─ engine.py           队列、线程池、下载、进度、文件名模板（平台无关）
│  ├─ music/              音乐下载
│  │  ├─ providers.py     网易云 / QQ / 酷狗 / 酷我：搜索、专辑歌单、歌词评论、直链
│  │  ├─ login.py         网易云扫码登录（unikey → 轮询 → MUSIC_U）
│  │  ├─ library.py       本地音乐库：标签、歌词、M3U、删除
│  │  ├─ ncm.py           NCM 解密（纯 Python，等价 ncmc；支持外部 ncmc.exe）
│  │  ├─ aes.py           AES-128-ECB（S 盒按定义现算，不抄常量）
│  │  ├─ service.py       音乐队列：下载 → 体检 → 转码 → 歌词 → 落盘
│  │  └─ xlsx.py          导出 Excel（标准库 zipfile + XML）
│  ├─ library.py          老库拍平（探测 / 计划 / 执行）
│  ├─ store.py            SQLite（任务 / 资源 / payload / 自定义目录 / 音乐任务）
│  ├─ server.py           本地 HTTP + JSON API（平台无关）
│  ├─ cli.py              命令行
│  └─ config.py           配置
├─ web/                   前端页面（任务队列 + 主页多选 + 音乐下载）
├─ tests/                 241 个单元测试（DASH 选流、MP4 装箱、平台分离、NCM、音乐队列）
└─ data/                  配置与数据库（运行时生成）
```

## 平台分离（新增平台只动一个文件）

抖音和 B 站的提取逻辑各自独立，上层不再出现 `if 平台 == "bilibili"`：

```
platforms/
├─ base.py               Platform 接口（8 个方法 + 3 个可选钩子）
├─ douyin_platform.py    抖音：单条 douyin.py / 主页 bridge.py / 登录 / 下载头
├─ bilibili_platform.py  B 站：view+playurl / DASH 选流 / UP 主列表 / 登录 / 下载头
├─ tiktok_platform.py    TikTok：单条 / 图集 / 短链 / 页面 Cookie 下载头
└─ registry.py           resolve(url) 识别平台、by_name 名字取、describe 下发前端
```

一个平台要回答的问题：

| 方法 | 作用 | 抖音 | B 站 |
|---|---|---|---|
| `matches` | 这链接是不是我的 | douyin.com / iesdouyin.com | bilibili.com / b23.tv |
| `normalize` | 去重键 | `aweme:<id>` | `bili:<BV号>`（带 `:p3`） |
| `extract` | 解析一条作品 | 分享页 `_ROUTER_DATA` | view + playurl（DASH） |
| `extract_fallback` | 高质量链路失败后降级 | 重试一次 | 退回整段 MP4 |
| `profile` / `profile_more` | 主页列表/翻页 | 页面桥（要预热 + 登录 Cookie） | WBI 签名接口 |
| `media_headers` | 下载用什么 Referer/Cookie | `www.douyin.com` + `cookie` | `www.bilibili.com` + `bilibili_cookie` |
| `login_state` / `start_login` / `logout` | 扫码登录 | 有头浏览器读 Cookie | 同左 |

配套改动：

- 公共零件（UA、`decode_text`、`guess_ext`、`PlatformError`）挪到 `net.py`；
  以前 `bilibili.py` 里写着 `from .douyin import decode_text`，现在两边都不再互相 import
  （`tests/test_platforms.py` 会检查这一点，防止以后又缠回去）
- `douyin.ExtractError` 和 `bilibili.BiliError` 都继承 `net.PlatformError`，engine 只 catch 一次
- 下载请求头由平台自己给（`media_headers`），不再是「Referer 里有没有 bilibili.com」的猜法
- `/api/bootstrap` 下发 `platforms`（名字/显示名/单页条数/识别正则/作者主页模板），
  前端用它识别链接、取标签和分页大小，不再硬编码平台
- 登录路由收敛成 `/api/login/<平台>`、`/api/login/<平台>/status`、`/api/logout/<平台>`；
  旧的 `/api/bili/*`、`/api/douyin/*` 保留兼容

新平台接入：写一个 `Platform` 子类 → 加进 `registry.PLATFORMS`。engine、server、前端都不用改。

### 媒体处理层（转换 / 提取）

和平台适配器同一套思路，只是换成「能力」：

```python
# douyinkit/media/transforms.py
register(Transform(name="audio_m4a", label="提取音轨（无损 m4a）", extension="m4a"))
register(Transform(name="audio_mp3", label="转 MP3 320k", extension="mp3",
                   needs="ffmpeg", codec_args=("-c:a", "libmp3lame", "-b:a", "320k")))
```

- `needs=None` → 纯 Python 实现，永远可用；`needs="ffmpeg"` → 找不到工具时明确报
  `ToolMissing`，`/api/bootstrap` 的 `capabilities` 会告诉前端哪个能点、为什么不能点
- 一条转换就是一个子任务，进度写进 `steps` 表（`job_id, idx, kind, status, progress,
  output_path`），以后串「下载 → 提取 → 转码 → 打标」只需要多加几行 step
- 加新能力 = 加一个 `Transform`，engine / API / 界面都不用动

## 打包成 exe

```bash
python build_exe.py                # -> dist/DouyinKit.exe（单文件，约 10 MB，无控制台）
python build_installer.py          # -> dist/DouyinKit-Setup-0.1.0.exe（含 ffmpeg）
python build_exe.py --console      # 保留控制台窗口，排错用
python build_exe.py --onedir       # 目录版：启动更快，排错方便
python build_exe.py --no-venv      # 用当前环境里已有的 PyInstaller
```

安装包用 Inno Setup 6 编译，**按用户安装**（默认 `%LOCALAPPDATA%\Programs\DouyinKit`，
不需要管理员权限），自带 `data/tools/ffmpeg.exe`，创建开始菜单快捷方式，可选桌面图标。
没装 Inno 时：

```powershell
winget install JRSoftware.InnoSetup
```

默认打成 **windowed**（没有黑色控制台窗口）：双击就是一个独立应用窗口，关窗即退出。
没有控制台所以看不到 print —— 日志写到 `data/log.txt`，启动崩溃会弹一个 Windows 对话框。

构建脚本默认在 `.build-venv/` 里装 PyInstaller，不污染日常 Python 环境；
`.build-venv/`、`build/`、`dist/`、`data/` 都已写进 `.gitignore`。

打包后路径会变，`douyinkit/paths.py` 负责区分：

| | 开发态 | exe |
|---|---|---|
| 只读资源 `web/` | 仓库根目录 | `sys._MEIPASS/web`（onefile 解包目录） |
| 配置 / 数据库 | 仓库 `data/` | **exe 同级 `data/`**，不可写时退回 `%LOCALAPPDATA%\DouyinKit\data` |

也可以用环境变量强制指定：`set DOUZY_DATA_DIR=D:\my-data`。

运行：

```bat
DouyinKit.exe                              :: 独立窗口
DouyinKit.exe --no-window                  :: 用默认浏览器打开
DouyinKit.exe --no-browser --port 9000     :: 只起服务
```

启动时会打印实际端口和**数据目录**（带 flush，重定向到文件也能立刻看到）：

```
DouyinKit 0.1.0 已启动：http://127.0.0.1:8765/
数据目录：D:\...\dist\data
按 Ctrl+C 退出。
```

两个打包后才会遇到的问题：

- **首次运行被 SmartScreen / 杀软拦**：单文件 exe 没有代码签名，误报很常见。
  点「更多信息 → 仍要运行」，或把 `dist\` 加进信任目录。
- **启动慢 1–3 秒**：onefile 每次都要把 10 MB 解到临时目录；想要快就用 `--onedir`。

打包实测（同一台机器，exe 里跑的）：

| 场景 | 结果 |
|---|---|
| `/api/health`、`/`（首页） | 200，页面正常 |
| 抖音单条下载 | done，2.7 MB |
| B 站 720P 下载 | done，14.3 MB，与源码版文件 **MD5 完全一致** |
| 抖音主页多选（页面桥） | 43 条 / 共 529，Edge 在 exe 内正常拉起 |
| 数据目录 | exe 同级 `data/` 自动生成 config.json + db |

## 前端测试（Playwright）

后端测试是全量单测；前端用 Playwright 驱动本机 Edge 做冒烟，不额外下载浏览器：

```bash
cd tests/ui
npm install          # 只装 playwright-core
node smoke.mjs       # 自己起服务、遍历 8 个分栏、检查热榜/格式/音质/档案，跑完关掉
```

## 接口
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/jobs` | 提交单条/多条链接，可带 `out_dir` / `force`（重新下载） |
| POST | `/api/profile` | 解析主页链接并拉第一页作品 |
| POST | `/api/resolve` | 展开短链并判断「主页 / 单条」 |
| POST | `/api/episodes` | 列出一条视频的选集（B 站分 P / 合集） |
| POST | `/api/profile/more` | 按 `max_cursor` 加载更多 |
| POST | `/api/select` | 提交多选作品，可带 `out_dir` / `platform` |
| GET | `/api/thumb?url=` | 封面代理（域名白名单） |
| GET | `/api/discover/hot?limit=` | 抖音热榜 |
| GET | `/api/rules` | 当前解析规则 |
| POST | `/api/rules` | 覆盖规则（只接受已知字段） |
| POST | `/api/rules/fetch` | 从 http(s) JSON 地址热更新规则 |
| POST | `/api/restart` | 重启应用（释放端口后拉起新进程） |
| POST | `/api/discover/search` | 关键词搜索，body `{"keyword","count"}`（需登录） |
| GET | `/api/archive?q=` | 扫描下载目录的视频 / 图片 |
| GET | `/api/archive/file?path=` | 本地媒体流（支持 Range） |
| POST | `/api/archive/delete` | 删除档案里的单个文件 |
| POST | `/api/jobs/<id>/retry` | 重试单条 |
| POST | `/api/jobs/<id>/cancel` | 取消（任意未结束状态；删掉已下载部分） |
| POST | `/api/jobs/cancel-all` | 一键取消所有未完成任务 |
| POST | `/api/jobs/clear-finished` | 清空已完成/失败/已取消的视频任务记录（文件保留） |
| POST | `/api/jobs/<id>/pause` | 暂停（保留 `.part`，可断点继续） |
| POST | `/api/jobs/<id>/resume` | 继续已暂停的任务 |
| POST | `/api/jobs/<id>/transform` | 加一步转换，body `{"op": "audio_m4a"}` |
| GET | `/api/jobs/<id>` | 任务详情（含 `assets` 与 `steps`） |
| POST | `/api/queue` | 暂停/继续整个队列，body `{"paused": true}` |
| GET | `/api/bootstrap` | 配置 + 统计 + 任务列表 + 平台描述 |
| POST | `/api/reveal` | 打开下载目录（限制在已知目录内） |
| POST | `/api/login/<平台>` | 发起扫码登录（`douyin` / `bilibili`） |
| GET | `/api/login/<平台>/status` | 登录状态 |
| POST | `/api/logout/<平台>` | 退出登录 |
| POST | `/api/music/search` | 搜歌，body `{"provider","keyword","page","limit"}` |
| POST | `/api/music/login/start` | 网易云扫码：拿 unikey + 二维码内容 |
| POST | `/api/music/login/poll` | 轮询扫码状态，803 时自动保存 `MUSIC_U` |
| GET | `/api/music/login/status` | 登录状态 / 昵称 / 是否会员 |
| POST | `/api/music/login/cookie` | 手填 Cookie，body `{"provider","cookie"}` |
| POST | `/api/music/login/logout` | 退出并清空该平台 Cookie |
| POST | `/api/music/collection` | 专辑/歌单/榜单，body `{"provider","kind","id"}` |
| POST | `/api/music/lyrics` | 歌词，body `{"song": {...}}` |
| POST | `/api/music/comments` | 评论，body `{"song": {...}, "page"}` |
| POST | `/api/music/download` | 批量下载，body `{"items":[...],"format","out","lyrics"}` |
| POST | `/api/music/convert` | 把已完成的一首转成指定格式（mp3/wav/flac/m4a/aac/ogg/opus/wma/aiff） |
| POST | `/api/music/convert-local` | 本地文件/文件夹转码，body `{"path","format","out"}` |
| POST | `/api/music/ncm` | 解密 NCM，body `{"path","format","out"}` |
| GET | `/api/music/queue` | 音乐队列快照（进度 / 错误 / 产物路径） |
| POST | `/api/music/queue/cancel` | 取消一首（清掉 `.part`） |
| POST | `/api/music/queue/pause` | 暂停一首（保留 `.part`，继续时 Range 续传） |
| POST | `/api/music/queue/resume` | 继续一首 |
| POST | `/api/music/queue/retry` | 重试一首 |
| POST | `/api/music/queue/clear` | 清掉已结束任务 |
| POST | `/api/music/export` | 导出 Excel，body `{"rows":[[...]]}` |
| GET | `/api/music/stream?id=` | 本地播放（支持 Range，供进度条拖动） |
| GET | `/api/music/library?q=` | 扫描音乐目录（标签 / 时长 / 是否有歌词） |
| GET | `/api/music/library/lyrics?path=` | 读同名 `.lrc` |
| POST | `/api/music/library/delete` | 删除音乐文件（连同 `.lrc`） |
| POST | `/api/music/library/m3u` | 导出 M3U 播放列表 |

## 环境要求

- Python 3.10+
- **主页批量需要本机装有 Edge 或 Chrome**（自动探测，无需配置）
- **B 站 DASH 合成不需要 ffmpeg**（纯 Python 重新装箱）；装不装都不影响

## 已知限制

- 主页只拉公开作品；私密、好友可见的作品接口本来就不返回
- **抖音主页未登录只给第一页**（实测约 43 条封顶，`count` 调大也不会更多）；
  想看全部必须扫码登录，登录后 Cookie 会写进页面桥
- 图集链路只做了单测，还没有用真实图文作品端到端验证
- 单条解析依赖 `_ROUTER_DATA` 字段结构，抖音改版就要动 `douyin.py`
- 页面桥首次调用会启动浏览器，约 4–6 秒；之后复用同一实例

### B 站

- **番剧 / 影视只下当前账号本来就能播的集**：不伪造会员、不绕地区限制；没权限的集会明确报
  `87007 / 87008 / -10403`，不会下载到奇怪的文件
- **`-403 访问权限不足` 不等于要登录**：UP 主删稿、改成私密、仅粉丝可见，B 站回的都是这个码。
  程序遇到 -403 会再查一次 `archive/stat`（稿件没了是 HTTP 404），确认删掉就直接提示
  「视频不存在了」，免得让人白登录一遍
- 档位取决于账号权限：匿名多数稿件能到 1080P（个别只给 480P，此时整段 MP4 反而有 720P，程序会自动选高的）；
  1080P60 / 4K / HDR 一般要登录
- 合成是**重新封装不转码**，所以 HDR / HEVC / AV1 的成片能不能播取决于播放器；想要最大兼容可以在设置里把画质上限调到 1080P
- UP 主列表偶尔会被风控（-352 / 412），重试或登录后更稳；请求太密会更容易触发
- 老视频若被拆成多段 `durl`，会按顺序二进制拼接（不转码），极少数可能需要在播放器里校验
- 选集默认只勾**当前这一集**；要整部就点「全选」再「下载所选」。合集只在视频本身是单 P 时才会作为选集列出
- 番剧的 `ss` / `ep` 链接都认；`media/md…` 是媒体页、不能播放，会提示改用 play 链接

## 许可证

本项目以 **GNU General Public License v3.0（GPL-3.0）** 发布，完整文本见 [LICENSE](LICENSE)。
你可以自由使用、修改、分发；分发修改版时需同样以 GPL-3.0 开源，并保留版权声明。

安装包内置的 `ffmpeg.exe` 来自 FFmpeg 项目，按其自身许可证（GPL/LGPL，取决于构建）分发；
前端二维码渲染使用 vendored `qrcode.min.js`，遵循其 MIT 许可证。

## 合规

仅用于下载你有权保存的内容（自己的作品、已获授权、公开可下载的个人存档）。
请遵守抖音 / B 站用户协议与当地版权法规，不要用于二次分发或商业牟利。















