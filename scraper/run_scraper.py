"""
主运行脚本 — 多渠道舆情数据采集

功能:
  1. 调用各平台采集器搜索关键词
  2. 统一输出标准化 JSON 到 data/ 目录
  3. 支持按产品、按平台、按关键词灵活采集

数据平台:
  - smzdm:    什么值得买（电商爆料）
  - zol:      中关村在线（产品点评）
  - weibo:    微博（公众舆论）
  - zhihu:    知乎（深度讨论）
  - bilibili: B站（视频评测）
  - coolapk:  酷安（数码圈讨论）

用法:
  # 采集所有产品、所有平台
  python run_scraper.py

  # 采集指定产品
  python run_scraper.py --product matebook-pro-s

  # 仅采集指定平台
  python run_scraper.py --platform weibo,zhihu

  # 仅采集电商平台（SMZDM + ZOL）
  python run_scraper.py --platform smzdm,zol

  # 仅采集社交平台（微博 + 知乎 + B站 + 酷安）
  python run_scraper.py --platform social

  # 自定义翻页数
  python run_scraper.py --max-pages 5
"""

import json
import os
import sys
import argparse
import logging
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# 导入处理：兼容包内运行和直接运行
# ---------------------------------------------------------------------------
_current_dir = os.path.dirname(os.path.abspath(__file__))
if _current_dir not in sys.path:
    sys.path.insert(0, _current_dir)

from smzdm_scraper import SmzdmScraper
from zol_scraper import ZolScraper
from sentiment_analyzer import SentimentAnalyzer
from weibo_scraper import WeiboScraper
from zhihu_scraper import ZhihuScraper
from bilibili_scraper import BilibiliScraper
# Playwright 采集器（用于需要浏览器渲染的平台）
from smzdm_playwright_scraper import SmzdmPlaywrightScraper
from weibo_playwright_scraper import WeiboPlaywrightScraper
from playwright_base import close_browser

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_scraper")

# ---------------------------------------------------------------------------
# 北京时区
# ---------------------------------------------------------------------------
TZ_BEIJING = timezone(timedelta(hours=8))

# ---------------------------------------------------------------------------
# 路径配置
# ---------------------------------------------------------------------------
SCRAPER_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRAPER_DIR, "..", "data")  # 输出到 sentiment-site/data/

# ---------------------------------------------------------------------------
# 产品定义 — 各平台搜索关键词
# ---------------------------------------------------------------------------
PRODUCTS = {
    "matebook-pro-s": {
        "name": "MateBook Pro S",
        "smzdm_keywords": [
            "MateBook Pro S", "华为MateBook Pro S",
        ],
        "zol_url": None,
        "weibo_keywords": [
            "MateBook Pro S", "华为MateBook Pro S",
        ],
        "zhihu_keywords": [
            "MateBook Pro S", "华为MateBook Pro S",
        ],
        "bilibili_keywords": [
            "MateBook Pro S", "华为MateBook Pro S",
        ],
        "coolapk_keywords": [
            "MateBook Pro S", "华为MateBook",
        ],
    },
    "matepad-pro": {
        "name": "MatePad Pro",
        "smzdm_keywords": [
            "MatePad Pro", "华为MatePad Pro 12", "MatePad Pro 12.2", "MatePad Pro 12.2英寸",
        ],
        "zol_url": "https://detail.zol.com.cn/2136/2135278/review.shtml",
        "weibo_keywords": [
            "MatePad Pro", "华为MatePad Pro",
        ],
        "zhihu_keywords": [
            "MatePad Pro", "华为MatePad Pro",
        ],
        "bilibili_keywords": [
            "MatePad Pro", "华为MatePad Pro", "MatePad Pro评测",
        ],
        "coolapk_keywords": [
            "MatePad Pro", "华为平板",
        ],
    },
    "matepad-pro-max": {
        "name": "MatePad Pro Max",
        "smzdm_keywords": [
            "MatePad Pro Max", "华为MatePad Pro 13.2",
        ],
        "zol_url": None,
        "weibo_keywords": [
            "MatePad Pro Max", "华为MatePad Pro 13.2",
        ],
        "zhihu_keywords": [
            "MatePad Pro Max", "华为MatePad Pro 13.2",
        ],
        "bilibili_keywords": [
            "MatePad Pro Max", "华为MatePad Pro 13.2", "MatePad Pro Max评测",
        ],
        "coolapk_keywords": [
            "MatePad Pro Max", "华为MatePad Pro 13.2",
        ],
    },
    "matebook-fold": {
        "name": "MateBook Fold",
        "smzdm_keywords": [
            "MateBook Fold", "华为MateBook Fold", "MateBook 非凡大师",
        ],
        "zol_url": "https://detail.zol.com.cn/2129/2128382/review.shtml",
        "weibo_keywords": [
            "MateBook Fold", "华为MateBook Fold", "MateBook非凡大师",
        ],
        "zhihu_keywords": [
            "MateBook Fold", "华为MateBook Fold",
        ],
        "bilibili_keywords": [
            "MateBook Fold", "华为MateBook Fold", "MateBook Fold评测",
        ],
        "coolapk_keywords": [
            "MateBook Fold", "华为折叠笔记本",
        ],
    },
}

