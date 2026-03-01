"""データ層の Pydantic モデル定義."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class StockInfo(BaseModel):
    """銘柄基本情報."""

    ticker: str = Field(..., description="ティッカーシンボル (例: 7203.T)")
    name: str = Field(..., description="銘柄名")
    name_en: str | None = Field(None, description="英語銘柄名")
    sector: str | None = Field(None, description="セクター")
    industry: str | None = Field(None, description="業種")
    market_cap: Decimal | None = Field(None, description="時価総額（億円）")
    shares_outstanding: int | None = Field(None, description="発行済株式数")
    exchange: str = Field("TSE", description="上場取引所")
    edinet_code: str | None = Field(None, description="EDINET コード")
    securities_code: str | None = Field(None, description="証券コード")

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, v: str) -> str:
        return v.strip().upper()


class OHLCVData(BaseModel):
    """日次 OHLCV（始値・高値・安値・終値・出来高）データ."""

    ticker: str
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    adj_close: Decimal | None = None

    @field_validator("open", "high", "low", "close", mode="before")
    @classmethod
    def coerce_decimal(cls, v: object) -> Decimal:
        return Decimal(str(v))


class FinancialSummary(BaseModel):
    """財務サマリー（四半期・通期）."""

    ticker: str
    period_end: date = Field(..., description="会計期間末日")
    period_type: str = Field(..., description="Q1/Q2/Q3/FY")
    revenue: Decimal | None = Field(None, description="売上高（百万円）")
    operating_income: Decimal | None = Field(None, description="営業利益（百万円）")
    net_income: Decimal | None = Field(None, description="当期純利益（百万円）")
    eps: Decimal | None = Field(None, description="一株当たり利益（円）")
    roe: Decimal | None = Field(None, description="自己資本利益率（%）")
    revenue_yoy: Decimal | None = Field(None, description="売上高前年同期比（%）")
    op_income_yoy: Decimal | None = Field(None, description="営業利益前年同期比（%）")
    revision_flag: bool = Field(False, description="業績修正フラグ")
    fetched_at: datetime = Field(default_factory=datetime.utcnow)


class EdinetDocument(BaseModel):
    """EDINET 書類メタデータ."""

    doc_id: str = Field(..., description="書類管理番号")
    edinet_code: str = Field(..., description="EDINET コード")
    securities_code: str | None = None
    company_name: str
    doc_type_code: str = Field(..., description="書類種別コード")
    doc_type_name: str
    period_start: date | None = None
    period_end: date | None = None
    submit_datetime: datetime
    doc_description: str | None = None
    xbrl_flag: bool = False
    pdf_flag: bool = False
    csv_flag: bool = False


class DisclosureDocument(BaseModel):
    """TDnet 適時開示書類."""

    doc_id: str
    securities_code: str
    company_name: str
    title: str
    category: str = Field(..., description="開示カテゴリ")
    publish_datetime: datetime
    pdf_url: str | None = None
    summary: str | None = None
    is_earnings: bool = Field(False, description="決算関連フラグ")
    is_revision: bool = Field(False, description="業績修正フラグ")
