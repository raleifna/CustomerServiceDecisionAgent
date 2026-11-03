import React from "react";
import ReactDOM from "react-dom/client";

import StaffConsole from "./StaffConsole";
import Storefront from "./shop/Storefront";
import "./shop/shop.css";

const isStaffConsole = window.location.pathname.startsWith("/staff");

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {isStaffConsole ? <StaffConsole /> : <Storefront />}
  </React.StrictMode>,
);
