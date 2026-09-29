"""
TELEGRAM NOTIFIER FOR UDTS STEP 2 — Daily Top 5 Stocks
Sends notifications before market open (9:15 AM) and after (9:30 AM)
"""

import os
import schedule
import time
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

import requests
import yfinance as yf
from telegram import Bot
from telegram.error import TelegramError

# ============================================================================
# CONFIGURATION
# ============================================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")

IST = ZoneInfo("Asia/Kolkata")
NIFTY200_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv"

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-IN,en;q=0.9,en-US;q=0.8",
    "Referer": "https://www.nseindia.com/",
}


def today_ist():
    return pd.Timestamp.now(tz=IST).normalize().tz_localize(None)


def send_telegram_message(message: str):
    """Send message via Telegram bot"""
    try:
        bot = Bot(token=TELEGRAM_BOT_TOKEN)
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode="HTML")
        print(f"[✓] Telegram message sent at {datetime.now(IST).strftime('%H:%M:%S')}")
    except TelegramError as e:
        print(f"[✗] Telegram error: {e}")
    except Exception as e:
        print(f"[✗] Error sending message: {e}")


@lru_cache(maxsize=1)
def get_symbols():
    """Get NIFTY 200 symbols"""
    try:
        import io
        r = requests.get(
            NIFTY200_URL,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.niftyindices.com/"},
            timeout=20,
        )
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        col = next(c for c in df.columns if str(c).strip().upper() in ("SYMBOL", "SYMBOLS"))
        return sorted(df[col].dropna().astype(str).str.strip().str.upper().tolist())
    except Exception as e:
        print(f"[✗] Error fetching symbols: {e}")
        return []


def _download_nse_bhavcopy(dt):
    """Download NSE EOD bhavcopy"""
    import io
    import zipfile
    
    ymd = dt.strftime("%Y%m%d")
    ddmmyyyy = dt.strftime("%d%m%Y")
    urls = [
        f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip",
        f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv",
    ]
    session = requests.Session()
    session.headers.update(NSE_HEADERS)

    for url in urls:
        try:
            r = session.get(url, timeout=20)
            if r.status_code != 200 or not r.content:
                continue
            if url.endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    names = [n for n in z.namelist() if n.lower().endswith(".csv")]
                    if not names:
                        continue
                    with z.open(names[0]) as f:
                        raw = pd.read_csv(f)
            else:
                raw = pd.read_csv(io.BytesIO(r.content))
            return _parse_nse_frame(raw, dt)
        except Exception:
            continue
    return None


def _parse_nse_frame(df, dt):
    """Normalize NSE bhavcopy"""
    cols = {str(c).strip(): c for c in df.columns}
    if "TckrSymb" in cols and "SctySrs" in cols:
        out = df.rename(columns={
            cols["TckrSymb"]: "Stock", cols["SctySrs"]: "Series",
            cols.get("TradDt", "TradDt"): "Date", cols["OpnPric"]: "Open",
            cols["HghPric"]: "High", cols["LwPric"]: "Low", cols["ClsPric"]: "Close",
        })
    else:
        lookup = {str(c).strip().upper(): c for c in df.columns}
        required = ["SYMBOL", "SERIES", "DATE", "OPEN_PRICE", "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE"]
        if not all(k in lookup for k in required):
            return None
        out = df.rename(columns={
            lookup["SYMBOL"]: "Stock", lookup["SERIES"]: "Series",
            lookup["DATE"]: "Date", lookup["OPEN_PRICE"]: "Open",
            lookup["HIGH_PRICE"]: "High", lookup["LOW_PRICE"]: "Low",
            lookup["CLOSE_PRICE"]: "Close",
        })
    keep = ["Stock", "Series", "Date", "Open", "High", "Low", "Close"]
    out = out[[c for c in keep if c in out.columns]].copy()
    out["Stock"] = out["Stock"].astype(str).str.strip().str.upper()
    out["Series"] = out["Series"].astype(str).str.strip().str.upper()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.normalize()
    for c in ["Open", "High", "Low", "Close"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=["Stock", "Open", "Close"])
    out = out[out["Series"].isin(["EQ", "BE", "BZ", "SM", "ST", "SZ"])]
    out = out[out["Date"] == pd.Timestamp(dt)]
    return out if not out.empty else None


