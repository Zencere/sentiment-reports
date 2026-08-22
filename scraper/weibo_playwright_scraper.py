"""
微博 Playwright 采集器 — 通过浏览器 Cookie 访问 m.weibo.cn API。

技术要点:
  - m.weibo.cn API 纯 requests 返回 ok=-100（需要浏览器 Cookie）
  - Playwright 访问首页自动获取 SUB/SUBP/XSRF-TOKEN 等认证 Cookie
  - 然后在页面上下文内 fetch API，可成功返回 ok=1
  - 每页约 8-10 条微博卡片
"""

import os
import sys
import json
import re
import logging
from typing import Optional

_current_dir = os.path.dirname(os.path.abspath(__file__))
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

from playwright_base import PlaywrightScraper

logger = logging.getLogger("weibo_pw")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
WEIBO_HOME = "https://m.weibo.cn/"
WEIBO_API = "https://m.weibo.cn/api/container/getIndex"


class WeiboPlaywrightScraper(PlaywrightScraper):
    """微博 — Playwright 版采集器。"""

    platform = "weibo"
    platform_name = "微博"
    base_url = WEIBO_HOME
    min_interval = 3.0

    def search(self, keyword: str, max_pages: int = 3, product: str = "") -> list[dict]:
        """
        搜索微博。

        Args:
            keyword: 搜索关键词。
            max_pages: 最大翻页数（每页约 8-10 条）。
            product: 关联产品 key。

        Returns:
            统一格式数据列表。
        """
        results = []
        page_num = 1

        # 先访问首页获取 Cookie
        if not self._init_cookies():
            logger.error("微博初始化 Cookie 失败")
            return []

        while page_num <= max_pages:
            logger.info("微博搜索 '%s' 第 %d/%d 页", keyword, page_num, max_pages)
            try:
                items = self._fetch_page(keyword, page_num, product)
                if not items:
                    logger.info("第 %d 页无结果，停止翻页", page_num)
                    break
                results.extend(items)
            except Exception as exc:
                logger.error("第 %d 页抓取失败: %s", page_num, exc)
                break

            page_num += 1

        logger.info("微博搜索完成，共 %d 条", len(results))
        return results

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _init_cookies(self) -> bool:
        """访问首页获取 Cookie。"""
        self._ensure_page()
        try:
            self._page.goto(WEIBO_HOME, timeout=30000, wait_until="domcontentloaded")
            self._wait_for(3000)
            cookies = self._context.cookies()
            # 检查是否有 SUB cookie
            has_sub = any(c.get("name") == "SUB" for c in cookies)
            logger.info("微博 Cookie 初始化完成，SUB=%s", "有" if has_sub else "无")
            return True
        except Exception as e:
            logger.error("微博首页访问失败: %s", e)
            return False

    def _fetch_page(self, keyword: str, page_num: int, product: str = "") -> list[dict]:
        """通过页面上下文 fetch API 抓取单页。"""
        self._wait()
        # 构建 containerid
        # q 需要 URL 编码
        from urllib.parse import quote
        q_encoded = quote(keyword)
        container_id = f"100103type=1&q={keyword}"
        container_id_encoded = quote(container_id, safe="")

        api_url = (
            f"{WEIBO_API}?containerid={container_id_encoded}&page={page_num}"
        )

        # 在页面上下文内 fetch（自动携带 Cookie）
        js_code = f"""
            async () => {{
                const r = await fetch("{api_url}", {{
                    headers: {{
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": "https://m.weibo.cn/",
                        "Accept": "application/json"
                    }}
                }});
                const text = await r.text();
                try {{
                    return JSON.parse(text);
                }} catch {{
                    return {{ ok: -1, raw: text.substring(0, 200) }};
                }}
            }}
        """

        try:
            data = self._page.evaluate(js_code)
        except Exception as e:
            logger.error("fetch API 失败: %s", e)
            return []

        if data.get("ok") != 1:
            logger.warning("微博 API 返回异常: ok=%s, msg=%s", data.get("ok"), data.get("msg", ""))
            return []

        cards = data.get("data", {}).get("cards", [])
        items = []

        for card in cards:
            if card.get("card_type") != 9:  # 微博卡片
                continue
            mblog = card.get("mblog")
            if not mblog:
                continue
            item = self._parse_mblog(mblog, product)
            if item:
                items.append(item)

        return items

    def _parse_mblog(self, mblog: dict, product: str = "") -> Optional[dict]:
        """解析单条微博。"""
        raw_id = str(mblog.get("id", ""))
        if not raw_id:
            return None

        user = mblog.get("user", {})
        author = user.get("screen_name", "")

        text = self._strip_html(mblog.get("text", ""))
        created_at = mblog.get("created_at", "")

        metrics = {
            "likes": mblog.get("attitudes_count", 0),
            "comments": mblog.get("comments_count", 0),
            "shares": mblog.get("reposts_count", 0),
            "views": 0,
        }

        post_url = f"https://m.weibo.cn/detail/{raw_id}"
        title = text[:100] if text else f"@a{author} 的微博"

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
            product=product,
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
        """去除 HTML 标签和实体。"""
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("&nbsp;", " ").replace("&amp;", "&")
        text = text.replace("&lt;", "<").replace("&gt;", ">")
        text = text.replace("&quot;", '"').replace("&#39;", "'")
        return text.strip()


# ---------------------------------------------------------------------------
# 测试入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    scraper = WeiboPlaywrightScraper()
    try:
        results = scraper.search("MatePad Pro", max_pages=1)
        print(f"\n=== 共获取 {len(results)} 条 ===")
        for r in results[:3]:
            print(f"\n  [{r['platform_name']}] @{r['author']}")
            print(f"    {r['content'][:120]}")
            print(f"    URL: {r['post_url']}")
    finally:
        scraper.close()
        from playwright_base import close_browser
        close_browser()