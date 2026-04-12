# ── LOAD .env FILE ─────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

import asyncio
import json
import logging
import os
import uuid
import websockets

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ── CONFIGURATION ───────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DERIV_APP_ID       = os.environ.get("DERIV_APP_ID", "1089")
DERIV_API_TOKEN    = os.environ.get("DERIV_API_TOKEN")
DERIV_WS_URL       = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"

ALERTS_FILE = "alerts.json"
USERS_FILE  = "users.json"
# ADMIN_ID    = int(os.environ.get("ADMIN_ID", "0"))
ADMIN_IDS = [2068321429, 6190585406]

# 💰 BTC PAYMENT CONFIG
BTC_ADDRESS = "bc1qdwf7va0xpkkutudgryd3tgmfscf8pmc7qn6v0n"
BTC_AMOUNT  = "BTC $20"

# ── LOGGING ──────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── GLOBAL STATE ─────────────────────────────────────────────────
symbol_cache = {}
alerts = {}
users = {}
subscribed_symbols = set()

# ════════════════════════════════════════════════════════════════
# 💰 USER PAYMENT SYSTEM
# ════════════════════════════════════════════════════════════════

def load_users():
    global users
    if os.path.exists(USERS_FILE):
        users = json.load(open(USERS_FILE))
    else:
        users = {}

def save_users():
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)

def has_access(user_id: str):
    return users.get(user_id, {}).get("paid", False)

def grant_access(user_id: str):
    users[user_id] = {"paid": True}
    save_users()

# ════════════════════════════════════════════════════════════════
# 💳 SHOW PAYMENT INFO
# ════════════════════════════════════════════════════════════════

async def show_payment(update: Update):
    chat_id = str(update.effective_chat.id)

    keyboard = [
        [InlineKeyboardButton("💸 I HAVE PAID (BTC)", callback_data=f"paid_{chat_id}")]
    ]


    msg = (
        "🔒 *PREMIUM ACCESS REQUIRED*\n\n"
        "🚀 Get real-time trading alerts instantly\n\n"

        "💰 *PAY WITH BITCOIN (BTC)*\n\n"

        f"🪙 *Amount:*\n`{BTC_AMOUNT}`\n\n"
        f"📥 *Wallet Address:*\n`{BTC_ADDRESS}`\n\n"

        "⚠️ *IMPORTANT:*\n"
        "• Send *ONLY BTC* to this address\n"
        "• Sending any other coin/network = *LOSS OF FUNDS* ❌\n"
        "• Send *exact amount* shown above\n"
        "• Wait for blockchain confirmation ⏳\n\n"

        f"🆔 *Your User ID:*\n`{chat_id}`\n\n"

        "👇 After payment, click the button below"
         )


    await update.message.reply_text(
        msg,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ════════════════════════════════════════════════════════════════
# 🔐 ACCESS CONTROL WRAPPER
# ════════════════════════════════════════════════════════════════

def paid_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_chat.id)

        if not has_access(user_id):
            await show_payment(update)
            return

        return await func(update, ctx)

    return wrapper

# ════════════════════════════════════════════════════════════════
# ⚡ DERIV FUNCTIONS (UNCHANGED)
# ════════════════════════════════════════════════════════════════

async def authorize(ws):
    if not DERIV_API_TOKEN:
        return
    await ws.send(json.dumps({"authorize": DERIV_API_TOKEN}))
    await ws.recv()

async def fetch_active_symbols():
    global symbol_cache
    async with websockets.connect(DERIV_WS_URL) as ws:
        await authorize(ws)
        await ws.send(json.dumps({"active_symbols": "brief"}))
        msg = json.loads(await ws.recv())

        for s in msg.get("active_symbols", []):
            symbol_cache[s["symbol"]] = s.get("display_name", s["symbol"])

# ════════════════════════════════════════════════════════════════
# 🔔 ALERT SYSTEM (UNCHANGED)
# ════════════════════════════════════════════════════════════════

def load_alerts():
    if os.path.exists(ALERTS_FILE):
        return json.load(open(ALERTS_FILE))
    return {}

def save_alerts():
    json.dump(alerts, open(ALERTS_FILE, "w"), indent=2)

async def check_alerts(symbol, price, app):
    for aid, a in alerts.items():
        if a["symbol"] == symbol and not a["triggered"]:
            if price >= a["price"]:
                a["triggered"] = True
                await app.bot.send_message(
                    chat_id=int(a["chat_id"]),
                    text=f"🔔 Alert hit {symbol} @ {price}"
                )
    save_alerts()

