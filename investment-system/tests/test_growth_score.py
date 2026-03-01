"""growth_score.py のユニットテスト."""

import numpy as np
import pandas as pd
import pytest
from decimal import Decimal

from src.analysis.growth_score import GrowthScorer


def make_price_series(n: int = 300, trend: float = 0.001) -> pd.DataFrame:
    """テスト用の価格 DataFrame を生成する。"""
    np.random.seed(42)
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + trend + np.random.normal(0, 0.01)))
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame({"close": prices, "open": prices, "high": prices, "low": prices}, index=idx)


class TestGrowthScorer:
    def setup_method(self):
        self.scorer = GrowthScorer()

    def test_score_returns_growth_score(self):
        df = make_price_series()
        result = self.scorer.score("TEST.T", "Test Co", df)
        assert result.ticker == "TEST.T"
        assert result.company_name == "Test Co"
        assert 0 <= float(result.total_score) <= 100

    def test_score_with_financial_data(self):
        df = make_price_series()
        fd = {
            "revenue_growth_3y": 25.0,
            "op_income_growth_3y": 30.0,
            "roe": 18.0,
            "op_margin": 15.0,
            "per": 25.0,
            "pbr": 3.0,
        }
        result = self.scorer.score("TEST.T", "Test Co", df, financial_data=fd)
        assert float(result.total_score) > 50  # 良好な財務は高スコア

    def test_score_empty_dataframe(self):
        """空の DataFrame でもエラーにならないことを確認する。"""
        result = self.scorer.score("TEST.T", "Test Co", pd.DataFrame())
        assert float(result.total_score) == 50.0  # デフォルト値

    def test_score_universe_sorted(self):
        """ユニバーススコアリングがスコア降順であることを確認する。"""
        items = [
            {"ticker": f"TK{i}.T", "company_name": f"Co{i}", "price_df": make_price_series()}
            for i in range(5)
        ]
        scores = self.scorer.score_universe(items)
        for i in range(len(scores) - 1):
            assert float(scores[i].total_score) >= float(scores[i + 1].total_score)

    def test_score_universe_assigns_rank(self):
        """順位が 1 から始まることを確認する。"""
        items = [
            {"ticker": f"TK{i}.T", "company_name": f"Co{i}", "price_df": make_price_series()}
            for i in range(3)
        ]
        scores = self.scorer.score_universe(items)
        assert scores[0].rank == 1
        assert scores[-1].rank == len(scores)

    def test_score_revenue_growth_high(self):
        """高成長は高スコアになることを確認する。"""
        score = self.scorer._score_revenue_growth(30.0)
        assert score > 90

    def test_score_revenue_growth_negative(self):
        """マイナス成長は低スコアになることを確認する。"""
        score = self.scorer._score_revenue_growth(-20.0)
        assert score == 0.0

    def test_score_roe_high(self):
        """高 ROE は高スコアになることを確認する。"""
        score = self.scorer._score_roe(25.0)
        assert score == 100.0

    def test_momentum_calculated(self):
        """モメンタムが計算されることを確認する。"""
        df = make_price_series(300, trend=0.003)
        result = self.scorer.score("TEST.T", "Test", df)
        assert result.momentum_1m is not None or result.momentum_3m is not None
