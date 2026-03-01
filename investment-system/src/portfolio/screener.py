"""株式スクリーニングモジュール.

グロース株スコアや財務条件でユニバースを絞り込む。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from ..analysis.models import GrowthScore
from ..data.models import StockInfo

logger = logging.getLogger(__name__)


@dataclass
class ScreenerCriteria:
    """スクリーニング条件."""

    # 最小スコア
    min_total_score: float = 60.0
    min_momentum_score: float = 0.0
    min_revenue_growth_score: float = 0.0

    # 財務条件
    min_market_cap: float | None = None      # 億円
    max_per: float | None = None             # 倍
    max_pbr: float | None = None             # 倍
    min_roe: float | None = None             # %
    min_revenue_growth_3y: float | None = None  # %

    # ユニバース条件
    sectors: list[str] = field(default_factory=list)   # 含めるセクター（空=全セクター）
    exclude_sectors: list[str] = field(default_factory=list)  # 除外セクター
    top_n: int | None = None  # 上位 N 銘柄に絞る


@dataclass
class ScreenerResult:
    """スクリーニング結果."""

    score: GrowthScore
    stock_info: StockInfo | None = None
    passed_criteria: list[str] = field(default_factory=list)
    failed_criteria: list[str] = field(default_factory=list)

    @property
    def ticker(self) -> str:
        return self.score.ticker

    @property
    def total_score(self) -> Decimal:
        return self.score.total_score


class StockScreener:
    """グロース株スクリーナー."""

    def __init__(self, criteria: ScreenerCriteria | None = None) -> None:
        self._criteria = criteria or ScreenerCriteria()

    @property
    def criteria(self) -> ScreenerCriteria:
        return self._criteria

    @criteria.setter
    def criteria(self, value: ScreenerCriteria) -> None:
        self._criteria = value

    # ------------------------------------------------------------------
    # スクリーニング実行
    # ------------------------------------------------------------------

    def screen(
        self,
        scores: list[GrowthScore],
        stock_info_map: dict[str, StockInfo] | None = None,
    ) -> list[ScreenerResult]:
        """スコアリング済み銘柄にスクリーニング条件を適用する。

        Args:
            scores: GrowthScore のリスト
            stock_info_map: ticker -> StockInfo のマッピング（任意）

        Returns:
            通過した ScreenerResult のリスト（スコア降順）
        """
        info_map = stock_info_map or {}
        results: list[ScreenerResult] = []

        for score in scores:
            info = info_map.get(score.ticker)
            result = self._evaluate(score, info)
            if not result.failed_criteria:
                results.append(result)

        # スコア降順でソート
        results.sort(key=lambda r: float(r.total_score), reverse=True)

        # 上位 N 件に絞る
        if self._criteria.top_n is not None:
            results = results[: self._criteria.top_n]

        logger.info(
            "スクリーニング結果: %d / %d 銘柄通過", len(results), len(scores)
        )
        return results

    def screen_by_sector(
        self,
        scores: list[GrowthScore],
        stock_info_map: dict[str, StockInfo],
        top_n_per_sector: int = 3,
    ) -> dict[str, list[ScreenerResult]]:
        """セクター別にスクリーニング・順位付けする。"""
        all_results = self.screen(scores, stock_info_map)

        sector_map: dict[str, list[ScreenerResult]] = {}
        for result in all_results:
            info = stock_info_map.get(result.ticker)
            sector = (info.sector if info and info.sector else "その他")
            sector_map.setdefault(sector, []).append(result)

        # セクターごとに上位 N に絞る
        return {
            sector: results[:top_n_per_sector]
            for sector, results in sector_map.items()
        }

    # ------------------------------------------------------------------
    # 条件評価
    # ------------------------------------------------------------------

    def _evaluate(
        self,
        score: GrowthScore,
        info: StockInfo | None,
    ) -> ScreenerResult:
        """単一銘柄に対してスクリーニング条件を評価する。"""
        passed: list[str] = []
        failed: list[str] = []

        c = self._criteria

        # --- スコア条件 ---
        if float(score.total_score) >= c.min_total_score:
            passed.append(f"総合スコア >= {c.min_total_score}")
        else:
            failed.append(f"総合スコア {score.total_score} < {c.min_total_score}")

        if float(score.momentum_score) >= c.min_momentum_score:
            passed.append("モメンタムスコア OK")
        else:
            failed.append(f"モメンタムスコア {score.momentum_score} < {c.min_momentum_score}")

        if float(score.revenue_growth_score) >= c.min_revenue_growth_score:
            passed.append("売上成長スコア OK")
        else:
            failed.append(f"売上成長スコア不足")

        # --- 財務条件 ---
        if c.min_market_cap is not None and info and info.market_cap is not None:
            if float(info.market_cap) >= c.min_market_cap:
                passed.append("時価総額 OK")
            else:
                failed.append(f"時価総額 {info.market_cap}億円 < {c.min_market_cap}億円")

        if c.max_per is not None and score.per is not None:
            if float(score.per) <= c.max_per:
                passed.append("PER OK")
            else:
                failed.append(f"PER {score.per}倍 > {c.max_per}倍")

        if c.max_pbr is not None and score.pbr is not None:
            if float(score.pbr) <= c.max_pbr:
                passed.append("PBR OK")
            else:
                failed.append(f"PBR {score.pbr}倍 > {c.max_pbr}倍")

        if c.min_roe is not None and score.roe is not None:
            if float(score.roe) >= c.min_roe:
                passed.append("ROE OK")
            else:
                failed.append(f"ROE {score.roe}% < {c.min_roe}%")

        # --- セクター条件 ---
        if info and info.sector:
            if c.sectors and info.sector not in c.sectors:
                failed.append(f"対象外セクター: {info.sector}")
            if info.sector in c.exclude_sectors:
                failed.append(f"除外セクター: {info.sector}")

        return ScreenerResult(
            score=score,
            stock_info=info,
            passed_criteria=passed,
            failed_criteria=failed,
        )

    # ------------------------------------------------------------------
    # プリセット
    # ------------------------------------------------------------------

    @classmethod
    def growth_preset(cls) -> "StockScreener":
        """グロース重視プリセット."""
        return cls(
            ScreenerCriteria(
                min_total_score=65.0,
                min_momentum_score=50.0,
                min_revenue_growth_score=60.0,
                min_roe=10.0,
                top_n=20,
            )
        )

    @classmethod
    def quality_growth_preset(cls) -> "StockScreener":
        """クオリティグロース重視プリセット."""
        return cls(
            ScreenerCriteria(
                min_total_score=70.0,
                min_revenue_growth_score=65.0,
                min_roe=15.0,
                max_per=50.0,
                top_n=15,
            )
        )
