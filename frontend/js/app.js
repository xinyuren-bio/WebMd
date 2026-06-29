(function () {
  "use strict";

  const pdbInput = document.getElementById("pdb-file");
  const mol2Input = document.getElementById("mol2-file");
  const pdbName = document.getElementById("pdb-name");
  const mol2Name = document.getElementById("mol2-name");

  const btnSubmit = document.getElementById("btn-submit");
  const submitHint = document.getElementById("submit-hint");
  const progressArea = document.getElementById("progress-area");
  const progressFill = document.getElementById("progress-fill");
  const progressText = document.getElementById("progress-text");
  const taskIdDisplay = document.getElementById("task-id-display");
  const errorArea = document.getElementById("error-area");
  const errorMsg = document.getElementById("error-msg");
  const sectionDownload = document.getElementById("section-download");
  const btnDownload = document.getElementById("btn-download");

  const pipeSteps = {
    processing_protein: document.getElementById("pipe-protein"),
    processing_ligand: document.getElementById("pipe-ligand"),
    solvating: document.getElementById("pipe-solvation"),
    converting_gmx: document.getElementById("pipe-convert"),
    generating_mdp: document.getElementById("pipe-mdp"),
  };

  pdbInput.addEventListener("change", function () {
    pdbName.textContent = this.files[0] ? this.files[0].name : "未选择文件";
    pdbName.classList.toggle("has-file", !!this.files[0]);
    updateSubmitButton();
  });

  mol2Input.addEventListener("change", function () {
    mol2Name.textContent = this.files[0] ? this.files[0].name : "未选择文件";
    mol2Name.classList.toggle("has-file", !!this.files[0]);
    updateSubmitButton();
  });

  function updateSubmitButton() {
    var ready = pdbInput.files[0] && mol2Input.files[0];
    btnSubmit.disabled = !ready;
    submitHint.textContent = ready ? "文件已就绪，点击提交" : "请先上传两个文件";
  }

  btnSubmit.disabled = true;

  function getParams() {
    return {
      temperature: parseFloat(document.getElementById("temperature").value),
      pressure: parseFloat(document.getElementById("pressure").value),
      timestep: parseFloat(document.getElementById("timestep").value) / 1000,
      simulation_time_ns: parseFloat(document.getElementById("sim-time").value),
      ion_conc: parseFloat(document.getElementById("ion-conc").value),
      constraints: document.getElementById("constraints").value,
      nonbonded_cutoff: parseFloat(document.getElementById("nonbonded-cutoff").value),
      box_padding: parseFloat(document.getElementById("box-padding").value),
      tau_t: parseFloat(document.getElementById("tau-t").value),
      tau_p: parseFloat(document.getElementById("tau-p").value),
      report_interval_ps: parseFloat(document.getElementById("report-interval").value),
    };
  }

  btnSubmit.addEventListener("click", async function () {
    if (!pdbInput.files[0] || !mol2Input.files[0]) return;

    errorArea.classList.add("hidden");
    sectionDownload.classList.add("hidden");
    if (window.MdViewer) window.MdViewer.hide();
    if (window.LigandFF) window.LigandFF.hide();
    progressArea.classList.remove("hidden");
    resetPipeline();

    var formData = new FormData();
    formData.append("pdb_file", pdbInput.files[0]);
    formData.append("mol2_file", mol2Input.files[0]);

    var params = getParams();
    Object.keys(params).forEach(function (key) {
      formData.append(key, params[key]);
    });

    try {
      btnSubmit.disabled = true;
      btnSubmit.textContent = "提交中...";

      var resp = await fetch("/api/tasks", { method: "POST", body: formData });

      if (!resp.ok) {
        var err = await resp.json();
        throw new Error(err.detail || "创建任务失败");
      }

      var task = await resp.json();
      taskIdDisplay.textContent = "任务 ID: " + task.task_id;
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
        var resp = await fetch("/api/tasks/" + taskId);
        if (!resp.ok) { clearInterval(interval); return; }

        var task = await resp.json();
        updatePipeline(task.status);
        progressText.textContent = task.status_label;

        var progressMap = {
          pending: 5,
          processing_protein: 15,
          processing_ligand: 30,
          solvating: 50,
          converting_gmx: 70,
          generating_mdp: 85,
          packaging: 95,
          completed: 100,
        };
        progressFill.style.width = (progressMap[task.status] || 0) + "%";

        if (task.status === "completed") {
          clearInterval(interval);
          btnDownload.href = "/api/tasks/" + taskId + "/download";
          sectionDownload.classList.remove("hidden");
          if (window.MdViewer) window.MdViewer.load(taskId);
          if (window.LigandFF) window.LigandFF.load(taskId);
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
      if (el) { el.classList.remove("active", "done", "failed"); }
    });
  }

  function updatePipeline(status) {
    resetPipeline();
    var order = [
      "processing_protein", "processing_ligand", "solvating",
      "converting_gmx", "generating_mdp"
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
        if (el) { el.classList.add("done"); }
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
})();
