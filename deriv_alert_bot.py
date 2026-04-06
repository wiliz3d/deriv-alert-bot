"""
Deriv Telegram Price Alert Bot
================================
Multi-user | 24/7 | Deployable on Railway (free)

Commands:
  /start                   - Welcome message
  /addalert SYMBOL PRICE   - Add a price alert  e.g. /addalert frxEURUSD 1.0850
  /listalerts              - List your active alerts
  /removealert ID          - Remove an alert by ID
  /symbols                 - Show common Deriv symbol names

Setup:
  1. Set environment variables (on Railway dashboard):
       TELEGRAM_BOT_TOKEN = your token from @BotFather
       DERIV_APP_ID       = your app id from developers.deriv.com (or use 1089 for testing)

  2. Deploy to Railway (see DEPLOY.md)
"""

import asyncio
import json
import logging
import os
import uuid

import websockets
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ─────────────────────────────────────────────
#  CONFIG — set these as env vars on Railway
# ─────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_TOKEN_HERE")
DERIV_APP_ID       = os.environ.get("DERIV_APP_ID", "1089")
DERIV_WS_URL       = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"
ALERTS_FILE        = "alerts.json"   # persists alerts across restarts
# ─────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

SYMBOL_HELP = """
*Common Deriv Symbol Names*

*Forex:*
  `frxEURUSD`  `frxGBPUSD`  `frxUSDJPY`
  `frxUSDCAD`  `frxAUDUSD`  `frxGBPJPY`

*Synthetic Indices:*
  `R_10`    Volatility 10
  `R_25`    Volatility 25
  `R_50`    Volatility 50
  `R_75`    Volatility 75
  `R_100`   Volatility 100
  `BOOM500`    `BOOM1000`
  `CRASH500`   `CRASH1000`
  `stpRNG`  Step Index

*Metals:*
  `frxXAUUSD`  Gold/USD
  `frxXAGUSD`  Silver/USD
"""

# ─── Alert storage ────────────────────────────

def load_alerts() -> dict:
    if os.path.exists(ALERTS_FILE):
        with open(ALERTS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_alerts(alerts: dict):
    with open(ALERTS_FILE, "w") as f:
        json.dump(alerts, f, indent=2)

alerts: dict = load_alerts()
subscribed_symbols: set = set()


# ─── Telegram handlers ────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Deriv Price Alert Bot*\n\n"
        "I'll notify you the moment price hits your zone — like TradingView alerts but straight to Telegram.\n\n"
        "*Commands:*\n"
        "  `/addalert SYMBOL PRICE` — set an alert\n"
        "  `/listalerts` — see your active alerts\n"
        "  `/removealert ID` — delete an alert\n"
        "  `/symbols` — see all supported symbols\n\n"
        "*Example:*\n"
        "`/addalert frxEURUSD 1.0850`\n"
        "`/addalert R_100 1520.50`",
        parse_mode="Markdown"
    )

async def cmd_symbols(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(SYMBOL_HELP, parse_mode="Markdown")

async def cmd_addalert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) != 2:
        await update.message.reply_text(
            "❌ Wrong format.\n\nUsage: `/addalert SYMBOL PRICE`\nExample: `/addalert frxEURUSD 1.0850`",
            parse_mode="Markdown"
        )
        return

    symbol = args[0].upper()
    try:
        price = float(args[1])
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid price. Use a number like `1.0850` or `1520.00`",
            parse_mode="Markdown"
        )
        return

    alert_id = str(uuid.uuid4())[:6].upper()
    chat_id  = str(update.effective_chat.id)
    username = update.effective_user.username or update.effective_user.first_name

    alerts[alert_id] = {
        "symbol":     symbol,
        "price":      price,
        "chat_id":    chat_id,
        "username":   username,
        "triggered":  False,
        "last_price": None
    }
    save_alerts(alerts)

    # Subscribe to this symbol if not already watching it
    if symbol not in subscribed_symbols:
        subscribed_symbols.add(symbol)
        asyncio.create_task(watch_symbol(symbol, ctx.application))

    await update.message.reply_text(
        f"✅ *Alert Set!*\n\n"
        f"🆔 ID: `{alert_id}`\n"
        f"📊 Symbol: `{symbol}`\n"
        f"🎯 Target Price: `{price}`\n\n"
        f"I'll ping you the moment price touches that level.",
        parse_mode="Markdown"
    )

