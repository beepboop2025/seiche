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
| `full-auction-results-2026-10-30.html` | 63889 | Government Stock - Full Auction Results, Oct 30, 2026 | `1a7a6fa83e6edb7416a22e1b76924aa97db7c7aac0bd4fa2c7123fdd4b80a29b` | 34260 to 40334 | `805a02bee0e6363b223191f772d3fd4a67e7d05175595b25398bb5780241779d` |
| `full-auction-results-2026-10-09.html` | 63775 | Government Stock - Full Auction Results, Oct 09, 2026 | `a68e93377ea232b29e77262ca02903f8b4e538e239bebfc4f0fd844acdbc6a1b` | 34260 to 38561 | `f9ab45403c2fa0e50c68f2b0a74daef7bafb8c54c05039410c4e98f200c09948` |
| `money-market-operations-2026-10-29.html` | 63885 | Money Market Operations as on October 29, 2026 (dated Oct 30) | `2c36e990417d9ea05aae822deb90767f1c7632da9b25530d23aaa074dcf79701` | 34286 to 63006 | `f3e13e5f34a6b766b7b1b7d992f1045315f38b9a9dfcf4ecf721d29842367e94` |

The 30 October auction mixed three conventional securities with the
30-year sovereign green bond `7.50% GOI SGrB 2056`; the earlier validator
rejected that identity and failed the whole adapter. The 9 October auction
carried 15Y and 50Y securities that had no declared slot. The 29 October money
market report shows blank variable-rate repo and reverse repo rows on a day
without those operations, and a net durable liquidity figure dated
30 September 2026. These are test inputs, not a current production dataset.
