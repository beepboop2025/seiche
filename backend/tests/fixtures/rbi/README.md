# RBI press-release excerpts

Each file is one contiguous, byte-for-byte excerpt of an RBI press-release page
retrieved on 2026-11-02 from
`https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=<id>`. The
excerpt runs from the release's `Date :` row through the end of its data table.
The site template, scripts, navigation and PDF links are omitted; no byte inside
the excerpt was changed. Publisher: Reserve Bank of India. Terms:
https://www.rbi.org.in/Scripts/Disclaimer.aspx. The excerpts keep only the
release date, title and statistical table that the parsers read.

| File | prid | Release | Full page SHA-256 | Excerpt bytes | Excerpt SHA-256 |
| --- | --- | --- | --- | --- | --- |
| `money-market-operations-2026-10-29.html` | 63885 | Money Market Operations as on October 29, 2026 (dated Oct 30) | `2c36e990417d9ea05aae822deb90767f1c7632da9b25530d23aaa074dcf79701` | 34286 to 63006 | `f3e13e5f34a6b766b7b1b7d992f1045315f38b9a9dfcf4ecf721d29842367e94` |

The 29 October money market
report shows blank variable-rate repo and reverse repo rows on a day
without those operations, and a net durable liquidity figure dated
30 September 2026. These are test inputs, not a current production dataset.
