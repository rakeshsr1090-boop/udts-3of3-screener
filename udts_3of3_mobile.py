import io
import json
import os
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

st.set_page_config(page_title="UDTS + Independent Index F&O", page_icon="📈", layout="wide")

st.markdown(
    """
    <style>
    :root {
        --ink: #17233b;
        --muted: #64748b;
        --line: #e2e8f0;
        --surface: #ffffff;
        --brand: #2457d6;
        --green: #07875b;
        --red: #c43d4b;
    }
    .stApp {
        background:
            radial-gradient(ellipse at 12% 0%, rgba(37, 99, 235, .08), transparent 34rem),
            #f5f7fb;
    }
    [data-testid="stMainBlockContainer"] {
        max-width: 1540px;
        padding-top: 2rem;
        padding-bottom: 4rem;
    }
    [data-testid="stHeader"] {
        background: rgba(245, 247, 251, .82);
    }
    [data-testid="stSidebar"] {
        border-right: 1px solid var(--line);
        background: rgba(255,255,255,.92);
    }
    [data-testid="stSidebar"] [data-testid="stRadio"] > label {
        color: var(--muted);
        font-size: .78rem;
        font-weight: 750;
        letter-spacing: .1em;
        text-transform: uppercase;
    }
    [data-testid="stSidebar"] [role="radiogroup"] label {
        padding: .65rem .75rem;
        border: 1px solid transparent;
        border-radius: 11px;
        color: var(--ink);
        font-weight: 650;
    }
    [data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
        border-color: #c9d8fb;
        background: #edf3ff;
        color: var(--brand);
    }
    .app-hero {
        position: relative;
        overflow: hidden;
        margin: 0 0 1.4rem;
        padding: 1.8rem 2rem;
        border: 1px solid rgba(255,255,255,.16);
        border-radius: 22px;
        color: #fff;
        background: linear-gradient(125deg, #132449 0%, #1f4ca7 60%, #3278d4 100%);
        box-shadow: 0 18px 42px rgba(30, 64, 130, .18);
    }
    .app-hero:after {
        position: absolute;
        top: -105px;
        right: -25px;
        width: 290px;
        height: 290px;
        border: 1px solid rgba(255,255,255,.15);
        border-radius: 50%;
        box-shadow: 0 0 0 34px rgba(255,255,255,.035), 0 0 0 70px rgba(255,255,255,.025);
        content: "";
    }
    .app-hero-kicker {
        margin-bottom: .45rem;
        color: #b9d1ff;
        font-size: .75rem;
        font-weight: 750;
        letter-spacing: .14em;
        text-transform: uppercase;
    }
    .app-hero h1 {
        margin: 0;
        color: #fff;
        font-size: clamp(1.7rem, 3vw, 2.45rem);
        font-weight: 780;
        letter-spacing: -.035em;
        line-height: 1.15;
    }
    .app-hero p {
        max-width: 760px;
        margin: .7rem 0 0;
        color: #d7e4ff;
        font-size: 1rem;
        line-height: 1.55;
    }
    [data-testid="stMetric"] {
        padding: 1rem 1.15rem;
        border: 1px solid var(--line);
        border-radius: 16px;
        background: var(--surface);
        box-shadow: 0 5px 16px rgba(15, 23, 42, .035);
    }
    [data-testid="stMetricLabel"] {
        color: var(--muted);
        font-size: .82rem;
        font-weight: 650;
    }
    [data-testid="stMetricValue"] {
        color: var(--ink);
        font-weight: 760;
    }
    [data-testid="stDataFrame"] {
        overflow: hidden;
        border: 1px solid var(--line);
        border-radius: 14px;
        background: #fff;
        box-shadow: 0 4px 14px rgba(15, 23, 42, .035);
    }
    [data-testid="stExpander"] {
        border: 1px solid var(--line);
        border-radius: 14px;
        background: rgba(255,255,255,.84);
    }
    [data-testid="stButton"] > button,
    [data-testid="stDownloadButton"] > button {
        min-height: 2.8rem;
        border-radius: 11px;
        font-weight: 680;
        transition: transform .15s ease, box-shadow .15s ease;
    }
    [data-testid="stButton"] > button:hover,
    [data-testid="stDownloadButton"] > button:hover {
        transform: translateY(-1px);
        box-shadow: 0 7px 18px rgba(37, 87, 214, .15);
    }
    [data-testid="stMarkdownContainer"] h2,
    [data-testid="stMarkdownContainer"] h3 {
        color: var(--ink);
        letter-spacing: -.025em;
    }
    [data-testid="stMarkdownContainer"] h3 {
        padding-bottom: .45rem;
        border-bottom: 1px solid var(--line);
    }
    [data-testid="stCaptionContainer"] {
        color: var(--muted);
    }
    .signal-guide {
        display: flex;
        flex-wrap: wrap;
        gap: .65rem;
        margin: .75rem 0 1rem;
    }
    .signal-guide span {
        padding: .5rem .8rem;
        border: 1px solid var(--line);
        border-radius: 999px;
        background: rgba(255,255,255,.84);
        color: var(--ink);
        font-size: .88rem;
        font-weight: 650;
    }
    @media (max-width: 700px) {
        [data-testid="stMainBlockContainer"] {
            padding: 1rem .75rem 2.5rem;
        }
        .app-hero {
            padding: 1.35rem 1.15rem;
            border-radius: 17px;
        }
        .app-hero:after {
            right: -180px;
        }
        [data-testid="stMetric"] {
            padding: .8rem;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

IST = ZoneInfo("Asia/Kolkata")
DAILY_RESULTS_FILE = Path(__file__).with_name("udts_daily_results.json")
NIFTY200_URLS = [
    "https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv",
    "https://archives.nseindia.com/content/indices/ind_nifty200list.csv",
    "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv",
]
NIFTY500_URLS = [
    "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
]
SECTOR_INDICES = {
    "NIFTY Bank": "^NSEBANK",
    "NIFTY Financial Services": "^CNXFINANCE",
    "NIFTY IT": "^CNXIT",
    "NIFTY Auto": "^CNXAUTO",
    "NIFTY FMCG": "^CNXFMCG",
    "NIFTY Pharma": "^CNXPHARMA",
    "NIFTY Metal": "^CNXMETAL",
    "NIFTY Realty": "^CNXREALTY",
    "NIFTY Energy": "^CNXENERGY",
    "NIFTY Infrastructure": "^CNXINFRA",
    "NIFTY Media": "^CNXMEDIA",
}
SECTOR_INDEX_FALLBACKS = {
    "NIFTY Financial Services": ("NIFTY_FIN_SERVICE.NS",),
    "NIFTY Media": ("NIFTY_MEDIA.NS",),
}
SECTOR_INDUSTRY_KEYWORDS = {
    "Banks": ("bank",),
    "Financial Services": ("financial", "finance", "insurance", "capital market", "investment"),
    "IT": ("software", "information technology", "it services", "technology"),
    "Auto": ("automobile", "auto ", "auto-", "tyre", "two wheel", "passenger car", "commercial vehicle"),
    "FMCG": ("fmcg", "consumer staples", "personal care", "food product", "beverage", "tobacco"),
    "Pharma": ("pharma", "healthcare", "hospital", "diagnostic", "biotech", "medical equipment"),
    "Metal": ("metal", "steel", "aluminium", "aluminum", "copper", "mining"),
    "Realty": ("real estate", "realty"),
    "Energy": ("energy", "oil", "gas", "power", "petroleum", "utility", "renewable"),
    "Infrastructure": ("infrastructure", "construction", "cement", "capital goods", "industrial", "engineering"),
    "Media": ("media", "entertainment", "broadcast", "publishing"),
}
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-IN,en;q=0.9,en-US;q=0.8",
    "Referer": "https://www.nseindia.com/",
}


def today_ist():
    return pd.Timestamp.now(tz=IST).normalize().tz_localize(None)


def save_daily_results(eod_date, scanned_at, broader_scan, min_strength, results):
    snapshot = {
        "eod_date": eod_date,
        "scanned_at": scanned_at,
        "broader_scan": bool(broader_scan),
        "min_strength": float(min_strength),
        "results": results,
    }
    temporary_file = DAILY_RESULTS_FILE.with_suffix(".json.tmp")
    temporary_file.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    temporary_file.replace(DAILY_RESULTS_FILE)


def list_saved_daily_results():
    if not DAILY_RESULTS_FILE.exists():
        return pd.DataFrame(columns=["eod_date", "scanned_at", "broader_scan", "min_strength"])
    snapshot = json.loads(DAILY_RESULTS_FILE.read_text(encoding="utf-8"))
    return pd.DataFrame([{
        "eod_date": snapshot["eod_date"],
        "scanned_at": snapshot["scanned_at"],
        "broader_scan": snapshot["broader_scan"],
        "min_strength": snapshot["min_strength"],
    }])


def load_daily_results(eod_date):
    if not DAILY_RESULTS_FILE.exists():
        return None
    snapshot = json.loads(DAILY_RESULTS_FILE.read_text(encoding="utf-8"))
    return snapshot if snapshot["eod_date"] == eod_date else None


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


@st.cache_data(ttl=86400, show_spinner=False)
def nse_industry_by_symbol():
    """Fetch NSE's industry classification for NIFTY 500 constituents."""
    session = requests.Session()
    session.headers.update(NSE_HEADERS)

    for url in NIFTY500_URLS:
        try:
            response = session.get(url, timeout=25)
            response.raise_for_status()
            constituents = pd.read_csv(io.StringIO(response.text))
            columns = {str(column).strip().upper(): column for column in constituents.columns}
            symbol_column = columns.get("SYMBOL")
            industry_column = columns.get("INDUSTRY")
            if symbol_column is None or industry_column is None:
                continue

            classifications = constituents[[symbol_column, industry_column]].dropna()
            industry_by_symbol = {
                str(symbol).strip().upper(): str(industry).strip()
                for symbol, industry in classifications.itertuples(index=False, name=None)
                if str(symbol).strip() and str(industry).strip()
            }
            if industry_by_symbol:
                return industry_by_symbol
        except (
            requests.RequestException,
            pd.errors.EmptyDataError,
            pd.errors.ParserError,
            UnicodeDecodeError,
            ValueError,
        ):
            continue

    return {}


def add_nse_industry(df, industry_by_symbol):
    result = df.copy()
    result["NSE Industry"] = (
        result["Stock"].astype(str).str.strip().str.upper()
        .map(industry_by_symbol)
        .fillna("Not classified")
    )
    return result


def _sector_for_industry(industry):
    normalized = str(industry).strip().lower()
    for sector, keywords in SECTOR_INDUSTRY_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return sector
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def yahoo_daily_close_history(tickers):
    """Fetch cached daily closes in batches to keep Yahoo requests bounded."""
    tickers = tuple(dict.fromkeys(tickers))
    if not tickers:
        return pd.DataFrame()

    batches = [tickers[i:i + 60] for i in range(0, len(tickers), 60)]
    closes_by_ticker = {}
    failed_batches = []

    def download_batch(batch):
        return yf.download(
            tickers=list(batch),
            period="1y",
            interval="1d",
            auto_adjust=True,
            group_by="ticker",
            progress=False,
            threads=False,
            timeout=30,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(download_batch, batch): batch for batch in batches}
        for future in as_completed(futures):
            batch = futures[future]
            try:
                prices = future.result()
            except Exception as error:
                failed_batches.append(f"{len(batch)} tickers ({type(error).__name__})")
                continue
            if prices.empty:
                continue

            if isinstance(prices.columns, pd.MultiIndex):
                for ticker in batch:
                    close_column = next(
                        (
                            column for column in prices.columns
                            if ticker in tuple(str(part) for part in column)
                            and "Close" in tuple(str(part) for part in column)
                        ),
                        None,
                    )
                    if close_column is not None:
                        closes_by_ticker[ticker] = prices[close_column].dropna()
            elif len(batch) == 1 and "Close" in prices.columns:
                closes_by_ticker[batch[0]] = prices["Close"].dropna()

    if not closes_by_ticker:
        result = pd.DataFrame()
    else:
        result = pd.DataFrame(closes_by_ticker).sort_index()
    result.attrs["unavailable_tickers"] = sorted(set(tickers) - set(closes_by_ticker))
    result.attrs["failed_batches"] = failed_batches
    return result


def build_sector_dashboard():
    industry_by_symbol = nse_industry_by_symbol()
    if not industry_by_symbol:
        raise RuntimeError("NSE NIFTY 500 industry classifications are unavailable.")

    members_by_sector = {sector: [] for sector in SECTOR_INDUSTRY_KEYWORDS}
    for symbol, industry in industry_by_symbol.items():
        sector = _sector_for_industry(industry)
        if sector:
            members_by_sector[sector].append(symbol + ".NS")

    index_tickers = ("^NSEI", *SECTOR_INDICES.values())
    index_prices = yahoo_daily_close_history(index_tickers)
    if index_prices.empty or "^NSEI" not in index_prices:
        raise RuntimeError("Yahoo Finance did not return NIFTY benchmark history.")

    for sector_name, ticker in SECTOR_INDICES.items():
        existing = index_prices[ticker].dropna() if ticker in index_prices else pd.Series(dtype=float)
        if len(existing) >= 50:
            continue
        for fallback_ticker in SECTOR_INDEX_FALLBACKS.get(sector_name, ()):
            fallback_prices = yahoo_daily_close_history((fallback_ticker,))
            if fallback_ticker not in fallback_prices:
                continue
            fallback_history = fallback_prices[fallback_ticker].dropna()
            if len(fallback_history) < 50:
                continue
            if ticker in index_prices:
                index_prices[ticker] = index_prices[ticker].combine_first(fallback_history)
            else:
                index_prices[ticker] = fallback_history
            break

    stock_tickers = tuple(
        ticker for members in members_by_sector.values() for ticker in members
    )
    stock_prices = yahoo_daily_close_history(stock_tickers)
    benchmark = index_prices["^NSEI"].dropna()
    if len(benchmark) < 22:
        raise RuntimeError("Not enough NIFTY history to calculate sector relative strength.")
    nifty_month_return = (benchmark.iloc[-1] / benchmark.iloc[-22] - 1) * 100

    rows = []
    unavailable_indices = []
    for sector_name, ticker in SECTOR_INDICES.items():
        sector_prices = index_prices[ticker].dropna() if ticker in index_prices else pd.Series(dtype=float)
        if len(sector_prices) < 50:
            unavailable_indices.append(sector_name)
            continue

        close = float(sector_prices.iloc[-1])
        one_week = (sector_prices.iloc[-1] / sector_prices.iloc[-6] - 1) * 100
        one_month = (sector_prices.iloc[-1] / sector_prices.iloc[-22] - 1) * 100
        three_month = (sector_prices.iloc[-1] / sector_prices.iloc[-64] - 1) * 100 if len(sector_prices) >= 64 else None
        above_20dma = close > sector_prices.tail(20).mean()
        above_50dma = close > sector_prices.tail(50).mean()
        index_trend = (
            "Above 20/50DMA" if above_20dma and above_50dma
            else "Below 20/50DMA" if not above_20dma and not above_50dma
            else "Mixed"
        )
        members = members_by_sector.get(
            "Banks" if sector_name == "NIFTY Bank" else
            "Financial Services" if sector_name == "NIFTY Financial Services" else
            sector_name.removeprefix("NIFTY "),
            [],
        )

        above_20_count = 0
        above_50_count = 0
        tracked = 0
        for member in members:
            if member not in stock_prices:
                continue
            history = stock_prices[member].dropna()
            if len(history) < 50:
                continue
            member_close = float(history.iloc[-1])
            tracked += 1
            above_20_count += member_close > history.tail(20).mean()
            above_50_count += member_close > history.tail(50).mean()

        breadth_20 = above_20_count / tracked * 100 if tracked else None
        breadth_50 = above_50_count / tracked * 100 if tracked else None
        relative_strength = one_month - nifty_month_return

        if (
            above_20dma and above_50dma and relative_strength > 0
            and breadth_20 is not None and breadth_20 >= 60
        ):
            signal = "Bullish"
        elif (
            not above_20dma and not above_50dma and relative_strength < 0
            and breadth_20 is not None and breadth_20 <= 40
        ):
            signal = "Bearish"
        else:
            signal = "Mixed / Neutral"

        rows.append({
            "Sector": sector_name,
            "Signal": signal,
            "Index Trend": index_trend,
            "Index": close,
            "1W Return": one_week,
            "1M Return": one_month,
            "3M Return": three_month,
            "1M vs NIFTY": relative_strength,
            "Above 20DMA": breadth_20,
            "Above 50DMA": breadth_50,
            "Breadth Coverage": f"{tracked}/{len(members)}",
        })

    result = pd.DataFrame(rows)
    if result.empty:
        raise RuntimeError("No sector index histories were returned by Yahoo Finance.")
    missing_constituents = stock_prices.attrs.get("unavailable_tickers", [])
    result["Signal Rank"] = result["Signal"].map(
        {"Bullish": 0, "Mixed / Neutral": 1, "Bearish": 2}
    )
    result = result.sort_values(
        ["Signal Rank", "1M vs NIFTY"], ascending=[True, False]
    ).drop(columns="Signal Rank").reset_index(drop=True)
    result.attrs["unavailable_indices"] = unavailable_indices
    result.attrs["nifty_month_return"] = nifty_month_return
    result.attrs["missing_constituents"] = len(missing_constituents)
    result.attrs["constituent_count"] = len(stock_tickers)
    result.attrs["breadth_available"] = not stock_prices.empty
    result.attrs["failed_batches"] = (
        index_prices.attrs.get("failed_batches", [])
        + stock_prices.attrs.get("failed_batches", [])
    )
    return result


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

        # Use the NSE EOD row as the source of truth for the daily candle.
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


def add_live_confirmation_filter(df, min_strength=8.0, broader_mode=False):
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
    fields = ["CPR", "VWAP", "EMA", "RSI", "RVOL", "ADX", "Avg Traded Value (₹)",
              "Target Traded Value (₹)", "CPR Pivot", "CPR TC", "CPR BC", "VWAP Value",
              "EMA 21", "EMA 34", "Confirm Score", "Confirmation", "Indicator Date",
              "Indicator TF", "Live Price"]
    for field in fields:
        result[field] = result["Stock"].map(lambda s: indicators.get(s, {}).get(field) if indicators.get(s) else None)

    result["Price"] = result["Live Price"].fillna(result["Price"])

    result["Confirmed"] = result.apply(
        lambda r: (r["Direction"] == "LONG" and r.get("Confirmation") == "LONG 4/4") or
                  (r["Direction"] == "SHORT" and r.get("Confirmation") == "SHORT 4/4"), axis=1
    )
    scored = result.apply(calculate_strength_score, axis=1, result_type="expand")
    scored.columns = ["Strength Score", "Mandatory Gate"]
    result[["Strength Score", "Mandatory Gate"]] = scored
    result["Grade"] = result["Strength Score"].apply(lambda v: "A+" if v >= 9 else "A" if v >= 8 else "B" if v >= 7 else "C" if v >= 6 else "D")
    result["Trade Candidate"] = result.apply(
        lambda r: bool(r["Mandatory Gate"]) and float(r["Strength Score"]) >= min_strength and (not broader_mode or r["Direction"] in ["LONG", "SHORT"]),
        axis=1,
    )
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


def add_confirmation_filter(df, only_confirmed=False, min_strength=8.0):
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
        lambda r: bool(r["Mandatory Gate"]) and float(r["Strength Score"]) >= min_strength, axis=1
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


def run_scheduled_daily_scan():
    syms = symbols()
    if not syms:
        raise RuntimeError("Could not load a stock universe; daily scan was not saved.")

    nse_eod, eod_dt = latest_nse_eod()
    if nse_eod is None or eod_dt is None:
        raise RuntimeError("Could not obtain the latest NSE EOD bhavcopy; daily scan was not saved.")

    eod_date = eod_dt.strftime("%Y-%m-%d")
    saved_results = list_saved_daily_results()
    if not saved_results.empty and saved_results.iloc[0]["eod_date"] == eod_date:
        print(f"Results for NSE EOD date {eod_date} are already saved; nothing to update.")
        return

    results = scan(syms, nse_eod)
    scanned_at = datetime.now(IST)
    save_daily_results(eod_date, scanned_at.isoformat(), False, 8.0, results)
    print(f"Saved {len(results)} stock results for NSE EOD date {eod_date}.")


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


def build_fno_candidates(df, min_strength=9.0):
    if df.empty: return pd.DataFrame()
    confirmed = (
        ((df["Direction"] == "LONG") & (df["Confirmation"] == "LONG 4/4")) |
        ((df["Direction"] == "SHORT") & (df["Confirmation"] == "SHORT 4/4"))
    )
    sel = df[
        df["Direction"].isin(["LONG", "SHORT"])
        & confirmed
        & (df["Grade"] == "A+")
        & (pd.to_numeric(df["Strength Score"], errors="coerce") >= min_strength)
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

        dates = sorted(set(bars.index.date))
        today = now.date()
        target_date = today if today in dates else dates[-1]
        prev_dates = [d for d in dates if d < target_date]
        if not prev_dates:
            return None
        prev_date = prev_dates[-1]
        prev = bars[bars.index.date == prev_date]
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
        q = q[q.index + pd.Timedelta(minutes=15) <= now]
        if len(q) < 35:
            return None
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
        expiry_rows = [x for x in rows if x.get("expiryDate") == expiry_text]
        strikes = sorted({
            _n(x.get("strikePrice"))
            for x in expiry_rows
            if _n(x.get("strikePrice")) is not None
        })
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
        for x in expiry_rows:
            if _n(x.get("strikePrice")) == strike:
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

if os.environ.get("UDTS_DAILY_SCAN") == "1":
    run_scheduled_daily_scan()
    raise SystemExit(0)

active_module = st.sidebar.radio(
    "Dashboard",
    ["UDTS Screener", "NSE Sector Trend & Breadth", "Index F&O Trading"],
    key="active_dashboard_module",
)

st.markdown(
    """
    <section class="app-hero">
        <div class="app-hero-kicker">Market analysis workspace</div>
        <h1>UDTS 3/3 Screener</h1>
        <p>Find aligned stock trends, review live confirmations, and explore independent
        sector strength and index setups — all in one place.</p>
    </section>
    """,
    unsafe_allow_html=True,
)
if active_module == "UDTS Screener":
    st.caption("STEP 1 · Strict UDTS filter using the previous completed Month + Week + Day")

    with st.expander("⚙️ Settings"):
        workers = st.slider("Parallel downloads", 2, 12, 8)
        broader_scan = st.checkbox("Broader market scan (include more Godrej-style setups)", value=False)
        min_strength = st.slider("Minimum strength filter", 6.5, 10.0, 7.0 if broader_scan else 8.0, 0.5)

    st.markdown(
        """
        <div class="signal-guide">
            <span>🟢 LONG · Month + Week + Day aligned</span>
            <span>🔴 SHORT · Month + Week + Day aligned</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button("🔄 SCAN UDTS ONLY", type="primary", use_container_width=True):
        try:
            syms = symbols()
            if broader_scan:
                syms = broaden_symbol_universe(syms)
            with st.spinner("Getting latest NSE EOD bhavcopy..."):
                nse_eod, eod_dt = latest_nse_eod()
            if nse_eod is None:
                st.error("Could not obtain the latest NSE EOD bhavcopy. Scan stopped so stale daily data is not used.")
            else:
                with st.spinner(f"Scanning {len(syms)} stocks for UDTS 3/3..."):
                    results = scan(syms, nse_eod, workers)
                scan_time = datetime.now(IST)
                save_daily_results(
                    eod_dt.strftime("%Y-%m-%d"),
                    scan_time.isoformat(),
                    broader_scan,
                    min_strength,
                    results,
                )
                st.session_state.results = results
                st.session_state.scan_time = scan_time.strftime("%d-%b-%Y %H:%M:%S IST")
                st.session_state.eod_date = eod_dt.strftime("%d-%b-%Y")
                st.session_state.confirm_results = None
                st.session_state.confirm_scan_time = None
                st.session_state.fno_results = None
                st.session_state.fno_scan_time = None
                st.session_state.broader_scan = broader_scan
                st.session_state.min_strength = min_strength
        except Exception as e:
            st.error(f"UDTS scan failed: {e}")

    with st.expander("📅 Saved daily UDTS results"):
        industry_by_symbol = None
        industry_warning_shown = False
        try:
            saved_days = list_saved_daily_results()
            if saved_days.empty:
                st.info("No daily results have been saved yet. Run a UDTS scan to create the first snapshot.")
            else:
                selected_date = st.selectbox(
                    "Choose an NSE EOD date",
                    saved_days["eod_date"].tolist(),
                    format_func=lambda value: pd.Timestamp(value).strftime("%d-%b-%Y"),
                    key="saved_udts_date",
                )
                saved_result = load_daily_results(selected_date)
                if saved_result is None:
                    st.error("The selected saved result could not be found.")
                else:
                    st.caption(
                        f"Scanned: {pd.Timestamp(saved_result['scanned_at']).strftime('%d-%b-%Y %H:%M:%S %Z')} | "
                        f"Broader scan: {'ON' if saved_result['broader_scan'] else 'OFF'} | "
                        f"Minimum strength: {saved_result['min_strength']:.1f}"
                    )
                    saved_df = pd.DataFrame(saved_result["results"])
                    if saved_df.empty:
                        st.info("The saved scan had no matching stocks.")
                    else:
                        industry_by_symbol = nse_industry_by_symbol()
                        if not industry_by_symbol:
                            st.warning("NSE industry classifications could not be loaded; stocks will show as Not classified.")
                            industry_warning_shown = True
                        saved_df = add_nse_industry(saved_df, industry_by_symbol)
                        history_cols = ["Stock", "NSE Industry", "Month", "Week", "Day", "Direction", "Price", "EOD Date"]
                        history_df = saved_df[history_cols].sort_values(["Direction", "Stock"])
                        st.dataframe(
                            history_df,
                            hide_index=True,
                            use_container_width=True,
                            column_config={
                                "Price": st.column_config.NumberColumn("Price (₹)", format="₹%.2f")
                            },
                        )
                        st.download_button(
                            "⬇️ Download saved daily results",
                            history_df.to_csv(index=False).encode(),
                            f"udts_results_{selected_date}.csv",
                            "text/csv",
                            use_container_width=True,
                            key="download_saved_udts_results",
                        )
        except (json.JSONDecodeError, KeyError, OSError, ValueError) as e:
            st.error(f"Could not load saved daily results: {e}")

    if "results" in st.session_state:
        df = pd.DataFrame(st.session_state.results)
        if not df.empty:
            if industry_by_symbol is None:
                industry_by_symbol = nse_industry_by_symbol()
            if not industry_by_symbol and not industry_warning_shown:
                st.warning("NSE industry classifications could not be loaded; stocks will show as Not classified.")
                industry_warning_shown = True
            df = add_nse_industry(df, industry_by_symbol)
            passed_udts = df[df.Direction.isin(["LONG", "SHORT"])].copy()
            mixed = df[~df.Direction.isin(["LONG", "SHORT"])].copy()
            st.session_state.udts_passed = passed_udts.copy()

            longs_udts = passed_udts[passed_udts.Direction == "LONG"].sort_values("Stock")
            shorts_udts = passed_udts[passed_udts.Direction == "SHORT"].sort_values("Stock")
            a, b, c = st.columns(3)
            a.metric("Scanned", len(df)); b.metric("🟢 UDTS LONG 3/3", len(longs_udts)); c.metric("🔴 UDTS SHORT 3/3", len(shorts_udts))
            st.caption("Last UDTS scan: " + st.session_state.scan_time)
            st.caption(f"NSE EOD candle used: {st.session_state.eod_date}")
            st.caption("Broader scan mode: ON" if st.session_state.get("broader_scan", False) else "Broader scan mode: OFF")

            udts_cols = ["Stock", "NSE Industry", "Month", "Week", "Day", "Direction", "Price"]
            st.subheader("🟢 UDTS LONG — 3/3")
            st.dataframe(longs_udts[udts_cols], hide_index=True, use_container_width=True,
                         column_config={"Price": st.column_config.NumberColumn("Price (₹)", format="₹%.2f")})
            st.subheader("🔴 UDTS SHORT — 3/3")
            st.dataframe(shorts_udts[udts_cols], hide_index=True, use_container_width=True,
                         column_config={"Price": st.column_config.NumberColumn("Price (₹)", format="₹%.2f")})
            with st.expander("⚪ Mixed / rejected by UDTS"):
                aligned_month_week = mixed["Month"].eq(mixed["Week"]) & mixed["Month"].isin(["🟢", "🔴"])
                daily_only_rejected = mixed.loc[
                    aligned_month_week & mixed["Day"].ne(mixed["Month"]),
                    ["Stock", "NSE Industry", "Month", "Week", "Day", "EOD Date"],
                ].copy()
                daily_only_rejected["UDTS Side"] = daily_only_rejected["Month"].map(
                    {"🟢": "LONG", "🔴": "SHORT"}
                )
                st.markdown("**Monthly + Weekly passed, Daily failed**")
                st.caption("Month and Week agree for LONG or SHORT, but the daily candle does not confirm that side.")
                st.dataframe(
                    daily_only_rejected[["Stock", "NSE Industry", "UDTS Side", "Month", "Week", "Day", "EOD Date"]],
                    hide_index=True,
                    use_container_width=True,
                )
                st.markdown("**All mixed / rejected results**")
                st.dataframe(mixed[["Stock", "NSE Industry", "Month", "Week", "Day", "Direction", "EOD Date"]], hide_index=True, use_container_width=True)
            st.download_button("⬇️ Download UDTS 3/3 List", passed_udts[udts_cols].to_csv(index=False).encode(), "udts_3of3_list.csv", "text/csv", use_container_width=True)

            st.divider()
            st.subheader("🎯 STEP 2 — LIVE CPR → VWAP → EMA 21/34 → RSI")
            st.caption("Uses ONLY UDTS-passed stocks above. The broader-scan slider relaxes the score threshold so you capture more Godrej-style names without breaking the core rules.")

            if st.button("🔄 REFRESH LIVE CONFIRMATION", use_container_width=True):
                try:
                    with st.spinner(f"Refreshing Step 2 for {len(passed_udts)} UDTS-passed stocks..."):
                        confirm_df = add_live_confirmation_filter(
                            passed_udts.copy(),
                            min_strength=st.session_state.get("min_strength", 7.0 if st.session_state.get("broader_scan", False) else 8.0),
                            broader_mode=st.session_state.get("broader_scan", False),
                        )
                    st.session_state.confirm_results = confirm_df
                    st.session_state.confirm_scan_time = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S IST")
                    st.session_state.fno_results = None
                    st.session_state.fno_scan_time = None
                except Exception as e:
                    st.error(f"Live confirmation refresh failed: {e}")

            if st.session_state.get("confirm_results") is not None:
                confirm_df = st.session_state.confirm_results.copy()
                longs_confirm = confirm_df[confirm_df.Direction == "LONG"].sort_values(["Trade Candidate", "Strength Score", "Confirm Score", "Stock"], ascending=[False, False, False, True])
                shorts_confirm = confirm_df[confirm_df.Direction == "SHORT"].sort_values(["Trade Candidate", "Strength Score", "Confirm Score", "Stock"], ascending=[False, False, False, True])
                x, y, z, q = st.columns(4)
                x.metric("UDTS stocks checked", len(confirm_df)); y.metric("🏆 above threshold", int((confirm_df["Strength Score"] >= st.session_state.get("min_strength", 8.0)).sum())); z.metric("🟢 LONG 4/4", int((confirm_df["Confirmation"] == "LONG 4/4").sum())); q.metric("🔴 SHORT 4/4", int((confirm_df["Confirmation"] == "SHORT 4/4").sum()))
                st.caption("Last live confirmation refresh: " + st.session_state.confirm_scan_time)
                st.caption("Indicator timeframe: 15-minute LIVE | Current completed 15m bars included; current in-progress bar excluded")

                confirm_cols = ["Stock", "NSE Industry", "Direction", "Price", "CPR", "VWAP", "EMA", "RSI", "RVOL", "ADX", "Confirm Score", "Strength Score", "Grade", "Mandatory Gate", "Trade Candidate", "Confirmation"]
                st.markdown("**🟢 LONG confirmation candidates**")
                st.dataframe(longs_confirm[confirm_cols], hide_index=True, use_container_width=True,
                             column_config={"Price": st.column_config.NumberColumn("Live Price (₹)", format="₹%.2f"), "RSI": st.column_config.NumberColumn("RSI", format="%.2f"), "Confirm Score": st.column_config.NumberColumn("Confirm", format="%.0f")})
                st.markdown("**🔴 SHORT confirmation candidates**")
                st.dataframe(shorts_confirm[confirm_cols], hide_index=True, use_container_width=True,
                             column_config={"Price": st.column_config.NumberColumn("Live Price (₹)", format="₹%.2f"), "RSI": st.column_config.NumberColumn("RSI", format="%.2f"), "Confirm Score": st.column_config.NumberColumn("Confirm", format="%.0f")})
                st.caption("10/10 ranking: CPR 2 + VWAP 2 + EMA 21/34 2 + RSI 1 + RVOL 1.5 + ADX 1 + Liquidity 0.5. CPR + VWAP are mandatory gates and score ≥8/10 is the trade-candidate threshold. Broader mode shifts the threshold lower to capture more similar setups.")
                st.download_button("⬇️ Download UDTS-passed + LIVE Confirmation List", confirm_df[confirm_cols].to_csv(index=False).encode(), "udts_3of3_live_confirmation.csv", "text/csv", use_container_width=True)

                st.divider()
                st.subheader("🏦 STEP 3 — TOP 15 F&O CONTRACTS")
                st.caption("Fetches only up to 15 stocks meeting: Grade A+, Strength Score ≥ threshold, and matching LONG/SHORT 4/4 confirmation. Broader mode uses a lower threshold and a wider universe so you don’t miss Godrej-like setups.")
                if st.button("🔄 FETCH TOP 15 F&O DETAILS", use_container_width=True):
                    try:
                        with st.spinner("Getting NSE lot sizes and ATM option prices for the top 15 candidates..."):
                            st.session_state.fno_results = build_fno_candidates(confirm_df.copy(), min_strength=st.session_state.get("min_strength", 8.0))
                        st.session_state.fno_scan_time = datetime.now(IST).strftime("%d-%b-%Y %H:%M:%S IST")
                    except Exception as e:
                        st.error(f"F&O details refresh failed: {e}")

                if st.session_state.get("fno_results") is not None:
                    fno_df = st.session_state.fno_results.copy()
                    st.caption("Last F&O refresh: " + st.session_state.fno_scan_time)
                    if fno_df.empty:
                        st.warning("NSE did not return usable ATM option quotes for the eligible candidates. Try again shortly.")
                    else:
                        fno_df = add_nse_industry(fno_df, industry_by_symbol or {})
                        fno_cols = ["Stock", "NSE Industry", "Direction", "Strength Score", "Grade", "ADX", "RVOL", "Expiry", "Underlying", "ATM Strike", "Lot Size", "CE-ATM Price - Long", "PE-ATM Price - Short"]
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
                st.info("UDTS is ready. Tap **REFRESH LIVE CONFIRMATION** during market hours for the current Step-2 setup.")
        else:
            st.warning("No stock data returned.")
    else:
        st.info("Tap **SCAN UDTS ONLY** to get the current 3/3 list.")

if active_module == "NSE Sector Trend & Breadth":
    st.divider()
    st.subheader("🧭 NSE SECTOR TREND & BREADTH — INDEPENDENT MODULE")
    st.caption(
        "Compares NSE sector indices with NIFTY and measures NIFTY 500 constituent breadth. "
        "This analysis is independent of the UDTS monthly/weekly/daily rules."
    )
    if st.button("🔄 REFRESH SECTOR TREND", use_container_width=True):
        try:
            with st.spinner("Loading NSE industry classifications and sector/stock history..."):
                st.session_state.sector_dashboard_results = build_sector_dashboard()
            st.session_state.sector_dashboard_scan_time = datetime.now(IST).strftime(
                "%d-%b-%Y %H:%M:%S IST"
            )
        except Exception as e:
            st.error(f"Sector dashboard refresh failed: {e}")

    if st.session_state.get("sector_dashboard_results") is not None:
        sector_df = st.session_state.sector_dashboard_results.copy()
        st.caption(
            "Last sector refresh: "
            + st.session_state.get("sector_dashboard_scan_time", "—")
        )
        unavailable_indices = sector_df.attrs.get("unavailable_indices", [])
        if unavailable_indices:
            st.warning(
                "Yahoo Finance did not return enough history for: "
                + ", ".join(unavailable_indices)
            )
        constituent_count = sector_df.attrs.get("constituent_count", 0)
        missing_constituents = sector_df.attrs.get("missing_constituents", 0)
        if not sector_df.attrs.get("breadth_available", False):
            st.warning(
                "Constituent breadth is unavailable; sectors will remain Mixed / Neutral "
                "because breadth is required for a directional label."
            )
        elif missing_constituents:
            st.warning(
                f"Yahoo Finance returned no usable history for {missing_constituents} of "
                f"{constituent_count} classified constituents. Breadth coverage is shown "
                "per sector."
            )
        failed_batches = sector_df.attrs.get("failed_batches", [])
        if failed_batches:
            st.warning(
                "Some Yahoo Finance history batches failed: " + "; ".join(failed_batches)
            )
        if sector_df.empty:
            st.info("No sector data is available right now.")
        else:
            st.dataframe(
                sector_df,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Index": st.column_config.NumberColumn("Index Level", format="%.2f"),
                    "1W Return": st.column_config.NumberColumn("1W Return", format="%.2f%%"),
                    "1M Return": st.column_config.NumberColumn("1M Return", format="%.2f%%"),
                    "3M Return": st.column_config.NumberColumn("3M Return", format="%.2f%%"),
                    "1M vs NIFTY": st.column_config.NumberColumn(
                        "1M Relative Strength vs NIFTY (pp)", format="%.2f"
                    ),
                    "Above 20DMA": st.column_config.NumberColumn(
                        "Stocks Above 20DMA", format="%.1f%%"
                    ),
                    "Above 50DMA": st.column_config.NumberColumn(
                        "Stocks Above 50DMA", format="%.1f%%"
                    ),
                },
            )
            st.caption(
                "Bullish requires the sector index above its 20/50-day averages, "
                "outperformance versus NIFTY over one month, and at least 60% of "
                "tracked constituents above their 20-day average. Bearish uses the "
                "opposite trend/relative-strength conditions and 40% or lower breadth; "
                "all other combinations are Mixed / Neutral. Breadth coverage shows "
                "tracked constituents versus NSE-classified NIFTY 500 constituents."
            )
            st.download_button(
                "⬇️ Download Sector Trend & Breadth",
                sector_df.to_csv(index=False).encode(),
                "nse_sector_trend_breadth.csv",
                "text/csv",
                use_container_width=True,
            )
    else:
        st.info("Tap **REFRESH SECTOR TREND** to view the independent sector dashboard.")

if active_module == "Index F&O Trading":
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
