import { registerUiIsland } from "../../core/ui-registry.js";
import { bindBubbleImages } from "../../legacy/msg.js";
import { renderMd } from "../../legacy/markdown.js";

// Progressive Vue island for the chat message list.
// It renders the outer message shell and now owns basic text-stream state.
// Charts, outline cards, and ask_user still use legacy DOM handlers.
export function mountChatUi() {
  window.BAA = window.BAA || {};

  const root = document.getElementById("chat-vue-root");
  const composerQueueRoot = document.getElementById("composer-queue-root");
  const Vue = window.Vue;
  if (!root || !Vue || !Vue.h || !Vue.render) {
    registerUiIsland("chat", null);
    return;
  }

  const { h, render, Fragment } = Vue;
  const messages = [];
  let seq = 0;
  let toolSeq = 0;
  let chartSeq = 0;
  let cardSeq = 0;
  let jobSeq = 0;
  const MIN_TOOL_VISIBLE_MS = 650;
  const ACTIVITY_KIND = "activity";
  const ACTIVITY_HIDE_DELAY_MS = 2000;

  const chartObserver = window.IntersectionObserver
    ? new IntersectionObserver((entries) => {
        entries.forEach(entry => {
          if (!entry.isIntersecting) return;
          const iframe = entry.target;
          if (!iframe.src) iframe.src = iframe.dataset.src;
          chartObserver.unobserve(iframe);
        });
      }, { rootMargin: "200px" })
    : null;

  function _assistantAvatar() {
    return h("img", {
      class: "assistant-avatar-img",
      src: "/static/Images/icon.png",
      alt: "AI",
    });
  }

  function _messageNode(msg) {
    if (msg.kind === "system") {
      return h("div", {
        key: msg.id,
        class: "sys-msg",
        "data-vue-msg-id": msg.id,
        style: "text-align:center;font-size:12px;color:#94a3b8;padding:3px 0;",
      }, msg.text || "");
    }

    const classes = ["msg", msg.role];
    if (msg.variant) classes.push(`msg-${msg.variant}`);

    return h("div", {
      key: msg.id,
      class: classes.join(" "),
      "data-vue-msg-id": msg.id,
    }, [
      h("div", { class: "msg-avatar" }, [
        msg.role === "user" ? "👤" : _assistantAvatar(),
      ]),
      h("div", { class: "msg-body" }, [
        _renderTurnQueueState(msg),
        h("div", { class: "tool-steps" }),
        h("div", { class: "job-list" }),
        h("div", { class: "chart-list" }),
        h("div", { class: "msg-bubble" }),
        h("div", { class: "card-list" }),
      ]),
    ]);
  }

  function _queueText(key, fallback, params) {
    if (!window.t) return fallback;
    const value = t(key, params);
    return value && value !== key ? value : fallback;
  }

  function _renderTurnQueueState(msg) {
    if (!msg.queueStatus) return null;
    let label = _queueText("queue.processing", "Starting…");
    if (msg.queueStatus === "queued") {
      label = _queueText("queue.waiting", `Waiting · position ${msg.queuePosition}`, {
        position: msg.queuePosition,
      });
    } else if (msg.queueStatus === "canceled") {
      label = _queueText("queue.canceled", "Removed from queue");
    }
    const children = [
      h("span", { class: "turn-queue-icon", "aria-hidden": "true" }, msg.queueStatus === "queued" ? "⏳" : "·"),
      h("span", { class: "turn-queue-label" }, label),
    ];
    if (msg.queueStatus === "queued" && msg.queueCallbacks?.onCancel) {
      children.push(h("button", {
        type: "button",
        class: "turn-queue-cancel",
        onClick: () => msg.queueCallbacks.onCancel(),
      }, _queueText("queue.cancel", "Cancel queued message")));
    }
    return h("div", {
      class: `turn-queue-state turn-queue-${msg.queueStatus}`,
      role: "status",
      "aria-live": "polite",
    }, children);
  }

  function renderComposerQueue(items, callbacks) {
    if (!composerQueueRoot) return false;
    const queue = Array.isArray(items) ? items : [];
    if (!queue.length) {
      render(null, composerQueueRoot);
      return true;
    }
    const first = queue[0];
    const countText = _queueText("queue.count", `${queue.length} messages queued`, { count: queue.length });
    const icon = (paths) => h("svg", {
      viewBox: "0 0 24 24",
      width: "20",
      height: "20",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": "1.9",
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      "aria-hidden": "true",
    }, paths.map(path => h("path", { d: path })));
    const children = [
      h("span", { class: "composer-queue-grip", "aria-hidden": "true" }, "⠿"),
      h("div", { class: "composer-queue-copy" }, [
        h("span", { class: "composer-queue-message" }, first.displayText || first.message || ""),
        h("span", { class: "composer-queue-count" }, countText),
      ]),
      h("div", { class: "composer-queue-actions" }, [
        h("button", {
          type: "button",
          class: "composer-queue-action composer-queue-send-now",
          title: _queueText("queue.send_now", "Append now and continue with this added context"),
          "aria-label": _queueText("queue.send_now", "Append now and continue with this added context"),
          onClick: () => callbacks?.onSendNow?.(first.id),
        }, [icon(["M5 4h14", "M12 20V8", "m7.5 12.5 4.5-4.5 4.5 4.5"])]),
        h("button", {
          type: "button",
          class: "composer-queue-action composer-queue-edit",
          title: _queueText("queue.edit", "Edit queued message"),
          "aria-label": _queueText("queue.edit", "Edit queued message"),
          onClick: () => callbacks?.onEdit?.(first.id),
        }, [icon(["M12 20h9", "M16.5 3.5a2.1 2.1 0 0 1 3 3L8 18l-4 1 1-4Z"])]),
        h("button", {
          type: "button",
          class: "composer-queue-action composer-queue-delete",
          title: _queueText("queue.delete", "Delete queued message"),
          "aria-label": _queueText("queue.delete", "Delete queued message"),
          onClick: () => callbacks?.onCancel?.(first.id),
        }, [icon(["M3 6h18", "M8 6V4h8v2", "M19 6l-1 14H6L5 6", "M10 11v5", "M14 11v5"])]),
      ]),
    ];
    render(h("div", {
      class: "composer-queue-bar",
      role: "status",
      "aria-live": "polite",
    }, children), composerQueueRoot);
    return true;
  }

  function _render() {
    render(h("div", { class: "chat-vue-list" }, messages.map(_messageNode)), root);
    messages.forEach(_renderToolsFor);
    messages.forEach(_renderJobsFor);
    messages.forEach(_renderChartsFor);
    messages.forEach(_renderCardsFor);
  }

  function _find(id) {
    return root.querySelector(`[data-vue-msg-id="${id}"]`);
  }

  function _messageIdFrom(target) {
    if (!target) return "";
    if (typeof target === "string") return target;
    const node = target.closest ? target.closest("[data-vue-msg-id]") : null;
    return node ? node.dataset.vueMsgId : "";
  }

  function _stateFor(target) {
    const id = _messageIdFrom(target);
    if (!id) return null;
    return messages.find(m => m.id === id) || null;
  }

  function _bubbleFor(target) {
    const id = _messageIdFrom(target);
    const el = id ? _find(id) : null;
    return el ? el.querySelector(".msg-bubble") : null;
  }

  function _bodyFor(target) {
    const id = _messageIdFrom(target);
    const el = id ? _find(id) : null;
    return el ? el.querySelector(".msg-body") : null;
  }

  function _chartListFor(target) {
    const id = _messageIdFrom(target);
    const el = id ? _find(id) : null;
    return el ? el.querySelector(".chart-list") : null;
  }

  function _cardListFor(target) {
    const id = _messageIdFrom(target);
    const el = id ? _find(id) : null;
    return el ? el.querySelector(".card-list") : null;
  }

  function _jobListFor(target) {
    const id = _messageIdFrom(target);
    const el = id ? _find(id) : null;
    return el ? el.querySelector(".job-list") : null;
  }

  function _removeTyping(typing) {
    if (typing && typing.parentNode) typing.remove();
  }

  function _bindImages(bubble) {
    bindBubbleImages(bubble);
  }

  function _stepsFor(target) {
    const id = _messageIdFrom(target);
    const el = id ? _find(id) : null;
    return el ? el.querySelector(".tool-steps") : null;
  }

  function _toolSummary(ev) {
    return String(ev.display || ev.detail || "").replace(/\s+/g, " ").trim();
  }

  function _toolDetail(ev) {
    return String(ev.detail || ev.display || "");
  }

  function _activityText(text) {
    if (text) return String(text);
    if (window.t) return t("tool.next_step") || "正在思考下一步…";
    return "正在思考下一步…";
  }

  function _renderToolStep(item) {
    const iconClass = item.compaction ? "compaction-spin" : "spin";
    const doneClass = item.compaction ? "done-compaction" : "done";
    const classes = ["tool-step"];
    if (item.compaction) classes.push("tool-step-compaction");
    if (!item.finished) classes.push("running");
    if (item.finished) classes.push(doneClass);
    const icon = item.finished ? (item.compaction ? "✦" : "✓") : "⟳";
    const attrs = {
      key: item.id,
      class: classes.join(" "),
      "data-tool": item.tool || "",
      "data-step-id": item.id,
    };
    if (item.finished) attrs["data-finished"] = "1";

    if (item.compaction) {
      const progress = item.progress === undefined || item.progress === null
        ? null
        : Math.max(0, Math.min(100, Number(item.progress) || 0));
      const hasProgress = progress !== null;
      const progressLabel = item.progressLabel || (hasProgress ? `压缩进度 ${progress}%` : "");
      const body = [
        h("span", { class: "tool-step-text" }, item.detail),
      ];
      if (hasProgress) {
        body.push(h("div", { class: "compaction-progress-row" }, [
          h("div", {
            class: "job-progress compaction-progress",
            role: "progressbar",
            "aria-label": progressLabel || "对话压缩进度",
            "aria-valuemin": "0",
            "aria-valuemax": "100",
            "aria-valuenow": String(progress),
          }, [h("span", { class: "job-progress-fill", style: { width: `${progress}%` } })]),
          h("span", { class: "job-progress-value compaction-progress-value" }, `${progress}%`),
        ]));
      }
      return h("div", attrs, [
        h("span", { class: iconClass }, icon),
        h("div", { class: "compaction-progress-body" }, body),
      ]);
    }

    return h("details", {
      ...attrs,
      open: item.open,
      onToggle: e => { item.open = e.currentTarget.open; },
    }, [
      h("summary", { class: "tool-step-head" }, [
        h("span", { class: iconClass }, icon),
        h("span", { class: "tool-step-text" }, item.summary),
      ]),
      h("div", { class: "tool-step-detail" }, item.detail),
    ]);
  }

  function _renderActivity(item) {
    return h("div", {
      key: item.id,
      class: "tool-step tool-step-activity running",
      "data-step-id": item.id,
    }, [
      h("span", { class: "spin" }, "⟳"),
      h("span", { class: "tool-step-text" }, item.text),
    ]);
  }

  function _renderRefItem(ref) {
    const headChildren = [
      h("span", { class: "knowledge-ref-type" }, ref.type || "来源"),
      h("span", { class: "knowledge-ref-title" }, ref.title || ref.source || "未命名来源"),
    ];
    if (ref.score !== "" && ref.score !== null && ref.score !== undefined) {
      headChildren.push(h("span", { class: "knowledge-ref-score" }, `score ${ref.score}`));
    }
    if (ref.rows !== null && ref.rows !== undefined) {
      headChildren.push(h("span", { class: "knowledge-ref-score" }, `${ref.rows} rows`));
    }
    const children = [
      h("div", { class: "knowledge-ref-head" }, headChildren),
    ];
    if (ref.source) children.push(h("div", { class: "knowledge-ref-source" }, ref.source));
    if (ref.snippet) children.push(h("div", { class: "knowledge-ref-snippet" }, ref.snippet));
    return h("div", { class: "knowledge-ref-item" }, children);
  }

  function _renderRefsPanel(item) {
    const refs = item.refs || [];
    const list = refs.length
      ? refs.map(_renderRefItem)
      : [h("div", { class: "knowledge-ref-empty" }, "本次知识库检索没有命中可引用条目。")];
    return h("details", {
      key: item.id,
      class: item.panelClass,
      "data-for-step": item.forStep || "",
      open: item.open,
      onToggle: e => { item.open = e.currentTarget.open; },
    }, [
      h("summary", null, item.title),
      h("div", { class: "knowledge-ref-list" }, list),
    ]);
  }

  function _renderAuditPanel(item) {
    const classes = ["tool-audit"];
    if (item.ok === false) classes.push("tool-audit-error");
    if (item.content) classes.push("tool-audit-has-summary");
    const attrs = {
      key: item.id,
      class: classes.join(" "),
      "data-tool": item.tool || "",
      "data-for-step": item.forStep || "",
      title: item.argsTitle || "",
    };
    const status = h(item.content ? "summary" : "span", { class: "tool-audit-status" }, item.status);
    const body = item.content
      ? h("div", { class: "tool-audit-summary" }, item.content)
      : null;
    if (item.content) {
      return h("details", {
        ...attrs,
        open: item.open,
        onToggle: e => { item.open = e.currentTarget.open; },
      }, [status, body]);
    }
    return h("div", attrs, [status]);
  }

  function _renderToolItem(item) {
    if (item.kind === ACTIVITY_KIND) return _renderActivity(item);
    if (item.kind === "step") return _renderToolStep(item);
    if (item.kind === "refs") return _renderRefsPanel(item);
    if (item.kind === "audit") return _renderAuditPanel(item);
    return null;
  }

  function _syncChartFrameHeight(iframe) {
    try {
      const doc = iframe.contentDocument;
      if (!doc?.body) return;

      const plotly = iframe.contentWindow?.Plotly;
      doc.querySelectorAll(".plotly-graph-div").forEach(plot => {
        if (plot.getBoundingClientRect().height < 240) {
          plot.style.minHeight = "360px";
        }
        if (plotly?.Plots?.resize && plot.classList.contains("js-plotly-plot")) {
          plotly.Plots.resize(plot);
        }
      });

      const contentHeight = Math.max(
        doc.body.scrollHeight,
        doc.documentElement?.scrollHeight || 0,
      );
      iframe.style.height = Math.max(420, contentHeight + 20) + "px";
    } catch (_) {}
  }

  function _mountChartIframe(iframe) {
    if (!iframe) return;
    if (chartObserver) {
      chartObserver.observe(iframe);
    } else if (!iframe.src) {
      iframe.src = iframe.dataset.src;
    }
  }

  function _renderChartFrame(item) {
    const children = [];
    if (item.title) {
      children.push(h("div", { class: "chart-frame-title" },
        item.chartType && item.title !== item.chartType
          ? `${item.title} · ${item.chartType}`
          : item.title
      ));
    }
    children.push(
      h("button", {
        class: "chart-expand-btn",
        type: "button",
        title: "在新标签页打开",
        onClick: () => window.open(`/api/chart/${item.chartId}`, "_blank"),
      }, "⛶"),
      h("iframe", {
        "data-src": `/api/chart/${item.chartId}`,
        sandbox: "allow-scripts allow-same-origin",
        referrerpolicy: "no-referrer",
        title: item.title || item.chartType || "分析图表",
        onVnodeMounted: vnode => _mountChartIframe(vnode.el),
        onLoad: e => {
          const iframe = e.currentTarget;
          requestAnimationFrame(() => _syncChartFrameHeight(iframe));
          setTimeout(() => _syncChartFrameHeight(iframe), 250);
        },
      }),
    );
    return h("div", {
      key: item.id,
      class: "chart-frame",
      "data-chart-id": item.chartId,
    }, children);
  }

  function _renderChartsFor(msg) {
    if (!msg || msg.kind !== "message") return;
    const list = _chartListFor(msg.id);
    if (!list) return;
    const charts = msg.charts || [];
    if (!charts.length) {
      render(null, list);
      return;
    }
    render(h(Fragment, null, charts.map(_renderChartFrame)), list);
  }

  function _jobText(key, fallback) {
    if (!window.t) return fallback;
    const value = t(key);
    return value && value !== key ? value : fallback;
  }

  function _artifactNode(artifact, index) {
    const typeName = {
      chart: "分析图表", file: "生成文件", export: "导出文件",
      tool_result: "完整工具结果", schema: "数据结构", report: "分析报告",
      tool_result_summary: "工具结果",
      ppt: "演示文稿", dashboard: "仪表盘", checkpoint: "工作目录检查点",
    }[String(artifact.type || "").toLowerCase()] || "任务结果";
    const name = artifact.filename || artifact.name || artifact.label || `${typeName} ${index + 1}`;
    const href = artifact.url || artifact.download_url || "";
    const attrs = { class: "job-artifact", key: `${name}-${index}` };
    if (href) {
      return h("a", { ...attrs, href, target: "_blank", rel: "noopener noreferrer" }, `↗ ${name}`);
    }
    return h("span", attrs, `✓ ${name}`);
  }

  function _renderJobCard(job) {
    const terminal = ["succeeded", "failed", "canceled"].includes(job.status);
    const canCancel = !terminal && job.status !== "canceling" && job.jobType !== "filehistory_rewind";
    const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
    const statusText = _jobText(`job.status.${job.status}`, job.status || "created");
    const children = [
      h("div", { class: "job-card-head" }, [
        h("div", { class: "job-card-title" }, [
          h("span", { class: "job-card-icon", "aria-hidden": "true" }, terminal ? (job.status === "succeeded" ? "✓" : "!") : "⟳"),
          h("span", null, job.label || job.jobType || _jobText("job.default_label", "Background job")),
        ]),
        h("span", { class: `job-status job-status-${job.status}` }, statusText),
      ]),
      h("div", {
        class: "job-progress",
        role: "progressbar",
        "aria-label": job.label || job.jobType || "Job progress",
        "aria-valuemin": "0",
        "aria-valuemax": "100",
        "aria-valuenow": String(progress),
      }, [h("span", { class: "job-progress-fill", style: { width: `${progress}%` } })]),
      h("div", { class: "job-card-meta" }, [
        h("span", { class: "job-progress-value" }, `${progress}%`),
        job.message ? h("span", { class: "job-message" }, job.message) : null,
      ]),
    ];
    if (job.artifacts.length) {
      children.push(h("div", { class: "job-artifacts" }, job.artifacts.map(_artifactNode)));
    }
    if (job.error) children.push(h("div", { class: "job-error", role: "alert" }, job.error));
    if (canCancel) {
      children.push(h("button", {
        type: "button",
        class: "job-cancel-btn",
        disabled: job.cancelPending,
        onClick: async () => {
          if (job.cancelPending || !job.callbacks?.onCancel) return;
          job.previousStatus = job.status;
          job.cancelPending = true;
          job.status = "canceling";
          _renderJobsFor(job._msg);
          try {
            await job.callbacks.onCancel(job.jobId);
          } catch (error) {
            job.cancelPending = false;
            job.status = job.previousStatus || "running";
            job.error = error?.message || _jobText("job.cancel_failed", "Could not cancel job");
            _renderJobsFor(job._msg);
          }
        },
      }, _jobText("job.cancel", "Cancel")));
    }
    return h("section", {
      key: job.id,
      class: `job-card job-card-${job.status}`,
      "data-job-id": job.jobId,
    }, children);
  }

  function _renderJobsFor(msg) {
    if (!msg || msg.kind !== "message") return;
    const list = _jobListFor(msg.id);
    if (!list) return;
    const jobs = msg.jobs || [];
    render(jobs.length ? h(Fragment, null, jobs.map(_renderJobCard)) : null, list);
  }

  function updateJob(target, ev, callbacks) {
    const msg = _stateFor(target);
    if (!msg || !ev || !ev.job_id) return false;
    msg.jobs = msg.jobs || [];
    let job = msg.jobs.find(item => item.jobId === ev.job_id);
    if (!job) {
      job = {
        id: `job-${++jobSeq}`,
        jobId: ev.job_id,
        jobType: ev.job_type || "",
        label: ev.label || "",
        status: ev.status || "created",
        progress: 0,
        message: "",
        artifacts: [],
        result: null,
        error: "",
        cancelPending: false,
        callbacks: callbacks || {},
        _msg: msg,
      };
      msg.jobs.push(job);
    }
    job.previousStatus = job.status;
    if (callbacks) job.callbacks = callbacks;
    if (ev.job_type) job.jobType = ev.job_type;
    if (ev.label) job.label = ev.label;
    if (ev.status) job.status = ev.status;
    if (ev.progress !== undefined) job.progress = ev.progress;
    if (ev.message !== undefined) job.message = ev.message || "";
    if (ev.type === "artifact_created" && ev.artifact) job.artifacts.push(ev.artifact);
    if (ev.type === "job_done") {
      job.status = ev.status || "succeeded";
      job.progress = 100;
      job.result = ev.result;
      job.cancelPending = false;
    }
    if (ev.type === "job_error") {
      job.status = ev.status || "failed";
      job.error = ev.error || "Job failed";
      job.cancelPending = false;
    }
    if (ev.type === "job_canceled") {
      job.status = ev.status || "canceled";
      job.cancelPending = false;
    }
    _renderJobsFor(msg);
    return true;
  }

  function _renderOutlineCard(item) {
    const children = [
      h("div", { class: "ppt-outline-header" }, [
        h("span", { class: "ppt-outline-icon" }, item.icon),
        h("span", null, item.headerTitle),
      ]),
      h("div", {
        class: "ppt-outline-content",
        innerHTML: renderMd(item.markdown || ""),
      }),
    ];

    if (item.editOpen) {
      children.push(h("div", { class: "ppt-outline-edit-wrap" }, [
        h("div", { class: "ppt-outline-edit-hint" }, "请说明希望如何修改："),
        h("textarea", {
          class: "ppt-outline-edit",
          rows: 3,
          placeholder: "例如：把第3张换成双栏文字，增加一张市场份额环形图…",
          value: item.editText,
          disabled: item.locked,
          onInput: e => { item.editText = e.currentTarget.value; },
          onKeydown: e => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              const txt = (e.currentTarget.value || "").trim();
              if (!txt || item.locked) return;
              item.locked = true;
              _renderCardsFor(item._msg);
              if (item.callbacks && item.callbacks.onRevise) item.callbacks.onRevise(txt);
            }
          },
        }),
      ]));
    }

    if (item.cancelled) {
      children.push(h("div", { class: "ppt-cancelled-note" }, "已取消。"));
    } else {
      children.push(h("div", { class: "ppt-outline-btns" }, [
        h("button", {
          class: "ppt-btn ppt-btn-confirm",
          type: "button",
          disabled: item.locked,
          onClick: () => {
            if (item.locked) return;
            item.locked = true;
            _renderCardsFor(item._msg);
            if (item.callbacks && item.callbacks.onConfirm) item.callbacks.onConfirm();
          },
        }, "✅ 确认生成"),
        h("button", {
          class: "ppt-btn ppt-btn-revise",
          type: "button",
          disabled: item.locked,
          onClick: () => {
            if (item.locked) return;
            item.editOpen = !item.editOpen;
            _renderCardsFor(item._msg);
          },
        }, "✏️ 修改大纲"),
        h("button", {
          class: "ppt-btn ppt-btn-cancel",
          type: "button",
          disabled: item.locked,
          onClick: () => {
            if (item.locked) return;
            item.locked = true;
            item.cancelled = true;
            _renderCardsFor(item._msg);
            if (item.callbacks && item.callbacks.onCancel) item.callbacks.onCancel();
          },
        }, "✕ 取消"),
      ]));
    }

    return h("div", { key: item.id, class: "ppt-outline-card" }, children);
  }

  function _renderAskUserCard(item) {
    const options = item.options || [];
    const allOpts = options.concat(["__other__"]);

    const chips = allOpts.map(opt => {
      const isOther = opt === "__other__";
      const selected = item.selected.includes(opt);
      const classes = ["ask-user-chip"];
      if (selected) classes.push("selected");
      return h("button", {
        key: opt,
        class: classes.join(" "),
        type: "button",
        disabled: item.locked,
        onClick: () => {
          if (item.locked) return;
          if (isOther) {
            item.otherOpen = !item.otherOpen;
            _renderCardsFor(item._msg);
            return;
          }
          if (item.multiSelect) {
            const idx = item.selected.indexOf(opt);
            if (idx >= 0) item.selected.splice(idx, 1);
            else item.selected.push(opt);
            _renderCardsFor(item._msg);
          } else {
            item.locked = true;
            _renderCardsFor(item._msg);
            if (item.callbacks && item.callbacks.onSubmit) item.callbacks.onSubmit(opt);
          }
        },
      }, isOther ? (window.t ? (t("ask_user.other") || "其他…") : "其他…") : opt);
    });

    const children = [
      h("div", { class: "ask-user-question" }, item.question || ""),
      h("div", { class: "ask-user-chips" }, chips),
    ];

    if (item.otherOpen) {
      children.push(h("div", { class: "ask-user-other-wrap" }, [
        h("input", {
          type: "text",
          class: "ask-user-other-input",
          placeholder: window.t ? (t("ask_user.other_placeholder") || "请输入您的回答…") : "请输入您的回答…",
          value: item.otherText,
          disabled: item.locked,
          onInput: e => { item.otherText = e.currentTarget.value; },
          onKeydown: e => {
            if (e.key === "Enter") {
              e.preventDefault();
              const val = (e.currentTarget.value || "").trim();
              if (!val || item.locked) return;
              item.locked = true;
              _renderCardsFor(item._msg);
              if (item.callbacks && item.callbacks.onSubmit) item.callbacks.onSubmit(val);
            }
          },
        }),
        h("button", {
          type: "button",
          class: "ask-user-other-btn",
          disabled: item.locked,
          onClick: () => {
            const val = (item.otherText || "").trim();
            if (!val || item.locked) return;
            item.locked = true;
            _renderCardsFor(item._msg);
            if (item.callbacks && item.callbacks.onSubmit) item.callbacks.onSubmit(val);
          },
        }, window.t ? (t("ask_user.submit") || "提交") : "提交"),
      ]));
    }

    if (item.multiSelect) {
      children.push(h("button", {
        type: "button",
        class: "ask-user-submit-btn",
        disabled: item.locked,
        onClick: () => {
          if (item.locked) return;
          const vals = [...item.selected];
          if (item.otherOpen && item.otherText) vals.push(item.otherText.trim());
          if (!vals.length) return;
          item.locked = true;
          _renderCardsFor(item._msg);
          if (item.callbacks && item.callbacks.onSubmit) item.callbacks.onSubmit(vals.join("、"));
        },
      }, window.t ? (t("ask_user.confirm") || "确认选择") : "确认选择"));
    }

    return h("div", { key: item.id, class: "ask-user-card" }, children);
  }

  function _renderCard(item) {
    if (item.kind === "outline") return _renderOutlineCard(item);
    if (item.kind === "ask_user") return _renderAskUserCard(item);
    return null;
  }

  function _renderCardsFor(msg) {
    if (!msg || msg.kind !== "message") return;
    const list = _cardListFor(msg.id);
    if (!list) return;
    const cards = msg.cards || [];
    if (!cards.length) {
      render(null, list);
      return;
    }
    render(h(Fragment, null, cards.map(_renderCard)), list);
  }

  function _renderToolsFor(msg) {
    if (!msg || msg.kind !== "message") return;
    const steps = _stepsFor(msg.id);
    if (!steps) return;
    const items = msg.tools || [];
    if (!items.length) {
      render(null, steps);
      return;
    }
    render(h(Fragment, null, items.map(_renderToolItem)), steps);
  }

  function _latestStep(msg, toolName) {
    if (!msg || !Array.isArray(msg.tools)) return null;
    for (let i = msg.tools.length - 1; i >= 0; i--) {
      const item = msg.tools[i];
      if (item.kind === "step" && item.tool === toolName) return item;
    }
    return null;
  }

  function _upsertPanel(msg, step, panel) {
    if (!msg || !step) return false;
    msg.tools = msg.tools || [];
    const idx = msg.tools.findIndex(item =>
      item.kind === panel.kind &&
      item.panelClass === panel.panelClass &&
      item.forStep === step.id
    );
    if (idx >= 0) msg.tools.splice(idx, 1);
    const stepIdx = msg.tools.findIndex(item => item.id === step.id);
    panel.forStep = step.id;
    msg.tools.splice(stepIdx + 1, 0, panel);
    _renderToolsFor(msg);
    return true;
  }

  function appendMsg(role, text, options = {}) {
    const id = `m${++seq}`;
    messages.push({
      id,
      kind: "message",
      role,
      variant: options.variant || "",
      text: text || "",
      reasoning: [],
      tools: [],
      charts: [],
      cards: [],
      jobs: [],
      queueStatus: "",
      queuePosition: 0,
      queueCallbacks: null,
      error: "",
      stopped: false,
    });
    _render();
    const el = _find(id);
    const bubble = el && el.querySelector(".msg-bubble");
    if (bubble && text !== null) {
      bubble.innerHTML = renderMd(text);
      _bindImages(bubble);
    }
    return el;
  }

  function sysMsg(text) {
    const id = `m${++seq}`;
    messages.push({ id, kind: "system", text: text || "" });
    _render();
    return _find(id);
  }

  function clear() {
    messages.forEach(msg => {
      (msg.tools || []).forEach(item => {
        if (item.finishTimer) clearTimeout(item.finishTimer);
      });
    });
    messages.length = 0;
    _render();
  }

  function countMessages() {
    return messages.filter(m => m.kind === "message").length;
  }

  function setTurnQueueState(target, status, position, callbacks) {
    const msg = _stateFor(target);
    if (!msg || msg.kind !== "message") return false;
    msg.queueStatus = status || "";
    msg.queuePosition = Number(position) || 0;
    msg.queueCallbacks = callbacks || null;
    _render();
    return true;
  }

  function setMessageText(target, text) {
    const msg = _stateFor(target);
    if (!msg || msg.kind !== "message") return false;
    msg.text = String(text || "");
    _render();
    const bubble = _bubbleFor(msg.id);
    if (bubble) {
      bubble.innerHTML = renderMd(msg.text);
      _bindImages(bubble);
    }
    return true;
  }

  function removeMessages(targets) {
    const ids = new Set((Array.isArray(targets) ? targets : [targets]).map(_messageIdFrom).filter(Boolean));
    if (!ids.size) return false;
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (ids.has(messages[index].id)) messages.splice(index, 1);
    }
    _render();
    return true;
  }

  function appendTextDelta(target, content, typing) {
    const msg = _stateFor(target);
    const bubble = _bubbleFor(target);
    if (!msg || !bubble) return false;
    hideToolActivity(target);
    _removeTyping(typing);
    const chunk = String(content || "");
    msg.text += chunk;
    bubble.insertAdjacentText("beforeend", chunk);
    return true;
  }

  function setMarkdown(target, markdownText, typing) {
    const msg = _stateFor(target);
    const bubble = _bubbleFor(target);
    if (!msg || !bubble) return false;
    hideToolActivity(target);
    _removeTyping(typing);
    msg.text = String(markdownText || "");
    msg.error = "";
    bubble.innerHTML = renderMd(msg.text);
    _bindImages(bubble);
    return true;
  }

  function setError(target, message, typing) {
    const msg = _stateFor(target);
    const bubble = _bubbleFor(target);
    if (!msg || !bubble) return false;
    hideToolActivity(target);
    _removeTyping(typing);
    msg.error = String(message || "");
    bubble.innerHTML = "";
    const span = document.createElement("span");
    span.className = "stream-error";
    span.textContent = `⚠ ${msg.error}`;
    bubble.appendChild(span);
    const retryBtn = document.createElement("button");
    retryBtn.className = "stream-retry-btn";
    retryBtn.textContent = "↺ 重试";
    retryBtn.addEventListener("click", () => window.BAA.chatStream?.retryLast?.());
    bubble.appendChild(retryBtn);
    return true;
  }

  function addReasoning(target, content, typing) {
    const msg = _stateFor(target);
    const bubble = _bubbleFor(target);
    if (!msg || !bubble) return false;
    _removeTyping(typing);
    const text = String(content || "");
    msg.reasoning.push(text);

    const block = document.createElement("div");
    block.className = "reasoning-block";
    const toggle = document.createElement("div");
    toggle.className = "reasoning-toggle";
    toggle.innerHTML = `<span class="reasoning-arrow">▶</span> ${window.t ? t('reasoning_toggle') : "Reasoning"}`;
    const body = document.createElement("div");
    body.className = "reasoning-body";
    body.textContent = text;
    toggle.addEventListener("click", () => {
      toggle.classList.toggle("open");
      body.classList.toggle("open");
    });
    block.appendChild(toggle);
    block.appendChild(body);
    bubble.before(block);
    // Reasoning has finished rendering, but the backend may still be deciding
    // whether to call another tool. Keep a visible hand-off state until the
    // next tool_start or final output replaces it.
    showToolActivity(target);
    return true;
  }

  function markStopped(target, noteText, typing) {
    const msg = _stateFor(target);
    const bubble = _bubbleFor(target);
    const body = _bodyFor(target);
    if (!msg || !bubble || !body) return false;
    hideToolActivity(target);
    _removeTyping(typing);
    msg.stopped = true;
    const stopNote = document.createElement("div");
    stopNote.className = "stop-note";
    stopNote.textContent = noteText || "";
    bubble.before(stopNote);
    if (!bubble.textContent.trim()) bubble.remove();
    return true;
  }

  function finishFinishedTools(target) {
    const msg = _stateFor(target);
    if (!msg || !Array.isArray(msg.tools)) return false;
    let changed = false;
    msg.tools.forEach(item => {
      if (item.kind === "step" && item.markedFinished && !item.finished && !item.finishTimer) {
        if (_finishToolItem(msg, item)) changed = true;
      }
    });
    if (changed) _renderToolsFor(msg);
    return true;
  }

  function showToolActivity(target, text, options = {}) {
    const msg = _stateFor(target);
    if (!msg) return false;
    msg.tools = msg.tools || [];
    const hasActiveStep = msg.tools.some(item =>
      item.kind === "step" && !item.finished && !item.markedFinished
    );
    if (hasActiveStep && !options.force) return true;
    const existing = msg.tools.find(item => item.kind === ACTIVITY_KIND);
    if (existing) {
      if (existing.hideTimer) {
        clearTimeout(existing.hideTimer);
        existing.hideTimer = null;
      }
      existing.hiding = false;
      existing.text = _activityText(text);
      msg.tools = msg.tools.filter(item => item.kind !== ACTIVITY_KIND);
      msg.tools.push(existing);
    } else {
      msg.tools.push({
        id: `activity-${++toolSeq}`,
        kind: ACTIVITY_KIND,
        text: _activityText(text),
        startedAt: Date.now(),
        hiding: false,
        hideTimer: null,
      });
    }
    _renderToolsFor(msg);
    return true;
  }

  function _removeActivityItem(msg, activityId) {
    if (!msg || !Array.isArray(msg.tools)) return false;
    const before = msg.tools.length;
    msg.tools = msg.tools.filter(item => !(item.kind === ACTIVITY_KIND && item.id === activityId));
    if (msg.tools.length !== before) _renderToolsFor(msg);
    return msg.tools.length !== before;
  }

  function _scheduleActivityHide(msg, item, delayMs) {
    if (!msg || !item || item.kind !== ACTIVITY_KIND) return false;
    if (item.hideTimer) clearTimeout(item.hideTimer);
    item.hiding = true;
    item.hideTimer = setTimeout(() => {
      item.hideTimer = null;
      _removeActivityItem(msg, item.id);
    }, delayMs);
    return true;
  }

  function hideToolActivity(target, options = {}) {
    const msg = _stateFor(target);
    if (!msg || !Array.isArray(msg.tools)) return false;
    const activities = msg.tools.filter(item => item.kind === ACTIVITY_KIND);
    if (!activities.length) return true;
    const delayMs = Number.isFinite(options.delayMs)
      ? Math.max(0, Number(options.delayMs))
      : ACTIVITY_HIDE_DELAY_MS;
    if (delayMs <= 0) {
      activities.forEach(item => {
        if (item.hideTimer) clearTimeout(item.hideTimer);
      });
      const before = msg.tools.length;
      msg.tools = msg.tools.filter(item => item.kind !== ACTIVITY_KIND);
      if (msg.tools.length !== before) _renderToolsFor(msg);
      return true;
    }
    activities.forEach(item => _scheduleActivityHide(msg, item, delayMs));
    return true;
  }

  function finishAllTools(target) {
    const msg = _stateFor(target);
    if (!msg || !Array.isArray(msg.tools)) return false;
    let changed = false;
    msg.tools.forEach(item => {
      if (item.kind === "step" && !item.finished) {
        item.markedFinished = true;
        if (_finishToolItem(msg, item)) changed = true;
      }
    });
    if (changed) _renderToolsFor(msg);
    return true;
  }

  function _finishToolItem(msg, item) {
    if (!item || item.finished) return false;
    const elapsed = Date.now() - (item.startedAt || Date.now());
    const remaining = MIN_TOOL_VISIBLE_MS - elapsed;
    if (remaining > 0) {
      item.markedFinished = true;
      if (!item.finishTimer) {
        item.finishTimer = setTimeout(() => {
          item.finishTimer = null;
          item.finished = true;
          item.markedFinished = true;
          _renderToolsFor(msg);
        }, remaining);
      }
      return false;
    }
    if (item.finishTimer) {
      clearTimeout(item.finishTimer);
      item.finishTimer = null;
    }
    item.finished = true;
    item.markedFinished = true;
    return true;
  }

  function startTool(target, ev) {
    const msg = _stateFor(target);
    if (!msg) return false;
    msg.tools = msg.tools || [];
    hideToolActivity(target, { delayMs: 0 });
    msg.tools.forEach(item => {
      if (item.kind === "step" && item.markedFinished && !item.finished && !item.finishTimer) {
        _finishToolItem(msg, item);
      }
    });
    const tool = ev.tool || "";
    msg.tools.push({
      id: `tool-${++toolSeq}`,
      kind: "step",
      tool,
      compaction: tool === "compaction",
      summary: _toolSummary(ev),
      detail: _toolDetail(ev),
      progress: ev.progress === undefined ? null : Math.max(0, Math.min(100, Number(ev.progress) || 0)),
      progressLabel: ev.progress_label || ev.progressLabel || "",
      startedAt: Date.now(),
      open: false,
      markedFinished: false,
      finished: false,
    });
    _renderToolsFor(msg);
    return true;
  }

  function updateToolProgress(target, ev) {
    const msg = _stateFor(target);
    if (!msg || !Array.isArray(msg.tools)) return false;
    const tool = ev.tool || "";
    const step = (tool ? _latestStep(msg, tool) : null)
      || msg.tools.findLast?.(item => item.kind === "step" && !item.finished)
      || [...msg.tools].reverse().find(item => item.kind === "step" && !item.finished);
    if (!step) return false;
    if (ev.detail || ev.display) {
      step.detail = _toolDetail(ev);
      step.summary = _toolSummary(ev) || step.summary;
    }
    if (ev.progress !== undefined) {
      step.progress = Math.max(0, Math.min(100, Number(ev.progress) || 0));
    }
    if (ev.progress_label || ev.progressLabel) {
      step.progressLabel = ev.progress_label || ev.progressLabel;
    }
    _renderToolsFor(msg);
    return true;
  }

  function endTool(target) {
    const msg = _stateFor(target);
    if (!msg || !Array.isArray(msg.tools)) return false;
    const tool = arguments.length > 1 && arguments[1] ? arguments[1].tool : "";
    const step = msg.tools.find(item =>
      item.kind === "step" &&
      !item.finished &&
      !item.markedFinished &&
      (!tool || item.tool === tool)
    ) || msg.tools.find(item => item.kind === "step" && !item.finished && !item.markedFinished);
    if (!step) return true;
    step.markedFinished = true;
    if (_finishToolItem(msg, step)) _renderToolsFor(msg);
    return true;
  }

  function setKnowledgeRefs(target, ev) {
    const msg = _stateFor(target);
    const step = _latestStep(msg, "query_knowledge");
    if (!step) return false;
    const refs = Array.isArray(ev.refs) ? ev.refs : [];
    return _upsertPanel(msg, step, {
      id: `panel-${++toolSeq}`,
      kind: "refs",
      panelClass: "knowledge-refs",
      refs,
      title: refs.length ? `引用来源（${refs.length} 条）` : "引用来源（未命中）",
      open: false,
    });
  }

  function setDataRefs(target, ev) {
    const msg = _stateFor(target);
    const refs = Array.isArray(ev.refs) ? ev.refs : [];
    if (!refs.length) return true;
    const step = _latestStep(msg, "query_data")
      || _latestStep(msg, "create_analysis_table")
      || _latestStep(msg, "run_analysis")
      || _latestStep(msg, "generate_chart");
    if (!step) return false;
    return _upsertPanel(msg, step, {
      id: `panel-${++toolSeq}`,
      kind: "refs",
      panelClass: "data-refs",
      refs,
      title: `数据依据（${refs.length} 条）`,
      open: false,
    });
  }

  function setToolAudit(target, ev) {
    const msg = _stateFor(target);
    const tool = ev.tool || "";
    if (!tool) return false;
    const step = _latestStep(msg, tool);
    if (!step) return false;
    if (!step.finished) {
      step.markedFinished = true;
      _finishToolItem(msg, step);
    }
    const elapsed = ev.elapsed_seconds !== undefined ? `${ev.elapsed_seconds}s` : "";
    const sourceCount = Array.isArray(ev.sources) ? ev.sources.length : 0;
    const artifactCount = Array.isArray(ev.artifacts) ? ev.artifacts.length : 0;
    const bits = [
      ev.parallel ? "并行" : "",
      elapsed && `耗时 ${elapsed}`,
      sourceCount ? `来源 ${sourceCount}` : "",
      artifactCount ? `产物 ${artifactCount}` : "",
      ev.error ? `错误 ${ev.error}` : "",
    ].filter(Boolean);
    let argsTitle = "";
    if (ev.args_preview) {
      try { argsTitle = JSON.stringify(ev.args_preview, null, 2); } catch (_) {}
    }
    return _upsertPanel(msg, step, {
      id: `panel-${++toolSeq}`,
      kind: "audit",
      panelClass: "tool-audit",
      tool,
      ok: ev.ok,
      status: bits.length ? bits.join(" · ") : "工具执行完成",
      content: ev.content ?? ev.data ?? ev.summary ?? "",
      argsTitle,
      open: false,
    });
  }

  function addChartRef(target, chartId, title = "", chartType = "") {
    const msg = _stateFor(target);
    if (!msg || !chartId) return false;
    hideToolActivity(target);
    msg.charts = msg.charts || [];
    if (msg.charts.some(item => item.chartId === chartId)) return true;
    msg.charts.push({
      id: `chart-${++chartSeq}`,
      chartId,
      title,
      chartType,
    });
    _renderChartsFor(msg);
    return true;
  }

  function addOutlineCard(target, data, callbacks) {
    const msg = _stateFor(target);
    if (!msg || !data) return false;
    hideToolActivity(target);
    msg.cards = msg.cards || [];
    const card = {
      id: `card-${++cardSeq}`,
      kind: "outline",
      icon: data.icon || "📄",
      headerTitle: data.headerTitle || "",
      markdown: data.markdown || "",
      editOpen: false,
      editText: "",
      locked: false,
      cancelled: false,
      callbacks: callbacks || {},
      _msg: msg,
    };
    msg.cards.push(card);
    _renderCardsFor(msg);
    return true;
  }

  function addAskUserCard(target, ev, callbacks) {
    const msg = _stateFor(target);
    if (!msg || !ev) return false;
    hideToolActivity(target);
    msg.cards = msg.cards || [];
    const card = {
      id: `card-${++cardSeq}`,
      kind: "ask_user",
      question: ev.question || "",
      options: (Array.isArray(ev.options) ? ev.options : [])
        .map(option => {
          if (typeof option === "string") return option.trim();
          if (!option || typeof option !== "object") return "";
          for (const key of ["label", "text", "title", "name", "value"]) {
            if (typeof option[key] === "string" && option[key].trim()) {
              return option[key].trim();
            }
          }
          return "";
        })
        .filter((option, index, all) => option && all.indexOf(option) === index)
        .slice(0, 6),
      multiSelect: !!ev.multi_select,
      selected: [],
      otherOpen: false,
      otherText: "",
      locked: false,
      callbacks: callbacks || {},
      _msg: msg,
    };
    msg.cards.push(card);
    _renderCardsFor(msg);
    return true;
  }

  registerUiIsland("chat", {
    appendMsg,
    sysMsg,
    clear,
    countMessages,
    setTurnQueueState,
    renderComposerQueue,
    setMessageText,
    removeMessages,
    appendTextDelta,
    setMarkdown,
    setError,
    addReasoning,
    markStopped,
    finishFinishedTools,
    finishAllTools,
    showToolActivity,
    hideToolActivity,
    startTool,
    updateToolProgress,
    endTool,
    setKnowledgeRefs,
    setDataRefs,
    setToolAudit,
    addChartRef,
    updateJob,
    addOutlineCard,
    addAskUserCard,
  });
}
