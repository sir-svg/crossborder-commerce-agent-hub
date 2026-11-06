"use strict";
const password = document.querySelector("#password");
const toggle = document.querySelector("#toggle-password");
toggle.addEventListener("click", () => {
  const visible = password.type === "password";
  password.type = visible ? "text" : "password";
  toggle.textContent = visible ? "隐藏" : "显示";
  toggle.setAttribute("aria-pressed", String(visible));
});
document.querySelector("#login-form").addEventListener("submit", () => {
  const button = document.querySelector("#login-submit");
  button.disabled = true;
  button.textContent = "正在登录…";
});
// 后退缓存恢复时重新启用按钮，仍可正常提交。
window.addEventListener("pageshow", () => {
  const button = document.querySelector("#login-submit");
  button.disabled = false;
  const arrow = document.createElement("span");
  arrow.textContent = "↗";
  arrow.setAttribute("aria-hidden", "true");
  button.replaceChildren(document.createTextNode("登录工作台"), arrow);
});
