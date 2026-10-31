import io
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="UDTS + Independent Index F&O", page_icon="📈", layout="centered")

IST = ZoneInfo("Asia/Kolkata")
NIFTY200_URLS = [
    "https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty200list.csv",
    "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv",
]
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
    """Fetch the NIFTY 200 constituent list, with a fallback to the NSE F&O lot-size universe.

    The direct Nifty Indices CSV endpoint is known to return 403 to automated scripts.
    We try a few public mirrors and then fall back to the exchange lot-size universe so the
    screener still works rather than failing outright.
    """
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0 Safari/537.36",
            "Accept": "text/csv,application/octet-stream,*/*;q=0.8",
            "Accept-Language": "en-IN,en;q=0.9,en-US;q=0.8",
            "Referer": "https://www.niftyindices.com/",
        }
    )

    try:
        session.get("https://www.niftyindices.com/", timeout=20)
    except Exception:
        pass
    try:
        session.get("https://www.nseindia.com/", timeout=20)
    except Exception:
        pass

    for url in NIFTY200_URLS:
        try:
            response = session.get(url, timeout=25)
            if response.status_code != 200:
                continue
            text = response.text.strip()
            if not text or "forbidden" in text.lower() or "denied" in text.lower():
                continue
            df = pd.read_csv(io.StringIO(text))
            col = next((c for c in df.columns if str(c).strip().upper() in ("SYMBOL", "SYMBOLS")), None)
            if col is not None:
                return sorted(df[col].dropna().astype(str).str.strip().str.upper().tolist())
        except Exception:
            continue

    fallback = nse_fno_lot_sizes()
    if fallback:
        return sorted(fallback.keys())
    return []


def _parse_nse_frame(df):
    """Normalize NSE UDiFF or legacy full-bhavcopy columns."""
    cols = {str(c).strip(): c for c in df.columns}

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
            parsed = _parse_nse_frame(raw)
            if parsed.empty:
                continue
            parsed = parsed[parsed["Date"] == pd.Timestamp(dt)]
            if not parsed.empty:
                return parsed
        except Exception:
            continue
    return None


@st.cache_data(ttl=300, show_spinner=False)
def latest_nse_eod():
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


def _debug_symbol(symbol, message, payload):
    if str(symbol).upper() == "GODREJIND":
        print(f"[DEBUG {message}] {symbol}: {payload}")


def get_udts(symbol, nse_eod):
    try:
        x = _yf_history(symbol)
        if x.empty or len(x) < 40:
            return None

        today = today_ist()
        x = x[x.index < today].copy()
        if nse_eod is None or nse_eod.empty:
            return None

        row = nse_eod[nse_eod["Stock"] == symbol]
        if row.empty:
            row = nse_eod[nse_eod["Stock"].eq(symbol)]
        if row.empty:
            return None

        r = row.iloc[0]
        eod_date = pd.Timestamp(r["Date"]).normalize()
        if eod_date > today:
            return None

        x.loc[eod_date, ["Open", "High", "Low", "Close"]] = [
            float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"])
        ]
        x = x.sort_index()
        if x.index[-1] != eod_date:
            return None

        day = x.iloc[-1]
        week = x.resample("W-FRI").agg(Open=("Open", "first"), High=("High", "max"), Low=("Low", "min"), Close=("Close", "last")).dropna()
        month = x.resample("ME").agg(Open=("Open", "first"), High=("High", "max"), Low=("Low", "min"), Close=("Close", "last")).dropna()
        if week.empty or month.empty:
            return None

        last_data_date = x.index[-1]
        current_week_period = last_data_date.to_period("W-FRI")
        current_month_period = last_data_date.to_period("M")
        week_periods = week.index.to_period("W-FRI")
        month_periods = month.index.to_period("M")

        completed_weeks = week[week_periods < current_week_period]
        completed_months = month[month_periods < current_month_period]

        is_week_complete = last_data_date.weekday() == 4
        is_month_complete = ((last_data_date + pd.Timedelta(days=1)).month != last_data_date.month)
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

        if symbol.upper() == "GODREJIND":
            _debug_symbol(symbol, "UDTS_RAW", {
                "month_open": float(m.Open), "month_close": float(m.Close), "month_red": bool(mr),
                "week_open": float(w.Open), "week_close": float(w.Close), "week_red": bool(wr),
                "day_open": float(day.Open), "day_close": float(day.Close), "day_red": bool(dr),
                "direction": direction,
                "last_data_date": str(last_data_date.date()),
                "is_week_complete": bool(is_week_complete), "is_month_complete": bool(is_month_complete),
            })

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

        today = today_ist().date()
        completed = x[x.index.date < today].copy()
        if completed.empty:
            return None

        dates = sorted(set(completed.index.date))
        if len(dates) < 2:
            return None

        target_date = dates[-1]
        prev_date = dates[-2]

        session = completed[completed.index.date == target_date].copy()
        prev = completed[completed.index.date == prev_date].copy()
        if session.empty or prev.empty:
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
        if float(vol.sum()) > 0:
            vwap = float((typical * vol).sum() / vol.sum())
        else:
            vwap = float(session["Close"].iloc[-1])

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

        daily = completed.groupby(completed.index.date).agg(
            Open=("Open", "first"), High=("High", "max"),
            Low=("Low", "min"), Close=("Close", "last"),
            Volume=("Volume", "sum")
        )
        prior_daily = daily.iloc[:-1].tail(20)
        target_volume = float(daily.iloc[-1]["Volume"])
        avg_prior_volume = float(prior_daily["Volume"].mean()) if not prior_daily.empty else 0.0
        rvol = target_volume / avg_prior_volume if avg_prior_volume > 0 else None

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

        traded_value = daily["Close"].astype(float) * daily["Volume"].astype(float)
        avg_traded_value = float(traded_value.tail(20).mean()) if not traded_value.empty else 0.0
        price = float(session["Close"].iloc[-1])
        target_traded_value = float(price * target_volume)

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


