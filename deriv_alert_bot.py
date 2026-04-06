"""
Deriv Telegram Price Alert Bot (UPDATED WITH API TOKEN)
"""

import asyncio
import json
import logging
import os
import uuid

import websockets
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_TOKEN_HERE")
DERIV_APP_ID       = os.environ.get("DERIV_APP_ID", "1089")
DERIV_API_TOKEN    = os.environ.get("DERIV_API_TOKEN", "YOUR_TOKEN_HERE")

DERIV_WS_URL = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
ALERTS_FILE  = "alerts.json"

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

symbol_cache = {}
alerts = {}
subscribed_symbols = set()


def load_alerts():
    if os.path.exists(ALERTS_FILE):
        with open(ALERTS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_alerts(data):
    with open(ALERTS_FILE, "w") as f:
        json.dump(data, f, indent=2)


# =========================
# FETCH SYMBOLS (UPDATED)
# =========================
async def fetch_active_symbols():
    global symbol_cache
    logger.info("Fetching active symbols...")

    try:
        async with websockets.connect(DERIV_WS_URL) as ws:

            # ✅ AUTHORIZE FIRST
            await ws.send(json.dumps({
                "authorize": DERIV_API_TOKEN
            }))

            await ws.recv()  # auth response

            # ✅ THEN REQUEST SYMBOLS
            await ws.send(json.dumps({
                "active_symbols": "brief",
                "product_type": "basic"
            }))

            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            msg = json.loads(raw)

            cache = {}
            for s in msg.get("active_symbols", []):
                symbol = s.get("symbol", "")
                cache[symbol] = {
                    "display_name": s.get("display_name", symbol),
                    "market": s.get("market_display_name", "Other"),
                }

            symbol_cache = cache
            logger.info(f"Loaded {len(symbol_cache)} symbols")

    except Exception as e:
        logger.error(f"Symbol fetch error: {e}")


# =========================
# WATCH PRICE (UPDATED)
# =========================
async def watch_symbol(symbol, app):
    logger.info(f"[{symbol}] Watching...")

    while True:
        try:
            async with websockets.connect(DERIV_WS_URL) as ws:

                # ✅ AUTHORIZE FIRST
                await ws.send(json.dumps({
                    "authorize": DERIV_API_TOKEN
                }))
                await ws.recv()

                # ✅ SUBSCRIBE TO PRICE
                await ws.send(json.dumps({
                    "ticks": symbol,
                    "subscribe": 1
                }))

                async for raw in ws:
                    msg = json.loads(raw)

                    if msg.get("msg_type") == "tick":
                        price = msg["tick"]["quote"]
                        await check_alerts(symbol, price, app)

        except Exception as e:
            logger.error(f"{symbol} error: {e}")
            await asyncio.sleep(5)


# =========================
# ALERT CHECK
# =========================
async def check_alerts(symbol, price, app):
    for aid, a in alerts.items():
        if a["symbol"] == symbol and not a["triggered"]:
            if abs(price - a["price"]) < 0.0001:

                a["triggered"] = True
                save_alerts(alerts)

                await app.bot.send_message(
                    chat_id=int(a["chat_id"]),
                    text=f"🔔 {symbol} hit {price}"
                )


# =========================
# COMMANDS
# =========================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot ready 🚀")


async def cmd_addalert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        symbol = ctx.args[0].upper()
        price = float(ctx.args[1])
    except:
        await update.message.reply_text("Usage: /addalert SYMBOL PRICE")
        return

    aid = str(uuid.uuid4())[:6]
    alerts[aid] = {
        "symbol": symbol,
        "price": price,
        "chat_id": str(update.effective_chat.id),
        "triggered": False
    }

    save_alerts(alerts)

    if symbol not in subscribed_symbols:
        subscribed_symbols.add(symbol)
        asyncio.create_task(watch_symbol(symbol, ctx.application))

    await update.message.reply_text(f"Alert set {symbol} @ {price}")


# =========================
# STARTUP
# =========================
async def on_startup(app):
    global alerts
    alerts = load_alerts()
    await fetch_active_symbols()


def main():

    if TELEGRAM_BOT_TOKEN == "YOUR_TOKEN_HERE":
        raise ValueError("Set TELEGRAM_BOT_TOKEN")

    if DERIV_API_TOKEN == "YOUR_TOKEN_HERE":
        raise ValueError("Set DERIV_API_TOKEN")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(on_startup).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("addalert", cmd_addalert))

    app.run_polling()


if __name__ == "__main__":
    main()