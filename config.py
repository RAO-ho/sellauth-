# SellAuth Bot の設定ファイル
# このファイルには秘密の情報が入ります。GitHubの公開リポジトリには絶対に置かないでください。

# ---- 必須 ----
DISCORD_TOKEN = "ここにBotトークン"
SELLAUTH_API_KEY = "ここにSellAuthのAPIキー"
SELLAUTH_SHOP_ID = "ここにSellAuthのショップID"

# ---- パネルのボタンに必要 ----
# 例: "https://xxxx.sellauth.com"
SELLAUTH_STORE_URL = "ここにストアのURL"

# ---- 任意 (そのままでOK) ----
# 管理コマンドを使えるユーザーID (サーバー管理者は自動で使えます)。例: [123456789012345678]
ADMIN_IDS = []

# 価格の前に付ける記号
CURRENCY_SYMBOL = "$"

# 商品ページのリンクの形 ({store} と {path} が入ります)
CHECKOUT_URL_TEMPLATE = "{store}/product/{path}"

# 在庫がこの数以下になったら通知
LOW_STOCK_THRESHOLD = 3

# 通知とパネルを確認する間隔 (秒)
POLL_SECONDS = 60

# データの保存先フォルダ
DATA_DIR = "data"
