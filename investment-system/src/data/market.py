"""株価データ取得モジュール (yfinance ラッパー).

Yahoo Finance から日本株の OHLCV データ・銘柄情報を取得する。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import yfinance as yf

from .models import FinancialSummary, OHLCVData, StockInfo

logger = logging.getLogger(__name__)

# 東証主要インデックス
TOPIX_TICKER = "^TOPIX"
NIKKEI225_TICKER = "^N225"
MOTHERS_TICKER = "^TSEMOTHR"  # 東証グロース指数


class MarketDataClient:
    """Yahoo Finance (yfinance) を使った市場データ取得クライアント."""

    # ------------------------------------------------------------------
    # 銘柄情報
    # ------------------------------------------------------------------

    def get_stock_info(self, ticker: str) -> StockInfo | None:
        """銘柄基本情報を取得する。

        Args:
            ticker: ティッカーシンボル (例: "7203.T")

        Returns:
            StockInfo、取得失敗時は None
        """
        try:
            yf_ticker = yf.Ticker(ticker)
            info = yf_ticker.info
            if not info or info.get("quoteType") is None:
                logger.warning("銘柄情報なし: %s", ticker)
                return None

            market_cap_raw = info.get("marketCap")
            market_cap = (
                Decimal(str(market_cap_raw)) / Decimal("100000000")  # 億円換算
                if market_cap_raw
                else None
            )

            return StockInfo(
                ticker=ticker,
                name=info.get("longName") or info.get("shortName") or ticker,
                name_en=info.get("longName"),
                sector=info.get("sector"),
                industry=info.get("industry"),
                market_cap=market_cap,
                shares_outstanding=info.get("sharesOutstanding"),
                exchange=info.get("exchange", "TSE"),
            )
        except Exception as e:
            logger.error("銘柄情報取得失敗: %s, error=%s", ticker, e)
            return None

    def get_multiple_stock_info(self, tickers: list[str]) -> dict[str, StockInfo]:
        """複数銘柄の基本情報を一括取得する。"""
        results: dict[str, StockInfo] = {}
        for ticker in tickers:
            info = self.get_stock_info(ticker)
            if info:
                results[ticker] = info
        return results

    # ------------------------------------------------------------------
    # 株価データ
    # ------------------------------------------------------------------

    def get_ohlcv(
        self,
        ticker: str,
        start: date | None = None,
        end: date | None = None,
        period: str = "1y",
    ) -> list[OHLCVData]:
        """OHLCV データを取得する。

        Args:
            ticker: ティッカーシンボル
            start: 開始日 (None の場合は period を使用)
            end: 終了日
            period: yfinance period 文字列 (start 指定時は無視)

        Returns:
            OHLCVData のリスト（日付昇順）
        """
        try:
            yf_ticker = yf.Ticker(ticker)
            if start:
                df = yf_ticker.history(
                    start=start.isoformat(),
                    end=(end or date.today()).isoformat(),
                    auto_adjust=True,
                )
            else:
                df = yf_ticker.history(period=period, auto_adjust=True)

            if df.empty:
                logger.warning("OHLCV データなし: %s", ticker)
                return []

            records: list[OHLCVData] = []
            for idx, row in df.iterrows():
                record_date = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
                records.append(
                    OHLCVData(
                        ticker=ticker,
                        date=record_date,
                        open=Decimal(str(round(row["Open"], 2))),
                        high=Decimal(str(round(row["High"], 2))),
                        low=Decimal(str(round(row["Low"], 2))),
                        close=Decimal(str(round(row["Close"], 2))),
                        volume=int(row["Volume"]),
                        adj_close=Decimal(str(round(row["Close"], 2))),
                    )
                )
            return records
        except Exception as e:
            logger.error("OHLCV 取得失敗: %s, error=%s", ticker, e)
            return []

    def get_ohlcv_df(
        self,
        ticker: str,
        start: date | None = None,
        period: str = "1y",
    ) -> pd.DataFrame:
        """OHLCV データを DataFrame で返す（分析用）。"""
        try:
            yf_ticker = yf.Ticker(ticker)
            if start:
                df = yf_ticker.history(start=start.isoformat(), auto_adjust=True)
            else:
                df = yf_ticker.history(period=period, auto_adjust=True)
            df.index = pd.to_datetime(df.index).tz_localize(None)
            df.columns = [c.lower() for c in df.columns]
            return df
        except Exception as e:
            logger.error("OHLCV DataFrame 取得失敗: %s, error=%s", ticker, e)
            return pd.DataFrame()

    def get_multiple_ohlcv_df(
        self,
        tickers: list[str],
        period: str = "1y",
    ) -> pd.DataFrame:
        """複数銘柄の終値を DataFrame で返す（列=ティッカー）。"""
        try:
            data = yf.download(
                tickers,
                period=period,
                auto_adjust=True,
                progress=False,
            )
            if "Close" in data.columns:
                return data["Close"]
            return data
        except Exception as e:
            logger.error("複数 OHLCV 取得失敗: error=%s", e)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # インデックス
    # ------------------------------------------------------------------

    def get_index_ohlcv(
        self,
        index: str = TOPIX_TICKER,
        period: str = "1y",
    ) -> pd.DataFrame:
        """インデックス OHLCV を取得する。"""
        return self.get_ohlcv_df(index, period=period)

    # ------------------------------------------------------------------
    # 財務データ
    # ------------------------------------------------------------------

    def get_financial_summary(self, ticker: str) -> list[FinancialSummary]:
        """yfinance から財務サマリーを取得する。

        Note:
            yfinance の financials は年次・四半期データ。
            詳細な四半期データは EDINET/TDnet と組み合わせること。
        """
        try:
            yf_ticker = yf.Ticker(ticker)
            financials = yf_ticker.financials  # 年次

            results: list[FinancialSummary] = []
            if financials is None or financials.empty:
                return results

            for col in financials.columns:
                period_end = col.date() if hasattr(col, "date") else date.today()
                revenue_row = financials.loc["Total Revenue"] if "Total Revenue" in financials.index else None
                op_income_row = financials.loc["Operating Income"] if "Operating Income" in financials.index else None
                net_income_row = financials.loc["Net Income"] if "Net Income" in financials.index else None

                def safe_decimal(series: pd.Series | None, col: object) -> Decimal | None:
                    if series is None:
                        return None
                    try:
                        v = series[col]  # type: ignore[index]
                        if pd.isna(v):
                            return None
                        return Decimal(str(round(float(v) / 1_000_000, 1)))  # 百万円
                    except Exception:
                        return None

                results.append(
                    FinancialSummary(
                        ticker=ticker,
                        period_end=period_end,
                        period_type="FY",
                        revenue=safe_decimal(revenue_row, col),
                        operating_income=safe_decimal(op_income_row, col),
                        net_income=safe_decimal(net_income_row, col),
                    )
                )
            return results
        except Exception as e:
            logger.error("財務サマリー取得失敗: %s, error=%s", ticker, e)
            return []

    # ------------------------------------------------------------------
    # ユーティリティ
    # ------------------------------------------------------------------

    @staticmethod
    def to_yahoo_ticker(securities_code: str) -> str:
        """証券コードを Yahoo Finance ティッカーに変換する。

        例: "7203" -> "7203.T"
        """
        code = securities_code.strip()
        if not code.endswith(".T"):
            return f"{code}.T"
        return code

    @staticmethod
    def get_latest_close(ticker: str) -> Decimal | None:
        """最新の終値を取得する（簡易版）。"""
        try:
            df = yf.Ticker(ticker).history(period="2d", auto_adjust=True)
            if df.empty:
                return None
            return Decimal(str(round(float(df["Close"].iloc[-1]), 2)))
        except Exception:
            return None
