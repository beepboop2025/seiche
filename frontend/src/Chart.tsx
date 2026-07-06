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
        ctx.font = "10px SF Mono, monospace";
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
          stroke: "#6b7686",
          grid: { stroke: "rgba(28,36,48,0.6)" },
          ticks: { stroke: "rgba(28,36,48,0.6)" },
          font: "10px SF Mono, monospace",
        },
        {
          stroke: "#6b7686",
          grid: { stroke: "rgba(28,36,48,0.6)" },
          ticks: { stroke: "rgba(28,36,48,0.6)" },
          font: "10px SF Mono, monospace",
          label: yLabel,
          labelFont: "10px SF Mono, monospace",
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

  return <div className="uplot-wrap" ref={ref} />;
}
