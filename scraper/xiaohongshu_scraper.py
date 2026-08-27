# -*- coding: utf-8 -*-
"""
小红书采集器 — 基于 Playwright 持久化浏览器的网页版采集。

为什么用 Playwright 而不是 requests：
  - 小红书网页搜索接口需要 x-s / x-t 等动态签名，直接 requests 会被风控拦截
  - 浏览器自动化可复用真实登录态（web_session 等 Cookie），绕过签名
  - 与项目现有 weibo / smzdm 的 Playwright 采集器思路一致

技术要点：
  1. 首次使用需扫码登录一次（python xhs_login.py），登录态持久化到本地 profile 目录
  2. 后续运行复用同一 profile（.xhs_profile/），无需重复登录
  3. 搜索页为无限滚动，通过滚动加载并按 note_id 去重
  4. 可选抓取笔记详情（正文 + 互动数据 + 热门评论）

数据说明：
  - 搜索卡片仅含：标题、作者、点赞数、封面链接、发布时间
  - 笔记详情页含：完整正文、点赞/收藏/评论/分享、评论列表
"""

import os
import sys
import re
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List

_current_dir = os.path.dirname(os.path.abspath(__file__))
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

from playwright.sync_api import sync_playwright

logger = logging.getLogger("xiaohongshu")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
TZ_BEIJING = timezone(timedelta(hours=8))

XHS_HOME = "https://www.xiaohongshu.com"
XHS_SEARCH = "https://www.xiaohongshu.com/search_result"
XHS_EXPLORE = "https://www.xiaohongshu.com/explore"

DEFAULT_PROFILE_DIR = os.path.join(_current_dir, ".xhs_profile")

# 笔记 ID 为 24 位十六进制（MongoDB ObjectId），也兼容更短/带前缀的情况
NOTE_ID_RE = re.compile(r"(?:/explore/|/search_result/|/discovery/item/)([a-zA-Z0-9_-]+)")

