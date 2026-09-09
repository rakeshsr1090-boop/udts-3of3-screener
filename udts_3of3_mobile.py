import streamlit as st
import pandas as pd
import yfinance as yf
import requests, io
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from zoneinfo import ZoneInfo

st.set_page_config(page_title="UDTS 3/3 Mobile Screener", page_icon="📈", layout="centered")

URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.niftyindices.com/"}

@st.cache_data(ttl=3600)
def symbols():
    r = requests.get(URL, headers=HEADERS, timeout=20)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    col = next(c for c in df.columns if str(c).strip().upper() in ("SYMBOL", "SYMBOLS"))
    return sorted(df[col].dropna().astype(str).str.strip().str.upper().tolist())

def get_udts(symbol):
    try:
        x = yf.download(symbol + ".NS", period="2y", interval="1d",
                         auto_adjust=False, progress=False, threads=False)
        if x.empty:
            return None
        if isinstance(x.columns, pd.MultiIndex):
            x.columns = x.columns.get_level_values(0)
        x = x[["Open","High","Low","Close"]].dropna()
        idx = pd.to_datetime(x.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        x.index = idx.normalize()
        x = x[~x.index.duplicated(keep="last")].sort_index()

        # Exclude today's incomplete daily candle.
        today = pd.Timestamp.now(tz="Asia/Kolkata").normalize().tz_localize(None)
        x = x[x.index < today]
        if len(x) < 40:
            return None

        day = x.iloc[-1]
        week = x.resample("W-FRI").agg(
            Open=("Open","first"), High=("High","max"),
            Low=("Low","min"), Close=("Close","last")
        ).dropna()
        month = x.resample("ME").agg(
            Open=("Open","first"), High=("High","max"),
            Low=("Low","min"), Close=("Close","last")
        ).dropna()

        if week.empty or month.empty:
            return None
        # Use the previous COMPLETED week and month.
        # yfinance daily data may contain the current incomplete week/month,
        # so never use the last period blindly.
        last_data_date = x.index[-1]
        current_week_period = last_data_date.to_period("W-FRI")
        current_month_period = last_data_date.to_period("M")

        week_periods = week.index.to_period("W-FRI")
        month_periods = month.index.to_period("M")

        completed_weeks = week[week_periods < current_week_period]
        completed_months = month[month_periods < current_month_period]

        # If the latest daily candle is the final trading day of its week/month,
        # that period is complete and may be used.
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
        latest_price = float(x["Close"].iloc[-1])
        return {
            "Stock": symbol,
            "Month": "🟢" if mg else "🔴" if mr else "⚪",
            "Week": "🟢" if wg else "🔴" if wr else "⚪",
            "Day": "🟢" if dg else "🔴" if dr else "⚪",
            "Direction": direction,
            "Price": latest_price
        }
    except Exception:
        return None

def get_latest_price(symbol):
    """Get the latest available 5-minute price for an UDTS-passed stock."""
    try:
        x = yf.download(
            symbol + ".NS",
            period="1d",
            interval="5m",
            auto_adjust=False,
            progress=False,
            threads=False
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
    """Add maximum whole-share quantity and required capital."""
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
        axis=1
    )
    return result


def scan(symbol_list, workers=8):
    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(get_udts, s) for s in symbol_list]
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
        help="Maximum amount allocated to one stock. Quantity is rounded down to whole shares."
    )

st.markdown("**LONG:** 🟢 Month + 🟢 Week + 🟢 Day")
st.markdown("**SHORT:** 🔴 Month + 🔴 Week + 🔴 Day")

if st.button("🔄 SCAN UDTS NOW", type="primary", use_container_width=True):
    try:
        syms = symbols()
        with st.spinner(f"Scanning NIFTY 200 ({len(syms)} stocks)..."):
            st.session_state.results = scan(syms, workers)
            st.session_state.scan_time = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d-%b-%Y %H:%M:%S IST")
    except Exception as e:
        st.error(f"Scan failed: {e}")

if "results" in st.session_state:
    df = pd.DataFrame(st.session_state.results)
    if not df.empty:
        passed = df[df.Direction.isin(["LONG","SHORT"])].copy()

        # Only UDTS 3/3 stocks get the intraday price/capital calculation.
        with st.spinner("Updating latest prices and capital quantities..."):
            passed = add_capital_filter(passed, capital_limit)

        longs = passed[passed.Direction == "LONG"].sort_values("Stock")
        shorts = passed[passed.Direction == "SHORT"].sort_values("Stock")

        a,b,c = st.columns(3)
        a.metric("Scanned", len(df))
        b.metric("🟢 LONG", len(longs))
        c.metric("🔴 SHORT", len(shorts))
        st.caption("Last scan: " + st.session_state.scan_time)
        st.caption(f"💰 Capital limit: ₹{capital_limit:,.0f} per stock | Max Qty = whole shares within the limit")

        display_cols = [
            "Stock","Month","Week","Day","Direction",
            "Price","Max Qty","Required Capital"
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
            }
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
            }
        )

        with st.expander("Mixed / rejected"):
            mixed = df[~df.Direction.isin(["LONG","SHORT"])]
            st.dataframe(
                mixed[["Stock","Month","Week","Day","Direction"]],
                hide_index=True,
                use_container_width=True
            )

        st.download_button(
            "⬇️ Download 3/3 + Capital List",
            passed[display_cols].to_csv(index=False).encode(),
            "udts_3of3_capital.csv",
            "text/csv",
            use_container_width=True
        )
    else:
        st.warning("No stock data returned.")
else:
    st.info("Tap SCAN UDTS NOW to get the current 3/3 list.")

st.caption("Implements the 3/3 candle rule you specified and adds a whole-share capital calculation; it is not a claim to reproduce IFMC's proprietary full UDTS system.")
