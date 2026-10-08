type Child = Node | string | number | null | undefined | false;
type Attrs = Record<string, unknown>;

/** Tiny element builder: h("button.btn.primary", { onclick }, "Slice"). */
export function h<K extends keyof HTMLElementTagNameMap>(spec: K | string, attrs: Attrs = {}, ...children: (Child | Child[])[]): HTMLElement {
  const [tag, ...classes] = spec.split(".");
  const el = document.createElement(tag || "div");
  if (classes.length) el.className = classes.join(" ");
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2), value as EventListener);
    } else if (key === "class") {
      el.className += (el.className ? " " : "") + String(value);
    } else if (key === "style" && typeof value === "object") {
      Object.assign(el.style, value);
    } else if (key in el && typeof value !== "string") {
      (el as unknown as Record<string, unknown>)[key] = value;
    } else {
      el.setAttribute(key, value === true ? "" : String(value));
    }
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
  return el;
}

export function clear(el: HTMLElement, ...children: (Child | Child[])[]): HTMLElement {
  el.replaceChildren();
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
  return el;
}

export function spool(color: string, large = false): HTMLElement {
  const el = h("span.spool" + (large ? ".large" : ""));
  el.style.setProperty("--c", color);
  return el;
}

export interface NumberFieldOptions {
  label: string;
  value: number;
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  onchange: (value: number) => void;
  title?: string;
}

export function numberField(o: NumberFieldOptions): HTMLElement {
  const input = h("input", {
    type: "number",
    value: String(o.value),
    min: o.min,
    max: o.max,
    step: o.step ?? "any",
    onchange: (e: Event) => {
      const v = parseFloat((e.target as HTMLInputElement).value);
      if (Number.isFinite(v)) o.onchange(v);
    },
  });
  return h("div.field", { title: o.title }, h("label", {}, o.label), h("div.with-unit", {}, input, o.unit ? h("em", {}, o.unit) : null));
}

export function selectField(label: string, value: string, options: (string | [string, string])[], onchange: (v: string) => void, title?: string): HTMLElement {
  const select = h(
    "select",
    { onchange: (e: Event) => onchange((e.target as HTMLSelectElement).value) },
    options.map((o) => {
      const [v, text] = Array.isArray(o) ? o : [o, o];
      return h("option", { value: v, selected: v === value }, text);
    }),
  );
  return h("div.field", { title }, h("label", {}, label), select);
}

export function checkField(label: string, checked: boolean, onchange: (v: boolean) => void, title?: string): HTMLElement {
  return h(
    "label.check",
    { title },
    h("input", { type: "checkbox", checked, onchange: (e: Event) => onchange((e.target as HTMLInputElement).checked) }),
    label,
  );
}

export function section(title: string, ...children: (Child | Child[])[]): HTMLElement {
  return h("section.section", {}, h("h2", {}, title), ...children);
}

export function formatTime(seconds: number | undefined): string {
  if (!seconds && seconds !== 0) return "–";
  const h_ = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h_ ? `${h_} h ${m} min` : `${m} min`;
}

export function mm(v: number): string {
  return `${v.toFixed(v >= 100 ? 0 : 1)} mm`;
}
