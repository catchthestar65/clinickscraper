# AGAクリニック スクレイパー

Google MapsからAGAクリニック情報を収集し、被リンク営業用リストを作成するWebアプリケーション。

## 機能

- 地域名を入力してAGAクリニックを検索・抽出
- 大手チェーン・アフィリエイト出稿クリニックの自動除外
- Claude APIによる高精度なデータ検証
- Google Sheetsへの自動出力
- 設定のカスタマイズ（横展開対応）

## 技術スタック

| 項目 | 技術 |
|------|------|
| バックエンド | Python 3.11 + Flask |
| フロントエンド | HTML + Tailwind CSS + Alpine.js |
| スクレイピング | Playwright |
| AI検証 | Claude API |
| データ出力 | Google Sheets API |
| ホスティング | Render |

## セットアップ

### 1. 環境変数設定

以下の環境変数を設定してください：

| 変数名 | 必須 | 説明 |
|--------|------|------|
| `ANTHROPIC_API_KEY` | Yes | Claude APIキー |
| `GOOGLE_SHEETS_CREDENTIALS` | Yes | Google Cloud サービスアカウントのJSONキー（1行に圧縮） |
| `GOOGLE_SHEETS_ID` | Yes | 出力先スプレッドシートのID |
| `GOOGLE_SHEETS_NAME` | No | 出力先シート名（デフォルト: 営業リスト） |
| `SECRET_KEY` | No | Flask秘密鍵（本番環境では設定推奨） |
| `FLASK_ENV` | No | development / production |

### 2. Google Sheets連携設定

#### 手順1: Google Cloud Console でプロジェクト作成

1. https://console.cloud.google.com/ にアクセス
2. 「プロジェクトを選択」→「新しいプロジェクト」
3. プロジェクト名を入力して作成

#### 手順2: Google Sheets API を有効化

1. 左メニュー「APIとサービス」→「ライブラリ」
2. 「Google Sheets API」を検索
3. 「有効にする」をクリック

#### 手順3: サービスアカウント作成

1. 「APIとサービス」→「認証情報」
2. 「認証情報を作成」→「サービスアカウント」
3. サービスアカウント名を入力
4. 「作成して続行」→「完了」

#### 手順4: JSONキー発行

1. 作成したサービスアカウントをクリック
2. 「キー」タブ→「鍵を追加」→「新しい鍵を作成」
3. 「JSON」を選択→「作成」
4. ダウンロードされたJSONファイルを保存

#### 手順5: スプレッドシートへ共有設定

1. 対象のGoogle Sheetsを開く
2. 右上「共有」をクリック
3. サービスアカウントのメールアドレスを追加（`xxx@project-id.iam.gserviceaccount.com`）
4. 権限を「編集者」に設定
5. 「送信」

#### 手順6: 環境変数設定

JSONファイルの内容を1行に圧縮して `GOOGLE_SHEETS_CREDENTIALS` に設定：

```bash
# Mac/Linuxの場合
cat credentials.json | jq -c .
```

### 3. ローカル開発

```bash
# リポジトリクローン
git clone https://github.com/catchthestar65/clinickscraper.git
cd clinickscraper

# 仮想環境作成
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 依存関係インストール
pip install -r requirements-dev.txt

# Playwrightブラウザインストール
playwright install chromium

# 環境変数設定
cp .env.example .env
# .envファイルを編集

# 起動
python -m app.main
```

ブラウザで http://localhost:5000 にアクセス

### 4. テスト実行

```bash
# 全テスト実行
pytest

# カバレッジ付き
pytest --cov=app --cov-report=html

# 特定のテスト
pytest tests/test_services/test_exclusion_filter.py -v
```

### 5. Renderへのデプロイ

1. GitHubリポジトリをRenderに接続
2. 環境変数を設定
3. 自動デプロイ

## 使い方

1. メイン画面で検索地域を入力（カンマ区切りで複数可）
2. 「検索実行」をクリック
3. リアルタイムでログを確認
4. 結果がGoogle Sheetsに自動追加される

## 横展開（他の業種への対応）

1. 設定画面で「検索サフィックス」を変更（例: 美容整形、歯科）
2. 除外キーワードを業種に合わせて編集
3. 別のGoogle Sheetsを指定

## ディレクトリ構造

```
clinicscraper/
├── app/
│   ├── __init__.py
│   ├── main.py              # Flaskアプリ
│   ├── config.py            # 設定管理
│   ├── exceptions.py        # カスタム例外
│   ├── models/              # Pydanticモデル
│   ├── routes/              # APIエンドポイント
│   ├── services/            # ビジネスロジック
│   └── templates/           # HTMLテンプレート
├── config/                  # YAML設定ファイル
├── tests/                   # テスト
├── requirements.txt         # 本番依存関係
├── requirements-dev.txt     # 開発依存関係
├── Dockerfile
├── render.yaml
└── README.md
```

## API エンドポイント

| メソッド | パス | 説明 |
|----------|------|------|
| GET | `/health` | ヘルスチェック |
| GET | `/ready` | レディネスチェック |
| POST | `/api/scrape` | スクレイピング実行（SSE） |
| POST | `/api/scrape/preview` | プレビュー（Sheets書き込みなし） |
| GET | `/api/settings/` | 設定取得 |
| POST | `/api/settings/` | 設定更新 |
| POST | `/api/settings/test-sheets` | Sheets接続テスト |
| GET | `/api/settings/exclusion-keywords` | 除外キーワード取得 |
| POST | `/api/settings/exclusion-keywords` | 除外キーワード追加 |
| DELETE | `/api/settings/exclusion-keywords` | 除外キーワード削除 |

## トラブルシューティング

### Google Sheets接続エラー

- サービスアカウントのメールがスプレッドシートに共有されているか確認
- `GOOGLE_SHEETS_CREDENTIALS` のJSONが正しくフォーマットされているか確認
- スプレッドシートIDが正しいか確認

### スクレイピングが遅い

- Google Mapsの読み込みには時間がかかります
- 1地域あたり30-60秒程度かかる場合があります

### Claude API エラー

- `ANTHROPIC_API_KEY` が正しく設定されているか確認
- API使用量の制限に達していないか確認

## ライセンス

Private - All rights reserved
