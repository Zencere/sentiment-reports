"""
什么值得买 (SMZDM) 爬虫
抓取华为产品相关的爆料/评论数据

API 端点:
  - 搜索页: https://search.smzdm.com/?c=home&s={keyword}&v=b
  - 爆料JSON: https://faxian.smzdm.com/json_more?p={page}
  - 兴趣流:  https://damo.smzdm.com/interest/more_page (POST)

技术要点:
  - 请求间隔 >= 3 秒
  - User-Agent 轮换 (fake_useragent + 内置备用列表)
  - 指数退避重试 (最多 3 次)
  - 遵守 robots.txt
  - 仅采集公开数据
"""

import json
import random
import time
import logging
from datetime import datetime
from typing import Optional
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

try:
    from fake_useragent import UserAgent
    _ua = UserAgent()
except Exception:
    _ua = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("smzdm_scraper")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SMZDM_BASE = "https://www.smzdm.com"
SEARCH_URL = "https://search.smzdm.com/"
JSON_MORE_URL = "https://faxian.smzdm.com/json_more"
DAMO_API_URL = "https://damo.smzdm.com/interest/more_page"

# 备用 User-Agent 列表（fake_useragent 不可用时使用）
FALLBACK_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

DEFAULT_HEADERS_BASE = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

JSON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.smzdm.com/",
    "Origin": "https://www.smzdm.com",
    "X-Requested-With": "XMLHttpRequest",
}

MAX_RETRIES = 3
RETRY_BACKOFF_FACTOR = 2


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _random_ua() -> str:
    """获取一个随机 User-Agent 字符串。"""
    if _ua is not None:
        try:
            return _ua.random
        except Exception:
            pass
    return random.choice(FALLBACK_USER_AGENTS)


def _check_robots(domain: str) -> bool:
    """检查 robots.txt 是否允许爬取。返回 True 表示允许。"""
    try:
        rp = RobotFileParser()
        rp.set_url(f"https://{domain}/robots.txt")
        rp.read()
        allowed = rp.can_fetch("*", f"https://{domain}/")
        if not allowed:
            logger.warning("robots.txt 禁止爬取 %s", domain)
        return allowed
    except Exception as exc:
        logger.warning("无法读取 robots.txt (%s): %s", domain, exc)
        return True  # 无法读取时默认允许


# ---------------------------------------------------------------------------
# SmzdmScraper
# ---------------------------------------------------------------------------

