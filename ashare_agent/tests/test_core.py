"""本地单元测试：技术指标与代码规范化（不依赖外网）。"""
from __future__ import annotations

import unittest

from ashare_agent.fetch import normalize_code, market_tag, em_secid, xq_symbol
from ashare_agent.indicators import calc_technicals
from ashare_agent.roles import ROLES, REPORT_SECTIONS, report_outline


class TestNormalize(unittest.TestCase):
    def test_code(self):
        self.assertEqual(normalize_code("002128"), "002128")
        self.assertEqual(normalize_code("002128.SZ"), "002128")
        self.assertEqual(market_tag("600519"), "sh")
        self.assertEqual(market_tag("002128"), "sz")
        self.assertEqual(em_secid("600519"), "1.600519")
        self.assertEqual(xq_symbol("002128"), "SZ002128")


class TestIndicators(unittest.TestCase):
    def test_calc(self):
        daily = []
        price = 100.0
        for i in range(80):
            price = price * (1.01 if i % 3 else 0.99)
            daily.append(
                {
                    "日期": f"2026-01-{(i % 28) + 1:02d}",
                    "开盘": price * 0.99,
                    "收盘": price,
                    "最高": price * 1.02,
                    "最低": price * 0.98,
                    "成交量": 1000 + i,
                }
            )
        tech = calc_technicals(daily)
        self.assertNotIn("error", tech)
        self.assertIn("macd", tech)
        self.assertIn("rsi14", tech)
        self.assertIn("boll", tech)
        self.assertIn("kdj", tech)
        self.assertIsNotNone(tech.get("ma20"))
        self.assertTrue(0 <= (tech.get("rsi14") or 0) <= 100)


class TestRoles(unittest.TestCase):
    def test_roles(self):
        ids = {r["id"] for r in ROLES}
        for need in ("market", "china_market", "bull", "bear", "trader", "judge"):
            self.assertIn(need, ids)
        self.assertGreaterEqual(len(REPORT_SECTIONS), 8)
        text = report_outline("002128", "电投能源")
        self.assertIn("多空辩论", text)
        self.assertIn("免责声明", text)


if __name__ == "__main__":
    unittest.main()