def get_live_intraday_indicators(symbol):
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
        today = now.date()
        completed_bars = x[x.index + pd.Timedelta(minutes=15) <= now].copy()
        if completed_bars.empty:
            return None

        dates = sorted(set(completed_bars.index.date))
        prev_dates = [d for d in dates if d < today]
        if not prev_dates:
            return None
        prev_date = prev_dates[-1]
        prev = completed_bars[completed_bars.index.date == prev_date].copy()
        if prev.empty:
            return None

        today_session = completed_bars[completed_bars.index.date == today].copy()
        if today_session.empty:
            target_date = dates[-1]
            session = completed_bars[completed_bars.index.date == target_date].copy()
        else:
            target_date = today
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

        live_price = get_latest_price(symbol)
        price = float(live_price) if live_price is not None and live_price > 0 else float(session["Close"].iloc[-1])

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
        confirmation = "LONG 4/4" if long_score == 4 else "SHORT 4/4" if short_score == 4 else f"L {long_score}/4 | S {short_score}/4"

        return {
            "CPR": "ABOVE TC" if long_cpr else "BELOW BC" if short_cpr else "INSIDE",
            "VWAP": "ABOVE" if long_vwap else "BELOW" if short_vwap else "—",
            "EMA": "21 > 34" if long_ema else "21 < 34" if short_ema else "21 ≈ 34",
            "RSI": round(rsi14, 2), "CPR Pivot": round(pivot, 2), "CPR TC": round(tc, 2), "CPR BC": round(bc, 2),
            "VWAP Value": round(vwap, 2), "EMA 21": round(ema21, 2), "EMA 34": round(ema34, 2),
            "Confirm Score": max(long_score, short_score), "Confirmation": confirmation,
            "RVOL": round(rvol, 2) if rvol is not None else None, "ADX": round(adx14, 2),
            "Avg Traded Value (₹)": round(avg_traded_value, 0),
            "Target Traded Value (₹)": round(price * target_volume, 0),
            "Indicator Date": pd.Timestamp(target_date).strftime("%d-%b-%Y"),
            "Indicator TF": "15m LIVE",
            "Live Price": round(price, 2),
        }
    except Exception:
        return None


def parse_confirmation_strength(label):
    if label is None:
        return 0
    text = str(label).upper().replace(" ", "")
    if "4/4" in text:
        return 4
    if "3/4" in text:
        return 3
    if "2/4" in text:
        return 2
    if "1/4" in text:
        return 1
    if "L" in text and "S" in text and "|" in text:
        try:
            left = text.split("L")[1].split("|")[0]
            right = text.split("S")[1]
            a = int(left.split("/")[0]) if left.split("/")[0].isdigit() else 0
            b = int(right.split("/")[0]) if right.split("/")[0].isdigit() else 0
            return max(a, b)
        except Exception:
            pass
    return 0


def add_live_confirmation_filter(df, min_strength=8.0, require_4_4=True):
    if df.empty:
        return df
    indicators = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(get_live_intraday_indicators, s): s for s in df["Stock"]}
        for f in as_completed(futures):
            s = futures[f]
            try:
                indicators[s] = f.result()
            except Exception:
                indicators[s] = None

    result = df.copy()
    for field in ["CPR", "VWAP", "EMA", "RSI", "RVOL", "ADX", "Avg Traded Value (₹)",
                  "Target Traded Value (₹)", "CPR Pivot", "CPR TC", "CPR BC", "VWAP Value",
                  "EMA 21", "EMA 34", "Confirm Score", "Confirmation", "Indicator Date",
                  "Indicator TF", "Live Price"]:
        result[field] = result["Stock"].map(lambda s: indicators.get(s, {}).get(field) if indicators.get(s) else None)

    result["ConfirmationStrength"] = result["Confirmation"].apply(parse_confirmation_strength)
    if require_4_4:
        result["Confirmed"] = result.apply(
            lambda r: (r["Direction"] == "LONG" and r.get("ConfirmationStrength", 0) >= 4) or
            (r["Direction"] == "SHORT" and r.get("ConfirmationStrength", 0) >= 4),
            axis=1,
        )
    else:
        result["Confirmed"] = result.apply(
            lambda r: (r["Direction"] == "LONG" and r.get("ConfirmationStrength", 0) >= 2) or
            (r["Direction"] == "SHORT" and r.get("ConfirmationStrength", 0) >= 2),
            axis=1,
        )
    result["Price"] = result["Live Price"].fillna(result["Price"])

    scored = result.apply(calculate_strength_score, axis=1, result_type="expand")
    scored.columns = ["Strength Score", "Mandatory Gate"]
    result[["Strength Score", "Mandatory Gate"]] = scored
    result["Grade"] = result["Strength Score"].apply(lambda v: "A+" if v >= 9 else "A" if v >= 8 else "B" if v >= 7 else "C" if v >= 6 else "D")
    result["Trade Candidate"] = result.apply(lambda r: bool(r["Mandatory Gate"]) and float(r["Strength Score"]) >= min_strength and bool(r["Confirmed"]), axis=1)
    return result


