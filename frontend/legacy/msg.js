// Compatibility message helpers + token bar + /status renderer.
import { state } from "../core/app-store.js";
import { $, scrollBottom } from "../core/dom.js";
import { getUiIsland } from "../core/ui-registry.js";
import { renderMd } from "./markdown.js";

  // ── 气泡内图片：no-referrer 策略绕过 OSS 防盗链 ─────────────────
  // OSS referer 白名单通常允许「空 Referer」，浏览器加 referrerpolicy="no-referrer"
  // 后发出的图片请求不带 Referer 头，多数情况下可以通过防盗链。
  // 同时准备 blob 兜底：若直连仍失败，用 fetch(no-cors) 拿 blob 本地显示。

  export function bindBubbleImages(bubbleEl) {
    bubbleEl.querySelectorAll("img").forEach(img => {
      if (img.dataset.bound) return;
      img.dataset.bound = "1";

      const originalSrc = img.getAttribute("src") || "";
      if (!originalSrc.startsWith("http")) return;

      // Step 1: 加 no-referrer，浏览器不发 Referer 头
      img.referrerPolicy = "no-referrer";
      img.crossOrigin    = "anonymous";

      // 点击新标签打开
      img.style.cursor = "pointer";
      img.addEventListener("click", () => window.open(originalSrc, "_blank", "noopener"));

      // Step 2: 若 no-referrer 仍失败，尝试 fetch blob 兜底
      img.addEventListener("error", () => {
        if (img.dataset.blobTried) {
          _replaceWithLink(img, originalSrc);
          return;
        }
        img.dataset.blobTried = "1";
        // fetch with no-cors — 拿到 opaque response，转 blob 作为本地 objectURL
        fetch(originalSrc, { mode: "no-cors", referrerPolicy: "no-referrer" })
          .then(r => r.blob())
          .then(blob => {
            if (!blob.size) throw new Error("empty blob");
            const blobUrl = URL.createObjectURL(blob);
            img.src = blobUrl;
            // blob URL 用完后在页面卸载时释放
            window.addEventListener("beforeunload", () => URL.revokeObjectURL(blobUrl), { once: true });
          })
          .catch(() => _replaceWithLink(img, originalSrc));
      });
    });
  }

  function _replaceWithLink(img, src) {
    const link = document.createElement("a");
    link.href   = src;
    link.target = "_blank";
    link.rel    = "noopener";
    link.textContent = "🖼️ " + (img.alt || "查看图片（点击打开原链接）");
    link.style.cssText = "display:inline-block;padding:6px 10px;background:#f1f5f9;" +
      "border-radius:6px;font-size:13px;color:#3b82f6;text-decoration:none;";
    img.replaceWith(link);
  }

  export function appendMsg(role, text, options = {}) {
    const chatUi = getUiIsland("chat");
    if (chatUi?.appendMsg) {
      const vueEl = chatUi.appendMsg(role, text, options);
      if (vueEl) {
        bindBubbleImages(vueEl.querySelector(".msg-bubble"));
        scrollBottom();
        return vueEl;
      }
    }

    const msgs = $("messages");
    const div  = document.createElement("div");
    div.className = `msg ${role}`;
    if (options.variant) div.classList.add(`msg-${options.variant}`);
    const avatar = role === "user"
      ? "👤"
      : `<img class="assistant-avatar-img" src="/static/Images/icon.png" alt="AI">`;
    div.innerHTML = `
      <div class="msg-avatar">${avatar}</div>
      <div class="msg-body">
        <div class="tool-steps"></div>
        <div class="msg-bubble">${text !== null ? renderMd(text) : ""}</div>
      </div>`;
    msgs.appendChild(div);
    // 绑定气泡内图片交互
    bindBubbleImages(div.querySelector(".msg-bubble"));
    scrollBottom();
    return div;
  }

  export function sysMsg(text) {
    const chatUi = getUiIsland("chat");
    if (chatUi?.sysMsg) {
      const vueEl = chatUi.sysMsg(text);
      scrollBottom();
      return vueEl;
    }

    const msgs = $("messages");
    const d = document.createElement("div");
    d.className = "sys-msg";
    d.style.cssText = "text-align:center;font-size:12px;color:#94a3b8;padding:3px 0;";
    d.textContent = text;
    msgs.appendChild(d);
  }

  export function clearMessages() {
    const chatUi = getUiIsland("chat");
    if (chatUi?.clear) {
      chatUi.clear();
    }
    document.querySelectorAll(".msg, .sys-msg").forEach(el => el.remove());
  }

  function fmtK(n) { return n >= 1000 ? (n / 1000).toFixed(1) + "K" : String(n); }

  export function updateTokenBar() {
    const wrap  = $("token-bar-wrap");
    const fill  = $("token-bar-fill");
    const label = $("token-bar-label");
    const { promptTokens, totalInput, totalOutput, contextWindow } = state.tokenState;

    if (!promptTokens && !totalInput) { wrap.classList.remove("visible"); return; }
    wrap.classList.add("visible");

    // Toggle warn/crit modifiers without touching the base class, so the same
    // function works for both the legacy .token-bar-fill and the new
    // .token-pill-fill (only the modifier classes need to flip).
    fill.classList.remove("warn", "crit");

    if (contextWindow) {
      const pct = Math.min(promptTokens / contextWindow * 100, 100);
      fill.style.width = pct + "%";
      if      (pct >= 85) fill.classList.add("crit");
      else if (pct >= 60) fill.classList.add("warn");
      label.textContent = t('ctx.bar', {
        used:  fmtK(promptTokens),
        total: fmtK(contextWindow),
        pct:   pct.toFixed(1),
      });
    } else {
      fill.style.width = "0%";
      label.textContent = t('token.bar', { input: fmtK(totalInput), output: fmtK(totalOutput) });
    }
  }

  export function showStatus() {
    const provKey   = $("model-sel").value;
    const cfg       = state.modelConfigs[provKey] || {};
    const modelName = cfg.model || provKey || t('status.no_model');
    const ctx       = state.tokenState.contextWindow;
    const pct       = (ctx && state.tokenState.promptTokens)
      ? ` (${(state.tokenState.promptTokens / ctx * 100).toFixed(1)}%)`
      : "";

    const lines = [
      t('status.line.model', { v: modelName }),
      t('status.line.src',   { v: state.srcConnected ? state.srcName : t('sidebar.disconnected') }),
      ``,
      t('status.line.usage'),
      t('status.line.input',  { v: state.tokenState.totalInput.toLocaleString() }),
      t('status.line.output', { v: state.tokenState.totalOutput.toLocaleString() }),
      ctx
        ? t('status.line.ctx',      { used: state.tokenState.promptTokens.toLocaleString(), total: ctx.toLocaleString(), pct })
        : t('status.line.ctx_none', { used: state.tokenState.promptTokens.toLocaleString() }),
    ];

    const aEl = appendMsg("assistant", null);
    aEl.querySelector(".msg-bubble").innerHTML = renderMd(lines.join("\n"));
    scrollBottom();
  }

