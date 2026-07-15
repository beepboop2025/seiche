import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { DepthProvider } from "./depth";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <DepthProvider>
      <App />
    </DepthProvider>
  </React.StrictMode>
);
