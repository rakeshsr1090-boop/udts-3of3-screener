import io
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="UDTS 3/3 Mobile Screener", page_icon="📈", layout="centered")

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


@st.cache_data(ttl=3600)
def symbols():
    r = requests.get(
        NIFTY200_URL,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.niftyindices.com/"},
        timeout=20,
    )
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    col = next(c for c in df.columns if str(c).strip().upper() in ("SYMBOL", "SYMBOLS"))
    return sorted(df[col].dropna().astype(str).str.strip().str.upper().tolist())


def _parse_nse_frame(df):
    """Normalize NSE UDiFF or legacy full-bhavcopy columns."""
    cols = {str(c).strip(): c for c in df.columns}

    # Current UDiFF Common Bhavcopy format.
    if "TckrSymb" in cols and "SctySrs" in cols:
        out = df.rename(
            columns={
                cols["TckrSymb"]: "Stock",
                cols["SctySrs"]: "Series",
                cols.get("TradDt", "TradDt"): "Date",
                cols["OpnPric"]: "Open",
                cols["HghPric"]: "High",
                cols["LwPric"]: "Low",
                cols["ClsPric"]: "Close",
            }
        )
    # Legacy NSE full bhavdata format.
    else:
        lookup = {str(c).strip().upper(): c for c in df.columns}
        required = [
            "SYMBOL", "SERIES", "DATE", "OPEN_PRICE",
            "HIGH_PRICE", "LOW_PRICE", "CLOSE_PRICE"
        ]
        if not all(k in lookup for k in required):
            raise ValueError("Unknown NSE bhavcopy format")
        out = df.rename(
            columns={
                lookup["SYMBOL"]: "Stock",
                lookup["SERIES"]: "Series",
                lookup["DATE"]: "Date",
                lookup["OPEN_PRICE"]: "Open",
                lookup["HIGH_PRICE"]: "High",
                lookup["LOW_PRICE"]: "Low",
                lookup["CLOSE_PRICE"]: "Close",
            }
        )

    keep = ["Stock", "Series", "Date", "Open", "High", "Low", "Close"]
    out = out[[c for c in keep if c in out.columns]].copy()
    out["Stock"] = out["Stock"].astype(str).str.strip().str.upper()
    out["Series"] = out["Series"].astype(str).str.strip().str.upper()
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.normalize()
    for c in ["Open", "High", "Low", "Close"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=["Stock", "Open", "Close"])
    out = out[out["Series"].isin(["EQ", "BE", "BZ", "SM", "ST", "SZ"])]
    return out


