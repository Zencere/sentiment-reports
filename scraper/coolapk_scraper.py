"""
酷安采集器 — 通过酷安搜索 API 采集数码圈讨论。

数据来源: https://api.coolapk.com/v6/search
酷安是中文数码爱好者社区，讨论质量较高，适合产品舆情监控。

技术要点:
  - V3 X-App-Token 动态签名认证（基于 libauth.so）
  - 请求间隔 >= 2 秒
  - 搜索类型: feed（动态）
  - 返回帖子标题、内容、作者、点赞/评论/转发数
"""

import os
import re
import sys
import time
import html
from typing import Optional

# 兼容包内运行和直接运行
try:
    from .base import BaseScraper
    from .coolapk_token import build_headers, DEFAULT_DEVICE
except ImportError:
    _current_dir = os.path.dirname(os.path.abspath(__file__))
    if _current_dir not in sys.path:
        sys.path.insert(0, _current_dir)
    from base import BaseScraper
    from coolapk_token import build_headers, DEFAULT_DEVICE


# ---------------------------------------------------------------------------
# 酷安 API 端点
# ---------------------------------------------------------------------------
COOLAPK_SEARCH_API = "https://api.coolapk.com/v6/search"


class CoolapkScraper(BaseScraper):
    """酷安搜索采集器（V3 认证）。"""

    platform = "coolapk"
    platform_name = "酷安"
    base_url = "https://www.coolapk.com"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 使用固定设备指纹，保持 token 一致性、避免触发风控
        self._device = DEFAULT_DEVICE
        self._cached_headers = None
        self._cached_ts = 0

    def _get_headers(self) -> dict:
        """获取带 V3 Token 的请求头（60 秒内缓存复用）。"""
        now = int(time.time())
        if self._cached_headers and (now - self._cached_ts) < 60:
            return self._cached_headers
        self._cached_headers = build_headers(device=self._device, ts=now)
        self._cached_ts = now
        return self._cached_headers

    def search(self, keyword: str, max_pages: int = 3, product: str = "") -> list[dict]:
        """
        搜索酷安动态。

        Args:
            keyword: 搜索关键词。
            max_pages: 最大翻页数（每页约 15 条）。
            product: 关联产品 key。

        Returns:
            统一格式数据列表。
        """
        results = []
        page = 1

        while page <= max_pages:
            self.logger.info("酷安搜索 '%s' 第 %d/%d 页", keyword, page, max_pages)
            try:
                items = self._fetch_page(keyword, page)
                if not items:
                    self.logger.info("第 %d 页无结果，停止翻页", page)
                    break
                results.extend(items)
            except Exception as exc:
                self.logger.error("第 %d 页抓取失败: %s", page, exc)
                break

            page += 1

        return results

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _fetch_page(self, keyword: str, page: int) -> list[dict]:
        """抓取单页搜索结果。"""
        params = {
            "q": keyword,
            "type": "feed",
            "page": page,
            "sort": "default",  # 默认排序
        }

        resp = self._request(
            COOLAPK_SEARCH_API,
            headers=self._get_headers(),
            params=params,
        )

        data = resp.json()
        # 酷安 v6 搜索返回 {"data": [feed, ...]}，无顶层 status 字段
        feed_list = data.get("data") if isinstance(data, dict) else None
        if not isinstance(feed_list, list):
            self.logger.warning("酷安 API 返回异常: %s", str(data)[:200])
            return []

        items = []

        for feed in feed_list:
            item = self._parse_feed(feed)
            if item:
                items.append(item)

        return items

    def _parse_feed(self, feed: dict) -> Optional[dict]:
        """解析单条动态。"""
        raw_id = str(feed.get("id") or feed.get("entityId") or "")
        if not raw_id:
            return None

        # 作者信息：顶层 username，兼容嵌套 userInfo
        author = feed.get("username", "") or ""
        if not author:
            user_info = feed.get("userInfo", {}) or {}
            author = user_info.get("username", "")

        # 酷安动态无标题字段，取正文前 100 字作为摘要
        message = feed.get("message", "") or ""
        if message:
            # 去掉 HTML 标签后反转义实体（&amp; &lt; &#39; 等）
            message = html.unescape(re.sub(r"<[^>]+>", "", message).strip())

        title = message[:100] if message else html.unescape(feed.get("title", "") or "")

        # 内容优先取 message
        content = message
        if not content:
            content = html.unescape(re.sub(r"<[^>]+>", "", feed.get("description", "") or feed.get("info", "") or "").strip())

        # 发布时间：酷安返回 Unix 时间戳（秒，字符串）
        last_update = feed.get("lastupdate", 0) or feed.get("dateline", 0) or 0
        publish_time = ""
        if last_update:
            try:
                from datetime import datetime, timezone, timedelta
                TZ = timezone(timedelta(hours=8))
                publish_time = datetime.fromtimestamp(int(last_update), tz=TZ).isoformat()
            except (ValueError, OSError, OverflowError):
                publish_time = ""

        # 互动数据
        metrics = {
            "likes": int(feed.get("likenum", 0) or 0),
            "comments": int(feed.get("replynum", 0) or 0),
            "shares": int(feed.get("forwardnum", 0) or feed.get("share_num", 0) or 0),
            "views": int(feed.get("viewnum", 0) or 0),
        }

        # 链接（url 可能是相对路径 /feed/xxx）
        url = feed.get("url", "") or feed.get("shareUrl", "") or ""
        if url.startswith("http"):
            post_url = url
        elif url:
            post_url = "https://www.coolapk.com" + url
        else:
            post_url = f"https://www.coolapk.com/feed/{raw_id}"

        return self._standardize_item(
            raw_id=raw_id,
            post_url=post_url,
            author=author,
            title=title,
            content=content,
            publish_time=publish_time,
            metrics=metrics,
            comments=[],
            product="",
        )

    # ------------------------------------------------------------------
    # 扩展：获取动态热门回复
    # ------------------------------------------------------------------

    def fetch_comments(self, feed_id: str, max_pages: int = 2) -> list[dict]:
        """获取动态的回复列表。

        Args:
            feed_id: 动态 ID。
            max_pages: 最大翻页数。

        Returns:
            回复列表。
        """
        comments = []
        page = 1

        while page <= max_pages:
            params = {
                "id": feed_id,
                "page": page,
                "listType": "hot",  # 热门回复
            }
            url = "https://api.coolapk.com/v6/feed/replyList"
            try:
                resp = self._request(url, headers=self._get_headers(), params=params)
                data = resp.json()
                if data.get("status") != 0:
                    break

                reply_list = data.get("data", [])
                if not reply_list:
                    break

                for r in reply_list:
                    user_info = r.get("userAction", {}).get("userInfo", {})
                    comments.append({
                        "author": user_info.get("username", ""),
                        "content": r.get("message", ""),
                        "time": str(r.get("lastupdate", "")),
                        "likes": int(r.get("likenum", 0)),
                    })

                page += 1
            except Exception as exc:
                self.logger.error("获取回复失败: %s", exc)
                break

        return comments