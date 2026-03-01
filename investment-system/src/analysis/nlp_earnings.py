"""決算 NLP 分析モジュール (Claude API).

Claude API を使って決算短信・有価証券報告書のテキストを解析し、
センチメント・業績評価・投資示唆を生成する。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from decimal import Decimal

import anthropic

from ..config import get_settings
from .models import EarningsAnalysisResult, SentimentLabel

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """あなたは日本株投資のエキスパートアナリストです。
提供された決算情報を分析し、投資家にとって重要な洞察を簡潔かつ正確に提供してください。
分析は客観的なファンダメンタル分析に基づき、センチメント・業績評価・リスク・投資示唆を含めてください。
出力は必ず JSON 形式で返してください。"""

ANALYSIS_PROMPT_TEMPLATE = """以下の決算情報を分析してください。

## 企業情報
- ティッカー: {ticker}
- 会社名: {company_name}
- 決算期末: {period_end}

## 決算テキスト
{earnings_text}

## 分析項目
以下の JSON 形式で分析結果を返してください:

```json
{{
  "summary": "決算内容の要約（200字以内）",
  "sentiment": "strong_positive|positive|neutral|negative|strong_negative",
  "sentiment_score": -1.0から1.0の数値,
  "revenue_assessment": "売上高に関する評価コメント",
  "profit_assessment": "利益に関する評価コメント",
  "guidance_assessment": "ガイダンス・業績見通しの評価",
  "key_positives": ["ポジティブ要因1", "ポジティブ要因2"],
  "key_risks": ["リスク要因1", "リスク要因2"],
  "investment_implication": "投資家への示唆",
  "action_suggestion": "推奨アクション（例: 注目継続、様子見、利益確定検討）",
  "confidence": 0.0から1.0の確信度
}}
```"""


class EarningsNLPAnalyzer:
    """Claude API を使った決算 NLP アナリスト."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self._client = anthropic.Anthropic(api_key=api_key or settings.anthropic_api_key)
        self._model = model or settings.claude_model

    # ------------------------------------------------------------------
    # メイン分析
    # ------------------------------------------------------------------

    def analyze_earnings(
        self,
        ticker: str,
        company_name: str,
        earnings_text: str,
        period_end: date | None = None,
        source_doc_id: str | None = None,
    ) -> EarningsAnalysisResult:
        """決算テキストを分析して構造化された結果を返す。

        Args:
            ticker: ティッカーシンボル
            company_name: 会社名
            earnings_text: 分析対象の決算テキスト
            period_end: 決算期末日
            source_doc_id: ソース書類 ID

        Returns:
            EarningsAnalysisResult
        """
        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            ticker=ticker,
            company_name=company_name,
            period_end=period_end.isoformat() if period_end else "不明",
            earnings_text=earnings_text[:8000],  # トークン制限考慮
        )

        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text
            result_data = self._parse_response(raw)
        except anthropic.APIError as e:
            logger.error("Claude API エラー: ticker=%s, error=%s", ticker, e)
            result_data = self._fallback_result()

        return EarningsAnalysisResult(
            ticker=ticker,
            company_name=company_name,
            analysis_date=datetime.utcnow(),
            period_end=period_end,
            source_doc_id=source_doc_id,
            model_used=self._model,
            **result_data,
        )

    def batch_analyze(
        self,
        items: list[dict],
    ) -> list[EarningsAnalysisResult]:
        """複数銘柄を一括分析する。

        Args:
            items: [{"ticker": ..., "company_name": ..., "text": ..., ...}, ...]

        Returns:
            EarningsAnalysisResult のリスト
        """
        results: list[EarningsAnalysisResult] = []
        for item in items:
            result = self.analyze_earnings(
                ticker=item["ticker"],
                company_name=item.get("company_name", item["ticker"]),
                earnings_text=item["text"],
                period_end=item.get("period_end"),
                source_doc_id=item.get("doc_id"),
            )
            results.append(result)
            logger.info("分析完了: %s (sentiment=%s)", item["ticker"], result.sentiment)
        return results

    def generate_portfolio_comment(
        self,
        analyses: list[EarningsAnalysisResult],
    ) -> str:
        """複数の決算分析結果からポートフォリオ全体のコメントを生成する。"""
        summaries = "\n".join(
            f"- {r.ticker} ({r.company_name}): {r.summary} [センチメント: {r.sentiment.value}]"
            for r in analyses
        )
        prompt = f"""以下の決算分析結果を踏まえ、ポートフォリオ全体への影響と市場動向についてコメントしてください（400字以内）。

{summaries}"""

        try:
            message = self._client.messages.create(
                model=self._model,
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception as e:
            logger.error("ポートフォリオコメント生成失敗: %s", e)
            return "コメント生成に失敗しました。"

    # ------------------------------------------------------------------
    # 内部ユーティリティ
    # ------------------------------------------------------------------

    def _parse_response(self, raw: str) -> dict:
        """Claude のレスポンスから JSON を抽出してパースする。"""
        # ```json ... ``` ブロックを抽出
        import re
        match = re.search(r"```json\s*([\s\S]+?)\s*```", raw)
        json_str = match.group(1) if match else raw

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("JSON パース失敗、フォールバック使用")
            return self._fallback_result()

        # バリデーション・型変換
        sentiment_raw = data.get("sentiment", "neutral")
        try:
            sentiment = SentimentLabel(sentiment_raw)
        except ValueError:
            sentiment = SentimentLabel.NEUTRAL

        return {
            "summary": str(data.get("summary", ""))[:500],
            "sentiment": sentiment,
            "sentiment_score": Decimal(str(data.get("sentiment_score", 0.0))),
            "revenue_assessment": str(data.get("revenue_assessment", "")),
            "profit_assessment": str(data.get("profit_assessment", "")),
            "guidance_assessment": str(data.get("guidance_assessment", "")),
            "key_positives": [str(p) for p in data.get("key_positives", [])],
            "key_risks": [str(r) for r in data.get("key_risks", [])],
            "investment_implication": str(data.get("investment_implication", "")),
            "action_suggestion": str(data.get("action_suggestion", "")),
            "confidence": Decimal(str(data.get("confidence", 0.5))),
        }

    @staticmethod
    def _fallback_result() -> dict:
        """API エラー時のフォールバック結果."""
        return {
            "summary": "分析に失敗しました。",
            "sentiment": SentimentLabel.NEUTRAL,
            "sentiment_score": Decimal("0"),
            "revenue_assessment": "",
            "profit_assessment": "",
            "guidance_assessment": "",
            "key_positives": [],
            "key_risks": [],
            "investment_implication": "",
            "action_suggestion": "",
            "confidence": Decimal("0"),
        }
