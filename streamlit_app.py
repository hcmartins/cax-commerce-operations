import hmac

import pandas as pd
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from commerce_operations.config import Settings
from commerce_operations.dashboard import data
from commerce_operations.persistence.database import create_database_engine, create_session_factory

st.set_page_config(
    page_title="AI-powered commerce operations",
    page_icon=":material/storefront:",
    layout="wide",
)


@st.cache_resource
def resources(database_url: str):
    settings = Settings(database_url=database_url)
    engine = create_database_engine(settings)
    return create_session_factory(engine)


settings = Settings()


def authenticate() -> None:
    configured = settings.dashboard_access_key
    has_key = configured is not None and bool(configured.get_secret_value())
    if not has_key and not settings.is_production:
        return
    if not has_key:
        st.error("Dashboard access is not configured. Set COMMERCE_DASHBOARD_ACCESS_KEY.")
        st.stop()
    with st.sidebar:
        supplied = st.text_input("Dashboard access key", type="password")
    if not supplied or not hmac.compare_digest(supplied, configured.get_secret_value()):
        st.info("Enter the dashboard access key to continue.")
        st.stop()


authenticate()
factory = resources(settings.database_url)


def query(loader):
    try:
        with factory() as session:
            return loader(session)
    except SQLAlchemyError:
        st.error(
            "The operations database is unavailable. Check the dashboard database configuration."
        )
        st.stop()


def show_table(loader, *, empty="No records found", key=None):
    records = query(loader)
    if not records:
        st.info(empty)
        return
    frame = pd.DataFrame(records)
    search = st.text_input("Filter visible records", key=key, placeholder="Type to filter…")
    if search:
        mask = (
            frame.astype(str)
            .apply(lambda column: column.str.contains(search, case=False, na=False))
            .any(axis=1)
        )
        frame = frame[mask]
    st.dataframe(frame, hide_index=True, width="stretch")


def overview_page():
    currency = settings.dashboard_currency.upper()
    symbol = {"GBP": "£", "USD": "$", "EUR": "€"}.get(currency, f"{currency} ")
    kpis = query(lambda session: data.overview(session, currency))
    with st.container(horizontal=True):
        st.metric("Stock value", f"{symbol}{kpis['stock_value']:,.2f}", border=True)
        st.metric("Pending procurements", kpis["pending_procurements"], border=True)
        st.metric("Pending approvals", kpis["pending_approvals"], border=True)
        st.metric("Awaiting publication", kpis["listings_awaiting_publication"], border=True)
        st.metric("Orders", kpis["orders"], border=True)
    with st.container(horizontal=True):
        st.metric("Revenue", f"{symbol}{kpis['revenue']:,.2f}", border=True)
        st.metric("Estimated profit", f"{symbol}{kpis['estimated_profit']:,.2f}", border=True)
        st.metric("Realised profit", f"{symbol}{kpis['realised_profit']:,.2f}", border=True)
        st.metric("Low-stock products", kpis["low_stock_products"], border=True)
        st.metric("Failed workflows", kpis["failed_workflows"], border=True)
    st.caption(
        f"Financial KPIs include {currency} records only. Estimated profit uses the latest "
        "pricing contribution per available unit; realised profit is revenue less cost basis "
        "and approved, processing, or completed refunds."
    )

    attention = query(data.action_required)
    st.subheader(f":material/priority_high: Action required — {len(attention)}")
    if attention:
        st.caption(
            "AI handles routine operations; people retain control of important decisions "
            "and exceptions."
        )
        st.dataframe(
            pd.DataFrame(attention),
            hide_index=True,
            column_order=("priority", "action", "type", "destination", "created"),
            column_config={
                "priority": st.column_config.TextColumn("Status"),
                "created": st.column_config.DatetimeColumn("Raised", format="DD MMM, HH:mm"),
            },
        )
    else:
        st.success(
            "No approvals or exceptions currently require attention.",
            icon=":material/check_circle:",
        )

    st.subheader(":material/automation: AI automation activity")
    activity = query(data.automation_activity)
    if activity:
        st.dataframe(
            pd.DataFrame(activity),
            hide_index=True,
            column_order=("time", "status", "action", "product", "agent"),
            column_config={
                "time": st.column_config.DatetimeColumn("Time", format="DD MMM, HH:mm"),
                "status": st.column_config.TextColumn("Status"),
                "agent": st.column_config.TextColumn("Agent / service"),
            },
        )
    else:
        st.info("Automation activity will appear as workflows, events, and specialist agents run.")

    trends = query(lambda session: data.commercial_trends(session, currency))
    chart_left, chart_right = st.columns(2)
    with chart_left.container(border=True):
        st.subheader("Revenue and orders")
        daily = pd.DataFrame(trends["daily"])
        if daily.empty:
            st.caption("Revenue trends will appear after marketplace orders are received.")
        else:
            st.line_chart(
                daily,
                x="date",
                y="revenue",
                x_label="Date",
                y_label=f"Revenue ({currency})",
            )
            st.bar_chart(daily, x="date", y="orders", x_label="Date", y_label="Orders")
    with chart_right.container(border=True):
        st.subheader("Inventory availability")
        inventory_chart = pd.DataFrame(trends["inventory"])
        if inventory_chart.empty:
            st.caption("Stock visibility will appear after goods are received.")
        else:
            st.bar_chart(inventory_chart, x="product", y=["available", "threshold"], stack=False)

    st.subheader("Product history")
    options = query(data.product_options)
    if not options:
        st.info("Product history will appear after the first approved product is ingested.")
        return
    labels = {label: product_id for label, product_id in options}
    selected = st.selectbox("Product", labels)
    timeline = query(lambda session: data.product_history(session, labels[selected]))
    performance = query(lambda session: data.product_performance(session, labels[selected]))
    if performance:
        with st.container(border=True):
            st.subheader("AI prediction vs actual performance")
            with st.container(horizontal=True):
                st.metric("Predicted ROI", f"{performance['predicted_roi']:.1f}%")
                st.metric("Realised ROI", f"{performance['realised_roi']:.1f}%")
                st.metric("Predicted margin", f"{performance['predicted_margin']:.1f}%")
                st.metric("Realised margin", f"{performance['realised_margin']:.1f}%")
            with st.container(horizontal=True):
                st.metric("Expected price", f"{symbol}{performance['expected_price']:,.2f}")
                st.metric("Average selling price", f"{symbol}{performance['average_price']:,.2f}")
                st.metric("Units sold", performance["units_sold"])
                st.metric("Realised profit", f"{symbol}{performance['realised_profit']:,.2f}")
            st.caption(
                f"Supplier: {performance['supplier']} · Ordered: {performance['quantity']} units · "
                f"Estimated landed cost: {symbol}{performance['landed_cost']:,.2f}"
            )
    st.dataframe(
        pd.DataFrame(timeline),
        hide_index=True,
        width="stretch",
        column_config={
            "time": st.column_config.DatetimeColumn("Time", format="DD MMM YYYY, HH:mm")
        },
    )


