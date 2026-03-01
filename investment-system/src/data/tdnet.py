"""TDnet 適時開示情報取得モジュール.

東証 TDnet (Timely Disclosure network) から適時開示情報を取得する。
公開 RSS フィードを利用する。
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

from .models import DisclosureDocument

logger = logging.getLogger(__name__)

JST = timezone(timedelta(hours=9))

# TDnet RSS フィード URL
TDNET_RSS_URL = "https://www.release.tdnet.info/inbs/I_main_00.html"
TDNET_RSS_FEED = "https://www.release.tdnet.info/inbs/i_{date}.rss"

# 決算関連キーワード
EARNINGS_KEYWORDS = [
    "決算短信", "業績予想", "配当", "決算", "四半期報告", "通期業績"
]
REVISION_KEYWORDS = ["業績修正", "修正", "下方修正", "上方修正"]


class TdnetClient:
    """TDnet 適時開示クライアント."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "InvestmentSystem/0.1 (research purpose)",
            "Accept": "application/xml, text/xml, */*",
        })

    # ------------------------------------------------------------------
    # 公開 RSS 取得
    # ------------------------------------------------------------------

    def fetch_disclosures(
        self,
        target_date: datetime | None = None,
    ) -> list[DisclosureDocument]:
        """指定日の適時開示情報を取得する。

        Args:
            target_date: 取得対象日時 (None の場合は今日)

        Returns:
            DisclosureDocument のリスト
        """
        if target_date is None:
            target_date = datetime.now(JST)

        date_str = target_date.strftime("%Y%m%d")
        url = TDNET_RSS_FEED.format(date=date_str)

        try:
            resp = self._session.get(url, timeout=30)
            resp.raise_for_status()
            return self._parse_rss(resp.text)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.info("TDnet RSS なし (休場日の可能性): %s", date_str)
                return []
            logger.error("TDnet RSS 取得失敗: %s, error=%s", url, e)
            return []
        except requests.RequestException as e:
            logger.error("TDnet 接続エラー: %s", e)
            return []

    def fetch_recent_earnings(self, days: int = 5) -> list[DisclosureDocument]:
        """直近 N 営業日の決算関連開示を取得する。"""
        docs: list[DisclosureDocument] = []
        now = datetime.now(JST)
        for i in range(days):
            target = now - timedelta(days=i)
            daily = self.fetch_disclosures(target)
            earnings = [d for d in daily if d.is_earnings]
            docs.extend(earnings)
        return docs

    def filter_by_ticker(
        self,
        documents: list[DisclosureDocument],
        securities_codes: list[str],
    ) -> list[DisclosureDocument]:
        """証券コードでフィルタリングする。"""
        code_set = set(securities_codes)
        return [d for d in documents if d.securities_code in code_set]

    # ------------------------------------------------------------------
    # 内部パーサー
    # ------------------------------------------------------------------

    def _parse_rss(self, xml_text: str) -> list[DisclosureDocument]:
        """RSS XML をパースして DisclosureDocument リストを返す。"""
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as e:
            logger.error("RSS XML パース失敗: %s", e)
            return []

        ns = {"": "http://www.w3.org/2005/Atom"}

        documents: list[DisclosureDocument] = []
        for entry in root.findall(".//item"):
            try:
                doc = self._parse_item(entry)
                if doc:
                    documents.append(doc)
            except Exception as e:
                logger.debug("エントリーパース失敗: %s", e)

        return documents

    def _parse_item(self, item: ET.Element) -> DisclosureDocument | None:
        """RSS item 要素を DisclosureDocument に変換する。"""

        def text(tag: str) -> str:
            el = item.find(tag)
            return el.text.strip() if el is not None and el.text else ""

        title = text("title")
        link = text("link")
        pub_date_str = text("pubDate")
        description = text("description")

        # 証券コードを description から抽出（4-5桁数字）
        code_match = re.search(r"\b(\d{4,5})\b", description or title)
        securities_code = code_match.group(1) if code_match else "0000"

        # 会社名を title から推測
        company_name = re.sub(r"\s*\[.+?\]\s*$", "", title).strip()

        # 日時パース
        publish_dt: datetime
        try:
            from email.utils import parsedate_to_datetime
            publish_dt = parsedate_to_datetime(pub_date_str)
        except Exception:
            publish_dt = datetime.now(JST)

        is_earnings = any(kw in title for kw in EARNINGS_KEYWORDS)
        is_revision = any(kw in title for kw in REVISION_KEYWORDS)

        return DisclosureDocument(
            doc_id=link.split("/")[-1] or title[:50],
            securities_code=securities_code,
            company_name=company_name,
            title=title,
            category="earnings" if is_earnings else "general",
            publish_datetime=publish_dt,
            pdf_url=link or None,
            summary=description or None,
            is_earnings=is_earnings,
            is_revision=is_revision,
        )
