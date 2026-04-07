"""
Deriv Telegram Price Alert Bot
================================

FEATURES:
- Real-time price alerts via Deriv WebSocket
- Price-crossing detection (won't miss alerts between ticks)
- Auto-stop watcher when no more alerts for a symbol
- Multiple alerts per user
- Full symbol browser + search
- Greeting help menu (hi/hello/hey)
- Alerts saved to file (survive restarts)

HOW TO RUN LOCALLY:
  1. Create a .env file in this folder:
       TELEGRAM_BOT_TOKEN=your_token_here
       DERIV_APP_ID=1089
       DERIV_API_TOKEN=your_pat_token (optional)

  2. Install dependencies:
       pip install python-telegram-bot websockets python-dotenv

  3. Run:
       python deriv_alert_bot.py

HOW TO DEPLOY (Railway):
  - Upload to GitHub
  - Connect repo on railway.app
  - Add env variables in Railway Variables tab (NOT in code or .env)
  - Done — runs 24/7
"""

# ── LOAD .env FILE (local testing only — Railway uses its own vars) ───
from dotenv import load_dotenv
load_dotenv()

import asyncio
import json
import logging
import os
import uuid

import websockets
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ── CONFIGURATION ─────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DERIV_APP_ID       = os.environ.get("DERIV_APP_ID", "1089")   # must be numeric
DERIV_API_TOKEN    = os.environ.get("DERIV_API_TOKEN")         # optional
DERIV_WS_URL       = f"wss://ws.derivws.com/websockets/v3?app_id={DERIV_APP_ID}"
ALERTS_FILE        = "alerts.json"

# ── LOGGING ───────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── GLOBAL STATE ──────────────────────────────────────────────────────
symbol_cache: dict      = {}   # {symbol: {display_name, market}}
alerts: dict            = {}   # {alert_id: {symbol, price, chat_id, ...}}
subscribed_symbols: set = set()


# ══════════════════════════════════════════════════════════════════════
#  ALERT FILE FUNCTIONS
# ══════════════════════════════════════════════════════════════════════

def load_alerts() -> dict:
    """Load saved alerts from disk."""
    if os.path.exists(ALERTS_FILE):
        with open(ALERTS_FILE, "r") as f:
            return json.load(f)
    return {}

def save_alerts(data: dict):
    """Save alerts to disk so they survive restarts."""
    with open(ALERTS_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ══════════════════════════════════════════════════════════════════════
#  DERIV WEBSOCKET FUNCTIONS
# ══════════════════════════════════════════════════════════════════════

async def authorize(ws):
    """
    Authorize with Deriv API token (optional).
    Public data like ticks and symbol lists works WITHOUT a token.
    A token is only needed for private account data.
    """
    if not DERIV_API_TOKEN:
        return  # skip — public access is fine for price alerts

    await ws.send(json.dumps({"authorize": DERIV_API_TOKEN}))
    resp = json.loads(await ws.recv())

    if "error" in resp:
        logger.error(f"Auth error: {resp['error']['message']}")
    else:
        logger.info("✅ Deriv authorized")


async def fetch_active_symbols():
    """
    On startup: connect to Deriv and download the full
    list of tradeable symbols into symbol_cache.
    This means /symbols and /search always show live data.
    """
    global symbol_cache
    logger.info("Fetching symbols from Deriv...")

    try:
        async with websockets.connect(DERIV_WS_URL) as ws:
            await authorize(ws)

            await ws.send(json.dumps({
                "active_symbols": "brief",
                "product_type": "basic"
            }))

            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))

            if "error" in msg:
                logger.error(f"Symbol fetch error: {msg['error']['message']}")
                return

            for s in msg.get("active_symbols", []):
                symbol_cache[s["symbol"]] = {
                    "display_name": s.get("display_name", s["symbol"]),
                    "market":       s.get("market_display_name", "Other"),
                }

            logger.info(f"✅ Loaded {len(symbol_cache)} symbols")

    except Exception as e:
        logger.error(f"fetch_active_symbols failed: {e}")


