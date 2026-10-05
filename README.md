# SellAuth 連携 Discord Bot

SellAuth のストアを Discord から管理し、カテゴリー別の自販機風パネルで在庫・価格を表示する Bot です。
購入はこれまで通り SellAuth のストアサイトで行います（決済・商品の受け渡しは SellAuth が処理）。

## できること

- **自販機風パネル**: 商品名・価格・在庫を表示し、ボタンでストアのサイトへ移動。在庫や価格が変わると自動で書き換わります
- **管理コマンド**: 商品・在庫・注文の確認、在庫の追加、グループ(カテゴリー)の作成
- **通知**: 新しい購入と、在庫切れ・在庫わずかを指定チャンネルに自動投稿

## ファイル構成

| ファイル | 内容 |
| --- | --- |
| `bot.py` | Bot 本体 |
| `config.py` | 設定ファイル（トークンなど。秘密の情報が入ります） |
| `requirements.txt` | 必要なライブラリ |

## セットアップ

1. 3つのファイルを同じフォルダに置く
2. ライブラリを入れる: `pip install -r requirements.txt`
3. `config.py` を開いて次の4つを書き換える
   - `DISCORD_TOKEN`: Discord Developer Portal で発行した Bot トークン
   - `SELLAUTH_API_KEY`: SellAuth ダッシュボードで発行した API キー
   - `SELLAUTH_SHOP_ID`: SellAuth のショップ ID
   - `SELLAUTH_STORE_URL`: ストアの URL（例 `https://xxxx.sellauth.com`）
4. 起動: `python bot.py`

「ここに〜」の文字が残っていると、未設定のメッセージが出て起動が止まります。
環境変数（同じ名前）でも設定できますが、`config.py` の値が優先されます。

### Discord 側の設定

- Bot の招待時に `bot` と `applications.commands` の両方にチェックを入れる
- 権限は「チャンネルを見る」「メッセージを送信」「埋め込みリンク」があれば足ります
- 特別な Intent（Message Content Intent など）は不要です

### 注意

`config.py` にはトークンと API キーが入ります。**GitHub の公開リポジトリには絶対に置かないでください。**
漏れた場合は、Discord と SellAuth の両方でキーを作り直してください。

## 使い方

コマンドは管理者（サーバーの管理者権限、または `config.py` の `ADMIN_IDS` に入れたユーザー）だけが使えます。

### パネルを置く

1. `/sa products` で商品 ID を確認する
2. パネルを置きたいチャンネルで `/sa vm_panel` を実行する
   - `title`: パネルのタイトル（例: アカウント）
   - `product_ids`: 載せる商品 ID（カンマ区切り）
   - `group_id`: グループ ID（`/sa groups` で確認）。商品 ID とどちらか一方でOK
3. カテゴリーごとにチャンネルを分けて、これを繰り返す

パネルは約60秒ごとに確認し、内容が変わったときだけ書き換えます。
在庫が 0 の商品は 🔴 表示になり、ボタンは押せなくなります。

### 通知を受け取る

通知を送りたいチャンネルで `/sa notify_here` を実行します。
Bot の起動前に完了していた注文は通知されません。

### コマンド一覧

| コマンド | 内容 |
| --- | --- |
| `/sa stats` | ストアの統計 |
| `/sa products` | 商品一覧（ID・価格・在庫） |
| `/sa product` | 商品の詳細とバリアント ID |
| `/sa orders` | 最新の注文10件 |
| `/sa order` | 注文の詳細 |
| `/sa stock_add` | 在庫を追加（入力欄が開く。1行に1つ） |
| `/sa groups` | グループ(カテゴリー)一覧 |
| `/sa group_create` | グループ(カテゴリー)を作成 |
| `/sa notify_here` | このチャンネルを通知先にする |
| `/sa vm_panel` | 自販機風パネルを設置 |
| `/sa vm_list` | 設置済みパネルの一覧 |
| `/sa vm_remove` | パネルを削除 |

## 設定項目（config.py）

| 項目 | 内容 | 初期値 |
| --- | --- | --- |
| `DISCORD_TOKEN` | Bot トークン（必須） | - |
| `SELLAUTH_API_KEY` | SellAuth の API キー（必須） | - |
| `SELLAUTH_SHOP_ID` | ショップ ID（必須） | - |
| `SELLAUTH_STORE_URL` | ストアの URL（パネルに必要） | - |
| `ADMIN_IDS` | 管理コマンドを使えるユーザー ID のリスト | `[]` |
| `CURRENCY_SYMBOL` | 価格の前に付ける記号 | `$` |
| `CHECKOUT_URL_TEMPLATE` | 商品ページのリンクの形 | `{store}/product/{path}` |
| `LOW_STOCK_THRESHOLD` | この数以下で在庫わずか通知 | `3` |
| `POLL_SECONDS` | 通知・パネルの確認間隔（秒） | `60` |
| `DATA_DIR` | データの保存先フォルダ | `data` |

パネルの登録や通知先は `DATA_DIR` の中の `sellauth_state.json` に保存されます。このフォルダを消すと、パネルが自動更新されなくなります。

## 動作が未確認の部分

SellAuth の公式 API 資料を一部しか確認できていないため、次の部分は想定で作っています。
うまく動かない場合は、画面に出るエラー内容や表示を元に調整してください。

- **商品ボタンのリンク**: `{ストアURL}/product/{商品のpath}` を想定しています。開けない場合は `CHECKOUT_URL_TEMPLATE` を変更してください
- **グループ ID でのパネル作成**: 商品とグループの紐づきを API がどう返すか未確認です。「商品が見つかりません」と出たら商品 ID を直接指定してください
- **在庫追加（`/sa stock_add`）**: エンドポイントが想定どおりか未確認です。失敗すると SellAuth の返答がそのまま表示されます
- **購入通知**: 注文の「完了」の判定や項目名が想定どおりか未確認です。通知が来ない場合は `/sa orders` の表示を確認してください

まず `/sa products` と `/sa orders` で読み取りが正しくできるか確かめてから、パネルや在庫追加に進むのがおすすめです。

## トラブルシューティング

| 症状 | 確認すること |
| --- | --- |
| 起動時に「未設定です」と出る | `config.py` の「ここに〜」が残っていないか |
| `/sa` コマンドが出てこない | 招待時に `applications.commands` を付けたか。Bot を入れ直す |
| 「このコマンドは管理者のみ」と出る | サーバー管理者権限、または `ADMIN_IDS` に自分の ID があるか |
| 商品が `?` や在庫不明になる | SellAuth の返す項目名が想定と違う可能性。`/sa product` の表示を確認 |
| パネルが更新されない | `DATA_DIR` が消えていないか。`/sa vm_list` に出るか確認し、出なければ置き直す |
| SellAuth API でエラーが出る | API キーとショップ ID が正しいか。キーの権限を確認 |
