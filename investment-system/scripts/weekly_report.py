#!/usr/bin/env python3
"""週次レポート生成スクリプト.

毎週月曜日の朝に実行し、以下のレポートを生成・配信する:
- 週間パフォーマンスサマリー
- 市場レジームレポート
- グロース上位銘柄ランキング
- 注目決算スケジュール

使い方:
    uv run python scripts/weekly_report.py
    uv run python scripts/weekly_report.py --output-only  # ファイル出力のみ
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import get_settings
from src.data.market import MarketDataClient, TOPIX_TICKER, NIKKEI225_TICKER
from src.analysis.regime import RegimeDetector
from src.analysis.growth_score import GrowthScorer
from src.portfolio.benchmark import BenchmarkComparator
from src.portfolio.screener import StockScreener
from src.alert.slack_notify import SlackNotifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

UNIVERSE = [
    "7203.T", "6758.T", "9984.T", "6861.T", "4063.T",
    "8306.T", "9432.T", "4502.T", "7267.T", "6954.T",
    "4519.T", "6367.T", "8035.T", "2914.T", "9983.T",
]


def generate_weekly_report(output_only: bool = False) -> str:
    """週次レポートを生成する。

    Returns:
        レポートテキスト（Markdown 形式）
    """
    settings = get_settings()
    settings.ensure_dirs()

    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # 月曜
    week_end = week_start + timedelta(days=4)              # 金曜

    market_client = MarketDataClient()
    regime_detector = RegimeDetector()
    scorer = GrowthScorer()
    comparator = BenchmarkComparator()
    screener = StockScreener.growth_preset()
    notifier = SlackNotifier()

    report_lines = [
        f"# 週次投資レポート",
        f"**期間**: {week_start.isoformat()} ～ {week_end.isoformat()}",
        f"**生成日時**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
    ]

    # ----------------------------------------------------------------
    # 1. 市場環境サマリー
    # ----------------------------------------------------------------
    logger.info("市場環境分析中...")
    report_lines.append("## 1. 市場環境")

    topix_df = market_client.get_ohlcv_df(TOPIX_TICKER, period="3mo")
    nikkei_df = market_client.get_ohlcv_df(NIKKEI225_TICKER, period="3mo")

    if not topix_df.empty and "close" in topix_df.columns:
        # 週間・月間リターン
        topix_prices = topix_df["close"].dropna()
        week_ret = float(topix_prices.iloc[-1] / topix_prices.iloc[-6] - 1) * 100 if len(topix_prices) >= 6 else 0.0
        month_ret = float(topix_prices.iloc[-1] / topix_prices.iloc[-22] - 1) * 100 if len(topix_prices) >= 22 else 0.0

        report_lines.extend([
            f"- **TOPIX**: 週間 {week_ret:+.2f}%  /  月間 {month_ret:+.2f}%",
        ])

        # レジーム
        regime = regime_detector.detect(topix_df["close"])
        report_lines.extend([
            f"- **市場レジーム**: {regime.state.value.upper()} (リスクスコア={regime.risk_score:.2f})",
            f"- **解釈**: {regime.interpretation}",
            f"- **推奨スタンス**: {regime.recommended_action}",
            "",
        ])
    else:
        report_lines.append("- TOPIX データ取得失敗\n")
        regime = None

    if not nikkei_df.empty and "close" in nikkei_df.columns:
        n_prices = nikkei_df["close"].dropna()
        nikkei_week = float(n_prices.iloc[-1] / n_prices.iloc[-6] - 1) * 100 if len(n_prices) >= 6 else 0.0
        report_lines.append(f"- **日経225**: 週間 {nikkei_week:+.2f}%")
        report_lines.append("")

    # ----------------------------------------------------------------
    # 2. グロース株スコアランキング
    # ----------------------------------------------------------------
    logger.info("スコアリング中...")
    report_lines.append("## 2. グロース株スコアランキング (上位 10 銘柄)")

    score_list = []
    for ticker in UNIVERSE:
        df = market_client.get_ohlcv_df(ticker, period="1y")
        if df.empty:
            continue
        info = market_client.get_stock_info(ticker)
        score = scorer.score(
            ticker=ticker,
            company_name=info.name if info else ticker,
            price_df=df,
        )
        score_list.append(score)

    score_list.sort(key=lambda s: float(s.total_score), reverse=True)
    for rank, s in enumerate(score_list[:10], 1):
        report_lines.append(
            f"{rank}. **{s.ticker}** ({s.company_name}) - "
            f"総合={s.total_score:.1f} / "
            f"MOM={s.momentum_score:.1f} / "
            f"成長={s.revenue_growth_score:.1f}"
        )
    report_lines.append("")

    # ----------------------------------------------------------------
    # 3. ベンチマーク比較
    # ----------------------------------------------------------------
    logger.info("ベンチマーク比較中...")
    report_lines.append("## 3. ベンチマーク比較 (vs TOPIX, 直近 3ヶ月)")

    if not topix_df.empty and "close" in topix_df.columns:
        top5_tickers = [s.ticker for s in score_list[:5]]
        for ticker in top5_tickers:
            df = market_client.get_ohlcv_df(ticker, period="3mo")
            if df.empty or "close" not in df.columns:
                continue
            info = market_client.get_stock_info(ticker)
            comparison = comparator.compare(
                df["close"].dropna(),
                topix_df["close"].dropna(),
                ticker,
                "TOPIX",
            )
            ps = comparison["portfolio"]
            report_lines.append(
                f"- {ticker} ({info.name if info else ''}): "
                f"トータルリターン={ps.total_return}% / "
                f"シャープ={ps.sharpe_ratio} / "
                f"ベータ={ps.beta}"
            )
    report_lines.append("")

    # ----------------------------------------------------------------
    # 4. 今後の注目イベント
    # ----------------------------------------------------------------
    report_lines.append("## 4. 今後の注目事項")
    report_lines.extend([
        "- 決算シーズン: EDINET・TDnet で随時モニタリング",
        "- 日銀金融政策決定会合: 金利・為替動向に注意",
        "- 米国経済指標: CPI・雇用統計が円高リスクに影響",
        "",
        "---",
        "*本レポートは情報提供を目的としており、投資を推奨するものではありません。*",
    ])

    report_text = "\n".join(report_lines)

    # ----------------------------------------------------------------
    # ファイル出力
    # ----------------------------------------------------------------
    report_path = settings.reports_dir / f"weekly_{today.isoformat()}.md"
    report_path.write_text(report_text, encoding="utf-8")
    logger.info("週次レポート保存: %s", report_path)

    # ----------------------------------------------------------------
    # Slack 送信
    # ----------------------------------------------------------------
    if not output_only:
        top_picks = [s.ticker for s in score_list[:3]]
        # Slack 向け簡易版（Markdown の一部を Slack 記法に変換）
        slack_text = report_text.replace("**", "*").replace("## ", "*").replace("# ", "*")
        notifier.send_weekly_report(slack_text[:3000], top_picks=top_picks)

    return report_text


def main() -> None:
    parser = argparse.ArgumentParser(description="週次レポート生成スクリプト")
    parser.add_argument(
        "--output-only",
        action="store_true",
        help="ファイル出力のみ（Slack 送信しない）",
    )
    args = parser.parse_args()

    report = generate_weekly_report(output_only=args.output_only)
    print(report)


if __name__ == "__main__":
    main()