def latest_nse_eod():
    """Get latest NSE EOD bhavcopy"""
    now = today_ist()
    current_time = pd.Timestamp.now(tz=IST).time()
    start_offset = 0 if current_time >= datetime.strptime("16:00", "%H:%M").time() else 1
    for i in range(start_offset, 8):
        dt = now - pd.Timedelta(days=i)
        data = _download_nse_bhavcopy(dt)
        if data is not None and not data.empty:
            return data, dt
    return None, None


def _yf_history(symbol):
    """Get 2 years history from Yahoo Finance"""
    try:
        x = yf.download(
            symbol + ".NS", period="2y", interval="1d",
            auto_adjust=False, progress=False, threads=False,
        )
        if x.empty:
            return pd.DataFrame()
        if isinstance(x.columns, pd.MultiIndex):
            x.columns = x.columns.get_level_values(0)
        x = x[["Open", "High", "Low", "Close"]].dropna()
        idx = pd.to_datetime(x.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        x.index = idx.normalize()
        return x[~x.index.duplicated(keep="last")].sort_index()
    except Exception:
        return pd.DataFrame()


def get_live_intraday_indicators(symbol):
    """Get STEP 2 indicators: CPR/VWAP/EMA/RSI"""
    try:
        x = yf.download(
            symbol + ".NS", period="30d", interval="15m",
            auto_adjust=False, progress=False, threads=False,
        )
        if x.empty:
            return None
        if isinstance(x.columns, pd.MultiIndex):
            x.columns = x.columns.get_level_values(0)
        needed = ["Open", "High", "Low", "Close", "Volume"]
        if not all(c in x.columns for c in needed):
            return None
        x = x[needed].dropna().copy()
        idx = pd.to_datetime(x.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_convert(IST).tz_localize(None)
        x.index = idx
        x = x.sort_index()
        if x.empty:
            return None

        now = pd.Timestamp.now(tz=IST).tz_localize(None)
        completed_bars = x[x.index + pd.Timedelta(minutes=15) <= now].copy()
        if completed_bars.empty:
            return None

        dates = sorted(set(completed_bars.index.date))
        prev_dates = [d for d in dates if d < now.date()]
        if not prev_dates:
            return None
        prev_date = prev_dates[-1]
        prev = completed_bars[completed_bars.index.date == prev_date].copy()
        if prev.empty:
            return None

        today_session = completed_bars[completed_bars.index.date == now.date()].copy()
        if today_session.empty:
            target_date = dates[-1]
            session = completed_bars[completed_bars.index.date == target_date].copy()
        else:
            target_date = now.date()
            session = today_session
        if session.empty:
            return None

        ph = float(prev["High"].max())
        pl = float(prev["Low"].min())
        pc = float(prev["Close"].iloc[-1])
        pivot = (ph + pl + pc) / 3.0
        bc = (ph + pl) / 2.0
        tc = 2.0 * pivot - bc
        tc, bc = max(tc, bc), min(tc, bc)

        typical = (session["High"] + session["Low"] + session["Close"]) / 3.0
        vol = pd.to_numeric(session["Volume"], errors="coerce").fillna(0)
        vwap = float((typical * vol).sum() / vol.sum()) if float(vol.sum()) > 0 else float(session["Close"].iloc[-1])

        history = completed_bars[completed_bars.index <= session.index[-1]].copy()
        close = history["Close"].astype(float)
        ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
        ema34 = float(close.ewm(span=34, adjust=False).mean().iloc[-1])
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss.replace(0, pd.NA)
        rsi = (100 - (100 / (1 + rs))).fillna(100 if gain.iloc[-1] > 0 else 50)
        rsi14 = float(rsi.iloc[-1])

        daily = completed_bars.groupby(completed_bars.index.date).agg(
            Open=("Open", "first"), High=("High", "max"),
            Low=("Low", "min"), Close=("Close", "last"),
            Volume=("Volume", "sum")
        )
        prior_daily = daily.iloc[:-1].tail(20) if target_date in daily.index else daily.tail(20)
        target_volume = float(daily.loc[target_date, "Volume"]) if target_date in daily.index else float(session["Volume"].sum())
        avg_prior_volume = float(prior_daily["Volume"].mean()) if not prior_daily.empty else 0.0
        rvol = target_volume / avg_prior_volume if avg_prior_volume > 0 else None

        high = history["High"].astype(float)
        low = history["Low"].astype(float)
        prev_close = close.shift(1)
        tr = pd.concat([high-low, (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
        atr14 = tr.ewm(alpha=1/14, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(alpha=1/14, adjust=False).mean() / atr14.replace(0, pd.NA)
        minus_di = 100 * minus_dm.ewm(alpha=1/14, adjust=False).mean() / atr14.replace(0, pd.NA)
        dx = (100 * (plus_di-minus_di).abs() / (plus_di+minus_di).replace(0, pd.NA)).fillna(0)
        adx14 = float(dx.ewm(alpha=1/14, adjust=False).mean().iloc[-1])

        traded_value = daily["Close"].astype(float) * daily["Volume"].astype(float)
        avg_traded_value = float(traded_value.tail(20).mean()) if not traded_value.empty else 0.0

        # Get latest price
        try:
            latest = yf.download(
                symbol + ".NS", period="1d", interval="5m",
                auto_adjust=False, progress=False, threads=False,
            )
            if not latest.empty:
                if isinstance(latest.columns, pd.MultiIndex):
                    latest.columns = latest.columns.get_level_values(0)
                close_data = latest["Close"].dropna()
                if not close_data.empty:
                    price = float(close_data.iloc[-1])
                else:
                    price = float(session["Close"].iloc[-1])
            else:
                price = float(session["Close"].iloc[-1])
        except:
            price = float(session["Close"].iloc[-1])

        long_cpr = price > tc
        long_vwap = price > vwap
        long_ema = ema21 > ema34
        long_rsi = rsi14 >= 55
        short_cpr = price < bc
        short_vwap = price < vwap
        short_ema = ema21 < ema34
        short_rsi = rsi14 <= 45

        long_score = sum([long_cpr, long_vwap, long_ema, long_rsi])
        short_score = sum([short_cpr, short_vwap, short_ema, short_rsi])

        # Calculate strength score
        score = 0.0
        direction = None
        if long_score == 4:
            direction = "LONG"
            score = 4.0
            if rvol is not None and rvol >= 1.5: score += 1.5
            if adx14 >= 25: score += 1.0
            if avg_traded_value >= 5e7: score += 0.5
        elif short_score == 4:
            direction = "SHORT"
            score = 4.0
            if rvol is not None and rvol >= 1.5: score += 1.5
            if adx14 >= 25: score += 1.0
            if avg_traded_value >= 5e7: score += 0.5
        else:
            direction = "NEUTRAL" if long_score >= short_score else "NEUTRAL"
            score = max(long_score, short_score)

        return {
            "Stock": symbol,
            "Price": round(price, 2),
            "Direction": direction,
            "CPR": "ABOVE TC" if long_cpr else "BELOW BC" if short_cpr else "INSIDE",
            "VWAP": "ABOVE" if long_vwap else "BELOW" if short_vwap else "AT",
            "EMA": "21>34" if long_ema else "21<34" if short_ema else "21≈34",
            "RSI": round(rsi14, 2),
            "Confirm": f"L{long_score}/4" if long_score >= 3 else f"S{short_score}/4" if short_score >= 3 else f"{max(long_score, short_score)}/4",
            "Score": round(score, 1),
            "RVOL": round(rvol, 2) if rvol else None,
            "ADX": round(adx14, 2),
        }
    except Exception as e:
        print(f"[!] Error for {symbol}: {str(e)[:50]}")
        return None


def scan_for_step2_results(symbol_list):
    """Scan all symbols for STEP 2 results"""
    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(get_live_intraday_indicators, s): s for s in symbol_list}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result and result["Direction"] in ["LONG", "SHORT"]:
                    results.append(result)
            except Exception:
                pass
    return results


def get_top5_telegram_message():
    """Main function to generate and send top 5 notification"""
    try:
        print(f"\n[START] Scanning at {datetime.now(IST).strftime('%H:%M:%S')}")
        
        # Get symbols
        symbols_list = get_symbols()
        if not symbols_list:
            send_telegram_message("❌ Could not fetch NIFTY 200 symbols")
            return

        print(f"[•] Found {len(symbols_list)} symbols")

        # Scan all
        print(f"[•] Scanning STEP 2 indicators...")
        results = scan_for_step2_results(symbols_list)

        if not results:
            send_telegram_message("⚪ No STEP 2 candidates found yet")
            return

        # Convert to DataFrame and sort
        df = pd.DataFrame(results)
        df = df[df["Direction"].isin(["LONG", "SHORT"])].copy()
        df = df.sort_values("Score", ascending=False).head(10)

        # Separate LONG and SHORT
        longs = df[df["Direction"] == "LONG"].head(5)
        shorts = df[df["Direction"] == "SHORT"].head(5)

        # Build message
        msg = f"<b>📊 STEP 2 — TOP STOCKS ({datetime.now(IST).strftime('%d-%b %H:%M IST')})</b>\n\n"

        if not longs.empty:
            msg += "<b>🟢 TOP 5 LONG (4/4 Confirmation):</b>\n"
            for idx, (_, row) in enumerate(longs.iterrows(), 1):
                msg += f"{idx}. <b>{row['Stock']}</b> ₹{row['Price']} | Score: <b>{row['Score']}/10</b>\n"
                msg += f"   CPR: {row['CPR']} | VWAP: {row['VWAP']} | RSI: {row['RSI']}\n"
            msg += "\n"

        if not shorts.empty:
            msg += "<b>🔴 TOP 5 SHORT (4/4 Confirmation):</b>\n"
            for idx, (_, row) in enumerate(shorts.iterrows(), 1):
                msg += f"{idx}. <b>{row['Stock']}</b> ₹{row['Price']} | Score: <b>{row['Score']}/10</b>\n"
                msg += f"   CPR: {row['CPR']} | VWAP: {row['VWAP']} | RSI: {row['RSI']}\n"

        msg += f"\n⚠️ <i>This is a filter aid, not a trade guarantee.</i>"

        # Send
        send_telegram_message(msg)
        print(f"[✓] Message sent successfully")

    except Exception as e:
        print(f"[✗] Error: {e}")
        send_telegram_message(f"❌ Scan error: {str(e)[:100]}")


def schedule_notifications():
    """Schedule two daily notifications"""
    # 9:15 AM - Before market open
    schedule.every().day.at("09:15").do(get_top5_telegram_message)
    print("✓ Scheduled notification at 09:15 IST (before market open)")

    # 9:35 AM - After market open
    schedule.every().day.at("09:35").do(get_top5_telegram_message)
    print("✓ Scheduled notification at 09:35 IST (after market open)")


def run_scheduler():
    """Keep scheduler running"""
    print("\n" + "="*60)
    print("TELEGRAM NOTIFIER — STEP 2 TOP 5 STOCKS")
    print("="*60)
    print(f"Bot Token: {TELEGRAM_BOT_TOKEN[:10]}...")
    print(f"Chat ID: {TELEGRAM_CHAT_ID}")
    print(f"Timezone: {IST}")
    print("="*60 + "\n")

    schedule_notifications()

    print("[•] Scheduler running. Waiting for scheduled times...\n")
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    get_top5_telegram_message()
