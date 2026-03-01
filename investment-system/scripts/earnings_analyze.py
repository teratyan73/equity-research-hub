#!/usr/bin/env python3
"""決算分析バッチスクリプト.

EDINET から決算書類を取得し、Claude API で NLP 分析を実施する。
結果を Slack に通知し、ローカルに JSON 保存する。

使い方:
    # 直近 7 日間の決算書類を一括分析
    uv run python scripts/earnings_analyze.py

    # 特定銘柄を指定
    uv run python scripts/earnings_analyze.py --ticker 7203.T

    # EDINET コードで指定
    uv run python scripts/earnings_analyze.py --edinet E02144
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import get_settings
from src.data.edinet import EdinetClient
from src.data.market import MarketDataClient
from src.analysis.nlp_earnings import EarningsNLPAnalyzer
from src.alert.slack_notify import SlackNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def analyze_recent_earnings(days: int = 7, min_confidence: float = 0.3) -> None:
    """直近 N 日間の決算書類を取得・分析する。"""
    settings = get_settings()
    settings.ensure_dirs()

    edinet_client = EdinetClient()
    market_client = MarketDataClient()
    analyzer = EarningsNLPAnalyzer()
    notifier = SlackNotifier()

    logger.info("直近 %d 日間の決算書類を取得中...", days)
    earnings_docs = edinet_client.fetch_recent_earnings(days=days)

    if not earnings_docs:
        logger.info("分析対象の決算書類が見つかりませんでした。")
        return

    logger.info("分析対象: %d 件", len(earnings_docs))
    results = []

    for doc in earnings_docs:
        logger.info("分析中: %s (%s)", doc.company_name, doc.doc_id)

        # PDF 取得
        content = edinet_client.download_document(
            doc.doc_id,
            doc_type=2,  # PDF
        )

        # テキスト抽出（簡易: PDF バイナリを直接渡す場合は要 pdfminer 等）
        # ここでは doc_description をフォールバックとして使用
        earnings_text = doc.doc_description or f"{doc.company_name} の決算書類"
        if content:
            try:
                earnings_text = content.decode("utf-8", errors="ignore")[:10000]
            except Exception:
                pass

        # 証券コードから ticker を構築
        ticker = (
            market_client.to_yahoo_ticker(doc.securities_code)
            if doc.securities_code
            else doc.edinet_code
        )

        result = analyzer.analyze_earnings(
            ticker=ticker,
            company_name=doc.company_name,
            earnings_text=earnings_text,
            period_end=doc.period_end,
            source_doc_id=doc.doc_id,
        )

        if float(result.confidence) >= min_confidence:
            results.append(result)
            notifier.send_earnings_alert(
                result,
                mention_channel=(result.sentiment.value in ("strong_positive", "strong_negative")),
            )
            logger.info(
                "分析完了: %s -> %s (confidence=%.2f)",
                doc.company_name,
                result.sentiment.value,
                float(result.confidence),
            )
        else:
            logger.info("確信度低のためスキップ: %s (%.2f)", doc.company_name, float(result.confidence))

    # JSON 保存
    if results:
        output_path = settings.reports_dir / f"earnings_{date.today().isoformat()}.json"
        data = [r.model_dump(mode="json") for r in results]
        output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        logger.info("分析結果を保存: %s (%d 件)", output_path, len(results))

        # ポートフォリオサマリーコメント
        if len(results) > 1:
            summary = analyzer.generate_portfolio_comment(results)
            notifier.send_text(f":memo: *決算分析サマリー ({date.today().isoformat()})*\n{summary}")


def analyze_single_ticker(ticker: str) -> None:
    """特定銘柄の最新決算を分析する。"""
    settings = get_settings()
    settings.ensure_dirs()

    market_client = MarketDataClient()
    edinet_client = EdinetClient()
    analyzer = EarningsNLPAnalyzer()
    notifier = SlackNotifier()

    info = market_client.get_stock_info(ticker)
    company_name = info.name if info else ticker

    logger.info("分析対象: %s (%s)", company_name, ticker)

    # 直近 30 日で EDINET 書類を検索
    docs = edinet_client.fetch_recent_earnings(days=30)

    # 証券コードでフィルタ（ティッカーの数字部分）
    sec_code = ticker.replace(".T", "")
    target_docs = [d for d in docs if d.securities_code == sec_code]

    if not target_docs:
        logger.warning("直近 30 日に決算書類が見つかりませんでした: %s", ticker)
        # フォールバック: 財務データを yfinance から取得してテキスト生成
        financials = market_client.get_financial_summary(ticker)
        if financials:
            latest = financials[0]
            earnings_text = (
                f"{company_name} の財務データ\n"
                f"売上高: {latest.revenue} 百万円\n"
                f"営業利益: {latest.operating_income} 百万円\n"
                f"当期純利益: {latest.net_income} 百万円\n"
            )
            result = analyzer.analyze_earnings(
                ticker=ticker,
                company_name=company_name,
                earnings_text=earnings_text,
                period_end=latest.period_end,
            )
            notifier.send_earnings_alert(result)
            logger.info("分析完了: %s -> %s", ticker, result.sentiment.value)
        return

    # 最新の書類で分析
    doc = target_docs[0]
    content = edinet_client.download_document(doc.doc_id, doc_type=2)
    earnings_text = content.decode("utf-8", errors="ignore")[:10000] if content else doc.doc_description or ""

    result = analyzer.analyze_earnings(
        ticker=ticker,
        company_name=company_name,
        earnings_text=earnings_text,
        period_end=doc.period_end,
        source_doc_id=doc.doc_id,
    )

    # 結果表示
    print(f"\n{'=' * 60}")
    print(f"分析結果: {company_name} ({ticker})")
    print(f"{'=' * 60}")
    print(f"センチメント: {result.sentiment.value}")
    print(f"スコア: {result.sentiment_score}")
    print(f"要約: {result.summary}")
    print(f"アクション: {result.action_suggestion}")
    print(f"{'=' * 60}\n")

    notifier.send_earnings_alert(result)

    output_path = settings.reports_dir / f"earnings_{ticker}_{date.today().isoformat()}.json"
    output_path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
    )
    logger.info("結果保存: %s", output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="決算分析バッチスクリプト")
    parser.add_argument("--ticker", help="分析対象ティッカー (例: 7203.T)")
    parser.add_argument("--edinet", help="EDINET コード (例: E02144)")
    parser.add_argument("--days", type=int, default=7, help="直近 N 日間を対象 (デフォルト: 7)")
    parser.add_argument(
        "--min-confidence", type=float, default=0.3, help="最小確信度閾値 (デフォルト: 0.3)"
    )
    args = parser.parse_args()

    if args.ticker:
        analyze_single_ticker(args.ticker)
    else:
        analyze_recent_earnings(days=args.days, min_confidence=args.min_confidence)


if __name__ == "__main__":
    main()
