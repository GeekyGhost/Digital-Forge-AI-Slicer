import { h } from "./dom";

export function toast(title: string, body = "", kind: "info" | "error" | "success" = "info", ms = kind === "error" ? 9000 : 5000) {
  const root = document.getElementById("toasts")!;
  const el = h("div.toast" + (kind === "info" ? "" : "." + kind), { role: kind === "error" ? "alert" : "status" }, h("strong", {}, title), body);
  el.addEventListener("click", () => el.remove());
  root.append(el);
  setTimeout(() => el.remove(), ms);
}
