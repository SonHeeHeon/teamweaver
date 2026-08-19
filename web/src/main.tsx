import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ReportPage } from "./report/ReportPage";
import "./index.css";

// 라우터 라이브러리를 새로 들이지 않는다 -- 경로가 둘뿐이다.
const isReport = window.location.pathname.startsWith("/report");

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {isReport ? <ReportPage /> : <App />}
  </React.StrictMode>,
);
