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
    """Find the latest available NSE EOD bhavcopy, normally yesterday after close."""
    now = today_ist()
    # Try the last 7 calendar days so weekends/holidays are handled automatically.
    for i in range(1, 8):
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
        if eod_date >= today:
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
st.caption("Strict filter: previous completed Month + Week + Day")

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

if st.button("🔄 SCAN UDTS NOW", type="primary", use_container_width=True):
    try:
        syms = symbols()
        with st.spinner("Getting latest NSE EOD bhavcopy..."):
            nse_eod, eod_dt = latest_nse_eod()
        if nse_eod is None:
            st.error("Could not obtain the latest NSE EOD bhavcopy. Scan stopped so stale daily data is not used.")
        else:
            with st.spinner(f"Scanning NIFTY 200 ({len(syms)} stocks)..."):
                st.session_state.results = scan(syms, nse_eod, workers)
                st.session_state.scan_time = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S IST")
                st.session_state.eod_date = eod_dt.strftime("%d-%b-%Y")
    except Exception as e:
        st.error(f"Scan failed: {e}")

if "results" in st.session_state:
    df = pd.DataFrame(st.session_state.results)
    if not df.empty:
        passed = df[df.Direction.isin(["LONG", "SHORT"])].copy()
        with st.spinner("Updating latest prices and capital quantities..."):
            passed = add_capital_filter(passed, capital_limit)

        longs = passed[passed.Direction == "LONG"].sort_values("Stock")
        shorts = passed[passed.Direction == "SHORT"].sort_values("Stock")

        a, b, c = st.columns(3)
        a.metric("Scanned", len(df))
        b.metric("🟢 LONG", len(longs))
        c.metric("🔴 SHORT", len(shorts))
        st.caption("Last scan: " + st.session_state.scan_time)
        st.caption(
            f"NSE EOD candle used: {st.session_state.eod_date} | "
            f"💰 Capital limit: ₹{capital_limit:,.0f} per stock"
        )

        display_cols = [
            "Stock", "Month", "Week", "Day", "Direction",
            "Price", "Max Qty", "Required Capital"
        ]

        st.subheader("🟢 UDTS LONG — 3/3 + Capital")
        st.dataframe(
            longs[display_cols],
            hide_index=True,
            use_container_width=True,
            column_config={
                "Price": st.column_config.NumberColumn("Price (₹)", format="₹%.2f"),
                "Max Qty": st.column_config.NumberColumn("Max Qty", format="%d"),
                "Required Capital": st.column_config.NumberColumn("Required Capital (₹)", format="₹%.2f"),
            },
        )

        st.subheader("🔴 UDTS SHORT — 3/3 + Capital")
        st.dataframe(
            shorts[display_cols],
            hide_index=True,
            use_container_width=True,
            column_config={
                "Price": st.column_config.NumberColumn("Price (₹)", format="₹%.2f"),
                "Max Qty": st.column_config.NumberColumn("Max Qty", format="%d"),
                "Required Capital": st.column_config.NumberColumn("Required Capital (₹)", format="₹%.2f"),
            },
        )

        with st.expander("Mixed / rejected"):
            mixed = df[~df.Direction.isin(["LONG", "SHORT"])]
            st.dataframe(
                mixed[["Stock", "Month", "Week", "Day", "Direction", "EOD Date"]],
                hide_index=True,
                use_container_width=True,
            )

        st.download_button(
            "⬇️ Download 3/3 + Capital List",
            passed[display_cols].to_csv(index=False).encode(),
            "udts_3of3_capital.csv",
            "text/csv",
            use_container_width=True,
        )
    else:
        st.warning("No stock data returned.")
else:
    st.info("Tap SCAN UDTS NOW to get the current 3/3 list.")

st.caption(
    "Day candle is sourced from NSE EOD bhavcopy; Yahoo Finance is used for historical context and intraday price. "
    "The scanner implements the 3/3 candle rule you specified and is not a claim to reproduce IFMC's proprietary full UDTS system."
)
