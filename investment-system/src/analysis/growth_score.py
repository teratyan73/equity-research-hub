"""グロース株スコアリングモジュール.

売上成長率・利益率・ROE・モメンタム・バリュエーションを
加重合成して 0〜100 のグロースポテンシャルスコアを算出する。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

from .models import GrowthScore

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------
# スコアリングウェイト（合計 = 1.0）
# -----------------------------------------------------------------------
WEIGHTS = {
    "revenue_growth": 0.30,    # 売上成長性
    "profit_growth": 0.20,     # 利益成長性
    "roe": 0.15,               # 資本効率
    "momentum": 0.20,          # 株価モメンタム
    "earnings_quality": 0.10,  # 利益の質（安定性）
    "valuation": 0.05,         # バリュエーション（割安ボーナス）
}


class GrowthScorer:
    """グロース株スコアリングエンジン."""

    # ------------------------------------------------------------------
    # メインスコアリング
    # ------------------------------------------------------------------

    def score(
        self,
        ticker: str,
        company_name: str,
        price_df: pd.DataFrame,
        financial_data: dict | None = None,
    ) -> GrowthScore:
        """単一銘柄のグロースポテンシャルスコアを算出する。

        Args:
            ticker: ティッカーシンボル
            company_name: 会社名
            price_df: OHLCV DataFrame（列: open/high/low/close/volume）
            financial_data: 財務データの辞書（revenue_growth_3y, roe, per, pbr 等）

        Returns:
            GrowthScore
        """
        fd = financial_data or {}

        # 各サブスコアを計算（0〜100）
        rev_score = self._score_revenue_growth(fd.get("revenue_growth_3y"))
        prof_score = self._score_profit_growth(fd.get("op_income_growth_3y"))
        roe_score = self._score_roe(fd.get("roe"))
        mom_score = self._score_momentum(price_df)
        quality_score = self._score_earnings_quality(price_df, fd.get("op_margin"))
        val_score = self._score_valuation(fd.get("per"), fd.get("pbr"))

        total = (
            rev_score * WEIGHTS["revenue_growth"]
            + prof_score * WEIGHTS["profit_growth"]
            + roe_score * WEIGHTS["roe"]
            + mom_score * WEIGHTS["momentum"]
            + quality_score * WEIGHTS["earnings_quality"]
            + val_score * WEIGHTS["valuation"]
        )

        # モメンタム計算（参照用）
        mom_1m = self._calc_momentum(price_df, 21)
        mom_3m = self._calc_momentum(price_df, 63)
        mom_12m = self._calc_momentum(price_df, 252)

        return GrowthScore(
            ticker=ticker,
            company_name=company_name,
            revenue_growth_score=Decimal(str(round(rev_score, 1))),
            profit_growth_score=Decimal(str(round(prof_score, 1))),
            roe_score=Decimal(str(round(roe_score, 1))),
            momentum_score=Decimal(str(round(mom_score, 1))),
            earnings_quality_score=Decimal(str(round(quality_score, 1))),
            valuation_score=Decimal(str(round(val_score, 1))),
            total_score=Decimal(str(round(total, 1))),
            revenue_growth_3y=self._to_decimal(fd.get("revenue_growth_3y")),
            op_margin=self._to_decimal(fd.get("op_margin")),
            roe=self._to_decimal(fd.get("roe")),
            per=self._to_decimal(fd.get("per")),
            pbr=self._to_decimal(fd.get("pbr")),
            momentum_1m=self._to_decimal(mom_1m),
            momentum_3m=self._to_decimal(mom_3m),
            momentum_12m=self._to_decimal(mom_12m),
        )

    def score_universe(
        self,
        universe: list[dict],
    ) -> list[GrowthScore]:
        """銘柄ユニバース全体をスコアリングして順位付けする。

        Args:
            universe: [{"ticker": ..., "company_name": ..., "price_df": ..., "financial_data": ...}, ...]

        Returns:
            GrowthScore のリスト（スコア降順）
        """
        scores: list[GrowthScore] = []
        for item in universe:
            score = self.score(
                ticker=item["ticker"],
                company_name=item.get("company_name", item["ticker"]),
                price_df=item.get("price_df", pd.DataFrame()),
                financial_data=item.get("financial_data"),
            )
            scores.append(score)

        # スコア降順でソートして順位付け
        scores.sort(key=lambda s: float(s.total_score), reverse=True)
        for rank, score in enumerate(scores, 1):
            score.rank = rank

        logger.info("ユニバーススコアリング完了: %d 銘柄", len(scores))
        return scores

    # ------------------------------------------------------------------
    # サブスコア計算（0〜100）
    # ------------------------------------------------------------------

    def _score_revenue_growth(self, growth_3y: float | None) -> float:
        """売上高 3 年 CAGR からスコアを算出する。"""
        if growth_3y is None:
            return 50.0
        # CAGR 30%超=100点, 0%=50点, -10%以下=0点
        return float(np.clip((growth_3y + 10) / 40 * 100, 0, 100))

    def _score_profit_growth(self, growth_3y: float | None) -> float:
        """営業利益 3 年 CAGR からスコアを算出する。"""
        if growth_3y is None:
            return 50.0
        return float(np.clip((growth_3y + 10) / 40 * 100, 0, 100))

    def _score_roe(self, roe: float | None) -> float:
        """ROE からスコアを算出する。"""
        if roe is None:
            return 50.0
        # ROE 20%超=100点, 8%=50点, 0%以下=0点
        return float(np.clip(roe / 20 * 100, 0, 100))

    def _score_momentum(self, price_df: pd.DataFrame) -> float:
        """株価モメンタムスコアを算出する。

        1M・3M・12M モメンタムを加重平均する。
        12M では最新 1M を除く（リバーサル対策）。
        """
        if price_df.empty or "close" not in price_df.columns:
            return 50.0

        w1, w3, w12 = 0.3, 0.3, 0.4

        m1 = self._calc_momentum(price_df, 21)
        m3 = self._calc_momentum(price_df, 63)
        # 12M - 1M でリバーサル除去
        m12_raw = self._calc_momentum(price_df, 252)
        m12_adj = (m12_raw - m1) if (m12_raw is not None and m1 is not None) else m12_raw

        vals = [(w1, m1), (w3, m3), (w12, m12_adj)]
        valid = [(w, v) for w, v in vals if v is not None]
        if not valid:
            return 50.0

        total_w = sum(w for w, _ in valid)
        weighted = sum(w * v for w, v in valid) / total_w

        # 30% 上昇 = 100点, 0% = 50点, -30% 以下 = 0点
        return float(np.clip((weighted + 30) / 60 * 100, 0, 100))

    def _score_earnings_quality(
        self, price_df: pd.DataFrame, op_margin: float | None
    ) -> float:
        """利益の質スコアを算出する（価格安定性 + 利益率）。"""
        scores: list[float] = []

        # ボラティリティの逆数（安定 = 高スコア）
        if not price_df.empty and "close" in price_df.columns:
            log_ret = np.log(price_df["close"] / price_df["close"].shift(1)).dropna()
            vol = float(log_ret.std()) * np.sqrt(252) * 100
            vol_score = float(np.clip(100 - vol * 2, 0, 100))
            scores.append(vol_score)

        # 営業利益率
        if op_margin is not None:
            # 20%超=100点, 5%=50点, 0%以下=0点
            margin_score = float(np.clip(op_margin / 20 * 100, 0, 100))
            scores.append(margin_score)

        return float(np.mean(scores)) if scores else 50.0

    def _score_valuation(self, per: float | None, pbr: float | None) -> float:
        """バリュエーションスコアを算出する（割安 = 高スコア）。"""
        scores: list[float] = []

        if per is not None and per > 0:
            # PER 10倍=100点, 30倍=50点, 50倍以上=0点
            per_score = float(np.clip((50 - per) / 40 * 100, 0, 100))
            scores.append(per_score)

        if pbr is not None and pbr > 0:
            # PBR 1倍=100点, 3倍=50点, 5倍以上=0点
            pbr_score = float(np.clip((5 - pbr) / 4 * 100, 0, 100))
            scores.append(pbr_score)

        return float(np.mean(scores)) if scores else 50.0

    # ------------------------------------------------------------------
    # ユーティリティ
    # ------------------------------------------------------------------

    def _calc_momentum(self, price_df: pd.DataFrame, days: int) -> float | None:
        """N 日間のモメンタム（%）を計算する。"""
        if price_df.empty or "close" not in price_df.columns:
            return None
        if len(price_df) < days + 1:
            return None
        start = float(price_df["close"].iloc[-days])
        end = float(price_df["close"].iloc[-1])
        if start == 0:
            return None
        return (end / start - 1) * 100

    @staticmethod
    def _to_decimal(v: float | None) -> Decimal | None:
        if v is None:
            return None
        return Decimal(str(round(v, 2)))
