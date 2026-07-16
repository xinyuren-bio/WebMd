(function () {
  "use strict";

  const pdbInput = document.getElementById("pdb-file");
  const mol2Input = document.getElementById("mol2-file");
  const pdbName = document.getElementById("pdb-name");
  const mol2Name = document.getElementById("mol2-name");
  const ligandTypeSel = document.getElementById("ligand-type");
  const ligandTypeHint = document.getElementById("ligand-type-hint");
  const peptideInput = document.getElementById("peptide-file");
  const peptideName = document.getElementById("peptide-file-name");
  const peptideLabel = document.getElementById("peptide-file-label");
  const mol2Group = document.getElementById("ligand-mol2-group");
  const peptideGroup = document.getElementById("ligand-peptide-group");
  const mol2Actions = document.getElementById("ligand-mol2-actions");
  const uploadSubtitle = document.getElementById("upload-subtitle");

  const btnSubmit = document.getElementById("btn-submit");
  const submitHint = document.getElementById("submit-hint");
  const progressArea = document.getElementById("progress-area");
  const progressFill = document.getElementById("progress-fill");
  const progressText = document.getElementById("progress-text");
  const taskIdDisplay = document.getElementById("task-id-display");
  const taskQrArea = document.getElementById("task-qr-area");
  const taskQrImg = document.getElementById("task-qr-img");
  const taskStatusLink = document.getElementById("task-status-link");
  const errorArea = document.getElementById("error-area");
  const errorMsg = document.getElementById("error-msg");
  const sectionDownload = document.getElementById("section-download");

  const pipeSteps = {
    processing_protein: document.getElementById("pipe-protein"),
    processing_ligand: document.getElementById("pipe-ligand"),
    solvating: document.getElementById("pipe-solvation"),
    converting_gmx: document.getElementById("pipe-convert"),
    generating_mdp: document.getElementById("pipe-mdp"),
  };

  function apiFetch(url, opts) {
    if (window.WebMdAuth && window.WebMdAuth.apiFetch) {
      return window.WebMdAuth.apiFetch(url, opts);
    }
    return fetch(url, opts);
  }

  function showTaskQr(taskId) {
    if (!taskQrArea || !taskId) return;
    var statusUrl = location.origin + "/status.html?id=" + encodeURIComponent(taskId);
    if (taskQrImg) {
      taskQrImg.src =
        "https://api.qrserver.com/v1/create-qr-code/?size=180x180&data=" +
        encodeURIComponent(statusUrl);
    }
    if (taskStatusLink) {
      taskStatusLink.href = statusUrl;
      taskStatusLink.textContent = statusUrl;
    }
    taskQrArea.classList.remove("hidden");
  }

  function hideTaskQr() {
    if (taskQrArea) taskQrArea.classList.add("hidden");
  }

  pdbInput.addEventListener("change", function () {
    pdbName.textContent = this.files[0] ? this.files[0].name : "未选择文件";
    pdbName.classList.toggle("has-file", !!this.files[0]);
    updateSubmitButton();
    updatePreview();
  });

  mol2Input.addEventListener("change", function () {
    mol2Name.textContent = this.files[0] ? this.files[0].name : "未选择文件";
    mol2Name.classList.toggle("has-file", !!this.files[0]);
    updateSubmitButton();
    updatePreview();
  });

  if (peptideInput) {
    peptideInput.addEventListener("change", function () {
      if (peptideName) {
        peptideName.textContent = this.files[0] ? this.files[0].name : "未选择文件";
        peptideName.classList.toggle("has-file", !!this.files[0]);
      }
      updateSubmitButton();
      updatePreview();
    });
  }

  function getLigandType() {
    return ligandTypeSel ? ligandTypeSel.value : "mol2";
  }

  function isPeptideMode() {
    var t = getLigandType();
    return t === "cyclic" || t === "linear";
  }

  function isCyclicMode() {
    return getLigandType() === "cyclic";
  }

  function syncLigandTypeUi() {
    var t = getLigandType();
    var pep = t === "cyclic" || t === "linear";
    if (mol2Group) mol2Group.classList.toggle("hidden", pep);
    if (peptideGroup) peptideGroup.classList.toggle("hidden", !pep);
    if (mol2Actions) mol2Actions.classList.toggle("hidden", pep);
    if (peptideLabel) {
      peptideLabel.textContent = t === "linear" ? "线形肽 · PDB" : "环肽 · PDB";
    }
    if (uploadSubtitle) {
      if (t === "cyclic") {
        uploadSubtitle.textContent =
          "蛋白 PDB + 环肽 PDB（标准氨基酸，头尾成环；请预先摆好相对位置）";
      } else if (t === "linear") {
        uploadSubtitle.textContent =
          "蛋白 PDB + 线形肽 PDB（标准氨基酸，保留 N/C 末端；请预先摆好相对位置）";
      } else {
        uploadSubtitle.textContent =
          "蛋白 PDB 必填；配体 MOL2 至少 1 个，最多 3 个（请在外部软件中摆好相对位置）";
      }
    }
    if (ligandTypeHint) {
      if (t === "cyclic") {
        ligandTypeHint.textContent =
          "请上传仅含环肽的 PDB（勿用 MOL2、勿含受体）。ff14SB + 自动连接首尾 N–C；暂不支持侧链杂原子修饰。";
      } else if (t === "linear") {
        ligandTypeHint.textContent =
          "请上传仅含线形肽的 PDB（勿用 MOL2、勿含受体）。ff14SB，保留 N/C 末端，不成环；暂不支持非标准氨基酸。";
      } else {
        ligandTypeHint.textContent =
          "小分子请上传 MOL2；肽类请上传仅含肽链的 PDB（标准氨基酸，勿含受体蛋白）。";
      }
    }
    var addH = document.getElementById("ligand-add-h");
    if (addH) {
      addH.disabled = pep;
      if (pep) addH.checked = false;
    }
    updateSubmitButton();
    updatePreview();
  }

  if (ligandTypeSel) {
    ligandTypeSel.addEventListener("change", syncLigandTypeUi);
  }

  window.WebMD = window.WebMD || {};
  window.WebMD.onLigandChange = function () {
    updateSubmitButton();
    updatePreview();
  };

  function getMol2Files() {
    if (window.WebMD && window.WebMD.getMol2Files) {
      return window.WebMD.getMol2Files();
    }
    return mol2Input.files[0] ? [mol2Input.files[0]] : [];
  }

  function updatePreview() {
    if (!window.MdViewer) return;
    if (isPeptideMode()) {
      if (
        pdbInput.files[0] &&
        peptideInput &&
        peptideInput.files[0] &&
        window.MdViewer.loadFromPdbs
      ) {
        window.MdViewer.loadFromPdbs(pdbInput.files[0], peptideInput.files[0]);
      } else {
        window.MdViewer.hide();
      }
      return;
    }
    var mol2s = getMol2Files();
    if (pdbInput.files[0] && mol2s.length && window.MdViewer.loadFromFiles) {
      window.MdViewer.loadFromFiles(pdbInput.files[0], mol2s);
    } else {
      window.MdViewer.hide();
    }
  }

  function updateSubmitButton() {
    var ready;
    if (isPeptideMode()) {
      ready = !!(pdbInput.files[0] && peptideInput && peptideInput.files[0]);
    } else {
      ready = !!(pdbInput.files[0] && getMol2Files().length > 0);
    }
    var loggedIn = window.WebMdAuth && window.WebMdAuth.getToken();
    btnSubmit.disabled = !ready;
    if (!loggedIn && ready) {
      submitHint.textContent = "请先登录后再提交";
    } else if (ready) {
      submitHint.textContent = "文件已就绪，点击提交";
    } else if (getLigandType() === "cyclic") {
      submitHint.textContent = "请先上传蛋白 PDB 与环肽 PDB";
    } else if (getLigandType() === "linear") {
      submitHint.textContent = "请先上传蛋白 PDB 与线形肽 PDB";
    } else {
      submitHint.textContent = "请先上传 PDB 与至少一个 MOL2";
    }
  }

  btnSubmit.disabled = true;

  function getParams() {
    return {
      temperature: parseFloat(document.getElementById("temperature").value),
      pressure: parseFloat(document.getElementById("pressure").value),
      timestep: parseFloat(document.getElementById("timestep").value) / 1000,
      simulation_time_ns: parseFloat(document.getElementById("sim-time").value),
      ion_conc: parseFloat(document.getElementById("ion-conc").value),
      salt_type: document.getElementById("salt-type")
        ? document.getElementById("salt-type").value
        : "nacl",
      constraints: document.getElementById("constraints").value,
      nonbonded_cutoff: parseFloat(document.getElementById("nonbonded-cutoff").value),
      box_padding: parseFloat(document.getElementById("box-padding").value),
      tau_t: parseFloat(document.getElementById("tau-t").value),
      tau_p: parseFloat(document.getElementById("tau-p").value),
      report_interval_ps: parseFloat(document.getElementById("report-interval").value),
      ligand_add_hydrogens: document.getElementById("ligand-add-h") &&
        document.getElementById("ligand-add-h").checked ? "1" : "0",
      ligand_type: getLigandType(),
      is_cyclic_peptide: getLigandType() === "cyclic" ? "1" : "0",
      is_linear_peptide: getLigandType() === "linear" ? "1" : "0",
    };
  }

  btnSubmit.addEventListener("click", async function () {
    if (isPeptideMode()) {
      if (!pdbInput.files[0] || !peptideInput || !peptideInput.files[0]) return;
    } else if (!pdbInput.files[0] || !getMol2Files().length) {
      return;
    }
    if (window.WebMdAuth && !window.WebMdAuth.requireLogin()) return;

    var params = getParams();
    if (params.simulation_time_ns !== 10 && params.simulation_time_ns !== 100 && params.simulation_time_ns !== 200) {
      showError("模拟时长仅支持 10 ns、100 ns 或 200 ns");
      return;
    }

    errorArea.classList.add("hidden");
    sectionDownload.classList.add("hidden");
    hideTaskQr();
    if (window.PaymentUI) window.PaymentUI.reset();
    progressArea.classList.remove("hidden");
    resetPipeline();

    var formData = new FormData();
    formData.append("pdb_file", pdbInput.files[0]);
    if (isPeptideMode()) {
      // 后端字段名沿用 cyclic_peptide_file，线形/环肽共用上传槽
      formData.append("cyclic_peptide_file", peptideInput.files[0]);
    } else if (window.WebMD && window.WebMD.appendMol2ToFormData) {
      window.WebMD.appendMol2ToFormData(formData);
    } else {
      formData.append("mol2_file", mol2Input.files[0]);
    }
    Object.keys(params).forEach(function (key) {
      formData.append(key, params[key]);
    });

    try {
      btnSubmit.disabled = true;
      btnSubmit.textContent = "提交中...";

      var resp = await apiFetch("/api/tasks", { method: "POST", body: formData });

      if (!resp.ok) {
        var err = await resp.json();
        throw new Error(err.detail || "创建任务失败");
      }

      var task = await resp.json();
      taskIdDisplay.textContent = "任务 ID: " + task.task_id;
      showTaskQr(task.task_id);
      pollTask(task.task_id);
    } catch (e) {
      showError(e.message);
      btnSubmit.disabled = false;
      btnSubmit.textContent = "开始准备模拟体系";
    }
  });

  function pollTask(taskId) {
    var interval = setInterval(async function () {
      try {
        var resp = await apiFetch("/api/tasks/" + taskId);
        if (!resp.ok) {
          clearInterval(interval);
          return;
        }

        var task = await resp.json();
        updatePipeline(task.status);
        progressText.textContent = task.status_label;

        var progressMap = {
          pending: 5,
          processing_protein: 15,
          processing_ligand: 30,
          awaiting_charge_confirm: 35,
          solvating: 50,
          converting_gmx: 70,
          generating_mdp: 85,
          packaging: 95,
          completed: 100,
        };
        progressFill.style.width = (progressMap[task.status] || 0) + "%";

        if (task.status === "awaiting_charge_confirm") {
          clearInterval(interval);
          showChargeConfirmModal(taskId, task);
          return;
        }

        if (task.status === "completed") {
          clearInterval(interval);
          sectionDownload.classList.remove("hidden");
          renderLigandFfSummary(task);
          if (window.MdViewer && (!window.MdViewer.hasLocalPreview || !window.MdViewer.hasLocalPreview())) {
            window.MdViewer.load(taskId);
          }
          if (window.PaymentUI) window.PaymentUI.onTaskReady(taskId);
          btnSubmit.textContent = "重新提交";
          btnSubmit.disabled = false;
        }

        if (task.status === "failed") {
          clearInterval(interval);
          showError(task.error_message || "任务执行失败");
          btnSubmit.textContent = "重新提交";
          btnSubmit.disabled = false;
        }
      } catch (e) {
        clearInterval(interval);
        showError("轮询失败: " + e.message);
        btnSubmit.disabled = false;
        btnSubmit.textContent = "开始准备模拟体系";
      }
    }, 1500);
  }

  function resetPipeline() {
    Object.keys(pipeSteps).forEach(function (k) {
      var el = pipeSteps[k];
      if (el) {
        el.classList.remove("active", "done", "failed");
      }
    });
  }

  function updatePipeline(status) {
    resetPipeline();
    var order = [
      "processing_protein", "processing_ligand", "solvating",
      "converting_gmx", "generating_mdp",
    ];
    var found = false;
    order.forEach(function (key) {
      if (found) return;
      var el = pipeSteps[key];
      if (!el) return;
      if (key === status) {
        el.classList.add("active");
        found = true;
      } else {
        el.classList.add("done");
      }
    });

    if (status === "completed") {
      Object.keys(pipeSteps).forEach(function (k) {
        var el = pipeSteps[k];
        if (el) el.classList.add("done");
      });
    }
    if (status === "failed") {
      Object.keys(pipeSteps).forEach(function (k) {
        var el = pipeSteps[k];
        if (el && el.classList.contains("active")) {
          el.classList.remove("active");
          el.classList.add("failed");
        }
      });
    }
  }

  function showError(msg) {
    errorArea.classList.remove("hidden");
    errorMsg.textContent = msg;
  }

  function renderLigandFfSummary(task) {
    var box = document.getElementById("ligand-ff-summary");
    if (!box) return;
    var ligs = task.ligands_ff || (task.params && task.params.ligands) || [];
    var lt = (task.params && task.params.ligand_type) || "";
    var isCyc =
      lt === "cyclic" || !!(task.params && task.params.is_cyclic_peptide);
    var isLin =
      lt === "linear" || !!(task.params && task.params.is_linear_peptide);
    if (!ligs.length || isCyc || isLin) {
      if (isCyc) {
        box.innerHTML =
          "<h4>力场说明</h4><p>环肽：Amber <strong>ff14SB</strong>（头尾 N–C 成环）；蛋白同为 ff14SB。</p>";
        box.classList.remove("hidden");
      } else if (isLin) {
        box.innerHTML =
          "<h4>力场说明</h4><p>线形肽：Amber <strong>ff14SB</strong>（保留 N/C 末端，不成环）；蛋白同为 ff14SB。</p>";
        box.classList.remove("hidden");
      } else {
        box.classList.add("hidden");
      }
      return;
    }
    var items = ligs.map(function (L) {
      var nc = L.net_charge != null ? L.net_charge : "—";
      return (
        "<li><strong>" +
        (L.resname || "LIG") +
        "</strong>（" +
        (L.source || "") +
        "）：净电荷 <strong>" +
        nc +
        "</strong> · 力场 " +
        (L.force_field || "GAFF2") +
        " · 电荷方法 " +
        (L.charge_method || "AM1-BCC") +
        "</li>"
      );
    });
    box.innerHTML =
      "<h4>小分子力场参数</h4><ul>" +
      items.join("") +
      "</ul><p class=\"hint\">以上设置已写入结果包中的 ligand_forcefield_summary.json</p>";
    box.classList.remove("hidden");
  }

  var chargeModal = document.getElementById("charge-confirm-modal");
  var chargeMsg = document.getElementById("charge-confirm-msg");
  var chargeSelect = document.getElementById("charge-confirm-select");
  var chargeErr = document.getElementById("charge-confirm-error");
  var pendingChargeTaskId = null;
  var pendingChargeLigIndex = 1;

  function hideChargeConfirmModal() {
    if (chargeModal) chargeModal.classList.add("hidden");
    if (chargeErr) {
      chargeErr.textContent = "";
      chargeErr.classList.add("hidden");
    }
  }

  function showChargeConfirmModal(taskId, task) {
    var info = task.charge_confirm || {};
    pendingChargeTaskId = taskId;
    pendingChargeLigIndex = info.ligand_index || 1;
    if (chargeMsg) {
      chargeMsg.textContent =
        info.message ||
        "原电荷计算失败，请选择一个可行净电荷后继续。";
    }
    if (chargeSelect) {
      chargeSelect.innerHTML = "";
      var opts = info.working_charges || [];
      if (!opts.length) opts = [0];
      opts.forEach(function (q) {
        var o = document.createElement("option");
        o.value = String(q);
        o.textContent = String(q);
        chargeSelect.appendChild(o);
      });
    }
    if (chargeModal) chargeModal.classList.remove("hidden");
    progressText.textContent = "等待确认配体净电荷";
  }

  var btnChargeOk = document.getElementById("charge-confirm-ok");
  var btnChargeCancel = document.getElementById("charge-confirm-cancel");
  if (btnChargeOk) {
    btnChargeOk.addEventListener("click", async function () {
      if (!pendingChargeTaskId || !chargeSelect) return;
      try {
        if (chargeErr) chargeErr.classList.add("hidden");
        var resp = await apiFetch(
          "/api/tasks/" + pendingChargeTaskId + "/confirm-charge",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              ligand_index: pendingChargeLigIndex,
              charge: parseInt(chargeSelect.value, 10),
            }),
          }
        );
        if (!resp.ok) {
          var err = await resp.json();
          throw new Error(err.detail || "确认失败");
        }
        hideChargeConfirmModal();
        progressArea.classList.remove("hidden");
        progressText.textContent = "已确认净电荷，继续构建…";
        pollTask(pendingChargeTaskId);
      } catch (e) {
        if (chargeErr) {
          chargeErr.textContent = e.message || String(e);
          chargeErr.classList.remove("hidden");
        }
      }
    });
  }
  if (btnChargeCancel) {
    btnChargeCancel.addEventListener("click", function () {
      hideChargeConfirmModal();
      btnSubmit.disabled = false;
      btnSubmit.textContent = "重新提交";
    });
  }

  window.addEventListener("storage", updateSubmitButton);
  setInterval(updateSubmitButton, 2000);
  syncLigandTypeUi();
})();
