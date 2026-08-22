"""
知乎采集器 — 通过知乎搜索 API 采集问答讨论。

数据来源: https://www.zhihu.com/api/v4/search_v3
知乎搜索 API 部分接口无需登录，用于搜索公开问答。

技术要点:
  - 请求间隔 >= 3 秒（知乎限速较严）
  - 搜索类型: content（综合内容）
  - 返回问答标题、摘要、作者、赞同/评论数
  - 完整正文需要单独请求问题/回答页面

限制:
  - 知乎新版 API 需要 x-zse-96 签名，此处使用旧版搜索接口
  - 若接口不可用，将回退到网页搜索代理方式
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
# 知乎 API 端点
# ---------------------------------------------------------------------------
ZHIHU_SEARCH_API = "https://www.zhihu.com/api/v4/search_v3"
ZHIHU_SEARCH_V2 = "https://www.zhihu.com/api/v4/search_v2"  # 备用

ZHIHU_DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.zhihu.com/search?type=content",
    "Origin": "https://www.zhihu.com",
    "x-api-version": "3.0.91",
}


class ZhihuScraper(BaseScraper):
    """知乎搜索采集器。"""

    platform = "zhihu"
    platform_name = "知乎"
    base_url = "https://www.zhihu.com"

    def search(self, keyword: str, max_pages: int = 3, product: str = "") -> list[dict]:
        """
        搜索知乎内容。

        Args:
            keyword: 搜索关键词。
            max_pages: 最大翻页数（每页约 20 条）。
            product: 关联产品 key。

        Returns:
            统一格式数据列表。
        """
        results = []
        offset = 0

        for page in range(1, max_pages + 1):
            self.logger.info("知乎搜索 '%s' 第 %d/%d 页", keyword, page, max_pages)
            try:
                items = self._fetch_page(keyword, offset)
                if not items:
                    self.logger.info("第 %d 页无结果，停止翻页", page)
                    break
                results.extend(items)
                offset += 20
            except Exception as exc:
                self.logger.error("第 %d 页抓取失败: %s", page, exc)
                # 尝试备用接口
                if page == 1:
                    try:
                        items = self._fetch_page_v2(keyword, page)
                        results.extend(items)
                        offset += 20
                        continue
                    except Exception:
                        break
                break

        return results

    # ------------------------------------------------------------------
    # 内部实现 — 主接口
    # ------------------------------------------------------------------

    def _fetch_page(self, keyword: str, offset: int) -> list[dict]:
        """抓取单页搜索结果（v3 API）。"""
        params = {
            "q": keyword,
            "type": "content",
            "offset": offset,
            "limit": 20,
            "t": "general",
        }

        resp = self._request(
            ZHIHU_SEARCH_API,
            headers=ZHIHU_DEFAULT_HEADERS,
            params=params,
        )

        data = resp.json()
        # 检查是否有错误（如需要登录）
        if "error" in data:
            self.logger.warning("知乎 API 返回错误: %s", data.get("error", {}).get("message", "unknown"))
            raise RuntimeError(f"知乎 API 错误: {data.get('error', {}).get('message', 'unknown')}")

        result_list = data.get("data", [])
        items = []

        for entry in result_list:
            obj = entry.get("object", {})
            if not obj:
                continue

            item = self._parse_search_entry(entry, obj)
            if item:
                items.append(item)

        return items

    # ------------------------------------------------------------------
    # 内部实现 — 备用接口
    # ------------------------------------------------------------------

    def _fetch_page_v2(self, keyword: str, page: int) -> list[dict]:
        """备用搜索接口（v2 API）。"""
        params = {
            "q": keyword,
            "type": "content",
            "page": page,
            "limit": 20,
        }

        resp = self._request(
            ZHIHU_SEARCH_V2,
            headers=ZHIHU_DEFAULT_HEADERS,
            params=params,
        )

        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"知乎 v2 API 错误: {data.get('error', {}).get('message', 'unknown')}")

        result_list = data.get("data", [])
        items = []

        for entry in result_list:
            obj = entry.get("object", {})
            if not obj:
                continue

            item = self._parse_search_entry(entry, obj)
            if item:
                items.append(item)

        return items

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------

    def _parse_search_entry(self, entry: dict, obj: dict) -> Optional[dict]:
        """解析搜索结果条目。"""
        obj_type = entry.get("type", "")

        if obj_type == "search_result":
            # 嵌套结构
            inner = obj
            obj_type = inner.get("type", "")
            obj = inner

        if obj_type == "answer":
            return self._parse_answer(obj)
        elif obj_type == "article":
            return self._parse_article(obj)
        elif obj_type == "question":
            return self._parse_question(obj)
        elif obj_type == "pin":
            return self._parse_pin(obj)
        else:
            # 尝试通用解析
            return self._parse_generic(obj)

    def _parse_answer(self, obj: dict) -> Optional[dict]:
        """解析回答。"""
        answer_id = str(obj.get("id", ""))
        if not answer_id:
            return None

        question = obj.get("question", {})
        question_title = question.get("title", "")
        question_id = str(question.get("id", ""))

        author_obj = obj.get("author", {})
        author = author_obj.get("name", "")

        # 回答摘要
        excerpt = obj.get("excerpt", "")
        content_preview = obj.get("content", "")

        # 组合内容
        content = f"问题: {question_title}\n\n回答: {excerpt or content_preview}"

        # 互动数据
        metrics = {
            "likes": obj.get("voteup_count", 0),
            "comments": obj.get("comment_count", 0),
            "shares": 0,
            "views": 0,
        }

        # 发布时间
        created_time = obj.get("created_time", 0)
        publish_time = self._ts_to_iso(created_time)

        # 链接
        post_url = f"https://www.zhihu.com/question/{question_id}/answer/{answer_id}"

        return self._standardize_item(
            raw_id=answer_id,
            post_url=post_url,
            author=author,
            title=question_title,
            content=content,
            publish_time=publish_time,
            metrics=metrics,
            comments=[],
            product="",
        )

    def _parse_article(self, obj: dict) -> Optional[dict]:
        """解析文章。"""
        article_id = str(obj.get("id", ""))
        if not article_id:
            return None

        author_obj = obj.get("author", {})
        author = author_obj.get("name", "")

        title = obj.get("title", "")
        excerpt = obj.get("excerpt", "")
        content = f"{title}\n\n{excerpt}"

        metrics = {
            "likes": obj.get("voteup_count", 0),
            "comments": obj.get("comment_count", 0),
            "shares": 0,
            "views": 0,
        }

        created_time = obj.get("created_time", 0)
        publish_time = self._ts_to_iso(created_time)

        post_url = f"https://zhuanlan.zhihu.com/p/{article_id}"

        return self._standardize_item(
            raw_id=article_id,
            post_url=post_url,
            author=author,
            title=title,
            content=content,
            publish_time=publish_time,
            metrics=metrics,
            comments=[],
            product="",
        )

    def _parse_question(self, obj: dict) -> Optional[dict]:
        """解析问题。"""
        question_id = str(obj.get("id", ""))
        if not question_id:
            return None

        author_obj = obj.get("author", {})
        author = author_obj.get("name", "")

        title = obj.get("title", "")
        detail = obj.get("detail", "") or obj.get("excerpt", "")
        content = f"{title}\n\n{detail}"

        metrics = {
            "likes": 0,
            "comments": obj.get("answer_count", 0),
            "shares": 0,
            "views": obj.get("follower_count", 0),
        }

        created_time = obj.get("created", 0)
        publish_time = self._ts_to_iso(created_time)

        post_url = f"https://www.zhihu.com/question/{question_id}"

        return self._standardize_item(
            raw_id=question_id,
            post_url=post_url,
            author=author,
            title=title,
            content=content,
            publish_time=publish_time,
            metrics=metrics,
            comments=[],
            product="",
        )

    def _parse_pin(self, obj: dict) -> Optional[dict]:
        """解析想法。"""
        pin_id = str(obj.get("id", ""))
        if not pin_id:
            return None

        author_obj = obj.get("author", {})
        author = author_obj.get("name", "")

        content = obj.get("content", "") or obj.get("excerpt", "")
        title = content[:100] if content else ""

        metrics = {
            "likes": obj.get("like_count", 0),
            "comments": obj.get("comment_count", 0),
            "shares": obj.get("repin_count", 0),
            "views": 0,
        }

        created_time = obj.get("created", 0)
        publish_time = self._ts_to_iso(created_time)

        post_url = f"https://www.zhihu.com/pin/{pin_id}"

        return self._standardize_item(
            raw_id=pin_id,
            post_url=post_url,
            author=author,
            title=title,
            content=content,
            publish_time=publish_time,
            metrics=metrics,
            comments=[],
            product="",
        )

    def _parse_generic(self, obj: dict) -> Optional[dict]:
        """通用解析（兜底）。"""
        raw_id = str(obj.get("id", obj.get("url_token", "")))
        if not raw_id:
            return None

        author = obj.get("author", {}).get("name", "") if isinstance(obj.get("author"), dict) else ""
        title = obj.get("title", "") or obj.get("excerpt", "")[:200]
        content = obj.get("content", "") or obj.get("excerpt", "") or title

        metrics = {
            "likes": obj.get("voteup_count", obj.get("like_count", 0)),
            "comments": obj.get("comment_count", 0),
            "shares": 0,
            "views": 0,
        }

        created = obj.get("created_time", obj.get("created", obj.get("updated_time", 0)))
        publish_time = self._ts_to_iso(created)

        post_url = obj.get("url", f"https://www.zhihu.com/question/{raw_id}")

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

    @staticmethod
    def _ts_to_iso(ts) -> str:
        """Unix 时间戳转 ISO 8601。"""
        if not ts:
            return ""
        try:
            from datetime import datetime, timezone, timedelta
            TZ = timezone(timedelta(hours=8))
            return datetime.fromtimestamp(int(ts), tz=TZ).isoformat()
        except (ValueError, TypeError, OSError):
            return str(ts)