# ════════════════════════════════════════════════════════════════
# 🤖 COMMANDS (ONLY WRAPPED — LOGIC SAME)
# ════════════════════════════════════════════════════════════════

HELP_TEXT = "Use /addalert, /listalerts etc"

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not has_access(str(update.effective_chat.id)):
        await show_payment(update)
        return
    await update.message.reply_text(HELP_TEXT)

@paid_only
async def cmd_addalert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    symbol = ctx.args[0].upper()
    price = float(ctx.args[1])

    aid = str(uuid.uuid4())[:6]
    alerts[aid] = {
        "symbol": symbol,
        "price": price,
        "chat_id": str(update.effective_chat.id),
        "triggered": False
    }
    save_alerts()

    await update.message.reply_text(f"✅ Alert set {aid}")

@paid_only
async def cmd_listalerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(str(alerts))

@paid_only
async def cmd_removealert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    aid = ctx.args[0]
    alerts.pop(aid, None)
    save_alerts()
    await update.message.reply_text("Removed")

# ════════════════════════════════════════════════════════════════
# 🔘 BUTTON SYSTEM (APPROVAL)
# ════════════════════════════════════════════════════════════════

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = str(query.from_user.id)

    # USER CLICKED "I PAID"
    if data.startswith("paid_"):
        await query.answer()

        keyboard = [[
            InlineKeyboardButton("✅ APPROVE", callback_data=f"approve_{user_id}"),
            InlineKeyboardButton("❌ REJECT", callback_data=f"reject_{user_id}")
        ]]


        for admin_id in ADMIN_IDS:
            await ctx.bot.send_message(
                chat_id=admin_id,
                text=(
                    "🧾 *NEW BTC PAYMENT REQUEST*\n\n"
                    f"👤 User ID: `{user_id}`\n"
                    f"💰 Amount: {BTC_AMOUNT}\n\n"
                    "Check wallet → then approve 👇"
                ),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        await query.edit_message_text("⏳ Waiting approval...")

    # ADMIN APPROVES
    elif data.startswith("approve_"):
        uid = data.split("_")[1]
        grant_access(uid)

        await ctx.bot.send_message(uid, "🎉 Access granted!")
        await query.edit_message_text("Approved")

    # ADMIN REJECTS
    elif data.startswith("reject_"):
        uid = data.split("_")[1]
        await ctx.bot.send_message(uid, "❌ Payment rejected")
        await query.edit_message_text("Rejected")

# ════════════════════════════════════════════════════════════════
# 🚀 MAIN
# ════════════════════════════════════════════════════════════════

async def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    load_users()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("addalert", cmd_addalert))
    app.add_handler(CommandHandler("listalerts", cmd_listalerts))
    app.add_handler(CommandHandler("removealert", cmd_removealert))

    app.add_handler(CallbackQueryHandler(handle_callback))

    await fetch_active_symbols()

    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())





















# # ── LOAD .env FILE (local testing only) ───
# from dotenv import load_dotenv
# load_dotenv()

# import asyncio
# import json
# import logging
# import os
# import uuid
# import websockets
# from telegram import Update
# from telegram.ext import (
#     Application,
#     CommandHandler,
#     ContextTypes,
#     MessageHandler,
#     filters,
# )

# # ── CONFIGURATION ─────────────────────────────────────────────────────
# TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
# DERIV_APP_ID       = os.environ.get("DERIV_APP_ID", "1089")
# DERIV_API_TOKEN    = os.environ.get("DERIV_API_TOKEN")
# DERIV_WS_URL       = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
# ALERTS_FILE        = "alerts.json"

# # ── LOGGING ───────────────────────────────────────────────────────────
# logging.basicConfig(
#     format="%(asctime)s [%(levelname)s] %(message)s",
#     level=logging.INFO
# )
# logger = logging.getLogger(__name__)

# # ── GLOBAL STATE ──────────────────────────────────────────────────────
# symbol_cache: dict      = {}
# alerts: dict            = {}
# subscribed_symbols: set = set()

# # ══════════════════════════════════════════════════════════════════════
# # ALERT FILE FUNCTIONS
# # ══════════════════════════════════════════════════════════════════════

# def load_alerts() -> dict:
#     if os.path.exists(ALERTS_FILE):
#         with open(ALERTS_FILE, "r") as f:
#             return json.load(f)
#     return {}

# def save_alerts(data: dict):
#     with open(ALERTS_FILE, "w") as f:
#         json.dump(data, f, indent=2)

# # ══════════════════════════════════════════════════════════════════════
# # DERIV WEBSOCKET FUNCTIONS
# # ══════════════════════════════════════════════════════════════════════

