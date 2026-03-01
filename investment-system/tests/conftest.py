"""pytest グローバル設定 / フィクスチャ.

yfinance は curl_cffi / bs4 などのシステム依存パッケージを要求するが、
このテスト環境では一部がインストールできない。
pytest 収集・テスト実行前に sys.modules へ yfinance モックスタブを差し込み、
import エラーを回避する。

本番環境では `uv sync` / `pip install -e ".[dev]"` を実行すれば
全依存が揃うため、このスタブは無用になる。
"""

import sys
from unittest.mock import MagicMock


def _ensure_yfinance_importable() -> None:
    """yfinance が import 可能かチェックし、不可ならモックを差し込む。"""
    # すでに正しくロード済みなら何もしない
    if "yfinance" in sys.modules:
        existing = sys.modules["yfinance"]
        # 本物の yfinance は Ticker クラスを持つ
        if not isinstance(existing, MagicMock) and hasattr(existing, "Ticker"):
            return

    try:
        # 試しにインポート。システム依存パッケージが不足していると
        # ここで ModuleNotFoundError / ImportError が発生する
        import importlib
        importlib.import_module("yfinance")
    except (ImportError, ModuleNotFoundError):
        # yfinance 本体と主要サブモジュールをモックで差し替える
        mock = MagicMock(name="yfinance")
        for name in [
            "yfinance",
            "yfinance.base",
            "yfinance.ticker",
            "yfinance.utils",
            "yfinance.data",
        ]:
            sys.modules[name] = mock


_ensure_yfinance_importable()
