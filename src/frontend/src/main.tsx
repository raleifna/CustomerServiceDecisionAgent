import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import StaffConsole from "./StaffConsole";
import "./styles.css";

const isStaffConsole = window.location.pathname.startsWith("/staff");

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {isStaffConsole ? <StaffConsole /> : <App />}
  </React.StrictMode>,
);
