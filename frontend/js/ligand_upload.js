(function () {
  "use strict";

  var MAX_LIGANDS = 3;
  var RESNAME_MAX = 4;
  var LIG_META = [
    { dot: "ligand-dot-1", tag: "LIG1" },
    { dot: "ligand-dot-2", tag: "LIG2" },
    { dot: "ligand-dot-3", tag: "LIG3" },
  ];
  // 与后端 _LIGAND_RESNAME_FORBIDDEN 保持一致的常用保留名
  var FORBIDDEN = {
    ALA: 1, ARG: 1, ASN: 1, ASP: 1, CYS: 1, GLN: 1, GLU: 1, GLY: 1, HIS: 1,
    HID: 1, HIE: 1, HIP: 1, ILE: 1, LEU: 1, LYS: 1, MET: 1, PHE: 1, PRO: 1,
    SER: 1, THR: 1, TRP: 1, TYR: 1, VAL: 1, HSD: 1, HSE: 1, HSP: 1,
    SOL: 1, WAT: 1, HOH: 1, TIP3: 1, TIP4: 1, SPC: 1, NA: 1, CL: 1, K: 1,
  };

  var mol2Input = document.getElementById("mol2-file");
  var mol2Name = document.getElementById("mol2-name");
  var extraList = document.getElementById("ligand-extra-list");
  var btnAdd = document.getElementById("btn-add-ligand");
  var tpl = document.getElementById("ligand-extra-template");
  var primaryResname = document.getElementById("ligand-resname-1");
  var primaryTag = document.getElementById("ligand-tag-1");

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

  /** 读取各配体槽位的残基名输入（空字符串表示用默认 LIG{i}） */
  function getLigandResnames() {
    var names = [];
    if (primaryResname) names.push(String(primaryResname.value || "").trim());
    else names.push("");
    if (extraList) {
      extraList.querySelectorAll(".ligand-extra-row").forEach(function (row) {
        var inp = row.querySelector(".ligand-resname-input");
        names.push(inp ? String(inp.value || "").trim() : "");
      });
    }
    return names.slice(0, MAX_LIGANDS);
  }

  /** 校验残基名；通过返回 null，失败返回中文错误信息 */
  function validateLigandResnames(nFiles) {
    var raws = getLigandResnames().slice(0, nFiles);
    var resolved = [];
    var i;
    for (i = 0; i < nFiles; i++) {
      var s = String(raws[i] || "").trim().toUpperCase();
      if (!s) s = "LIG" + (i + 1);
      if (s.length > RESNAME_MAX) {
        return "配体 " + (i + 1) + " 残基名过长：最多 " + RESNAME_MAX + " 个字符（GRO 栏宽 5，本站兼容 Amber 限制为 " + RESNAME_MAX + "）";
      }
      if (!/^[A-Z][A-Z0-9]*$/.test(s)) {
        return "配体 " + (i + 1) + " 残基名不合法：须以字母开头，仅含字母与数字";
      }
      if (FORBIDDEN[s]) {
        return "配体 " + (i + 1) + " 残基名「" + s + "」与氨基酸/溶剂保留名冲突";
      }
      resolved.push(s);
    }
    var seen = {};
    for (i = 0; i < resolved.length; i++) {
      if (seen[resolved[i]]) {
        return "配体残基名不可重复：" + resolved.join(", ");
      }
      seen[resolved[i]] = 1;
    }
    return null;
  }

  function refreshAddButton() {
    if (!btnAdd) return;
    var rows = extraList ? extraList.querySelectorAll(".ligand-extra-row").length : 0;
    btnAdd.disabled = rows >= MAX_LIGANDS - 1;
    btnAdd.textContent =
      rows >= MAX_LIGANDS - 1 ? "已达配体数量上限（3 个）" : "+ 添加配体（最多 3 个）";
  }

  function syncTagFromInput(inp, tagEl, fallback) {
    if (!tagEl) return;
    var s = String((inp && inp.value) || "").trim().toUpperCase();
    tagEl.textContent = s || fallback;
  }

  function bindResnameInput(inp, tagEl, fallback) {
    if (!inp) return;
    inp.placeholder = fallback;
    inp.addEventListener("input", function () {
      // 仅保留字母数字并转大写预览
      var v = String(inp.value || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
      if (v !== inp.value) inp.value = v;
      if (inp.value.length > RESNAME_MAX) inp.value = inp.value.slice(0, RESNAME_MAX);
      syncTagFromInput(inp, tagEl, fallback);
    });
    syncTagFromInput(inp, tagEl, fallback);
  }

  function updateRowMeta(row, index) {
    var meta = LIG_META[index] || LIG_META[2];
    var dot = row.querySelector(".ligand-color-dot");
    var tag = row.querySelector(".ligand-tag");
    var label = row.querySelector(".ligand-extra-label");
    var rnInp = row.querySelector(".ligand-resname-input");
    if (dot) dot.className = "ligand-color-dot " + meta.dot;
    if (tag) tag.textContent = (rnInp && String(rnInp.value || "").trim().toUpperCase()) || meta.tag;
    if (label) label.textContent = "配体 " + (index + 1);
    if (rnInp) {
      rnInp.placeholder = meta.tag;
      rnInp.setAttribute("data-default", meta.tag);
    }
  }

  function bindExtraRow(row, index) {
    updateRowMeta(row, index);
    var nameEl = row.querySelector(".ligand-extra-name");
    var fileInp = row.querySelector(".ligand-extra-file");
    var rmBtn = row.querySelector(".ligand-remove-btn");
    var meta = LIG_META[index] || LIG_META[2];
    bindResnameInput(row.querySelector(".ligand-resname-input"), row.querySelector(".ligand-tag"), meta.tag);

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
      var meta = LIG_META[i + 1] || LIG_META[2];
      var inp = row.querySelector(".ligand-resname-input");
      var tag = row.querySelector(".ligand-tag");
      if (inp && tag) syncTagFromInput(inp, tag, meta.tag);
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

  bindResnameInput(primaryResname, primaryTag, "LIG1");
  refreshAddButton();

  window.WebMD = window.WebMD || {};
  window.WebMD.getMol2Files = getMol2Files;
  window.WebMD.getLigandResnames = getLigandResnames;
  window.WebMD.validateLigandResnames = validateLigandResnames;
  window.WebMD.appendMol2ToFormData = function (fd) {
    var files = getMol2Files();
    if (!files.length) return files;
    fd.append("mol2_file", files[0]);
    if (files[1]) fd.append("mol2_file_2", files[1]);
    if (files[2]) fd.append("mol2_file_3", files[2]);
    var names = getLigandResnames();
    fd.append("ligand_resname", names[0] || "");
    if (files[1]) fd.append("ligand_resname_2", names[1] || "");
    if (files[2]) fd.append("ligand_resname_3", names[2] || "");
    return files;
  };
})();
