import { useEffect, useRef } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";

export interface ChartSeries {
  label: string;
  color: string;
  dash?: number[];
  fill?: string;
}

interface Props {
  /** rows of [isoDate, v1, v2, ...] — nulls allowed */
  rows: (string | number | null)[][];
  series: ChartSeries[];
  height?: number;
  yLabel?: string;
  /** horizontal reference line value (e.g. the kink) */
  refLine?: { value: number; color: string; label: string } | null;
}

export default function Chart({ rows, series, height = 170, yLabel, refLine }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const plotRef = useRef<uPlot | null>(null);

  useEffect(() => {
    if (!ref.current || rows.length === 0) return;
    const xs = rows.map((r) => new Date(r[0] as string).getTime() / 1000);
    const data: uPlot.AlignedData = [
      xs,
      ...series.map((_, i) => rows.map((r) => (r[i + 1] == null ? null : Number(r[i + 1])))),
    ] as uPlot.AlignedData;

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
          stroke: s.color,
          width: 1.4,
          dash: s.dash,
          fill: s.fill,
          points: { show: false },
        })),
      ],
      hooks: refLine
        ? {
            draw: [
              (u) => {
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
              },
            ],
          }
        : undefined,
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
  }, [rows, series, height, yLabel, refLine]);

  return <div className="uplot-wrap" ref={ref} />;
}
