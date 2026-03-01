"""Streamlit ダッシュボード - 日本株投資分析システム.

起動方法:
    streamlit run src/dashboard/app.py
"""

from __future__ import annotations

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# プロジェクトルートを sys.path に追加
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# ページ設定（最初に呼び出す必要がある）
# ------------------------------------------------------------------
st.set_page_config(
    page_title="日本株投資分析システム",
    page_icon=":chart_with_upwards_trend:",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ------------------------------------------------------------------
# 設定ロード
# ------------------------------------------------------------------
@st.cache_resource
def load_settings():
    from src.config import get_settings
    return get_settings()


# ------------------------------------------------------------------
# データ取得（キャッシュ付き）
# ------------------------------------------------------------------
@st.cache_data(ttl=3600)
def fetch_market_data(ticker: str, period: str) -> pd.DataFrame:
    from src.data.market import MarketDataClient
    client = MarketDataClient()
    return client.get_ohlcv_df(ticker, period=period)


@st.cache_data(ttl=3600)
def fetch_stock_info(ticker: str):
    from src.data.market import MarketDataClient
    client = MarketDataClient()
    return client.get_stock_info(ticker)


@st.cache_data(ttl=1800)
def fetch_index_data(period: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    from src.data.market import MarketDataClient, TOPIX_TICKER, NIKKEI225_TICKER
    client = MarketDataClient()
    topix = client.get_ohlcv_df(TOPIX_TICKER, period=period)
    nikkei = client.get_ohlcv_df(NIKKEI225_TICKER, period=period)
    return topix, nikkei


# ------------------------------------------------------------------
# サイドバー
# ------------------------------------------------------------------
def render_sidebar() -> dict:
    """サイドバーの設定 UI を描画し、選択値を返す。"""
    st.sidebar.title(":bar_chart: 分析設定")
    st.sidebar.markdown("---")

    ticker = st.sidebar.text_input(
        "ティッカーシンボル",
        value="7203.T",
        help="例: 7203.T (トヨタ), 6758.T (ソニー), 9984.T (ソフトバンクG)",
    )

    period = st.sidebar.selectbox(
        "期間",
        options=["3mo", "6mo", "1y", "2y", "5y"],
        index=2,
        format_func=lambda p: {
            "3mo": "3ヶ月", "6mo": "6ヶ月", "1y": "1年", "2y": "2年", "5y": "5年"
        }[p],
    )

    st.sidebar.markdown("---")
    show_regime = st.sidebar.checkbox("市場レジーム分析", value=True)
    show_benchmark = st.sidebar.checkbox("ベンチマーク比較", value=True)

    st.sidebar.markdown("---")
    st.sidebar.caption("日本株投資分析システム v0.1")

    return {
        "ticker": ticker.strip().upper(),
        "period": period,
        "show_regime": show_regime,
        "show_benchmark": show_benchmark,
    }


# ------------------------------------------------------------------
# ページ: 概要
# ------------------------------------------------------------------
def render_overview(ticker: str, period: str) -> None:
    st.header(":mag: 銘柄概要")

    col1, col2 = st.columns([1, 2])

    with col1:
        with st.spinner("銘柄情報を取得中..."):
            info = fetch_stock_info(ticker)

        if info:
            st.metric("銘柄名", info.name)
            st.metric("セクター", info.sector or "不明")
            st.metric(
                "時価総額",
                f"{info.market_cap:,.0f} 億円" if info.market_cap else "不明",
            )
        else:
            st.warning(f"銘柄情報が取得できませんでした: {ticker}")

    with col2:
        with st.spinner("株価データを取得中..."):
            df = fetch_market_data(ticker, period)

        if not df.empty and "close" in df.columns:
            import plotly.graph_objects as go

            fig = go.Figure()
            fig.add_trace(
                go.Candlestick(
                    x=df.index,
                    open=df["open"],
                    high=df["high"],
                    low=df["low"],
                    close=df["close"],
                    name=ticker,
                )
            )
            fig.update_layout(
                title=f"{ticker} - 株価チャート",
                xaxis_title="日付",
                yaxis_title="株価（円）",
                height=400,
                xaxis_rangeslider_visible=False,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.error("株価データを取得できませんでした。")


# ------------------------------------------------------------------
# ページ: グローススコア
# ------------------------------------------------------------------
def render_growth_score(ticker: str, period: str) -> None:
    st.header(":trophy: グロース株スコアリング")

    with st.spinner("スコア計算中..."):
        df = fetch_market_data(ticker, period)
        info = fetch_stock_info(ticker)

        if df.empty:
            st.warning("データが不足しています。")
            return

        from src.analysis.growth_score import GrowthScorer
        scorer = GrowthScorer()
        score = scorer.score(
            ticker=ticker,
            company_name=info.name if info else ticker,
            price_df=df,
        )

    # スコアゲージ表示
    col1, col2, col3 = st.columns(3)
    col1.metric("総合スコア", f"{score.total_score} / 100")
    col2.metric("モメンタムスコア", f"{score.momentum_score}")
    col3.metric("売上成長スコア", f"{score.revenue_growth_score}")

    import plotly.graph_objects as go

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=float(score.total_score),
        title={"text": "グロースポテンシャルスコア"},
        gauge={
            "axis": {"range": [0, 100]},
            "bar": {"color": "royalblue"},
            "steps": [
                {"range": [0, 40], "color": "lightgray"},
                {"range": [40, 70], "color": "lightyellow"},
                {"range": [70, 100], "color": "lightgreen"},
            ],
            "threshold": {
                "line": {"color": "red", "width": 4},
                "thickness": 0.75,
                "value": 60,
            },
        },
    ))
    fig.update_layout(height=300)
    st.plotly_chart(fig, use_container_width=True)

    # レーダーチャート
    categories = ["売上成長", "利益成長", "ROE", "モメンタム", "利益の質", "バリュエーション"]
    values = [
        float(score.revenue_growth_score),
        float(score.profit_growth_score),
        float(score.roe_score),
        float(score.momentum_score),
        float(score.earnings_quality_score),
        float(score.valuation_score),
    ]
    values_closed = values + [values[0]]
    categories_closed = categories + [categories[0]]

    fig_radar = go.Figure(go.Scatterpolar(
        r=values_closed,
        theta=categories_closed,
        fill="toself",
        name=ticker,
    ))
    fig_radar.update_layout(
        polar={"radialaxis": {"visible": True, "range": [0, 100]}},
        title="スコア内訳（レーダーチャート）",
        height=400,
    )
    st.plotly_chart(fig_radar, use_container_width=True)


# ------------------------------------------------------------------
# ページ: 市場レジーム
# ------------------------------------------------------------------
def render_regime(period: str) -> None:
    st.header(":compass: 市場レジーム分析 (TOPIX)")

    with st.spinner("レジーム分析中..."):
        topix_df, _ = fetch_index_data(period)

        if topix_df.empty or "close" not in topix_df.columns:
            st.warning("TOPIX データを取得できませんでした。")
            return

        from src.analysis.regime import RegimeDetector
        detector = RegimeDetector()
        regime = detector.detect(topix_df["close"])

    # レジーム表示
    regime_colors = {
        "risk_on": "🟢",
        "risk_off": "🔴",
        "neutral": "🟡",
        "transition": "🟠",
    }
    emoji = regime_colors.get(regime.state.value, "⚪")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("現在レジーム", f"{emoji} {regime.state.value.upper()}")
    col2.metric("リスクスコア", f"{regime.risk_score:.2f}")
    col3.metric("確信度", f"{regime.regime_confidence:.2f}")
    col4.metric("MAシグナル", regime.moving_avg_signal.upper())

    st.info(f"**解釈:** {regime.interpretation}")
    st.warning(f"**推奨アクション:** {regime.recommended_action}")

    if regime.state_changed and regime.previous_state:
        st.success(
            f"⚡ レジーム変化を検知: {regime.previous_state.value} → {regime.state.value}"
        )


# ------------------------------------------------------------------
# ページ: ベンチマーク比較
# ------------------------------------------------------------------
def render_benchmark(ticker: str, period: str) -> None:
    st.header(":scales: ベンチマーク比較 (vs TOPIX)")

    with st.spinner("比較分析中..."):
        stock_df = fetch_market_data(ticker, period)
        topix_df, nikkei_df = fetch_index_data(period)

        if stock_df.empty or topix_df.empty:
            st.warning("比較データが不足しています。")
            return

        from src.portfolio.benchmark import BenchmarkComparator
        comparator = BenchmarkComparator()

        stock_prices = stock_df["close"].dropna()
        topix_prices = topix_df["close"].dropna()
        nikkei_prices = nikkei_df["close"].dropna() if not nikkei_df.empty else pd.Series(dtype=float)

        comparison = comparator.compare(
            stock_prices, topix_prices, ticker, "TOPIX"
        )
        port_stats = comparison["portfolio"]
        bench_stats = comparison["benchmark"]

    # 統計サマリー
    col1, col2 = st.columns(2)
    with col1:
        st.subheader(f"{ticker}")
        st.metric("トータルリターン", f"{port_stats.total_return}%")
        st.metric("年率リターン", f"{port_stats.annualized_return}%")
        st.metric("ボラティリティ", f"{port_stats.volatility}%")
        st.metric("シャープレシオ", f"{port_stats.sharpe_ratio}")
        st.metric("最大DD", f"{port_stats.max_drawdown}%")

    with col2:
        st.subheader("TOPIX")
        st.metric("トータルリターン", f"{bench_stats.total_return}%")
        st.metric("年率リターン", f"{bench_stats.annualized_return}%")
        st.metric("ボラティリティ", f"{bench_stats.volatility}%")
        st.metric("シャープレシオ", f"{bench_stats.sharpe_ratio}")
        st.metric("最大DD", f"{bench_stats.max_drawdown}%")

    if port_stats.beta is not None:
        st.metric("ベータ (対TOPIX)", f"{port_stats.beta}")
    if port_stats.alpha is not None:
        st.metric("アルファ (年率%)", f"{port_stats.alpha}%")

    # 相対強度チャート
    import plotly.graph_objects as go

    rs = comparator.relative_strength_index(stock_prices, topix_prices)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=rs.index, y=rs.values, name="相対強度", line={"color": "royalblue"}))
    fig.add_hline(y=100, line_dash="dash", line_color="gray")
    fig.update_layout(
        title=f"相対強度指数: {ticker} vs TOPIX (基準=100)",
        xaxis_title="日付",
        yaxis_title="相対強度",
        height=350,
    )
    st.plotly_chart(fig, use_container_width=True)


# ------------------------------------------------------------------
# メイン
# ------------------------------------------------------------------
def main() -> None:
    st.title(":chart_with_upwards_trend: 日本株投資分析システム")
    st.markdown("EDINET・yfinance・Claude API を活用したグロース株分析プラットフォーム")

    config = render_sidebar()
    ticker = config["ticker"]
    period = config["period"]

    tab1, tab2, tab3, tab4 = st.tabs(
        ["銘柄概要", "グローススコア", "市場レジーム", "ベンチマーク比較"]
    )

    with tab1:
        render_overview(ticker, period)

    with tab2:
        render_growth_score(ticker, period)

    with tab3:
        if config["show_regime"]:
            render_regime(period)
        else:
            st.info("サイドバーで「市場レジーム分析」を有効にしてください。")

    with tab4:
        if config["show_benchmark"]:
            render_benchmark(ticker, period)
        else:
            st.info("サイドバーで「ベンチマーク比較」を有効にしてください。")


if __name__ == "__main__":
    main()
