// Markdown compatibility rendering — backed by marked + DOMPurify.
import { esc } from "../core/dom.js";

const { marked, DOMPurify } = globalThis;
let render;

if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
  console.error("[BAA] marked/DOMPurify not loaded — markdown will render as plain text");
  render = text => esc(text || "");
} else {
  const renderer = new marked.Renderer();
  const originalLink = renderer.link.bind(renderer);
  renderer.link = (href, title, text) => {
    const html = originalLink(href, title, text);
    if (!href) return html;
    const newTab = /^https?:\/\//i.test(href) || href.startsWith("/dashboard/");
    if (!newTab) return html;
    return html.replace(/^<a /, '<a target="_blank" rel="noopener" ');
  };

  marked.setOptions({
    renderer,
    breaks: true,
    gfm: true,
    headerIds: false,
    mangle: false,
  });

  render = text => {
    if (!text) return "";
    const raw = marked.parse(String(text));
    return DOMPurify.sanitize(raw, { ADD_ATTR: ["target"] });
  };
}

export function renderMd(text) {
  return render(text);
}
