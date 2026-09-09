import streamlit as st
import pandas as pd
import yfinance as yf
import requests, io
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

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
        today = pd.Timestamp.now().normalize()
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
        week, month = week[week.index < today], month[month.index < today]
        if week.empty or month.empty:
            return None

        m = month.iloc[-1]; w = week.iloc[-1]
        mg, wg, dg = m.Close > m.Open, w.Close > w.Open, day.Close > day.Open
        mr, wr, dr = m.Close < m.Open, w.Close < w.Open, day.Close < day.Open

        direction = "LONG" if mg and wg and dg else "SHORT" if mr and wr and dr else "—"
        return {
            "Stock": symbol,
            "Month": "🟢" if mg else "🔴" if mr else "⚪",
            "Week": "🟢" if wg else "🔴" if wr else "⚪",
            "Day": "🟢" if dg else "🔴" if dr else "⚪",
            "Direction": direction
        }
    except Exception:
        return None

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

st.markdown("**LONG:** 🟢 Month + 🟢 Week + 🟢 Day")
st.markdown("**SHORT:** 🔴 Month + 🔴 Week + 🔴 Day")

if st.button("🔄 SCAN UDTS NOW", type="primary", use_container_width=True):
    try:
        syms = symbols()
        with st.spinner(f"Scanning NIFTY 200 ({len(syms)} stocks)..."):
            st.session_state.results = scan(syms, workers)
            st.session_state.scan_time = datetime.now().strftime("%d-%b-%Y %H:%M:%S")
    except Exception as e:
        st.error(f"Scan failed: {e}")

if "results" in st.session_state:
    df = pd.DataFrame(st.session_state.results)
    if not df.empty:
        passed = df[df.Direction.isin(["LONG","SHORT"])]
        longs = passed[passed.Direction == "LONG"].sort_values("Stock")
        shorts = passed[passed.Direction == "SHORT"].sort_values("Stock")

        a,b,c = st.columns(3)
        a.metric("Scanned", len(df))
        b.metric("🟢 LONG", len(longs))
        c.metric("🔴 SHORT", len(shorts))
        st.caption("Last scan: " + st.session_state.scan_time)

        st.subheader("🟢 UDTS LONG — 3/3")
        st.dataframe(longs[["Stock","Month","Week","Day","Direction"]],
                     hide_index=True, use_container_width=True)

        st.subheader("🔴 UDTS SHORT — 3/3")
        st.dataframe(shorts[["Stock","Month","Week","Day","Direction"]],
                     hide_index=True, use_container_width=True)

        with st.expander("Mixed / rejected"):
            mixed = df[~df.Direction.isin(["LONG","SHORT"])]
            st.dataframe(mixed[["Stock","Month","Week","Day","Direction"]],
                         hide_index=True, use_container_width=True)

        st.download_button(
            "⬇️ Download 3/3 List",
            passed[["Stock","Month","Week","Day","Direction"]].to_csv(index=False).encode(),
            "udts_3of3.csv", "text/csv", use_container_width=True
        )
    else:
        st.warning("No stock data returned.")
else:
    st.info("Tap SCAN UDTS NOW to get the current 3/3 list.")

st.caption("Implements the 3/3 candle rule you specified; it is not a claim to reproduce IFMC's proprietary full UDTS system.")