def calculate_strength_score(row):
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


def add_confirmation_filter(df, only_confirmed=False, min_strength=8.0, require_4_4=True):
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

    result["ConfirmationStrength"] = result["Confirmation"].apply(parse_confirmation_strength)
    if require_4_4:
        result["Confirmed"] = result.apply(
            lambda r: (r["Direction"] == "LONG" and r.get("ConfirmationStrength", 0) >= 4) or
            (r["Direction"] == "SHORT" and r.get("ConfirmationStrength", 0) >= 4),
            axis=1,
        )
    else:
        result["Confirmed"] = result.apply(
            lambda r: (r["Direction"] == "LONG" and r.get("ConfirmationStrength", 0) >= 2) or
            (r["Direction"] == "SHORT" and r.get("ConfirmationStrength", 0) >= 2),
            axis=1,
        )

    scored = result.apply(calculate_strength_score, axis=1, result_type="expand")
    scored.columns = ["Strength Score", "Mandatory Gate"]
    result[["Strength Score", "Mandatory Gate"]] = scored
    result["Grade"] = result["Strength Score"].apply(
        lambda v: "A+" if v >= 9 else "A" if v >= 8 else "B" if v >= 7 else "C" if v >= 6 else "D"
    )
    result["Trade Candidate"] = result.apply(
        lambda r: bool(r["Mandatory Gate"]) and float(r["Strength Score"]) >= min_strength and bool(r["Confirmed"]), axis=1
    )
    if only_confirmed:
        result = result[result["Confirmed"]].copy()
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


def _nse_option_session():
    session = requests.Session()
    session.headers.update({
        **NSE_HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    })
    session.get("https://www.nseindia.com/", timeout=20)
    session.get("https://www.nseindia.com/option-chain", timeout=20)
    return session


def nse_option_chain(symbol, session):
    url = "https://www.nseindia.com/api/option-chain-equities"
    last_error = None
    for attempt in range(3):
        try:
            response = session.get(url, params={"symbol": symbol}, timeout=25)
            response.raise_for_status()
            payload = response.json()
            if payload.get("records", {}).get("data"):
                return payload
            last_error = ValueError("NSE returned no option-chain records")
        except Exception as error:
            last_error = error
        if attempt < 2:
            session.get("https://www.nseindia.com/option-chain", timeout=20)
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"NSE option chain unavailable for {symbol}: {last_error}")


def _n(v):
    try: return None if v in (None,"","-") else float(v)
    except Exception: return None


def _option_lot_size(*options):
    for option in options:
        if not option:
            continue
        for field in ("marketLot", "lotSize", "marketlot"):
            value = _n(option.get(field))
            if value and value > 0:
                return int(value)
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def nse_fno_lot_sizes():
    try:
        response = requests.get(
            "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv",
            headers=NSE_HEADERS, timeout=20,
        )
        response.raise_for_status()
        raw = pd.read_csv(io.StringIO(response.text))
        columns = {str(column).strip().upper(): column for column in raw.columns}
        symbol_col = next((column for name, column in columns.items() if "SYMBOL" in name), None)
        lot_col = next((column for name, column in columns.items() if "LOT" in name), None)
        if not symbol_col or not lot_col:
            return {}
        lots = {}
        for _, row in raw[[symbol_col, lot_col]].dropna().iterrows():
            lot = _n(str(row[lot_col]).split()[0])
            if lot and lot > 0:
                lots[str(row[symbol_col]).strip().upper()] = int(lot)
        return lots
    except Exception:
        return {}


