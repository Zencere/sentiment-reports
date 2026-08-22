"""
采集器基类 — 提供统一的接口、限速、重试、UA 轮换、日志和标准化数据格式。

所有平台采集器继承此类，只需实现 `_search()` 和 `_parse_item()` 两个方法。
"""

import time
import random
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional
from abc import ABC, abstractmethod

import requests

try:
    from fake_useragent import UserAgent
    _ua = UserAgent()
except Exception:
    _ua = None

# ---------------------------------------------------------------------------
# 北京时区 (UTC+8)
# ---------------------------------------------------------------------------
TZ_BEIJING = timezone(timedelta(hours=8))

# ---------------------------------------------------------------------------
# 默认 User-Agent 池
# ---------------------------------------------------------------------------
FALLBACK_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
]

# ---------------------------------------------------------------------------
# 统一数据模型
# ---------------------------------------------------------------------------
# 所有采集器返回的每条数据都遵循此结构：
# {
#     "id":        str,    # 唯一标识，平台前缀 + hash
#     "platform":  str,    # weibo | zhihu | bilibili | smzdm | zol | coolapk
#     "post_url":  str,    # 帖子直链
#     "author":    str,    # 作者名
#     "title":     str,    # 标题/摘要（截断至 200 字）
#     "content":   str,    # 正文内容（截断至 2000 字）
#     "publish_time": str, # ISO 8601 格式，北京时间
#     "metrics": {
#         "likes":     int,
#         "comments":  int,
#         "shares":    int,
#         "views":     int,
#     },
#     "comments": [        # 热门评论（如有），最多 20 条
#         {
#             "author":  str,
#             "content": str,
#             "time":    str,
#             "likes":   int,
#         }
#     ],
#     "scraped_at": str,   # ISO 8601 抓取时间
#     "product":    str,   # 关联产品 key
# }

# ---------------------------------------------------------------------------
# 基类
# ---------------------------------------------------------------------------

