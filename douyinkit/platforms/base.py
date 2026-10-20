"""平台适配器的接口定义。

一个平台要能自己回答这几件事，上层（engine / server / 前端）就不用再写
`if 平台 == "bilibili"`：

  - 这个链接是不是我的（`matches`）
  - 怎么去重（`normalize`）
  - 怎么解析一条作品（`extract`）
  - 主页怎么列/怎么翻页（`profile` / `profile_more`）
  - 下载时要带什么请求头（`media_headers`）
  - 登录怎么发起、状态怎么查（`login_state` / `start_login` / `logout`）

新增平台只要实现这些方法，然后注册进 `platforms.registry`。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Platform(ABC):
    name: str = ""
    label: str = ""
    # 主页一次拉多少条（未登录时各平台上限不一样）
    page_size: int = 50
    supports_login: bool = False
    # 给前端做链接识别用，必须是 JS 也能用的正则
    url_pattern: str = ""
    # 作者主页地址模板，`{id}` 会被换成作者 ID
    profile_url_template: str = ""

    # ---------- 链接 ----------

    @abstractmethod
    def matches(self, url: str) -> bool:
        """这个平台的链接吗？"""

    @abstractmethod
    def normalize(self, url: str) -> tuple[str, str]:
        """返回 (干净链接, 去重键)。"""

    @abstractmethod
    def is_profile_url(self, url: str) -> bool:
        """是主页/作者页链接吗？"""

    @abstractmethod
    def resolve_short(self, url: str) -> str:
        """展开短链（不是短链就原样返回）。"""

    # ---------- 内容 ----------

    @abstractmethod
    def extract(self, url: str, cfg: dict) -> dict:
        """解析一条作品，返回带 assets 的 meta。失败抛 `PlatformError`。"""

    def extract_fallback(self, url: str, cfg: dict) -> dict:
        """高质量链路失败后的降级解析（例如 B 站的 DASH 拉不动）。

        默认就是再解析一次；需要降级的平台自己覆盖。
        """
        return self.extract(url, cfg)

    def episodes(self, url: str, cfg: dict) -> dict:
        """这条链接的「选集」列表；默认表示没有选集（按单条处理）。

        有分 P / 合集 / 剧集的平台覆盖它，前端据此弹出勾选框。
        """
        return {"platform": self.name, "has_multi": False, "episodes": []}

    @abstractmethod
    def profile(self, url: str, count: int, cfg: dict) -> dict:
        """主页第一页：{profile, items, has_more, max_cursor, ...}。"""

    @abstractmethod
    def profile_more(self, token: str, cursor: int, count: int, cfg: dict) -> dict:
        """主页翻页：token 是该平台自己的游标（B 站是 mid，抖音是 sec_uid）。"""

    @abstractmethod
    def item_source_url(self, video_id: str) -> str:
        """主页多选时，用作品 ID 拼回作品链接。"""

    # ---------- 下载 ----------

    @abstractmethod
    def media_headers(self, cfg: dict) -> dict:
        """下载媒体时的请求头（Referer / Cookie 各平台不同）。"""

    # ---------- 登录 ----------

    def login_state(self, cfg: dict) -> dict:
        return {"status": "unsupported", "message": "该平台不支持登录", "has_cookie": False}

    def start_login(self) -> dict:
        raise NotImplementedError

    def logout(self) -> dict:
        raise NotImplementedError

    # ---------- 给前端 ----------

    def describe(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "page_size": self.page_size,
            "supports_login": self.supports_login,
            "pattern": self.url_pattern,
            "profile_url": self.profile_url_template,
        }