def fno_pick(symbol, lot_sizes, session):
    data=nse_option_chain(symbol, session); rec=data.get("records",{})
    spot=_n(rec.get("underlyingValue")); rows=rec.get("data",[])
    if spot is None or not rows: return None
    exps=[]
    for x in rows:
        try: exps.append(pd.to_datetime(x.get("expiryDate"),dayfirst=True))
        except Exception: pass
    if not exps: return None
    expiry=min(exps); expiry_text=expiry.strftime("%d-%b-%Y")
    strikes=[]
    for x in rows:
        if x.get("expiryDate")!=expiry_text: continue
        strike=_n(x.get("strikePrice"))
        if strike is not None:
            strikes.append(strike)
    if not strikes: return None
    atm=min(strikes, key=lambda strike: abs(strike-spot))
    contract=next((x for x in rows if x.get("expiryDate")==expiry_text and _n(x.get("strikePrice"))==atm), None)
    if not contract: return None
    ce, pe = contract.get("CE"), contract.get("PE")
    ce_price = _n(ce.get("lastPrice")) if ce else None
    pe_price = _n(pe.get("lastPrice")) if pe else None
    if not any(price and price > 0 for price in (ce_price, pe_price)):
        return None
    return {
        "Stock": symbol, "Expiry": expiry_text, "Underlying": spot,
        "ATM Strike": atm, "Lot Size": _option_lot_size(ce, pe) or lot_sizes.get(symbol),
        "CE-ATM Price - Long": ce_price,
        "PE-ATM Price - Short": pe_price,
    }


def build_fno_candidates(df, min_strength=9.0, require_4_4=True):
    if df.empty: return pd.DataFrame()
    if "ConfirmationStrength" not in df.columns:
        df = df.copy()
        df["ConfirmationStrength"] = df["Confirmation"].apply(parse_confirmation_strength)
    if require_4_4:
        confirmed = df["ConfirmationStrength"] >= 4
    else:
        confirmed = df["ConfirmationStrength"] >= 2
    sel = df[
        df["Direction"].isin(["LONG", "SHORT"]) &
        confirmed &
        (df["Grade"] == "A+") &
        (pd.to_numeric(df["Strength Score"], errors="coerce") >= min_strength)
    ].copy()
    if sel.empty: return pd.DataFrame()
    sel = sel.sort_values(["ADX", "RVOL"], ascending=[False, False])
    sel = sel.head(15)
    lot_sizes = nse_fno_lot_sizes()
    out=[]
    session = _nse_option_session()
    for _, r in sel.sort_values("Stock").iterrows():
        try:
            q = fno_pick(str(r["Stock"]), lot_sizes, session)
            if q:
                q["Direction"] = r["Direction"]
                q["Strength Score"] = r["Strength Score"]
                q["Grade"] = r["Grade"]
                q["ADX"] = r["ADX"]
                q["RVOL"] = r["RVOL"]
                out.append(q)
        except Exception:
            pass
        time.sleep(0.25)
    return pd.DataFrame(out).sort_values("Stock") if out else pd.DataFrame()


@st.cache_data(ttl=45, show_spinner=False)
def get_index_fno_setup(symbol, option_moneyness="1-step ITM"):
    try:
        ticker = "^NSEI" if symbol == "NIFTY" else "^NSEBANK"
        x = yf.download(ticker, period="10d", interval="5m",
                         auto_adjust=False, progress=False, threads=False)
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
        now = pd.Timestamp.now(tz=IST).tz_localize(None)
        bars = x[x.index + pd.Timedelta(minutes=5) <= now].copy()
        if bars.empty:
            return None

        today = now.date()
        dates = sorted(set(bars.index.date))
        prev_dates = [d for d in dates if d < today]
        if not prev_dates:
            return None
        prev_date = prev_dates[-1]
        prev = bars[bars.index.date == prev_date]
        session = bars[bars.index.date == today]
        target_date = today if not session.empty else dates[-1]
        if session.empty:
            session = bars[bars.index.date == target_date]
        if session.empty:
            return None

        ph, pl, pc = float(prev.High.max()), float(prev.Low.min()), float(prev.Close.iloc[-1])
        pivot = (ph + pl + pc) / 3
        bc = (ph + pl) / 2
        tc = 2 * pivot - bc
        tc, bc = max(tc, bc), min(tc, bc)

        typical = (session.High + session.Low + session.Close) / 3
        vol = pd.to_numeric(session.Volume, errors="coerce").fillna(0)
        vwap = float((typical * vol).sum() / vol.sum()) if float(vol.sum()) > 0 else float(session.Close.iloc[-1])

        q = bars.resample("15min", origin="start_day").agg(
            Open=("Open","first"), High=("High","max"), Low=("Low","min"),
            Close=("Close","last"), Volume=("Volume","sum")).dropna()
        close15 = q.Close.astype(float)
        ema21 = float(close15.ewm(span=21, adjust=False).mean().iloc[-1])
        ema34 = float(close15.ewm(span=34, adjust=False).mean().iloc[-1])
        delta = close15.diff()
        gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss.replace(0, pd.NA)
        rsi = (100 - 100/(1+rs)).fillna(100 if gain.iloc[-1] > 0 else 50)
        rsi14 = float(rsi.iloc[-1])
        price = float(session.Close.iloc[-1])

        long_trend = price > vwap and ema21 > ema34 and rsi14 >= 55
        short_trend = price < vwap and ema21 < ema34 and rsi14 <= 45
        trend = "LONG" if long_trend else "SHORT" if short_trend else "NEUTRAL"

        b = session.tail(8).copy()
        breakout = "WAIT"
        retest_level = None
        if len(b) >= 2:
            a, c = b.iloc[-2], b.iloc[-1]
            long_level = ph if a["High"] >= ph * 0.998 else float(a["High"])
            short_level = pl if a["Low"] <= pl * 1.002 else float(a["Low"])
            if float(a["Close"]) > long_level and float(c["Low"]) <= long_level and float(c["Close"]) > long_level:
                breakout, retest_level = "BULLISH BREAKOUT+RETEST", long_level
            elif float(a["Close"]) < short_level and float(c["High"]) >= short_level and float(c["Close"]) < short_level:
                breakout, retest_level = "BEARISH BREAKOUT+RETEST", short_level
        entry_ok = (trend == "LONG" and breakout == "BULLISH BREAKOUT+RETEST") or (trend == "SHORT" and breakout == "BEARISH BREAKOUT+RETEST")

        direction = trend if entry_ok else "WAIT"
        return {
            "Index": symbol, "Price": price, "15m Trend": trend,
            "Prev Day High": ph, "Prev Day Low": pl,
            "CPR": "ABOVE TC" if price > tc else "BELOW BC" if price < bc else "INSIDE",
            "CPR TC": tc, "CPR BC": bc, "VWAP": "ABOVE" if price > vwap else "BELOW" if price < vwap else "AT",
            "VWAP Value": vwap, "EMA 21": ema21, "EMA 34": ema34,
            "EMA": "21 > 34" if ema21 > ema34 else "21 < 34" if ema21 < ema34 else "21 ≈ 34",
            "RSI": rsi14, "5m Breakout/Retest": breakout, "Retest Level": retest_level,
            "Direction": direction, "Entry Signal": "READY" if entry_ok else "WAIT",
            "Indicator TF": "15m + 5m LIVE", "Indicator Date": pd.Timestamp(target_date).strftime("%d-%b-%Y"),
        }
    except Exception:
        return None


