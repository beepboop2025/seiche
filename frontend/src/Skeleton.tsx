/** Loading skeletons — the board's shape appears instantly, then fills in.
 *  Shimmer respects prefers-reduced-motion (see styles.css). */

function Bar({ w, h = 12 }: { w: number | string; h?: number }) {
  return <div className="skel" style={{ width: typeof w === "number" ? `${w}px` : w, height: h }} />;
}

function SkelCard({ span = 6 }: { span?: number }) {
  return (
    <div className={`skel-card span${span}`}>
      <Bar w={120} h={11} />
      <div style={{ height: 10 }} />
      <Bar w="70%" h={26} />
      <div style={{ height: 14 }} />
      <Bar w="100%" h={8} />
      <div style={{ height: 6 }} />
      <Bar w="88%" h={8} />
      <div style={{ height: 6 }} />
      <Bar w="94%" h={8} />
    </div>
  );
}

/** Full first-load state: masthead + hero dial + headline strip + a few cards. */
export function AppSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading the board">
      <div className="masthead">
        <Bar w={150} h={24} />
        <Bar w={260} h={11} />
        <div className="right" style={{ marginLeft: "auto" }}>
          <Bar w={120} h={11} />
        </div>
      </div>
      <div className="hero" style={{ gap: 26 }}>
        <Bar w={150} h={68} />
        <div className="decomp">
          {[92, 80, 86, 74, 88].map((w, i) => (
            <div className="row" key={i}>
              <Bar w={70} h={10} />
              <Bar w={`${w}%`} h={7} />
              <Bar w={30} h={10} />
            </div>
          ))}
        </div>
      </div>
      <div className="strip">
        {Array.from({ length: 6 }).map((_, i) => (
          <div className="stat" key={i}>
            <Bar w={80} h={10} />
            <div style={{ height: 8 }} />
            <Bar w={60} h={21} />
          </div>
        ))}
      </div>
      <div className="grid">
        <SkelCard span={8} />
        <SkelCard span={4} />
        <SkelCard span={4} />
        <SkelCard span={8} />
      </div>
    </div>
  );
}

/** Lighter state shown while a lazy-loaded tab's code chunk arrives. */
export function TabSkeleton() {
  return (
    <div className="grid" aria-busy="true" aria-label="Loading tab">
      <SkelCard span={8} />
      <SkelCard span={4} />
      <SkelCard span={6} />
      <SkelCard span={6} />
    </div>
  );
}
