# Huzhmi

版本 **v1.0** · 许可证 **GPL-3.0**

## 端口

| 端口 | 用途 |
|---|---|
| `8765` | Web 界面、REST API、媒体流 |
| `8766`–`8776` | `8765` 被占用时自动顺延 |

## 功能

### 视频下载

- 抖音：视频、图集、主页批量、日期筛选、去水印、封面、原声
- TikTok：公开视频、图集、短链
- B 站：BV / av、分 P、番剧、UP 主批量、DASH、字幕
- 最高画质自动选择，MP4 封装不转码
- 自定义目录、并发下载、断点续传、失败重试、SQLite 去重

### 抖音发现

- 热榜
- 关键词搜索
- 搜索结果多选下载

### 音乐

- 搜索：网易云、QQ 音乐、酷狗、酷我
- 专辑、歌单、榜单、歌词、封面
- 网易云扫码登录，其他平台 Cookie 登录
- 批量下载；MP3、WAV、FLAC、原格式
- NCM 解密
- 本地音乐库：播放、歌词、封面、删除、M3U、Excel
- 音乐体检：识别坏文件、按专辑批量转换

### 音频转换

- NCM → MP3、WAV、FLAC、M4A、AAC、OGG、Opus、WMA、AIFF
- 音频互转
- 高质量、标准、省空间三档

### 作品档案

- 扫描下载目录
- 视频播放、图片预览
- 标题 / 作者筛选
- 打开文件、删除文件

### 队列

- 暂停、继续、取消、重试、清空
- 单实例运行
- 应用内重启
- 退出时清理已完成任务记录

### 解析规则

- `rules.json` 热更新
- 分享页解析失败自动切换浏览器兜底

## 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/jobs` | 提交视频链接 |
| GET | `/api/jobs` | 任务列表 |
| POST | `/api/jobs/<id>/pause` | 暂停 |
| POST | `/api/jobs/<id>/resume` | 继续 |
| POST | `/api/jobs/<id>/cancel` | 取消 |
| POST | `/api/profile` | 主页作品 |
| POST | `/api/profile/more` | 主页翻页 |
| POST | `/api/select` | 多选下载 |
| GET | `/api/discover/hot` | 抖音热榜 |
| POST | `/api/discover/search` | 抖音搜索 |
| GET | `/api/archive` | 作品档案 |
| GET | `/api/archive/file` | 档案媒体流 |
| POST | `/api/archive/delete` | 删除档案文件 |
| POST | `/api/music/search` | 音乐搜索 |
| POST | `/api/music/collection` | 专辑 / 歌单 / 榜单 |
| POST | `/api/music/download` | 音乐下载 |
| POST | `/api/music/ncm` | NCM 解密 |
| POST | `/api/music/convert` | 单曲转码 |
| POST | `/api/music/convert-local` | 本地音频转码 |
| GET | `/api/music/library` | 本地音乐库 |
| POST | `/api/music/library/m3u` | 导出 M3U |
| POST | `/api/music/library/delete` | 删除音乐 |
| GET | `/api/music/queue` | 音乐队列 |
| POST | `/api/music/queue/pause` | 暂停音乐任务 |
| POST | `/api/music/queue/resume` | 继续音乐任务 |
| POST | `/api/music/queue/cancel` | 取消音乐任务 |
| GET | `/api/rules` | 解析规则 |
| POST | `/api/rules/fetch` | 规则热更新 |
| POST | `/api/restart` | 重启应用 |

## 许可证

GPL-3.0，完整文本见 [LICENSE](LICENSE)。
