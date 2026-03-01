"""config.py のユニットテスト."""

import os
from unittest.mock import patch

import pytest


def test_settings_load_from_env() -> None:
    """環境変数から設定を読み込めることを確認する。"""
    env = {
        "ANTHROPIC_API_KEY": "test-anthropic-key",
        "EDINET_API_KEY": "test-edinet-key",
        "SLACK_WEBHOOK_URL": "https://hooks.slack.com/test",
        "LOG_LEVEL": "DEBUG",
        "MIN_MARKET_CAP": "200",
        "MAX_PORTFOLIO_SIZE": "15",
    }
    with patch.dict(os.environ, env, clear=True):
        from src.config import Settings
        settings = Settings()  # type: ignore[call-arg]
        assert settings.anthropic_api_key == "test-anthropic-key"
        assert settings.edinet_api_key == "test-edinet-key"
        assert settings.log_level == "DEBUG"
        assert settings.min_market_cap == 200
        assert settings.max_portfolio_size == 15


def test_settings_time_validator_valid() -> None:
    """正常な時刻フォーマットが受け入れられることを確認する。"""
    env = {
        "ANTHROPIC_API_KEY": "key",
        "EDINET_API_KEY": "key",
        "DAILY_UPDATE_TIME": "09:30",
    }
    with patch.dict(os.environ, env, clear=True):
        from src.config import Settings
        s = Settings()  # type: ignore[call-arg]
        assert s.daily_update_time == "09:30"


def test_settings_time_validator_invalid() -> None:
    """不正な時刻フォーマットが拒否されることを確認する。"""
    from pydantic import ValidationError
    env = {
        "ANTHROPIC_API_KEY": "key",
        "EDINET_API_KEY": "key",
        "DAILY_UPDATE_TIME": "25:00",
    }
    with patch.dict(os.environ, env, clear=True):
        from src.config import Settings
        with pytest.raises(ValidationError):
            Settings()  # type: ignore[call-arg]


def test_settings_directory_properties(tmp_path) -> None:
    """ディレクトリプロパティが正しく動作することを確認する。"""
    env = {
        "ANTHROPIC_API_KEY": "key",
        "EDINET_API_KEY": "key",
        "DATA_DIR": str(tmp_path),
    }
    with patch.dict(os.environ, env, clear=True):
        from src.config import Settings
        s = Settings()  # type: ignore[call-arg]
        assert s.raw_dir == tmp_path / "raw"
        assert s.processed_dir == tmp_path / "processed"
        assert s.reports_dir == tmp_path / "reports"


def test_ensure_dirs_creates_directories(tmp_path) -> None:
    """ensure_dirs() がディレクトリを作成することを確認する。"""
    env = {
        "ANTHROPIC_API_KEY": "key",
        "EDINET_API_KEY": "key",
        "DATA_DIR": str(tmp_path / "data"),
    }
    with patch.dict(os.environ, env, clear=True):
        from src.config import Settings
        s = Settings()  # type: ignore[call-arg]
        s.ensure_dirs()
        assert s.raw_dir.exists()
        assert s.processed_dir.exists()
        assert s.reports_dir.exists()
