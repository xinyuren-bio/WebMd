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
  var taskIdEl = document.getElementById("payment-task-id");
  var payerNoteEl = document.getElementById("payment-payer-note");
  var pendingBox = document.getElementById("payment-pending-box");
  var modalActions = document.getElementById("payment-modal-actions");
  var paymentError = document.getElementById("payment-error");
  var tipSection = document.getElementById("tip-section");
  var tipQr = document.getElementById("tip-qr");
  var tipTaskId = document.getElementById("tip-task-id");

  var currentTaskId = null;
  var paymentConfig = { amount: 30, qr_url: "/assets/images/pay.jpg", enabled: false, tip_enabled: true };
  var paymentEnabled = false;
  var tipEnabled = false;
  var pollTimer = null;

  function hideTipSection() {
    if (tipSection) tipSection.classList.add("hidden");
  }

  function showTipSection(taskId) {
    if (!tipEnabled || !tipSection) return;
    if (tipTaskId) tipTaskId.textContent = taskId || "";
    if (tipQr && paymentConfig.tip_qr_url) tipQr.src = paymentConfig.tip_qr_url;
    tipSection.classList.remove("hidden");
  }

  function enableFreeDownloadMode() {
    paymentEnabled = false;
    stopPolling();
    if (btnPayDownload) btnPayDownload.classList.add("hidden");
    if (modal) modal.classList.add("hidden");
  }

  function showFreeDownload(taskId) {
    if (btnPayDownload) btnPayDownload.classList.add("hidden");
    if (btnDownload) {
      btnDownload.classList.remove("hidden");
      btnDownload.href = "/api/tasks/" + taskId + "/download";
    }
    if (downloadHint) downloadHint.textContent = "任务已完成，可下载结果包";
    showTipSection(taskId);
  }

  function formatAmount(n) {
    return Number(n).toFixed(2);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function startPolling(taskId) {
    stopPolling();
    pollTimer = setInterval(function () {
      refreshPaymentStatus(taskId);
    }, 5000);
  }

  function loadConfig() {
    fetch("/api/payment/config")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (cfg) {
        if (!cfg) return;
        paymentConfig = cfg;
        paymentEnabled = cfg.enabled === true;
        tipEnabled = cfg.tip_enabled === true;
        if (cfg.tip_qr_url && tipQr) tipQr.src = cfg.tip_qr_url;
        if (!paymentEnabled) {
          enableFreeDownloadMode();
          return;
        }
        hideTipSection();
        if (btnPayDownload) btnPayDownload.classList.remove("hidden");
        updatePayButtonLabel("unpaid", cfg.amount);
      })
      .catch(function () {
        enableFreeDownloadMode();
      });
  }

  function updatePayButtonLabel(status, amount) {
    if (!btnPayDownload) return;
    btnPayDownload.disabled = false;
    if (status === "paid") {
      btnPayDownload.classList.add("hidden");
      return;
    }
    btnPayDownload.classList.remove("hidden");
    if (status === "pending") {
      btnPayDownload.disabled = true;
      btnPayDownload.textContent = "支付核实中…";
      return;
    }
    var a = amount != null ? formatAmount(amount) : formatAmount(paymentConfig.amount);
    btnPayDownload.textContent = "支付宝支付 ¥" + a + " 后下载";
  }

  function showModal(taskId, paymentData) {
    if (!modal) return;
    currentTaskId = taskId;
    if (taskIdEl) taskIdEl.textContent = taskId || "";
    if (paymentError) {
      paymentError.textContent = "";
      paymentError.classList.add("hidden");
    }

    var status = paymentData && paymentData.payment_status
      ? paymentData.payment_status
      : (paymentData && paymentData.paid ? "paid" : "unpaid");

    if (status === "pending") {
      if (pendingBox) pendingBox.classList.remove("hidden");
      if (modalActions) modalActions.classList.add("hidden");
      if (payerNoteEl) payerNoteEl.disabled = true;
    } else {
      if (pendingBox) pendingBox.classList.add("hidden");
      if (modalActions) modalActions.classList.remove("hidden");
      if (payerNoteEl) {
        payerNoteEl.disabled = false;
        payerNoteEl.value = "";
      }
    }

    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
  }

  function hideModal() {
    if (!modal) return;
    modal.classList.add("hidden");
    document.body.style.overflow = "";
  }

  function applyPaymentData(taskId, data) {
    if (!data) return;
    var amount = data.payment_amount != null ? data.payment_amount : data.amount;
    var status = data.payment_status || (data.paid ? "paid" : "unpaid");

    if (data.qr_url && qrImg) qrImg.src = data.qr_url;
    if (amount != null) {
      paymentConfig.amount = amount;
      if (amountEl) amountEl.textContent = formatAmount(amount);
    }

    if (status === "paid") {
      stopPolling();
      if (btnPayDownload) btnPayDownload.classList.add("hidden");
      if (btnDownload) {
        btnDownload.classList.remove("hidden");
        btnDownload.href = "/api/tasks/" + taskId + "/download";
      }
      if (downloadHint) downloadHint.textContent = "支付已核实，可下载结果包";
      hideTipSection();
    } else if (status === "pending") {
      if (btnDownload) {
        btnDownload.classList.add("hidden");
        btnDownload.removeAttribute("href");
      }
      updatePayButtonLabel("pending", amount);
      if (downloadHint) {
        downloadHint.textContent = "支付核实中，请稍候。核实通过后将自动出现下载按钮";
      }
      startPolling(taskId);
    } else {
      stopPolling();
      if (btnPayDownload) btnPayDownload.classList.remove("hidden");
      if (btnDownload) {
        btnDownload.classList.add("hidden");
        btnDownload.removeAttribute("href");
      }
      updatePayButtonLabel("unpaid", amount);
      if (downloadHint) {
        var a = amount != null ? formatAmount(amount) : formatAmount(paymentConfig.amount);
        downloadHint.textContent = "扫码支付 ¥" + a + " 并确认后，等待核实即可下载";
      }
    }
  }

  function refreshPaymentStatus(taskId) {
    if (!taskId) return;
    if (!paymentEnabled) {
      showFreeDownload(taskId);
      return;
    }
    fetch("/api/tasks/" + taskId + "/payment")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        applyPaymentData(taskId, data);
        if (modal && !modal.classList.contains("hidden") && currentTaskId === taskId) {
          var status = data.payment_status || (data.paid ? "paid" : "unpaid");
          if (status === "pending") {
            if (pendingBox) pendingBox.classList.remove("hidden");
            if (modalActions) modalActions.classList.add("hidden");
          } else if (status === "paid") {
            hideModal();
          }
        }
      })
      .catch(function () {});
  }

  function confirmPayment() {
    if (!currentTaskId) return;
    if (btnConfirm) {
      btnConfirm.disabled = true;
      btnConfirm.textContent = "提交中…";
    }

    var fd = new FormData();
    fd.append("payer_note", payerNoteEl ? payerNoteEl.value.trim() : "");

    fetch("/api/tasks/" + currentTaskId + "/payment/confirm", {
      method: "POST",
      body: fd,
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (e) {
            throw new Error(e.detail || "提交失败");
          });
        }
        return r.json();
      })
      .then(function (data) {
        applyPaymentData(currentTaskId, data);
        if (pendingBox) pendingBox.classList.remove("hidden");
        if (modalActions) modalActions.classList.add("hidden");
        if (payerNoteEl) payerNoteEl.disabled = true;
      })
      .catch(function (err) {
        if (paymentError) {
          paymentError.textContent = err.message || "提交失败";
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
      if (!paymentEnabled || !currentTaskId) return;
      fetch("/api/tasks/" + currentTaskId + "/payment")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (data && amountEl) amountEl.textContent = formatAmount(data.payment_amount || data.amount);
          showModal(currentTaskId, data);
        })
        .catch(function () {
          showModal(currentTaskId, null);
        });
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
      stopPolling();
      if (!paymentEnabled) {
        if (btnDownload) {
          btnDownload.classList.add("hidden");
          btnDownload.removeAttribute("href");
        }
        hideTipSection();
        return;
      }
      if (btnPayDownload) {
        btnPayDownload.classList.remove("hidden");
        btnPayDownload.disabled = false;
      }
      if (btnDownload) {
        btnDownload.classList.add("hidden");
        btnDownload.removeAttribute("href");
      }
    },
  };
})();
