"""音乐下载与转换：平台搜索 / 专辑歌单 / NCM 解密 / 批量下载 / 转 MP3·WAV。

对外只暴露四块：

* `providers` —— 网易云、QQ、酷狗、酷我的搜索与直链解析；
* `ncm`       —— 纯 Python NCM 解密（等价 ncmc）；
* `service`   —— 下载队列（校验、转码、歌词、断点续跑）；
* `xlsx`      —— 导出 Excel（不依赖 openpyxl）。

老的「音乐体检 / 按专辑转换」还在 `huzhmi.media`，两边共用 ffmpeg 和体检逻辑。
"""

from . import library, login, ncm, providers, service, xlsx  # noqa: F401

__all__ = ["library", "login", "ncm", "providers", "service", "xlsx"]
