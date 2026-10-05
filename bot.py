"""
SellAuth 連携 Discord Bot (単体版)

コマンド (すべて管理者のみ)
- /products [product_id] : 商品一覧。商品IDを入れると詳細(バリアントID)
- /orders [order_id]     : 売上と最新の注文。注文IDを入れると詳細
- /stock                 : 在庫を追加 (入力欄が開きます)
- /groups [name]         : グループ(カテゴリー)一覧。名前を入れると新規作成
- /panel                 : 自販機風パネルを置く (在庫・価格を自動更新 + ストアへのボタン)
- /notify                : このチャンネルを購入・在庫切れの通知先にする

パネルを消したいときは、Discordでそのメッセージを削除するだけでOKです。

設定 (config.py に書く。環境変数でも可)
- DISCORD_TOKEN        : Botトークン (必須)
- SELLAUTH_API_KEY     : SellAuthのAPIキー (必須)
- SELLAUTH_SHOP_ID     : SellAuthのショップID (必須)
- SELLAUTH_STORE_URL   : ストアのURL 例 https://xxxx.sellauth.com (パネルのボタンに必要)
- CHECKOUT_URL_TEMPLATE: 商品ページのリンクの形 省略時 {store}/product/{path}
- CURRENCY_SYMBOL      : 価格の前に付ける記号 省略時 $
- ADMIN_IDS            : 管理コマンドを許可するユーザーID (省略可)
- LOW_STOCK_THRESHOLD  : 在庫がこの数以下で通知 (省略時 3)
- POLL_SECONDS         : 通知・パネルの確認間隔 (省略時 60)
- DATA_DIR             : 保存先 (省略時 data)
"""

import json
import logging
import os
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sellauth_bot")

try:
    import config  # 同じフォルダの config.py
except ImportError:
    config = None


def setting(name, default=None):
    """config.py を優先し、なければ環境変数から読む"""
    v = getattr(config, name, None) if config else None
    if v is None or v == "" or v == [] or v == ():
        v = os.getenv(name)
    return default if v is None or v == "" else v


def required(name):
    v = setting(name)
    if not v or str(v).startswith("ここに"):
        raise SystemExit(f"{name} が未設定です。config.py に設定してください。")
    return str(v)


def parse_ids(raw):
    if isinstance(raw, (list, tuple, set)):
        return {int(x) for x in raw}
    return {int(x) for x in str(raw).replace(" ", "").split(",") if x.isdigit()}


TOKEN = required("DISCORD_TOKEN")
API_KEY = required("SELLAUTH_API_KEY")
SHOP_ID = required("SELLAUTH_SHOP_ID")
STORE_URL = str(setting("SELLAUTH_STORE_URL", "")).rstrip("/")
if STORE_URL.startswith("ここに"):
    STORE_URL = ""
CHECKOUT_URL_TEMPLATE = setting("CHECKOUT_URL_TEMPLATE", "{store}/product/{path}")
CURRENCY_SYMBOL = setting("CURRENCY_SYMBOL", "$")
ADMIN_IDS = parse_ids(setting("ADMIN_IDS", ""))
LOW_STOCK = int(setting("LOW_STOCK_THRESHOLD", 3))
POLL_SECONDS = int(setting("POLL_SECONDS", 60))
DATA_DIR = setting("DATA_DIR", "data")
STATE_PATH = os.path.join(DATA_DIR, "sellauth_state.json")

DONE_STATUSES = {"completed", "complete", "paid", "delivered", "fulfilled"}


# ---------------------------------------------------------------- API
class SellAuthError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


class SellAuth:
    BASE = "https://api.sellauth.com/v1"

    def __init__(self, key, shop_id):
        self.key = key
        self.shop_id = shop_id
        self.session = None

    async def request(self, method, path, **kw):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self.key}", "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=20),
            )
        url = f"{self.BASE}/shops/{self.shop_id}{path}"
        async with self.session.request(method, url, **kw) as r:
            text = await r.text()
            if r.status >= 400:
                raise SellAuthError(r.status, text[:300])
            if not text:
                return {}
            try:
                return json.loads(text)
            except ValueError:
                return {"raw": text}

    @staticmethod
    def rows(res):
        if isinstance(res, dict):
            data = res.get("data", [])
            return data if isinstance(data, list) else []
        return res if isinstance(res, list) else []

    async def products(self):
        return self.rows(await self.request("GET", "/products"))

    async def product(self, product_id):
        res = await self.request("GET", f"/products/{product_id}")
        return res.get("data", res) if isinstance(res, dict) else res

    async def invoices(self):
        return self.rows(await self.request("GET", "/invoices"))

    async def invoice(self, invoice_id):
        res = await self.request("GET", f"/invoices/{invoice_id}")
        return res.get("data", res) if isinstance(res, dict) else res

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


