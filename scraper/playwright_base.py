"""
Playwright 采集器基类 — 为需要浏览器渲染的平台提供统一接口。

使用场景:
  - SMZDM: 搜索页 JS 渲染，requests 返回 202
  - 微博: m.weibo.cn API 需要浏览器 Cookie
  - 知乎: 搜索需要登录态（当前不可用）
  - 酷安: API 需要认证（当前不可用）

技术要点:
  - 共享浏览器实例（所有采集器复用同一个 Edge 进程）
  - 自动获取/刷新 Cookie
  - 上下文隔离（每个采集器独立 context）
  - 统一数据格式与 BaseScraper 兼容
"""

import logging
import time
import random
from typing import Optional
from contextlib import contextmanager

logger = logging.getLogger("playwright_base")

# ---------------------------------------------------------------------------
# 全局浏览器实例（单例模式，节省资源）
# ---------------------------------------------------------------------------
_browser = None
_playwright = None
_launch_count = 0

# 备用 User-Agent
FALLBACK_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


def _random_ua() -> str:
    return random.choice(FALLBACK_USER_AGENTS)


def get_playwright():
    """获取全局 Playwright 实例。"""
    global _playwright
    if _playwright is None:
        from playwright.sync_api import sync_playwright
        _playwright = sync_playwright().start()
        logger.info("Playwright 已启动")
    return _playwright


def get_browser():
    """获取全局浏览器实例（Edge，无头模式）。"""
    global _browser, _launch_count
    if _browser is None:
        pw = get_playwright()
        _browser = pw.chromium.launch(
            headless=True,
            channel="msedge",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ]
        )
        _launch_count += 1
        logger.info("Edge 浏览器已启动 (第 %d 次)", _launch_count)
    return _browser


def close_browser():
    """关闭全局浏览器实例。"""
    global _browser, _playwright
    if _browser:
        try:
            _browser.close()
            logger.info("浏览器已关闭")
        except Exception:
            pass
        _browser = None
    if _playwright:
        try:
            _playwright.stop()
            logger.info("Playwright 已停止")
        except Exception:
            pass
        _playwright = None


def new_context(ua: str = None) -> tuple:
    """创建新的浏览器上下文和页面。

    Returns:
        (context, page) 元组。
    """
    browser = get_browser()
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=ua or _random_ua(),
        locale="zh-CN",
    )
    page = context.new_page()
    return context, page


# ---------------------------------------------------------------------------
# 通用 Playwright 采集器基类
# ---------------------------------------------------------------------------

class PlaywrightScraper:
    """基于 Playwright 的采集器基类。

    子类需要实现:
      - platform: str         平台标识
      - platform_name: str    平台中文名
      - search(keyword, max_pages, product) -> list[dict]
    """

    platform: str = ""
    platform_name: str = ""
    base_url: str = ""
    min_interval: float = 3.0  # 请求间隔（秒）

    def __init__(self):
        self._last_request = 0.0
        self._context = None
        self._page = None

    # ------------------------------------------------------------------
    # 资源管理
    # ------------------------------------------------------------------

    def _ensure_page(self):
        """确保页面可用（懒初始化）。"""
        if self._page is None or self._page.is_closed():
            self._context, self._page = new_context()
            logger.info("[%s] 创建新页面", self.platform_name)

    def close(self):
        """关闭当前采集器的页面和上下文。"""
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None
        if self._context:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None

    # ------------------------------------------------------------------
    # 请求控制
    # ------------------------------------------------------------------

    def _wait(self):
        """限速等待。"""
        elapsed = time.time() - self._last_request
        if elapsed < self.min_interval:
            sleep_time = self.min_interval - elapsed + random.uniform(0, 1)
            logger.debug("等待 %.1f 秒...", sleep_time)
            time.sleep(sleep_time)
        self._last_request = time.time()

    def _goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30000) -> bool:
        """安全导航到 URL。

        Returns:
            True 表示成功。
        """
        self._ensure_page()
        self._wait()
        try:
            self._page.goto(url, timeout=timeout, wait_until=wait_until)
            return True
        except Exception as e:
            logger.error("[%s] 导航失败: %s - %s", self.platform_name, url, e)
            return False

    def _wait_for(self, ms: int = 3000):
        """等待指定毫秒。"""
        if self._page:
            self._page.wait_for_timeout(ms)

    def _query_all(self, selector: str):
        """查询所有匹配元素。"""
        if self._page:
            return self._page.query_selector_all(selector)
        return []

    def _query(self, selector: str):
        """查询单个元素。"""
        if self._page:
            return self._page.query_selector(selector)
        return None

    # ------------------------------------------------------------------
    # 标准化输出
    # ------------------------------------------------------------------

    def _standardize_item(
        self,
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
        """统一数据格式。"""
        from datetime import datetime, timezone, timedelta
        TZ = timezone(timedelta(hours=8))

        return {
            "id": f"{self.platform}_{raw_id}",
            "platform": self.platform,
            "platform_name": self.platform_name,
            "post_url": post_url,
            "author": author,
            "title": title,
            "content": content,
            "publish_time": publish_time,
            "metrics": metrics or {"likes": 0, "comments": 0, "shares": 0, "views": 0},
            "comments": comments or [],
            "scraped_at": datetime.now(TZ).isoformat(),
            "product": product,
        }

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def __del__(self):
        self.close()


# ---------------------------------------------------------------------------
# 模块级清理（进程退出时自动调用）
# ---------------------------------------------------------------------------
import atexit

@atexit.register
def _cleanup():
    close_browser()