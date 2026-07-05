(function () {
  "use strict";

  var modal = document.getElementById("payment-modal");
  var backdrop = document.getElementById("payment-modal-backdrop");
  var btnClose = document.getElementById("payment-modal-close");
  var btnCancel = document.getElementById("payment-cancel");
  var btnConfirm = document.getElementById("payment-confirm");
  var btnPayDownload = document.getElementById("btn-pay-download");
  var btnDownload = document.getElementById("btn-download");
  var downloadHint = document.getElementById("download-hint");
  var qrImg = document.getElementById("payment-qr");
  var amountEl = document.getElementById("payment-amount");
  var amountTextEl = document.getElementById("payment-amount-text");
  var taskIdEl = document.getElementById("payment-task-id");
  var paymentError = document.getElementById("payment-error");

  var currentTaskId = null;
  var paymentConfig = { amount: 30, qr_url: "/assets/images/wechat-pay.png" };

  function loadConfig() {
    fetch("/api/payment/config")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (cfg) {
        if (!cfg) return;
        paymentConfig = cfg;
        if (btnPayDownload) {
          btnPayDownload.textContent = "微信支付 ¥" + cfg.amount + " 后下载";
        }
        if (amountEl) amountEl.textContent = cfg.amount;
        if (amountTextEl) amountTextEl.textContent = cfg.amount;
        if (qrImg && cfg.qr_url) qrImg.src = cfg.qr_url;
      })
      .catch(function () {});
  }

  function showModal(taskId) {
    if (!modal) return;
    currentTaskId = taskId;
    if (taskIdEl) taskIdEl.textContent = taskId || "";
    if (paymentError) {
      paymentError.textContent = "";
      paymentError.classList.add("hidden");
    }
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
  }

  function hideModal() {
    if (!modal) return;
    modal.classList.add("hidden");
    document.body.style.overflow = "";
  }

  function setPaidUI(taskId, paid) {
    if (!btnPayDownload || !btnDownload) return;
    if (paid) {
      btnPayDownload.classList.add("hidden");
      btnDownload.classList.remove("hidden");
      btnDownload.href = "/api/tasks/" + taskId + "/download";
      if (downloadHint) downloadHint.textContent = "支付已完成，可下载结果包";
    } else {
      btnPayDownload.classList.remove("hidden");
      btnDownload.classList.add("hidden");
      btnDownload.removeAttribute("href");
      if (downloadHint) {
        downloadHint.textContent = "扫码支付 ¥" + paymentConfig.amount + " 并确认后，即可下载结果包";
      }
    }
  }

  function refreshPaymentStatus(taskId) {
    if (!taskId) return;
    fetch("/api/tasks/" + taskId + "/payment")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        if (data.qr_url && qrImg) qrImg.src = data.qr_url;
        if (data.amount != null) {
          paymentConfig.amount = data.amount;
          if (btnPayDownload) btnPayDownload.textContent = "微信支付 ¥" + data.amount + " 后下载";
        }
        setPaidUI(taskId, !!data.paid);
      })
      .catch(function () {});
  }

  function confirmPayment() {
    if (!currentTaskId) return;
    if (btnConfirm) {
      btnConfirm.disabled = true;
      btnConfirm.textContent = "确认中…";
    }
    fetch("/api/tasks/" + currentTaskId + "/payment/confirm", { method: "POST" })
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (e) {
            throw new Error(e.detail || "确认失败");
          });
        }
        return r.json();
      })
      .then(function () {
        hideModal();
        setPaidUI(currentTaskId, true);
      })
      .catch(function (err) {
        if (paymentError) {
          paymentError.textContent = err.message || "确认失败";
          paymentError.classList.remove("hidden");
        }
      })
      .finally(function () {
        if (btnConfirm) {
          btnConfirm.disabled = false;
          btnConfirm.textContent = "我已完成支付";
        }
      });
  }

  if (btnPayDownload) {
    btnPayDownload.addEventListener("click", function () {
      if (currentTaskId) showModal(currentTaskId);
    });
  }
  if (btnConfirm) btnConfirm.addEventListener("click", confirmPayment);
  if (btnCancel) btnCancel.addEventListener("click", hideModal);
  if (btnClose) btnClose.addEventListener("click", hideModal);
  if (backdrop) backdrop.addEventListener("click", hideModal);

  loadConfig();

  window.PaymentUI = {
    onTaskReady: function (taskId) {
      currentTaskId = taskId;
      refreshPaymentStatus(taskId);
    },
    reset: function () {
      currentTaskId = null;
      if (btnPayDownload) btnPayDownload.classList.remove("hidden");
      if (btnDownload) {
        btnDownload.classList.add("hidden");
        btnDownload.removeAttribute("href");
      }
    },
  };
})();