async def cmd_listalerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user_alerts = {
        k: v for k, v in alerts.items()
        if v["chat_id"] == chat_id and not v["triggered"]
    }

    if not user_alerts:
        await update.message.reply_text(
            "📭 You have no active alerts.\n\nUse `/addalert SYMBOL PRICE` to set one.",
            parse_mode="Markdown"
        )
        return

    lines = [f"*Your Active Alerts ({len(user_alerts)}):*\n"]
    for aid, a in user_alerts.items():
        lines.append(f"🎯 `{aid}` — *{a['symbol']}* @ `{a['price']}`")

    lines.append("\nUse `/removealert ID` to cancel one.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_removealert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text(
            "Usage: `/removealert ALERT_ID`\nGet your IDs from /listalerts",
            parse_mode="Markdown"
        )
        return

    alert_id = ctx.args[0].upper()
    chat_id  = str(update.effective_chat.id)

    if alert_id in alerts and alerts[alert_id]["chat_id"] == chat_id:
        symbol = alerts[alert_id]["symbol"]
        del alerts[alert_id]
        save_alerts(alerts)
        await update.message.reply_text(
            f"🗑️ Alert `{alert_id}` for `{symbol}` has been removed.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "❌ Alert not found. Use /listalerts to see your active alert IDs.",
            parse_mode="Markdown"
        )


# ─── Deriv WebSocket price watcher ───────────

async def watch_symbol(symbol: str, app: Application):
    """Connects to Deriv WebSocket and watches live ticks for a symbol."""
    logger.info(f"[{symbol}] Starting price watcher...")

    while True:
        try:
            async with websockets.connect(DERIV_WS_URL, ping_interval=30) as ws:
                await ws.send(json.dumps({"ticks": symbol, "subscribe": 1}))
                logger.info(f"[{symbol}] Subscribed to ticks.")

                async for raw in ws:
                    msg = json.loads(raw)

                    if "error" in msg:
                        logger.error(f"[{symbol}] Deriv error: {msg['error']['message']}")
                        break

                    if msg.get("msg_type") == "tick":
                        current_price = msg["tick"]["quote"]
                        await check_alerts_for_symbol(symbol, current_price, app)

        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"[{symbol}] Connection closed. Reconnecting in 5s...")
        except Exception as e:
            logger.error(f"[{symbol}] Unexpected error: {e}. Reconnecting in 5s...")

        await asyncio.sleep(5)


async def check_alerts_for_symbol(symbol: str, current_price: float, app: Application):
    """Fire alerts when price crosses or touches a target zone."""
    triggered_ids = []

    for alert_id, alert in alerts.items():
        if alert["symbol"] != symbol or alert["triggered"]:
            continue

        target     = alert["price"]
        last_price = alert.get("last_price")

        hit = False

        # Direct touch (within 0.01% tolerance)
        if abs(current_price - target) / target <= 0.0001:
            hit = True
        # Price crossed the level between last tick and this tick
        elif last_price and price_crossed(last_price, current_price, target):
            hit = True

        if hit:
            alert["triggered"] = True
            triggered_ids.append(alert_id)

            direction = "📈 Price moved UP to" if current_price >= target else "📉 Price moved DOWN to"

            try:
                await app.bot.send_message(
                    chat_id=int(alert["chat_id"]),
                    text=(
                        f"🔔 *PRICE ALERT TRIGGERED!*\n\n"
                        f"📊 *Symbol:* `{symbol}`\n"
                        f"🎯 *Your Target:* `{target}`\n"
                        f"💰 *Current Price:* `{current_price}`\n"
                        f"↕️ {direction} your zone\n\n"
                        f"🆔 Alert ID: `{alert_id}`"
                    ),
                    parse_mode="Markdown"
                )
                logger.info(f"[{symbol}] Alert {alert_id} fired @ {current_price}")
            except Exception as e:
                logger.error(f"Failed to send alert {alert_id}: {e}")
        else:
            alert["last_price"] = current_price

    if triggered_ids:
        save_alerts(alerts)


def price_crossed(prev: float, curr: float, target: float) -> bool:
    """True if price passed through the target between two ticks."""
    return (prev < target <= curr) or (prev > target >= curr)


# ─── Startup: re-subscribe to symbols that had alerts before restart ──

async def resume_subscriptions(app: Application):
    """On bot start, re-watch any symbols that have pending alerts."""
    symbols_needed = set(
        v["symbol"] for v in alerts.values() if not v["triggered"]
    )
    for symbol in symbols_needed:
        if symbol not in subscribed_symbols:
            subscribed_symbols.add(symbol)
            asyncio.create_task(watch_symbol(symbol, app))
            logger.info(f"Resumed watcher for {symbol}")


# ─── Main ─────────────────────────────────────

def main():
    if TELEGRAM_BOT_TOKEN == "YOUR_TOKEN_HERE":
        raise ValueError("Set your TELEGRAM_BOT_TOKEN environment variable!")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(resume_subscriptions)
        .build()
    )

    app.add_handler(CommandHandler("start",        cmd_start))
    app.add_handler(CommandHandler("help",         cmd_start))
    app.add_handler(CommandHandler("symbols",      cmd_symbols))
    app.add_handler(CommandHandler("addalert",     cmd_addalert))
    app.add_handler(CommandHandler("listalerts",   cmd_listalerts))
    app.add_handler(CommandHandler("removealert",  cmd_removealert))

    logger.info("✅ Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
