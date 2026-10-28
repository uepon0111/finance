# TQQQ Candle Lab

TQQQの日足データを取り込み、ローソク足パターン別の過去統計・市場局面・ニュース出典・Geminiの補助検証を表示する、GitHub Pages向けの日本語リサーチダッシュボードです。

> **投資判断の代わりにはなりません。** TQQQはNasdaq-100の日次リターンの3倍を目指すレバレッジETFです。複数日にわたるリターンは単純な3倍から乖離する可能性があります。必ず公式資料を確認してください: <https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq>

## できること

- yfinance経由でTQQQの調整後日足OHLCVを取得し、QQQ・VIXを市場局面の説明変数に使用
- 陽線／陰線、十字線、コマ、ハンマー、流れ星、包み足、はらみ足、切り込み線、かぶせ線、明けの明星、宵の明星、赤三兵、黒三兵などを分類
- 各足の出現数・出現頻度、1／3／5／10営業日後の上昇率・下落率・平均上昇幅・平均下落幅・平均リターンを計算
- 統計信頼度、市場局面を揃えた過去統計、Geminiの補助検証を別々に表示
- チャート上で日付を選び、OHLC値・足の分類・その後の実績・保存済みイベント参照を確認
- ニュース参照の公開日、配信元、URL、キーワード分類を保存。AIが指定したsource IDは取得済み記事と照合してから表示
- 毎日のAI方向判定をその後の値動きと照合し、1／3／5／10営業日後の方向一致率を蓄積
- GitHub Actionsで自動更新し、生成済みJSONをGitHub Pagesへ配信。設定すれば同じデータのバックアップをGoogle Driveにも保存

## ディレクトリ構成

```text
.
├── .github/workflows/update-and-deploy.yml  # 定期更新・テスト・Pagesデプロイ
├── pipeline/
│   ├── analytics.py                         # 足の分類・統計・市場局面
│   └── collect.py                           # yfinance／ニュース／Gemini／Drive
├── site/
│   ├── index.html                           # 日本語UI
│   ├── assets/styles.css                    # レスポンシブUI
│   ├── assets/app.js                        # チャート・表・日付詳細
│   └── data/
│       ├── latest.json                      # 最新分析（初回実行までは空状態）
│       └── history.json                     # AI検証履歴・ニュース参照アーカイブ
├── tests/test_analytics.py                  # ネット接続不要の単体テスト
├── tools/drive_oauth_setup.py               # Drive OAuthトークン作成補助
└── requirements.txt
```

## 1. GitHub Pagesに公開する

1. このフォルダー内のファイルをGitHubリポジトリのルートにアップロードします。ブランチ名が `main` 以外の場合は `.github/workflows/update-and-deploy.yml` の `branches: [main]` と push先を実際のブランチに合わせてください。
2. GitHubリポジトリの **Settings → Pages → Build and deployment → Source** を `GitHub Actions` にします。
3. **Settings → Actions → General → Workflow permissions** で `Read and write permissions` を許可します。ワークフロー内の `contents: write` は毎回更新されるJSONの保存に使います。
4. **Actions → Update TQQQ analysis and deploy Pages → Run workflow** で初回実行します。価格取得・テスト・JSON生成に成功するとPagesへデプロイされます。
5. リポジトリの **Settings → Pages** に表示されるURLを開きます。初回実行前の画面には価格サンプルは表示せず、未生成状態を表示します。

ワークフローは平日の `23:17 UTC`（日本時間では翌日 `08:17`）に実行されます。GitHubのスケジュール実行は遅延することがあり、米国市場の休場日には最新取引日が更新されない場合があります。必要に応じて手動実行してください。

## 2. Gemini APIの設定（任意）

Geminiを使わなくても、価格統計・足のチャート・ニュース一覧は動作します。Geminiは補助検証なので、キーを設定していない場合はAI欄を「未設定」と表示します。

