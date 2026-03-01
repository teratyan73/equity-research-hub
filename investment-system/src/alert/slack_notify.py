"""Slack 通知モジュール.

Incoming Webhook を使って投資アラートを Slack に送信する。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import requests

from ..analysis.models import EarningsAnalysisResult, MarketRegime, RegimeState
from ..config import get_settings

logger = logging.getLogger(__name__)

# センチメント別の絵文字
SENTIMENT_EMOJI = {
    "strong_positive": ":rocket:",
    "positive": ":chart_with_upwards_trend:",
    "neutral": ":bar_chart:",
    "negative": ":chart_with_downwards_trend:",
    "strong_negative": ":red_circle:",
}

REGIME_EMOJI = {
    RegimeState.RISK_ON: ":green_circle:",
    RegimeState.RISK_OFF: ":red_circle:",
    RegimeState.NEUTRAL: ":yellow_circle:",
    RegimeState.TRANSITION: ":large_orange_circle:",
}


class SlackNotifier:
    """Slack Incoming Webhook 通知クライアント."""

    def __init__(self, webhook_url: str | None = None) -> None:
        settings = get_settings()
        self._webhook_url = webhook_url or settings.slack_webhook_url
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    @property
    def is_configured(self) -> bool:
        return bool(self._webhook_url)

    # ------------------------------------------------------------------
    # 決算アラート
    # ------------------------------------------------------------------

    def send_earnings_alert(
        self,
        analysis: EarningsAnalysisResult,
        mention_channel: bool = False,
    ) -> bool:
        """決算分析結果をアラートとして送信する。

        Args:
            analysis: EarningsAnalysisResult
            mention_channel: True の場合 @channel メンション

        Returns:
            送信成功時 True
        """
        emoji = SENTIMENT_EMOJI.get(analysis.sentiment.value, ":bar_chart:")
        mention = "<!channel> " if mention_channel else ""

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} 決算分析アラート: {analysis.company_name} ({analysis.ticker})",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*センチメント*\n{analysis.sentiment.value}"},
                    {"type": "mrkdwn", "text": f"*スコア*\n{analysis.sentiment_score}"},
                    {"type": "mrkdwn", "text": f"*決算期末*\n{analysis.period_end or '不明'}"},
                    {"type": "mrkdwn", "text": f"*確信度*\n{analysis.confidence}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*要約*\n{analysis.summary}"},
            },
        ]

        if analysis.key_positives:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*ポジティブ要因*\n" + "\n".join(f"• {p}" for p in analysis.key_positives),
                },
            })

        if analysis.key_risks:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*リスク要因*\n" + "\n".join(f"• {r}" for r in analysis.key_risks),
                },
            })

        if analysis.action_suggestion:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*アクション提案*\n{mention}{analysis.action_suggestion}",
                },
            })

        blocks.append({"type": "divider"})

        return self._send({"blocks": blocks})

    # ------------------------------------------------------------------
    # レジームアラート
    # ------------------------------------------------------------------

    def send_regime_alert(self, regime: MarketRegime) -> bool:
        """市場レジーム変化アラートを送信する。

        レジームが変化した場合のみ送信することを推奨。
        """
        emoji = REGIME_EMOJI.get(regime.state, ":bar_chart:")
        change_text = (
            f"{regime.previous_state.value} → {regime.state.value}"
            if regime.state_changed and regime.previous_state
            else regime.state.value
        )

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} 市場レジーム{'変化' if regime.state_changed else ''}アラート",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*レジーム*\n{change_text}"},
                    {"type": "mrkdwn", "text": f"*日付*\n{regime.reference_date}"},
                    {"type": "mrkdwn", "text": f"*リスクスコア*\n{regime.risk_score}"},
                    {"type": "mrkdwn", "text": f"*確信度*\n{regime.regime_confidence}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*解釈*\n{regime.interpretation}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*推奨アクション*\n{regime.recommended_action}"},
            },
            {"type": "divider"},
        ]

        return self._send({"blocks": blocks})

    # ------------------------------------------------------------------
    # 汎用テキスト
    # ------------------------------------------------------------------

    def send_text(self, text: str, channel: str | None = None) -> bool:
        """シンプルなテキストメッセージを送信する。"""
        payload: dict = {"text": text}
        if channel:
            payload["channel"] = channel
        return self._send(payload)

    def send_weekly_report(
        self,
        report_text: str,
        top_picks: list[str] | None = None,
    ) -> bool:
        """週次レポートを送信する。"""
        blocks: list[dict] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f":memo: 週次投資レポート ({datetime.now().strftime('%Y/%m/%d')})",
                },
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": report_text[:3000]},
            },
        ]

        if top_picks:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*注目銘柄*\n" + ", ".join(top_picks),
                },
            })

        blocks.append({"type": "divider"})
        return self._send({"blocks": blocks})

    # ------------------------------------------------------------------
    # 内部送信
    # ------------------------------------------------------------------

    def _send(self, payload: dict) -> bool:
        """Webhook に POST する。"""
        if not self.is_configured:
            logger.warning("Slack Webhook URL が未設定です。通知をスキップします。")
            return False

        try:
            resp = self._session.post(
                self._webhook_url,  # type: ignore[arg-type]
                data=json.dumps(payload),
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("Slack 通知送信完了")
            return True
        except requests.RequestException as e:
            logger.error("Slack 通知送信失敗: %s", e)
            return False
