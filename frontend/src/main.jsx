import React, { useState, useEffect } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { GoogleOAuthProvider } from "@react-oauth/google";
import App from "./App";
import api from "./api/client";

function Root() {
  const [clientId, setClientId] = useState(null);

  useEffect(() => {
    api.get("/api/settings/oauth")
      .then(res => setClientId(res.data.client_id || ""))
      .catch(() => setClientId(""));
  }, []);

  if (clientId === null) return null; // wait for client ID before rendering

  return (
    <GoogleOAuthProvider clientId={clientId}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </GoogleOAuthProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>
);
