import React from "react";
import ReactDOM from "react-dom/client";
import Site from "./Site";
import { DepthProvider } from "./depth";
import "./styles.css";
// The family interface (vendored from liquilens-site, see each file's header), then Seiche's own layer.
import "./research-ui.css";
import "./family-tokens.css";
import "./research-interface.css";
import "./research/economy";
import "./research/flow";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <DepthProvider>
      <Site />
    </DepthProvider>
  </React.StrictMode>
);
