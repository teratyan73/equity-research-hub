"""株価データ取得モジュール (yfinance ラッパー).

Yahoo Finance から日本株の OHLCV データ・銘柄情報・財務データを取得する。

モジュールレベル関数 (get_stock_prices 等) はキャッシュ付きで利用可能。
後方互換のため MarketDataClient クラスも維持している。

キャッシュ仕様:
    当日に同一 ticker+period を再取得しないよう、
    ./data/raw/cache/ 配下に parquet ファイルを保存する。
    翌日以降は自動的にキャッシュミスとなり再取得される。
"""

from __future__ import annotations

import logging
import math
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable

import pandas as pd
import yfinance as yf

from .models import FinancialSummary, OHLCVData, StockInfo

logger = logging.getLogger(__name__)

# ===========================================================================
# 定数
# ===========================================================================

# 既存コードとの後方互換用エイリアス
TOPIX_TICKER = "^TOPIX"
NIKKEI225_TICKER = "^N225"
MOTHERS_TICKER = "^TSEMOTHR"

# インデックス ETF : 日本語ラベル -> ティッカー
INDEX_TICKERS: dict[str, str] = {
    "TOPIX": "1306.T",
    "日経225": "^N225",
    "グロース250": "2516.T",
    "TOPIX100": "1311.T",
    "TOPIXSmall": "1318.T",
}

# セクター ETF : 日本語ラベル -> ティッカー (NEXT FUNDS TOPIX-17 シリーズ)
SECTOR_ETF_TICKERS: dict[str, str] = {
    "銀行": "1615.T",
    "素材・化学": "1620.T",
    "医薬品": "1621.T",
    "自動車・輸送機": "1622.T",
    "機械": "1624.T",
    "電機・精密": "1625.T",
    "情報通信": "1626.T",
    "金融（除く銀行）": "1629.T",
    "不動産": "1630.T",
}

_DEFAULT_CACHE_DIR = Path("./data/raw/cache")

# ===========================================================================
# キャッシュ (当日分 parquet)
# ===========================================================================


class _PriceCache:
    """当日分の株価データを parquet ファイルにキャッシュする。

    同一 ticker+period の当日リクエストをファイルキャッシュで短絡し、
    yfinance へのリクエスト数を削減する。
    """

    def __init__(self, cache_dir: Path = _DEFAULT_CACHE_DIR) -> None:
        self._dir: Path | None = None
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            self._dir = cache_dir
        except OSError as e:
            logger.debug("キャッシュディレクトリ作成失敗 → キャッシュ無効: %s", e)

    # ------------------------------------------------------------------

    def _path(self, ticker: str, period: str) -> Path | None:
        if self._dir is None:
            return None
        today = date.today().isoformat()
        safe = ticker.replace("^", "IDX_").replace(".", "_").replace("/", "_")
        return self._dir / f"{safe}_{period}_{today}.parquet"

    def get(self, ticker: str, period: str) -> pd.DataFrame | None:
        """キャッシュから DataFrame を返す。キャッシュミスは None。"""
        path = self._path(ticker, period)
        if path is None or not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            logger.debug("キャッシュヒット: %s (%s)", ticker, period)
            return df
        except Exception as e:
            logger.debug("キャッシュ読み込み失敗 (無視): %s", e)
            return None

    def set(self, ticker: str, period: str, df: pd.DataFrame) -> None:
        """DataFrame をキャッシュに保存する。失敗は無視する。"""
        path = self._path(ticker, period)
        if path is None or df.empty:
            return
        try:
            df.to_parquet(path)
            logger.debug("キャッシュ保存: %s → %s", ticker, path.name)
        except Exception as e:
            logger.debug("キャッシュ書き込み失敗 (無視): %s", e)


# モジュール共有シングルトン (テストでは monkeypatch で差し替え可能)
_cache = _PriceCache()

# ===========================================================================
# 内部ユーティリティ
# ===========================================================================