class BaseScraper(ABC):
    """所有平台采集器的基类。"""

    # 子类必须覆盖的属性
    platform: str = ""           # 平台标识: weibo, zhihu, bilibili, smzdm, zol, coolapk
    platform_name: str = ""      # 平台中文名
    base_url: str = ""           # 平台根 URL

    def __init__(
        self,
        min_interval: float = 2.0,
        max_retries: int = 3,
        retry_backoff: float = 2.0,
        timeout: int = 20,
    ):
        """
        Args:
            min_interval: 两次请求最小间隔（秒）。
            max_retries: 最大重试次数。
            retry_backoff: 重试退避因子。
            timeout: 请求超时（秒）。
        """
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.timeout = timeout

        self._last_request = 0.0
        self.session = requests.Session()
        self.logger = logging.getLogger(f"scraper.{self.platform}")

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def _random_ua(self) -> str:
        """获取随机 User-Agent。"""
        if _ua is not None:
            try:
                return _ua.random
            except Exception:
                pass
        return random.choice(FALLBACK_USER_AGENTS)

    def _wait(self) -> None:
        """限速等待。"""
        elapsed = time.time() - self._last_request
        if elapsed < self.min_interval:
            sleep_time = self.min_interval - elapsed + random.uniform(0, 0.5)
            self.logger.debug("限速等待 %.1f 秒...", sleep_time)
            time.sleep(sleep_time)
        self._last_request = time.time()

    def _request(
        self,
        url: str,
        *,
        headers: dict = None,
        params: dict = None,
        method: str = "GET",
        **kwargs,
    ) -> requests.Response:
        """带重试机制的 HTTP 请求。

        Raises:
            RuntimeError: 重试耗尽后仍失败。
        """
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                self._wait()
                req_headers = {"User-Agent": self._random_ua()}
                if headers:
                    req_headers.update(headers)

                resp = self.session.request(
                    method=method,
                    url=url,
                    headers=req_headers,
                    params=params,
                    timeout=self.timeout,
                    **kwargs,
                )
                resp.raise_for_status()
                return resp

            except requests.exceptions.Timeout as exc:
                last_exc = exc
                self.logger.warning("请求超时 (第 %d/%d 次): %s", attempt, self.max_retries, url)
            except requests.exceptions.HTTPError as exc:
                last_exc = exc
                status = exc.response.status_code if exc.response is not None else None
                self.logger.warning("HTTP %s (第 %d/%d 次): %s", status, attempt, self.max_retries, url)
                if status == 429:
                    time.sleep(10 * attempt)
            except requests.exceptions.ConnectionError as exc:
                last_exc = exc
                self.logger.warning("连接错误 (第 %d/%d 次): %s", attempt, self.max_retries, url)
            except Exception as exc:
                last_exc = exc
                self.logger.warning("请求异常 (第 %d/%d 次): %s", attempt, self.max_retries, exc)

            if attempt < self.max_retries:
                backoff = self.retry_backoff ** attempt + random.random()
                time.sleep(backoff)

        raise RuntimeError(f"请求失败（已重试 {self.max_retries} 次）: {url}") from last_exc

    # ------------------------------------------------------------------
    # 统一 ID 生成
    # ------------------------------------------------------------------

    @staticmethod
    def make_id(platform: str, raw_id: str) -> str:
        """生成统一 ID：平台前缀 + 原始 ID 的 SHA256 前 12 位。"""
        h = hashlib.sha256(f"{platform}:{raw_id}".encode()).hexdigest()[:12]
        return f"{platform}_{h}"

    # ------------------------------------------------------------------
    # 标准化输出
    # ------------------------------------------------------------------

    def _standardize_item(
        self,
        *,
        raw_id: str,
        post_url: str,
        author: str,
        title: str,
        content: str,
        publish_time: str,
        metrics: dict = None,
        comments: list = None,
        product: str = "",
    ) -> dict:
        """将平台原始数据转换为统一格式。

        Args:
            raw_id: 平台原始 ID。
            post_url: 帖子直链（用户可点击访问）。
            author: 作者名。
            title: 标题/摘要。
            content: 正文内容。
            publish_time: 发布时间字符串（ISO 8601 或常见格式）。
            metrics: {'likes': int, 'comments': int, 'shares': int, 'views': int}。
            comments: 评论列表。
            product: 关联产品 key。

        Returns:
            统一格式 dict。
        """
        default_metrics = {"likes": 0, "comments": 0, "shares": 0, "views": 0}
        if metrics:
            default_metrics.update(metrics)

        return {
            "id": self.make_id(self.platform, raw_id),
            "platform": self.platform,
            "post_url": post_url,
            "author": author or "",
            "title": (title or "")[:200],
            "content": (content or "")[:2000],
            "publish_time": self._normalize_time(publish_time),
            "metrics": default_metrics,
            "comments": comments or [],
            "scraped_at": datetime.now(TZ_BEIJING).isoformat(),
            "product": product,
        }

    @staticmethod
    def _normalize_time(raw: str) -> str:
        """尝试将各种时间格式统一为 ISO 8601（北京时间）。"""
        if not raw:
            return ""

        # 已经是 ISO 8601
        if "T" in raw:
            return raw

        # 常见格式: 2026-08-21 14:30:00 / 2026-08-21 / 08-21 14:30
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%m-%d %H:%M",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(raw.strip(), fmt)
                if dt.year == 1900:
                    dt = dt.replace(year=datetime.now(TZ_BEIJING).year)
                return dt.replace(tzinfo=TZ_BEIJING).isoformat()
            except ValueError:
                continue

        return raw

    # ------------------------------------------------------------------
    # 子类必须实现的接口
    # ------------------------------------------------------------------

    @abstractmethod
    def search(self, keyword: str, max_pages: int = 3, product: str = "") -> list[dict]:
        """搜索关键词并返回统一格式数据列表。

        Args:
            keyword: 搜索关键词。
            max_pages: 最大翻页数。
            product: 关联产品 key。

        Returns:
            list[dict]: 统一格式数据列表。
        """
        ...

    def search_multi_keywords(self, keywords: list[str], max_pages: int = 3, product: str = "") -> list[dict]:
        """对多个关键词搜索并去重合并。

        Args:
            keywords: 关键词列表。
            max_pages: 每个关键词的最大翻页数。
            product: 关联产品 key。

        Returns:
            list[dict]: 去重后的统一格式数据列表。
        """
        seen_ids = set()
        results = []

        for kw in keywords:
            self.logger.info("[%s] 搜索关键词: %s", product or "全局", kw)
            try:
                items = self.search(keyword=kw, max_pages=max_pages, product=product)
                for item in items:
                    if item["id"] not in seen_ids:
                        seen_ids.add(item["id"])
                        results.append(item)
                self.logger.info("[%s] 关键词 '%s' 获取 %d 条（去重后累计 %d 条）",
                                 product or "全局", kw, len(items), len(results))
            except Exception as exc:
                self.logger.error("[%s] 关键词 '%s' 搜索失败: %s", product or "全局", kw, exc)

        return results