import streamlit as st

def main():
    st.title("Tab 结构测试 - 正确版本")
    
    # 创建5个tab
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["Tab 1", "Tab 2", "Tab 3", "Tab 4", "Tab 5"])
    
    with tab1:
        st.write("这是 Tab 1 - 正常显示")
    
    with tab2:
        st.write("这是 Tab 2 - 正常显示")
    
    with tab3:
        st.write("这是 Tab 3 - 正常显示")
    
    with tab4:
        st.write("这是 Tab 4 - 正常显示")
    
    # 关键：tab5 也在 main() 函数内部！
    with tab5:
        st.write("这是 Tab 5 - 现在应该能正常显示了！")

# 调用 main
if __name__ == "__main__":
    main()
