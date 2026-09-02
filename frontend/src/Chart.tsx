import { P } from "./palette";
import { useEffect, useRef, useState } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import ShareBar from "./ShareBar";
import { CHART_EXPORT_W, cardTitle, composeChartCard, contextualLink } from "./share";
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
  /** exact publish-time card route, only when this plot has a matching view */
  sharePath?: string;
}

type RangeKey = "1Y" | "3Y" | "ALL";
type CursorReadout = { date: string; values: (number | null)[] };

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

export default function Chart({ rows, series, height = 170, yLabel, refLine, vlines, source, asOf, note, sharePath }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const cursorIndexRef = useRef<number | null>(null);
  const animationRef = useRef<number | null>(null);
  const pointerStartRef = useRef<{ x: number; y: number } | null>(null);
  const viewRef = useRef<{ min: number; max: number } | null>(null);
  const rowsRef = useRef(rows);
  rowsRef.current = rows;
  const [range, setRange] = useState<RangeKey>("ALL");
  const [expanded, setExpanded] = useState(false);
  const [readout, setReadout] = useState<CursorReadout | null>(null);

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

  const dataBounds = (): { min: number; max: number } | null => {
    const activeRows = rowsRef.current;
    if (activeRows.length < 2) return null;
    return {
      min: new Date(activeRows[0][0] as string).getTime() / 1000,
      max: new Date(activeRows[activeRows.length - 1][0] as string).getTime() / 1000,
    };
  };

  const animateScale = (targetMin: number, targetMax: number) => {
    const plot = plotRef.current;
    const bounds = dataBounds();
    if (!plot || !bounds || targetMax <= targetMin) return;

    const width = Math.min(targetMax - targetMin, bounds.max - bounds.min);
    let endMin = Math.max(bounds.min, targetMin);
    let endMax = endMin + width;
    if (endMax > bounds.max) {
      endMax = bounds.max;
      endMin = endMax - width;
    }
    const startMin = plot.scales.x.min ?? bounds.min;
    const startMax = plot.scales.x.max ?? bounds.max;
    if (animationRef.current !== null) cancelAnimationFrame(animationRef.current);

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      plot.setScale("x", { min: endMin, max: endMax });
      return;
    }
    const started = performance.now();
    const duration = 180;
    const frame = (now: number) => {
      const t = Math.min(1, (now - started) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      plot.setScale("x", {
        min: startMin + (endMin - startMin) * eased,
        max: startMax + (endMax - startMax) * eased,
      });
      if (t < 1) animationRef.current = requestAnimationFrame(frame);
      else animationRef.current = null;
    };
    animationRef.current = requestAnimationFrame(frame);
  };

  const chooseRange = (key: RangeKey) => {
    setRange(key);
    const full = dataBounds();
    if (!full) return;
    const selected = rangeBounds(key);
    animateScale(selected?.min ?? full.min, selected?.max ?? full.max);
  };

  const zoomBy = (factor: number) => {
    const plot = plotRef.current;
    const bounds = dataBounds();
    if (!plot || !bounds) return;
    const min = plot.scales.x.min ?? bounds.min;
    const max = plot.scales.x.max ?? bounds.max;
    const center = (min + max) / 2;
    const half = Math.max(
      (max - min) * factor / 2,
      (bounds.max - bounds.min) / Math.max(rowsRef.current.length, 2),
    );
    animateScale(center - half, center + half);
  };

  const panBy = (direction: -1 | 1) => {
    const plot = plotRef.current;
    const bounds = dataBounds();
    if (!plot || !bounds) return;
    const min = plot.scales.x.min ?? bounds.min;
    const max = plot.scales.x.max ?? bounds.max;
    const distance = (max - min) * 0.22 * direction;
    animateScale(min + distance, max + distance);
  };

  const resetZoom = () => {
    const bounds = dataBounds();
    if (!bounds) return;
    setRange("ALL");
    animateScale(bounds.min, bounds.max);
  };

  const openExplorer = () => {
    returnFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : ref.current;
    setExpanded(true);
  };
  const closeExplorer = () => setExpanded(false);

  const handlePlotKey = (event: React.KeyboardEvent) => {
    const key = event.key;
    if ((key === "Enter" || key === " ") && !expanded) {
      event.preventDefault();
      openExplorer();
    } else if (key === "Escape") {
      event.preventDefault();
      expanded ? closeExplorer() : resetZoom();
    } else if (key === "ArrowLeft" || key === "ArrowRight") {
      event.preventDefault();
      panBy(key === "ArrowLeft" ? -1 : 1);
    } else if (key === "+" || key === "=") {
      event.preventDefault();
      zoomBy(0.72);
    } else if (key === "-" || key === "_") {
      event.preventDefault();
      zoomBy(1.38);
    } else if (key === "0") {
      event.preventDefault();
      resetZoom();
    }
  };

  useEffect(() => {
    if (!expanded) return;
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeExplorer();
      } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        panBy(event.key === "ArrowLeft" ? -1 : 1);
      } else if (event.key === "+" || event.key === "=") {
        event.preventDefault();
        zoomBy(0.72);
      } else if (event.key === "-" || event.key === "_") {
        event.preventDefault();
        zoomBy(1.38);
      } else if (event.key === "0") {
        event.preventDefault();
        resetZoom();
      }
    };
    window.addEventListener("keydown", onKey);
    requestAnimationFrame(() => closeRef.current?.focus());
    return () => {
      document.body.style.overflow = oldOverflow;
      window.removeEventListener("keydown", onKey);
      const returnTo = returnFocusRef.current;
      requestAnimationFrame(() => { if (returnTo?.isConnected) returnTo.focus(); });
    };
  }, [expanded]);

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

    const hooks: uPlot.Hooks.Arrays = {};
    if (drawHooks.length) hooks.draw = drawHooks;
    if (!forExport) {
      hooks.setCursor = [(u) => {
        const idx = u.cursor.idx ?? null;
        if (idx === cursorIndexRef.current) return;
        cursorIndexRef.current = idx;
        if (idx == null || !rows[idx]) {
          setReadout(null);
          return;
        }
        setReadout({
          date: String(rows[idx][0]),
          values: series.map((_, seriesIndex) => {
            const value = rows[idx][seriesIndex + 1];
            return value == null || !Number.isFinite(Number(value)) ? null : Number(value);
          }),
        });
      }];
      hooks.setScale = [(u, key) => {
        if (key === "x" && u.scales.x.min != null && u.scales.x.max != null) {
          viewRef.current = { min: u.scales.x.min, max: u.scales.x.max };
        }
      }];
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
      hooks,
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
        link: contextualLink(ref.current),
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
    cursorIndexRef.current = null;
    const plotHeight = () => expanded ? Math.max(260, window.innerHeight - 300) : height;
    plotRef.current = new uPlot(
      makeOpts(ref.current.clientWidth, plotHeight(), false), buildData(), ref.current,
    );
    const full = dataBounds();
    const remembered = viewRef.current;
    if (full && remembered) {
      const width = Math.min(remembered.max - remembered.min, full.max - full.min);
      const min = Math.max(full.min, Math.min(remembered.min, full.max - width));
      plotRef.current.setScale("x", { min, max: min + width });
    } else {
      const bounds = rangeBounds(range);
      if (bounds) plotRef.current.setScale("x", bounds);
    }

    const onResize = () => {
      if (ref.current && plotRef.current)
        plotRef.current.setSize({ width: ref.current.clientWidth, height: plotHeight() });
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      if (animationRef.current !== null) cancelAnimationFrame(animationRef.current);
      plotRef.current?.destroy();
      plotRef.current = null;
    };
  }, [rows, series, height, yLabel, refLine, vlines, expanded]);

  // "reveal" wipes the plot in on first paint (a clip-path animation the
  // compositor can run); data refreshes redraw in place without replaying it.
  const firstDate = rows.length ? String(rows[0][0]) : null;
  const lastDate = rows.length ? String(rows[rows.length - 1][0]) : null;
  const spanDays = firstDate && lastDate
    ? (Date.parse(lastDate) - Date.parse(firstDate)) / 86400000
    : 0;
  const latest = rows.length ? rows[rows.length - 1] : null;

  const chartTitle = yLabel ?? (series.map((item) => item.label).join(", ") || "Seiche chart");

  return (
    <div
      className={`chart-shell${expanded ? " chart-shell--expanded" : ""}`}
      data-share-path={sharePath}
      role={expanded ? "dialog" : undefined}
      aria-modal={expanded ? true : undefined}
      aria-label={expanded ? `${chartTitle} explorer` : undefined}
      onMouseDown={(event) => {
        if (expanded && event.target === event.currentTarget) closeExplorer();
      }}
    >
    <figure className={`chartbox${expanded ? " chartbox--expanded" : ""}`}>
      {expanded && (
        <div className="chart-explorer-head">
          <div><span>INTERACTIVE CHART</span><h2>{chartTitle}</h2></div>
          <button ref={closeRef} type="button" onClick={closeExplorer}>Close <kbd>Esc</kbd></button>
        </div>
      )}
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
        <div className="chart-controls">
          <div className="chart-ranges" aria-label="Chart time range">
            {spanDays > 400 && (["1Y", "3Y", "ALL"] as RangeKey[]).map((key) => (
              <button key={key} type="button" className={range === key ? "on" : ""} onClick={() => chooseRange(key)}>
                {key}
              </button>
            ))}
          </div>
          <div className="chart-nav" aria-label="Chart navigation controls">
            <button type="button" onClick={() => panBy(-1)} aria-label="Pan earlier" title="Pan earlier · Left arrow">←</button>
            <button type="button" onClick={() => zoomBy(1.38)} aria-label="Zoom out" title="Zoom out · minus">−</button>
            <button type="button" onClick={() => zoomBy(0.72)} aria-label="Zoom in" title="Zoom in · plus">+</button>
            <button type="button" onClick={() => panBy(1)} aria-label="Pan later" title="Pan later · Right arrow">→</button>
            <button type="button" onClick={resetZoom} aria-label="Reset chart zoom" title="Reset zoom · 0">RESET</button>
            <button type="button" className="chart-expand" onClick={expanded ? closeExplorer : openExplorer} aria-label={expanded ? "Close chart explorer" : "Open chart explorer"}>
              {expanded ? "CLOSE" : "EXPLORE ↗"}
            </button>
          </div>
        </div>
      </div>
      <div
        className="uplot-wrap reveal"
        ref={ref}
        role="group"
        tabIndex={0}
        aria-label={`${chartTitle}, ${rows.length} observations through ${asOf ?? lastDate ?? "an unavailable date"}. Press Enter to expand; plus and minus zoom; arrow keys pan; zero resets; Escape closes.`}
        onKeyDown={handlePlotKey}
        onPointerDown={(event) => { pointerStartRef.current = { x: event.clientX, y: event.clientY }; }}
        onPointerCancel={() => { pointerStartRef.current = null; }}
        onPointerUp={(event) => {
          const start = pointerStartRef.current;
          pointerStartRef.current = null;
          if (!expanded && start && Math.hypot(event.clientX - start.x, event.clientY - start.y) < 5) {
            openExplorer();
          }
        }}
      />
      <div className="chart-readout" aria-label="Selected chart observation">
        {readout ? (
          <>
            <time>{readout.date}</time>
            {series.map((item, index) => (
              <span key={item.label}><i style={{ background: item.color }} />{item.label} <b>{readout.values[index] == null ? "—" : fmt(readout.values[index], 3)}</b></span>
            ))}
          </>
        ) : (
          <span>Move across the plot for an exact dated reading · click to explore</span>
        )}
      </div>
      {note && <figcaption className="chart-note">{note}</figcaption>}
      <div className="chartfoot">
        <div className="chart-actions">
          <ShareBar
            compose={composeExport}
            title={() => cardTitle(ref.current, yLabel ?? series[0]?.label ?? "seiche")}
            link={() => contextualLink(ref.current)}
          />
          <CopyCSV rows={[["date", ...series.map((item) => item.label)], ...rows]} label="copy data" />
        </div>
        <div className="zoomhint">drag to zoom · scroll/pinch · arrows pan · +/− zoom · 0 reset · Esc closes</div>
      </div>
    </figure>
    </div>
  );
}
