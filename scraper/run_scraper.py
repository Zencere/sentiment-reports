"""
主运行脚本 — 电商数据爬虫 & 舆情分析

功能:
  1. 调用 smzdm_scraper 抓取什么值得买爆料
  2. 调用 zol_scraper 抓取 ZOL 产品点评
  3. 调用 sentiment_analyzer 进行情感分析
  4. 输出 JSON 结果到 scraper/output/ 目录

用法:
  # 抓取所有产品
  python run_scraper.py

  # 抓取指定产品
  python run_scraper.py --product matepad-pro

  # 仅抓取 SMZDM
  python run_scraper.py --source smzdm

  # 仅抓取 ZOL
  python run_scraper.py --source zol

  # 自定义翻页数
  python run_scraper.py --max-pages 5
"""

import json
import os
import sys
import argparse
import logging
from datetime import datetime

# 处理包内运行和直接运行的导入差异
try:
    from .smzdm_scraper import SmzdmScraper
    from .zol_scraper import ZolScraper
    from .sentiment_analyzer import SentimentAnalyzer
except ImportError:
    # 直接运行 run_scraper.py 时，确保当前目录在 sys.path
    _current_dir = os.path.dirname(os.path.abspath(__file__))
    if _current_dir not in sys.path:
        sys.path.insert(0, _current_dir)
    from smzdm_scraper import SmzdmScraper
    from zol_scraper import ZolScraper
    from sentiment_analyzer import SentimentAnalyzer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_scraper")

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

# 输出目录
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")

# 产品定义：关键词映射 + ZOL URL
PRODUCTS = {
    "matebook-pro-s": {
        "name": "MateBook Pro S",
        "smzdm_keywords": [
            "MateBook Pro S",
            "华为MateBook Pro S",
        ],
        "zol_url": None,  # ZOL 暂无此产品页面
    },
    "matepad-pro": {
        "name": "MatePad Pro",
        "smzdm_keywords": [
            "MatePad Pro",
            "华为MatePad Pro 12",
            "MatePad Pro 12.2",
            "MatePad Pro 12.2英寸",
        ],
        "zol_url": "https://detail.zol.com.cn/2136/2135278/review.shtml",
    },
    "matepad-pro-max": {
        "name": "MatePad Pro Max",
        "smzdm_keywords": [
            "MatePad Pro Max",
            "华为MatePad Pro 13.2",
        ],
        "zol_url": None,
    },
    "matebook-fold": {
        "name": "MateBook Fold",
        "smzdm_keywords": [
            "MateBook Fold",
            "华为MateBook Fold",
            "MateBook 非凡大师",
        ],
        "zol_url": "https://detail.zol.com.cn/2129/2128382/review.shtml",
    },
}

# 默认爬取配置
DEFAULT_MAX_PAGES_SMZDM = 3
DEFAULT_MAX_PAGES_ZOL = 5
DEFAULT_MIN_INTERVAL = 3.0


# ---------------------------------------------------------------------------
# 核心逻辑
# ---------------------------------------------------------------------------

