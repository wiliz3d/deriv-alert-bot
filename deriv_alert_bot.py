"""
Deriv Telegram Price Alert Bot
================================
IMPORTANT: Never hardcode tokens. Always use environment variables.

Set these on Railway dashboard → Variables tab:
  TELEGRAM_BOT_TOKEN  = your token from @BotFather
  DERIV_APP_ID        = numeric app ID (optional, default = 1089)
  DERIV_API_TOKEN     = your PAT token (optional)
"""

from dotenv import load_dotenv
load_dotenv()

import asyncio
import json
import logging
import os
import uuid

import websockets
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ── Config (from environment variables) ──────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DERIV_APP_ID       = os.environ.get("DERIV_APP_ID", "32VehTuPdWhk0vu3Ob8ed")   # default public app id
DERIV_API_TOKEN    = os.environ.get("DERIV_API_TOKEN")        # optional
DERIV_WS_URL       = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
ALERTS_FILE        = "alerts.json"
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

symbol_cache = {}
alerts = {}
subscribed_symbols = set()


# ── Alert persistence ─────────────────────────────────────────────────────────

def load_alerts():
    """Load alerts from file"""
    if os.path.exists(ALERTS_FILE):
        with open(ALERTS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_alerts(data):
    """Save alerts to file"""
    with open(ALERTS_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ── Deriv helpers ─────────────────────────────────────────────────────────────

async def authorize(ws):
    """
    Authorize WebSocket using API token.
    If no token is provided → skip (public data still works).
    """
    if not DERIV_API_TOKEN:
        return

    await ws.send(json.dumps({"authorize": DERIV_API_TOKEN}))
    resp = json.loads(await ws.recv())

    if "error" in resp:
        logger.error(f"Auth error: {resp['error']['message']}")
    else:
        logger.info("✅ Deriv authorized successfully.")


async def fetch_active_symbols():
    """Fetch all tradable symbols from Deriv"""
    global symbol_cache
    logger.info("Fetching active symbols from Deriv...")

    try:
        async with websockets.connect(DERIV_WS_URL) as ws:
            await authorize(ws)

            await ws.send(json.dumps({
                "active_symbols": "brief",
                "product_type": "basic"
            }))

            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            msg = json.loads(raw)

            if "error" in msg:
                logger.error(f"Symbol fetch error: {msg['error']['message']}")
                return

            cache = {}
            for s in msg.get("active_symbols", []):
                sym = s.get("symbol", "")
                cache[sym] = {
                    "display_name": s.get("display_name", sym),
                    "market": s.get("market_display_name", "Other"),
                }

            symbol_cache = cache
            logger.info(f"✅ Loaded {len(symbol_cache)} symbols.")

    except Exception as e:
        logger.error(f"fetch_active_symbols error: {e}")


async def watch_symbol(symbol, app):
    """Watch live price for a symbol and trigger alerts"""
    logger.info(f"[{symbol}] Watcher started.")

    while True:
        try:
            async with websockets.connect(DERIV_WS_URL, ping_interval=30) as ws:
                await authorize(ws)

                await ws.send(json.dumps({
                    "ticks": symbol,
                    "subscribe": 1
                }))

                logger.info(f"[{symbol}] Subscribed to ticks.")

                async for raw in ws:
                    msg = json.loads(raw)

                    if "error" in msg:
                        logger.error(f"[{symbol}] {msg['error']['message']}")
                        break

                    if msg.get("msg_type") == "tick":
                        current = msg["tick"]["quote"]
                        await check_alerts(symbol, current, app)

                        # Stop watcher if no alerts left
                        if not any(a["symbol"] == symbol and not a["triggered"] for a in alerts.values()):
                            logger.info(f"[{symbol}] No active alerts — stopping watcher.")
                            subscribed_symbols.discard(symbol)
                            return

        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"[{symbol}] Disconnected. Reconnecting in 5s...")
        except Exception as e:
            logger.error(f"[{symbol}] Error: {e}. Reconnecting in 5s...")

        await asyncio.sleep(5)


async def check_alerts(symbol, current_price, app):
    """Check if price hits alert target"""
    triggered = []

    for aid, a in alerts.items():
        if a["symbol"] != symbol or a["triggered"]:
            continue

        target = a["price"]
        last_price = a.get("last_price")

        # Smart hit detection (handles crossing + precision)
        hit = (abs(current_price - target) / target <= 0.0001) or \
              (last_price is not None and price_crossed(last_price, current_price, target))

        if hit:
            a["triggered"] = True
            triggered.append(aid)

            try:
                await app.bot.send_message(
                    chat_id=int(a["chat_id"]),
                    text=(
                        f"🔔 *PRICE ALERT TRIGGERED!*\n\n"
                        f"📊 *Symbol:* `{symbol}`\n"
                        f"🎯 *Target:* `{target}`\n"
                        f"💰 *Current:* `{current_price}`\n\n"
                        f"🆔 ID: `{aid}`"
                    ),
                    parse_mode="Markdown"
                )

                logger.info(f"[{symbol}] Alert {aid} triggered @ {current_price}")

            except Exception as e:
                logger.error(f"Send error: {e}")

        else:
            a["last_price"] = current_price

    if triggered:
        save_alerts(alerts)


def price_crossed(prev, curr, target):
    """Detect if price crossed target between ticks"""
    return (prev < target <= curr) or (prev > target >= curr)


# ── Telegram commands ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Bot ready. Use /addalert SYMBOL PRICE")


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
        "triggered": False,
        "last_price": None
    }

    save_alerts(alerts)

    if symbol not in subscribed_symbols:
        subscribed_symbols.add(symbol)
        asyncio.create_task(watch_symbol(symbol, ctx.application))

    await update.message.reply_text(f"✅ Alert set {symbol} @ {price}")


# ── Startup ───────────────────────────────────────────────────────────────────

async def on_startup(app):
    global alerts
    alerts = load_alerts()
    await fetch_active_symbols()
    logger.info("✅ Bot ready.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Only REQUIRED variable
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN is not set!")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(on_startup)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("addalert", cmd_addalert))

    logger.info("🚀 Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()