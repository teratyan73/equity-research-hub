"""分析結果の Pydantic モデル定義."""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class RegimeState(str, Enum):
    """市場レジーム状態."""
    RISK_ON = "risk_on"        # リスクオン
    RISK_OFF = "risk_off"      # リスクオフ
    NEUTRAL = "neutral"        # 中立
    TRANSITION = "transition"  # 転換期


class SentimentLabel(str, Enum):
    """センチメントラベル."""
    STRONG_POSITIVE = "strong_positive"
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    STRONG_NEGATIVE = "strong_negative"


class EarningsAnalysisResult(BaseModel):
    """決算 NLP 分析結果."""

    ticker: str
    company_name: str
    analysis_date: datetime = Field(default_factory=datetime.utcnow)
    period_end: date | None = None

    # サマリー
    summary: str = Field(..., description="決算内容の要約（日本語）")
    sentiment: SentimentLabel = Field(..., description="全体センチメント")
    sentiment_score: Decimal = Field(..., description="センチメントスコア -1.0〜1.0", ge=-1, le=1)

    # 業績評価
    revenue_assessment: str = Field("", description="売上高評価コメント")
    profit_assessment: str = Field("", description="利益評価コメント")
    guidance_assessment: str = Field("", description="ガイダンス評価コメント")

    # サプライズ判定
    earnings_surprise: Decimal | None = Field(None, description="EPSサプライズ率（%）")
    revenue_surprise: Decimal | None = Field(None, description="売上サプライズ率（%）")

    # リスク・注目点
    key_risks: list[str] = Field(default_factory=list, description="主要リスク")
    key_positives: list[str] = Field(default_factory=list, description="ポジティブ要因")

    # 投資示唆
    investment_implication: str = Field("", description="投資への示唆")
    action_suggestion: str = Field("", description="アクション提案")

    # メタ
    source_doc_id: str | None = None
    model_used: str = ""
    confidence: Decimal = Field(Decimal("0.5"), description="分析の確信度", ge=0, le=1)


class GrowthScore(BaseModel):
    """グロース株スコアリング結果."""

    ticker: str
    company_name: str
    scored_at: datetime = Field(default_factory=datetime.utcnow)

    # 個別スコア (0〜100)
    revenue_growth_score: Decimal = Field(Decimal("0"), ge=0, le=100)
    profit_growth_score: Decimal = Field(Decimal("0"), ge=0, le=100)
    roe_score: Decimal = Field(Decimal("0"), ge=0, le=100)
    momentum_score: Decimal = Field(Decimal("0"), ge=0, le=100)
    earnings_quality_score: Decimal = Field(Decimal("0"), ge=0, le=100)
    valuation_score: Decimal = Field(Decimal("0"), ge=0, le=100)

    # 総合スコア
    total_score: Decimal = Field(Decimal("0"), ge=0, le=100)
    rank: int | None = None  # ユニバース内順位

    # 財務指標（参考）
    revenue_growth_3y: Decimal | None = Field(None, description="売上高3年CAGR（%）")
    op_margin: Decimal | None = Field(None, description="営業利益率（%）")
    roe: Decimal | None = Field(None, description="ROE（%）")
    per: Decimal | None = Field(None, description="PER（倍）")
    pbr: Decimal | None = Field(None, description="PBR（倍）")
    momentum_1m: Decimal | None = Field(None, description="1ヶ月モメンタム（%）")
    momentum_3m: Decimal | None = Field(None, description="3ヶ月モメンタム（%）")
    momentum_12m: Decimal | None = Field(None, description="12ヶ月モメンタム（%）")


class MarketRegime(BaseModel):
    """市場レジーム分析結果."""

    detected_at: datetime = Field(default_factory=datetime.utcnow)
    reference_date: date

    state: RegimeState
    previous_state: RegimeState | None = None
    state_changed: bool = False
    days_in_current_state: int = 0

    # シグナル指標
    vix_level: Decimal | None = None
    topix_trend: Decimal | None = Field(None, description="TOPIX 20日トレンド（%）")
    breadth: Decimal | None = Field(None, description="騰落レシオ")
    volatility_20d: Decimal | None = Field(None, description="20日ボラティリティ（%）")
    moving_avg_signal: str = Field("", description="移動平均シグナル (bull/bear/neutral)")

    # スコア
    regime_confidence: Decimal = Field(Decimal("0.5"), ge=0, le=1)
    risk_score: Decimal = Field(
        Decimal("0.5"), ge=0, le=1, description="リスクスコア (0=低リスク, 1=高リスク)"
    )

    # 解釈
    interpretation: str = ""
    recommended_action: str = ""
