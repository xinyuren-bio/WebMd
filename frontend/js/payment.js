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
  var wechatQrImg = document.getElementById("payment-wechat-qr");
  var amountEl = document.getElementById("payment-amount");
  var amountHintEl = document.getElementById("payment-amount-hint");
  var amountHintWrap = document.getElementById("payment-amount-hint-wrap");
  var amountNoteEl = document.getElementById("payment-amount-note");
  var modalTitleEl = document.getElementById("payment-modal-title");
  var taskIdEl = document.getElementById("payment-task-id");
  var payerNoteEl = document.getElementById("payment-payer-note");
  var pendingBox = document.getElementById("payment-pending-box");
  var modalActions = document.getElementById("payment-modal-actions");
  var paymentError = document.getElementById("payment-error");
  var tipSection = document.getElementById("tip-section");
  var tipQr = document.getElementById("tip-qr");
  var tipTaskId = document.getElementById("tip-task-id");
  var qrRow = document.getElementById("payment-qr-row");
  var freeHint = document.getElementById("payment-free-hint");
  var freeRemainEl = document.getElementById("payment-free-remain");
  var freeTotalEl = document.getElementById("payment-free-total");
  var amountLine = document.querySelector("#payment-modal .payment-amount");
  var paymentNoteEl = document.querySelector("#payment-modal .payment-note");
  var paymentTaskIdHint = document.querySelector("#payment-modal .payment-task-id");

  var currentTaskId = null;
  var paymentConfig = { amount: 147.7, qr_url: "/assets/images/pay_150.jpg", wechat_qr_url: "/assets/images/wechat_pay_150.png", enabled: true, tip_enabled: false, free_10ns_quota: 5 };
  var paymentEnabled = true;
  var tipEnabled = false;
  var pollTimer = null;
  var freeMode = false;

  function apiFetch(url, opts) {
    if (window.WebMdAuth && window.WebMdAuth.apiFetch) {
      return window.WebMdAuth.apiFetch(url, opts);
    }
    return fetch(url, opts);
  }

  function hideDownloadBtn() {
    if (btnDownload) btnDownload.classList.add("hidden");
  }

  function showDownloadBtn() {
    if (btnDownload) btnDownload.classList.remove("hidden");
  }

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

  function setAmountDisplay(amount) {
    var a = formatAmount(amount);
    if (amountEl) amountEl.textContent = a;
    if (amountHintEl) amountHintEl.textContent = a;
    if (amountNoteEl) amountNoteEl.textContent = a;
    if (modalTitleEl) {
      modalTitleEl.textContent = freeMode
        ? "使用免费额度启动 MD"
        : "支付 ¥" + a + " 启动 MD 模拟";
    }
  }

  function setFreeModeUi(on, data) {
    freeMode = !!on;
    if (qrRow) qrRow.classList.toggle("hidden", freeMode);
    if (amountHintWrap) amountHintWrap.classList.toggle("hidden", freeMode);
    if (amountLine) amountLine.classList.toggle("hidden", freeMode);
    if (paymentNoteEl) paymentNoteEl.classList.toggle("hidden", freeMode);
    if (paymentTaskIdHint) paymentTaskIdHint.classList.toggle("hidden", freeMode);
    if (payerNoteEl) {
      payerNoteEl.classList.toggle("hidden", freeMode);
      var lab = document.querySelector('label[for="payment-payer-note"]');
      if (lab) lab.classList.toggle("hidden", freeMode);
    }
    if (freeHint) {
      freeHint.classList.toggle("hidden", !freeMode);
      if (freeMode && data) {
        if (freeRemainEl) freeRemainEl.textContent = String(data.free_quota_remaining != null ? data.free_quota_remaining : "—");
        if (freeTotalEl) freeTotalEl.textContent = String(data.free_quota_total != null ? data.free_quota_total : 5);
      }
    }
    if (btnConfirm) {
      btnConfirm.textContent = freeMode ? "使用免费额度" : "我已完成支付";
    }
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
        if (cfg.qr_url && qrImg) qrImg.src = cfg.qr_url;
        if (cfg.wechat_qr_url && wechatQrImg) wechatQrImg.src = cfg.wechat_qr_url;
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

  function updatePayButtonLabel(status, amount, data) {
    if (!btnPayDownload) return;
    btnPayDownload.disabled = false;
    if (status === "paid") {
      btnPayDownload.classList.add("hidden");
      return;
    }
    // 面议档（>30 万原子）：不支持自助付款，引导联系客服微信
    if (data && data.negotiable) {
      btnPayDownload.classList.remove("hidden");
      btnPayDownload.disabled = true;
      btnPayDownload.textContent = "体系较大 · 请加客服微信面议";
      return;
    }
    btnPayDownload.classList.remove("hidden");
    if (status === "pending") {
      btnPayDownload.disabled = true;
      btnPayDownload.textContent = "支付核实中…";
      return;
    }
    if (data && data.free_eligible) {
      var left = data.free_quota_remaining != null ? data.free_quota_remaining : "?";
      var tot = data.free_quota_total != null ? data.free_quota_total : 5;
      btnPayDownload.textContent = "免费启动 10 ns（剩余 " + left + "/" + tot + "）";
      return;
    }
    var a = amount != null ? formatAmount(amount) : formatAmount(paymentConfig.amount);
    btnPayDownload.textContent = "支付 ¥" + a + " 启动模拟";
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
    var isFree = !!(paymentData && paymentData.free_eligible && status === "unpaid");
    setFreeModeUi(isFree, paymentData || {});
    if (paymentData && paymentData.payment_amount != null) {
      setAmountDisplay(paymentData.payment_amount);
    }

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
    var isFree = !!(data.free_eligible && status === "unpaid");

    // 面议档（>30 万原子）：展示客服微信码，禁用自助付款
    if (data.negotiable && status !== "paid") {
      stopPolling();
      if (data.qr_url && qrImg) qrImg.src = data.qr_url;
      if (data.wechat_qr_url && wechatQrImg) wechatQrImg.src = data.wechat_qr_url;
      setFreeModeUi(false, data);
      if (amountEl) amountEl.textContent = "面议";
      if (amountHintEl) amountHintEl.textContent = "面议";
      if (amountNoteEl) amountNoteEl.textContent = "面议";
      if (modalTitleEl) modalTitleEl.textContent = "体系较大，请添加客服微信面议";
      if (btnDownload) hideDownloadBtn();
      updatePayButtonLabel("unpaid", null, data);
      if (downloadHint) {
        downloadHint.textContent =
          "该体系超过 30 万原子，价格需面议。请扫描客服微信二维码添加，确认后由客服为你开通。";
      }
      if (btnConfirm) {
        btnConfirm.disabled = true;
        btnConfirm.textContent = "请联系客服微信";
      }
      return;
    }

    if (data.qr_url && qrImg) qrImg.src = data.qr_url;
    if (data.wechat_qr_url && wechatQrImg) wechatQrImg.src = data.wechat_qr_url;
    if (amount != null) {
      paymentConfig.amount = amount;
      setFreeModeUi(isFree, data);
      setAmountDisplay(amount);
    } else {
      setFreeModeUi(isFree, data);
    }

    if (status === "paid") {
      stopPolling();
      setFreeModeUi(false, data);
      if (btnPayDownload) btnPayDownload.classList.add("hidden");
      if (btnDownload) showDownloadBtn();
      if (downloadHint) {
        downloadHint.textContent = Number(amount) <= 0
          ? "已使用免费额度，可下载文件包；MD 模拟将排队（需管理员配置算力）"
          : "支付已核实，可下载文件包；MD 模拟将自动排队运行";
      }
      hideTipSection();
    } else if (status === "pending") {
      setFreeModeUi(false, data);
      if (btnDownload) hideDownloadBtn();
      if (downloadHint) {
        downloadHint.textContent = "支付核实中，请稍候。核实通过后将自动出现下载按钮";
      }
      startPolling(taskId);
    } else {
      stopPolling();
      if (btnPayDownload) btnPayDownload.classList.remove("hidden");
      if (btnDownload) hideDownloadBtn();
      updatePayButtonLabel("unpaid", amount, data);
      if (downloadHint) {
        if (isFree) {
          var left = data.free_quota_remaining != null ? data.free_quota_remaining : "?";
          downloadHint.textContent = "前处理已完成。可使用 10 ns 免费额度（剩余 " + left + " 次）直接启动";
        } else {
          var a = amount != null ? formatAmount(amount) : formatAmount(paymentConfig.amount);
          downloadHint.textContent = "前处理已完成。支付 ¥" + a + " 后等待核实，通过后可下载并启动 MD";
        }
      }
    }
  }

  function refreshPaymentStatus(taskId) {
    if (!taskId) return;
    if (!paymentEnabled) {
      showFreeDownload(taskId);
      return;
    }
    apiFetch("/api/tasks/" + taskId + "/payment")
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

    apiFetch("/api/tasks/" + currentTaskId + "/payment/confirm", {
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
        if (data.payment_status === "paid") {
          hideModal();
          return;
        }
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
          btnConfirm.textContent = freeMode ? "使用免费额度" : "我已完成支付";
        }
      });
  }

  if (btnPayDownload) {
    btnPayDownload.addEventListener("click", function () {
      if (!paymentEnabled || !currentTaskId) return;
      apiFetch("/api/tasks/" + currentTaskId + "/payment")
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (data) {
            applyPaymentData(currentTaskId, data);
          }
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

  if (btnDownload) {
    btnDownload.addEventListener("click", function () {
      if (!currentTaskId || !window.WebMdAuth) return;
      btnDownload.disabled = true;
      window.WebMdAuth.downloadWithAuth(currentTaskId)
        .catch(function (e) {
          alert(e.message || "下载失败");
        })
        .finally(function () {
          btnDownload.disabled = false;
        });
    });
  }

  loadConfig();

  /**
   * 从状态页/我的任务深链恢复支付：/?task=JobID#prepare
   */
  function resumePaymentFromQuery() {
    var params = new URLSearchParams(location.search);
    var tid = (params.get("task") || params.get("pay") || "").trim();
    if (!tid) return;

    function run() {
      if (!window.WebMdAuth || !window.WebMdAuth.getToken()) {
        if (window.WebMdAuth) window.WebMdAuth.requireLogin();
        return;
      }
      if (window.WebMD && typeof window.WebMD.switchView === "function") {
        window.WebMD.switchView("prepare");
      }
      var sectionDownload = document.getElementById("section-download");
      if (sectionDownload) sectionDownload.classList.remove("hidden");
      var progressArea = document.getElementById("progress-area");
      if (progressArea) {
        progressArea.classList.remove("hidden");
        var tidEl = document.getElementById("task-id-display");
        if (tidEl) {
          tidEl.innerHTML =
            'Job ID：<a class="task-id-link" href="/status.html?id='
            + encodeURIComponent(tid)
            + '"><code>'
            + tid
            + "</code></a>";
        }
      }

      currentTaskId = tid;
      var apiFetch = window.WebMdAuth.apiFetch;
      apiFetch("/api/tasks/" + tid)
        .then(function (r) {
          if (r.status === 401 || r.status === 403) {
            window.WebMdAuth.requireLogin();
            return null;
          }
          if (!r.ok) {
            return r.json().then(function (e) {
              throw new Error(e.detail || "无法加载任务");
            });
          }
          return r.json();
        })
        .then(function (task) {
          if (!task) return;
          // 等待肽序列：交给 app.js 的深链恢复（显示填写面板）
          if (task.status === "awaiting_peptide_sequence") {
            if (window.WebMdPrep && typeof window.WebMdPrep.pollTask === "function") {
              window.WebMdPrep.pollTask(tid);
            }
            return null;
          }
          // 未完成前处理时不打开支付弹窗
          if (task.status !== "completed") return null;
          return apiFetch("/api/tasks/" + tid + "/payment");
        })
        .then(function (r) {
          if (!r) return null;
          if (r.status === 401 || r.status === 403) {
            window.WebMdAuth.requireLogin();
            return null;
          }
          if (!r.ok) {
            return r.json().then(function (e) {
              throw new Error(e.detail || "无法加载支付信息");
            });
          }
          return r.json();
        })
        .then(function (data) {
          if (!data) return;
          applyPaymentData(tid, data);
          var status = data.payment_status || (data.paid ? "paid" : "unpaid");
          if (status === "unpaid" || status === "pending") {
            showModal(tid, data);
          }
        })
        .catch(function (err) {
          alert(err.message || "打开支付失败，请从「我的任务」重试");
        });
    }

    run();
    window.addEventListener("webmd-auth-changed", function onAuth() {
      if (window.WebMdAuth && window.WebMdAuth.getToken()) {
        window.removeEventListener("webmd-auth-changed", onAuth);
        run();
      }
    });
  }

  // 等 auth / 视图脚本就绪后再恢复
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      setTimeout(resumePaymentFromQuery, 0);
    });
  } else {
    setTimeout(resumePaymentFromQuery, 0);
  }

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
      if (btnDownload) hideDownloadBtn();
    },
  };
})();