async def watch_symbol(symbol: str, app: Application):
    """
    Background task: streams live price ticks for one symbol.
    - Auto-reconnects if Deriv drops the connection
    - Stops automatically when no more alerts exist for this symbol
    """
    logger.info(f"[{symbol}] Watcher started")

    while True:
        try:
            async with websockets.connect(DERIV_WS_URL, ping_interval=30) as ws:
                await authorize(ws)

                await ws.send(json.dumps({
                    "ticks": symbol,
                    "subscribe": 1
                }))

                logger.info(f"[{symbol}] Subscribed to ticks")

                async for raw in ws:
                    msg = json.loads(raw)

                    if "error" in msg:
                        logger.error(f"[{symbol}] {msg['error']['message']}")
                        break

                    if msg.get("msg_type") == "tick":
                        current_price = msg["tick"]["quote"]
                        await check_alerts(symbol, current_price, app)

                        # ── STOP CONDITION ──────────────────────────
                        # If no more active alerts for this symbol,
                        # shut down this watcher to save resources
                        still_needed = any(
                            a["symbol"] == symbol and not a["triggered"]
                            for a in alerts.values()
                        )
                        if not still_needed:
                            logger.info(f"[{symbol}] No alerts left — stopping watcher")
                            subscribed_symbols.discard(symbol)
                            return

        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"[{symbol}] Disconnected — reconnecting in 5s...")
        except Exception as e:
            logger.error(f"[{symbol}] Error: {e} — reconnecting in 5s...")

        await asyncio.sleep(5)


async def check_alerts(symbol: str, current_price: float, app: Application):
    """
    Called on every price tick.
    Two ways an alert fires:
      1. Price is within 0.01% of target (direct touch)
      2. Price crossed the target between last tick and this tick
         e.g. target=1.08, last=1.079, current=1.081 → crossed it
    This means no alert is ever missed between ticks.
    """
    triggered_ids = []

    for aid, a in alerts.items():
        if a["symbol"] != symbol or a["triggered"]:
            continue

        target     = a["price"]
        last_price = a.get("last_price")

        # Method 1: direct touch (percentage-based, works for all pairs)
        touched = abs(current_price - target) / target <= 0.0001

        # Method 2: price crossed the level between ticks
        crossed = (
            last_price is not None and
            ((last_price < target <= current_price) or
             (last_price > target >= current_price))
        )

        if touched or crossed:
            a["triggered"] = True
            triggered_ids.append(aid)

            display = a.get("display_name", symbol)

            try:
                await app.bot.send_message(
                    chat_id=int(a["chat_id"]),
                    text=(
                        f"🔔 *PRICE ALERT TRIGGERED!*\n\n"
                        f"📊 *Symbol:* `{symbol}` ({display})\n"
                        f"🎯 *Your Target:* `{target}`\n"
                        f"💰 *Current Price:* `{current_price}`\n\n"
                        f"🆔 Alert ID: `{aid}`\n\n"
                        f"Set a new alert with /addalert"
                    ),
                    parse_mode="Markdown"
                )
                logger.info(f"[{symbol}] Alert {aid} fired @ {current_price}")
            except Exception as e:
                logger.error(f"Failed to send alert {aid}: {e}")

        else:
            # Save last price for crossing detection next tick
            a["last_price"] = current_price

    if triggered_ids:
        save_alerts(alerts)


# ══════════════════════════════════════════════════════════════════════
#  TELEGRAM COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════

