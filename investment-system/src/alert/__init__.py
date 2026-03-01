"""アラートモジュール群."""

from .slack_notify import SlackNotifier

__all__ = ["SlackNotifier"]
