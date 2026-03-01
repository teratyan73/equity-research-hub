#!/usr/bin/env python3
"""日次バッチ更新スクリプト.

毎営業日の朝に実行し、以下を処理する:
1. 株価データの最新化
2. TDnet 適時開示の取得
3. 市場レジームの判定
4. グロース株スコアの更新
5. 必要に応じて Slack アラート送信

使い方:
    uv run python scripts/daily_update.py
    uv run python scripts/daily_update.py --daemon   # スケジューラーモード
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

import schedule
import time

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import get_settings
from src.data.market import MarketDataClient, TOPIX_TICKER
from src.data.tdnet import TdnetClient
from src.analysis.regime import RegimeDetector
from src.analysis.growth_score import GrowthScorer
from src.alert.slack_notify import SlackNotifier

# ロギング設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# 監視ユニバース（証券コード -> Yahoo Finance ティッカー）
DEFAULT_UNIVERSE = [
    "7203.T",   # トヨタ自動車
    "6758.T",   # ソニーグループ
    "9984.T",   # ソフトバンクグループ
    "6861.T",   # キーエンス
    "4063.T",   # 信越化学工業
    "8306.T",   # 三菱UFJ FG
    "9432.T",   # 日本電信電話
    "4502.T",   # 武田薬品工業
    "7267.T",   # ホンダ
    "6954.T",   # ファナック
]


def run_daily_update(tickers: list[str] | None = None) -> None:
    """日次更新を実行する。"""
    settings = get_settings()
    settings.ensure_dirs()

    target_tickers = tickers or DEFAULT_UNIVERSE
    today = date.today()

    logger.info("=" * 60)
    logger.info("日次更新開始: %s", today.isoformat())
    logger.info("対象銘柄数: %d", len(target_tickers))
    logger.info("=" * 60)

    market_client = MarketDataClient()
    tdnet_client = TdnetClient()
    regime_detector = RegimeDetector()
    scorer = GrowthScorer()
    notifier = SlackNotifier()

    # ----------------------------------------------------------------
    # 1. 市場レジーム判定
    # ----------------------------------------------------------------
    logger.info("[1/4] 市場レジーム分析中...")
    topix_df = market_client.get_ohlcv_df(TOPIX_TICKER, period="1y")
    if not topix_df.empty and "close" in topix_df.columns:
        regime = regime_detector.detect(topix_df["close"])
        logger.info(
            "レジーム: %s (リスクスコア=%.2f, 確信度=%.2f)",
            regime.state.value,
            float(regime.risk_score),
            float(regime.regime_confidence),
        )
        if regime.state_changed:
            logger.warning("レジーム変化を検知: %s -> %s", regime.previous_state, regime.state)
            notifier.send_regime_alert(regime)
    else:
        logger.warning("TOPIX データ取得失敗")
        regime = None

    # ----------------------------------------------------------------
    # 2. 株価データ更新 & スコアリング
    # ----------------------------------------------------------------
    logger.info("[2/4] 株価データ更新・スコアリング中...")
    scores = []
    for ticker in target_tickers:
        df = market_client.get_ohlcv_df(ticker, period="1y")
        if df.empty:
            logger.warning("株価データなし: %s", ticker)
            continue

        info = market_client.get_stock_info(ticker)
        score = scorer.score(
            ticker=ticker,
            company_name=info.name if info else ticker,
            price_df=df,
        )
        scores.append(score)
        logger.info("スコア: %s = %.1f", ticker, float(score.total_score))

    # ----------------------------------------------------------------
    # 3. TDnet 適時開示取得
    # ----------------------------------------------------------------
    logger.info("[3/4] 適時開示情報取得中...")
    disclosures = tdnet_client.fetch_disclosures()
    earnings_disclosures = [d for d in disclosures if d.is_earnings]
    logger.info("適時開示: %d 件 (うち決算 %d 件)", len(disclosures), len(earnings_disclosures))

    # ----------------------------------------------------------------
    # 4. サマリーを Slack に送信
    # ----------------------------------------------------------------
    logger.info("[4/4] サマリー送信中...")
    if scores:
        top_scores = sorted(scores, key=lambda s: float(s.total_score), reverse=True)[:5]
        summary_lines = [f"*本日の日次更新完了* ({today.isoformat()})"]

        if regime:
            summary_lines.append(f"市場レジーム: {regime.state.value.upper()} (リスク={regime.risk_score:.2f})")

        summary_lines.append("\n*グロース上位銘柄:*")
        for rank, s in enumerate(top_scores, 1):
            summary_lines.append(f"{rank}. {s.ticker} ({s.company_name}) - {s.total_score:.1f}点")

        if earnings_disclosures:
            summary_lines.append(f"\n本日の決算開示: {len(earnings_disclosures)} 件")

        notifier.send_text("\n".join(summary_lines))

    logger.info("日次更新完了")


def main() -> None:
    parser = argparse.ArgumentParser(description="日次バッチ更新スクリプト")
    parser.add_argument(
        "--daemon", action="store_true", help="スケジューラーモードで起動"
    )
    parser.add_argument(
        "--tickers", nargs="*", help="対象ティッカーを指定 (例: 7203.T 6758.T)"
    )
    args = parser.parse_args()

    tickers = args.tickers or None

    if args.daemon:
        settings = get_settings()
        update_time = settings.daily_update_time
        logger.info("スケジューラー起動: 毎日 %s JST に実行", update_time)
        schedule.every().day.at(update_time).do(run_daily_update, tickers=tickers)
        while True:
            schedule.run_pending()
            time.sleep(60)
    else:
        run_daily_update(tickers=tickers)


if __name__ == "__main__":
    main()