# 反检测脚本（隐藏 Playwright 特征）
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || {};
window.chrome.runtime = {};
"""


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def parse_count(raw) -> int:
    """将 '1.2万' / '3.4千' / '2.1亿' / '123' 等字符串转为整数。"""
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    s = str(raw).strip().replace(",", "")
    if not s:
        return 0
    try:
        if "亿" in s:
            return int(float(s.replace("亿", "")) * 1e8)
        if "万" in s:
            return int(float(s.replace("万", "")) * 1e4)
        if "千" in s:
            return int(float(s.replace("千", "")) * 1e3)
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def _pick(d: dict, *keys: str) -> str:
    """从 dict 中取第一个非空的字符串值。"""
    for k in keys:
        v = d.get(k)
        if v:
            return str(v).strip()
    return ""


# ---------------------------------------------------------------------------
# 采集器
# ---------------------------------------------------------------------------

class XiaohongshuScraper:
    """小红书 — Playwright 持久化上下文采集器。"""

    platform = "xiaohongshu"
    platform_name = "小红书"
    base_url = XHS_HOME
    min_interval = 2.0

    def __init__(
        self,
        user_data_dir: Optional[str] = None,
        headless: bool = True,
        min_interval: float = 2.0,
    ):
        """
        Args:
            user_data_dir: 持久化 profile 目录，默认 .xhs_profile/。
            headless: 是否无头运行。首次登录需设为 False。
            min_interval: 请求间隔（秒）。
        """
        self.user_data_dir = user_data_dir or DEFAULT_PROFILE_DIR
        self.headless = headless
        self.min_interval = min_interval
        self._pw = None
        self._context = None
        self._page = None
        self._last_request = 0.0

    # ------------------------------------------------------------------
    # 浏览器生命周期
    # ------------------------------------------------------------------

    def _start(self) -> None:
        """启动持久化浏览器上下文。"""
        if self._context is not None:
            return
        self._pw = sync_playwright().start()
        self._context = self._pw.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
            channel="msedge",
            viewport={"width": 1280, "height": 900},
            locale="zh-CN",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
            ),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        self._context.add_init_script(STEALTH_JS)
        self._page = self._context.new_page()
        logger.info("小红书浏览器已启动 (headless=%s)", self.headless)

    def close(self) -> None:
        """关闭浏览器。"""
        try:
            if self._page is not None:
                self._page.close()
        except Exception:
            pass
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._pw = None

    def is_logged_in(self) -> bool:
        """判断是否已登录（是否存在 web_session Cookie）。"""
        if self._context is None:
            return False
        try:
            cookies = self._context.cookies()
            return any(c.get("name") == "web_session" and c.get("value") for c in cookies)
        except Exception:
            return False

    def login(self, timeout: int = 180) -> bool:
        """交互式扫码登录（会弹出可见浏览器窗口）。

        Returns:
            True 表示登录成功。
        """
        self.close()
        self.headless = False
        self._start()

        print("=" * 60)
        print("  请在弹出的浏览器窗口中完成小红书登录（扫码）")
        print("  登录成功后本脚本会自动检测并保存登录态")
        print("=" * 60)

        try:
            self._page.goto(XHS_HOME, timeout=60000, wait_until="domcontentloaded")
        except Exception as exc:
            self._last_request = time.time()
            logger.error("访问小红书首页失败: %s", exc)

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_logged_in():
                logger.info("检测到登录态，登录成功")
                return True
            time.sleep(3)

        logger.warning("等待登录超时（%d 秒），请重试", timeout)
        return False

    # ------------------------------------------------------------------
    # 请求控制
    # ------------------------------------------------------------------

    def _wait(self) -> None:
        elapsed = time.time() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.time()

    def _goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 40000) -> bool:
        self._wait()
        try:
            self._page.goto(url, timeout=timeout, wait_until=wait_until)
            return True
        except Exception as exc:
            logger.error("导航失败: %s - %s", url, exc)
            return False

    # ------------------------------------------------------------------
    # 搜索主入口
    # ------------------------------------------------------------------

    def search(
        self,
        keyword: str,
        max_pages: int = 3,
        product: str = "",
        *,
        fetch_detail: bool = False,
        max_detail: int = 10,
    ) -> List[dict]:
        """搜索关键词并返回统一格式数据。

        Args:
            keyword: 搜索关键词。
            max_pages: 搜索页滚动加载轮数（约每轮 10-15 条）。
            product: 关联产品 key。
            fetch_detail: 是否打开笔记详情抓取正文与评论（较慢）。
            max_detail: fetch_detail=True 时最多抓取多少条详情。

        Returns:
            统一格式数据列表。
        """
        self._start()
        if not self.is_logged_in():
            logger.error(
                "小红书未登录。请先运行: python xhs_login.py 完成扫码登录"
            )
            return []

        summaries = self._load_search_notes(keyword, max_pages)
        logger.info("小红书搜索 '%s' 获取 %d 条笔记摘要", keyword, len(summaries))

        if fetch_detail:
            enriched = []
            for i, s in enumerate(summaries[:max_detail]):
                note_id = s.get("note_id")
                if not note_id:
                    enriched.append(s)
                    continue
                logger.info("抓取详情 %d/%d: %s", i + 1, min(len(summaries), max_detail), note_id)
                detail = self.fetch_note_detail(note_id)
                if detail:
                    merged = dict(s)
                    merged.update(detail)
                    enriched.append(merged)
                else:
                    enriched.append(s)
                self._wait()
            summaries = enriched

        results = []
        for s in summaries:
            item = self._standardize_item(s, product=product)
            if item:
                results.append(item)

        return results

    # ------------------------------------------------------------------
    # 搜索页滚动加载
    # ------------------------------------------------------------------

    def _load_search_notes(self, keyword: str, max_pages: int) -> List[dict]:
        """滚动加载搜索页并提取笔记摘要。"""
        from urllib.parse import quote
        url = f"{XHS_SEARCH}?keyword={quote(keyword)}&source=web_search_result_notes"

        if not self._goto(url, wait_until="domcontentloaded", timeout=40000):
            return []

        # 等待首屏卡片渲染
        self._page.wait_for_timeout(4000)

        seen: dict = {}
        for _ in range(max(1, max_pages)):
            notes = self._extract_search_notes()
            for n in notes:
                nid = n.get("note_id")
                if nid and nid not in seen:
                    seen[nid] = n
            self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self._page.wait_for_timeout(1800)

        # 最后一次提取
        for n in self._extract_search_notes():
            nid = n.get("note_id")
            if nid and nid not in seen:
                seen[nid] = n

        return list(seen.values())

    def _extract_search_notes(self) -> List[dict]:
        """从当前 DOM 提取搜索笔记摘要。"""
        js = """
        () => {
          const out = [];
          const items = document.querySelectorAll('section.note-item');
          for (const it of items) {
            const cover = it.querySelector('a.cover');
            const href = cover ? (cover.getAttribute('href') || '') : '';
            const titleEl = it.querySelector('a.title span, a.title .title, .title');
            const title = titleEl ? titleEl.innerText.trim() : '';
            const nameEl = it.querySelector('.author .name, .info .name, .name');
            const name = nameEl ? nameEl.innerText.trim() : '';
            const likeEl = it.querySelector('.like-wrapper .count, .count');
            const like = likeEl ? likeEl.innerText.trim() : '';
            const timeEl = it.querySelector('.time, .date, .author-wrapper .time');
            const time = timeEl ? timeEl.innerText.trim() : '';
            // 附带的互动标识（可能包含 评论/收藏 数量）
            out.push({ href, title, author: name, like, time });
          }
          return out;
        }
        """
        try:
            raw = self._page.evaluate(js)
        except Exception as exc:
            logger.error("提取搜索卡片失败: %s", exc)
            return []

        notes = []
        for r in raw or []:
            href = r.get("href", "")
            note_id = self._extract_note_id(href)
            if not note_id:
                continue
            notes.append({
                "note_id": note_id,
                "post_url": f"{XHS_EXPLORE}/{note_id}",
                "title": r.get("title", ""),
                "author": r.get("author", ""),
                "like": r.get("like", ""),
                "time": r.get("time", ""),
            })
        return notes

    @staticmethod
    def _extract_note_id(href: str) -> str:
        """从链接中提取笔记 ID。"""
        if not href:
            return ""
        m = NOTE_ID_RE.search(href)
        if m:
            return m.group(1)
        return ""

    # ------------------------------------------------------------------
    # 笔记详情
    # ------------------------------------------------------------------

    def fetch_note_detail(self, note_id: str) -> Optional[dict]:
        """打开笔记详情页，提取正文、互动数据与评论。"""
        if not note_id:
            return None
        url = f"{XHS_EXPLORE}/{note_id}"
        if not self._goto(url, wait_until="domcontentloaded", timeout=40000):
            return None
        self._page.wait_for_timeout(3500)

        js = """
        () => {
          const g = (sel) => {
            const el = document.querySelector(sel);
            return el ? el.innerText.trim() : '';
          };
          const title = g('#detail-title') || g('.title');
          let content = '';
          const nc = document.querySelector('#detail-desc') || document.querySelector('.note-content') || document.querySelector('.desc');
          if (nc) content = nc.innerText.trim();
          const author = g('.author-wrapper .name') || g('.username') || g('.author-name');
          const date = g('.bottom-container .date') || g('.date');
          const like = g('.engage-bar .like-wrapper .count') || g('.like-wrapper .count');
          const collect = g('.engage-bar .collect-wrapper .count') || g('.collect-wrapper .count');
          const chat = g('.engage-bar .chat-wrapper .count') || g('.chat-wrapper .count');
          const share = g('.engage-bar .share-wrapper .count') || g('.share-wrapper .count');

          const comments = [];
          document.querySelectorAll('.comment-item').forEach(c => {
            const nmEl = c.querySelector('.name');
            const ctEl = c.querySelector('.note-text') || c.querySelector('.content');
            const lkEl = c.querySelector('.like-count .count') || c.querySelector('.like-wrapper .count');
            const dtEl = c.querySelector('.date');
            comments.push({
              author: nmEl ? nmEl.innerText.trim() : '',
              content: ctEl ? ctEl.innerText.trim() : '',
              likes: lkEl ? lkEl.innerText.trim() : '',
              time: dtEl ? dtEl.innerText.trim() : '',
            });
          });

          return { title, content, author, date, like, collect, chat, share, comments };
        }
        """
        try:
            d = self._page.evaluate(js)
        except Exception as exc:
            logger.error("提取笔记详情失败: %s", exc)
            return None

        if not d:
            return None

        return {
            "title": d.get("title", ""),
            "content": d.get("content", ""),
            "author": d.get("author", ""),
            "publish_time": d.get("date", ""),
            "metrics": {
                "likes": parse_count(d.get("like")),
                "comments": parse_count(d.get("chat")),
                "shares": parse_count(d.get("share")),
                "views": 0,
                "collects": parse_count(d.get("collect")),
            },
            "comments": [
                {
                    "author": c.get("author", ""),
                    "content": c.get("content", ""),
                    "time": c.get("time", ""),
                    "likes": parse_count(c.get("likes")),
                }
                for c in (d.get("comments") or [])[:20]
                if c.get("content")
            ],
        }

    # ------------------------------------------------------------------
    # 标准化输出
    # ------------------------------------------------------------------

    def _standardize_item(self, s: dict, product: str = "") -> Optional[dict]:
        """将笔记摘要/详情转为统一格式。"""
        note_id = s.get("note_id") or self._extract_note_id(s.get("post_url", ""))
        if not note_id:
            return None

        title = s.get("title", "")
        content = s.get("content", "")

        # 无正文时用标题兜底
        if not content:
            content = title

        metrics = {
            "likes": 0, "comments": 0, "shares": 0, "views": 0,
        }
        if s.get("metrics"):
            metrics.update(s["metrics"])
        elif s.get("like"):
            # 仅有搜索卡片点赞数
            metrics["likes"] = parse_count(s.get("like"))

        now = datetime.now(TZ_BEIJING).isoformat()

        return {
            "id": f"{self.platform}_{note_id}",
            "platform": self.platform,
            "platform_name": self.platform_name,
            "post_url": s.get("post_url") or f"{XHS_EXPLORE}/{note_id}",
            "author": s.get("author", ""),
            "title": (title or "")[:200],
            "content": (content or "")[:2000],
            "publish_time": self._normalize_time(s.get("publish_time") or s.get("time", "")),
            "metrics": metrics,
            "comments": s.get("comments", []),
            "scraped_at": now,
            "product": product,
        }

    @staticmethod
    def _normalize_time(raw: str) -> str:
        """将各种时间格式统一为 ISO 8601（北京时间）。"""
        if not raw:
            return ""
        raw = raw.strip()
        if "T" in raw:
            return raw

        # 相对时间: "3天前" / "5小时前" / "刚刚" / "昨天"
        now = datetime.now(TZ_BEIJING)
        try:
            if "分钟前" in raw:
                n = int(re.search(r"\d+", raw).group())
                return (now - timedelta(minutes=n)).isoformat()
            if "小时前" in raw:
                n = int(re.search(r"\d+", raw).group())
                return (now - timedelta(hours=n)).isoformat()
            if "天前" in raw:
                n = int(re.search(r"\d+", raw).group())
                return (now - timedelta(days=n)).isoformat()
            if raw == "刚刚":
                return now.isoformat()
            if raw in ("昨天", "昨日"):
                return (now - timedelta(days=1)).isoformat()
        except Exception:
            pass

        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m-%d", "%m月%d日"):
            try:
                dt = datetime.strptime(raw, fmt)
                if dt.year == 1900:
                    dt = dt.replace(year=now.year)
                return dt.replace(tzinfo=TZ_BEIJING).isoformat()
            except ValueError:
                continue

        return raw


# ---------------------------------------------------------------------------
# 测试入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    scraper = XiaohongshuScraper(headless=True)
    try:
        scraper._start()
        if not scraper.is_logged_in():
            print("未登录，请先运行 python xhs_login.py")
        else:
            results = scraper.search("MatePad Pro", max_pages=2, fetch_detail=False)
            print(f"\n=== 共获取 {len(results)} 条 ===")
            for r in results[:5]:
                print(f"\n  [{r['platform_name']}] @{r['author']}")
                print(f"    标题: {r['title'][:60]}")
                print(f"    点赞: {r['metrics']['likes']}  评论: {r['metrics']['comments']}")
                print(f"    链接: {r['post_url']}")
    finally:
        scraper.close()