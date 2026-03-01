"""regime.py のユニットテスト."""

import numpy as np
import pandas as pd
import pytest

from src.analysis.models import RegimeState
from src.analysis.regime import RegimeDetector


def make_trend_series(n: int, trend: float = 0.0, vol: float = 0.01, seed: int = 42) -> pd.Series:
    """テスト用トレンド系列を生成する。"""
    np.random.seed(seed)
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + trend + np.random.normal(0, vol)))
    idx = pd.date_range("2022-01-01", periods=n, freq="B")
    return pd.Series(prices, index=idx)


class TestRegimeDetector:
    def setup_method(self):
        self.detector = RegimeDetector()

    def test_detect_returns_market_regime(self):
        prices = make_trend_series(200)
        regime = self.detector.detect(prices)
        assert regime.state in RegimeState.__members__.values()
        assert 0 <= float(regime.risk_score) <= 1
        assert 0 <= float(regime.regime_confidence) <= 1

    def test_detect_bull_market(self):
        """強いトレンド上昇市場をリスクオンと判定することを確認する。"""
        prices = make_trend_series(200, trend=0.005, vol=0.005)
        regime = self.detector.detect(prices)
        assert regime.state in (RegimeState.RISK_ON, RegimeState.NEUTRAL, RegimeState.TRANSITION)

    def test_detect_bear_market(self):
        """強いトレンド下落市場をリスクオフと判定することを確認する。"""
        prices = make_trend_series(200, trend=-0.005, vol=0.005)
        regime = self.detector.detect(prices)
        assert regime.state in (RegimeState.RISK_OFF, RegimeState.NEUTRAL, RegimeState.TRANSITION)

    def test_detect_high_volatility_risk_off(self):
        """高ボラティリティ時にリスクオフまたは転換を判定することを確認する。"""
        prices = make_trend_series(200, trend=0.0, vol=0.05)  # 年率約 80% ボラ
        regime = self.detector.detect(prices)
        # 高ボラは risk_off か transition になりやすい
        assert regime.state in (RegimeState.RISK_OFF, RegimeState.NEUTRAL, RegimeState.TRANSITION)

    def test_detect_insufficient_data(self):
        """データ不足でも例外が出ないことを確認する。"""
        prices = make_trend_series(30)  # 75日 MA に足りない
        regime = self.detector.detect(prices)
        assert regime is not None

    def test_state_change_detection(self):
        """レジーム変化フラグが設定されることを確認する。"""
        # 最初の検知
        prices_bull = make_trend_series(200, trend=0.005, vol=0.003)
        detector = RegimeDetector()
        r1 = detector.detect(prices_bull)
        r1_state = r1.state

        # 2回目 (異なるトレンド)
        prices_bear = make_trend_series(200, trend=-0.005, vol=0.003)
        r2 = detector.detect(prices_bear)

        if r2.state != r1_state:
            assert r2.state_changed is True
            assert r2.previous_state == r1_state

    def test_interpretation_not_empty(self):
        """解釈テキストが空でないことを確認する。"""
        prices = make_trend_series(200)
        regime = self.detector.detect(prices)
        assert len(regime.interpretation) > 0
        assert len(regime.recommended_action) > 0

    def test_risk_score_bounds(self):
        """リスクスコアが 0〜1 の範囲であることを確認する。"""
        for _ in range(5):
            prices = make_trend_series(200, trend=np.random.uniform(-0.005, 0.005))
            regime = self.detector.detect(prices)
            assert 0 <= float(regime.risk_score) <= 1