@st.cache_data(ttl=60, show_spinner=False)
def nse_index_option_chain(symbol):
    url = "https://www.nseindia.com/api/option-chain-indices"
    ses = requests.Session(); ses.headers.update(NSE_HEADERS)
    ses.get("https://www.nseindia.com/option-chain", timeout=15)
    r = ses.get(url, params={"symbol": symbol}, timeout=20)
    r.raise_for_status()
    return r.json()


def pick_index_option(symbol, direction, moneyness="1-step ITM"):
    if direction not in ("LONG", "SHORT"):
        return None
    try:
        data = nse_index_option_chain(symbol)
        rec = data.get("records", {})
        spot = _n(rec.get("underlyingValue"))
        rows = rec.get("data", [])
        if spot is None or not rows:
            return None
        expiries = []
        for x in rows:
            try: expiries.append(pd.to_datetime(x.get("expiryDate"), dayfirst=True))
            except Exception: pass
        if not expiries: return None
        expiry = min(expiries)
        expiry_text = expiry.strftime("%d-%b-%Y")
        strikes = sorted({_n(x.get("strikePrice")) for x in rows if _n(x.get("strikePrice")) is not None})
        if not strikes: return None
        atm = min(strikes, key=lambda s: abs(s-spot))
        idx = strikes.index(atm)
        if moneyness == "ATM":
            strike = atm
        elif direction == "LONG":
            strike = strikes[max(0, idx-1)]
        else:
            strike = strikes[min(len(strikes)-1, idx+1)]
        typ = "CE" if direction == "LONG" else "PE"
        chosen = None
        for x in rows:
            if x.get("expiryDate") == expiry_text and _n(x.get("strikePrice")) == strike:
                chosen = x.get(typ); break
        if not chosen: return None
        premium = _n(chosen.get("lastPrice"))
        if premium is None or premium <= 0: return None
        bid, ask = _n(chosen.get("bidprice")), _n(chosen.get("askPrice"))
        spread_pct = ((ask-bid)/premium*100) if bid is not None and ask is not None and ask >= bid else None
        return {"Option": f"{symbol} {strike:.0f} {typ}", "Expiry": expiry_text,
                "Strike": strike, "Type": typ, "Underlying": spot,
                "Premium": premium, "Bid": bid, "Ask": ask, "Spread %": spread_pct,
                "OI": _n(chosen.get("openInterest")), "Volume": _n(chosen.get("totalTradedVolume")),
                "IV": _n(chosen.get("impliedVolatility")), "Moneyness": moneyness}
    except Exception:
        return None


