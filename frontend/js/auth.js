(function () {
  "use strict";

  var TOKEN_KEY = "webmd_token";
  var USER_KEY = "webmd_user";

  function getToken() {
    try {
      return localStorage.getItem(TOKEN_KEY) || "";
    } catch (e) {
      return "";
    }
  }

  function setSession(token, user) {
    try {
      localStorage.setItem(TOKEN_KEY, token);
      localStorage.setItem(USER_KEY, JSON.stringify(user || {}));
    } catch (e) {}
    updateHeaderUI();
  }

  function clearSession() {
    try {
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
    } catch (e) {}
    updateHeaderUI();
  }

  function getUser() {
    try {
      var raw = localStorage.getItem(USER_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function authHeaders() {
    var t = getToken();
    return t ? { Authorization: "Bearer " + t } : {};
  }

  function apiFetch(url, opts) {
    opts = opts || {};
    opts.headers = Object.assign({}, opts.headers || {}, authHeaders());
    return fetch(url, opts);
  }

  function updateHeaderUI() {
    var guest = document.getElementById("auth-guest");
    var userBar = document.getElementById("auth-user-bar");
    var emailEl = document.getElementById("auth-user-email");
    var u = getUser();
    if (u && u.email) {
      if (guest) guest.classList.add("hidden");
      if (userBar) userBar.classList.remove("hidden");
      if (emailEl) emailEl.textContent = u.email;
    } else {
      if (guest) guest.classList.remove("hidden");
      if (userBar) userBar.classList.add("hidden");
    }
  }

  function showAuthModal(mode) {
    var modal = document.getElementById("auth-modal");
    var title = document.getElementById("auth-modal-title");
    var err = document.getElementById("auth-error");
    var codeRow = document.getElementById("auth-code-row");
    var codeInput = document.getElementById("auth-code");
    var btnSubmit = document.getElementById("auth-submit");
    if (!modal) return;
    modal.dataset.mode = mode || "login";
    if (title) title.textContent = mode === "register" ? "注册账号" : "登录";
    if (codeRow) codeRow.classList.toggle("hidden", mode !== "register");
    if (codeInput) codeInput.value = "";
    if (btnSubmit) btnSubmit.textContent = mode === "register" ? "注册" : "登录";
    if (err) {
      err.textContent = "";
      err.classList.add("hidden");
    }
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
  }

  function sendVerificationCode() {
    var email = document.getElementById("auth-email");
    var err = document.getElementById("auth-error");
    var btn = document.getElementById("btn-send-code");
    if (!email) return;

    var em = email.value.trim();
    if (!em) {
      if (err) {
        err.textContent = "请先填写邮箱";
        err.classList.remove("hidden");
      }
      return;
    }

    if (btn) {
      btn.disabled = true;
      btn.textContent = "发送中…";
    }

    fetch("/api/auth/send-verification-code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: em }),
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (e) {
            throw new Error(e.detail || "发送失败");
          });
        }
        return r.json();
      })
      .then(function () {
        if (err) {
          err.textContent = "验证码已发送，请查收邮件";
          err.classList.remove("hidden");
          err.style.color = "#27ae60";
        }
        var sec = 60;
        if (btn) btn.textContent = sec + "s";
        var t = setInterval(function () {
          sec -= 1;
          if (btn) btn.textContent = sec > 0 ? sec + "s" : "获取验证码";
          if (sec <= 0) {
            clearInterval(t);
            if (btn) btn.disabled = false;
          }
        }, 1000);
      })
      .catch(function (e) {
        if (err) {
          err.textContent = e.message || "发送失败";
          err.classList.remove("hidden");
          err.style.color = "";
        }
        if (btn) btn.disabled = false;
        if (btn) btn.textContent = "获取验证码";
      });
  }

  function hideAuthModal() {
    var modal = document.getElementById("auth-modal");
    if (modal) modal.classList.add("hidden");
    document.body.style.overflow = "";
  }

  function submitAuth() {
    var modal = document.getElementById("auth-modal");
    var mode = modal ? modal.dataset.mode : "login";
    var email = document.getElementById("auth-email");
    var password = document.getElementById("auth-password");
    var err = document.getElementById("auth-error");
    var btn = document.getElementById("auth-submit");
    var codeInput = document.getElementById("auth-code");
    if (!email || !password) return;

    var em = email.value.trim();
    var pw = password.value;
    var code = codeInput ? codeInput.value.trim() : "";
    if (!em || !pw) {
      if (err) {
        err.textContent = "请填写邮箱和密码";
        err.classList.remove("hidden");
      }
      return;
    }
    if (mode === "register" && !code) {
      if (err) {
        err.textContent = "请先获取并填写邮箱验证码";
        err.classList.remove("hidden");
      }
      return;
    }

    if (btn) {
      btn.disabled = true;
      btn.textContent = "提交中…";
    }

    var payload = mode === "register"
      ? { email: em, password: pw, code: code }
      : { email: em, password: pw };

    fetch("/api/auth/" + (mode === "register" ? "register" : "login"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        if (!r.ok) {
          return r.json().then(function (e) {
            throw new Error(e.detail || "操作失败");
          });
        }
        return r.json();
      })
      .then(function (data) {
        setSession(data.token, data.user);
        hideAuthModal();
        password.value = "";
      })
      .catch(function (e) {
        if (err) {
          err.textContent = e.message || "操作失败";
          err.classList.remove("hidden");
        }
      })
      .finally(function () {
        if (btn) {
          btn.disabled = false;
          btn.textContent = mode === "register" ? "注册" : "登录";
        }
      });
  }

  function requireLogin() {
    if (getToken()) return true;
    showAuthModal("login");
    return false;
  }

  function downloadWithAuth(taskId) {
    return apiFetch("/api/tasks/" + taskId + "/download").then(function (r) {
      if (!r.ok) {
        return r.json().then(function (e) {
          throw new Error(e.detail || "下载失败");
        });
      }
      return r.blob().then(function (blob) {
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "gromacs_md_" + taskId + ".tar.gz";
        a.click();
        URL.revokeObjectURL(a.href);
      });
    });
  }

  document.getElementById("btn-login") &&
    document.getElementById("btn-login").addEventListener("click", function () {
      showAuthModal("login");
    });
  document.getElementById("btn-register") &&
    document.getElementById("btn-register").addEventListener("click", function () {
      showAuthModal("register");
    });
  document.getElementById("btn-logout") &&
    document.getElementById("btn-logout").addEventListener("click", clearSession);
  document.getElementById("btn-send-code") &&
    document.getElementById("btn-send-code").addEventListener("click", sendVerificationCode);
  document.getElementById("auth-submit") &&
    document.getElementById("auth-submit").addEventListener("click", submitAuth);
  document.getElementById("auth-cancel") &&
    document.getElementById("auth-cancel").addEventListener("click", hideAuthModal);
  document.getElementById("auth-modal-close") &&
    document.getElementById("auth-modal-close").addEventListener("click", hideAuthModal);
  document.getElementById("auth-switch-register") &&
    document.getElementById("auth-switch-register").addEventListener("click", function (e) {
      e.preventDefault();
      showAuthModal("register");
    });
  document.getElementById("auth-switch-login") &&
    document.getElementById("auth-switch-login").addEventListener("click", function (e) {
      e.preventDefault();
      showAuthModal("login");
    });

  var authBackdrop = document.getElementById("auth-modal-backdrop");
  if (authBackdrop) authBackdrop.addEventListener("click", hideAuthModal);

  updateHeaderUI();

  window.WebMdAuth = {
    getToken: getToken,
    getUser: getUser,
    apiFetch: apiFetch,
    requireLogin: requireLogin,
    downloadWithAuth: downloadWithAuth,
    updateHeaderUI: updateHeaderUI,
  };
})();
