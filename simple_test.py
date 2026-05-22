import streamlit as st

st.title("Test App - 5 Tabs")

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["Tab1", "Tab2", "Tab3", "Tab4", "策略回测", "MACD筛选"])

with tab1:
    st.write("Tab 1 works")

with tab2:
    st.write("Tab 2 works")

with tab3:
    st.write("Tab 3 works")

with tab4:
    st.write("Tab 4 works")

with tab5:
    st.header("策略回测")
    st.write("策略回测内容 - Tab 5")
    st.button("测试按钮")

with tab6:
    st.header("MACD底背离筛选")
    st.write("MACD筛选内容 - Tab 6")
    st.button("扫描按钮")

if __name__ == "__main__":
    st.write("Done")