# 平台分组
PLATFORM_GROUPS = {
    "ecommerce": ["smzdm", "zol"],       # 电商平台
    "social": ["weibo", "zhihu", "bilibili", "coolapk"],  # 社交平台
    "all": ["smzdm", "zol", "weibo", "zhihu", "bilibili", "coolapk"],
}

# 默认配置
DEFAULT_MAX_PAGES = 3
DEFAULT_MIN_INTERVAL = 2.0


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def ensure_dir(path: str) -> str:
    """确保目录存在并返回路径。"""
    os.makedirs(path, exist_ok=True)
    return path


def save_json(data, filepath: str) -> str:
    """保存 JSON 文件。"""
    ensure_dir(os.path.dirname(filepath))
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("已保存: %s", filepath)
    return filepath


def resolve_platforms(platform_arg: str) -> list[str]:
    """解析 --platform 参数，支持分组名和逗号分隔列表。"""
    if platform_arg in PLATFORM_GROUPS:
        return PLATFORM_GROUPS[platform_arg]
    return [p.strip() for p in platform_arg.split(",") if p.strip()]


# ---------------------------------------------------------------------------
# 核心采集逻辑
# ---------------------------------------------------------------------------

def scrape_product(
    product_key: str,
    product_config: dict,
    *,
    platforms: list[str],
    max_pages: int = DEFAULT_MAX_PAGES,
    min_interval: float = DEFAULT_MIN_INTERVAL,
) -> dict:
    """
    采集单个产品的全平台数据。

    Returns:
        {
            "product_key": ...,
            "product_name": ...,
            "scraped_at": ...,
            "platforms": {
                "weibo": { "items": [...], "count": N, "error": None },
                "zhihu": {...},
                ...
            },
            "total_items": N,
        }
    """
    product_name = product_config["name"]
    scraped_at = datetime.now(TZ_BEIJING).isoformat()
    result = {
        "product_key": product_key,
        "product_name": product_name,
        "scraped_at": scraped_at,
        "platforms": {},
    }

    # ---- 什么值得买 (Playwright) ----
    if "smzdm" in platforms:
        keywords = product_config.get("smzdm_keywords", [])
        if keywords:
            logger.info("[%s] 开始采集 什么值得买 (Playwright)...", product_name)
            try:
                scraper = SmzdmPlaywrightScraper()
                all_items = []
                for kw in keywords:
                    items = scraper.search(kw, max_pages=max_pages, product=product_key)
                    all_items.extend(items)
                scraper.close()
                result["platforms"]["smzdm"] = {
                    "items": all_items,
                    "count": len(all_items),
                }
                logger.info("[%s] 什么值得买: %d 条", product_name, len(all_items))
            except Exception as exc:
                logger.error("[%s] 什么值得买采集失败: %s", product_name, exc)
                result["platforms"]["smzdm"] = {"items": [], "count": 0, "error": str(exc)}
        else:
            result["platforms"]["smzdm"] = {"items": [], "count": 0, "note": "no keywords"}

    # ---- ZOL ----
    if "zol" in platforms:
        zol_url = product_config.get("zol_url")
        if zol_url:
            logger.info("[%s] 开始采集 ZOL...", product_name)
            try:
                scraper = ZolScraper(min_interval=min_interval)
                items = scraper.fetch_reviews(zol_url, max_pages=max_pages)
                std_items = _normalize_zol(items, product_key)
                result["platforms"]["zol"] = {
                    "items": std_items,
                    "count": len(std_items),
                }
                logger.info("[%s] ZOL: %d 条", product_name, len(std_items))
            except Exception as exc:
                logger.error("[%s] ZOL采集失败: %s", product_name, exc)
                result["platforms"]["zol"] = {"items": [], "count": 0, "error": str(exc)}
        else:
            result["platforms"]["zol"] = {"items": [], "count": 0, "note": "no zol_url"}

    # ---- 微博 (Playwright) ----
    if "weibo" in platforms:
        keywords = product_config.get("weibo_keywords", [])
        if keywords:
            logger.info("[%s] 开始采集 微博 (Playwright)...", product_name)
            try:
                scraper = WeiboPlaywrightScraper()
                all_items = []
                for kw in keywords:
                    items = scraper.search(kw, max_pages=max_pages, product=product_key)
                    all_items.extend(items)
                scraper.close()
                result["platforms"]["weibo"] = {
                    "items": all_items,
                    "count": len(all_items),
                }
                logger.info("[%s] 微博: %d 条", product_name, len(all_items))
            except Exception as exc:
                logger.error("[%s] 微博采集失败: %s", product_name, exc)
                result["platforms"]["weibo"] = {"items": [], "count": 0, "error": str(exc)}
        else:
            result["platforms"]["weibo"] = {"items": [], "count": 0, "note": "no keywords"}

    # ---- 知乎 (当前需要登录，暂时跳过) ----
    if "zhihu" in platforms:
        result["platforms"]["zhihu"] = {
            "items": [],
            "count": 0,
            "note": "知乎搜索需要登录态，Playwright 也无法绕过（搜索结果显示'未搜索到相关内容'），待后续研究"
        }
        logger.info("[%s] 知乎: 跳过（需要登录）", product_name)

    # ---- B站 ----
    if "bilibili" in platforms:
        keywords = product_config.get("bilibili_keywords", [])
        if keywords:
            logger.info("[%s] 开始采集 B站...", product_name)
            try:
                scraper = BilibiliScraper(min_interval=min_interval)
                items = scraper.search_multi_keywords(keywords, max_pages=max_pages, product=product_key)
                result["platforms"]["bilibili"] = {
                    "items": items,
                    "count": len(items),
                }
                logger.info("[%s] B站: %d 条", product_name, len(items))
            except Exception as exc:
                logger.error("[%s] B站采集失败: %s", product_name, exc)
                result["platforms"]["bilibili"] = {"items": [], "count": 0, "error": str(exc)}
        else:
            result["platforms"]["bilibili"] = {"items": [], "count": 0, "note": "no keywords"}

    # ---- 酷安 (API 需认证，当前被阻断) ----
    if "coolapk" in platforms:
        result["platforms"]["coolapk"] = {
            "items": [],
            "count": 0,
            "note": "酷安 API 需要登录认证（请求返回 403/1001，浏览器端 fetch 被 CORS 拦截，搜索页显示'出错了'），待后续研究"
        }
        logger.info("[%s] 酷安: 跳过（API 需认证）", product_name)

    # ---- 汇总 ----
    total = sum(
        p.get("count", 0)
        for p in result["platforms"].values()
    )
    result["total_items"] = total

    return result


