"""
微博采集器 — 通过 m.weibo.cn 移动端 API 搜索公开微博。

数据来源: https://m.weibo.cn/api/container/getIndex
API 无需登录即可搜索公开微博，返回 JSON 格式。

技术要点:
  - 请求间隔 >= 2 秒（微博 API 限速较严）
  - 每页最多返回 10 条微博卡片
  - 解析 card_type=9 的微博卡片获取正文和互动数据
  - 支持提取热门评论
"""

import json
import os
import sys
from typing import Optional

# 兼容包内运行和直接运行
try:
    from .base import BaseScraper
except ImportError:
    _current_dir = os.path.dirname(os.path.abspath(__file__))
    if _current_dir not in sys.path:
        sys.path.insert(0, _current_dir)
    from base import BaseScraper


# ---------------------------------------------------------------------------
# Weibo 搜索 API 端点
# ---------------------------------------------------------------------------
WEIBO_SEARCH_API = "https://m.weibo.cn/api/container/getIndex"

WEIBO_DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://m.weibo.cn/",
    "X-Requested-With": "XMLHttpRequest",
}


class WeiboScraper(BaseScraper):
    """微博移动端搜索采集器。"""

    platform = "weibo"
    platform_name = "微博"
    base_url = "https://m.weibo.cn"

    def search(self, keyword: str, max_pages: int = 3, product: str = "") -> list[dict]:
        """
        搜索微博。

        Args:
            keyword: 搜索关键词。
            max_pages: 最大翻页数（每页约 10 条）。
            product: 关联产品 key。

        Returns:
            统一格式数据列表。
        """
        results = []
        page = 1

        while page <= max_pages:
            self.logger.info("微博搜索 '%s' 第 %d/%d 页", keyword, page, max_pages)
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
        container_id = f"100103type=1&q={keyword}"
        params = {
            "containerid": container_id,
            "page": page,
        }

        resp = self._request(
            WEIBO_SEARCH_API,
            headers=WEIBO_DEFAULT_HEADERS,
            params=params,
        )

        data = resp.json()
        if data.get("ok") != 1:
            self.logger.warning("微博 API 返回异常: %s", data.get("msg", "unknown"))
            return []

        cards = data.get("data", {}).get("cards", [])
        items = []

        for card in cards:
            if card.get("card_type") != 9:  # 微博卡片
                continue

            mblog = card.get("mblog")
            if not mblog:
                continue

            item = self._parse_mblog(mblog, keyword)
            if item:
                items.append(item)

        return items

    def _parse_mblog(self, mblog: dict, keyword: str = "") -> Optional[dict]:
        """解析单条微博数据。"""
        raw_id = str(mblog.get("id", ""))
        if not raw_id:
            return None

        user = mblog.get("user", {})
        author = user.get("screen_name", "")

        # 微博正文（去除 HTML 标签）
        text = mblog.get("text", "")
        text = self._strip_html(text)

        # 发布时间
        created_at = mblog.get("created_at", "")

        # 互动数据
        metrics = {
            "likes": mblog.get("attitudes_count", 0),
            "comments": mblog.get("comments_count", 0),
            "shares": mblog.get("reposts_count", 0),
            "views": 0,
        }

        # 帖子链接
        post_url = f"https://m.weibo.cn/detail/{raw_id}"

        # 标题：取正文前 100 字
        title = text[:100] if text else f"@{author} 的微博"

        # 提取热门评论
        comments = self._parse_comments(mblog)

        return self._standardize_item(
            raw_id=raw_id,
            post_url=post_url,
            author=author,
            title=title,
            content=text,
            publish_time=created_at,
            metrics=metrics,
            comments=comments,
            product="",  # 由调用方设置
        )

    def _parse_comments(self, mblog: dict) -> list[dict]:
        """提取热门评论。"""
        comments = []
        raw_comments = mblog.get("comments", [])
        for c in raw_comments[:20]:
            comments.append({
                "author": c.get("user", {}).get("screen_name", ""),
                "content": self._strip_html(c.get("text", "")),
                "time": c.get("created_at", ""),
                "likes": c.get("like_count", 0),
            })
        return comments

    @staticmethod
    def _strip_html(text: str) -> str:
        """去除微博文本中的 HTML 标签和转义。"""
        import re
        # 去除 HTML 标签
        text = re.sub(r"<[^>]+>", "", text)
        # 常见 HTML 实体
        text = text.replace("&nbsp;", " ").replace("&amp;", "&")
        text = text.replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&quot;", '"').replace("&#39;", "'")
        return text.strip()

    # ------------------------------------------------------------------
    # 扩展：获取单条微博的完整评论
    # ------------------------------------------------------------------

    def fetch_comments(self, weibo_id: str, max_pages: int = 3) -> list[dict]:
        """获取单条微博的评论列表。

        Args:
            weibo_id: 微博 ID。
            max_pages: 最大翻页数。

        Returns:
            评论列表。
        """
        comments = []
        page = 1

        while page <= max_pages:
            params = {
                "id": weibo_id,
                "page": page,
            }
            url = "https://m.weibo.cn/api/comments/show"
            try:
                resp = self._request(url, headers=WEIBO_DEFAULT_HEADERS, params=params)
                data = resp.json()
                if data.get("ok") != 1:
                    break

                comment_list = data.get("data", {}).get("data", [])
                if not comment_list:
                    break

                for c in comment_list:
                    comments.append({
                        "author": c.get("user", {}).get("screen_name", ""),
                        "content": self._strip_html(c.get("text", "")),
                        "time": c.get("created_at", ""),
                        "likes": c.get("like_count", 0),
                    })

                page += 1
            except Exception as exc:
                self.logger.error("获取评论失败: %s", exc)
                break

        return comments