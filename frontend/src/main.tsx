import React from "react";
import ReactDOM from "react-dom/client";
import Site from "./Site";
import { DepthProvider } from "./depth";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <DepthProvider>
      <Site />
    </DepthProvider>
  </React.StrictMode>
);
