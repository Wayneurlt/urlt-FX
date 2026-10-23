"""Write a SAMPLE economic-calendar file for testing the news-triggered mode.

These events are NOT real news. They are tagged origin=sample and the UI
labels them "SAMPLE DATA". Use them to exercise the news pipeline in
simulation/paper only.

    python scripts/seed_sample_events.py                 # fixed historical sample set (for backtests)
    python scripts/seed_sample_events.py --relative 30   # also add events starting 30 minutes from now (paper demo)
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "sample_events.json"

HISTORICAL = [
    ("USD", "Non-Farm Payrolls (sample)", "2025-01-10T13:30:00Z"),
    ("USD", "CPI m/m (sample)", "2025-01-15T13:30:00Z"),
    ("GBP", "CPI y/y (sample)", "2025-01-15T07:00:00Z"),
    ("EUR", "ECB Rate Decision (sample)", "2025-01-30T13:15:00Z"),
    ("USD", "FOMC Statement (sample)", "2025-01-29T19:00:00Z"),
    ("JPY", "BoJ Policy Rate (sample)", "2025-01-24T03:00:00Z"),
    ("USD", "Non-Farm Payrolls (sample)", "2025-02-07T13:30:00Z"),
    ("USD", "CPI m/m (sample)", "2025-02-12T13:30:00Z"),
    ("GBP", "BoE Rate Decision (sample)", "2025-02-06T12:00:00Z"),
    ("USD", "Non-Farm Payrolls (sample)", "2025-03-07T13:30:00Z"),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--relative", type=int, default=None, help="add 3 sample events starting N minutes from now"
    )
    args = ap.parse_args()
    events = [
        {"event_id": f"sample-{i}", "currency": c, "name": n, "scheduled_at": t, "impact": "high"}
        for i, (c, n, t) in enumerate(HISTORICAL)
    ]
    if args.relative is not None:
        base = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(minutes=args.relative)
        for k, (c, n) in enumerate(
            [("USD", "Sample USD release"), ("EUR", "Sample EUR release"), ("JPY", "Sample JPY release")]
        ):
            events.append(
                {
                    "event_id": f"sample-rel-{base:%Y%m%d%H%M}-{k}",
                    "currency": c,
                    "name": f"{n} (SAMPLE)",
                    "scheduled_at": (base + timedelta(minutes=15 * k)).isoformat(),
                    "impact": "high",
                }
            )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(events, indent=1), encoding="utf-8")
    print(f"wrote {len(events)} SAMPLE events to {OUT}")


if __name__ == "__main__":
    main()
