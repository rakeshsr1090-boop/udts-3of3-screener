# TELEGRAM STEP 2 NOTIFIER — SETUP GUIDE

## Complete Setup in 5 Steps (FREE)

### Step 1: Create Telegram Bot

1. Open **Telegram** app
2. Search for **@BotFather**
3. Send: `/newbot`
4. Name your bot: `UDTS Screener Bot`
5. **Copy the Bot Token** (looks like: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)

**Save this token — you'll need it in Step 4**

---

### Step 2: Get Your Chat ID

1. Search for **@userinfobot** on Telegram
2. Send: `/start`
3. It will show your **User ID** (a number like `987654321`)

**Save this ID — you'll need it in Step 4**

---

### Step 3: Add Secrets to GitHub

1. Go to your repository: https://github.com/rakeshsr1090-boop/udts-3of3-screener
2. Click **Settings** (top right)
3. Left sidebar → **Secrets and variables** → **Actions**
4. Click **New repository secret**

**Add Two Secrets:**

**Secret 1:**
- Name: `TELEGRAM_BOT_TOKEN`
- Value: (paste the token from Step 1)
- Click **Add secret**

**Secret 2:**
- Name: `TELEGRAM_CHAT_ID`
- Value: (paste your user ID from Step 2)
- Click **Add secret**

---

### Step 4: Enable GitHub Actions

1. Go to your repository
2. Click **Actions** (top menu)
3. You should see workflow: **"STEP 2 Top 5 Telegram Alert"**
4. Click it → Click **"Enable workflow"** (if disabled)

---

### Step 5: Test Immediately (Optional)

1. In the **Actions** tab
2. Find the workflow **"STEP 2 Top 5 Telegram Alert"**
3. Click **Run workflow** (green button)
4. In 30 seconds, you should get a **Telegram notification** on your phone

---

## What Happens Next (Automatic)

✅ **9:15 AM IST** (before market open) — Telegram alert with Top 5 STEP 2 stocks

✅ **9:35 AM IST** (after market open) — Same alert refreshed

✅ **Only on trading days** (Monday–Friday)

✅ **Fully automatic** — No manual clicking needed

---

## What You'll Receive

Example message:

```
📊 STEP 2 — TOP STOCKS (29-Sep 09:15 IST)

🟢 TOP 5 LONG (4/4 Confirmation):
1. RELIANCE ₹2850.50 | Score: 9.5/10
   CPR: ABOVE TC | VWAP: ABOVE | RSI: 62.4

2. INFY ₹4200.25 | Score: 8.8/10
   CPR: ABOVE TC | VWAP: ABOVE | RSI: 59.8

[... 3 more ...]

🔴 TOP 5 SHORT (4/4 Confirmation):
1. TCS ₹3950.00 | Score: 9.0/10
   CPR: BELOW BC | VWAP: BELOW | RSI: 42.1

[... 4 more ...]

⚠️ This is a filter aid, not a trade guarantee.
```

---

## Troubleshooting

### ❌ Not getting notifications?

**Check 1:** Verify secrets are set
- Go to **Settings** → **Secrets and variables** → **Actions**
- Confirm `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` exist

**Check 2:** Check GitHub Actions logs
- Go to **Actions** tab
- Click latest workflow run
- Check if it says ✅ or ❌
- Click the failed job to see error logs

**Check 3:** Telegram permissions
- Open Telegram → Search bot name → Send `/start`
- Make sure you're receiving messages from the bot

**Check 4:** Time zone
- The schedule is set for **IST (Asia/Kolkata)**
- GitHub runs on UTC, so 9:15 AM IST = 3:45 AM UTC
- If you're in a different timezone, modify the cron times

---

## Manual Cron Times (if needed)

Edit `.github/workflows/telegram_step2_notifier.yml`

**Current schedule:**
```yaml
  - cron: '45 3 * * 1-5'  # 9:15 AM IST
  - cron: '5 4 * * 1-5'   # 9:35 AM IST
```

**To change time:**
- Use https://crontab.guru
- Enter your desired time
- Copy the cron expression
- Paste into the workflow file

---

## Cost

✅ **Completely FREE**
- GitHub Actions: Free tier (2000 minutes/month, you need ~2 minutes/day = 10 min/month)
- Telegram: Free forever
- Your code: Already yours

---

## Next Steps

1. ✅ Create Telegram bot (@BotFather)
2. ✅ Get your Chat ID (@userinfobot)
3. ✅ Add secrets to GitHub (Settings → Secrets)
4. ✅ Enable workflow in Actions tab
5. ✅ Test by running manually
6. ✅ Get automatic daily alerts at 9:15 AM & 9:35 AM IST

---

**Questions?** Check the workflow logs in the Actions tab — they show exactly what's happening.