HELP_TEXT = (
    "👋 *Welcome to Deriv Alert Bot!*\n\n"
    "📌 *Commands:*\n\n"
    "➤ `/addalert SYMBOL PRICE`\n"
    "Set a price alert\n"
    "_Example:_ `/addalert frxEURUSD 1.0850`\n"
    "_Example:_ `/addalert R_100 1520.50`\n\n"
    "➤ `/listalerts`\n"
    "View your active alerts\n\n"
    "➤ `/removealert ID`\n"
    "Delete an alert by its ID\n\n"
    "➤ `/symbols`\n"
    "Browse all available Deriv pairs\n\n"
    "➤ `/search KEYWORD`\n"
    "Search for a symbol\n"
    "_Example:_ `/search boom` or `/search EUR`\n\n"
    "🚀 I'll notify you the moment price hits your zone!"
)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")

async def cmd_greeting(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def cmd_addalert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Set a new price alert."""
    if not ctx.args or len(ctx.args) != 2:
        await update.message.reply_text(
            "Usage: `/addalert SYMBOL PRICE`\nExample: `/addalert frxEURUSD 1.0850`\n\nUse /search to find symbols.",
            parse_mode="Markdown"
        )
        return

    symbol = ctx.args[0].upper()

    # Validate symbol exists on Deriv
    if symbol_cache and symbol not in symbol_cache:
        await update.message.reply_text(
            f"❌ Symbol `{symbol}` not found on Deriv.\n\n"
            f"Try: `/search {ctx.args[0]}`",
            parse_mode="Markdown"
        )
        return

    try:
        price = float(ctx.args[1])
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid price. Use a number like `1.0850` or `1520`",
            parse_mode="Markdown"
        )
        return

    aid      = str(uuid.uuid4())[:6].upper()
    chat_id  = str(update.effective_chat.id)
    username = update.effective_user.username or update.effective_user.first_name
    display  = symbol_cache.get(symbol, {}).get("display_name", symbol)

    alerts[aid] = {
        "symbol":       symbol,
        "display_name": display,
        "price":        price,
        "chat_id":      chat_id,
        "username":     username,
        "triggered":    False,
        "last_price":   None,
        "repeat":       3,
    }
    save_alerts(alerts)

    # Start a background watcher for this symbol if not already running
    if symbol not in subscribed_symbols:
        subscribed_symbols.add(symbol)
        asyncio.create_task(watch_symbol(symbol, ctx.application))

    await update.message.reply_text(
        f"✅ *Alert Set!*\n\n"
        f"🆔 ID: `{aid}`\n"
        f"📊 Symbol: `{symbol}` ({display})\n"
        f"🎯 Target Price: `{price}`\n\n"
        f"I'll ping you the moment price hits that level.",
        parse_mode="Markdown"
    )


async def cmd_listalerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show all active alerts for this user."""
    chat_id = str(update.effective_chat.id)
    user_alerts = {
        aid: a for aid, a in alerts.items()
        if a["chat_id"] == chat_id and not a["triggered"]
    }

    if not user_alerts:
        await update.message.reply_text(
            "📭 No active alerts.\n\nUse `/addalert SYMBOL PRICE` to set one.",
            parse_mode="Markdown"
        )
        return

    lines = [f"*Your Active Alerts ({len(user_alerts)}):*\n"]
    for aid, a in user_alerts.items():
        display = a.get("display_name", a["symbol"])
        lines.append(f"🎯 `{aid}` — *{a['symbol']}* ({display}) @ `{a['price']}`")
    lines.append("\nUse `/removealert ID` to cancel one.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_removealert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Delete an alert by ID."""
    if not ctx.args:
        await update.message.reply_text(
            "Usage: `/removealert ID`\nGet IDs from /listalerts",
            parse_mode="Markdown"
        )
        return

    aid     = ctx.args[0].upper()
    chat_id = str(update.effective_chat.id)

    if aid in alerts and alerts[aid]["chat_id"] == chat_id:
        sym = alerts[aid]["symbol"]
        del alerts[aid]
        save_alerts(alerts)
        await update.message.reply_text(
            f"🗑️ Alert `{aid}` for `{sym}` removed.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "❌ Alert not found. Use /listalerts to see your IDs.",
            parse_mode="Markdown"
        )


async def cmd_symbols(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Show all available Deriv symbols grouped by market."""
    if not symbol_cache:
        await update.message.reply_text("⏳ Symbols still loading, try again in a moment.")
        return

    grouped = {}
    for sym, info in symbol_cache.items():
        m = info["market"]
        grouped.setdefault(m, []).append((sym, info["display_name"]))

    lines = ["*All Available Deriv Symbols*\n"]
    for market, syms in sorted(grouped.items()):
        lines.append(f"\n*{market}* ({len(syms)} pairs)")
        for sym, display in sorted(syms)[:15]:
            lines.append(f"  `{sym}` — {display}")
        if len(syms) > 15:
            lines.append(f"  _...{len(syms)-15} more. Use /search to find them._")

    lines.append(f"\n📊 *Total: {len(symbol_cache)} symbols*")
    lines.append("Use `/search KEYWORD` — e.g. `/search EUR` or `/search boom`")

    # Split into chunks — Telegram has a 4096 char limit per message
    text, chunks, cur = "\n".join(lines), [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 4000:
            chunks.append(cur)
            cur = line
        else:
            cur += ("\n" if cur else "") + line
    if cur:
        chunks.append(cur)

    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode="Markdown")


async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Search for a symbol by keyword."""
    if not ctx.args:
        await update.message.reply_text(
            "Usage: `/search KEYWORD`\nExamples: `/search EUR` `/search boom` `/search gold`",
            parse_mode="Markdown"
        )
        return

    keyword = " ".join(ctx.args).lower()
    results = [
        (sym, info["display_name"], info["market"])
        for sym, info in symbol_cache.items()
        if keyword in sym.lower() or keyword in info["display_name"].lower()
    ]

    if not results:
        await update.message.reply_text(
            f"❌ No symbols found for `{keyword}`.\n\nTry `/symbols` to browse all.",
            parse_mode="Markdown"
        )
        return

    lines = [f"🔍 *Results for* `{keyword}`:\n"]
    for sym, display, market in sorted(results)[:30]:
        lines.append(f"  `{sym}` — {display} _{market}_")
    if len(results) > 30:
        lines.append(f"\n_Showing 30 of {len(results)}. Narrow your search._")
    lines.append(f"\nUse `/addalert SYMBOL PRICE` to set an alert.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ══════════════════════════════════════════════════════════════════════
#  STARTUP
# ══════════════════════════════════════════════════════════════════════

async def on_startup(app: Application):
    """
    Runs once when bot starts:
    1. Load saved alerts from file
    2. Fetch live symbol list from Deriv
    3. Resume watchers for any alerts that survived a restart
    """
    global alerts
    alerts = load_alerts()
    await fetch_active_symbols()

    for sym in set(a["symbol"] for a in alerts.values() if not a["triggered"]):
        if sym not in subscribed_symbols:
            subscribed_symbols.add(sym)
            asyncio.create_task(watch_symbol(sym, app))
            logger.info(f"Resumed watcher for {sym}")

    logger.info("✅ Bot ready")


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════


# ────────── MAIN ──────────

# ────────── MAIN ──────────
import asyncio

async def main():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("❌ TELEGRAM_BOT_TOKEN not set. Add it to your environment variables.")

    # Build the bot application
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Run startup tasks: load alerts and fetch symbols
    await on_startup(app)

    # Add command handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help",  cmd_start))
    app.add_handler(CommandHandler("addalert", cmd_addalert))
    app.add_handler(CommandHandler("listalerts", cmd_listalerts))
    app.add_handler(CommandHandler("removealert", cmd_removealert))
    app.add_handler(CommandHandler("symbols", cmd_symbols))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("(?i)^(hi|hello|hey)$"), cmd_greeting))

    logger.info("🚀 Bot starting...")
    await app.run_polling()

# Run the bot
if __name__ == "__main__":
    asyncio.run(main())

