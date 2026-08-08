"""EIA legacy-table parsing stays structural and dependency-free."""

from __future__ import annotations

from seiche.sources.eia_petroleum import parse_history


def test_parse_history_reads_weekly_pairs_and_ignores_missing_values() -> None:
    rows = []
    for year in range(2024, 2027):
        for month, name in enumerate(
            (
                "Jan",
                "Feb",
                "Mar",
                "Apr",
                "May",
                "Jun",
                "Jul",
                "Aug",
                "Sep",
                "Oct",
                "Nov",
                "Dec",
            ),
            start=1,
        ):
            cells = [f"<td class='B6'>{year}-{name}</td>"]
            for day, value in ((7, 20_000 + month), (14, 20_100 + month)):
                cells.extend(
                    [
                        f"<td class='B5'>{month:02d}/{day:02d}</td>",
                        f"<td class='B3'>{value:,}</td>",
                    ]
                )
            cells.extend(["<td class='B5'>&nbsp;</td>", "<td class='B3'>NA</td>"])
            rows.append(f"<tr>{''.join(cells)}</tr>")

    series = parse_history(f"<table><tbody>{''.join(rows)}</tbody></table>")
    assert len(series) == 72
    assert series.index[0].date().isoformat() == "2024-01-07"
    assert series.index[-1].date().isoformat() == "2026-12-14"
    assert series.iloc[0] == 20_001.0
    assert series.iloc[-1] == 20_112.0
