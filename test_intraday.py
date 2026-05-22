import os
for k in ['HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy','ALL_PROXY','all_proxy']:
    os.environ.pop(k, None)
import akshare as ak

# Test with 688518 (a stock we know exists)
try:
    df = ak.stock_intraday_em(symbol='688518')
    print("stock_intraday_em columns:", df.columns.tolist())
    print("Shape:", df.shape)
    print("First 5 rows:")
    print(df.head())
    print("\nLast 3 rows:")
    print(df.tail())
    print("\nDtypes:")
    print(df.dtypes)
except Exception as e:
    print(f"Error: {e}")

# Also test 000001
try:
    df2 = ak.stock_intraday_em(symbol='000001')
    print("\n\n000001 intraday data:")
    print("Shape:", df2.shape)
    print(df2.head(3))
    print("...")
    print(df2.tail(3))
except Exception as e2:
    print(f"000001 Error: {e2}")