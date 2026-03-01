"""環境変数・設定管理モジュール (pydantic-settings)."""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """アプリケーション全体の設定。.env ファイルから自動ロード。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # API キー
    # ------------------------------------------------------------------
    anthropic_api_key: str = Field(..., description="Anthropic API キー")
    edinet_api_key: str = Field(..., description="EDINET API キー")
    slack_webhook_url: str | None = Field(None, description="Slack Webhook URL")

    # ------------------------------------------------------------------
    # アプリケーション設定
    # ------------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        "INFO", description="ログレベル"
    )
    data_dir: Path = Field(Path("./data"), description="データ保存ルートディレクトリ")
    target_market: str = Field("tokyo", description="対象市場")
    min_market_cap: int = Field(100, description="最小時価総額（億円）", ge=0)
    max_portfolio_size: int = Field(20, description="最大ポートフォリオ銘柄数", ge=1)

    # ------------------------------------------------------------------
    # Claude モデル
    # ------------------------------------------------------------------
    claude_model: str = Field("claude-opus-4-6", description="使用する Claude モデル ID")

    # ------------------------------------------------------------------
    # DuckDB
    # ------------------------------------------------------------------
    duckdb_path: Path = Field(
        Path("./data/processed/investment.duckdb"), description="DuckDB ファイルパス"
    )

    # ------------------------------------------------------------------
    # スケジュール
    # ------------------------------------------------------------------
    daily_update_time: str = Field("08:00", description="日次更新時刻 (HH:MM, JST)")
    weekly_report_day: int = Field(0, description="週次レポート曜日 (0=月曜)", ge=0, le=6)
    weekly_report_time: str = Field("07:00", description="週次レポート送信時刻 (HH:MM)")

    # ------------------------------------------------------------------
    # バリデーション
    # ------------------------------------------------------------------
    @field_validator("daily_update_time", "weekly_report_time")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        parts = v.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            raise ValueError(f"時刻は HH:MM 形式で指定してください: {v!r}")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError(f"不正な時刻値: {v!r}")
        return v

    # ------------------------------------------------------------------
    # 便利プロパティ
    # ------------------------------------------------------------------
    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    def ensure_dirs(self) -> None:
        """必要なデータディレクトリを作成する。"""
        for d in (self.raw_dir, self.processed_dir, self.reports_dir):
            d.mkdir(parents=True, exist_ok=True)


# シングルトンインスタンス（モジュールレベルでキャッシュ）
_settings: Settings | None = None


def get_settings() -> Settings:
    """設定のシングルトンインスタンスを返す。"""
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
