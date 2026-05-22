
import streamlit as st

st.title("测试页面")

tab1, tab2, tab3 = st.tabs(["测试1", "测试2", "测试3"])

with tab1:
    st.write("这是Tab1")

with tab2:
    st.write("这是Tab2 - 策略回测测试")

with tab3:
    st.write("这是Tab3 - MACD测试")

if __name__ == "__main__":
    st.write("完成")
