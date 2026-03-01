"""screener.py のユニットテスト."""

from decimal import Decimal

import pytest

from src.analysis.models import GrowthScore
from src.data.models import StockInfo
from src.portfolio.screener import ScreenerCriteria, StockScreener


def make_score(ticker: str, total: float, momentum: float = 60.0, rev_growth: float = 60.0) -> GrowthScore:
    return GrowthScore(
        ticker=ticker,
        company_name=f"Co_{ticker}",
        total_score=Decimal(str(total)),
        momentum_score=Decimal(str(momentum)),
        revenue_growth_score=Decimal(str(rev_growth)),
        profit_growth_score=Decimal("50"),
        roe_score=Decimal("50"),
        earnings_quality_score=Decimal("50"),
        valuation_score=Decimal("50"),
    )


def make_info(ticker: str, sector: str = "IT", market_cap: float = 500.0) -> StockInfo:
    return StockInfo(
        ticker=ticker,
        name=f"Company {ticker}",
        sector=sector,
        market_cap=Decimal(str(market_cap)),
    )


class TestStockScreener:
    def test_screen_filters_by_min_score(self):
        scores = [make_score("A.T", 80), make_score("B.T", 50), make_score("C.T", 70)]
        screener = StockScreener(ScreenerCriteria(min_total_score=65.0))
        results = screener.screen(scores)
        tickers = [r.ticker for r in results]
        assert "A.T" in tickers
        assert "C.T" in tickers
        assert "B.T" not in tickers

    def test_screen_sorted_by_score_descending(self):
        scores = [make_score("A.T", 70), make_score("B.T", 90), make_score("C.T", 80)]
        screener = StockScreener(ScreenerCriteria(min_total_score=0.0))
        results = screener.screen(scores)
        assert results[0].ticker == "B.T"
        assert results[1].ticker == "C.T"

    def test_screen_top_n_limit(self):
        scores = [make_score(f"TK{i}.T", 70 + i) for i in range(10)]
        screener = StockScreener(ScreenerCriteria(min_total_score=0.0, top_n=3))
        results = screener.screen(scores)
        assert len(results) == 3

    def test_screen_with_sector_filter(self):
        scores = [make_score("A.T", 80), make_score("B.T", 80)]
        info_map = {
            "A.T": make_info("A.T", sector="IT"),
            "B.T": make_info("B.T", sector="Finance"),
        }
        screener = StockScreener(ScreenerCriteria(min_total_score=0.0, sectors=["IT"]))
        results = screener.screen(scores, info_map)
        assert len(results) == 1
        assert results[0].ticker == "A.T"

    def test_screen_with_exclude_sector(self):
        scores = [make_score("A.T", 80), make_score("B.T", 80)]
        info_map = {
            "A.T": make_info("A.T", sector="IT"),
            "B.T": make_info("B.T", sector="Finance"),
        }
        screener = StockScreener(ScreenerCriteria(min_total_score=0.0, exclude_sectors=["Finance"]))
        results = screener.screen(scores, info_map)
        tickers = [r.ticker for r in results]
        assert "A.T" in tickers
        assert "B.T" not in tickers

    def test_growth_preset(self):
        screener = StockScreener.growth_preset()
        assert screener.criteria.min_total_score == 65.0
        assert screener.criteria.top_n == 20

    def test_quality_growth_preset(self):
        screener = StockScreener.quality_growth_preset()
        assert screener.criteria.min_total_score == 70.0
        assert screener.criteria.top_n == 15

    def test_screen_by_sector(self):
        scores = [
            make_score("A.T", 80),
            make_score("B.T", 75),
            make_score("C.T", 70),
            make_score("D.T", 85),
        ]
        info_map = {
            "A.T": make_info("A.T", "IT"),
            "B.T": make_info("B.T", "Finance"),
            "C.T": make_info("C.T", "IT"),
            "D.T": make_info("D.T", "Finance"),
        }
        screener = StockScreener(ScreenerCriteria(min_total_score=0.0))
        sector_results = screener.screen_by_sector(scores, info_map, top_n_per_sector=1)
        assert "IT" in sector_results
        assert "Finance" in sector_results
        assert len(sector_results["IT"]) == 1