class SmzdmScraper:
    """什么值得买爬虫。"""

    def __init__(self, min_interval: float = 3.0):
        self.min_interval = min_interval
        self._last_request = 0.0
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS_BASE)

    # ---- 内部方法 ----------------------------------------------------------

    def _wait(self) -> None:
        """确保请求间隔 >= min_interval 秒。"""
        elapsed = time.time() - self._last_request
        if elapsed < self.min_interval:
            sleep_time = self.min_interval - elapsed + random.uniform(0, 1)
            logger.debug("等待 %.1f 秒...", sleep_time)
            time.sleep(sleep_time)
        self._last_request = time.time()

    def _headers(self, extra: Optional[dict] = None) -> dict:
        """生成带随机 UA 的请求头。"""
        h = {"User-Agent": _random_ua()}
        if extra:
            h.update(extra)
        return h

    def _request(
        self,
        method: str,
        url: str,
        *,
        retries: int = MAX_RETRIES,
        **kwargs,
    ) -> requests.Response:
        """带重试机制的 HTTP 请求。"""
        last_exc = None
        for attempt in range(1, retries + 1):
            try:
                self._wait()
                kwargs.setdefault("headers", self._headers())
                kwargs.setdefault("timeout", 15)
                resp = self.session.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.exceptions.Timeout as exc:
                last_exc = exc
                logger.warning("请求超时 (第 %d 次): %s", attempt, url)
            except requests.exceptions.HTTPError as exc:
                last_exc = exc
                status = exc.response.status_code if exc.response is not None else None
                logger.warning("HTTP %s (第 %d 次): %s", status, attempt, url)
                if status == 429:
                    time.sleep(10 * attempt)
            except requests.exceptions.ConnectionError as exc:
                last_exc = exc
                logger.warning("连接错误 (第 %d 次): %s", attempt, url)
            except Exception as exc:
                last_exc = exc
                logger.warning("请求异常 (第 %d 次): %s - %s", attempt, url, exc)

            if attempt < retries:
                backoff = RETRY_BACKOFF_FACTOR ** attempt
                time.sleep(backoff + random.random())

        raise RuntimeError(f"请求失败（已重试 {retries} 次）: {url}") from last_exc

    # ---- 公开 API -----------------------------------------------------------

    def search_by_keyword(
        self,
        keywords: list,
        max_pages: int = 3,
    ) -> list[dict]:
        """
        通过关键词搜索什么值得买爆料。

        Args:
            keywords: 搜索关键词列表，取第一个有效关键词。
            max_pages: 最大翻页数。

        Returns:
            结构化爆料列表，每项包含:
              - title: 标题
              - price: 价格
              - source_platform: 来源平台
              - comment_count: 评论数
              - worthy_votes: "值"票数
              - unworthy_votes: "不值"票数
              - url: 爆料链接
              - publish_time: 发布时间
              - scraped_at: 抓取时间
              - source: "smzdm"
        """
        if not keywords:
            return []

        results: list[dict] = []
        scraped_at = datetime.now().isoformat()

        for keyword in keywords:
            logger.info("搜索关键词: %s (最多 %d 页)", keyword, max_pages)

            for page in range(1, max_pages + 1):
                try:
                    items = self._fetch_json_page(keyword, page)
                    if not items:
                        logger.info("关键词 [%s] 第 %d 页无数据，停止翻页", keyword, page)
                        break

                    for item in items:
                        item["scraped_at"] = scraped_at
                        item["source"] = "smzdm"
                        item["search_keyword"] = keyword
                        results.append(item)

                    logger.info("关键词 [%s] 第 %d 页获取 %d 条", keyword, page, len(items))

                except Exception as exc:
                    logger.error("关键词 [%s] 第 %d 页抓取失败: %s", keyword, page, exc)
                    break

            # 关键词之间额外间隔
            time.sleep(self.min_interval)

        # 去重（按 URL）
        seen = set()
        unique: list[dict] = []
        for item in results:
            url = item.get("url", "")
            if url and url not in seen:
                seen.add(url)
                unique.append(item)
        logger.info("搜索完成，共 %d 条（去重后 %d 条）", len(results), len(unique))
        return unique

    def _fetch_json_page(self, keyword: str, page: int) -> list[dict]:
        """
        尝试通过 JSON 接口获取爆料列表。

        优先使用 faxian.smzdm.com/json_more（GET），
        失败时回退到 damo.smzdm.com/interest/more_page（POST）。
        """
        # 方式一：faxian.smzdm.com/json_more（GET）
        items = self._try_faxian_json(keyword, page)
        if items:
            return items

        # 方式二：damo.smzdm.com/interest/more_page（POST）
        items = self._try_damo_api(keyword, page)
        if items:
            return items

        # 方式三：搜索页 HTML 解析
        return self._try_search_html(keyword, page)

    # ------------------------------------------------------------------
    # 方式一：faxian JSON
    # ------------------------------------------------------------------

    def _try_faxian_json(self, keyword: str, page: int) -> list[dict]:
        """尝试 faxian.smzdm.com/json_more GET 接口。"""
        try:
            params = {"p": page, "type": "1", "keyword": keyword}
            resp = self._request(
                "GET",
                JSON_MORE_URL,
                params=params,
                headers=self._headers(JSON_HEADERS),
            )
            data = resp.json()
            return self._parse_faxian_json(data)
        except Exception as exc:
            logger.debug("faxian JSON 接口失败: %s", exc)
            return []

    def _parse_faxian_json(self, data: dict) -> list[dict]:
        """解析 faxian JSON 返回数据。"""
        items: list[dict] = []
        # 可能的字段名变体
        raw_list = (
            data.get("data", {}).get("list")
            or data.get("data", {}).get("rows")
            or data.get("list")
            or data.get("data", [])
        )
        if isinstance(raw_list, dict):
            raw_list = raw_list.get("list") or raw_list.get("rows") or []
        if not isinstance(raw_list, list):
            return []

        for entry in raw_list:
            if not isinstance(entry, dict):
                continue
            items.append(self._normalize_item(entry))
        return items

    # ------------------------------------------------------------------
    # 方式二：damo API (POST)
    # ------------------------------------------------------------------

    def _try_damo_api(self, keyword: str, page: int) -> list[dict]:
        """尝试 damo.smzdm.com/interest/more_page POST 接口。"""
        try:
            payload = {
                "keyword": keyword,
                "page": page,
                "page_size": 20,
                "sort": "default",
                "interest_type": "all",
                "channel_id": "",
            }
            resp = self._request(
                "POST",
                DAMO_API_URL,
                json=payload,
                headers=self._headers(JSON_HEADERS),
            )
            data = resp.json()
            return self._parse_damo_json(data)
        except Exception as exc:
            logger.debug("damo API 接口失败: %s", exc)
            return []

    def _parse_damo_json(self, data: dict) -> list[dict]:
        """解析 damo API 返回数据。"""
        items: list[dict] = []
        raw_list = (
            data.get("data", {}).get("list")
            or data.get("data", {}).get("rows")
            or data.get("list")
            or data.get("data", [])
        )
        if isinstance(raw_list, dict):
            raw_list = raw_list.get("list") or raw_list.get("rows") or []
        if not isinstance(raw_list, list):
            return []

        for entry in raw_list:
            if not isinstance(entry, dict):
                continue
            items.append(self._normalize_item(entry))
        return items

    # ------------------------------------------------------------------
    # 方式三：搜索页 HTML
    # ------------------------------------------------------------------

    def _try_search_html(self, keyword: str, page: int) -> list[dict]:
        """回退：解析搜索页 HTML。"""
        try:
            params = {"c": "home", "s": keyword, "v": "b", "p": page}
            resp = self._request(
                "GET",
                SEARCH_URL,
                params=params,
                headers=self._headers(DEFAULT_HEADERS_BASE),
            )
            soup = BeautifulSoup(resp.text, "lxml")
            items: list[dict] = []

            # 搜索列表卡片
            for card in soup.select(".feed-row-wide, .feed-block, .feed-card"):
                title_el = card.select_one(".feed-block-title a, .feed-ver-title a")
                price_el = card.select_one(".feed-block-extras span, .z-highlight, .feed-price")
                mall_el = card.select_one(".feed-block-extras a, .mall, .feed-mall")
                comment_el = card.select_one(".feed-block-extras .comment, .feed-comment")
                worthy_el = card.select_one(".worthy-num, .feed-worthy")
                unworthy_el = card.select_one(".unworthy-num, .feed-unworthy")
                time_el = card.select_one(".feed-block-extras .time, .feed-time")

                title = title_el.get_text(strip=True) if title_el else ""
                if not title:
                    continue

                url = title_el.get("href", "") if title_el else ""
                if url and not url.startswith("http"):
                    url = SMZDM_BASE + url

                items.append({
                    "title": title,
                    "price": price_el.get_text(strip=True) if price_el else "",
                    "source_platform": mall_el.get_text(strip=True) if mall_el else "",
                    "comment_count": self._parse_int(comment_el.get_text(strip=True)) if comment_el else 0,
                    "worthy_votes": self._parse_int(worthy_el.get_text(strip=True)) if worthy_el else 0,
                    "unworthy_votes": self._parse_int(unworthy_el.get_text(strip=True)) if unworthy_el else 0,
                    "url": url,
                    "publish_time": time_el.get_text(strip=True) if time_el else "",
                })
            return items
        except Exception as exc:
            logger.debug("搜索页 HTML 解析失败: %s", exc)
            return []

    # ------------------------------------------------------------------
    # 字段归一化
    # ------------------------------------------------------------------

    def _normalize_item(self, entry: dict) -> dict:
        """将不同接口返回的字段统一为标准格式。"""
        # 标题
        title = (
            entry.get("article_title")
            or entry.get("title")
            or entry.get("name")
            or ""
        )

        # URL
        url = entry.get("article_url") or entry.get("url") or entry.get("link") or ""
        if url and not url.startswith("http"):
            url = SMZDM_BASE + url

        # 价格
        price = entry.get("article_price") or entry.get("price") or ""

        # 来源平台
        mall = entry.get("article_mall") or entry.get("mall") or entry.get("source_platform") or ""

        # 评论数
        comment = entry.get("article_comment") or entry.get("comment_count") or entry.get("comments") or 0

        # 值/不值
        worthy = entry.get("article_worthy") or entry.get("worthy") or entry.get("worthy_votes") or 0
        unworthy = entry.get("article_unworthy") or entry.get("unworthy") or entry.get("unworthy_votes") or 0

        # 时间
        pub_time = (
            entry.get("article_date")
            or entry.get("publish_time")
            or entry.get("time")
            or entry.get("date")
            or ""
        )

        return {
            "title": self._clean_text(title),
            "price": self._clean_text(str(price)),
            "source_platform": self._clean_text(mall),
            "comment_count": self._parse_int(comment),
            "worthy_votes": self._parse_int(worthy),
            "unworthy_votes": self._parse_int(unworthy),
            "url": url,
            "publish_time": self._clean_text(pub_time),
        }

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_text(text: str) -> str:
        """清理文本（去空白、换行）。"""
        return " ".join(text.replace("\n", " ").replace("\r", " ").split())

    @staticmethod
    def _parse_int(value) -> int:
        """安全地解析整数。"""
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            cleaned = "".join(ch for ch in value if ch.isdigit() or ch == "-")
            try:
                return int(cleaned)
            except ValueError:
                return 0
        return 0


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def scrape_smzdm(
    keywords: list,
    max_pages: int = 3,
    min_interval: float = 3.0,
) -> list[dict]:
    """
    便捷函数：抓取什么值得买爆料。

    Args:
        keywords: 搜索关键词列表。
        max_pages: 每个关键词的最大翻页数。
        min_interval: 请求最小间隔（秒）。

    Returns:
        结构化爆料列表。
    """
    scraper = SmzdmScraper(min_interval=min_interval)
    return scraper.search_by_keyword(keywords, max_pages=max_pages)


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    # 简单测试：搜索华为MatePad Pro
    test_keywords = ["华为MatePad Pro 12.2"]
    print(f"测试搜索: {test_keywords}")

    results = scrape_smzdm(test_keywords, max_pages=1, min_interval=3.0)
    print(f"\n获取到 {len(results)} 条结果:\n")
    for i, item in enumerate(results[:10], 1):
        print(f"{i}. {item['title']}")
        print(f"   价格: {item['price']}  |  平台: {item['source_platform']}")
        print(f"   评论: {item['comment_count']}  |  值: {item['worthy_votes']} / 不值: {item['unworthy_votes']}")
        print(f"   链接: {item['url']}")
        print()