# ---------------------------------------------------------------- helpers
def clip(text, n):
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def fmt_num(v):
    """数字の見た目を整える: 5.00 -> 5 / 5.50 -> 5.5 / 5.25 -> 5.25"""
    try:
        d = float(v)
        if d == int(d):
            return str(int(d))
        return f"{d:.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError, OverflowError):
        return str(v)


def money(v):
    return f"{CURRENCY_SYMBOL}{fmt_num(v)}" if v is not None else "?"


def stock_of(p):
    for key in ("stock_count", "stock", "stock_amount"):
        v = p.get(key)
        if isinstance(v, (int, float)):
            return int(v)
    total, found = 0, False
    for v in p.get("variants") or []:
        for key in ("stock_count", "stock", "stock_amount"):
            if isinstance(v.get(key), (int, float)):
                total += int(v[key])
                found = True
                break
    return total if found else None


def price_of(p):
    for v in p.get("variants") or []:
        if v.get("price") is not None:
            return v.get("price")
    return p.get("price")


def checkout_url(p):
    if not STORE_URL:
        return None
    path = p.get("path") or p.get("id")
    return CHECKOUT_URL_TEMPLATE.format(store=STORE_URL, path=path, id=p.get("id"))


def invoice_summary(inv):
    status = str(inv.get("status", "?"))
    email = inv.get("email") or inv.get("customer_email") or "-"
    raw = inv.get("price") or inv.get("total") or inv.get("amount")
    amount = fmt_num(raw) if raw is not None else "-"
    currency = inv.get("currency") or ""
    name = "-"
    try:
        items = inv.get("items") or []
        if items:
            first = items[0]
            name = (first.get("product") or {}).get("name") or first.get("name") or "-"
            if len(items) > 1:
                name += f" ほか{len(items) - 1}点"
    except Exception:
        pass
    return status, email, f"{amount} {currency}".strip(), name


def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)


# ---------------------------------------------------------------- bot
class SellAuthBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default())
        self.sa = SellAuth(API_KEY, SHOP_ID)
        self.state = load_state()
        self.panel_sigs = {}

    async def setup_hook(self):
        await self.tree.sync()
        poll_loop.start()
        panel_loop.start()

    async def close(self):
        await self.sa.close()
        await super().close()


bot = SellAuthBot()


async def is_admin(interaction: discord.Interaction) -> bool:
    if interaction.user.id in ADMIN_IDS:
        return True
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.administrator)


def admin_command(name, description):
    """管理者だけに見える・使えるスラッシュコマンドを作る"""

    def deco(fn):
        fn = app_commands.check(is_admin)(fn)
        fn = app_commands.default_permissions(administrator=True)(fn)
        fn = app_commands.guild_only()(fn)
        return bot.tree.command(name=name, description=description)(fn)

    return deco


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    original = getattr(error, "original", error)
    if isinstance(error, app_commands.CheckFailure):
        msg = "このコマンドは管理者のみ使えます。"
    elif isinstance(original, SellAuthError):
        msg = f"SellAuth APIでエラーが出ました。\n```{clip(original, 500)}```"
    else:
        log.exception("command error", exc_info=original)
        msg = "エラーが発生しました。"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


