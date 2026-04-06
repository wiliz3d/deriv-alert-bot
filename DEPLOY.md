# 🚀 DEPLOY GUIDE — Deriv Alert Bot on Railway (Free)

## What You Need
- GitHub account (free)
- Railway account (free) → https://railway.app
- Telegram bot token from @BotFather
- Deriv App ID from https://developers.deriv.com

---

## STEP 1 — Get Your Telegram Bot Token
1. Open Telegram → search **@BotFather**
2. Send `/newbot`
3. Give it a name (e.g. "Deriv Alerts") and a username (e.g. `myderivbot`)
4. Copy the token it gives you → looks like `7123456789:AAFxxx...`

---

## STEP 2 — Get Deriv App ID
1. Go to https://developers.deriv.com
2. Sign in with your Deriv account
3. Click "Register an application"
4. Fill in any name, set scope to `read`
5. Copy your **App ID** (it's a number like `12345`)

> You can skip this and use `1089` for testing, but get your own for production.

---

## STEP 3 — Upload Files to GitHub
1. Go to https://github.com → create a **new repository** (name it `deriv-alert-bot`)
2. Make it **Private**
3. Upload these 4 files:
   - `deriv_alert_bot.py`
   - `requirements.txt`
   - `Procfile`
   - `railway.toml`

---

## STEP 4 — Deploy on Railway
1. Go to https://railway.app → sign in with GitHub
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Select your `deriv-alert-bot` repo
4. Railway will auto-detect Python and start building

---

## STEP 5 — Set Environment Variables
In Railway dashboard:
1. Click your project → go to **"Variables"** tab
2. Add these two:

| Variable | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | your token from BotFather |
| `DERIV_APP_ID` | your app ID from Deriv |

3. Railway will **automatically restart** the bot with the new variables

---

## STEP 6 — Test It
1. Open Telegram → find your bot by its username
2. Send `/start`
3. Send `/addalert frxEURUSD 1.0850`
4. Check Railway logs to confirm it's subscribing

---

## Logs (to check if it's running)
In Railway dashboard → click your service → **"Logs"** tab
You should see:
```
✅ Bot is running...
[frxEURUSD] Subscribed to ticks.
```

---

## ⚠️ Free Tier Limits
- Railway free gives **500 hours/month** (≈ 20 days continuous)
- To get full 24/7, add a credit card to Railway (still free, just unlocks more hours)
- Or use **Render.com** as backup (also free, similar setup)

---

## Sharing the Bot with Others
Just share your bot's Telegram username (e.g. `t.me/myderivbot`)
- Each user gets their OWN alerts
- Alerts are saved to file so they survive restarts
- No one can see or delete another user's alerts
