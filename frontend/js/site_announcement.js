(function () {
  "use strict";

  // 关闭后本机记住，避免每次进站反复打扰
  var STORAGE_KEY = "webmd_site_announcement_v2";
  var bar = document.getElementById("site-announcement");
  var btn = document.getElementById("site-announcement-close");

  if (!bar) return;

  function isDismissed() {
    try {
      return localStorage.getItem(STORAGE_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  function hide() {
    bar.classList.add("hidden");
    try {
      localStorage.setItem(STORAGE_KEY, "1");
    } catch (e) {}
  }

  if (isDismissed()) {
    bar.classList.add("hidden");
    return;
  }

  if (btn) btn.addEventListener("click", hide);
})();
