import { engine, type Task } from "../engine";
import { clear, h } from "./dom";

/** Running tasks in the status bar, with progress and a cancel button. */
export function mountTaskStrip(root: HTMLElement, status: HTMLElement) {
  const active = new Map<string, Task>();
  const render = () => {
    clear(
      root,
      [...active.values()].map((t) =>
        h(
          "div.task",
          { title: t.message },
          h("span", {}, `${t.title}: ${t.message}`),
          h("progress", { max: 1, value: t.status === "queued" ? 0 : Math.max(0.02, t.progress) }),
          h("button.x", { title: "Cancel", "aria-label": `Cancel ${t.title}`, onclick: () => engine.cancel(t.id) }, "✕"),
        ),
      ),
    );
  };
  engine.onTask((task) => {
    if (task.status === "queued" || task.status === "running") active.set(task.id, task);
    else {
      active.delete(task.id);
      status.textContent = task.status === "done" ? `${task.title} finished` : `${task.title}: ${task.status}`;
    }
    render();
  });
}
