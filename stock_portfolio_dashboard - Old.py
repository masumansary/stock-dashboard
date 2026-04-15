import sqlite3
from datetime import datetime
from typing import Optional

import pandas as pd
import plotly.express as px
import streamlit as st
import yfinance as yf
from streamlit_autorefresh import st_autorefresh

# =========================
# Page config
# =========================
st.set_page_config(
    page_title="Portfolio Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# =========================
# Styling
# =========================
st.markdown(
    """
    <style>
        .main {
            background-color: #0f172a;
        }
        .block-container {
            padding-top: 1.5rem;
            padding-bottom: 1rem;
            max-width: 1400px;
        }
        .metric-card {
            background: linear-gradient(135deg, #111827, #1f2937);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 18px;
            padding: 18px 20px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.18);
        }
        .section-card {
            background: #111827;
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 18px;
            padding: 18px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.12);
        }
        .title-text {
            font-size: 2rem;
            font-weight: 700;
            color: #f8fafc;
            margin-bottom: 0.15rem;
        }
        .subtle-text {
            color: #94a3b8;
            font-size: 0.95rem;
            margin-bottom: 1rem;
        }
        .profit-text {
            color: #22c55e;
            font-weight: 700;
        }
        .loss-text {
            color: #ef4444;
            font-weight: 700;
        }
        .stDataFrame, .stTable {
            border-radius: 14px;
            overflow: hidden;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# =========================
# Auto refresh (5 minutes)
# =========================
st_autorefresh(interval=300000, key="portfolio_refresh")

DB_PATH = "portfolio.db"
TRANSACTION_TYPES = ["BUY", "SELL"]


# =========================
# Database helpers
# =========================
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            transaction_type TEXT NOT NULL CHECK (transaction_type IN ('BUY', 'SELL')),
            quantity REAL NOT NULL CHECK (quantity > 0),
            price REAL NOT NULL CHECK (price > 0),
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


@st.cache_data(ttl=60)
def load_transactions() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql_query(
        """
        SELECT id, trade_date, UPPER(TRIM(ticker)) AS ticker, transaction_type, quantity, price, created_at
        FROM transactions
        ORDER BY date(trade_date) ASC, id ASC
        """,
        conn,
    )
    conn.close()
    return df


def insert_transaction(trade_date: str, ticker: str, transaction_type: str, quantity: float, price: float) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO transactions (trade_date, ticker, transaction_type, quantity, price, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            trade_date,
            ticker.upper().strip(),
            transaction_type,
            float(quantity),
            float(price),
            datetime.now().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()
    load_transactions.clear()
    get_latest_prices.clear()


# =========================
# Portfolio calculation
# =========================
def compute_current_holdings(transactions: pd.DataFrame) -> pd.DataFrame:
    if transactions.empty:
        return pd.DataFrame(
            columns=[
                "ticker",
                "quantity",
                "avg_cost",
                "invested_amount",
            ]
        )

    holdings = {}

    for _, row in transactions.iterrows():
        ticker = row["ticker"]
        tx_type = row["transaction_type"]
        qty = float(row["quantity"])
        price = float(row["price"])

        if ticker not in holdings:
            holdings[ticker] = {"quantity": 0.0, "cost": 0.0}

        current_qty = holdings[ticker]["quantity"]
        current_cost = holdings[ticker]["cost"]

        if tx_type == "BUY":
            holdings[ticker]["quantity"] = current_qty + qty
            holdings[ticker]["cost"] = current_cost + (qty * price)
        elif tx_type == "SELL":
            if current_qty <= 0:
                continue

            sell_qty = min(qty, current_qty)
            avg_cost_before_sell = current_cost / current_qty if current_qty > 0 else 0
            holdings[ticker]["quantity"] = current_qty - sell_qty
            holdings[ticker]["cost"] = current_cost - (sell_qty * avg_cost_before_sell)

    records = []
    for ticker, values in holdings.items():
        qty = round(values["quantity"], 8)
        if qty > 0:
            invested = max(values["cost"], 0)
            avg_cost = invested / qty if qty else 0
            records.append(
                {
                    "ticker": ticker,
                    "quantity": qty,
                    "avg_cost": avg_cost,
                    "invested_amount": invested,
                }
            )

    return pd.DataFrame(records).sort_values("ticker").reset_index(drop=True)


@st.cache_data(ttl=300)
def get_latest_prices(tickers: tuple) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame(columns=["ticker", "current_price"])

    price_rows = []

    for ticker in tickers:
        current_price: Optional[float] = None
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period="5d", interval="1d", auto_adjust=False)
            if not hist.empty:
                current_price = float(hist["Close"].dropna().iloc[-1])
        except Exception:
            current_price = None

        price_rows.append({"ticker": ticker, "current_price": current_price})

    return pd.DataFrame(price_rows)


def build_analytics(holdings_df: pd.DataFrame, prices_df: pd.DataFrame) -> pd.DataFrame:
    if holdings_df.empty:
        return pd.DataFrame()

    df = holdings_df.merge(prices_df, on="ticker", how="left")
    df["current_price"] = pd.to_numeric(df["current_price"], errors="coerce")
    df["market_value"] = df["quantity"] * df["current_price"]
    df["gain_loss"] = df["market_value"] - df["invested_amount"]
    df["return_pct"] = df.apply(
        lambda x: (x["gain_loss"] / x["invested_amount"] * 100) if x["invested_amount"] else 0,
        axis=1,
    )

    total_market_value = df["market_value"].sum(skipna=True)
    df["allocation_pct"] = df.apply(
        lambda x: (x["market_value"] / total_market_value * 100) if total_market_value else 0,
        axis=1,
    )

    return df.sort_values("market_value", ascending=False).reset_index(drop=True)


def format_currency(value: float) -> str:
    return f"${value:,.2f}"


def format_percentage(value: float) -> str:
    return f"{value:,.2f}%"


# =========================
# UI helpers
# =========================
def show_metric_card(label: str, value: str, delta: Optional[str] = None, delta_positive: Optional[bool] = None) -> None:
    delta_html = ""
    if delta is not None:
        css_class = "profit-text" if delta_positive else "loss-text"
        delta_html = f'<div class="{css_class}" style="margin-top:8px; font-size:0.95rem;">{delta}</div>'

    st.markdown(
        f"""
        <div class="metric-card">
            <div style="color:#94a3b8; font-size:0.95rem;">{label}</div>
            <div style="color:#f8fafc; font-size:1.75rem; font-weight:700; margin-top:6px;">{value}</div>
            {delta_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_header() -> None:
    st.markdown('<div class="title-text">US Stock Portfolio Dashboard</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subtle-text">Tracks only your currently held stocks using full buy/sell transaction history. Prices refresh automatically every 5 minutes.</div>',
        unsafe_allow_html=True,
    )


# =========================
# App
# =========================
def main() -> None:
    init_db()
    render_header()

    top_left, top_right = st.columns([4, 1])
    with top_right:
        if st.button("🔄 Refresh Now", use_container_width=True):
            load_transactions.clear()
            get_latest_prices.clear()
            st.rerun()

    transactions_df = load_transactions()
    holdings_df = compute_current_holdings(transactions_df)

    if holdings_df.empty:
        st.info("No active holdings yet. Add your first BUY transaction to start the dashboard.")
    else:
        tickers = tuple(sorted(holdings_df["ticker"].unique().tolist()))
        prices_df = get_latest_prices(tickers)
        analytics_df = build_analytics(holdings_df, prices_df)

        total_invested = analytics_df["invested_amount"].sum(skipna=True)
        total_value = analytics_df["market_value"].sum(skipna=True)
        total_gain_loss = analytics_df["gain_loss"].sum(skipna=True)
        total_return_pct = (total_gain_loss / total_invested * 100) if total_invested else 0

        best_row = analytics_df.loc[analytics_df["gain_loss"].idxmax()] if not analytics_df.empty else None
        worst_row = analytics_df.loc[analytics_df["gain_loss"].idxmin()] if not analytics_df.empty else None

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            show_metric_card("Total Invested", format_currency(total_invested))
        with c2:
            show_metric_card("Current Value", format_currency(total_value))
        with c3:
            show_metric_card(
                "Total Gain / Loss",
                format_currency(total_gain_loss),
                format_percentage(total_return_pct),
                total_gain_loss >= 0,
            )
        with c4:
            show_metric_card("Active Holdings", str(len(analytics_df)))

        st.write("")
        d1, d2 = st.columns(2)
        with d1:
            if best_row is not None:
                st.markdown('<div class="section-card">', unsafe_allow_html=True)
                st.markdown(f"**Best Performer:** {best_row['ticker']}")
                st.markdown(f"Gain/Loss: {'🟢' if best_row['gain_loss'] >= 0 else '🔴'} {format_currency(best_row['gain_loss'])}")
                st.markdown(f"Return: {format_percentage(best_row['return_pct'])}")
                st.markdown('</div>', unsafe_allow_html=True)
        with d2:
            if worst_row is not None:
                st.markdown('<div class="section-card">', unsafe_allow_html=True)
                st.markdown(f"**Worst Performer:** {worst_row['ticker']}")
                st.markdown(f"Gain/Loss: {'🟢' if worst_row['gain_loss'] >= 0 else '🔴'} {format_currency(worst_row['gain_loss'])}")
                st.markdown(f"Return: {format_percentage(worst_row['return_pct'])}")
                st.markdown('</div>', unsafe_allow_html=True)

        st.write("")
        st.subheader("Current Holdings")
        display_df = analytics_df.copy()
        display_df["quantity"] = display_df["quantity"].map(lambda x: round(x, 4))
        display_df["avg_cost"] = display_df["avg_cost"].map(lambda x: round(x, 2))
        display_df["current_price"] = display_df["current_price"].map(lambda x: round(x, 2) if pd.notnull(x) else None)
        display_df["invested_amount"] = display_df["invested_amount"].map(lambda x: round(x, 2))
        display_df["market_value"] = display_df["market_value"].map(lambda x: round(x, 2) if pd.notnull(x) else None)
        display_df["gain_loss"] = display_df["gain_loss"].map(lambda x: round(x, 2) if pd.notnull(x) else None)
        display_df["return_pct"] = display_df["return_pct"].map(lambda x: round(x, 2) if pd.notnull(x) else None)
        display_df["allocation_pct"] = display_df["allocation_pct"].map(lambda x: round(x, 2) if pd.notnull(x) else None)
        display_df = display_df.rename(
            columns={
                "ticker": "Ticker",
                "quantity": "Quantity",
                "avg_cost": "Avg Cost",
                "current_price": "Current Price",
                "invested_amount": "Invested Amount",
                "market_value": "Market Value",
                "gain_loss": "Gain/Loss",
                "return_pct": "Return %",
                "allocation_pct": "Allocation %",
            }
        )
        st.dataframe(display_df, use_container_width=True, hide_index=True)

        st.write("")
        g1, g2 = st.columns(2)

        with g1:
            st.subheader("Gain / Loss by Stock")
            chart_df = analytics_df.copy().sort_values("gain_loss", ascending=False)
            fig_gl = px.bar(
                chart_df,
                x="ticker",
                y="gain_loss",
                text_auto=".2s",
                labels={"ticker": "Ticker", "gain_loss": "Gain / Loss ($)"},
                title=None,
            )
            fig_gl.update_layout(height=420, margin=dict(l=10, r=10, t=20, b=10))
            st.plotly_chart(fig_gl, use_container_width=True)

        with g2:
            st.subheader("Portfolio Allocation")
            fig_alloc = px.pie(
                analytics_df,
                names="ticker",
                values="market_value",
                hole=0.5,
            )
            fig_alloc.update_layout(height=420, margin=dict(l=10, r=10, t=20, b=10))
            st.plotly_chart(fig_alloc, use_container_width=True)

        st.write("")
        st.subheader("Total Portfolio Gain / Loss")
        total_gl_df = pd.DataFrame(
            {
                "Metric": ["Invested Amount", "Current Value", "Gain / Loss"],
                "Amount": [total_invested, total_value, total_gain_loss],
            }
        )
        fig_total = px.bar(
            total_gl_df,
            x="Metric",
            y="Amount",
            text_auto=".2s",
            labels={"Amount": "Amount ($)"},
            title=None,
        )
        fig_total.update_layout(height=360, margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(fig_total, use_container_width=True)

    st.write("")
    st.subheader("Add Transaction")
    with st.form("add_transaction_form", clear_on_submit=True):
        f1, f2, f3, f4, f5 = st.columns(5)
        with f1:
            trade_date = st.date_input("Date", value=datetime.today())
        with f2:
            ticker = st.text_input("Ticker", placeholder="e.g. NVDA").upper().strip()
        with f3:
            transaction_type = st.selectbox("Type", TRANSACTION_TYPES)
        with f4:
            quantity = st.number_input("Quantity", min_value=0.0001, value=1.0, step=1.0, format="%.4f")
        with f5:
            price = st.number_input("Price", min_value=0.0001, value=1.0, step=0.01, format="%.4f")

        submitted = st.form_submit_button("Save Transaction", use_container_width=True)
        if submitted:
            if not ticker:
                st.error("Ticker is required.")
            else:
                insert_transaction(
                    trade_date=str(trade_date),
                    ticker=ticker,
                    transaction_type=transaction_type,
                    quantity=float(quantity),
                    price=float(price),
                )
                st.success(f"{transaction_type} transaction saved for {ticker}.")
                st.rerun()

    st.write("")
    st.subheader("Transaction History")
    if transactions_df.empty:
        st.info("No transactions saved yet.")
    else:
        history_df = transactions_df.copy()
        history_df = history_df.rename(
            columns={
                "id": "ID",
                "trade_date": "Date",
                "ticker": "Ticker",
                "transaction_type": "Type",
                "quantity": "Quantity",
                "price": "Price",
                "created_at": "Saved At",
            }
        )
        st.dataframe(history_df, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
