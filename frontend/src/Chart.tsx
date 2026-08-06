import { P } from "./palette";
import { useEffect, useRef, useState } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import ShareBar from "./ShareBar";
import { CHART_EXPORT_W, cardTitle, composeChartCard } from "./share";
import { CopyCSV, fmt } from "./lib";
import "./styles-editorial.css";

export interface ChartSeries {
  label: string;
  color: string;
  dash?: number[];
  fill?: string;
  /** render as unconnected points (scatter) */
  pointsOnly?: boolean;
}

interface Props {
  /** rows of [isoDate, v1, v2, ...] — nulls allowed */
  rows: (string | number | null)[][];
  series: ChartSeries[];
  height?: number;
  yLabel?: string;
  /** horizontal reference line value (e.g. the kink) */
  refLine?: { value: number; color: string; label: string } | null;
  /** vertical event markers (e.g. episode dates) */
  vlines?: { dates: string[]; color: string } | null;
  /** exact source line carried into the visible chart evidence bar */
  source?: string;
  /** publication date/time for the plotted series */
  asOf?: string | null;
  /** precision/caveat note specific to this chart */
  note?: string;
}

type RangeKey = "1Y" | "3Y" | "ALL";

/**
 * Gesture layer: ctrl/⌘+scroll zooms the time axis around the cursor (browsers
 * report trackpad pinch as ctrl+wheel, so pinch works for free), two-finger
 * touch pinch zooms on phones, drag-select zoom and double-click reset are
 * uPlot built-ins. Plain scroll is left alone so the page still scrolls.
 * Listeners live on u.over and die with the plot — no explicit teardown.
 */
function gesturePlugin(): uPlot.Plugin {
  return {
    hooks: {
      ready(u: uPlot) {
        const xs = u.data[0];
        if (!xs || xs.length < 2) return;
        const dmin = xs[0] as number, dmax = xs[xs.length - 1] as number;

        const zoomTo = (centerVal: number, centerFrac: number, factor: number) => {
          const min = u.scales.x.min ?? dmin, max = u.scales.x.max ?? dmax;
          let nr = (max - min) * factor;
          nr = Math.min(nr, dmax - dmin);
          let nmin = centerVal - centerFrac * nr;
          let nmax = nmin + nr;
          if (nmin < dmin) { nmin = dmin; nmax = dmin + nr; }
          if (nmax > dmax) { nmax = dmax; nmin = dmax - nr; }
          u.setScale("x", { min: nmin, max: nmax });
        };

        u.over.addEventListener("wheel", (e: WheelEvent) => {
          if (!e.ctrlKey && !e.metaKey) return;
          e.preventDefault();
          const rect = u.over.getBoundingClientRect();
          const left = e.clientX - rect.left;
          zoomTo(u.posToVal(left, "x"), left / rect.width, e.deltaY < 0 ? 0.85 : 1 / 0.85);
        }, { passive: false });

        // touch pinch — track two pointers, zoom by the change in their gap
        const pts = new Map<number, number>(); // pointerId -> clientX
        let lastGap = 0;
        u.over.addEventListener("pointerdown", (e: PointerEvent) => {
          if (e.pointerType !== "touch") return;
          pts.set(e.pointerId, e.clientX);
          if (pts.size === 2) {
            const [a, b] = [...pts.values()];
            lastGap = Math.abs(a - b);
          }
        });
        u.over.addEventListener("pointermove", (e: PointerEvent) => {
          if (!pts.has(e.pointerId)) return;
          pts.set(e.pointerId, e.clientX);
          if (pts.size !== 2) return;
          const [a, b] = [...pts.values()];
          const gap = Math.abs(a - b);
          if (lastGap > 12 && gap > 12) {
            const rect = u.over.getBoundingClientRect();
            const mid = (a + b) / 2 - rect.left;
            zoomTo(u.posToVal(mid, "x"), mid / rect.width, lastGap / gap);
          }
          lastGap = gap;
        });
        const lift = (e: PointerEvent) => { pts.delete(e.pointerId); lastGap = 0; };
        u.over.addEventListener("pointerup", lift);
        u.over.addEventListener("pointercancel", lift);
      },
    },
  };
}