def _fetch_with_retry(
    fetch_fn: Callable[[], pd.DataFrame],
    ticker: str,
    max_attempts: int = 3,
    base_sleep: float = 0.5,
) -> pd.DataFrame:
    """リトライ付きでフェッチ関数を実行する。

    Args:
        fetch_fn: 引数なしで呼び出せるフェッチ関数。
        ticker: ログ表示用ティッカー文字列。
        max_attempts: 最大試行回数。
        base_sleep: リトライ間の待機秒数（指数的に増加: 0.5, 1.0, 1.5）。

    Returns:
        取得できた DataFrame。全試行失敗時は空 DataFrame。
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            df = fetch_fn()
            if df is not None and not df.empty:
                return df
        except Exception as e:
            last_exc = e
            logger.debug(
                "フェッチ失敗 (attempt %d/%d): ticker=%s, error=%s",
                attempt + 1, max_attempts, ticker, e,
            )
        # 最後の試行後は sleep しない
        if attempt < max_attempts - 1:
            time.sleep(base_sleep * (attempt + 1))

    if last_exc:
        logger.warning("最大リトライ到達: ticker=%s, error=%s", ticker, last_exc)
    return pd.DataFrame()


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """yfinance の OHLCV DataFrame を正規化する。

    - 列名をスネークケース小文字に統一 (例: "Stock Splits" → "stock_splits")
    - tz-aware インデックスを tz-naive DatetimeIndex に変換
    """
    if df.empty:
        return df
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df = df.copy()
        df.index = df.index.tz_localize(None)
    df.columns = pd.Index([str(c).lower().replace(" ", "_") for c in df.columns])
    return df


def _safe_val(info: dict, key: str) -> float | None:
    """info dict から float 値を安全に取り出す。NaN / Inf は None に変換。"""
    v = info.get(key)
    if v is None:
        return None
    try:
        fv = float(v)
        return None if not math.isfinite(fv) else fv
    except (TypeError, ValueError):
        return None


def _safe_pct(info: dict, key: str) -> float | None:
    """info dict から小数比率を % (×100) に変換して返す。"""
    v = _safe_val(info, key)
    return round(v * 100, 2) if v is not None else None


# ===========================================================================
# モジュールレベル公開関数
# ===========================================================================


def get_stock_prices(ticker: str, period: str = "2y") -> pd.DataFrame:
    """個別銘柄の株価データを取得する。

    当日分のキャッシュ（parquet）があればネットワークリクエストを省略する。
    取得失敗時は空 DataFrame を返し例外を送出しない。

    Args:
        ticker: ティッカーシンボル。日本株は末尾に ".T" が必要。
                例: "7203.T"（トヨタ）、"6758.T"（ソニー）
        period: 取得期間。yfinance の period 文字列。
                有効値: "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"

    Returns:
        正規化済み OHLCV DataFrame。
        列: open, high, low, close, volume, dividends, stock_splits
        インデックス: tz-naive DatetimeIndex（日付昇順）
        取得失敗時は空 DataFrame。
    """
    # キャッシュチェック
    cached = _cache.get(ticker, period)
    if cached is not None:
        return cached

    def _fetch() -> pd.DataFrame:
        raw = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        return _normalize_ohlcv(raw)

    df = _fetch_with_retry(_fetch, ticker)

    if df.empty:
        logger.warning("株価データ取得失敗: ticker=%s, period=%s", ticker, period)
    else:
        _cache.set(ticker, period, df)
        logger.info(
            "株価データ取得完了: ticker=%s, period=%s, rows=%d", ticker, period, len(df)
        )

    return df


def get_multiple_stocks(
    tickers: list[str],
    period: str = "1y",
    sleep_sec: float = 0.5,
) -> dict[str, pd.DataFrame]:
    """複数銘柄の株価データを一括取得する。

    銘柄間に sleep を挟んで Yahoo Finance のレートリミットを回避する。

    Args:
        tickers: ティッカーシンボルのリスト。例: ["7203.T", "6758.T"]
        period: 取得期間（yfinance period 文字列）。
        sleep_sec: 銘柄間の待機秒数（rate limit 対策）。

    Returns:
        {ticker: DataFrame} の辞書。
        取得失敗した銘柄は空 DataFrame がセットされる。
    """
    results: dict[str, pd.DataFrame] = {}
    for i, ticker in enumerate(tickers):
        df = get_stock_prices(ticker, period)
        results[ticker] = df
        if df.empty:
            logger.warning("取得失敗: %s", ticker)
        # 最後の銘柄の後は sleep しない
        if i < len(tickers) - 1:
            time.sleep(sleep_sec)

    success_count = sum(1 for df in results.values() if not df.empty)
    logger.info("一括取得完了: %d/%d 銘柄成功", success_count, len(tickers))
    return results


def get_financial_data(ticker: str) -> dict:
    """財務データを取得する。

    yfinance の `.info` から主要な財務指標を抽出して返す。
    日本株は一部データが未提供のため None になるフィールドがある。

    Args:
        ticker: ティッカーシンボル（例: "7203.T"）

    Returns:
        財務指標の辞書。主要キー:
          - per, per_forward    : 実績 / 予想 PER（倍）
          - pbr                 : PBR（倍）
          - roe, roa            : ROE / ROA（%）
          - operating_margin    : 営業利益率（%）
          - profit_margin       : 純利益率（%）
          - dividend_yield      : 配当利回り（%）
          - dividend_rate       : 1株当たり年間配当（円）
          - market_cap          : 時価総額（円）
          - market_cap_oku      : 時価総額（億円）
          - revenue             : 売上高（円）
          - revenue_growth      : 売上成長率（%、前年同期比）
          - earnings_growth     : 利益成長率（%）
          - debt_to_equity      : D/E レシオ
          - beta                : ベータ値
          - 52week_change       : 52 週騰落率（%）
          - eps_trailing        : 実績 EPS
          - eps_forward         : 予想 EPS
        取得失敗時は空の辞書 {} を返す。
    """
    try:
        info = yf.Ticker(ticker).info
    except Exception as e:
        logger.error("財務データ取得失敗: ticker=%s, error=%s", ticker, e)
        return {}

    if not info:
        logger.warning("財務データ空: ticker=%s", ticker)
        return {}

    market_cap = _safe_val(info, "marketCap")

    return {
        # 基本情報
        "ticker": ticker,
        "company_name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        # バリュエーション
        "per": _safe_val(info, "trailingPE"),
        "per_forward": _safe_val(info, "forwardPE"),
        "pbr": _safe_val(info, "priceToBook"),
        "ev": _safe_val(info, "enterpriseValue"),
        # 収益性
        "roe": _safe_pct(info, "returnOnEquity"),
        "roa": _safe_pct(info, "returnOnAssets"),
        "operating_margin": _safe_pct(info, "operatingMargins"),
        "profit_margin": _safe_pct(info, "profitMargins"),
        # 配当
        "dividend_yield": _safe_pct(info, "dividendYield"),
        "dividend_rate": _safe_val(info, "dividendRate"),
        # 規模
        "market_cap": market_cap,
        "market_cap_oku": (
            round(market_cap / 1e8, 1) if market_cap is not None else None
        ),
        "shares_outstanding": _safe_val(info, "sharesOutstanding"),
        # 財務成長
        "revenue": _safe_val(info, "totalRevenue"),
        "revenue_growth": _safe_pct(info, "revenueGrowth"),
        "earnings_growth": _safe_pct(info, "earningsGrowth"),
        # 財務健全性
        "debt_to_equity": _safe_val(info, "debtToEquity"),
        "current_ratio": _safe_val(info, "currentRatio"),
        "quick_ratio": _safe_val(info, "quickRatio"),
        # リスク
        "beta": _safe_val(info, "beta"),
        "52week_change": _safe_pct(info, "52WeekChange"),
        # EPS
        "eps_trailing": _safe_val(info, "trailingEps"),
        "eps_forward": _safe_val(info, "forwardEps"),
    }


def get_index_data(period: str = "2y") -> pd.DataFrame:
    """主要インデックス ETF の価格データを取得する。

    TOPIX・日経225・東証グロース250・TOPIX100・TOPIXSmall を
    まとめて取得し、日本語ラベルの列を持つ終値 DataFrame を返す。

    Args:
        period: 取得期間（yfinance period 文字列）。

    Returns:
        列がインデックス名（日本語）、行が日付の終値 DataFrame。
        取得できなかったインデックスは列に含まれない。
        全取得失敗時は空 DataFrame。
    """
    series: dict[str, pd.Series] = {}
    for label, ticker in INDEX_TICKERS.items():
        df = get_stock_prices(ticker, period)
        if not df.empty and "close" in df.columns:
            series[label] = df["close"].rename(label)
        else:
            logger.warning("インデックスデータ取得失敗: %s (%s)", label, ticker)
        time.sleep(0.5)

    if not series:
        logger.error("全インデックスデータの取得に失敗しました")
        return pd.DataFrame()

    result = pd.concat(series.values(), axis=1)
    result.index = pd.to_datetime(result.index)
    result.sort_index(inplace=True)
    logger.info(
        "インデックスデータ取得完了: %d 系列, %d 行", len(series), len(result)
    )
    return result


def get_sector_etfs(period: str = "1y") -> pd.DataFrame:
    """主要セクター ETF の価格データを取得する。

    NEXT FUNDS TOPIX-17 シリーズ ETF（銀行・医薬品・機械・情報通信等）を
    まとめて取得し、日本語セクター名の列を持つ終値 DataFrame を返す。

    Args:
        period: 取得期間（yfinance period 文字列）。

    Returns:
        列がセクター名（日本語）、行が日付の終値 DataFrame。
        取得できなかったセクターは列に含まれない。
        全取得失敗時は空 DataFrame。
    """
    series: dict[str, pd.Series] = {}
    for label, ticker in SECTOR_ETF_TICKERS.items():
        df = get_stock_prices(ticker, period)
        if not df.empty and "close" in df.columns:
            series[label] = df["close"].rename(label)
        else:
            logger.warning("セクター ETF データ取得失敗: %s (%s)", label, ticker)
        time.sleep(0.5)

    if not series:
        logger.error("全セクター ETF データの取得に失敗しました")
        return pd.DataFrame()

    result = pd.concat(series.values(), axis=1)
    result.index = pd.to_datetime(result.index)
    result.sort_index(inplace=True)
    logger.info(
        "セクター ETF 取得完了: %d セクター, %d 行", len(series), len(result)
    )
    return result


# ===========================================================================
# MarketDataClient クラス (後方互換)
# ===========================================================================


class MarketDataClient:
    """Yahoo Finance (yfinance) を使った市場データ取得クライアント.

    モジュールレベル関数 (get_stock_prices 等) のクラスラッパー。
    既存コードとの後方互換のために維持する。
    """

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
            info = yf.Ticker(ticker).info
            if not info or info.get("quoteType") is None:
                logger.warning("銘柄情報なし: %s", ticker)
                return None

            market_cap_raw = info.get("marketCap")
            market_cap = (
                Decimal(str(market_cap_raw)) / Decimal("100000000")
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
        """OHLCV データを OHLCVData リストで返す。

        Args:
            ticker: ティッカーシンボル
            start: 開始日 (None の場合は period を使用)
            end: 終了日
            period: yfinance period 文字列 (start 指定時は無視)

        Returns:
            OHLCVData のリスト（日付昇順）。取得失敗時は空リスト。
        """
        try:
            yf_ticker = yf.Ticker(ticker)
            if start:
                raw = yf_ticker.history(
                    start=start.isoformat(),
                    end=(end or date.today()).isoformat(),
                    auto_adjust=True,
                )
            else:
                raw = yf_ticker.history(period=period, auto_adjust=True)

            if raw.empty:
                logger.warning("OHLCV データなし: %s", ticker)
                return []

            records: list[OHLCVData] = []
            for idx, row in raw.iterrows():
                record_date = (
                    idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
                )
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
        """OHLCV データを DataFrame で返す（分析用）。

        start を指定した場合はキャッシュを使わず直接取得する。
        """
        if start:
            try:
                raw = yf.Ticker(ticker).history(
                    start=start.isoformat(), auto_adjust=True
                )
                return _normalize_ohlcv(raw)
            except Exception as e:
                logger.error("OHLCV DataFrame 取得失敗: %s, error=%s", ticker, e)
                return pd.DataFrame()

        # start 未指定時はモジュール関数経由でキャッシュを利用
        return get_stock_prices(ticker, period)

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
        return get_stock_prices(index, period)

    # ------------------------------------------------------------------
    # 財務データ
    # ------------------------------------------------------------------

    def get_financial_summary(self, ticker: str) -> list[FinancialSummary]:
        """yfinance から年次財務サマリーを取得する。

        Note:
            詳細な四半期データは EDINET/TDnet と組み合わせること。
        """
        try:
            financials = yf.Ticker(ticker).financials

            results: list[FinancialSummary] = []
            if financials is None or financials.empty:
                return results

            rev_row = (
                financials.loc["Total Revenue"]
                if "Total Revenue" in financials.index
                else None
            )
            op_row = (
                financials.loc["Operating Income"]
                if "Operating Income" in financials.index
                else None
            )
            ni_row = (
                financials.loc["Net Income"]
                if "Net Income" in financials.index
                else None
            )

            for col in financials.columns:
                period_end = col.date() if hasattr(col, "date") else date.today()

                def _d(series: pd.Series | None) -> Decimal | None:
                    if series is None:
                        return None
                    try:
                        v = series[col]  # type: ignore[index]
                        if pd.isna(v):
                            return None
                        return Decimal(str(round(float(v) / 1_000_000, 1)))
                    except Exception:
                        return None

                results.append(
                    FinancialSummary(
                        ticker=ticker,
                        period_end=period_end,
                        period_type="FY",
                        revenue=_d(rev_row),
                        operating_income=_d(op_row),
                        net_income=_d(ni_row),
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
        return code if code.endswith(".T") else f"{code}.T"

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
