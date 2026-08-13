# -*- coding: utf-8 -*-
"""ETF 池刷新测试：Tushare etf_basic -> etf_pool 行映射（纯函数，无需数据库/网络）。"""
import unittest

import pandas as pd

from app.services.etf_pool_sync import _ts_code_to_symbol, etf_rows_from_df


class TestTsCodeToSymbol(unittest.TestCase):
    def test_sh_sz_conversion(self):
        self.assertEqual(_ts_code_to_symbol("510300.SH"), "SH510300")
        self.assertEqual(_ts_code_to_symbol("159001.SZ"), "SZ159001")


class TestEtfRowMapping(unittest.TestCase):
    def test_filters_to_listed_sh_sz_only(self):
        df = pd.DataFrame(
            [
                {"ts_code": "510300.SH", "exchange": "SH", "list_status": "L", "csname": "华泰柏瑞沪深300ETF"},
                {"ts_code": "159001.SZ", "exchange": "SZ", "list_status": "L", "csname": "易方达货币ETF-A"},
                {"ts_code": "513100.SH", "exchange": "SH", "list_status": "D", "csname": "已退市ETF"},
                {"ts_code": "512480.SH", "exchange": "SH", "list_status": "L", "csname": ""},
                {"ts_code": "588000.SH", "exchange": "SH", "list_status": "L", "csname": "华夏科创50ETF", "extname": "华夏上证科创板50成份ETF"},
                {"ts_code": "515030.SH", "exchange": "BJ", "list_status": "L", "csname": "北交所ETF"},
            ]
        )
        rows = etf_rows_from_df(df)
        symbols = [r["symbol"] for r in rows]
        self.assertEqual(symbols, ["SH510300", "SZ159001", "SH588000"])
        self.assertEqual(rows[0]["name"], "华泰柏瑞沪深300ETF")
        # 退市 / 空名称 / 非沪深交易所 全部跳过
        self.assertNotIn("SH513100", symbols)
        self.assertNotIn("SH512480", symbols)
        self.assertNotIn("SH515030", symbols)

    def test_csname_falls_back_to_extname(self):
        df = pd.DataFrame(
            [{"ts_code": "588000.SH", "exchange": "SH", "list_status": "L", "csname": None, "extname": "华夏上证科创板50成份ETF"}]
        )
        rows = etf_rows_from_df(df)
        self.assertEqual(rows[0]["name"], "华夏上证科创板50成份ETF")

    def test_nan_meta_cleaned_to_none(self):
        df = pd.DataFrame(
            [{"ts_code": "510300.SH", "exchange": "SH", "list_status": "L", "csname": "沪深300ETF",
              "index_code": float("nan"), "index_name": "沪深300", "list_date": 20120528,
              "mgt_fee": 0.5, "etf_type": "纯境内"}]
        )
        rows = etf_rows_from_df(df)
        data = rows[0]["data"]
        self.assertIsNone(data["index_code"])
        self.assertEqual(data["index_name"], "沪深300")
        self.assertEqual(data["list_date"], 20120528)
        self.assertEqual(data["mgt_fee"], 0.5)
        self.assertEqual(data["etf_type"], "纯境内")
        self.assertEqual(data["ts_code"], "510300.SH")


if __name__ == "__main__":
    unittest.main()