def ensure_output_dir() -> str:
    """确保输出目录存在并返回其路径。"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return OUTPUT_DIR


def save_json(data, filename: str) -> str:
    """保存 JSON 到输出目录，返回文件路径。"""
    out_dir = ensure_output_dir()
    filepath = os.path.join(out_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("结果已保存: %s", filepath)
    return filepath


def scrape_product(
    product_key: str,
    product_config: dict,
    *,
    source: str = "all",
    max_pages_smzdm: int = DEFAULT_MAX_PAGES_SMZDM,
    max_pages_zol: int = DEFAULT_MAX_PAGES_ZOL,
    min_interval: float = DEFAULT_MIN_INTERVAL,
) -> dict:
    """
    抓取单个产品的数据并进行分析。

    Returns:
        {
            "product_key": ...,
            "product_name": ...,
            "scraped_at": ...,
            "smzdm": { "items": [...], "count": N },
            "zol":    { "reviews": [...], "count": N },
            "sentiment_analysis": { ... },
        }
    """
    product_name = product_config["name"]
    scraped_at = datetime.now().isoformat()
    result = {
        "product_key": product_key,
        "product_name": product_name,
        "scraped_at": scraped_at,
        "smzdm": None,
        "zol": None,
        "sentiment_analysis": None,
    }

    smzdm_items = []
    zol_reviews = []

    # ---- SMZDM ----
    if source in ("all", "smzdm"):
        keywords = product_config.get("smzdm_keywords", [])
        if keywords:
            logger.info("[%s] 开始抓取什么值得买...", product_name)
            try:
                scraper = SmzdmScraper(min_interval=min_interval)
                smzdm_items = scraper.search_by_keyword(keywords, max_pages=max_pages_smzdm)
                result["smzdm"] = {
                    "items": smzdm_items,
                    "count": len(smzdm_items),
                }
                logger.info("[%s] SMZDM: %d 条", product_name, len(smzdm_items))
            except Exception as exc:
                logger.error("[%s] SMZDM 抓取失败: %s", product_name, exc)
                result["smzdm"] = {"items": [], "count": 0, "error": str(exc)}
        else:
            result["smzdm"] = {"items": [], "count": 0, "note": "no keywords configured"}

    # ---- ZOL ----
    if source in ("all", "zol"):
        zol_url = product_config.get("zol_url")
        if zol_url:
            logger.info("[%s] 开始抓取 ZOL 点评...", product_name)
            try:
                scraper = ZolScraper(min_interval=min_interval)
                zol_reviews = scraper.fetch_reviews(zol_url, max_pages=max_pages_zol)
                result["zol"] = {
                    "reviews": zol_reviews,
                    "count": len(zol_reviews),
                }
                logger.info("[%s] ZOL: %d 条", product_name, len(zol_reviews))
            except Exception as exc:
                logger.error("[%s] ZOL 抓取失败: %s", product_name, exc)
                result["zol"] = {"reviews": [], "count": 0, "error": str(exc)}
        else:
            result["zol"] = {"reviews": [], "count": 0, "note": "no zol_url configured"}

    # ---- 情感分析 ----
    if smzdm_items or zol_reviews:
        logger.info("[%s] 开始情感分析...", product_name)
        try:
            analyzer = SentimentAnalyzer()
            analysis = analyzer.analyze_all(smzdm_items, zol_reviews)
            result["sentiment_analysis"] = analysis
        except Exception as exc:
            logger.error("[%s] 情感分析失败: %s", product_name, exc)
            result["sentiment_analysis"] = {"error": str(exc)}

    return result


def run(
    products: list = None,
    *,
    source: str = "all",
    max_pages_smzdm: int = DEFAULT_MAX_PAGES_SMZDM,
    max_pages_zol: int = DEFAULT_MAX_PAGES_ZOL,
    min_interval: float = DEFAULT_MIN_INTERVAL,
) -> dict:
    """
    主运行函数。

    Args:
        products: 产品 key 列表，None 表示全部。
        source: "all" | "smzdm" | "zol"
        max_pages_smzdm: SMZDM 最大翻页数。
        max_pages_zol: ZOL 最大翻页数。
        min_interval: 请求间隔（秒）。

    Returns:
        完整结果字典。
    """
    if products is None:
        products = list(PRODUCTS.keys())

    date_str = datetime.now().strftime("%Y%m%d")
    all_results = {
        "run_metadata": {
            "run_at": datetime.now().isoformat(),
            "source": source,
            "products": products,
            "config": {
                "max_pages_smzdm": max_pages_smzdm,
                "max_pages_zol": max_pages_zol,
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
        logger.info("开始处理: %s (%s)", config["name"], product_key)
        logger.info("=" * 60)

        try:
            product_result = scrape_product(
                product_key,
                config,
                source=source,
                max_pages_smzdm=max_pages_smzdm,
                max_pages_zol=max_pages_zol,
                min_interval=min_interval,
            )
            all_results["products"][product_key] = product_result

            # 每个产品单独保存一份 JSON
            filename = f"{product_key}_{date_str}.json"
            save_json(product_result, filename)

        except Exception as exc:
            logger.error("[%s] 处理失败: %s", product_key, exc)
            all_results["products"][product_key] = {
                "error": str(exc),
                "product_key": product_key,
            }

    # 保存汇总文件
    summary_filename = f"all_products_{date_str}.json"
    save_json(all_results, summary_filename)

    # 打印汇总
    _print_summary(all_results)

    return all_results


def _print_summary(results: dict) -> None:
    """打印运行汇总。"""
    print("\n" + "=" * 60)
    print("  舆情抓取汇总")
    print("=" * 60)

    for key, product in results.get("products", {}).items():
        name = PRODUCTS.get(key, {}).get("name", key)
        smzdm_count = 0
        zol_count = 0
        if product.get("smzdm"):
            smzdm_count = product["smzdm"].get("count", 0)
        if product.get("zol"):
            zol_count = product["zol"].get("count", 0)

        sentiment = product.get("sentiment_analysis", {})
        overall = sentiment.get("overall", {})

        print(f"\n  [{name}]")
        print(f"    SMZDM 爆料: {smzdm_count} 条")
        print(f"    ZOL 点评:   {zol_count} 条")
        if overall:
            print(f"    正面: {overall.get('positive', 0)}  "
                  f"中性: {overall.get('neutral', 0)}  "
                  f"负面: {overall.get('negative', 0)}  "
                  f"(总计 {overall.get('total', 0)})")

    print(f"\n  输出目录: {OUTPUT_DIR}")
    print("=" * 60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="华为产品舆情数据爬虫",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例:\n"
               "  python run_scraper.py\n"
               "  python run_scraper.py --product matepad-pro\n"
               "  python run_scraper.py --source smzdm --max-pages 5\n",
    )

    parser.add_argument(
        "-p", "--product",
        nargs="*",
        choices=list(PRODUCTS.keys()),
        default=None,
        help="指定产品 key（不指定则爬取全部）。可选: %(choices)s",
    )
    parser.add_argument(
        "-s", "--source",
        choices=["all", "smzdm", "zol"],
        default="all",
        help="数据来源。默认: all",
    )
    parser.add_argument(
        "--max-pages-smzdm",
        type=int,
        default=DEFAULT_MAX_PAGES_SMZDM,
        help=f"SMZDM 最大翻页数。默认: {DEFAULT_MAX_PAGES_SMZDM}",
    )
    parser.add_argument(
        "--max-pages-zol",
        type=int,
        default=DEFAULT_MAX_PAGES_ZOL,
        help=f"ZOL 最大翻页数。默认: {DEFAULT_MAX_PAGES_ZOL}",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_MIN_INTERVAL,
        help=f"请求间隔（秒）。默认: {DEFAULT_MIN_INTERVAL}",
    )

    args = parser.parse_args()

    run(
        products=args.product,
        source=args.source,
        max_pages_smzdm=args.max_pages_smzdm,
        max_pages_zol=args.max_pages_zol,
        min_interval=args.interval,
    )


if __name__ == "__main__":
    main()