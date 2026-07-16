(function () {
  "use strict";

  // 同一标签页同一路径 30 分钟内只上报一次，避免刷新/切 tab 重复计数
  var DEDUPE_MS = 30 * 60 * 1000;

  function track() {
    var path = location.pathname + (location.hash || "");
    if (!path) path = "/";
    var key = "webmd_pv_" + path;
    try {
      var last = parseInt(sessionStorage.getItem(key) || "0", 10);
      if (last && Date.now() - last < DEDUPE_MS) return;
      sessionStorage.setItem(key, String(Date.now()));
    } catch (e) {}

    fetch("/api/analytics/visit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: path }),
    }).catch(function () {});
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", track);
  } else {
    track();
  }

  window.addEventListener("hashchange", track);
})();
