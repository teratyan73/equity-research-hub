"""benchmark.py のユニットテスト."""

import numpy as np
import pandas as pd
import pytest
from decimal import Decimal

from src.portfolio.benchmark import BenchmarkComparator, PerformanceStats


def make_price_series(n: int = 252, trend: float = 0.0005, seed: int = 42) -> pd.Series:
    np.random.seed(seed)
    prices = [100.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + trend + np.random.normal(0, 0.01)))
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.Series(prices, index=idx)


class TestBenchmarkComparator:
    def setup_method(self):
        self.comparator = BenchmarkComparator()

    def test_calc_stats_basic(self):
        prices = make_price_series()
        stats = self.comparator.calc_stats(prices, "TestPortfolio")
        assert stats.ticker_or_name == "TestPortfolio"
        assert isinstance(stats.total_return, Decimal)
        assert isinstance(stats.volatility, Decimal)
        assert float(stats.volatility) > 0

    def test_calc_stats_sharpe_positive_for_uptrend(self):
        prices = make_price_series(trend=0.002)  # 上昇トレンド
        stats = self.comparator.calc_stats(prices)
        assert float(stats.sharpe_ratio) > 0

    def test_compare_returns_both_stats(self):
        port = make_price_series(seed=1)
        bench = make_price_series(seed=2)
        result = self.comparator.compare(port, bench)
        assert "portfolio" in result
        assert "benchmark" in result

    def test_compare_calculates_beta_alpha(self):
        port = make_price_series(seed=1)
        bench = make_price_series(seed=2)
        result = self.comparator.compare(port, bench)
        ps = result["portfolio"]
        assert ps.beta is not None
        assert ps.alpha is not None

    def test_relative_strength_starts_at_100(self):
        port = make_price_series(seed=1)
        bench = make_price_series(seed=2)
        rs = self.comparator.relative_strength_index(port, bench)
        assert abs(float(rs.iloc[0]) - 100.0) < 0.01

    def test_max_drawdown_negative(self):
        prices = make_price_series()
        stats = self.comparator.calc_stats(prices)
        assert float(stats.max_drawdown) <= 0

    def test_compare_multiple(self):
        bench = make_price_series(seed=0)
        price_map = {
            "A.T": make_price_series(seed=1),
            "B.T": make_price_series(seed=2),
        }
        results = self.comparator.compare_multiple(price_map, bench)
        assert "A.T" in results
        assert "B.T" in results
        assert "TOPIX" in results

    def test_summary_table(self):
        bench = make_price_series(seed=0)
        price_map = {"A.T": make_price_series(seed=1)}
        results = self.comparator.compare_multiple(price_map, bench)
        df = self.comparator.summary_table(results)
        assert "トータルリターン(%)" in df.columns
        assert len(df) == 2  # A.T + TOPIX