def build_index_fno_table(moneyness="1-step ITM"):
    rows = []
    for symbol in ("NIFTY", "BANKNIFTY"):
        setup = get_index_fno_setup(symbol, moneyness)
        if not setup:
            continue
        if setup["Direction"] in ("LONG", "SHORT"):
            opt = pick_index_option(symbol, setup["Direction"], moneyness)
            if opt:
                setup.update(opt)
                entry = float(opt["Premium"])
                sl = round(entry * 0.80, 2)
                risk = round(entry - sl, 2)
                setup["Option Entry"] = entry
                setup["Fixed SL"] = sl
                setup["Target 1 (1:1.5)"] = round(entry + 1.5*risk, 2)
                setup["Target 2 (1:2)"] = round(entry + 2*risk, 2)
                setup["Risk/Reward"] = "1:1.5 / 1:2"
                rows.append(setup)
        else:
            setup.update({"Option":"—","Expiry":"—","Strike":None,"Type":"—","Underlying":setup["Price"],
                          "Premium":None,"Moneyness":moneyness,"Option Entry":None,"Fixed SL":None,
                          "Target 1 (1:1.5)":None,"Target 2 (1:2)":None,"Risk/Reward":"—"})
            rows.append(setup)
    return pd.DataFrame(rows)

st.title("📈 UDTS 3/3 Mobile Screener")
st.caption("STEP 1: Strict UDTS filter — previous completed Month + Week + Day")

with st.expander("⚙️ Settings"):
    workers = st.slider("Parallel downloads", 2, 12, 8)
    scan_mode = st.radio(
        "Scan style",
        ["Strict 4/4 only", "Broader similar-stock scan"],
        horizontal=True,
        key="scan_mode",
    )

st.markdown("**LONG:** 🟢 Month + 🟢 Week + 🟢 Day")
st.markdown("**SHORT:** 🔴 Month + 🔴 Week + 🔴 Day")

if st.button("🔄 SCAN UDTS ONLY", type="primary", use_container_width=True):
    try:
        syms = symbols()
        with st.spinner("Getting latest NSE EOD bhavcopy..."):
            nse_eod, eod_dt = latest_nse_eod()
        if nse_eod is None:
            st.error("Could not obtain the latest NSE EOD bhavcopy. Scan stopped so stale daily data is not used.")
        else:
            with st.spinner(f"Scanning {len(syms)} stocks for UDTS 3/3..."):
                st.session_state.results = scan(syms, nse_eod, workers)
            st.session_state.scan_time = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S IST")
            st.session_state.eod_date = eod_dt.strftime("%d-%b-%Y")
            st.session_state.confirm_results = None
            st.session_state.confirm_scan_time = None
            st.session_state.fno_results = None
            st.session_state.fno_scan_time = None
            st.session_state.scan_mode = scan_mode
    except Exception as e:
        st.error(f"UDTS scan failed: {e}")