def _download_nse_bhavcopy(dt):
    """Download one NSE end-of-day bhavcopy. Returns normalized rows or None."""
    ymd = dt.strftime("%Y%m%d")
    ddmmyyyy = dt.strftime("%d%m%Y")

    urls = [
        # Current UDiFF common bhavcopy.
        f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip",
        # Legacy/full bhavdata fallback.
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

            parsed = _parse_nse_frame(raw)
            if parsed.empty:
                continue

            # Only accept the requested trading date.
            parsed = parsed[parsed["Date"] == pd.Timestamp(dt)]
            if not parsed.empty:
                return parsed
        except Exception:
            continue

    return None


@st.cache_data(ttl=300, show_spinner=False)
def latest_nse_eod():
    """Find the latest completed NSE EOD bhavcopy.

    During market hours, today's candle is still incomplete, so start from the
    previous calendar day. After the normal NSE equity session is over, start
    with today so the scanner uses today's completed EOD candle as soon as NSE
    publishes the bhavcopy. Weekends/holidays are handled by searching back.
    """
    now = today_ist()
    current_time = pd.Timestamp.now(tz=IST).time()

    # NSE cash-market trading is complete by 15:30 IST. Give the EOD file a
    # little publication buffer; after 16:00 IST we can use today's EOD file.
    # Before that, today's candle must never be treated as completed.
    start_offset = 0 if current_time >= datetime.strptime("16:00", "%H:%M").time() else 1

    # Try the current day first after close, then walk backward for weekends,
    # holidays, or an EOD file that has not yet been published.
    for i in range(start_offset, 8):
        dt = now - pd.Timedelta(days=i)
        data = _download_nse_bhavcopy(dt)
        if data is not None and not data.empty:
            return data, dt
    return None, None


def _yf_history(symbol):
    x = yf.download(
        symbol + ".NS",
        period="2y",
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=False,
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


def get_udts(symbol, nse_eod):
    try:
        x = _yf_history(symbol)
        if x.empty or len(x) < 40:
            return None

        # Always exclude the current IST date.
        today = today_ist()
        x = x[x.index < today].copy()

        # Patch/overwrite the most recent completed trading day with NSE EOD data.
        # This fixes the common 00:00–morning IST Yahoo lag that caused Sep-09
        # to be skipped and Sep-08 to be treated as the previous day.
        if nse_eod is None or nse_eod.empty:
            return None

        row = nse_eod[nse_eod["Stock"] == symbol]
        if row.empty:
            # Stock may have changed series; fall back to the latest EQ-like row.
            row = nse_eod[nse_eod["Stock"].eq(symbol)]
        if row.empty:
            return None

        r = row.iloc[0]
        eod_date = pd.Timestamp(r["Date"]).normalize()
        # On/after 16:00 IST, today's NSE EOD candle is valid and must be used.
        # During market hours, latest_nse_eod() will return the previous trading day.
        if eod_date > today:
            return None

        x.loc[eod_date, ["Open", "High", "Low", "Close"]] = [
            float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"])
        ]
        x = x.sort_index()

        # Do not silently accept stale Yahoo data. The NSE EOD date must be the
        # latest row used for the Day signal.
        if x.index[-1] != eod_date:
            return None

        day = x.iloc[-1]
        week = x.resample("W-FRI").agg(
            Open=("Open", "first"),
            High=("High", "max"),
            Low=("Low", "min"),
            Close=("Close", "last"),
        ).dropna()
        month = x.resample("ME").agg(
            Open=("Open", "first"),
            High=("High", "max"),
            Low=("Low", "min"),
            Close=("Close", "last"),
        ).dropna()

        if week.empty or month.empty:
            return None

        last_data_date = x.index[-1]
        current_week_period = last_data_date.to_period("W-FRI")
        current_month_period = last_data_date.to_period("M")
        week_periods = week.index.to_period("W-FRI")
        month_periods = month.index.to_period("M")

        completed_weeks = week[week_periods < current_week_period]
        completed_months = month[month_periods < current_month_period]

        # A period is complete only when the EOD date is the final calendar day
        # of that period. This avoids treating Wed/Thu data as a completed week.
        is_week_complete = last_data_date.weekday() == 4
        is_month_complete = (
            (last_data_date + pd.Timedelta(days=1)).month != last_data_date.month
        )
        if is_week_complete:
            completed_weeks = week[week_periods <= current_week_period]
        if is_month_complete:
            completed_months = month[month_periods <= current_month_period]

        if completed_weeks.empty or completed_months.empty:
            return None

        w = completed_weeks.iloc[-1]
        m = completed_months.iloc[-1]
        mg, wg, dg = m.Close > m.Open, w.Close > w.Open, day.Close > day.Open
        mr, wr, dr = m.Close < m.Open, w.Close < w.Open, day.Close < day.Open
        direction = "LONG" if mg and wg and dg else "SHORT" if mr and wr and dr else "—"

        return {
            "Stock": symbol,
            "Month": "🟢" if mg else "🔴" if mr else "⚪",
            "Week": "🟢" if wg else "🔴" if wr else "⚪",
            "Day": "🟢" if dg else "🔴" if dr else "⚪",
            "Direction": direction,
            "Price": float(day.Close),
            "EOD Date": eod_date.strftime("%d-%b-%Y"),
        }
    except Exception:
        return None


def get_latest_price(symbol):
    """Get latest available 5-minute price for an UDTS-passed stock."""
    try:
        x = yf.download(
            symbol + ".NS",
            period="1d",
            interval="5m",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        if x.empty:
            return None
        if isinstance(x.columns, pd.MultiIndex):
            x.columns = x.columns.get_level_values(0)
        close = x["Close"].dropna()
        if close.empty:
            return None
        return float(close.iloc[-1])
    except Exception:
        return None


def get_intraday_indicators(symbol):
    """Calculate CPR -> VWAP -> EMA 21/34 -> RSI(14) using ONLY completed sessions up to the latest completed trading day.

    IMPORTANT: today's intraday candles are never used.  If today is a trading
    day, the indicator snapshot is based on yesterday's completed session.
    On weekends/holidays it automatically uses the most recent completed
    trading session before today.
    """
    try:
        x = yf.download(
            symbol + ".NS",
            period="30d",
            interval="15m",
            auto_adjust=False,
            progress=False,
            threads=False,
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

        # STRICT RULE: exclude every bar belonging to today's IST date.
        today = today_ist().date()
        completed = x[x.index.date < today].copy()
        if completed.empty:
            return None

        dates = sorted(set(completed.index.date))
        if len(dates) < 2:
            return None

        # "Yesterday" = latest completed trading session available before today.
        target_date = dates[-1]
        prev_date = dates[-2]

        session = completed[completed.index.date == target_date].copy()
        prev = completed[completed.index.date == prev_date].copy()
        if session.empty or prev.empty:
            return None

        # CPR for the target session is based on the previous completed day's OHLC.
        ph = float(prev["High"].max())
        pl = float(prev["Low"].min())
        pc = float(prev["Close"].iloc[-1])

        pivot = (ph + pl + pc) / 3.0
        bc = (ph + pl) / 2.0
        tc = 2.0 * pivot - bc
        tc, bc = max(tc, bc), min(tc, bc)

        # VWAP for the target completed session (yesterday), NOT today's session.
        typical = (session["High"] + session["Low"] + session["Close"]) / 3.0
        vol = pd.to_numeric(session["Volume"], errors="coerce").fillna(0)
        if float(vol.sum()) > 0:
            vwap = float((typical * vol).sum() / vol.sum())
        else:
            vwap = float(session["Close"].iloc[-1])

        # EMA and RSI are calculated using data only through the target session.
        history = completed[completed.index.date <= target_date].copy()
        close = history["Close"].astype(float)
        ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
        ema34 = float(close.ewm(span=34, adjust=False).mean().iloc[-1])
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss.replace(0, pd.NA)
        rsi = (100 - (100 / (1 + rs))).fillna(100 if gain.iloc[-1] > 0 else 50)
        rsi14 = float(rsi.iloc[-1])

        # ------------------------------------------------------------------
        # Additional strength metrics for the 10/10 ranking model.
        # RVOL: target-session volume versus the average of the prior 20
        # completed sessions. ADX: 14-period trend strength on completed
        # 15-minute candles. Liquidity: target-session traded value proxy.
        # ------------------------------------------------------------------
        daily = completed.groupby(completed.index.date).agg(
            Open=("Open", "first"), High=("High", "max"),
            Low=("Low", "min"), Close=("Close", "last"),
            Volume=("Volume", "sum")
        )
        prior_daily = daily.iloc[:-1].tail(20)
        target_volume = float(daily.iloc[-1]["Volume"])
        avg_prior_volume = float(prior_daily["Volume"].mean()) if not prior_daily.empty else 0.0
        rvol = target_volume / avg_prior_volume if avg_prior_volume > 0 else None

        # ADX(14) on 15-minute completed bars.
        high = history["High"].astype(float)
        low = history["Low"].astype(float)
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
        atr14 = tr.ewm(alpha=1/14, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(alpha=1/14, adjust=False).mean() / atr14.replace(0, pd.NA)
        minus_di = 100 * minus_dm.ewm(alpha=1/14, adjust=False).mean() / atr14.replace(0, pd.NA)
        dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)).fillna(0)
        adx14 = float(dx.ewm(alpha=1/14, adjust=False).mean().iloc[-1])

        # Liquidity score uses average daily traded-value proxy over the latest
        # 20 completed sessions (Close * Volume), not today's live market.
        traded_value = daily["Close"].astype(float) * daily["Volume"].astype(float)
        avg_traded_value = float(traded_value.tail(20).mean()) if not traded_value.empty else 0.0
        price = float(session["Close"].iloc[-1])
        target_traded_value = float(price * target_volume)

        # Use yesterday's completed 15-minute closing price for confirmation.

        # Strict confirmation rules.
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

        if long_score == 4:
            confirmation = "LONG 4/4"
        elif short_score == 4:
            confirmation = "SHORT 4/4"
        else:
            confirmation = f"L {long_score}/4 | S {short_score}/4"

        return {
            "CPR": "ABOVE TC" if long_cpr else "BELOW BC" if short_cpr else "INSIDE",
            "VWAP": "ABOVE" if long_vwap else "BELOW" if short_vwap else "—",
            "EMA": "21 > 34" if long_ema else "21 < 34" if short_ema else "21 ≈ 34",
            "RSI": round(rsi14, 2),
            "CPR Pivot": round(pivot, 2),
            "CPR TC": round(tc, 2),
            "CPR BC": round(bc, 2),
            "VWAP Value": round(vwap, 2),
            "EMA 21": round(ema21, 2),
            "EMA 34": round(ema34, 2),
            "Confirm Score": max(long_score, short_score),
            "Confirmation": confirmation,
            "RVOL": round(rvol, 2) if rvol is not None else None,
            "ADX": round(adx14, 2),
            "Avg Traded Value (₹)": round(avg_traded_value, 0),
            "Target Traded Value (₹)": round(target_traded_value, 0),
            "Indicator Date": target_date.strftime("%d-%b-%Y"),
            "Indicator TF": "15m",
        }
    except Exception:
        return None


def calculate_strength_score(row):
    """10-point ranking score applied after UDTS 3/3.

    CPR 2 + VWAP 2 + EMA21/34 2 + RSI 1 + RVOL 1.5 + ADX 1 +
    Liquidity 0.5. CPR and VWAP are mandatory gates for the final trade
    candidate; the score itself is a ranking aid, not a buy guarantee.
    """
    direction = row.get("Direction")
    score = 0.0
    if direction == "LONG":
        if row.get("CPR") == "ABOVE TC": score += 2.0
        if row.get("VWAP") == "ABOVE": score += 2.0
        if row.get("EMA") == "21 > 34": score += 2.0
        rsi = row.get("RSI")
        if pd.notna(rsi) and 55 <= float(rsi) <= 70: score += 1.0
        rvol = row.get("RVOL")
        if pd.notna(rvol) and float(rvol) >= 1.5: score += 1.5
        adx = row.get("ADX")
        if pd.notna(adx) and float(adx) >= 25: score += 1.0
        liq = row.get("Avg Traded Value (₹)")
        if pd.notna(liq) and float(liq) >= 5e7: score += 0.5
        gate = row.get("CPR") == "ABOVE TC" and row.get("VWAP") == "ABOVE"
    elif direction == "SHORT":
        if row.get("CPR") == "BELOW BC": score += 2.0
        if row.get("VWAP") == "BELOW": score += 2.0
        if row.get("EMA") == "21 < 34": score += 2.0
        rsi = row.get("RSI")
        if pd.notna(rsi) and 30 <= float(rsi) <= 45: score += 1.0
        rvol = row.get("RVOL")
        if pd.notna(rvol) and float(rvol) >= 1.5: score += 1.5
        adx = row.get("ADX")
        if pd.notna(adx) and float(adx) >= 25: score += 1.0
        liq = row.get("Avg Traded Value (₹)")
        if pd.notna(liq) and float(liq) >= 5e7: score += 0.5
        gate = row.get("CPR") == "BELOW BC" and row.get("VWAP") == "BELOW"
    else:
        gate = False
    return round(score, 1), gate


def add_confirmation_filter(df, only_confirmed=False):
    """Add CPR/VWAP/EMA/RSI columns without changing the UDTS 3/3 result."""
    if df.empty:
        return df
    indicators = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(get_intraday_indicators, s): s for s in df["Stock"]}
        for f in as_completed(futures):
            s = futures[f]
            try:
                indicators[s] = f.result()
            except Exception:
                indicators[s] = None

    result = df.copy()
    fields = [
        "CPR", "VWAP", "EMA", "RSI", "CPR Pivot", "CPR TC", "CPR BC",
        "VWAP Value", "EMA 21", "EMA 34", "Confirm Score", "Confirmation", "Indicator Date", "Indicator TF"
    ]
    for field in fields:
        result[field] = result["Stock"].map(
            lambda s: indicators.get(s, {}).get(field) if indicators.get(s) else None
        )

    # Confirmation must agree with the UDTS direction.
    result["Confirmed"] = result.apply(
        lambda r: (
            r["Direction"] == "LONG" and r.get("Confirmation") == "LONG 4/4"
        ) or (
            r["Direction"] == "SHORT" and r.get("Confirmation") == "SHORT 4/4"
        ),
        axis=1,
    )

    scored = result.apply(calculate_strength_score, axis=1, result_type="expand")
    scored.columns = ["Strength Score", "Mandatory Gate"]
    result[["Strength Score", "Mandatory Gate"]] = scored
    result["Grade"] = result["Strength Score"].apply(
        lambda v: "A+" if v >= 9 else "A" if v >= 8 else "B" if v >= 7 else "C" if v >= 6 else "D"
    )
    result["Trade Candidate"] = result.apply(
        lambda r: bool(r["Mandatory Gate"]) and float(r["Strength Score"]) >= 8.0, axis=1
    )
    if only_confirmed:
        result = result[result["Confirmed"]].copy()
    return result


def add_capital_filter(df, capital_limit):
    if df.empty:
        return df
    prices = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(get_latest_price, s): s for s in df["Stock"]}
        for f in as_completed(futures):
            s = futures[f]
            try:
                prices[s] = f.result()
            except Exception:
                prices[s] = None

    result = df.copy()
    result["Price"] = result["Stock"].map(prices).fillna(result["Price"])

    def max_qty(price):
        if pd.isna(price) or price <= 0:
            return 0
        return int(capital_limit // float(price))

    result["Max Qty"] = result["Price"].apply(max_qty)
    result["Required Capital"] = result.apply(
        lambda r: round(float(r["Price"]) * int(r["Max Qty"]), 2)
        if pd.notna(r["Price"]) else 0.0,
        axis=1,
    )
    return result


def scan(symbol_list, nse_eod, workers=8):
    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(get_udts, s, nse_eod) for s in symbol_list]
        for f in as_completed(futures):
            r = f.result()
            if r:
                out.append(r)
    return out


st.title("📈 UDTS 3/3 Mobile Screener")
st.caption("STEP 1: Strict UDTS filter — previous completed Month + Week + Day")

with st.expander("⚙️ Settings"):
    workers = st.slider("Parallel downloads", 2, 12, 8)
    capital_limit = st.number_input(
        "💰 Maximum trading capital per stock (₹)",
        min_value=1000,
        max_value=10000,
        value=10000,
        step=500,
        help="Maximum amount allocated to one stock. Quantity is rounded down to whole shares.",
    )

st.markdown("**LONG:** 🟢 Month + 🟢 Week + 🟢 Day")
st.markdown("**SHORT:** 🔴 Month + 🔴 Week + 🔴 Day")

# -----------------------------------------------------------------------------
# STEP 1 — UDTS ONLY
# -----------------------------------------------------------------------------
if st.button("🔄 SCAN UDTS ONLY", type="primary", use_container_width=True):
    try:
        syms = symbols()
        with st.spinner("Getting latest NSE EOD bhavcopy..."):
            nse_eod, eod_dt = latest_nse_eod()
        if nse_eod is None:
            st.error("Could not obtain the latest NSE EOD bhavcopy. Scan stopped so stale daily data is not used.")
        else:
            with st.spinner(f"Scanning NIFTY 200 ({len(syms)} stocks) for UDTS 3/3..."):
                st.session_state.results = scan(syms, nse_eod, workers)
            st.session_state.scan_time = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S IST")
            st.session_state.eod_date = eod_dt.strftime("%d-%b-%Y")
            # Clear the downstream table whenever a fresh UDTS scan is run.
            st.session_state.confirm_results = None
            st.session_state.confirm_scan_time = None
    except Exception as e:
        st.error(f"UDTS scan failed: {e}")

if "results" in st.session_state:
    df = pd.DataFrame(st.session_state.results)

    if not df.empty:
        passed_udts = df[df.Direction.isin(["LONG", "SHORT"])].copy()
        mixed = df[~df.Direction.isin(["LONG", "SHORT"])].copy()

        # Capital is kept as an existing feature, but it is calculated only for
        # UDTS-passed stocks and displayed in the first table.
        with st.spinner("Updating latest prices and capital quantities for UDTS-passed stocks..."):
            passed_udts = add_capital_filter(passed_udts, capital_limit)

        # Keep the clean UDTS result available for the second-stage scan.
        st.session_state.udts_passed = passed_udts.copy()

        longs_udts = passed_udts[passed_udts.Direction == "LONG"].sort_values("Stock")
        shorts_udts = passed_udts[passed_udts.Direction == "SHORT"].sort_values("Stock")

        a, b, c = st.columns(3)
        a.metric("Scanned", len(df))
        b.metric("🟢 UDTS LONG 3/3", len(longs_udts))
        c.metric("🔴 UDTS SHORT 3/3", len(shorts_udts))
        st.caption("Last UDTS scan: " + st.session_state.scan_time)
        st.caption(
            f"NSE EOD candle used: {st.session_state.eod_date} | "
            f"💰 Capital limit: ₹{capital_limit:,.0f} per stock"
        )

        udts_cols = [
            "Stock", "Month", "Week", "Day", "Direction",
            "Price", "Max Qty", "Required Capital"
        ]

        st.subheader("🟢 UDTS LONG — 3/3")
        st.dataframe(
            longs_udts[udts_cols],
            hide_index=True,
            use_container_width=True,
            column_config={
                "Price": st.column_config.NumberColumn("Price (₹)", format="₹%.2f"),
                "Max Qty": st.column_config.NumberColumn("Max Qty", format="%d"),
                "Required Capital": st.column_config.NumberColumn("Required Capital (₹)", format="₹%.2f"),
            },
        )

        st.subheader("🔴 UDTS SHORT — 3/3")
        st.dataframe(
            shorts_udts[udts_cols],
            hide_index=True,
            use_container_width=True,
            column_config={
                "Price": st.column_config.NumberColumn("Price (₹)", format="₹%.2f"),
                "Max Qty": st.column_config.NumberColumn("Max Qty", format="%d"),
                "Required Capital": st.column_config.NumberColumn("Required Capital (₹)", format="₹%.2f"),
            },
        )

        with st.expander("⚪ Mixed / rejected by UDTS"):
            st.dataframe(
                mixed[["Stock", "Month", "Week", "Day", "Direction", "EOD Date"]],
                hide_index=True,
                use_container_width=True,
            )

        st.download_button(
            "⬇️ Download UDTS 3/3 List",
            passed_udts[udts_cols].to_csv(index=False).encode(),
            "udts_3of3_list.csv",
            "text/csv",
            use_container_width=True,
        )

        # ---------------------------------------------------------------------
        # STEP 2 — CONFIRMATION ONLY ON THE UDTS OUTPUT
        # ---------------------------------------------------------------------
        st.divider()
        st.subheader("🎯 STEP 2 — CPR → VWAP → EMA 21/34 → RSI")
        st.caption(
            "This section uses ONLY the stocks that passed UDTS 3/3 above. "
            "It does not rescan the NIFTY 200."
        )

        if st.button("🔄 REFRESH CPR → VWAP → EMA → RSI", use_container_width=True):
            try:
                with st.spinner(f"Refreshing confirmation indicators for {len(passed_udts)} UDTS-passed stocks..."):
                    confirm_df = add_confirmation_filter(passed_udts.copy(), only_confirmed=False)
                st.session_state.confirm_results = confirm_df
                st.session_state.confirm_scan_time = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S IST")
            except Exception as e:
                st.error(f"Confirmation refresh failed: {e}")

        if st.session_state.get("confirm_results") is not None:
            confirm_df = st.session_state.confirm_results.copy()
            longs_confirm = confirm_df[confirm_df.Direction == "LONG"].sort_values(
                ["Trade Candidate", "Strength Score", "Confirm Score", "Stock"], ascending=[False, False, False, True]
            )
            shorts_confirm = confirm_df[confirm_df.Direction == "SHORT"].sort_values(
                ["Trade Candidate", "Strength Score", "Confirm Score", "Stock"], ascending=[False, False, False, True]
            )

            x, y, z, q = st.columns(4)
            x.metric("UDTS stocks checked", len(confirm_df))
            y.metric("🏆 8+ /10", int((confirm_df["Strength Score"] >= 8).sum()))
            z.metric("🟢 LONG 4/4", int((confirm_df["Confirmation"] == "LONG 4/4").sum()))
            q.metric("🔴 SHORT 4/4", int((confirm_df["Confirmation"] == "SHORT 4/4").sum()))
            st.caption("Last confirmation refresh: " + st.session_state.confirm_scan_time)
            st.caption("Indicator timeframe: 15-minute | Data through latest completed session only — today's intraday candles are excluded")

            confirm_cols = [
                "Stock", "Direction", "Price", "Max Qty", "Required Capital",
                "CPR", "VWAP", "EMA", "RSI", "RVOL", "ADX", "Confirm Score",
                "Strength Score", "Grade", "Mandatory Gate", "Trade Candidate", "Confirmation"
            ]

            st.markdown("**LONG confirmation candidates**")
            st.dataframe(
                longs_confirm[confirm_cols],
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Price": st.column_config.NumberColumn("Price (₹)", format="₹%.2f"),
                    "Max Qty": st.column_config.NumberColumn("Max Qty", format="%d"),
                    "Required Capital": st.column_config.NumberColumn("Required Capital (₹)", format="₹%.2f"),
                    "RSI": st.column_config.NumberColumn("RSI", format="%.2f"),
                    "Confirm Score": st.column_config.NumberColumn("Confirm", format="%d/4"),
                    "Strength Score": st.column_config.NumberColumn("Strength", format="%.1f/10"),
                },
            )

            st.markdown("**SHORT confirmation candidates**")
            st.dataframe(
                shorts_confirm[confirm_cols],
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Price": st.column_config.NumberColumn("Price (₹)", format="₹%.2f"),
                    "Max Qty": st.column_config.NumberColumn("Max Qty", format="%d"),
                    "Required Capital": st.column_config.NumberColumn("Required Capital (₹)", format="₹%.2f"),
                    "RSI": st.column_config.NumberColumn("RSI", format="%.2f"),
                    "Confirm Score": st.column_config.NumberColumn("Confirm", format="%d/4"),
                    "Strength Score": st.column_config.NumberColumn("Strength", format="%.1f/10"),
                },
            )

            st.caption(
                "10/10 ranking: CPR 2 + VWAP 2 + EMA 21/34 2 + RSI 1 + RVOL 1.5 + ADX 1 + Liquidity 0.5. "
                "A trade candidate requires CPR + VWAP gates and a score ≥ 8/10. "
                "LONG RSI = 55–70; SHORT RSI = 30–45; RVOL ≥ 1.5; ADX ≥ 25; average traded value ≥ ₹5 crore. "
                "All calculations use completed data only and are ranking/confirmation aids, not guarantees."
            )

            st.download_button(
                "⬇️ Download UDTS-passed + Confirmation List",
                confirm_df[confirm_cols].to_csv(index=False).encode(),
                "udts_3of3_confirmation.csv",
                "text/csv",
                use_container_width=True,
            )
        else:
            st.info("UDTS is ready. Tap **REFRESH CPR → VWAP → EMA → RSI** to calculate the second-stage filters.")

    else:
        st.warning("No stock data returned.")
else:
    st.info("Tap **SCAN UDTS ONLY** to get the current 3/3 list.")

st.caption(
    "Day candle is sourced from NSE EOD bhavcopy; Yahoo Finance is used for historical context and intraday price. "
    "The scanner implements the 3/3 candle rule you specified and is not a claim to reproduce IFMC's proprietary full UDTS system."
)
