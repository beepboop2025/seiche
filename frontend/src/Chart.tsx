import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

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
}

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

export default function Chart({ rows, series, height = 170, yLabel, refLine, vlines }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);

  useEffect(() => {
    if (!ref.current || rows.length === 0) return;
    const xs = rows.map((r) => new Date(r[0] as string).getTime() / 1000);
    const data: uPlot.AlignedData = [
      xs,
      ...series.map((_, i) => rows.map((r) => (r[i + 1] == null ? null : Number(r[i + 1])))),
    ] as uPlot.AlignedData;

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
        ctx.font = "10px Inter, sans-serif";
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

    const opts: uPlot.Options = {
      width: ref.current.clientWidth,
      height,
      cursor: { points: { size: 5 } },
      legend: { show: series.length > 1 },
      axes: [
        {
          stroke: "#75798c",
          grid: { stroke: "rgba(233,233,237,0.07)" },
          ticks: { stroke: "rgba(233,233,237,0.07)" },
          font: "10px Inter, sans-serif",
        },
        {
          stroke: "#75798c",
          grid: { stroke: "rgba(233,233,237,0.07)" },
          ticks: { stroke: "rgba(233,233,237,0.07)" },
          font: "10px Inter, sans-serif",
          label: yLabel,
          labelFont: "10px Inter, sans-serif",
        },
      ],
      series: [
        {},
        ...series.map((s) => ({
          label: s.label,
          stroke: s.pointsOnly ? "transparent" : s.color,
          width: s.pointsOnly ? 0 : 1.4,
          dash: s.dash,
          fill: s.fill,
          paths: s.pointsOnly ? () => null : undefined,
          points: s.pointsOnly
            ? { show: true, size: 6, fill: s.color, stroke: s.color }
            : { show: false },
        })),
      ],
      hooks: drawHooks.length ? { draw: drawHooks } : undefined,
      plugins: [gesturePlugin()],
    };

    plotRef.current?.destroy();
    plotRef.current = new uPlot(opts, data, ref.current);

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
  }, [rows, series, height, yLabel, refLine, vlines]);

  // "reveal" wipes the plot in on first paint (a clip-path animation the
  // compositor can run); data refreshes redraw in place without replaying it.
  return (
    <div className="chartbox">
      <div className="uplot-wrap reveal" ref={ref} />
      <div className="zoomhint">drag to zoom · ⌘/ctrl+scroll or pinch to zoom · double-click resets</div>
    </div>
  );
}
