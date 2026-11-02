import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./styles.css";
import { applyTheme, getTheme } from "./theme";

applyTheme(getTheme()); // restore the remembered Light / Auto / Dark choice before the first paint

// QueryClient is React Query's cache. Every `useQuery` in the app reads from and writes to this one
// cache, so two components asking for "/api/runs" share a single request and update together.
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, staleTime: 5_000 } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