# async def authorize(ws):
#     if not DERIV_API_TOKEN:
#         return
#     await ws.send(json.dumps({"authorize": DERIV_API_TOKEN}))
#     resp = json.loads(await ws.recv())
#     if "error" in resp:
#         logger.error(f"Auth error: {resp['error']['message']}")
#     else:
#         logger.info("✅ Deriv authorized")

# async def fetch_active_symbols():
#     global symbol_cache
#     logger.info("Fetching symbols from Deriv...")
#     try:
#         async with websockets.connect(DERIV_WS_URL) as ws:
#             await authorize(ws)
#             await ws.send(json.dumps({
#                 "active_symbols": "brief",
#                 "product_type": "basic"
#             }))
#             msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
#             if "error" in msg:
#                 logger.error(f"Symbol fetch error: {msg['error']['message']}")
#                 return
#             for s in msg.get("active_symbols", []):
#                 symbol_cache[s["symbol"]] = {
#                     "display_name": s.get("display_name", s["symbol"]),
#                     "market":       s.get("market_display_name", "Other"),
#                 }
#             logger.info(f"✅ Loaded {len(symbol_cache)} symbols")
#     except Exception as e:
#         logger.error(f"fetch_active_symbols failed: {e}")

# async def watch_symbol(symbol: str, app: Application):
#     logger.info(f"[{symbol}] Watcher started")
#     while True:
#         try:
#             async with websockets.connect(DERIV_WS_URL, ping_interval=30) as ws:
#                 await authorize(ws)
#                 await ws.send(json.dumps({
#                     "ticks": symbol,
#                     "subscribe": 1
#                 }))
#                 logger.info(f"[{symbol}] Subscribed to ticks")
#                 async for raw in ws:
#                     msg = json.loads(raw)
#                     if "error" in msg:
#                         logger.error(f"[{symbol}] {msg['error']['message']}")
#                         break
#                     if msg.get("msg_type") == "tick":
#                         current_price = msg["tick"]["quote"]
#                         await check_alerts(symbol, current_price, app)
#                         still_needed = any(
#                             a["symbol"] == symbol and not a["triggered"]
#                             for a in alerts.values()
#                         )
#                         if not still_needed:
#                             logger.info(f"[{symbol}] No alerts left — stopping watcher")
#                             subscribed_symbols.discard(symbol)
#                             return
#         except Exception as e:
#             logger.error(f"[{symbol}] Connection error: {e} — reconnecting in 5s...")
#         await asyncio.sleep(5)

# async def check_alerts(symbol: str, current_price: float, app: Application):
#     triggered_ids = []
#     for aid, a in alerts.items():
#         if a["symbol"] != symbol or a["triggered"]:
#             continue
#         target = a["price"]
#         last_price = a.get("last_price")
#         touched = abs(current_price - target) / target <= 0.0001
#         crossed = (
#             last_price is not None and
#             ((last_price < target <= current_price) or
#              (last_price > target >= current_price))
#         )
#         if touched or crossed:
#             a["triggered"] = True
#             triggered_ids.append(aid)
#             display = a.get("display_name", symbol)
#             try:
#                 await app.bot.send_message(
#                     chat_id=int(a["chat_id"]),
#                     text=(
#                         f"🔔 *PRICE ALERT TRIGGERED!*\n\n"
#                         f"📊 *Symbol:* `{symbol}` ({display})\n"
#                         f"🎯 *Your Target:* `{target}`\n"
#                         f"💰 *Current Price:* `{current_price}`\n\n"
#                         f"🆔 Alert ID: `{aid}`"
#                     ),
#                     parse_mode="Markdown"
#                 )
#             except Exception as e:
#                 logger.error(f"Failed to send alert {aid}: {e}")
#         else:
#             a["last_price"] = current_price
#     if triggered_ids:
#         save_alerts(alerts)

# # ══════════════════════════════════════════════════════════════════════
# # TELEGRAM COMMAND HANDLERS
# # ══════════════════════════════════════════════════════════════════════

# HELP_TEXT = (
#     "👋 *Welcome to Deriv Alert Bot!*\n\n"
#     "➤ `/addalert SYMBOL PRICE` - Set alert\n"
#     "➤ `/listalerts` - View alerts\n"
#     "➤ `/removealert ID` - Delete alert\n"
#     "➤ `/symbols` - Browse pairs\n"
#     "➤ `/search KEYWORD` - Search pairs"
# )

# async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
#     await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")

