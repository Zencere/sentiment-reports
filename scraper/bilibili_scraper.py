"""
B站采集器 — 通过 Bilibili 官方搜索 API 搜索视频。

数据来源: https://api.bilibili.com/x/web-interface/search/type
B站搜索 API 无需登录即可使用，返回结构化 JSON。

技术要点:
  - 请求间隔 >= 1.5 秒
  - 每页返回 20 条视频
  - 搜索类型: 视频 (search_type=video)
  - 返回视频标题、简介、UP主、播放量、弹幕数、评论数等
"""

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
# Bilibili API 端点
# ---------------------------------------------------------------------------
BILIBILI_SEARCH_API = "https://api.bilibili.com/x/web-interface/search/type"

BILIBILI_DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://search.bilibili.com/",
    "Origin": "https://search.bilibili.com",
}


class BilibiliScraper(BaseScraper):
    """B站视频搜索采集器。"""

    platform = "bilibili"
    platform_name = "B站"
    base_url = "https://www.bilibili.com"

    def search(self, keyword: str, max_pages: int = 3, product: str = "") -> list[dict]:
        """
        搜索B站视频。

        Args:
            keyword: 搜索关键词。
            max_pages: 最大翻页数（每页 20 条）。
            product: 关联产品 key。

        Returns:
            统一格式数据列表。
        """
        results = []
        page = 1

        while page <= max_pages:
            self.logger.info("B站搜索 '%s' 第 %d/%d 页", keyword, page, max_pages)
            try:
                items = self._fetch_page(keyword, page, product)
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

    def _fetch_page(self, keyword: str, page: int, product: str = "") -> list[dict]:
        """抓取单页搜索结果。"""
        params = {
            "search_type": "video",
            "keyword": keyword,
            "page": page,
            "order": "totalrank",  # 综合排序
        }

        resp = self._request(
            BILIBILI_SEARCH_API,
            headers=BILIBILI_DEFAULT_HEADERS,
            params=params,
        )

        data = resp.json()
        if data.get("code") != 0:
            self.logger.warning("B站 API 返回异常: code=%s, msg=%s",
                                data.get("code"), data.get("message"))
            return []

        result_list = data.get("data", {}).get("result", [])
        items = []

        for video in result_list:
            # 过滤掉广告和无关内容
            if video.get("type") == "activity":
                continue

            item = self._parse_video(video, product)
            if item:
                items.append(item)

        return items

    def _parse_video(self, video: dict, product: str = "") -> Optional[dict]:
        """解析单条视频数据。"""
        aid = str(video.get("aid", ""))
        bvid = video.get("bvid", "")
        raw_id = bvid or aid
        if not raw_id:
            return None

        author = video.get("author", "")
        title = video.get("title", "").replace('<em class="keyword">', "").replace("</em>", "")
        description = video.get("description", "")

        # 发布时间：B站返回 Unix 时间戳
        pubdate = video.get("pubdate", 0)
        publish_time = ""
        if pubdate:
            from datetime import datetime, timezone, timedelta
            TZ = timezone(timedelta(hours=8))
            publish_time = datetime.fromtimestamp(pubdate, tz=TZ).isoformat()

        # 互动数据
        metrics = {
            "likes": video.get("like", 0),
            "comments": video.get("review", 0),
            "shares": 0,
            "views": video.get("play", 0),
        }

        # 视频链接
        post_url = f"https://www.bilibili.com/video/{bvid}" if bvid else f"https://www.bilibili.com/video/av{aid}"

        # 内容：标题 + 简介
        content = f"{title}\n\n{description}".strip()

        return self._standardize_item(
            raw_id=raw_id,
            post_url=post_url,
            author=author,
            title=title,
            content=content,
            publish_time=publish_time,
            metrics=metrics,
            comments=[],  # B站评论需要单独 API 获取
            product=product,
        )

    # ------------------------------------------------------------------
    # 扩展：获取视频热门评论
    # ------------------------------------------------------------------

    def fetch_comments(self, aid: str, max_pages: int = 2) -> list[dict]:
        """获取视频的热门评论。

        Args:
            aid: 视频 AV 号。
            max_pages: 最大翻页数。

        Returns:
            评论列表。
        """
        comments = []
        page = 1

        while page <= max_pages:
            params = {
                "oid": aid,
                "type": 1,  # 视频
                "mode": 3,  # 热度排序
                "ps": 20,
                "pn": page,
            }
            url = "https://api.bilibili.com/x/v2/reply/main"
            try:
                resp = self._request(url, headers=BILIBILI_DEFAULT_HEADERS, params=params)
                data = resp.json()
                if data.get("code") != 0:
                    break

                reply_list = data.get("data", {}).get("replies", [])
                if not reply_list:
                    break

                for r in reply_list:
                    comments.append({
                        "author": r.get("member", {}).get("uname", ""),
                        "content": r.get("content", {}).get("message", ""),
                        "time": str(r.get("ctime", "")),
                        "likes": r.get("like", 0),
                    })

                page += 1
            except Exception as exc:
                self.logger.error("获取评论失败: %s", exc)
                break

        return comments