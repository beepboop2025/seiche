/**
 * The Odometer — digits that roll instead of swapping.
 *
 * Each digit is a vertical strip of 0–9 translated to the current numeral;
 * on a refresh the strip rolls to the new reading, so the eye sees the
 * direction of the change (rolling up = worse — this is a stress gauge).
 * Non-digit characters (sign, decimal point, comma) render statically.
 * Columns are keyed from the RIGHT so a widening number (9 → 10) prepends a
 * column instead of shuffling every digit.
 *
 * Motion is transform-only and lives in CSS (styles-cinema.css), so DESK
 * mode and reduced motion kill the roll by killing the transition — the
 * digits still land on the right reading.
 */
import { fmt } from "../lib";

const DIGITS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9];

export default function Odo({ v, d = 0 }: { v: number | null | undefined; d?: number }) {
  if (v == null) return <>—</>;
  const chars = fmt(v, d).split("");
  const n = chars.length;
  return (
    <span className="odo" aria-label={fmt(v, d)}>
      {chars.map((ch, i) => {
        const key = n - i; // stable from the right
        if (!/[0-9]/.test(ch)) {
          return (
            <span className="odo-lit" key={key} aria-hidden="true">
              {ch}
            </span>
          );
        }
        const digit = parseInt(ch, 10);
        return (
          <span className="odo-col" key={key} aria-hidden="true">
            <span className="odo-strip" style={{ transform: `translateY(${-digit}em)` }}>
              {DIGITS.map((k) => (
                <span className="odo-digit" key={k}>
                  {k}
                </span>
              ))}
            </span>
          </span>
        );
      })}
    </span>
  );
}