# async def cmd_addalert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
#     if not ctx.args or len(ctx.args) != 2:
#         await update.message.reply_text("Usage: `/addalert SYMBOL PRICE`", parse_mode="Markdown")
#         return
#     symbol = ctx.args[0].upper()
#     if symbol_cache and symbol not in symbol_cache:
#         await update.message.reply_text(f"❌ Symbol `{symbol}` not found.", parse_mode="Markdown")
#         return
#     try:
#         price = float(ctx.args[1])
#     except ValueError:
#         await update.message.reply_text("❌ Invalid price.", parse_mode="Markdown")
#         return

#     aid = str(uuid.uuid4())[:6].upper()
#     alerts[aid] = {
#         "symbol": symbol,
#         "display_name": symbol_cache.get(symbol, {}).get("display_name", symbol),
#         "price": price,
#         "chat_id": str(update.effective_chat.id),
#         "triggered": False,
#         "last_price": None
#     }
#     save_alerts(alerts)
#     if symbol not in subscribed_symbols:
#         subscribed_symbols.add(symbol)
#         asyncio.create_task(watch_symbol(symbol, ctx.application))
#     await update.message.reply_text(f"✅ Alert Set! ID: `{aid}`", parse_mode="Markdown")

# async def cmd_listalerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
#     chat_id = str(update.effective_chat.id)
#     user_alerts = {aid: a for aid, a in alerts.items() if a["chat_id"] == chat_id and not a["triggered"]}
#     if not user_alerts:
#         await update.message.reply_text("📭 No active alerts.")
#         return
#     msg = "\n".join([f"🎯 `{aid}` - `{a['symbol']}` @ `{a['price']}`" for aid, a in user_alerts.items()])
#     await update.message.reply_text(f"*Active Alerts:*\n{msg}", parse_mode="Markdown")

# async def cmd_removealert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
#     if not ctx.args: return
#     aid = ctx.args[0].upper()
#     if aid in alerts:
#         del alerts[aid]
#         save_alerts(alerts)
#         await update.message.reply_text(f"🗑️ Removed `{aid}`")

# # ✅ SYMBOLS (90)
# async def cmd_symbols(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
#     if not symbol_cache:
#         await update.message.reply_text("⏳ Loading...")
#         return
#     text = "\n".join([
#         f"`{s}` - {info['display_name']}"
#         for s, info in list(symbol_cache.items())[:90]
#     ])
#     await update.message.reply_text(f"*Symbols (Top 90):*\n{text}", parse_mode="Markdown")

# # ✅ SEARCH (SMART)
# async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
#     if not ctx.args: return
#     kw = ctx.args[0].lower()

#     results = [
#         f"`{s}` - {info['display_name']}"
#         for s, info in symbol_cache.items()
#         if kw in s.lower() or kw in info["display_name"].lower()
#     ][:40]

#     await update.message.reply_text(
#         f"*Search Results:*\n" + "\n".join(results)
#         if results else "No results.",
#         parse_mode="Markdown"
#     )

# # ══════════════════════════════════════════════════════════════════════
# # STARTUP & MAIN
# # ══════════════════════════════════════════════════════════════════════

# async def on_startup(app: Application):
#     global alerts
#     alerts = load_alerts()
#     await fetch_active_symbols()
#     for sym in set(a["symbol"] for a in alerts.values() if not a["triggered"]):
#         if sym not in subscribed_symbols:
#             subscribed_symbols.add(sym)
#             asyncio.create_task(watch_symbol(sym, app))
#     logger.info("✅ Bot ready")

# async def main():
#     if not TELEGRAM_BOT_TOKEN:
#         raise ValueError("TELEGRAM_BOT_TOKEN missing!")

#     app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

#     # 🔥 FIX: kill old sessions
#     await app.bot.delete_webhook(drop_pending_updates=True)
    
#     app.add_handler(CommandHandler("start", cmd_start))
#     app.add_handler(CommandHandler("addalert", cmd_addalert))
#     app.add_handler(CommandHandler("listalerts", cmd_listalerts))
#     app.add_handler(CommandHandler("removealert", cmd_removealert))
#     app.add_handler(CommandHandler("symbols", cmd_symbols))
#     app.add_handler(CommandHandler("search", cmd_search))

#     await app.initialize()
#     await on_startup(app)
#     await app.updater.start_polling()
#     await app.start()
    
#     logger.info("🚀 Bot is live.")
#     while True:
#         await asyncio.sleep(3600)

# if __name__ == "__main__":
#     try:
#         asyncio.run(main())
#     except (KeyboardInterrupt, SystemExit):
#         pass
