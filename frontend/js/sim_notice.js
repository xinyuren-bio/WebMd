(function () {
  "use strict";

  var STORAGE_KEY = "webmd_sim_notice_dismissed_v2";
  var modal = document.getElementById("sim-notice-modal");
  var btnOk = document.getElementById("sim-notice-ok");
  var btnClose = document.getElementById("sim-notice-close");
  var backdrop = document.getElementById("sim-notice-backdrop");
  var chkDismiss = document.getElementById("sim-notice-dismiss");

  if (!modal) return;

  function isDismissed() {
    try {
      return localStorage.getItem(STORAGE_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  function persistDismiss() {
    try {
      localStorage.setItem(STORAGE_KEY, "1");
    } catch (e) {}
  }

  function show() {
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
  }

  function hide() {
    if (chkDismiss && chkDismiss.checked) {
      persistDismiss();
    }
    modal.classList.add("hidden");
    document.body.style.overflow = "";
  }

  if (btnOk) btnOk.addEventListener("click", hide);
  if (btnClose) btnClose.addEventListener("click", hide);
  if (backdrop) backdrop.addEventListener("click", hide);

  // 进入页面自动弹出；勾选「不再提示」后写入 localStorage
  if (!isDismissed()) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", show);
    } else {
      show();
    }
  }
})();
