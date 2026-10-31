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


def broaden_symbol_universe(symbols_list):
    """Broaden the default NIFTY-200 scan by adding the NSE F&O lot-size universe.

    This keeps the main screen from being too tight while still filtering out random
    microcaps and low-liquidity names. It is used only when the user enables the
    broader scan mode.
    """
    expanded = []
    for s in symbols_list or []:
        if s is None:
            continue
        val = str(s).strip().upper()
        if val:
            expanded.append(val)

    fno = nse_fno_lot_sizes()
    if fno:
        for s in fno.keys():
            val = str(s).strip().upper()
            if val:
                expanded.append(val)

    return sorted(dict.fromkeys(expanded))


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

        # Use NSE EOD row as the source of truth for the daily candle.
        # The final daily candle must not be derived from a merged Yahoo series,
        # because that can drift or misclassify the last candle.
        day_open = float(r["Open"])
        day_close = float(r["Close"])
        day_high = float(r["High"])
        day_low = float(r["Low"])

        x.loc[eod_date, ["Open", "High", "Low", "Close"]] = [
            day_open, day_high, day_low, day_close
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
        mg, wg, dg = m.Close > m.Open, w.Close > w.Open, day_close > day_open
        mr, wr, dr = m.Close < m.Open, w.Close < w.Open, day_close < day_open
        direction = "LONG" if mg and wg and dg else "SHORT" if mr and wr and dr else "—"

        if symbol.upper() == "GODREJIND":
            _debug_symbol(symbol, "UDTS_RAW", {
                "month_open": float(m.Open), "month_close": float(m.Close), "month_red": bool(mr),
                "week_open": float(w.Open), "week_close": float(w.Close), "week_red": bool(wr),
                "day_open": float(day_open), "day_close": float(day_close), "day_red": bool(dr),
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


# ... rest of file unchanged ...
