"""
SMZDM Playwright 采集器 — 通过浏览器渲染获取搜索页数据。

技术要点:
  - SMZDM 搜索页是 JS 渲染的，纯 requests 返回 202 反爬
  - Playwright 可正常渲染并获取 .feed-block 元素
  - 每页约 20 条结果，包含标题、价格、日期、商城、链接
  - 请求间隔 >= 3 秒
"""

import os
import sys
import re
import logging
from typing import Optional

_current_dir = os.path.dirname(os.path.abspath(__file__))
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

from playwright_base import PlaywrightScraper

logger = logging.getLogger("smzdm_pw")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
SMZDM_SEARCH_URL = "https://search.smzdm.com/"
SMZDM_BASE = "https://www.smzdm.com"


class SmzdmPlaywrightScraper(PlaywrightScraper):
    """SMZDM 什么值得买 — Playwright 版采集器。"""

    platform = "smzdm"
    platform_name = "什么值得买"
    base_url = SMZDM_BASE
    min_interval = 3.0

    def search(self, keyword: str, max_pages: int = 3, product: str = "") -> list[dict]:
        """
        搜索 SMZDM。

        Args:
            keyword: 搜索关键词。
            max_pages: 最大翻页数（每页约 20 条）。
            product: 关联产品 key。

        Returns:
            统一格式数据列表。
        """
        results = []
        page_num = 1

        while page_num <= max_pages:
            logger.info("SMZDM 搜索 '%s' 第 %d/%d 页", keyword, page_num, max_pages)
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

        logger.info("SMZDM 搜索完成，共 %d 条", len(results))
        return results

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _fetch_page(self, keyword: str, page_num: int, product: str = "") -> list[dict]:
        """抓取单页搜索结果。"""
        # 构建 URL
        params = f"c=home&s={keyword}&v=b"
        if page_num > 1:
            params += f"&p={page_num}"
        url = f"{SMZDM_SEARCH_URL}?{params}"

        if not self._goto(url, wait_until="domcontentloaded", timeout=30000):
            return []

        # 等待渲染
        self._wait_for(5000)

        # 获取 feed-block 元素
        blocks = self._query_all(".feed-block")
        if not blocks:
            logger.warning("未找到 .feed-block 元素")
            return []

        items = []
        for block in blocks:
            item = self._parse_block(block, keyword, product)
            if item:
                items.append(item)

        logger.info("第 %d 页获取 %d 条", page_num, len(items))
        return items

    def _parse_block(self, block, keyword: str, product: str = "") -> Optional[dict]:
        """解析单个 feed-block 元素。"""
        try:
            # 标题
            title_el = block.query_selector(".feed-block-title")
            title = title_el.inner_text().strip() if title_el else ""

            # 链接
            link_el = block.query_selector("a")
            link = link_el.get_attribute("href") if link_el else ""
            if link and link.startswith("/"):
                link = SMZDM_BASE + link

            # 提取 ID
            raw_id = ""
            if link:
                match = re.search(r"/p/(\d+)/?", link)
                if match:
                    raw_id = match.group(1)
            if not raw_id:
                raw_id = str(hash(title + link))[:16]

            # 价格/扩展信息
            extras_el = block.query_selector(".feed-block-extras")
            extras_text = extras_el.inner_text().strip() if extras_el else ""

            # 解析价格和日期/商城
            price = ""
            mall = ""
            pub_date = ""
            if extras_text:
                # 尝试分离价格和日期/商城
                lines = [l.strip() for l in extras_text.split("\n") if l.strip()]
                for line in lines:
                    if "元" in line or "¥" in line or re.search(r"\d+\.?\d*", line):
                        if not price:
                            price = line
                    elif "京东" in line or "天猫" in line or "淘宝" in line or \
                         "拼多多" in line or "苏宁" in line or "唯品会" in line or \
                         "考拉" in line or "亚马逊" in line or "小米有品" in line:
                        mall = line
                    elif re.match(r"\d{2}-\d{2}\s", line) or re.match(r"\d{4}-\d{2}-\d{2}", line):
                        pub_date = line
                    elif not pub_date and not mall:
                        # 可能是日期
                        if re.match(r"\d{2}-\d{2}", line) or "小时" in line or "分钟" in line:
                            pub_date = line

            # 描述
            desc_el = block.query_selector(".feed-block-descripe")
            desc = desc_el.inner_text().strip() if desc_el else ""

            # 内容（标题 + 描述）
            content = f"{title}\n{desc}".strip()
            if price:
                content += f"\n价格: {price}"
            if mall:
                content += f"\n商城: {mall}"

            return self._standardize_item(
                raw_id=raw_id,
                post_url=link,
                author=mall or "SMZDM",
                title=title,
                content=content,
                publish_time=pub_date,
                metrics={"likes": 0, "comments": 0, "shares": 0, "views": 0},
                comments=[],
                product=product,
            )
        except Exception as e:
            logger.debug("解析 block 失败: %s", e)
            return None


# ---------------------------------------------------------------------------
# 测试入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    scraper = SmzdmPlaywrightScraper()
    try:
        results = scraper.search("MatePad Pro", max_pages=1)
        print(f"\n=== 共获取 {len(results)} 条 ===")
        for r in results[:3]:
            print(f"\n  [{r['platform_name']}] {r['title'][:80]}")
            print(f"    URL: {r['post_url']}")
            print(f"    Content: {r['content'][:120]}")
    finally:
        scraper.close()
        from playwright_base import close_browser
        close_browser()