# 日本株投資分析システム

Claude API・EDINET・yfinanceを活用した日本株グロース株投資分析プラットフォームです。

## 主な機能

| 機能 | 説明 |
|------|------|
| 決算NLP分析 | Claude APIで有価証券報告書・決算短信を自動解析 |
| 市場レジーム検知 | 統計モデルでリスクオン/オフ局面を判定 |
| グロース株スコアリング | 売上成長率・ROE・モメンタムを組み合わせたスコア算出 |
| スクリーニング | カスタム条件でユニバースを絞り込み |
| ベンチマーク比較 | TOPIX・日経225との相対パフォーマンス測定 |
| Slackアラート | 決算サプライズ・レジーム転換を即時通知 |
| Streamlitダッシュボード | ブラウザで分析結果を可視化 |

## セットアップ

### 必要条件

- Python 3.11 以上
- [uv](https://docs.astral.sh/uv/) (推奨) または pip

### 1. リポジトリのクローン

```bash
git clone <repository-url>
cd investment-system
```

### 2. APIキーの取得

| API | 取得先 | 用途 |
|-----|--------|------|
| Anthropic API | https://console.anthropic.com/ | 決算テキスト解析 |
| EDINET API | https://disclosure2.edinet-fsa.go.jp/ | 有価証券報告書取得 |
| Slack Webhook | https://api.slack.com/apps | アラート通知（任意） |

### 3. 環境変数の設定

```bash
cp .env.example .env
# .env を編集して各APIキーを設定
```

### 4. 依存パッケージのインストール

#### uv を使う場合（推奨）

```bash
uv sync
```

#### pip を使う場合

```bash
pip install -e .
```

### 5. データディレクトリの作成

```bash
mkdir -p data/raw data/processed data/reports
```

## 使い方

### Streamlit ダッシュボードの起動

```bash
uv run streamlit run src/dashboard/app.py
```

ブラウザで `http://localhost:8501` を開きます。

### 日次バッチ更新

```bash
uv run python scripts/daily_update.py
```

### 決算分析（単一銘柄）

```bash
uv run python scripts/earnings_analyze.py --ticker 7203.T
```

### 週次レポート生成

```bash
uv run python scripts/weekly_report.py
```

### スケジューラーの起動（自動実行）

```bash
uv run python scripts/daily_update.py --daemon
```

## プロジェクト構成

```
investment-system/
├── src/
│   ├── config.py           # 環境変数・設定管理
│   ├── data/
│   │   ├── edinet.py       # EDINET API連携
│   │   ├── tdnet.py        # TDnet適時開示取得
│   │   ├── market.py       # 株価データ取得
│   │   └── models.py       # データモデル
│   ├── analysis/
│   │   ├── nlp_earnings.py # 決算NLP分析（Claude API）
│   │   ├── regime.py       # 市場レジーム検知
│   │   ├── growth_score.py # グロース株スコアリング
│   │   └── models.py       # 分析結果モデル
│   ├── portfolio/
│   │   ├── screener.py     # スクリーニング
│   │   └── benchmark.py    # ベンチマーク比較
│   ├── alert/
│   │   └── slack_notify.py # Slack通知
│   └── dashboard/
│       └── app.py          # Streamlit UI
├── scripts/
│   ├── daily_update.py     # 日次バッチ
│   ├── earnings_analyze.py # 決算分析バッチ
│   └── weekly_report.py    # 週次レポート
└── data/                   # ローカルデータ（gitignore対象）
    ├── raw/
    ├── processed/
    └── reports/
```

## 開発

### テストの実行

```bash
uv run pytest
```

### リントの実行

```bash
uv run ruff check src/ scripts/ tests/
uv run mypy src/
```

## 注意事項

- `data/` ディレクトリは `.gitignore` に含まれており、Git管理外です
- `.env` ファイルは絶対にコミットしないでください
- EDINET APIの利用には利用規約への同意が必要です
- 本システムは投資判断を補助するツールであり、投資を推奨するものではありません

## ライセンス

MIT License
