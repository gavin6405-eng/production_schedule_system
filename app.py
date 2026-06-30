
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import pandas as pd
import streamlit as st
import altair as alt

from scheduler import parse_holidays, build_schedule, build_daily_loading

st.set_page_config(page_title="生產排程反推系統", page_icon="📅", layout="wide")

st.title("📅 生產排程反推系統")
st.caption("依客戶入庫日、標準工時、週休與假日，自動反推各製令的發料、開工及工序時間。")

with st.sidebar:
    st.header("排程參數")
    workday_start = st.time_input("上班時間", value=pd.Timestamp("08:00").time())
    workday_end = st.time_input("下班時間", value=pd.Timestamp("17:00").time())
    lunch_start = st.time_input("午休開始", value=pd.Timestamp("12:00").time())
    lunch_end = st.time_input("午休結束", value=pd.Timestamp("13:00").time())
    buffer_days = st.number_input("客戶入庫前預留工作天", min_value=0.0, value=0.0, step=0.5)
    st.markdown("---")
    st.info("請先下載範例檔，填入資料後上傳。")

template_path = Path(__file__).parent / "templates" / "生產排程匯入範例.xlsx"
if template_path.exists():
    st.download_button(
        "⬇️ 下載匯入範例 Excel",
        data=template_path.read_bytes(),
        file_name="生產排程匯入範例.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

uploaded = st.file_uploader("上傳生產排程 Excel（需含：訂單資料、工時設定、假日設定）", type=["xlsx"])

if uploaded is None:
    st.markdown("""
### 使用方式
1. 下載範例 Excel。
2. 在「訂單資料」填入製令、客戶入庫日等資料。
3. 在「工時設定」依實際流程填入各工序標準工時。
4. 在「假日設定」填入國定假日、公司休假日。
5. 上傳後按下「執行反推排程」。
""")
    st.stop()

try:
    xls = pd.ExcelFile(uploaded)
    required_sheets = {"訂單資料", "工時設定", "假日設定"}
    missing_sheets = required_sheets - set(xls.sheet_names)
    if missing_sheets:
        st.error(f"缺少工作表：{', '.join(sorted(missing_sheets))}")
        st.stop()

    orders = pd.read_excel(xls, sheet_name="訂單資料")
    process_cfg = pd.read_excel(xls, sheet_name="工時設定")
    holiday_df = pd.read_excel(xls, sheet_name="假日設定")
except Exception as exc:
    st.error(f"讀取 Excel 失敗：{exc}")
    st.stop()

tabs = st.tabs(["訂單資料預覽", "工時設定", "假日設定"])
with tabs[0]:
    st.dataframe(orders, use_container_width=True, hide_index=True)
with tabs[1]:
    st.dataframe(process_cfg, use_container_width=True, hide_index=True)
with tabs[2]:
    st.dataframe(holiday_df, use_container_width=True, hide_index=True)

if st.button("▶️ 執行反推排程", type="primary", use_container_width=True):
    try:
        holidays = parse_holidays(holiday_df)
        summary, details = build_schedule(
            orders=orders,
            process_cfg=process_cfg,
            holidays=holidays,
            workday_start=workday_start.strftime("%H:%M"),
            workday_end=workday_end.strftime("%H:%M"),
            lunch_start=lunch_start.strftime("%H:%M"),
            lunch_end=lunch_end.strftime("%H:%M"),
            buffer_days=buffer_days,
        )
        loading = build_daily_loading(details, holidays)

        st.success(f"排程完成：{len(summary)} 筆製令、{len(details)} 筆工序明細。")

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("製令數", f"{summary['製令'].nunique():,}" if "製令" in summary else len(summary))
        c2.metric("總工時", f"{summary['總標準工時(小時)'].sum():,.1f}")
        c3.metric("最早發料日", str(summary["預計發料日"].min()))
        c4.metric("最晚客戶入庫日", str(pd.to_datetime(summary["客戶入庫日"], errors="coerce").max().date()))

        st.subheader("反推排程總表")
        st.dataframe(summary, use_container_width=True, hide_index=True)

        st.subheader("各工序明細")
        st.dataframe(details, use_container_width=True, hide_index=True)

        if not loading.empty:
            st.subheader("每日工時負荷")
            chart = (
                alt.Chart(loading)
                .mark_bar()
                .encode(
                    x=alt.X("日期:T", title="日期"),
                    y=alt.Y("工時負荷(小時):Q", title="工時負荷"),
                    tooltip=["日期:T", "工時負荷(小時):Q", "製令數:Q"],
                )
            )
            st.altair_chart(chart, use_container_width=True)
            st.dataframe(loading, use_container_width=True, hide_index=True)

        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            summary.to_excel(writer, sheet_name="排程總表", index=False)
            details.to_excel(writer, sheet_name="工序明細", index=False)
            loading.to_excel(writer, sheet_name="每日負荷", index=False)
            process_cfg.to_excel(writer, sheet_name="工時設定", index=False)
            holiday_df.to_excel(writer, sheet_name="假日設定", index=False)

            for ws in writer.book.worksheets:
                ws.freeze_panes = "A2"
                ws.auto_filter.ref = ws.dimensions
                for col_cells in ws.columns:
                    length = max(len(str(c.value)) if c.value is not None else 0 for c in col_cells)
                    ws.column_dimensions[col_cells[0].column_letter].width = min(max(length + 2, 10), 32)

        st.download_button(
            "⬇️ 下載排程結果 Excel",
            data=output.getvalue(),
            file_name="生產排程反推結果.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    except Exception as exc:
        st.exception(exc)