1. Google AI StudioでGemini APIキーを作成します: <https://aistudio.google.com/apikey>
2. GitHubリポジトリの **Settings → Secrets and variables → Actions → New repository secret** を開き、名前を `GEMINI_API_KEYS` にします。
3. 1つだけならJSON配列 ` ["API_KEY_1"] `、複数キーなら例のようなJSON配列を登録します（説明用の文字列は置き換えてください）。

```json
["API_KEY_1", "API_KEY_2"]
```

4. 必要なら **Variables** に `GEMINI_MODEL` を設定します。省略時は `gemini-3.8-flash` を指定します。利用可能なモデルと無料枠はGoogle側で変わるため、実行前に[モデル一覧](https://ai.google.dev/gemini-api/docs/models)と[レート制限](https://ai.google.dev/gemini-api/docs/rate-limits)を確認してください。

互換用に単一キーの `GEMINI_API_KEY` も読み取りますが、複数キーの設定は `GEMINI_API_KEYS` を推奨します。API応答が認証・権限・レート制限・一時的なサーバーエラーとなった場合、登録済みキーを順に試します。Geminiのレート制限はAPIキー単位ではなくプロジェクト単位なので、同じプロジェクトのキーを増やしても割当量は増えません。**この機能は、利用権限があり各プロジェクトの規約・割当量に従ったフェイルオーバー用です。無料枠の制限を回避する目的で複数アカウントを使うことを推奨・保証するものではなく、Googleの規約や割当は各自で確認してください。**すべてのキーで失敗した場合、価格統計は維持し、Gemini検証のみ未実施として記録します。

同じ市場日については、既に成功しているGemini評価を再利用して不要な重複呼び出しを抑えます。新しい取引日のデータが得られた際に新規評価を行います。

## 3. Google Driveへのバックアップ（任意）

Google DriveはOAuthでユーザーが許可した個人アカウントを使用します。サービスアカウントやブラウザーへAPIキーを埋め込む方式ではありません。

### OAuthクライアントを作成

1. [Google Cloud Console](https://console.cloud.google.com/)でプロジェクトを用意し、Google Drive APIを有効にします。
2. OAuth同意画面を設定し、OAuthクライアントIDの種類を **Desktop app** にします。
3. クライアントJSONをダウンロードし、ローカル作業フォルダーに `client_secret.json` という名前で置きます。このファイルと生成トークンはGitHubへアップロードしないでください。
4. ローカル環境で依存パッケージを入れてから、次のコマンドを実行します。

```bash
python -m pip install -r requirements.txt
python tools/drive_oauth_setup.py --client-secrets client_secret.json --label drive-1 --output drive-account-1.json
```

ブラウザーで対象アカウントを選択し、アプリのアクセスを許可します。`drive-account-1.json` は秘密情報を含むので共有・コミットしないでください。追加アカウントも `--label drive-2 --output drive-account-2.json` のように実行します。

### Actions Secretに登録

生成ファイル内の各JSONオブジェクトを1つの配列にまとめ、Secret `DRIVE_ACCOUNTS_JSON` に登録します。

```json
[
  {
    "label": "drive-1",
    "client_id": "YOUR_CLIENT_ID",
    "client_secret": "YOUR_CLIENT_SECRET",
    "refresh_token": "YOUR_REFRESH_TOKEN"
  },
  {
    "label": "drive-2",
    "client_id": "YOUR_SECOND_CLIENT_ID",
    "client_secret": "YOUR_SECOND_CLIENT_SECRET",
    "refresh_token": "YOUR_SECOND_REFRESH_TOKEN"
  }
]
```

Actionsが各アカウントのOAuth認証を行い、Google Drive内に `TQQQ Candle Lab` フォルダーを作成して `latest.json` と `history.json` を更新します。認証・容量・API割当等でアカウントが利用できない場合は、次に設定されたアカウントを試します。Google側の制限やデータの所有・共有設定によっては切替に失敗する場合があるため、Actionsログでバックアップ成功を確認してください。

OAuth同意画面がテストモードのままだと、構成によってはrefresh tokenの有効期間が制限されることがあります。本人が使う運用でも、Google Cloud Consoleの公開ステータス・テストユーザー設定・同意画面要件を確認し、トークン失効時は再認証してください。バックアップ失敗は価格分析を止めず、GitHub上のJSONは維持します。

## 4. 信頼度の定義と注意点

### ① 過去統計

- パターンが出た日を母集団にし、その日の終値から1／3／5／10営業日後の終値を比較します。
- 上昇率・下落率は将来リターンの符号で計算し、平均上昇幅はプラスになった例のみ、平均下落幅はマイナスになった例のみを平均します。
- 格付けは保守的な目安です。概ね、Aは100件以上かつ95% Wilson区間の下限が偶然水準・全体基準を上回ること、Bは40件以上かつ区間下限が全体基準を上回ること、Cは10件以上、Dは10件未満を基本とします。条件を満たさない大標本はA/Bに上げません。
- 十字線やコマなど中立型は、上昇／下落の予測方向を前提にせず、|リターン|が0.5%以内かどうかを補助的な一致指標にします。

### ② 過去情勢＋直近情勢

- QQQの5営業日変化、VIX水準・5営業日変化、TQQQの20営業日実現ボラティリティで、リスク選好／リスク回避／高ボラティリティ／中立を機械的にラベル化します。
- 現在の足が同じ局面で過去に出た場合の値動きを比較します。
- Google News RSSの取得済み見出しには、URL・公開日・配信元・キーワード分類を付けます。最初の運用では保存済みイベント履歴が少ないため、過去の相場局面統計が中心です。運用日が増えると、同日に収集したイベント参照を日次ログに紐付けて振り返れます。
- 見出しのキーワード分類は簡易ヒューリスティックであり、本文の事実確認・ニュースの因果効果の測定ではありません。

### ③ Geminiによる補助検証

- Geminiへ送るのは、統計要約、市場局面、見出し・日付・URL等の参照メタデータです。取得した記事本文を完全に理解・検証したとは扱いません。
- AIが返したsource IDは取得済みニュースのIDと照合し、一致するIDのみを出典リンクとして表示します。AIが作ったURLや日付を出典として採用しません。
- AIの格付けはAIが申告する確信度だけで決めず、統計・局面統計との整合性、過去予測の検証件数・方向一致率を条件にします。初期データでは高い格付けになりにくい設定です。
- 予測方向を1／3／5／10営業日後の実現リターンで評価します。これは「学習済み投資モデルが自動的に改善して利益を保証する」ものではありません。パターン統計は価格が更新されるたびに再計算され、AI判定の過去実績は新しい結果が確定したときに追記されます。

## 5. テストとデバッグ

Actionsはデータ取得前に、ネットワークを使わない単体テストを実行します。ローカルでも次のコマンドで確認できます。

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

市場データ取得・ニュース取得・Gemini API・Google Drive APIは外部サービスなので、単体テストが成功しても外部サービスの稼働や認証を保証するものではありません。Actionsの各ステップログで失敗箇所を確認してください。キー、OAuthトークン、クライアントシークレットをログや公開JSONへ出力しない設計です。

## 6. 公開・プライバシー・データの扱い

- GitHub Pagesは通常公開サイトです。`site/data/latest.json` と `site/data/history.json` も公開されます。ここには市場価格、記事リンク、統計、AI出力のみを保存し、APIキーやOAuthトークンは含めません。個人メモや非公開情報を追加しないでください。
- APIキーとDrive OAuth情報はGitHub ActionsのSecretsにだけ保存します。生成されたOAuthトークンJSONをリポジトリへ入れないでください。
- yfinanceはYahoo Finance公開データを取得するサードパーティライブラリで、Yahooの公式な有料市場データAPIの代替保証ではありません。取得内容・利用条件・公開可否は最新の提供元条件を確認してください。
- データ更新にはGitHub Actionsの実行時間・リポジトリ容量・Google/Geminiの割当を使います。定期実行の頻度を不必要に高く設定しないでください。
