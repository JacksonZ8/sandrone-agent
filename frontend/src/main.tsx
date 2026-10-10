import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import PrefsProvider from "./prefs/PrefsProvider";
import { registerServiceWorker } from "./pwa";
import "@fontsource/cormorant-garamond/600.css";
import "@fontsource/noto-serif-sc/500.css";
import "./theme/tokens.css";
import "./theme/legacy.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <PrefsProvider>
      <App />
    </PrefsProvider>
  </StrictMode>,
);

registerServiceWorker();