# ---------------------------------------------------------------- /products
@admin_command("products", "商品一覧 (IDを入れると詳細)")
@app_commands.describe(product_id="詳しく見たい商品のID (空なら一覧)")
async def cmd_products(interaction: discord.Interaction, product_id: Optional[str] = None):
    await interaction.response.defer(ephemeral=True)

    if product_id:
        p = await bot.sa.product(product_id)
        embed = discord.Embed(title=clip(p.get("name", "?"), 256), color=0x57F287)
        stock = stock_of(p)
        embed.add_field(name="商品ID", value=str(p.get("id")))
        embed.add_field(name="在庫合計", value=str(stock if stock is not None else "?"))
        url = checkout_url(p)
        if url:
            embed.add_field(name="商品ページ", value=url, inline=False)
        vs = []
        for v in p.get("variants") or []:
            vstock = v.get("stock_count", v.get("stock", "?"))
            vs.append(f"`{v.get('id')}` {clip(v.get('name', '-'), 50)} / {money(v.get('price'))} / 在庫 {fmt_num(vstock)}")
        if vs:
            embed.add_field(name="バリアント (ID)", value=clip("\n".join(vs), 1024), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    products = await bot.sa.products()
    lines = []
    for p in products:
        stock = stock_of(p)
        lines.append(f"`{p.get('id')}` **{clip(p.get('name', '?'), 60)}** / {money(price_of(p))} / 在庫 {stock if stock is not None else '?'}")
    embed = discord.Embed(
        title=f"🛍 商品一覧 ({len(products)})",
        description=clip("\n".join(lines) or "商品がありません", 4000),
        color=0x57F287,
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


# ---------------------------------------------------------------- /orders
@admin_command("orders", "売上と最新の注文 (IDを入れると詳細)")
@app_commands.describe(order_id="詳しく見たい注文のID (空なら一覧)")
async def cmd_orders(interaction: discord.Interaction, order_id: Optional[str] = None):
    await interaction.response.defer(ephemeral=True)

    if order_id:
        inv = await bot.sa.invoice(order_id)
        status, email, amount, name = invoice_summary(inv)
        embed = discord.Embed(title=f"🧾 注文 {inv.get('id')}", color=0xFEE75C)
        embed.add_field(name="状態", value=status)
        embed.add_field(name="商品", value=clip(name, 1024))
        embed.add_field(name="金額", value=amount)
        embed.add_field(name="購入者", value=clip(email, 1024))
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    invoices = await bot.sa.invoices()
    lines = []
    for inv in invoices[:10]:
        status, email, amount, name = invoice_summary(inv)
        lines.append(f"`{inv.get('id')}` [{status}] {clip(name, 40)} / {amount} / {clip(email, 40)}")
    embed = discord.Embed(title="🧾 最新の注文", description="\n".join(lines) or "注文がありません", color=0xFEE75C)

    # 売上などの統計 (取れたときだけ表示)
    try:
        res = await bot.sa.request("GET", "/stats")
        stats = res.get("data", res) if isinstance(res, dict) else {}
        count = 0
        for k, v in stats.items():
            if isinstance(v, bool) or not isinstance(v, (str, int, float)):
                continue
            shown = fmt_num(v) if isinstance(v, (int, float)) else v
            embed.add_field(name=clip(k, 256), value=clip(shown, 1024), inline=True)
            count += 1
            if count >= 6:
                break
    except Exception:
        log.exception("stats failed")
    await interaction.followup.send(embed=embed, ephemeral=True)


# ---------------------------------------------------------------- /stock
class StockModal(discord.ui.Modal, title="在庫を追加"):
    stock_items = discord.ui.TextInput(
        label="追加する在庫 (1行に1つ)",
        style=discord.TextStyle.paragraph,
        max_length=4000,
    )

    def __init__(self, product_id, variant_id):
        super().__init__()
        self.product_id = product_id
        self.variant_id = variant_id

    async def on_submit(self, interaction: discord.Interaction):
        lines = [x.strip() for x in self.stock_items.value.splitlines() if x.strip()]
        await interaction.response.defer(ephemeral=True)
        try:
            await bot.sa.request(
                "POST",
                f"/products/{self.product_id}/deliverables/append/{self.variant_id}",
                json={"deliverables": lines},
            )
        except SellAuthError as e:
            await interaction.followup.send(f"在庫の追加に失敗しました。\n```{e}```", ephemeral=True)
            return
        await interaction.followup.send(f"✅ {len(lines)}件の在庫を追加しました。", ephemeral=True)


@admin_command("stock", "在庫を追加")
@app_commands.describe(product_id="商品ID", variant_id="バリアントID (/products 商品ID で確認)")
async def cmd_stock(interaction: discord.Interaction, product_id: str, variant_id: str):
    await interaction.response.send_modal(StockModal(product_id, variant_id))


# ---------------------------------------------------------------- /groups
@admin_command("groups", "カテゴリー(グループ)一覧 (名前を入れると作成)")
@app_commands.describe(name="新しく作るグループ名 (空なら一覧)")
async def cmd_groups(interaction: discord.Interaction, name: Optional[str] = None):
    await interaction.response.defer(ephemeral=True)
    if name:
        await bot.sa.request("POST", "/groups", json={"name": name, "visibility": "public", "products": []})
        await interaction.followup.send(f"✅ グループ「{name}」を作成しました。", ephemeral=True)
        return
    groups = bot.sa.rows(await bot.sa.request("GET", "/groups"))
    lines = [f"`{g.get('id')}` {g.get('name')}" for g in groups]
    embed = discord.Embed(title="📁 グループ", description="\n".join(lines) or "グループがありません", color=0x5865F2)
    await interaction.followup.send(embed=embed, ephemeral=True)


# ---------------------------------------------------------------- /notify
@admin_command("notify", "このチャンネルを購入・在庫切れの通知先にする")
async def cmd_notify(interaction: discord.Interaction):
    bot.state["notify_channel_id"] = interaction.channel_id
    save_state(bot.state)
    await interaction.response.send_message("✅ このチャンネルに購入・在庫切れの通知を送ります。", ephemeral=True)


# ---------------------------------------------------------------- /panel (自販機パネル)
def group_member_ids(group, all_products):
    ids = set()
    for x in group.get("products") or []:
        ids.add(str(x.get("id")) if isinstance(x, dict) else str(x))
    gid = str(group.get("id"))
    for p in all_products:
        g = p.get("group")
        if str(p.get("group_id")) == gid or (isinstance(g, dict) and str(g.get("id")) == gid):
            ids.add(str(p.get("id")))
    return ids


def panel_products(info, all_products, groups):
    by_id = {str(p.get("id")): p for p in all_products}
    ids = [str(i) for i in info.get("product_ids") or []]
    if info.get("group_id"):
        g = next((g for g in groups if str(g.get("id")) == str(info["group_id"])), None)
        if g:
            ids += [i for i in sorted(group_member_ids(g, all_products)) if i not in ids]
    return [by_id[i] for i in ids if i in by_id]


def build_panel(info, products):
    lines = []
    for p in products:
        stock = stock_of(p)
        if stock is None:
            mark, st = "⚪", "在庫不明"
        elif stock <= 0:
            mark, st = "🔴", "在庫切れ"
        else:
            mark, st = "🟢", f"在庫 {stock}"
        lines.append(f"{mark} **{clip(p.get('name', '?'), 60)}**\n　{money(price_of(p))} ｜ {st}")
    embed = discord.Embed(
        title=info["title"],
        description=clip("\n".join(lines) or "現在、商品がありません", 4000),
        color=0x57F287,
    )
    embed.set_footer(text=f"約{POLL_SECONDS}秒ごとに自動更新 / 購入はストアのサイトで行います")

    view = discord.ui.View(timeout=None)
    if STORE_URL:
        view.add_item(discord.ui.Button(label="ストアを開く", url=STORE_URL, emoji="🛒"))
        for p in products[:24]:
            url = checkout_url(p)
            if not url:
                continue
            stock = stock_of(p)
            view.add_item(
                discord.ui.Button(
                    label=clip(p.get("name", "?"), 30),
                    url=url,
                    disabled=(stock is not None and stock <= 0),
                )
            )
    return embed, view


def panel_signature(embed, view):
    buttons = [(getattr(c, "label", ""), getattr(c, "url", ""), getattr(c, "disabled", False)) for c in view.children]
    return json.dumps([embed.title, embed.description, buttons], ensure_ascii=False)


async def get_panel_message(info, message_id):
    ch = bot.get_channel(info["channel_id"])
    if ch is None:
        ch = await bot.fetch_channel(info["channel_id"])
    return await ch.fetch_message(int(message_id))


async def refresh_panels(all_products):
    panels = bot.state.get("panels") or {}
    if not panels:
        return
    groups = []
    if any(i.get("group_id") for i in panels.values()):
        try:
            groups = bot.sa.rows(await bot.sa.request("GET", "/groups"))
        except Exception:
            log.exception("groups fetch failed")
    changed_state = False
    for message_id, info in list(panels.items()):
        try:
            embed, view = build_panel(info, panel_products(info, all_products, groups))
            sig = panel_signature(embed, view)
            if bot.panel_sigs.get(message_id) == sig:
                continue
            msg = await get_panel_message(info, message_id)
            await msg.edit(embed=embed, view=view)
            bot.panel_sigs[message_id] = sig
        except discord.NotFound:
            # パネルのメッセージが削除されたので、登録も消す
            panels.pop(message_id, None)
            bot.panel_sigs.pop(message_id, None)
            changed_state = True
        except Exception:
            log.exception("panel refresh failed: %s", message_id)
    if changed_state:
        save_state(bot.state)


@admin_command("panel", "自販機風パネルをこのチャンネルに置く")
@app_commands.describe(
    title="パネルのタイトル 例: アカウント",
    product_ids="載せる商品ID (カンマ区切り。/products で確認)",
    group_id="グループID (/groups で確認)。商品IDとどちらか一方でOK",
)
async def cmd_panel(interaction: discord.Interaction, title: str, product_ids: str = "", group_id: str = ""):
    await interaction.response.defer(ephemeral=True)
    if not STORE_URL:
        await interaction.followup.send("ストアのURLが未設定です。config.py の SELLAUTH_STORE_URL を設定してください。", ephemeral=True)
        return
    ids = [x.strip() for x in product_ids.replace("、", ",").split(",") if x.strip()]
    if not ids and not group_id.strip():
        await interaction.followup.send("商品ID か グループID のどちらかを入れてください。", ephemeral=True)
        return
    info = {
        "title": title,
        "product_ids": ids,
        "group_id": group_id.strip() or None,
        "channel_id": interaction.channel_id,
    }
    all_products = await bot.sa.products()
    groups = bot.sa.rows(await bot.sa.request("GET", "/groups")) if info["group_id"] else []
    products = panel_products(info, all_products, groups)
    if not products:
        await interaction.followup.send(
            "商品が見つかりませんでした。商品IDを直接入れてください (/products で確認できます)。",
            ephemeral=True,
        )
        return
    embed, view = build_panel(info, products)
    msg = await interaction.channel.send(embed=embed, view=view)
    bot.state.setdefault("panels", {})[str(msg.id)] = info
    save_state(bot.state)
    bot.panel_sigs[str(msg.id)] = panel_signature(embed, view)
    await interaction.followup.send(f"✅ パネルを置きました ({len(products)}商品)。消すときはそのメッセージを削除してください。", ephemeral=True)


@tasks.loop(seconds=POLL_SECONDS)
async def panel_loop():
    try:
        await refresh_panels(await bot.sa.products())
    except Exception:
        log.exception("panel loop failed")


@panel_loop.before_loop
async def before_panel_loop():
    await bot.wait_until_ready()


# ---------------------------------------------------------------- 通知
async def notify_channel():
    cid = bot.state.get("notify_channel_id")
    if not cid:
        return None
    ch = bot.get_channel(cid)
    if ch is None:
        try:
            ch = await bot.fetch_channel(cid)
        except Exception:
            return None
    return ch


@tasks.loop(seconds=POLL_SECONDS)
async def poll_loop():
    try:
        invoices = await bot.sa.invoices()
        seen = set(bot.state.get("seen_invoices", []))
        done = [i for i in invoices if str(i.get("status", "")).lower() in DONE_STATUSES]

        if not bot.state.get("initialized"):
            # 初回は過去分を通知せず、既読にするだけ
            seen.update(str(i.get("id")) for i in done)
            bot.state["initialized"] = True
        else:
            ch = await notify_channel()
            for inv in reversed(done):
                iid = str(inv.get("id"))
                if iid in seen:
                    continue
                if ch:
                    status, email, amount, name = invoice_summary(inv)
                    embed = discord.Embed(title="💰 新しい購入", color=0x57F287)
                    embed.add_field(name="商品", value=clip(name, 1024))
                    embed.add_field(name="金額", value=amount)
                    embed.add_field(name="注文ID", value=iid)
                    await ch.send(embed=embed)
                seen.add(iid)

        bot.state["seen_invoices"] = list(seen)[-500:]

        # 在庫アラート
        ch = await notify_channel()
        alerted = set(bot.state.get("low_stock_alerted", []))
        for p in await bot.sa.products():
            stock = stock_of(p)
            pid = str(p.get("id"))
            if stock is None:
                continue
            if stock <= LOW_STOCK:
                if pid not in alerted and ch:
                    label = "在庫切れ" if stock == 0 else f"在庫わずか ({stock})"
                    await ch.send(f"⚠️ **{p.get('name')}** が{label}です。")
                alerted.add(pid)
            else:
                alerted.discard(pid)
        bot.state["low_stock_alerted"] = list(alerted)
        save_state(bot.state)
    except Exception:
        log.exception("poll failed")


@poll_loop.before_loop
async def before_poll():
    await bot.wait_until_ready()


if __name__ == "__main__":
    bot.run(TOKEN)
