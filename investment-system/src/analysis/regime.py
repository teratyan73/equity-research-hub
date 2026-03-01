"""市場レジーム検知モジュール.

移動平均・ボラティリティ・騰落比率などの統計指標を組み合わせて
市場のリスクオン/オフ局面を判定する。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

from .models import MarketRegime, RegimeState

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# 定数
# -----------------------------------------------------------------------
SHORT_MA = 25      # 短期移動平均（日）
LONG_MA = 75       # 長期移動平均（日）
VOLATILITY_WINDOW = 20  # ボラティリティ計算ウィンドウ（日）
HIGH_VOL_THRESHOLD = 20.0   # 高ボラティリティ閾値（年率%）
LOW_VOL_THRESHOLD = 12.0    # 低ボラティリティ閾値（年率%）


class RegimeDetector:
    """移動平均・ボラティリティベースの市場レジーム検知器."""

    def __init__(
        self,
        short_ma: int = SHORT_MA,
        long_ma: int = LONG_MA,
        vol_window: int = VOLATILITY_WINDOW,
    ) -> None:
        self._short_ma = short_ma
        self._long_ma = long_ma
        self._vol_window = vol_window
        self._history: list[MarketRegime] = []

    # ------------------------------------------------------------------
    # メイン検知
    # ------------------------------------------------------------------

    def detect(
        self,
        price_series: pd.Series,
        reference_date: date | None = None,
    ) -> MarketRegime:
        """価格系列からレジームを検知する。

        Args:
            price_series: 日次終値の pandas Series（インデックス=日付）
            reference_date: 基準日（None の場合は系列の最終日）

        Returns:
            MarketRegime
        """
        if len(price_series) < self._long_ma:
            logger.warning(
                "データが短すぎます: %d日 (必要: %d日)", len(price_series), self._long_ma
            )

        ref_date = reference_date or price_series.index[-1].date()

        # 各シグナルを計算
        ma_signal = self._calc_ma_signal(price_series)
        volatility = self._calc_volatility(price_series)
        trend_pct = self._calc_trend(price_series, days=20)
        breadth = self._calc_breadth_proxy(price_series)

        # レジーム判定
        state = self._classify_regime(ma_signal, volatility, trend_pct)

        # 前回レジームとの比較
        previous_state = self._history[-1].state if self._history else None
        state_changed = previous_state is not None and previous_state != state
        days_in_state = self._count_days_in_state(state)

        risk_score = self._calc_risk_score(volatility, ma_signal, trend_pct)

        regime = MarketRegime(
            reference_date=ref_date,
            state=state,
            previous_state=previous_state,
            state_changed=state_changed,
            days_in_current_state=days_in_state,
            topix_trend=Decimal(str(round(trend_pct, 2))) if not np.isnan(trend_pct) else None,
            volatility_20d=Decimal(str(round(volatility, 2))) if not np.isnan(volatility) else None,
            breadth=Decimal(str(round(breadth, 2))) if not np.isnan(breadth) else None,
            moving_avg_signal=ma_signal,
            regime_confidence=Decimal(str(round(self._calc_confidence(ma_signal, volatility, trend_pct), 2))),
            risk_score=Decimal(str(round(risk_score, 2))),
            interpretation=self._generate_interpretation(state, volatility, trend_pct),
            recommended_action=self._generate_action(state),
        )

        self._history.append(regime)
        return regime

    def detect_from_market_client(
        self,
        market_client: object,
        index_ticker: str = "^TOPIX",
        period: str = "1y",
    ) -> MarketRegime:
        """MarketDataClient から直接インデックスデータを取得して検知する。"""
        df = market_client.get_ohlcv_df(index_ticker, period=period)  # type: ignore[attr-defined]
        if df.empty:
            logger.error("インデックスデータ取得失敗: %s", index_ticker)
            return MarketRegime(
                reference_date=date.today(),
                state=RegimeState.NEUTRAL,
                interpretation="データ取得失敗",
            )
        return self.detect(df["close"])

    # ------------------------------------------------------------------
    # シグナル計算
    # ------------------------------------------------------------------

    def _calc_ma_signal(self, prices: pd.Series) -> str:
        """移動平均クロスシグナルを計算する。"""
        if len(prices) < self._long_ma:
            return "neutral"
        short = prices.rolling(self._short_ma).mean().iloc[-1]
        long_ = prices.rolling(self._long_ma).mean().iloc[-1]
        if short > long_ * 1.02:
            return "bull"
        elif short < long_ * 0.98:
            return "bear"
        return "neutral"

    def _calc_volatility(self, prices: pd.Series) -> float:
        """年率換算ボラティリティ（%）を計算する。"""
        if len(prices) < self._vol_window + 1:
            return float("nan")
        log_ret = np.log(prices / prices.shift(1)).dropna()
        vol = float(log_ret.tail(self._vol_window).std()) * np.sqrt(252) * 100
        return vol

    def _calc_trend(self, prices: pd.Series, days: int = 20) -> float:
        """N 日間のトレンド（%）を計算する。"""
        if len(prices) < days + 1:
            return float("nan")
        start = float(prices.iloc[-days])
        end = float(prices.iloc[-1])
        if start == 0:
            return float("nan")
        return (end / start - 1) * 100

    def _calc_breadth_proxy(self, prices: pd.Series) -> float:
        """騰落比率のプロキシ（20日新高値/安値比）を計算する。"""
        if len(prices) < 20:
            return 50.0
        rolling_max = prices.rolling(20).max()
        rolling_min = prices.rolling(20).min()
        latest = float(prices.iloc[-1])
        max_val = float(rolling_max.iloc[-1])
        min_val = float(rolling_min.iloc[-1])
        if max_val == min_val:
            return 50.0
        breadth = (latest - min_val) / (max_val - min_val) * 100
        return float(breadth)

    # ------------------------------------------------------------------
    # レジーム分類
    # ------------------------------------------------------------------

    def _classify_regime(
        self, ma_signal: str, volatility: float, trend_pct: float
    ) -> RegimeState:
        """各シグナルを総合してレジームを分類する。"""
        scores = {
            RegimeState.RISK_ON: 0,
            RegimeState.RISK_OFF: 0,
            RegimeState.NEUTRAL: 0,
        }

        # 移動平均シグナル
        if ma_signal == "bull":
            scores[RegimeState.RISK_ON] += 2
        elif ma_signal == "bear":
            scores[RegimeState.RISK_OFF] += 2
        else:
            scores[RegimeState.NEUTRAL] += 1

        # ボラティリティ
        if not np.isnan(volatility):
            if volatility > HIGH_VOL_THRESHOLD:
                scores[RegimeState.RISK_OFF] += 2
            elif volatility < LOW_VOL_THRESHOLD:
                scores[RegimeState.RISK_ON] += 1
            else:
                scores[RegimeState.NEUTRAL] += 1

        # トレンド
        if not np.isnan(trend_pct):
            if trend_pct > 3:
                scores[RegimeState.RISK_ON] += 1
            elif trend_pct < -3:
                scores[RegimeState.RISK_OFF] += 1

        best = max(scores, key=lambda k: scores[k])
        max_score = scores[best]

        # スコアが拮抗している場合は TRANSITION
        sorted_scores = sorted(scores.values(), reverse=True)
        if len(sorted_scores) >= 2 and sorted_scores[0] - sorted_scores[1] <= 1:
            return RegimeState.TRANSITION

        return best

    def _calc_risk_score(
        self, volatility: float, ma_signal: str, trend_pct: float
    ) -> float:
        """リスクスコア (0=低リスク, 1=高リスク) を計算する。"""
        score = 0.5
        if not np.isnan(volatility):
            vol_score = min(volatility / 30.0, 1.0)
            score = score * 0.4 + vol_score * 0.4
        if ma_signal == "bull":
            score -= 0.1
        elif ma_signal == "bear":
            score += 0.1
        if not np.isnan(trend_pct):
            trend_adj = -trend_pct / 20.0
            score += max(-0.1, min(0.1, trend_adj))
        return max(0.0, min(1.0, score))

    def _calc_confidence(
        self, ma_signal: str, volatility: float, trend_pct: float
    ) -> float:
        """分析の確信度 (0〜1) を計算する。"""
        confidence = 0.5
        if ma_signal in ("bull", "bear"):
            confidence += 0.2
        if not np.isnan(volatility):
            if volatility > HIGH_VOL_THRESHOLD or volatility < LOW_VOL_THRESHOLD:
                confidence += 0.15
        if not np.isnan(trend_pct) and abs(trend_pct) > 5:
            confidence += 0.15
        return min(confidence, 1.0)

    def _count_days_in_state(self, current_state: RegimeState) -> int:
        """現在のレジームが継続している日数を返す。"""
        count = 0
        for regime in reversed(self._history):
            if regime.state == current_state:
                count += 1
            else:
                break
        return count

    # ------------------------------------------------------------------
    # 解釈生成
    # ------------------------------------------------------------------

    def _generate_interpretation(
        self, state: RegimeState, volatility: float, trend_pct: float
    ) -> str:
        """レジームの解釈テキストを生成する。"""
        base = {
            RegimeState.RISK_ON: "リスクオン局面",
            RegimeState.RISK_OFF: "リスクオフ局面",
            RegimeState.NEUTRAL: "中立局面",
            RegimeState.TRANSITION: "レジーム転換期",
        }[state]

        details = []
        if not np.isnan(volatility):
            details.append(f"ボラティリティ {volatility:.1f}%")
        if not np.isnan(trend_pct):
            direction = "上昇" if trend_pct > 0 else "下落"
            details.append(f"20日トレンド {direction}{abs(trend_pct):.1f}%")

        if details:
            return f"{base}（{', '.join(details)}）"
        return base

    def _generate_action(self, state: RegimeState) -> str:
        """レジームに応じた推奨アクションを返す。"""
        return {
            RegimeState.RISK_ON: "グロース株・小型株への積極的なエクスポージャーを維持",
            RegimeState.RISK_OFF: "ポジション縮小、ディフェンシブ銘柄へのシフトを検討",
            RegimeState.NEUTRAL: "選別的な銘柄選択、ポジションの分散維持",
            RegimeState.TRANSITION: "新規ポジション追加を抑制、既存ポジションのモニタリング強化",
        }[state]
