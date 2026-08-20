from collections import OrderedDict
from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd

CSV_PATH = "simulation/simulation_data/transactions.csv"


def spread_timestamps(
    day_date: date, count: int, open_hour: float = 10.0, close_hour: float = 18.0
) -> list[datetime]:
    peak_hour = (open_hour + close_hour) / 2
    spread = 2.0

    decimal_hours = np.random.normal(peak_hour, spread, count)
    decimal_hours = np.clip(decimal_hours, open_hour, close_hour - 0.05)
    decimal_hours = np.sort(decimal_hours)

    timestamps = []
    for h in decimal_hours:
        hour = int(h)
        minute = int((h - hour) * 60)
        second = int(((h - hour) * 60 - minute) * 60)
        timestamps.append(
            datetime(day_date.year, day_date.month, day_date.day, hour, minute, second)  # noqa: DTZ001
        )

    return timestamps


def load_transactions(csv_path: str) -> tuple[OrderedDict, dict]:
    df = pd.read_csv(csv_path)
    df["sales_date_time"] = pd.to_datetime(df["sales_date_time"])
    df["date"] = df["sales_date_time"].dt.date

    grouped = OrderedDict()
    for sale_date, day_df in df.groupby("date", sort=True):
        grouped[sale_date] = day_df

    for sale_date, df in grouped.items():
        timestamps = spread_timestamps(sale_date, len(df))
        df["sales_date_time"] = timestamps

    products_df = pd.read_csv(csv_path.replace("transactions.csv", "products.csv"))
    products = {}
    for _, row in products_df.iterrows():
        products[str(row["gtin"]).strip()] = {
            "name": row["product_name"].strip(),
            "price": Decimal(str(row["price"])),
        }

    return grouped, products


if __name__ == "__main__":
    grouped, products = load_transactions(CSV_PATH)
