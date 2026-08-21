"""
情感分析器（舆情监控）

结合 SnowNLP 中文情感分析和平台自带评分/投票数据，
输出综合情感标签和统计摘要。

核心逻辑:
  1. SnowNLP 对评论/标题文本打分（0-1，越接近 1 越正面）
  2. SMZDM: 值/不值投票 -> 平台标签
  3. ZOL:   评分 >=4 -> 正面, 3 -> 中性, <3 -> 负面
  4. 综合:  取 SnowNLP 和平台标签，优先级为平台标签 > SnowNLP
  5. 统计:  正面/中性/负面比例
"""

import logging
from typing import Optional

try:
    from snownlp import SnowNLP
    _SNOWNLP_AVAILABLE = True
except ImportError:
    _SNOWNLP_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sentiment_analyzer")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# SnowNLP 阈值
SNOW_POSITIVE_THRESHOLD = 0.6   # >= 0.6 为正面
SNOW_NEGATIVE_THRESHOLD = 0.4   # <= 0.4 为负面，中间为中性

# 情感标签
SENTIMENT_POSITIVE = "正面"
SENTIMENT_NEUTRAL = "中性"
SENTIMENT_NEGATIVE = "负面"


# ---------------------------------------------------------------------------
# 底层文本分析
# ---------------------------------------------------------------------------

def _snownlp_sentiment(text: str) -> Optional[float]:
    """使用 SnowNLP 分析文本情感，返回 0-1 之间的分数。"""
    if not text or not text.strip() or not _SNOWNLP_AVAILABLE:
        return None
    try:
        s = SnowNLP(text.strip())
        return s.sentiments
    except Exception as exc:
        logger.debug("SnowNLP 分析失败: %s", exc)
        return None


def _score_to_label(score: float) -> str:
    """将 SnowNLP 分数转为情感标签。"""
    if score >= SNOW_POSITIVE_THRESHOLD:
        return SENTIMENT_POSITIVE
    if score <= SNOW_NEGATIVE_THRESHOLD:
        return SENTIMENT_NEGATIVE
    return SENTIMENT_NEUTRAL


# ---------------------------------------------------------------------------
# SentimentAnalyzer
# ---------------------------------------------------------------------------