# ---------------------------------------------------------------------------
# 数据标准化（SMZDM / ZOL → 统一格式）
# ---------------------------------------------------------------------------

def _normalize_smzdm(items: list[dict], product_key: str) -> list[dict]:
    """将 SMZDM 原始数据转为统一格式。"""
    import hashlib
    std = []
    for item in items:
        article_id = str(item.get("article_id", item.get("id", "")))
        article_url = item.get("article_url", "")
        raw_id = article_id or article_url
        if not raw_id:
            continue

        h = hashlib.sha256(f"smzdm:{raw_id}".encode()).hexdigest()[:12]
        std.append({
            "id": f"smzdm_{h}",
            "platform": "smzdm",
            "post_url": article_url or f"https://www.smzdm.com/p/{article_id}/",
            "author": item.get("user_name", ""),
            "title": (item.get("title", "") or item.get("article_title", ""))[:200],
            "content": item.get("description", "")[:2000],
            "publish_time": item.get("publish_time", item.get("date", "")),
            "metrics": {
                "likes": item.get("worthy_count", 0),
                "comments": item.get("comment_count", 0),
                "shares": 0,
                "views": 0,
            },
            "comments": [],
            "scraped_at": datetime.now(TZ_BEIJING).isoformat(),
            "product": product_key,
        })
    return std


def _normalize_zol(items: list[dict], product_key: str) -> list[dict]:
    """将 ZOL 原始数据转为统一格式。"""
    import hashlib
    std = []
    for item in items:
        username = item.get("username", "")
        url = item.get("url", "")
        raw_id = f"{username}_{url}"
        if not raw_id.strip("_"):
            continue

        h = hashlib.sha256(f"zol:{raw_id}".encode()).hexdigest()[:12]
        std.append({
            "id": f"zol_{h}",
            "platform": "zol",
            "post_url": url or "",
            "author": username,
            "title": (item.get("content", ""))[:200],
            "content": item.get("content", "")[:2000],
            "publish_time": item.get("publish_time", ""),
            "metrics": {
                "likes": item.get("like_count", 0),
                "comments": 0,
                "shares": 0,
                "views": 0,
            },
            "comments": [],
            "scraped_at": datetime.now(TZ_BEIJING).isoformat(),
            "product": product_key,
        })
    return std


# ---------------------------------------------------------------------------
# 主运行函数
# ---------------------------------------------------------------------------

