"""
ZOL 中关村在线 (detail.zol.com.cn) 产品点评爬虫

点评页面以 HTML 直接渲染，使用 requests + BeautifulSoup4 解析。

分页规则（常见两种，均兼容）:
  - review.shtml -> review_1.shtml -> review_2.shtml ...
  - review.shtml?p=1 / ?page=1 ...

技术要点:
  - 请求间隔 >= 3 秒
  - User-Agent 轮换
  - 指数退避重试
  - 遵守 robots.txt
"""

import re
import random
import time
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

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
logger = logging.getLogger("zol_scraper")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

ZOL_BASE = "https://detail.zol.com.cn"

FALLBACK_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 Edg/119.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": ZOL_BASE,
}

MAX_RETRIES = 3
RETRY_BACKOFF_FACTOR = 2


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _random_ua() -> str:
    if _ua is not None:
        try:
            return _ua.random
        except Exception:
            pass
    return random.choice(FALLBACK_USER_AGENTS)


# ---------------------------------------------------------------------------
# ZolScraper
# ---------------------------------------------------------------------------

class ZolScraper:
    """ZOL 中关村在线产品点评爬虫。"""

    def __init__(self, min_interval: float = 3.0):
        self.min_interval = min_interval
        self._last_request = 0.0
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    # ---- 内部方法 ----------------------------------------------------------

    def _wait(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self.min_interval:
            sleep_time = self.min_interval - elapsed + random.uniform(0, 1)
            logger.debug("等待 %.1f 秒...", sleep_time)
            time.sleep(sleep_time)
        self._last_request = time.time()

    def _headers(self) -> dict:
        return {"User-Agent": _random_ua()}

    def _request(
        self,
        url: str,
        *,
        retries: int = MAX_RETRIES,
    ) -> requests.Response:
        """带重试机制的 GET 请求。"""
        last_exc = None
        for attempt in range(1, retries + 1):
            try:
                self._wait()
                resp = self.session.get(
                    url,
                    headers=self._headers(),
                    timeout=20,
                )
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
                time.sleep(RETRY_BACKOFF_FACTOR ** attempt + random.random())

        raise RuntimeError(f"请求失败（已重试 {retries} 次）: {url}") from last_exc

    # ---- 公开 API -----------------------------------------------------------

    def fetch_reviews(self, url: str, max_pages: int = 5) -> list[dict]:
        """
        抓取产品点评（含分页）。

        Args:
            url: 点评页面 URL（例如 https://detail.zol.com.cn/2136/2135278/review.shtml）
            max_pages: 最大翻页数。

        Returns:
            点评列表，每项包含:
              - username: 用户名
              - rating: 评分（1-5，如能解析到）
              - content: 评论内容
              - publish_time: 发布时间
              - like_count: 点赞数
              - url: 评论页面链接
              - scraped_at: 抓取时间
              - source: "zol"
        """
        results: list[dict] = []
        scraped_at = datetime.now().isoformat()

        base_url = self._normalize_url(url)
        logger.info("开始抓取点评: %s (最多 %d 页)", base_url, max_pages)

        for page in range(1, max_pages + 1):
            page_url = self._build_page_url(base_url, page)
            try:
                resp = self._request(page_url)
                items = self.parse_review_page(resp.text, page_url, scraped_at)

                if not items:
                    logger.info("第 %d 页无点评数据，停止翻页", page)
                    break

                results.extend(items)
                logger.info("第 %d 页获取 %d 条点评", page, len(items))
            except Exception as exc:
                logger.error("第 %d 页抓取失败: %s", page, exc)
                break

        logger.info("点评抓取完成，共 %d 条", len(results))
        return results

    # ---- 分页 URL 构造 ------------------------------------------------------

    @staticmethod
    def _normalize_url(url: str) -> str:
        """标准化 URL，去掉可能已有的分页参数。"""
        url = url.strip()
        if not url.startswith("http"):
            url = ZOL_BASE + url
        # 去掉查询参数
        return url.split("?")[0]

    @staticmethod
    def _build_page_url(base_url: str, page: int) -> str:
        """
        构造第 N 页 URL。

        第 1 页: review.shtml
        第 N 页: review_{N-1}.shtml  （ZOL 常见格式）
        """
        if page <= 1:
            return base_url

        # 处理 review.shtml -> review_1.shtml 的格式
        if base_url.endswith(".shtml"):
            stem = base_url[:-len(".shtml")]
            return f"{stem}_{page - 1}.shtml"

        # 其他情况回退到查询参数
        sep = "&" if "?" in base_url else "?"
        return f"{base_url}{sep}p={page}"

    # ---- 页面解析 -----------------------------------------------------------

    def parse_review_page(self, html: str, page_url: str, scraped_at: str) -> list[dict]:
        """解析单页点评 HTML，提取结构化数据。"""
        soup = BeautifulSoup(html, "lxml")
        items: list[dict] = []

        # 尝试多种可能的评论容器选择器
        review_blocks = (
            soup.select(".comment-list li")
            or soup.select(".review-list .review-item")
            or soup.select(".review-item")
            or soup.select(".comment-item")
            or soup.select(".content-list li")
            or soup.select(".review_list li")
            or soup.select(".pingjia-list li")
            or soup.select("[class*='review'] [class*='item']")
        )

        for block in review_blocks:
            item = self._parse_review_block(block, page_url, scraped_at)
            if item and item.get("content") or (item and item.get("username")):
                items.append(item)

        return items

    def _parse_review_block(self, block, page_url: str, scraped_at: str) -> Optional[dict]:
        """从单个评论块中提取字段。"""
        # 用户名
        username = self._extract_text(block, [
            ".user-name", ".review-name", ".comment-user", ".nickname",
            ".user_name", ".name", "a[class*='user']", "a[class*='name']",
        ])

        # 评分：优先数字，其次星级
        rating = self._extract_rating(block)

        # 评论内容
        content = self._extract_text(block, [
            ".comment-content", ".review-content", ".content", ".con",
            ".comment_text", ".review-text", ".text", "p", "dd", ".desc",
        ])

        # 时间
        publish_time = self._extract_text(block, [
            ".comment-time", ".review-time", ".time", ".date",
            ".comment_date", "[class*='time']", "span[class*='date']",
        ])

        # 点赞数
        like_count = self._extract_int(block, [
            ".comment-like", ".review-like", ".useful", ".like",
            ".comment_useful", "[class*='like']", "[class*='zan']",
        ])

        # 评论详情链接
        link_el = block.select_one("a[href*='review']") or block.select_one("a")
        comment_url = ""
        if link_el and link_el.get("href"):
            href = link_el["href"]
            if href.startswith("http"):
                comment_url = href
            elif href.startswith("/"):
                comment_url = "https://detail.zol.com.cn" + href
            else:
                comment_url = page_url

        if not username and not content:
            return None

        return {
            "username": username,
            "rating": rating,
            "content": content,
            "publish_time": publish_time,
            "like_count": like_count,
            "url": comment_url or page_url,
            "scraped_at": scraped_at,
            "source": "zol",
        }

    # ---- 字段提取辅助 --------------------------------------------------------

    @staticmethod
    def _extract_text(block, selectors: list) -> str:
        """按顺序尝试多个选择器提取文本。"""
        for sel in selectors:
            el = block.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if text:
                    return ZolScraper._clean_text(text)
        return ""

    @staticmethod
    def _extract_int(block, selectors: list) -> int:
        """按顺序尝试多个选择器提取整数。"""
        for sel in selectors:
            el = block.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                cleaned = "".join(ch for ch in text if ch.isdigit())
                if cleaned:
                    try:
                        return int(cleaned)
                    except ValueError:
                        continue
        return 0

    @staticmethod
    def _extract_rating(block) -> Optional[float]:
        """提取评分（1-5 分）。"""
        # 1) 数字评分
        for sel in [".score", ".rating", ".star-num", ".comment-score", "[class*='score']", "[class*='rating']"]:
            el = block.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                match = re.search(r"(\d+(?:\.\d+)?)", text)
                if match:
                    val = float(match.group(1))
                    if 0 < val <= 5:
                        return val
                    # 若是 0-10 分制，换算为 1-5 分制
                    if 5 < val <= 10:
                        return round(val / 2, 1)

        # 2) 星级图片 / class 标记（如 star5, star-4, star_3）
        for el in block.select("[class*='star']"):
            cls = " ".join(el.get("class", []))
            match = re.search(r"star[_-]?(\d)", cls, re.IGNORECASE)
            if match:
                return float(match.group(1))

        # 3) title 属性（如 title="5分"）
        for el in block.select("[title*='分']"):
            title = el.get("title", "")
            match = re.search(r"(\d+(?:\.\d+)?)\s*分", title)
            if match:
                return float(match.group(1))

        return None

    @staticmethod
    def _clean_text(text: str) -> str:
        return " ".join(text.replace("\n", " ").replace("\r", " ").split())


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def scrape_zol(
    url: str,
    max_pages: int = 5,
    min_interval: float = 3.0,
) -> list[dict]:
    """
    便捷函数：抓取 ZOL 产品点评。

    Args:
        url: 点评页面 URL。
        max_pages: 最大翻页数。
        min_interval: 请求最小间隔（秒）。

    Returns:
        结构化点评列表。
    """
    scraper = ZolScraper(min_interval=min_interval)
    return scraper.fetch_reviews(url, max_pages=max_pages)


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 简单测试
    test_url = "https://detail.zol.com.cn/2136/2135278/review.shtml"
    print(f"测试抓取: {test_url}")

    reviews = scrape_zol(test_url, max_pages=2, min_interval=3.0)
    print(f"\n获取到 {len(reviews)} 条点评:\n")
    for i, r in enumerate(reviews[:10], 1):
        print(f"{i}. [{r.get('rating') or '?'}星] {r['username']}")
        print(f"   内容: {r['content'][:80]}")
        print(f"   时间: {r['publish_time']}  |  点赞: {r['like_count']}")
        print()