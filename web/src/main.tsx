import React from "react";
import ReactDOM from "react-dom/client";
import Root from "./Root";
import { ReportPage } from "./report/ReportPage";
import { installAuthFetch } from "./auth";
import "./index.css";

// 라우터 라이브러리를 새로 들이지 않는다 -- 경로가 둘뿐이다.
const isReport = window.location.pathname.startsWith("/report");
// PDF용 /report는 서버 안 브라우저가 데이터를 주입받아 그린다(로그인 없음). 앱 화면만 로그인 관문을 둔다.
if (!isReport) installAuthFetch();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {isReport ? <ReportPage /> : <Root />}
  </React.StrictMode>,
);