if "results" in st.session_state:
    df = pd.DataFrame(st.session_state.results)
    if not df.empty:
        passed_udts = df[df.Direction.isin(["LONG", "SHORT"])].copy()
        mixed = df[~df.Direction.isin(["LONG", "SHORT"])].copy()
        st.session_state.udts_passed = passed_udts.copy()

        longs_udts = passed_udts[passed_udts.Direction == "LONG"].sort_values("Stock")
        shorts_udts = passed_udts[passed_udts.Direction == "SHORT"].sort_values("Stock")
        a, b, c = st.columns(3)
        a.metric("Scanned", len(df)); b.metric("🟢 UDTS LONG 3/3", len(longs_udts)); c.metric("🔴 UDTS SHORT 3/3", len(shorts_udts))
        st.caption("Last UDTS scan: " + st.session_state.scan_time)
        st.caption(f"NSE EOD candle used: {st.session_state.eod_date}")

        udts_cols = ["Stock", "Month", "Week", "Day", "Direction", "Price"]
        st.subheader("🟢 UDTS LONG — 3/3")
        st.dataframe(longs_udts[udts_cols], hide_index=True, use_container_width=True,
                     column_config={"Price": st.column_config.NumberColumn("Price (₹)", format="₹%.2f")})
        st.subheader("🔴 UDTS SHORT — 3/3")
        st.dataframe(shorts_udts[udts_cols], hide_index=True, use_container_width=True,
                     column_config={"Price": st.column_config.NumberColumn("Price (₹)", format="₹%.2f")})
        with st.expander("⚪ Mixed / rejected by UDTS"):
            st.dataframe(mixed[["Stock", "Month", "Week", "Day", "Direction", "EOD Date"]], hide_index=True, use_container_width=True)
        st.download_button("⬇️ Download UDTS 3/3 List", passed_udts[udts_cols].to_csv(index=False).encode(), "udts_3of3_list.csv", "text/csv", use_container_width=True)

        st.divider()
        st.subheader("🎯 STEP 2 — LIVE CPR → VWAP → EMA 21/34 → RSI")
        st.caption("Uses ONLY UDTS-passed stocks above. It does NOT rescan NIFTY 200.")
        current_scan_mode = st.session_state.get("scan_mode", scan_mode)
        if current_scan_mode == "Broader similar-stock scan":
            st.caption("Broader mode: allows 2/4 or 3/4 live confirmation and lowers the strength threshold to 7 so you catch more Godrej-like setups.")
            min_strength = 7.0
            require_4_4 = False
        else:
            st.caption("Strict mode: only 4/4 live confirmation and strength threshold ≥8 are considered.")
            min_strength = 8.0
            require_4_4 = True

        if st.button("🔄 REFRESH LIVE CONFIRMATION", use_container_width=True):
            try:
                with st.spinner(f"Refreshing Step 2 for {len(passed_udts)} UDTS-passed stocks..."):
                    confirm_df = add_live_confirmation_filter(passed_udts.copy(), min_strength=min_strength, require_4_4=require_4_4)
                st.session_state.confirm_results = confirm_df
                st.session_state.confirm_scan_time = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S IST")
                st.session_state.fno_results = None
                st.session_state.fno_scan_time = None
                st.session_state.scan_mode = current_scan_mode
            except Exception as e:
                st.error(f"Live confirmation refresh failed: {e}")

        if st.session_state.get("confirm_results") is not None:
            confirm_df = st.session_state.confirm_results.copy()
        elif current_scan_mode == "Broader similar-stock scan":
            confirm_df = add_live_confirmation_filter(passed_udts.copy(), min_strength=min_strength, require_4_4=require_4_4)
        else:
            confirm_df = add_live_confirmation_filter(passed_udts.copy(), min_strength=min_strength, require_4_4=require_4_4)

        longs_confirm = confirm_df[confirm_df.Direction == "LONG"].sort_values(["Trade Candidate", "Strength Score", "Confirm Score", "Stock"], ascending=[False, False, False, True])
        shorts_confirm = confirm_df[confirm_df.Direction == "SHORT"].sort_values(["Trade Candidate", "Strength Score", "Confirm Score", "Stock"], ascending=[False, False, False, True])
        x, y, z, q = st.columns(4)
        x.metric("UDTS stocks checked", len(confirm_df)); y.metric("🏆 7+/10", int((confirm_df["Strength Score"] >= 7).sum())); z.metric("🟢 LONG 4/4", int((confirm_df["Confirmation"] == "LONG 4/4").sum())); q.metric("🔴 SHORT 4/4", int((confirm_df["Confirmation"] == "SHORT 4/4").sum()))
        st.caption("Last live confirmation refresh: " + st.session_state.get("confirm_scan_time", "—"))
        st.caption("Indicator timeframe: 15-minute LIVE | Current completed 15m bars included; current in-progress bar excluded")

        confirm_cols = ["Stock", "Direction", "Price", "CPR", "VWAP", "EMA", "RSI", "RVOL", "ADX", "Confirm Score", "Strength Score", "Grade", "Mandatory Gate", "Trade Candidate", "Confirmation"]
        st.markdown("**🟢 LONG confirmation candidates**")
        st.dataframe(longs_confirm[confirm_cols], hide_index=True, use_container_width=True,
                     column_config={"Price": st.column_config.NumberColumn("Live Price (₹)", format="₹%.2f"), "RSI": st.column_config.NumberColumn("RSI", format="%.2f"), "Confirm Score": st.column_config.NumberColumn("Confirm", format="%.0f")})
        st.markdown("**🔴 SHORT confirmation candidates**")
        st.dataframe(shorts_confirm[confirm_cols], hide_index=True, use_container_width=True,
                     column_config={"Price": st.column_config.NumberColumn("Live Price (₹)", format="₹%.2f"), "RSI": st.column_config.NumberColumn("RSI", format="%.2f"), "Confirm Score": st.column_config.NumberColumn("Confirm", format="%.0f")})
        st.caption("10/10 ranking: CPR 2 + VWAP 2 + EMA 21/34 2 + RSI 1 + RVOL 1.5 + ADX 1 + Liquidity 0.5. CPR + VWAP are mandatory gates and score ≥7/10 in Broader mode is used to widen the list beyond exact 4/4 turnarounds.")
        st.download_button("⬇️ Download UDTS-passed + LIVE Confirmation List", confirm_df[confirm_cols].to_csv(index=False).encode(), "udts_3of3_live_confirmation.csv", "text/csv", use_container_width=True)

        st.divider()
        st.subheader("🏦 STEP 3 — TOP 15 F&O CONTRACTS")
        st.caption("Fetches only up to 15 stocks meeting: Grade A+, Strength Score threshold, and matching LONG/SHORT confirmation. In Broader mode the confirmation threshold is lowered so you see more similar setups instead of only exact 4/4 turns.")
        if st.button("🔄 FETCH TOP 15 F&O DETAILS", use_container_width=True):
            try:
                with st.spinner("Getting NSE lot sizes and ATM option prices for the top 15 candidates..."):
                    st.session_state.fno_results = build_fno_candidates(confirm_df.copy(), min_strength=min_strength, require_4_4=require_4_4)
                st.session_state.fno_scan_time = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S IST")
            except Exception as e:
                st.error(f"F&O details refresh failed: {e}")

        if st.session_state.get("fno_results") is not None:
            fno_df = st.session_state.fno_results.copy()
            st.caption("Last F&O refresh: " + st.session_state.fno_scan_time)
            if fno_df.empty:
                st.warning("NSE did not return usable ATM option quotes for the eligible candidates. Try again shortly.")
            else:
                fno_cols = ["Stock", "Direction", "Strength Score", "Grade", "ADX", "RVOL", "Expiry", "Underlying", "ATM Strike", "Lot Size", "CE-ATM Price - Long", "PE-ATM Price - Short"]
                st.dataframe(fno_df[fno_cols], hide_index=True, use_container_width=True,
                    column_config={
                        "Strength Score": st.column_config.NumberColumn("Strength", format="%.1f/10"),
                        "ADX": st.column_config.NumberColumn("ADX", format="%.2f"),
                        "RVOL": st.column_config.NumberColumn("RVOL", format="%.2f"),
                        "Underlying": st.column_config.NumberColumn("Underlying (₹)", format="₹%.2f"),
                        "ATM Strike": st.column_config.NumberColumn("ATM Strike (₹)", format="₹%.2f"),
                        "Lot Size": st.column_config.NumberColumn("Lot Size", format="%d"),
                        "CE-ATM Price - Long": st.column_config.NumberColumn("CE-ATM Price - Long (₹)", format="₹%.2f"),
                        "PE-ATM Price - Short": st.column_config.NumberColumn("PE-ATM Price - Short (₹)", format="₹%.2f"),
                    })
                st.download_button("⬇️ Download Top 15 F&O Details", fno_df.to_csv(index=False).encode(), "udts_top15_fno_details.csv", "text/csv", use_container_width=True)
    else:
        st.warning("No stock data returned.")
