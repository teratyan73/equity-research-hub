"""src/data/market.py のテスト.

ユニットテスト (モック使用) と統合テスト (実 API) の 2 層構成。

統合テストは環境変数 RUN_INTEGRATION_TESTS=1 を設定した場合のみ実行される。
    RUN_INTEGRATION_TESTS=1 pytest tests/test_market.py -v -m integration
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# ===========================================================================
# テスト用ヘルパー
# ===========================================================================


def _yf_history(n: int = 120, price: float = 1000.0) -> pd.DataFrame:
    """yfinance の .history() が返す形式の DataFrame を生成する。

    - 列名は大文字・スペース区切り（例: "Stock Splits"）
    - インデックスは tz-aware (Asia/Tokyo)
    """
    idx = pd.date_range("2023-01-01", periods=n, freq="B", tz="Asia/Tokyo")
    return pd.DataFrame(
        {
            "Open": [price * 0.98] * n,
            "High": [price * 1.03] * n,
            "Low": [price * 0.96] * n,
            "Close": [price] * n,
            "Volume": [1_500_000] * n,
            "Dividends": [0.0] * n,
            "Stock Splits": [0.0] * n,
        },
        index=idx,
    )


def _normalized_df(n: int = 120, price: float = 1000.0) -> pd.DataFrame:
    """正規化済み（小文字・tz-naive）の DataFrame を生成する。"""
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "open": [price * 0.98] * n,
            "high": [price * 1.03] * n,
            "low": [price * 0.96] * n,
            "close": [price] * n,
            "volume": [1_500_000] * n,
            "dividends": [0.0] * n,
            "stock_splits": [0.0] * n,
        },
        index=idx,
    )


# ===========================================================================
# フィクスチャ
# ===========================================================================


@pytest.fixture
def no_cache(monkeypatch):
    """キャッシュを無効化するフィクスチャ。

    _cache.get は常に None を返し、_cache.set は何もしない。
    これにより全テストが yfinance モックを経由する。
    """
    import src.data.market as m

    monkeypatch.setattr(m._cache, "get", lambda ticker, period: None)
    monkeypatch.setattr(m._cache, "set", lambda ticker, period, df: None)


@pytest.fixture
def mock_yf(no_cache):
    """yfinance をモック化するフィクスチャ。

    デフォルトで _yf_history() を返す Ticker を用意する。
    """
    with patch("src.data.market.yf") as mock:
        mock.Ticker.return_value.history.return_value = _yf_history()
        yield mock


@pytest.fixture
def mock_yf_empty(no_cache):
    """常に空 DataFrame を返す yfinance モック。"""
    with patch("src.data.market.yf") as mock:
        mock.Ticker.return_value.history.return_value = pd.DataFrame()
        yield mock


# ===========================================================================
# _normalize_ohlcv
# ===========================================================================


class TestNormalizeOhlcv:
    def test_lowercases_columns(self):
        from src.data.market import _normalize_ohlcv

        df = _yf_history(10)
        result = _normalize_ohlcv(df)
        assert list(result.columns) == [
            "open", "high", "low", "close", "volume", "dividends", "stock_splits"
        ]

    def test_removes_timezone(self):
        from src.data.market import _normalize_ohlcv

        df = _yf_history(10)
        assert df.index.tz is not None  # 事前確認
        result = _normalize_ohlcv(df)
        assert result.index.tz is None

    def test_empty_returns_empty(self):
        from src.data.market import _normalize_ohlcv

        assert _normalize_ohlcv(pd.DataFrame()).empty

    def test_tz_naive_index_unchanged(self):
        from src.data.market import _normalize_ohlcv

        df = _normalized_df(10)  # already tz-naive
        result = _normalize_ohlcv(df)
        assert result.index.tz is None


# ===========================================================================
# _fetch_with_retry
# ===========================================================================


class TestFetchWithRetry:
    def test_returns_on_first_success(self):
        from src.data.market import _fetch_with_retry

        expected = _normalized_df(50)
        call_count = 0

        def _fetch():
            nonlocal call_count
            call_count += 1
            return expected

        result = _fetch_with_retry(_fetch, "7203.T", max_attempts=3, base_sleep=0)
        assert not result.empty
        assert call_count == 1

    def test_retries_on_failure_then_succeeds(self):
        from src.data.market import _fetch_with_retry

        attempt = 0

        def _fetch():
            nonlocal attempt
            attempt += 1
            if attempt < 3:
                raise ConnectionError("一時的なエラー")
            return _normalized_df(50)

        result = _fetch_with_retry(_fetch, "TEST.T", max_attempts=3, base_sleep=0)
        assert not result.empty
        assert attempt == 3

    def test_returns_empty_after_max_retries(self):
        from src.data.market import _fetch_with_retry

        def _fetch():
            raise ConnectionError("常に失敗")

        result = _fetch_with_retry(_fetch, "FAIL.T", max_attempts=3, base_sleep=0)
        assert result.empty

    def test_returns_empty_when_fetch_returns_empty(self):
        from src.data.market import _fetch_with_retry

        result = _fetch_with_retry(lambda: pd.DataFrame(), "EMPTY.T", base_sleep=0)
        assert result.empty


# ===========================================================================
# _safe_val / _safe_pct
# ===========================================================================


class TestSafeVal:
    def test_returns_float(self):
        from src.data.market import _safe_val

        assert _safe_val({"x": 1.5}, "x") == 1.5

    def test_returns_none_for_missing_key(self):
        from src.data.market import _safe_val

        assert _safe_val({}, "x") is None

    def test_returns_none_for_nan(self):
        from src.data.market import _safe_val

        assert _safe_val({"x": float("nan")}, "x") is None

    def test_returns_none_for_inf(self):
        from src.data.market import _safe_val

        assert _safe_val({"x": float("inf")}, "x") is None

    def test_converts_int(self):
        from src.data.market import _safe_val

        assert _safe_val({"x": 100}, "x") == 100.0


class TestSafePct:
    def test_multiplies_by_100(self):
        from src.data.market import _safe_pct

        result = _safe_pct({"roe": 0.123}, "roe")
        assert result == pytest.approx(12.3, rel=1e-3)

    def test_returns_none_for_nan(self):
        from src.data.market import _safe_pct

        assert _safe_pct({"x": float("nan")}, "x") is None

    def test_returns_none_for_missing(self):
        from src.data.market import _safe_pct

        assert _safe_pct({}, "x") is None


# ===========================================================================
# get_stock_prices
# ===========================================================================


class TestGetStockPrices:
    def test_returns_non_empty_dataframe(self, mock_yf):
        from src.data.market import get_stock_prices

        df = get_stock_prices("7203.T", period="1y")
        assert isinstance(df, pd.DataFrame)
        assert not df.empty

    def test_columns_are_lowercase(self, mock_yf):
        from src.data.market import get_stock_prices

        df = get_stock_prices("7203.T", period="1y")
        for col in df.columns:
            assert col == col.lower(), f"列名が小文字でない: {col!r}"

    def test_required_columns_present(self, mock_yf):
        from src.data.market import get_stock_prices

        df = get_stock_prices("7203.T", period="1y")
        for col in ("open", "high", "low", "close", "volume"):
            assert col in df.columns, f"列 {col!r} が存在しない"

    def test_index_is_tz_naive(self, mock_yf):
        from src.data.market import get_stock_prices

        df = get_stock_prices("7203.T", period="1y")
        assert df.index.tz is None, "インデックスに timezone が残っている"

    def test_returns_empty_dataframe_on_failure(self, mock_yf_empty):
        from src.data.market import get_stock_prices

        df = get_stock_prices("INVALID.T", period="1y")
        assert df.empty

    def test_does_not_raise_on_exception(self, no_cache):
        """例外が発生しても空 DataFrame を返すことを確認する。"""
        from src.data.market import get_stock_prices

        with patch("src.data.market.yf") as mock:
            mock.Ticker.return_value.history.side_effect = Exception("ネットワークエラー")
            df = get_stock_prices("ERR.T", period="1y")
        assert df.empty

    def test_cache_hit_skips_yfinance(self, monkeypatch):
        """キャッシュヒット時は yfinance を呼ばないことを確認する。"""
        import src.data.market as m

        cached_df = _normalized_df(50)
        monkeypatch.setattr(m._cache, "get", lambda t, p: cached_df)

        with patch("src.data.market.yf") as mock_yf:
            df = m.get_stock_prices("7203.T", period="1y")
            mock_yf.Ticker.assert_not_called()

        assert df is cached_df

    def test_cache_miss_saves_to_cache(self, no_cache, monkeypatch):
        """キャッシュミス時に取得後キャッシュへ保存することを確認する。"""
        import src.data.market as m

        saved: list[tuple] = []
        monkeypatch.setattr(
            m._cache, "set", lambda t, p, df: saved.append((t, p))
        )

        with patch("src.data.market.yf") as mock:
            mock.Ticker.return_value.history.return_value = _yf_history()
            m.get_stock_prices("7203.T", period="1y")

        assert len(saved) == 1
        assert saved[0][0] == "7203.T"

    def test_ticker_with_dot_t_suffix(self, mock_yf):
        """".T" を含むティッカーが正しく処理されることを確認する。"""
        from src.data.market import get_stock_prices

        df = get_stock_prices("6758.T", period="1y")
        assert not df.empty
        mock_yf.Ticker.assert_called_once_with("6758.T")


# ===========================================================================
# get_multiple_stocks
# ===========================================================================


class TestGetMultipleStocks:
    def test_returns_dict_with_all_tickers(self, mock_yf):
        from src.data.market import get_multiple_stocks

        tickers = ["7203.T", "6758.T", "9984.T"]
        result = get_multiple_stocks(tickers, period="1y", sleep_sec=0)
        assert set(result.keys()) == set(tickers)

    def test_successful_tickers_have_data(self, mock_yf):
        from src.data.market import get_multiple_stocks

        result = get_multiple_stocks(["7203.T", "6758.T"], period="1y", sleep_sec=0)
        for ticker, df in result.items():
            assert not df.empty, f"{ticker} のデータが空"

    def test_failed_ticker_returns_empty_dataframe(self, no_cache):
        """一部ティッカーが失敗しても辞書に空 DataFrame で含まれることを確認する。"""
        from src.data.market import get_multiple_stocks

        call_count = 0

        def _side_effect(ticker):
            nonlocal call_count
            mock = MagicMock()
            call_count += 1
            if ticker == "FAIL.T":
                mock.history.return_value = pd.DataFrame()
            else:
                mock.history.return_value = _yf_history()
            return mock

        with patch("src.data.market.yf") as mock_yf:
            mock_yf.Ticker.side_effect = _side_effect
            result = get_multiple_stocks(
                ["7203.T", "FAIL.T"], period="1y", sleep_sec=0
            )

        assert "7203.T" in result
        assert "FAIL.T" in result
        assert not result["7203.T"].empty
        assert result["FAIL.T"].empty

    def test_sleep_between_tickers(self, mock_yf, monkeypatch):
        """銘柄間に sleep が呼ばれることを確認する。"""
        from src.data import market as m

        sleep_calls: list[float] = []
        monkeypatch.setattr(m.time, "sleep", lambda s: sleep_calls.append(s))

        m.get_multiple_stocks(["7203.T", "6758.T", "9984.T"], sleep_sec=0.5)
        # 3 銘柄 → sleep は 2 回 (最後の後は不要)
        assert len(sleep_calls) == 2
        assert all(s == 0.5 for s in sleep_calls)

    def test_no_sleep_for_single_ticker(self, mock_yf, monkeypatch):
        from src.data import market as m

        sleep_calls: list[float] = []
        monkeypatch.setattr(m.time, "sleep", lambda s: sleep_calls.append(s))

        m.get_multiple_stocks(["7203.T"], sleep_sec=0.5)
        assert len(sleep_calls) == 0


# ===========================================================================
# get_financial_data
# ===========================================================================


class TestGetFinancialData:
    def _make_info(self) -> dict:
        return {
            "longName": "Toyota Motor Corporation",
            "sector": "Consumer Cyclical",
            "industry": "Auto Manufacturers",
            "trailingPE": 10.5,
            "forwardPE": 9.2,
            "priceToBook": 1.3,
            "returnOnEquity": 0.12,   # 12%
            "returnOnAssets": 0.04,   # 4%
            "operatingMargins": 0.08, # 8%
            "profitMargins": 0.06,    # 6%
            "dividendYield": 0.025,   # 2.5%
            "dividendRate": 240.0,
            "marketCap": 30_000_000_000_000,
            "sharesOutstanding": 1_500_000_000,
            "totalRevenue": 30_000_000_000_000,
            "revenueGrowth": 0.05,    # 5%
            "earningsGrowth": 0.08,   # 8%
            "debtToEquity": 45.0,
            "currentRatio": 1.2,
            "beta": 0.85,
            "52WeekChange": 0.15,     # 15%
            "trailingEps": 200.0,
            "forwardEps": 220.0,
        }

    def test_returns_dict(self):
        from src.data.market import get_financial_data

        with patch("src.data.market.yf.Ticker") as mock:
            mock.return_value.info = self._make_info()
            result = get_financial_data("7203.T")

        assert isinstance(result, dict)
        assert len(result) > 0

    def test_ticker_key_is_set(self):
        from src.data.market import get_financial_data

        with patch("src.data.market.yf.Ticker") as mock:
            mock.return_value.info = self._make_info()
            result = get_financial_data("7203.T")

        assert result["ticker"] == "7203.T"

    def test_company_name_extracted(self):
        from src.data.market import get_financial_data

        with patch("src.data.market.yf.Ticker") as mock:
            mock.return_value.info = self._make_info()
            result = get_financial_data("7203.T")

        assert result["company_name"] == "Toyota Motor Corporation"

    def test_pct_fields_are_percent(self):
        """ROE 等の % フィールドが小数ではなく % 値になっていることを確認する。"""
        from src.data.market import get_financial_data

        with patch("src.data.market.yf.Ticker") as mock:
            mock.return_value.info = self._make_info()
            result = get_financial_data("7203.T")

        assert result["roe"] == pytest.approx(12.0, rel=1e-3)
        assert result["roa"] == pytest.approx(4.0, rel=1e-3)
        assert result["operating_margin"] == pytest.approx(8.0, rel=1e-3)
        assert result["dividend_yield"] == pytest.approx(2.5, rel=1e-3)
        assert result["revenue_growth"] == pytest.approx(5.0, rel=1e-3)
        assert result["52week_change"] == pytest.approx(15.0, rel=1e-3)

    def test_market_cap_oku_conversion(self):
        """時価総額が億円に正しく変換されることを確認する。"""
        from src.data.market import get_financial_data

        with patch("src.data.market.yf.Ticker") as mock:
            mock.return_value.info = self._make_info()
            result = get_financial_data("7203.T")

        # 30_000_000_000_000 JPY = 300,000 億円
        assert result["market_cap_oku"] == pytest.approx(300_000.0, rel=1e-3)

    def test_nan_values_become_none(self):
        """NaN 値が None に変換されることを確認する。"""
        from src.data.market import get_financial_data

        info = self._make_info()
        info["trailingPE"] = float("nan")
        info["forwardPE"] = float("inf")

        with patch("src.data.market.yf.Ticker") as mock:
            mock.return_value.info = info
            result = get_financial_data("7203.T")

        assert result["per"] is None
        assert result["per_forward"] is None

    def test_returns_empty_dict_on_exception(self):
        from src.data.market import get_financial_data

        with patch("src.data.market.yf.Ticker") as mock:
            mock.side_effect = Exception("接続エラー")
            result = get_financial_data("7203.T")

        assert result == {}

    def test_returns_empty_dict_for_empty_info(self):
        from src.data.market import get_financial_data

        with patch("src.data.market.yf.Ticker") as mock:
            mock.return_value.info = {}
            result = get_financial_data("7203.T")

        assert result == {}

    def test_required_keys_present(self):
        """必須キーが全て含まれることを確認する。"""
        from src.data.market import get_financial_data

        with patch("src.data.market.yf.Ticker") as mock:
            mock.return_value.info = self._make_info()
            result = get_financial_data("7203.T")

        required = [
            "ticker", "company_name", "per", "pbr", "roe",
            "market_cap", "market_cap_oku", "revenue_growth",
            "dividend_yield", "beta",
        ]
        for key in required:
            assert key in result, f"必須キー {key!r} が存在しない"


# ===========================================================================
# get_index_data
# ===========================================================================


class TestGetIndexData:
    def test_returns_dataframe(self, mock_yf):
        from src.data.market import get_index_data

        with patch("src.data.market.time.sleep"):  # sleep をスキップ
            df = get_index_data(period="1y")

        assert isinstance(df, pd.DataFrame)
        assert not df.empty

    def test_columns_are_japanese_labels(self, mock_yf):
        from src.data.market import INDEX_TICKERS, get_index_data

        with patch("src.data.market.time.sleep"):
            df = get_index_data(period="1y")

        for label in INDEX_TICKERS.keys():
            assert label in df.columns, f"列 {label!r} が存在しない"

    def test_index_is_datetime(self, mock_yf):
        from src.data.market import get_index_data

        with patch("src.data.market.time.sleep"):
            df = get_index_data(period="1y")

        assert pd.api.types.is_datetime64_any_dtype(df.index)

    def test_sorted_ascending(self, mock_yf):
        from src.data.market import get_index_data

        with patch("src.data.market.time.sleep"):
            df = get_index_data(period="1y")

        assert df.index.is_monotonic_increasing

    def test_returns_empty_on_full_failure(self, mock_yf_empty):
        from src.data.market import get_index_data

        with patch("src.data.market.time.sleep"):
            df = get_index_data(period="1y")

        assert df.empty

    def test_partial_failure_still_returns_data(self, no_cache, monkeypatch):
        """一部インデックスの取得失敗でも残りのデータが返ることを確認する。"""
        from src.data import market as m

        call_num = 0

        def _patched_get_stock_prices(ticker: str, period: str) -> pd.DataFrame:
            nonlocal call_num
            call_num += 1
            if call_num == 1:
                return pd.DataFrame()  # 1件目失敗
            return _normalized_df(50)

        monkeypatch.setattr(m, "get_stock_prices", _patched_get_stock_prices)
        monkeypatch.setattr(m.time, "sleep", lambda s: None)

        df = m.get_index_data(period="1y")
        assert not df.empty
        assert len(df.columns) == len(m.INDEX_TICKERS) - 1


# ===========================================================================
# get_sector_etfs
# ===========================================================================


class TestGetSectorEtfs:
    def test_returns_dataframe(self, mock_yf):
        from src.data.market import get_sector_etfs

        with patch("src.data.market.time.sleep"):
            df = get_sector_etfs(period="1y")

        assert isinstance(df, pd.DataFrame)
        assert not df.empty

    def test_columns_are_sector_labels(self, mock_yf):
        from src.data.market import SECTOR_ETF_TICKERS, get_sector_etfs

        with patch("src.data.market.time.sleep"):
            df = get_sector_etfs(period="1y")

        for label in SECTOR_ETF_TICKERS.keys():
            assert label in df.columns, f"セクター列 {label!r} が存在しない"

    def test_index_is_datetime(self, mock_yf):
        from src.data.market import get_sector_etfs

        with patch("src.data.market.time.sleep"):
            df = get_sector_etfs(period="1y")

        assert pd.api.types.is_datetime64_any_dtype(df.index)

    def test_sorted_ascending(self, mock_yf):
        from src.data.market import get_sector_etfs

        with patch("src.data.market.time.sleep"):
            df = get_sector_etfs(period="1y")

        assert df.index.is_monotonic_increasing

    def test_returns_empty_on_full_failure(self, mock_yf_empty):
        from src.data.market import get_sector_etfs

        with patch("src.data.market.time.sleep"):
            df = get_sector_etfs(period="1y")

        assert df.empty


# ===========================================================================
# _PriceCache
# ===========================================================================


class TestPriceCache:
    def test_get_returns_none_on_miss(self, tmp_path):
        from src.data.market import _PriceCache

        cache = _PriceCache(tmp_path / "cache")
        assert cache.get("7203.T", "1y") is None

    def test_set_and_get_roundtrip(self, tmp_path):
        from src.data.market import _PriceCache

        cache = _PriceCache(tmp_path / "cache")
        df = _normalized_df(30)
        cache.set("7203.T", "1y", df)
        result = cache.get("7203.T", "1y")
        assert result is not None
        assert len(result) == 30

    def test_different_period_is_cache_miss(self, tmp_path):
        from src.data.market import _PriceCache

        cache = _PriceCache(tmp_path / "cache")
        df = _normalized_df(30)
        cache.set("7203.T", "1y", df)
        assert cache.get("7203.T", "2y") is None  # 異なる period

    def test_ticker_with_caret_is_safe_filename(self, tmp_path):
        """^ を含むティッカー（インデックス）がファイル名に安全に変換されることを確認する。"""
        from src.data.market import _PriceCache

        cache = _PriceCache(tmp_path / "cache")
        df = _normalized_df(30)
        cache.set("^N225", "1y", df)
        result = cache.get("^N225", "1y")
        assert result is not None

    def test_set_empty_dataframe_does_not_create_file(self, tmp_path):
        from src.data.market import _PriceCache

        cache_dir = tmp_path / "cache"
        cache = _PriceCache(cache_dir)
        cache.set("7203.T", "1y", pd.DataFrame())
        # 空 DataFrame は保存されない
        assert list(cache_dir.glob("*.parquet")) == []

    def test_disabled_gracefully_on_bad_dir(self):
        """書き込み不可パスでもエラーにならないことを確認する。

        /etc/passwd は通常ファイルなので、その「子ディレクトリ」は
        root であっても mkdir が失敗する。
        キャッシュが無効化されても例外を出さないことを確認する。
        """
        from src.data.market import _PriceCache

        # 既存ファイルを親とするパスは mkdir が必ず失敗する（root も不可）
        cache = _PriceCache(Path("/etc/passwd/impossible_cache_dir"))
        assert cache._dir is None, "不正パスでキャッシュが有効化されてはいけない"
        assert cache.get("7203.T", "1y") is None
        cache.set("7203.T", "1y", _normalized_df(10))  # 例外なし


# ===========================================================================
# MarketDataClient (後方互換)
# ===========================================================================


class TestMarketDataClientBackcompat:
    """既存コードが依存する MarketDataClient のインターフェイスを確認する。"""

    def test_get_ohlcv_df_delegates_to_get_stock_prices(self, mock_yf):
        from src.data.market import MarketDataClient

        client = MarketDataClient()
        df = client.get_ohlcv_df("7203.T", period="1y")
        assert not df.empty
        assert "close" in df.columns

    def test_to_yahoo_ticker_appends_dot_t(self):
        from src.data.market import MarketDataClient

        assert MarketDataClient.to_yahoo_ticker("7203") == "7203.T"

    def test_to_yahoo_ticker_no_duplicate_suffix(self):
        from src.data.market import MarketDataClient

        assert MarketDataClient.to_yahoo_ticker("7203.T") == "7203.T"

    def test_get_stock_info_returns_none_on_failure(self):
        from src.data.market import MarketDataClient

        with patch("src.data.market.yf.Ticker") as mock:
            mock.side_effect = Exception("API エラー")
            client = MarketDataClient()
            assert client.get_stock_info("7203.T") is None

    def test_constants_exported(self):
        """定数が後方互換のためにエクスポートされていることを確認する。"""
        from src.data import market

        assert hasattr(market, "TOPIX_TICKER")
        assert hasattr(market, "NIKKEI225_TICKER")
        assert hasattr(market, "MOTHERS_TICKER")


# ===========================================================================
# 統合テスト (実 API 呼び出し)
# ===========================================================================

_INTEGRATION = pytest.mark.skipif(
    not os.environ.get("RUN_INTEGRATION_TESTS"),
    reason="実 API テストは RUN_INTEGRATION_TESTS=1 で有効化",
)


@_INTEGRATION
class TestIntegration:
    """Yahoo Finance の実 API を呼び出す統合テスト.

    実行方法:
        RUN_INTEGRATION_TESTS=1 pytest tests/test_market.py -m integration -v
    """

    def test_toyota_stock_prices(self):
        """トヨタ(7203.T)の株価データが取得できることを確認する。"""
        from src.data.market import get_stock_prices

        df = get_stock_prices("7203.T", period="1mo")
        assert not df.empty, "トヨタ株価データが空"
        assert "close" in df.columns
        assert len(df) > 10, f"行数が少なすぎる: {len(df)}"
        assert (df["close"] > 0).all(), "終値に 0 以下の値がある"
        assert df.index.tz is None, "tz-aware インデックスが残っている"

    def test_sony_stock_prices(self):
        """ソニー(6758.T)の株価データが取得できることを確認する。"""
        from src.data.market import get_stock_prices

        df = get_stock_prices("6758.T", period="1mo")
        assert not df.empty, "ソニー株価データが空"
        assert "close" in df.columns
        assert len(df) > 10, f"行数が少なすぎる: {len(df)}"
        assert (df["close"] > 0).all(), "終値に 0 以下の値がある"

    def test_toyota_and_sony_multiple_stocks(self):
        """get_multiple_stocks でトヨタ・ソニーを一括取得できることを確認する。"""
        from src.data.market import get_multiple_stocks

        tickers = ["7203.T", "6758.T"]
        result = get_multiple_stocks(tickers, period="1mo")
        assert set(result.keys()) == set(tickers)
        for ticker, df in result.items():
            assert not df.empty, f"{ticker} のデータが空"
            assert "close" in df.columns

    def test_toyota_financial_data(self):
        """トヨタの財務データが取得できることを確認する。"""
        from src.data.market import get_financial_data

        data = get_financial_data("7203.T")
        assert isinstance(data, dict)
        assert data.get("ticker") == "7203.T"
        # 主要フィールドが存在する
        assert "per" in data
        assert "market_cap" in data
        if data["market_cap"] is not None:
            assert data["market_cap"] > 0

    def test_sony_financial_data(self):
        """ソニーの財務データが取得できることを確認する。"""
        from src.data.market import get_financial_data

        data = get_financial_data("6758.T")
        assert isinstance(data, dict)
        assert data.get("ticker") == "6758.T"

    def test_index_data_returns_major_indices(self):
        """主要インデックスデータが取得できることを確認する。"""
        from src.data.market import get_index_data

        df = get_index_data(period="1mo")
        assert not df.empty
        assert len(df.columns) >= 1, "インデックス列が 1 つ以上必要"

    def test_cache_works_for_second_call(self):
        """2 回目の呼び出しがキャッシュから返ることを確認する（速度比較）。"""
        from src.data.market import get_stock_prices

        # 1 回目 (ネットワーク or キャッシュ)
        start = time.perf_counter()
        df1 = get_stock_prices("7203.T", period="1mo")
        elapsed1 = time.perf_counter() - start

        # 2 回目 (必ずキャッシュ)
        start = time.perf_counter()
        df2 = get_stock_prices("7203.T", period="1mo")
        elapsed2 = time.perf_counter() - start

        assert not df1.empty
        assert not df2.empty
        # 2 回目はキャッシュのため十分速い（1 秒以内）
        assert elapsed2 < 1.0, f"キャッシュ取得が遅すぎる: {elapsed2:.3f}s"
