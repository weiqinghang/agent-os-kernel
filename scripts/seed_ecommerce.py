#!/usr/bin/env python3
"""Seed ~10_000 ecommerce sales rows into SQLite for fake-das."""
from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "services" / "fake_das" / "data" / "sales.db"

CATEGORIES = [
    ("电子产品", ["手机", "耳机", "平板", "充电宝", "智能手表"]),
    ("服饰鞋包", ["运动鞋", "T恤", "连衣裙", "双肩包", "夹克"]),
    ("家居生活", ["床品四件套", "收纳箱", "台灯", "保温杯", "香薰"]),
    ("美妆个护", ["面霜", "口红", "洗发水", "面膜", "防晒霜"]),
    ("食品饮料", ["坚果礼盒", "咖啡豆", "茶叶", "饼干", "果汁"]),
]
CHANNELS = ["天猫", "京东", "抖音电商", "拼多多", "自营商城"]
PROVINCES = [
    "广东", "浙江", "江苏", "上海", "北京", "四川", "湖北", "山东",
    "福建", "河南", "湖南", "安徽", "重庆", "陕西", "辽宁",
]


def seed(db_path: Path, n: int = 10_000, seed_val: int = 42) -> int:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    rng = random.Random(seed_val)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE sales (
            id INTEGER PRIMARY KEY,
            order_id TEXT NOT NULL,
            sku TEXT NOT NULL,
            category TEXT NOT NULL,
            channel TEXT NOT NULL,
            province TEXT NOT NULL,
            order_date TEXT NOT NULL,
            qty INTEGER NOT NULL,
            gmv REAL NOT NULL,
            returned INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    cur.execute("CREATE INDEX idx_sales_cat ON sales(category)")
    cur.execute("CREATE INDEX idx_sales_ch ON sales(channel)")
    cur.execute("CREATE INDEX idx_sales_prov ON sales(province)")
    cur.execute("CREATE INDEX idx_sales_date ON sales(order_date)")

    start = date(2025, 7, 25)  # ~90 days before 2026-10-22 demo
    rows = []
    for i in range(1, n + 1):
        cat, skus = rng.choice(CATEGORIES)
        sku = rng.choice(skus)
        channel = rng.choice(CHANNELS)
        province = rng.choice(PROVINCES)
        # Bias GMV toward 广东/浙江 and 抖音/天猫 for interesting insights
        day = start + timedelta(days=rng.randint(0, 89))
        qty = rng.randint(1, 5)
        base = {
            "电子产品": 890,
            "服饰鞋包": 220,
            "家居生活": 160,
            "美妆个护": 180,
            "食品饮料": 95,
        }[cat]
        mult = 1.0
        if province in ("广东", "浙江", "江苏", "上海"):
            mult *= 1.35
        if channel in ("抖音电商", "天猫"):
            mult *= 1.25
        unit = base * mult * rng.uniform(0.75, 1.35)
        gmv = round(unit * qty, 2)
        returned = 1 if rng.random() < (0.12 if channel == "拼多多" else 0.06) else 0
        rows.append(
            (
                i,
                f"ORD{100000 + i}",
                sku,
                cat,
                channel,
                province,
                day.isoformat(),
                qty,
                gmv,
                returned,
            )
        )

    cur.executemany(
        "INSERT INTO sales VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    cur.execute(
        """
        CREATE TABLE datasets_meta (
            name TEXT PRIMARY KEY,
            description TEXT,
            row_count INTEGER,
            columns TEXT
        )
        """
    )
    cols = "id,order_id,sku,category,channel,province,order_date,qty,gmv,returned"
    cur.execute(
        "INSERT INTO datasets_meta VALUES (?,?,?,?)",
        (
            "ecommerce_sales",
            "近90天电商订单行（POC 自造）",
            n,
            cols,
        ),
    )
    conn.commit()
    count = cur.execute("SELECT COUNT(*) FROM sales").fetchone()[0]
    conn.close()
    return count


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--n", type=int, default=10_000)
    args = p.parse_args()
    count = seed(args.db, args.n)
    print(f"seeded {count} rows -> {args.db}")


if __name__ == "__main__":
    main()
