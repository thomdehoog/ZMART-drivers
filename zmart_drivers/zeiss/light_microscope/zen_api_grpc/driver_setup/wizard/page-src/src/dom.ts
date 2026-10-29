/** Small helpers for building the page without a framework. */

type Child = Node | string | null | undefined | false;

/** Make an element: `h("button", { class: "primary", onclick }, "Save")`. */
export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  props: Record<string, unknown> = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "class") node.className = String(value);
    else if (key.startsWith("on") && typeof value === "function")
      node.addEventListener(key.slice(2), value as EventListener);
    else if (value !== undefined && value !== false) node.setAttribute(key, String(value));
  }
  for (const child of children) if (child) node.append(child);
  return node;
}