else:
    st.info("Tap **SCAN UDTS ONLY** to get the current 3/3 list.")

st.divider()
st.subheader("📊 INDEX F&O TRADING — INDEPENDENT MODULE")
st.caption("Standalone NIFTY/BANKNIFTY intraday module — completely independent of UDTS Steps 1–3. Refresh this directly without running UDTS.")
st.caption("Flow: 15m Trend → CPR + Previous Day High/Low → VWAP → 5m Breakout + Retest → EMA 21/34 → RSI → ATM/1-step ITM → Fixed SL → 1:1.5 / 1:2")

fno_moneyness = st.radio("Option selection", ["1-step ITM", "ATM"], horizontal=True, key="index_fno_moneyness")
if st.button("🔄 REFRESH INDEX F&O SETUP", use_container_width=True):
    try:
        with st.spinner("Checking live NIFTY and BANKNIFTY setup..."):
            st.session_state.index_fno_results = build_index_fno_table(fno_moneyness)
        st.session_state.index_fno_scan_time = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S IST")
    except Exception as e:
        st.error(f"Index F&O refresh failed: {e}")

if st.session_state.get("index_fno_results") is not None:
    idxdf = st.session_state.index_fno_results.copy()
    st.caption("Last Index F&O refresh: " + st.session_state.get("index_fno_scan_time", "—"))
    if idxdf.empty:
        st.info("No index data available right now.")
    else:
        display_cols = ["Index","Price","15m Trend","Prev Day High","Prev Day Low","CPR","VWAP","EMA","RSI",
                        "5m Breakout/Retest","Direction","Entry Signal","Option","Expiry","Type","Strike",
                        "Premium","Option Entry","Fixed SL","Target 1 (1:1.5)","Target 2 (1:2)","Risk/Reward"]
        st.dataframe(idxdf[display_cols], hide_index=True, use_container_width=True,
            column_config={
                "Price": st.column_config.NumberColumn("Index Price", format="%.2f"),
                "Prev Day High": st.column_config.NumberColumn("Prev Day High", format="%.2f"),
                "Prev Day Low": st.column_config.NumberColumn("Prev Day Low", format="%.2f"),
                "RSI": st.column_config.NumberColumn("RSI", format="%.2f"),
                "Premium": st.column_config.NumberColumn("Premium", format="₹%.2f"),
                "Option Entry": st.column_config.NumberColumn("Entry", format="₹%.2f"),
                "Fixed SL": st.column_config.NumberColumn("SL", format="₹%.2f"),
                "Target 1 (1:1.5)": st.column_config.NumberColumn("T1", format="₹%.2f"),
                "Target 2 (1:2)": st.column_config.NumberColumn("T2", format="₹%.2f"),
            })
        st.download_button("⬇️ Download Index F&O Setup", idxdf.to_csv(index=False).encode(),
                           "index_fno_step4.csv", "text/csv", use_container_width=True)
        st.warning("This is a rules-based setup/contract-selection aid, not a trade guarantee. Verify live price, option liquidity, expiry and current exchange lot size before placing any F&O order.")
else:
    st.info("Tap **REFRESH INDEX F&O SETUP** to get the current NIFTY/BANKNIFTY setup — no UDTS scan needed.")

st.caption("Day candle is sourced from NSE EOD bhavcopy; Yahoo Finance is used for historical context, 15-minute bars and current price. The scanner implements the 3/3 candle rule you specified and is designed to work without depending on Nifty Indices' anti-bot CSV endpoint.")