def procurement_page():
    show_table(
        data.procurements,
        empty=(
            "No procurement requests yet. Approved opportunities automatically enter "
            "procurement review."
        ),
        key="procurement_filter",
    )


def approvals_page():
    show_table(data.approvals, empty="No decisions need approval right now.", key="approval_filter")


def inventory_page():
    show_table(
        data.inventory,
        empty=("No stock received yet. Goods receipts create inventory and movement history."),
        key="inventory_filter",
    )


def drafts_page():
    show_table(
        data.listing_drafts,
        empty=(
            "No listing drafts yet. When stock is received, the AI Listing Agent can prepare "
            "marketplace-ready content for approval."
        ),
        key="draft_filter",
    )


def listings_page():
    show_table(data.published_listings, empty="No marketplace listings", key="listing_filter")


def orders_page():
    show_table(data.orders, empty="No marketplace orders", key="order_filter")


def messages_page():
    st.caption("Customer content is visible only to authorised operations users.")
    show_table(data.customer_messages, empty="No customer messages", key="message_filter")


def workflows_page():
    show_table(data.workflow_runs, empty="No workflow runs", key="workflow_filter")


def failures_page():
    show_table(data.failures, empty="No workflow or event failures", key="failure_filter")


counts = query(data.navigation_counts)


def counted(label: str, count: int) -> str:
    return f"{label} ({count})" if count else label


pages = {
    "Operations": [
        st.Page(overview_page, title="Overview", icon=":material/dashboard:"),
        st.Page(procurement_page, title="Procurement", icon=":material/local_shipping:"),
        st.Page(
            approvals_page,
            title=counted("Pending approvals", counts["pending_approvals"]),
            icon=":material/approval:",
        ),
        st.Page(inventory_page, title="Inventory", icon=":material/inventory_2:"),
    ],
    "Commerce": [
        st.Page(
            drafts_page,
            title=counted("Listing drafts", counts["listing_drafts"]),
            icon=":material/edit_note:",
        ),
        st.Page(listings_page, title="Published listings", icon=":material/store:"),
        st.Page(orders_page, title="Orders", icon=":material/receipt_long:"),
        st.Page(messages_page, title="Customer messages", icon=":material/forum:"),
    ],
    "Platform": [
        st.Page(workflows_page, title="Workflow runs", icon=":material/account_tree:"),
        st.Page(
            failures_page,
            title=counted("Failures / exceptions", counts["failures"]),
            icon=":material/error:",
        ),
    ],
}

page = st.navigation(pages, position="sidebar")
with st.container(horizontal=True, vertical_alignment="center"):
    st.title(f"{page.icon} {page.title}")
    if settings.demo_mode:
        st.badge("DEMO · SANDBOX", icon=":material/science:", color="blue")
st.markdown("**AI-powered commerce operations**")
st.caption(
    "From procurement and inventory to marketplace listings, orders and customer operations — "
    "managed from one intelligent platform."
)
if settings.demo_mode:
    st.caption(
        "All records are synthetic. External supplier, marketplace, payment, and customer "
        "actions are disabled."
    )
page.run()