export default function Chart({ rows, series, height = 170, yLabel, refLine, vlines, source, asOf, note }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  const [range, setRange] = useState<RangeKey>("ALL");

  const buildData = (): uPlot.AlignedData => {
    const xs = rows.map((r) => new Date(r[0] as string).getTime() / 1000);
    return [
      xs,
      ...series.map((_, i) => rows.map((r) => (r[i + 1] == null ? null : Number(r[i + 1])))),
    ] as uPlot.AlignedData;
  };

  const rangeBounds = (key: RangeKey): { min: number; max: number } | null => {
    if (key === "ALL" || rows.length < 2) return null;
    const max = new Date(rows[rows.length - 1][0] as string).getTime() / 1000;
    const years = key === "1Y" ? 1 : 3;
    const min = new Date(new Date(max * 1000).setUTCFullYear(new Date(max * 1000).getUTCFullYear() - years)).getTime() / 1000;
    const floor = new Date(rows[0][0] as string).getTime() / 1000;
    return { min: Math.max(min, floor), max };
  };

  const chooseRange = (key: RangeKey) => {
    setRange(key);
    const plot = plotRef.current;
    if (!plot || rows.length < 2) return;
    const bounds = rangeBounds(key);
    if (bounds) plot.setScale("x", bounds);
    else {
      const xs = plot.data[0];
      plot.setScale("x", { min: xs[0] as number, max: xs[xs.length - 1] as number });
    }
  };

  // One options builder serves the live plot and the export render; the export
  // draws its own legend and cursor-free frame at card width, with fonts sized
  // for a 1200px social card instead of a 500px panel.
  const makeOpts = (width: number, h: number, forExport: boolean): uPlot.Options => {
    const fontPx = forExport ? 12.5 : 10;
    const font = `${fontPx}px Inter, sans-serif`;

    const drawHooks: ((u: uPlot) => void)[] = [];
    if (refLine) {
      drawHooks.push((u) => {
        const y = u.valToPos(refLine.value, "y", true);
        const ctx = u.ctx;
        ctx.save();
        ctx.strokeStyle = refLine.color;
        ctx.setLineDash([5, 5]);
        ctx.beginPath();
        ctx.moveTo(u.bbox.left, y);
        ctx.lineTo(u.bbox.left + u.bbox.width, y);
        ctx.stroke();
        ctx.fillStyle = refLine.color;
        ctx.font = font;
        ctx.fillText(refLine.label, u.bbox.left + 6, y - 5);
        ctx.restore();
      });
    }
    if (vlines && vlines.dates.length) {
      drawHooks.push((u) => {
        const ctx = u.ctx;
        ctx.save();
        ctx.strokeStyle = vlines.color;
        ctx.setLineDash([2, 4]);
        for (const d of vlines.dates) {
          const t = new Date(d).getTime() / 1000;
          if (t < (u.scales.x.min ?? 0) || t > (u.scales.x.max ?? Infinity)) continue;
          const x = u.valToPos(t, "x", true);
          ctx.beginPath();
          ctx.moveTo(x, u.bbox.top);
          ctx.lineTo(x, u.bbox.top + u.bbox.height);
          ctx.stroke();
        }
        ctx.restore();
      });
    }

    return {
      width,
      height: h,
      cursor: forExport ? { show: false } : { points: { size: 5 } },
      legend: { show: !forExport && series.length > 1 },
      axes: [
        {
          stroke: P.faint,
          grid: { stroke: P.grid },
          ticks: { stroke: P.grid },
          font,
        },
        {
          stroke: P.faint,
          grid: { stroke: P.grid },
          ticks: { stroke: P.grid },
          font,
          label: yLabel,
          labelFont: font,
        },
      ],
      series: [
        {},
        ...series.map((s) => ({
          label: s.label,
          stroke: s.pointsOnly ? "transparent" : s.color,
          width: s.pointsOnly ? 0 : forExport ? 2 : 1.4,
          dash: s.dash,
          fill: s.fill,
          paths: s.pointsOnly ? () => null : undefined,
          points: s.pointsOnly
            ? { show: true, size: forExport ? 8 : 6, fill: s.color, stroke: s.color }
            : { show: false },
        })),
      ],
      hooks: drawHooks.length ? { draw: drawHooks } : undefined,
      plugins: forExport ? [] : [gesturePlugin()],
    };
  };

  // The export re-renders the plot at card width offscreen (a scaled-up blit
  // of a 500px panel canvas would ship soft pixels), carries the live zoom
  // window over, and hands the crisp frame to the card composer.
  const composeExport = async (): Promise<HTMLCanvasElement> => {
    const live = plotRef.current;
    if (!live || rows.length === 0) throw new Error("no data yet");
    const cssW = CHART_EXPORT_W;
    const cssH = Math.max(300, Math.round(height * 1.8));
    const host = document.createElement("div");
    host.style.cssText = `position:fixed;left:-99999px;top:0;width:${cssW}px;`;
    document.body.appendChild(host);
    // Declared outside the try so the finally can destroy it: a compose
    // failure must not leak the export instance's window listeners.
    let exp: uPlot | undefined;
    try {
      const plot = new uPlot(makeOpts(cssW, cssH, true), buildData(), host);
      exp = plot;
      const xs = live.data[0];
      const lx = live.scales.x;
      if (xs && xs.length > 1 && lx.min != null && lx.max != null &&
          (lx.min > (xs[0] as number) || lx.max < (xs[xs.length - 1] as number))) {
        plot.setScale("x", { min: lx.min, max: lx.max });
      }
      // uPlot sizes and paints its canvas on a deferred frame, and the delay
      // is not a fixed frame count. Snapshotting early ships a finished card
      // with an empty plot, so wait until the canvas is sized AND carries ink,
      // nudging a redraw if it stalls; past the deadline, fall back to the
      // on-screen canvas, which always has pixels.
      const hasInk = (u: uPlot): boolean => {
        const c = u.ctx.canvas;
        if (!c.width || !c.height) return false;
        // Probe only the plot region (bbox, in device px): axis ink alone
        // must not pass a chart whose data area is still blank.
        const b = u.bbox;
        const usable = b && b.width > 0 && b.height > 0;
        const sx = usable ? b.left : 0;
        const sy = usable ? b.top : 0;
        const sw = usable ? b.width : c.width;
        const sh = usable ? b.height : c.height;
        const probe = document.createElement("canvas");
        probe.width = 48;
        probe.height = 24;
        const pctx = probe.getContext("2d", { willReadFrequently: true })!;
        pctx.drawImage(c, sx, sy, sw, sh, 0, 0, 48, 24);
        const d = pctx.getImageData(0, 0, 48, 24).data;
        for (let j = 3; j < d.length; j += 4) if (d[j] > 0) return true;
        return false;
      };
      // rAF is suspended in hidden tabs, so race each frame wait against a
      // shared 2s timeout; wall-clock time, not frame count, bounds the wait.
      const deadline = Date.now() + 2000;
      let painted = false;
      for (let i = 0; i < 16 && Date.now() < deadline; i++) {
        await new Promise((r) => {
          const t = setTimeout(() => r(null), Math.max(0, deadline - Date.now()));
          requestAnimationFrame(() => { clearTimeout(t); r(null); });
        });
        const c = plot.ctx.canvas;
        if (c.width >= cssW && hasInk(plot)) { painted = true; break; }
        if (i === 7) { try { plot.redraw(false, false); } catch { /* keep waiting */ } }
      }
      const src = painted ? plot.ctx.canvas : live.ctx.canvas;
      const dpr = window.devicePixelRatio || 1;
      const lastX = xs && xs.length ? (xs[xs.length - 1] as number) * 1000 : null;
      const card = composeChartCard(src, {
        title: cardTitle(ref.current, yLabel ?? series[0]?.label ?? "seiche"),
        sub: yLabel,
        series,
        cssW: Math.round(src.width / dpr),
        cssH: Math.round(src.height / dpr),
        dataThrough: lastX,
      });
      return card;
    } finally {
      exp?.destroy();
      host.remove();
    }
  };

  useEffect(() => {
    if (!ref.current || rows.length === 0) return;
    plotRef.current?.destroy();
    plotRef.current = new uPlot(
      makeOpts(ref.current.clientWidth, height, false), buildData(), ref.current,
    );
    const bounds = rangeBounds(range);
    if (bounds) plotRef.current.setScale("x", bounds);

    const onResize = () => {
      if (ref.current && plotRef.current)
        plotRef.current.setSize({ width: ref.current.clientWidth, height });
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      plotRef.current?.destroy();
      plotRef.current = null;
    };
  }, [rows, series, height, yLabel, refLine, vlines, range]);

  // "reveal" wipes the plot in on first paint (a clip-path animation the
  // compositor can run); data refreshes redraw in place without replaying it.
  const firstDate = rows.length ? String(rows[0][0]) : null;
  const lastDate = rows.length ? String(rows[rows.length - 1][0]) : null;
  const spanDays = firstDate && lastDate
    ? (Date.parse(lastDate) - Date.parse(firstDate)) / 86400000
    : 0;
  const latest = rows.length ? rows[rows.length - 1] : null;

  return (
    <figure className="chartbox">
      <div className="chart-evidence">
        <div className="chart-evidence__facts">
          <span>{source ?? "Seiche point-in-time series"}</span>
          <span>{rows.length.toLocaleString()} observations</span>
          <span>through {asOf ?? lastDate ?? "unavailable"}</span>
          {latest && series.slice(0, 3).map((item, index) => (
            <span key={item.label} className="chart-evidence__latest">
              {item.label} {latest[index + 1] == null ? "—" : fmt(Number(latest[index + 1]), 2)}
            </span>
          ))}
        </div>
        <div className="chart-ranges" aria-label="Chart time range">
          {spanDays > 400 && (["1Y", "3Y", "ALL"] as RangeKey[]).map((key) => (
            <button key={key} type="button" className={range === key ? "on" : ""} onClick={() => chooseRange(key)}>
              {key}
            </button>
          ))}
        </div>
      </div>
      <div
        className="uplot-wrap reveal"
        ref={ref}
        role="img"
        aria-label={`${yLabel ?? series.map((item) => item.label).join(", ")} chart, ${rows.length} observations through ${asOf ?? lastDate ?? "an unavailable date"}`}
      />
      {note && <figcaption className="chart-note">{note}</figcaption>}
      <div className="chartfoot">
        <div className="chart-actions">
          <ShareBar
            compose={composeExport}
            title={() => cardTitle(ref.current, yLabel ?? series[0]?.label ?? "seiche")}
          />
          <CopyCSV rows={[["date", ...series.map((item) => item.label)], ...rows]} label="copy data" />
        </div>
        <div className="zoomhint">drag to zoom · ⌘/ctrl+scroll or pinch to zoom · double-click resets</div>
      </div>
    </figure>
  );
}
