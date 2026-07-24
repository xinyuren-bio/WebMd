(function () {
  "use strict";

  var MAX_LIGANDS = 3;
  var LIG_META = [
    { dot: "ligand-dot-1", tag: "LIG1" },
    { dot: "ligand-dot-2", tag: "LIG2" },
    { dot: "ligand-dot-3", tag: "LIG3" },
  ];

  var mol2Input = document.getElementById("mol2-file");
  var mol2Name = document.getElementById("mol2-name");
  var extraList = document.getElementById("ligand-extra-list");
  var btnAdd = document.getElementById("btn-add-ligand");
  var tpl = document.getElementById("ligand-extra-template");

  function countLigandSlots() {
    var n = mol2Input && mol2Input.files[0] ? 1 : 0;
    if (extraList) {
      n += extraList.querySelectorAll(".ligand-extra-row").length;
    }
    return n;
  }

  function getMol2Files() {
    var files = [];
    if (mol2Input && mol2Input.files[0]) files.push(mol2Input.files[0]);
    if (extraList) {
      extraList.querySelectorAll(".ligand-extra-file").forEach(function (inp) {
        if (inp.files[0]) files.push(inp.files[0]);
      });
    }
    return files.slice(0, MAX_LIGANDS);
  }

  function refreshAddButton() {
    if (!btnAdd) return;
    var rows = extraList ? extraList.querySelectorAll(".ligand-extra-row").length : 0;
    btnAdd.disabled = rows >= MAX_LIGANDS - 1;
    btnAdd.textContent =
      rows >= MAX_LIGANDS - 1 ? "已达配体数量上限（3 个）" : "+ 添加配体（最多 3 个）";
  }

  function updateRowMeta(row, index) {
    var meta = LIG_META[index] || LIG_META[2];
    var dot = row.querySelector(".ligand-color-dot");
    var tag = row.querySelector(".ligand-tag");
    var label = row.querySelector(".ligand-extra-label");
    if (dot) dot.className = "ligand-color-dot " + meta.dot;
    if (tag) tag.textContent = meta.tag;
    if (label) label.textContent = "配体 " + (index + 1);
  }

  function bindExtraRow(row, index) {
    updateRowMeta(row, index);
    var nameEl = row.querySelector(".ligand-extra-name");
    var fileInp = row.querySelector(".ligand-extra-file");
    var rmBtn = row.querySelector(".ligand-remove-btn");

    fileInp.addEventListener("change", function () {
      nameEl.textContent = fileInp.files[0] ? fileInp.files[0].name : "未选择文件";
      nameEl.classList.toggle("has-file", !!fileInp.files[0]);
      window.WebMD && window.WebMD.onLigandChange && window.WebMD.onLigandChange();
    });

    rmBtn.addEventListener("click", function () {
      row.remove();
      reindexExtraRows();
      refreshAddButton();
      window.WebMD && window.WebMD.onLigandChange && window.WebMD.onLigandChange();
    });
  }

  function reindexExtraRows() {
    if (!extraList) return;
    extraList.querySelectorAll(".ligand-extra-row").forEach(function (row, i) {
      updateRowMeta(row, i + 1);
    });
  }

  function addExtraRow() {
    if (!tpl || !extraList) return;
    if (extraList.querySelectorAll(".ligand-extra-row").length >= MAX_LIGANDS - 1) return;
    var node = tpl.content.cloneNode(true);
    var row = node.querySelector(".ligand-extra-row");
    extraList.appendChild(node);
    bindExtraRow(row, extraList.querySelectorAll(".ligand-extra-row").length);
    refreshAddButton();
  }

  if (btnAdd) {
    btnAdd.addEventListener("click", addExtraRow);
  }

  refreshAddButton();

  window.WebMD = window.WebMD || {};
  window.WebMD.getMol2Files = getMol2Files;
  window.WebMD.appendMol2ToFormData = function (fd) {
    var files = getMol2Files();
    if (!files.length) return files;
    fd.append("mol2_file", files[0]);
    if (files[1]) fd.append("mol2_file_2", files[1]);
    if (files[2]) fd.append("mol2_file_3", files[2]);
    return files;
  };
})();