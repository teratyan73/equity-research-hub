"""EDINET API 連携モジュール.

EDINET (Electronic Disclosure for Investors' NETwork) から
有価証券報告書・決算短信等の書類を取得する。

API 仕様: https://disclosure2.edinet-fsa.go.jp/weee0010.aspx
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from pathlib import Path

import requests

from ..config import get_settings
from .models import EdinetDocument

logger = logging.getLogger(__name__)

EDINET_BASE_URL = "https://disclosure.edinet-fsa.go.jp/api/v2"

# 書類種別コード
DOC_TYPE_ANNUAL_REPORT = "120"        # 有価証券報告書
DOC_TYPE_QUARTERLY_REPORT = "140"     # 四半期報告書
DOC_TYPE_EARNINGS_BRIEF = "30"        # 決算短信（連結）
DOC_TYPE_EARNINGS_BRIEF_NC = "35"     # 決算短信（非連結）


class EdinetClient:
    """EDINET API クライアント."""

    def __init__(self, api_key: str | None = None) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.edinet_api_key
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "InvestmentSystem/0.1"})

    # ------------------------------------------------------------------
    # 書類一覧取得
    # ------------------------------------------------------------------

    def fetch_document_list(
        self,
        target_date: date,
        doc_type: str = "2",  # 1=メタのみ, 2=メタ+書類情報
    ) -> list[EdinetDocument]:
        """指定日付の提出書類一覧を取得する。

        Args:
            target_date: 取得対象日
            doc_type: "1" または "2"

        Returns:
            EdinetDocument のリスト
        """
        url = f"{EDINET_BASE_URL}/documents.json"
        params = {
            "date": target_date.strftime("%Y-%m-%d"),
            "type": doc_type,
            "Subscription-Key": self._api_key,
        }

        try:
            resp = self._session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.error("EDINET 書類一覧取得失敗: date=%s, error=%s", target_date, e)
            return []

        results = data.get("results", [])
        documents: list[EdinetDocument] = []
        for item in results:
            try:
                doc = self._parse_document(item)
                if doc:
                    documents.append(doc)
            except Exception as e:
                logger.debug("書類パース失敗: %s, error=%s", item.get("docID"), e)

        logger.info("EDINET 書類一覧取得: date=%s, count=%d", target_date, len(documents))
        return documents

    def fetch_recent_earnings(self, days: int = 7) -> list[EdinetDocument]:
        """直近 N 日間の決算短信書類を取得する。"""
        today = date.today()
        docs: list[EdinetDocument] = []
        for i in range(days):
            target = today - timedelta(days=i)
            daily_docs = self.fetch_document_list(target)
            earnings = [
                d for d in daily_docs
                if d.doc_type_code in (DOC_TYPE_EARNINGS_BRIEF, DOC_TYPE_EARNINGS_BRIEF_NC)
            ]
            docs.extend(earnings)
            time.sleep(0.5)  # レート制限対策
        return docs

    # ------------------------------------------------------------------
    # 書類本文取得
    # ------------------------------------------------------------------

    def download_document(
        self,
        doc_id: str,
        doc_type: int = 2,  # 1=提出本文, 2=PDF, 3=添付書類, 4=XBRL
        save_dir: Path | None = None,
    ) -> bytes | None:
        """書類本文をダウンロードする。

        Args:
            doc_id: 書類管理番号
            doc_type: 書類タイプ番号
            save_dir: 保存先ディレクトリ（None の場合は保存しない）

        Returns:
            ダウンロードしたバイト列、失敗時は None
        """
        url = f"{EDINET_BASE_URL}/documents/{doc_id}"
        params = {
            "type": doc_type,
            "Subscription-Key": self._api_key,
        }

        try:
            resp = self._session.get(url, params=params, timeout=60, stream=True)
            resp.raise_for_status()
            content = resp.content
        except requests.RequestException as e:
            logger.error("EDINET 書類ダウンロード失敗: doc_id=%s, error=%s", doc_id, e)
            return None

        if save_dir:
            ext = "pdf" if doc_type == 2 else "zip"
            path = save_dir / f"{doc_id}.{ext}"
            path.write_bytes(content)
            logger.info("書類保存: %s", path)

        return content

    # ------------------------------------------------------------------
    # 内部ユーティリティ
    # ------------------------------------------------------------------

    def _parse_document(self, item: dict) -> EdinetDocument | None:
        """API レスポンスの個別アイテムを EdinetDocument に変換する。"""
        from datetime import datetime

        def parse_date(s: str | None) -> date | None:
            if not s:
                return None
            try:
                return date.fromisoformat(s)
            except ValueError:
                return None

        def parse_datetime(s: str | None) -> datetime | None:
            if not s:
                return None
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(s, fmt)
                except ValueError:
                    continue
            return None

        submit_dt = parse_datetime(item.get("submitDateTime"))
        if submit_dt is None:
            return None

        return EdinetDocument(
            doc_id=item["docID"],
            edinet_code=item.get("edinetCode", ""),
            securities_code=item.get("secCode") or None,
            company_name=item.get("filerName", ""),
            doc_type_code=item.get("docTypeCode", ""),
            doc_type_name=item.get("docDescription", ""),
            period_start=parse_date(item.get("periodStart")),
            period_end=parse_date(item.get("periodEnd")),
            submit_datetime=submit_dt,
            doc_description=item.get("docDescription"),
            xbrl_flag=bool(item.get("xbrlFlag") == "1"),
            pdf_flag=bool(item.get("pdfFlag") == "1"),
            csv_flag=bool(item.get("csvFlag") == "1"),
        )