class SentimentAnalyzer:
    """综合情感分析器。"""

    def __init__(self):
        if not _SNOWNLP_AVAILABLE:
            logger.warning(
                "SnowNLP 未安装，将仅使用平台自带评分。"
                "请运行: pip install snownlp"
            )

    # ---- SMZDM 分析 ---------------------------------------------------------

    def analyze_smzdm_item(self, item: dict) -> dict:
        """
        分析单条 SMZDM 爆料的情感。

        输入字段:
          - title: 商品标题
          - worthy_votes: "值"票数
          - unworthy_votes: "不值"票数

        输出在输入基础上增加:
          - platform_sentiment: 平台标签（基于投票）
          - snownlp_score: SnowNLP 分数
          - snownlp_sentiment: SnowNLP 标签
          - final_sentiment: 综合标签
        """
        item = dict(item)  # 不修改原数据
        title = item.get("title", "")
        worthy = item.get("worthy_votes", 0)
        unworthy = item.get("unworthy_votes", 0)

        # 平台标签（基于投票）
        platform_sentiment = self._smzdm_vote_label(worthy, unworthy)

        # SnowNLP
        snownlp_score = _snownlp_sentiment(title)
        snownlp_sentiment = _score_to_label(snownlp_score) if snownlp_score is not None else None

        # 综合标签
        final_sentiment = self._combine(platform_sentiment, snownlp_sentiment)

        item["platform_sentiment"] = platform_sentiment
        item["snownlp_score"] = snownlp_score
        item["snownlp_sentiment"] = snownlp_sentiment
        item["final_sentiment"] = final_sentiment
        return item

    @staticmethod
    def _smzdm_vote_label(worthy: int, unworthy: int) -> Optional[str]:
        """
        根据值/不值投票确定平台标签。

        规则:
          - 值 > 不值 且 值 >= 2 -> 正面
          - 不值 > 值 且 不值 >= 2 -> 负面
          - 值 == 不值 或 票数太少 -> 中性
          - 无数据 -> None
        """
        total = worthy + unworthy
        if total == 0:
            return None
        if worthy >= 2 and worthy > unworthy * 1.5:
            return SENTIMENT_POSITIVE
        if unworthy >= 2 and unworthy > worthy * 1.5:
            return SENTIMENT_NEGATIVE
        return SENTIMENT_NEUTRAL

    # ---- ZOL 分析 -----------------------------------------------------------

    def analyze_zol_review(self, review: dict) -> dict:
        """
        分析单条 ZOL 点评的情感。

        输入字段:
          - content: 评论内容
          - rating: 评分（1-5）

        输出在输入基础上增加:
          - platform_sentiment: 平台标签（基于评分）
          - snownlp_score: SnowNLP 分数
          - snownlp_sentiment: SnowNLP 标签
          - final_sentiment: 综合标签
        """
        review = dict(review)
        content = review.get("content", "")
        rating = review.get("rating")

        # 平台标签（基于评分）
        platform_sentiment = self._zol_rating_label(rating)

        # SnowNLP（基于评论内容）
        snownlp_score = _snownlp_sentiment(content)
        snownlp_sentiment = _score_to_label(snownlp_score) if snownlp_score is not None else None

        # 综合标签
        final_sentiment = self._combine(platform_sentiment, snownlp_sentiment)

        review["platform_sentiment"] = platform_sentiment
        review["snownlp_score"] = snownlp_score
        review["snownlp_sentiment"] = snownlp_sentiment
        review["final_sentiment"] = final_sentiment
        return review

    @staticmethod
    def _zol_rating_label(rating) -> Optional[str]:
        """
        根据 ZOL 评分确定平台标签。

        规则:
          - >= 4 分 -> 正面
          - 3 分 -> 中性
          - < 3 分 -> 负面
          - 无评分 -> None
        """
        if rating is None:
            return None
        try:
            r = float(rating)
        except (TypeError, ValueError):
            return None
        if r >= 4:
            return SENTIMENT_POSITIVE
        if r >= 3:
            return SENTIMENT_NEUTRAL
        return SENTIMENT_NEGATIVE

    # ---- 综合 ---------------------------------------------------------------

    @staticmethod
    def _combine(
        platform_label: Optional[str],
        snownlp_label: Optional[str],
    ) -> str:
        """
        综合平台标签和 SnowNLP 标签。

        优先级:
          1. 平台标签（评分/投票更可靠）
          2. SnowNLP 标签
          3. 默认中性
        """
        if platform_label:
            return platform_label
        if snownlp_label:
            return snownlp_label
        return SENTIMENT_NEUTRAL

    # ---- 批量分析 -----------------------------------------------------------

    def analyze_smzdm_batch(self, items: list[dict]) -> list[dict]:
        """批量分析 SMZDM 数据。"""
        return [self.analyze_smzdm_item(item) for item in items]

    def analyze_zol_batch(self, reviews: list[dict]) -> list[dict]:
        """批量分析 ZOL 数据。"""
        return [self.analyze_zol_review(rev) for rev in reviews]

    def analyze_all(
        self,
        smzdm_items: Optional[list[dict]] = None,
        zol_reviews: Optional[list[dict]] = None,
    ) -> dict:
        """
        分析全部数据并返回结构化结果。

        Returns:
            {
                "smzdm":  { "items": [...], "summary": {...} },
                "zol":    { "reviews": [...], "summary": {...} },
                "overall": { ... }
            }
        """
        result: dict = {}

        if smzdm_items:
            analyzed_smzdm = self.analyze_smzdm_batch(smzdm_items)
            result["smzdm"] = {
                "items": analyzed_smzdm,
                "summary": self._summarize(analyzed_smzdm),
            }

        if zol_reviews:
            analyzed_zol = self.analyze_zol_batch(zol_reviews)
            result["zol"] = {
                "reviews": analyzed_zol,
                "summary": self._summarize(analyzed_zol),
            }

        # 总体摘要
        all_items = []
        if smzdm_items:
            all_items.extend(result.get("smzdm", {}).get("items", []))
        if zol_reviews:
            all_items.extend(result.get("zol", {}).get("reviews", []))
        result["overall"] = self._summarize(all_items)
        return result

    # ---- 统计 ---------------------------------------------------------------

    @staticmethod
    def _summarize(items: list[dict]) -> dict:
        """统计正面/中性/负面比例。"""
        total = len(items)
        positive = sum(1 for i in items if i.get("final_sentiment") == SENTIMENT_POSITIVE)
        neutral = sum(1 for i in items if i.get("final_sentiment") == SENTIMENT_NEUTRAL)
        negative = sum(1 for i in items if i.get("final_sentiment") == SENTIMENT_NEGATIVE)

        # 平台评分覆盖
        has_platform = sum(1 for i in items if i.get("platform_sentiment"))
        has_snownlp = sum(1 for i in items if i.get("snownlp_sentiment"))

        return {
            "total": total,
            "positive": positive,
            "neutral": neutral,
            "negative": negative,
            "positive_ratio": round(positive / total, 4) if total > 0 else 0,
            "neutral_ratio": round(neutral / total, 4) if total > 0 else 0,
            "negative_ratio": round(negative / total, 4) if total > 0 else 0,
            "platform_coverage": has_platform,
            "snownlp_coverage": has_snownlp,
        }


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def analyze_smzdm(items: list[dict]) -> list[dict]:
    """便捷函数：分析 SMZDM 数据。"""
    return SentimentAnalyzer().analyze_smzdm_batch(items)


def analyze_zol(reviews: list[dict]) -> list[dict]:
    """便捷函数：分析 ZOL 数据。"""
    return SentimentAnalyzer().analyze_zol_batch(reviews)


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    # 简单测试
    analyzer = SentimentAnalyzer()

    test_smzdm = [
        {"title": "华为MatePad Pro 12.2英寸，性价比超高，值得入手！", "worthy_votes": 45, "unworthy_votes": 3},
        {"title": "MateBook Pro S 这价格有点贵，感觉不太值", "worthy_votes": 2, "unworthy_votes": 18},
        {"title": "华为MatePad日常使用还行", "worthy_votes": 5, "unworthy_votes": 5},
    ]
    test_zol = [
        {"content": "屏幕清晰，运行流畅，续航给力，非常满意！", "rating": 5},
        {"content": "一般般，没什么特别的亮点", "rating": 3},
        {"content": "发热严重，续航差，不建议购买", "rating": 2},
    ]

    print("=== SMZDM 分析 ===")
    for item in analyzer.analyze_smzdm_batch(test_smzdm):
        print(f"  {item['title'][:40]:40s} -> {item['final_sentiment']} "
              f"(平台: {item['platform_sentiment']}, NLP: {item.get('snownlp_sentiment')})")

    print("\n=== ZOL 分析 ===")
    for r in analyzer.analyze_zol_batch(test_zol):
        print(f"  [{r['rating']}星] {r['content'][:40]:40s} -> {r['final_sentiment']} "
              f"(平台: {r['platform_sentiment']}, NLP: {r.get('snownlp_sentiment')})")

    print("\n=== 综合统计 ===")
    result = analyzer.analyze_all(test_smzdm, test_zol)
    print(json.dumps(result["overall"], ensure_ascii=False, indent=2))