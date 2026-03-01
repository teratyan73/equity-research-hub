"""ベンチマーク比較モジュール.

TOPIX・日経225 等のインデックスに対するポートフォリオの
相対パフォーマンスを計算・可視化する。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PerformanceStats:
    """パフォーマンス統計量."""

    ticker_or_name: str
    total_return: Decimal       # 期間トータルリターン（%）
    annualized_return: Decimal  # 年率リターン（%）
    volatility: Decimal         # 年率ボラティリティ（%）
    sharpe_ratio: Decimal       # シャープレシオ
    max_drawdown: Decimal       # 最大ドローダウン（%）
    beta: Decimal | None = None        # ベンチマーク比ベータ
    alpha: Decimal | None = None       # ジェンセンのアルファ（年率%）
    information_ratio: Decimal | None = None  # 情報レシオ
    tracking_error: Decimal | None = None     # トラッキングエラー（%）
    active_return: Decimal | None = None      # アクティブリターン（%）


class BenchmarkComparator:
    """ポートフォリオとベンチマークのパフォーマンス比較器."""

    RISK_FREE_RATE = 0.001  # 無リスク金利（年率, 日本国債10年想定）

    def __init__(
        self,
        benchmark_ticker: str = "^TOPIX",
        risk_free_rate: float = RISK_FREE_RATE,
    ) -> None:
        self._benchmark_ticker = benchmark_ticker
        self._rfr = risk_free_rate

    # ------------------------------------------------------------------
    # パフォーマンス計算
    # ------------------------------------------------------------------

    def calc_stats(
        self,
        price_series: pd.Series,
        name: str = "Portfolio",
    ) -> PerformanceStats:
        """単一系列のパフォーマンス統計を計算する。

        Args:
            price_series: 日次価格系列（インデックス=datetime）
            name: 表示名

        Returns:
            PerformanceStats
        """
        ret = self._daily_returns(price_series)
        n_days = len(price_series)
        n_years = n_days / 252

        total_ret = float((price_series.iloc[-1] / price_series.iloc[0] - 1) * 100)
        ann_ret = float(((1 + total_ret / 100) ** (1 / max(n_years, 0.001)) - 1) * 100)
        vol = float(ret.std() * np.sqrt(252) * 100)
        sharpe = (ann_ret / 100 - self._rfr) / (vol / 100) if vol > 0 else 0.0
        mdd = float(self._max_drawdown(price_series))

        return PerformanceStats(
            ticker_or_name=name,
            total_return=Decimal(str(round(total_ret, 2))),
            annualized_return=Decimal(str(round(ann_ret, 2))),
            volatility=Decimal(str(round(vol, 2))),
            sharpe_ratio=Decimal(str(round(sharpe, 3))),
            max_drawdown=Decimal(str(round(mdd, 2))),
        )

    def compare(
        self,
        portfolio_prices: pd.Series,
        benchmark_prices: pd.Series,
        portfolio_name: str = "Portfolio",
        benchmark_name: str = "TOPIX",
    ) -> dict[str, PerformanceStats]:
        """ポートフォリオとベンチマークを比較する。

        Args:
            portfolio_prices: ポートフォリオ日次価格系列
            benchmark_prices: ベンチマーク日次価格系列
            portfolio_name: ポートフォリオ表示名
            benchmark_name: ベンチマーク表示名

        Returns:
            {"portfolio": PerformanceStats, "benchmark": PerformanceStats}
        """
        # 日付を揃える
        port, bench = portfolio_prices.align(benchmark_prices, join="inner")

        if len(port) < 5:
            logger.warning("比較データが不足しています: %d 日", len(port))

        port_stats = self.calc_stats(port, portfolio_name)
        bench_stats = self.calc_stats(bench, benchmark_name)

        # アクティブ分析
        port_ret = self._daily_returns(port)
        bench_ret = self._daily_returns(bench)

        active_ret = port_ret - bench_ret
        tracking_error = float(active_ret.std() * np.sqrt(252) * 100)
        active_return = float(active_ret.mean() * 252 * 100)
        ir = active_return / tracking_error if tracking_error > 0 else 0.0

        # ベータ・アルファ
        beta, alpha = self._calc_beta_alpha(port_ret, bench_ret)

        port_stats.beta = Decimal(str(round(beta, 3)))
        port_stats.alpha = Decimal(str(round(alpha, 2)))
        port_stats.information_ratio = Decimal(str(round(ir, 3)))
        port_stats.tracking_error = Decimal(str(round(tracking_error, 2)))
        port_stats.active_return = Decimal(str(round(active_return, 2)))

        return {"portfolio": port_stats, "benchmark": bench_stats}

    def compare_multiple(
        self,
        price_map: dict[str, pd.Series],
        benchmark_prices: pd.Series,
        benchmark_name: str = "TOPIX",
    ) -> dict[str, PerformanceStats]:
        """複数銘柄をベンチマークと比較する。

        Args:
            price_map: {ticker: price_series} の辞書
            benchmark_prices: ベンチマーク価格系列

        Returns:
            {ticker: PerformanceStats} の辞書
        """
        results: dict[str, PerformanceStats] = {
            benchmark_name: self.calc_stats(benchmark_prices, benchmark_name)
        }
        for name, prices in price_map.items():
            comparison = self.compare(prices, benchmark_prices, name, benchmark_name)
            results[name] = comparison["portfolio"]
        return results

    def relative_strength_index(
        self,
        portfolio_prices: pd.Series,
        benchmark_prices: pd.Series,
    ) -> pd.Series:
        """相対強度指数（ポートフォリオ / ベンチマーク）を返す。"""
        port, bench = portfolio_prices.align(benchmark_prices, join="inner")
        # 基準点を 100 に正規化
        port_norm = port / port.iloc[0] * 100
        bench_norm = bench / bench.iloc[0] * 100
        return port_norm / bench_norm * 100

    def summary_table(
        self,
        stats_map: dict[str, PerformanceStats],
    ) -> pd.DataFrame:
        """パフォーマンス統計をサマリー DataFrame に変換する。"""
        rows = []
        for name, s in stats_map.items():
            rows.append({
                "名称": name,
                "トータルリターン(%)": float(s.total_return),
                "年率リターン(%)": float(s.annualized_return),
                "ボラティリティ(%)": float(s.volatility),
                "シャープレシオ": float(s.sharpe_ratio),
                "最大DD(%)": float(s.max_drawdown),
                "ベータ": float(s.beta) if s.beta else None,
                "アルファ(%)": float(s.alpha) if s.alpha else None,
                "情報レシオ": float(s.information_ratio) if s.information_ratio else None,
            })
        return pd.DataFrame(rows).set_index("名称")

    # ------------------------------------------------------------------
    # 内部ユーティリティ
    # ------------------------------------------------------------------

    @staticmethod
    def _daily_returns(prices: pd.Series) -> pd.Series:
        return prices.pct_change().dropna()

    @staticmethod
    def _max_drawdown(prices: pd.Series) -> float:
        rolling_max = prices.cummax()
        drawdown = (prices - rolling_max) / rolling_max * 100
        return float(drawdown.min())

    def _calc_beta_alpha(
        self, port_ret: pd.Series, bench_ret: pd.Series
    ) -> tuple[float, float]:
        """ベータとジェンセンのアルファを計算する。"""
        aligned_port, aligned_bench = port_ret.align(bench_ret, join="inner")
        if len(aligned_port) < 10:
            return 1.0, 0.0

        cov_matrix = np.cov(aligned_port, aligned_bench)
        bench_var = cov_matrix[1, 1]
        beta = cov_matrix[0, 1] / bench_var if bench_var > 0 else 1.0

        ann_port = float(aligned_port.mean()) * 252
        ann_bench = float(aligned_bench.mean()) * 252
        alpha = (ann_port - self._rfr) - beta * (ann_bench - self._rfr)

        return float(beta), float(alpha * 100)