def run(
    products: list = None,
    *,
    platforms: list[str] = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    min_interval: float = DEFAULT_MIN_INTERVAL,
) -> dict:
    """
    主运行函数。

    Args:
        products: 产品 key 列表，None 表示全部。
        platforms: 平台列表，默认全部。
        max_pages: 每个平台最大翻页数。
        min_interval: 请求间隔（秒）。

    Returns:
        完整结果字典。
    """
    if products is None:
        products = list(PRODUCTS.keys())
    if platforms is None:
        platforms = PLATFORM_GROUPS["all"]

    date_str = datetime.now(TZ_BEIJING).strftime("%Y%m%d")
    all_results = {
        "run_metadata": {
            "run_at": datetime.now(TZ_BEIJING).isoformat(),
            "platforms": platforms,
            "products": products,
            "config": {
                "max_pages": max_pages,
                "min_interval": min_interval,
            },
        },
        "products": {},
    }

    for product_key in products:
        if product_key not in PRODUCTS:
            logger.warning("未知产品: %s，跳过", product_key)
            continue

        config = PRODUCTS[product_key]
        logger.info("=" * 60)
        logger.info("开始采集: %s (%s)", config["name"], product_key)
        logger.info("=" * 60)

        try:
            product_result = scrape_product(
                product_key,
                config,
                platforms=platforms,
                max_pages=max_pages,
                min_interval=min_interval,
            )
            all_results["products"][product_key] = product_result

            # 每个产品单独保存
            filename = f"{product_key}_{date_str}.json"
            filepath = os.path.join(DATA_DIR, "products", filename)
            save_json(product_result, filepath)

        except Exception as exc:
            logger.error("[%s] 采集失败: %s", product_key, exc, exc_info=True)
            all_results["products"][product_key] = {
                "error": str(exc),
                "product_key": product_key,
            }

    # 保存汇总
    summary_filename = f"all_products_{date_str}.json"
    summary_path = os.path.join(DATA_DIR, "daily", summary_filename)
    save_json(all_results, summary_path)

    # 打印汇总
    _print_summary(all_results)

    return all_results


def _print_summary(results: dict) -> None:
    """打印运行汇总。"""
    print("\n" + "=" * 70)
    print("  多渠道舆情数据采集汇总")
    print("=" * 70)

    grand_total = 0
    for key, product in results.get("products", {}).items():
        name = PRODUCTS.get(key, {}).get("name", key)
        print(f"\n  [{name}]")
        platform_data = product.get("platforms", {})
        for plat_name, plat_result in platform_data.items():
            count = plat_result.get("count", 0)
            error = plat_result.get("error")
            note = plat_result.get("note")
            if error:
                print(f"    {plat_name:12s}: 失败 ({error})")
            elif note:
                print(f"    {plat_name:12s}: 跳过 ({note})")
            else:
                print(f"    {plat_name:12s}: {count} 条")
                grand_total += count

    print(f"\n  总计: {grand_total} 条")
    print(f"  输出目录: {DATA_DIR}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="华为产品多渠道舆情数据采集",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python run_scraper.py                                    # 全产品全平台
  python run_scraper.py --product matebook-pro-s           # 指定产品
  python run_scraper.py --platform weibo,zhihu             # 指定平台
  python run_scraper.py --platform ecommerce               # 仅电商平台
  python run_scraper.py --platform social --max-pages 5    # 社交平台，5页
  python run_scraper.py -p matepad-pro -s weibo -n 5       # 微博搜MatePad Pro，5页
        """,
    )

    parser.add_argument(
        "-p", "--product",
        nargs="*",
        choices=list(PRODUCTS.keys()),
        default=None,
        help="指定产品 key（不指定则全部）。可选: %(choices)s",
    )
    parser.add_argument(
        "-s", "--platform",
        type=str,
        default="all",
        help="数据平台。支持: all, ecommerce, social, 或逗号分隔的列表 (smzdm,zol,weibo,zhihu,bilibili,coolapk)",
    )
    parser.add_argument(
        "-n", "--max-pages",
        type=int,
        default=DEFAULT_MAX_PAGES,
        help=f"每个平台最大翻页数。默认: {DEFAULT_MAX_PAGES}",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_MIN_INTERVAL,
        help=f"请求间隔（秒）。默认: {DEFAULT_MIN_INTERVAL}",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help="数据输出目录。默认: sentiment-site/data/",
    )

    args = parser.parse_args()

    # 解析平台
    platforms = resolve_platforms(args.platform)

    # 自定义数据目录
    global DATA_DIR
    if args.data_dir:
        DATA_DIR = args.data_dir

    print(f"平台: {platforms}")
    print(f"产品: {args.product or list(PRODUCTS.keys())}")
    print(f"翻页: {args.max_pages} 页/平台")
    print(f"输出: {DATA_DIR}")
    print()

    run(
        products=args.product,
        platforms=platforms,
        max_pages=args.max_pages,
        min_interval=args.interval,
    )


if __name__ == "__main__":
    main()