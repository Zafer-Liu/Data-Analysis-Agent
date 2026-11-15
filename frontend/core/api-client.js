const JSON_CONTENT_TYPE = "application/json";
const READ_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);
const COPY = {
  loading: ["加载中…", "Loading…"],
  retry: ["重试", "Retry"],
  cancel: ["取消", "Cancel"],
  cancelled: ["已取消加载。可重新加载。", "Loading cancelled. You can reload."],
  failed: ["请求失败", "Request failed"],
  network: ["无法连接服务器，请检查网络。", "Cannot reach the server. Check your connection."],
  timeout: ["请求超时。", "The request timed out."],
  auth: ["登录已过期，请重新登录。", "Your session expired. Sign in again."],
  invalid: ["服务器返回了无法读取的响应。", "The server returned an unreadable response."],
  unknown: [
    "服务器可能已完成操作。请先刷新核对，再决定是否重新提交。",
    "The operation may have completed. Refresh to check before submitting again.",
  ],
  busy: ["该操作正在处理，请稍候。", "This operation is already in progress."],
  empty: ["暂无记录", "No records yet"],
  loadFailed: ["加载失败：{message}", "Could not load: {message}"],
  localSave: [
    "服务器操作已完成，但本地副本保存失败：{message}",
    "Saved on the server, but the local copy could not be saved: {message}",
  ],
  retryLocal: ["重试保存副本", "Retry local save"],
  refresh: ["刷新", "Refresh"],
  saving: ["保存中…", "Saving…"],
  saved: ["已保存", "Saved"],
  save: ["保存", "Save"],
  toggleLabel: ["启用知识记录", "Enable knowledge record"],
  deleted: ["已删除", "Deleted"],
  updated: ["已更新", "Updated"],
  close: ["关闭", "Close"],
  enterName: ["请输入业务名称", "Enter a business category name"],
  metricRequired: ["指标名称不能为空", "Enter a metric name"],
  ruleRequired: ["规则 ID 不能为空", "Enter a rule ID"],
  topicRequired: ["主题不能为空", "Enter a topic"],
  recordMissing: ["记录已不存在，请刷新列表。", "This record no longer exists. Refresh the list."],
  noImport: ["没有可入库的记录或源文件", "There are no records or source files to import"],
  restoreFailed: [
    "业务知识库本地恢复失败：{message}",
    "Could not restore local knowledge: {message}",
  ],
  invalidFile: [
    "不支持的格式，请上传 .xlsx / .xls / .docx / .yaml / .yml",
    "Upload an .xlsx, .xls, .docx, .yaml or .yml file",
  ],
  fileTooLarge: ["本体文件不能超过 256 KB", "Ontology files must be 256 KB or smaller"],
  importReady: ["解析完成，请确认后入库。", "Parsing complete. Review before importing."],
  importSaved: [
    "入库成功：指标 {metrics} 条，规则 {rules} 条，背景知识 {notes} 条，RAG 分块 {chunks} 条",
    "Imported: {metrics} metrics, {rules} rules, {notes} notes and {chunks} RAG chunks",
  ],
  optionsLoading: ["正在加载导入选项…", "Loading import options…"],
  optionsPending: ["导入选项尚未加载完成，请稍候", "Wait for the import options to finish loading"],
  questionRequired: ["请输入分析问题", "Enter an analysis question"],
  unbound: [
    "当前没有匹配的活动数据源，请先连接并绑定。",
    "Connect and bind a matching active data source first.",
  ],
  disabled: ["本体已停用。", "This ontology is disabled."],
  noMatches: [
    "未找到相关定义，或所在业务分类已停用。",
    "No related definitions were found, or the business category is disabled.",
  ],
  reviewBlocked: ["质量复核待修正", "Quality review requires changes"],
  cancelPending: [
    "已请求终止；执行器退出前仍占用资源。",
    "Cancellation requested. Resources remain in use until the executor exits.",
  ],
  runCancelled: ["Workflow Run 已取消", "Workflow run cancelled"],
  teamsTitle: ["团队", "Teams"],
  teamsSubtitle: [
    "查看成员协作、Workflow 运行状态和材料交接。",
    "Review team collaboration, workflow progress and evidence handoffs.",
  ],
  teamsViews: ["团队协作视图", "Team collaboration views"],
  teamMembers: ["团队成员", "Team members"],
  teamsEmpty: [
    "还没有团队。可以让 Agent 创建一个 team 来拆分分析任务。",
    "No teams yet. Ask the Agent to create a team for your analysis.",
  ],
  chooseTeam: ["选择一个团队查看状态。", "Select a team to view its status."],
  metricsEmpty: ["暂无指标定义", "No metric definitions yet"],
  rulesEmpty: ["暂无规则", "No rules yet"],
  notesEmpty: ["暂无背景知识", "No background notes yet"],
  workflowsEmpty: [
    "还没有 Workflow。创建或保存动态协作路径后会显示在这里。",
    "No workflows yet. Create one or save a team plan to get started.",
  ],
  runsEmpty: ["暂无 Workflow Run。", "No workflow runs yet."],
  parsing: ["正在解析文件，请稍候…", "Parsing the file…"],
  cancelledImport: ["已停止等待解析结果。", "Stopped waiting for the parsed result."],
};

// Uses the incumbent language setting without introducing another store.
export function requestMessage(key, vars = {}) {
  const translationKey = `request.${key}`;
  const translated = globalThis.t?.(translationKey, vars);
  const lang = globalThis.getLang?.() || globalThis.document?.documentElement?.lang || "zh";
  const fallback = COPY[key]?.[String(lang).startsWith("en") ? 1 : 0] || key;
  const text = translated && translated !== translationKey ? translated : fallback;
  return text.replace(/\{(\w+)\}/g, (match, name) => String(vars[name] ?? match));
}

export class ApiError extends Error {
  constructor(
    message,
    {
      status = 0,
      statusText = "",
      url = "",
      payload = null,
      code = "REQUEST_FAILED",
      requestId = "",
      kind = "http",
      method = "GET",
      cause,
    } = {},
  ) {
    super(message, cause ? { cause } : undefined);
    this.name = "ApiError";
    Object.assign(this, { status, statusText, url, payload, code, requestId, kind, method });
    this.retryable =
      READ_METHODS.has(method) &&
      (kind === "network" || kind === "timeout" || status === 429 || status >= 500);
    this.outcomeUnknown =
      !READ_METHODS.has(method) && ["network", "timeout", "abort", "parse"].includes(kind);
  }
}

export function isSilentRequestError(error) {
  return error?.code === "REQUEST_ABORTED" || error?.code === "STALE_REQUEST";
}

export function requestErrorMessage(error) {
  const message = String(error?.message || requestMessage("failed"));
  return error?.outcomeUnknown ? `${message} ${requestMessage("unknown")}` : message;
}

function isJsonBody(body) {
  if (body == null || typeof body !== "object") return false;
  return (
    Object.getPrototypeOf(body) === Object.prototype ||
    Array.isArray(body) ||
    Object.getPrototypeOf(body) === null
  );
}

function payloadMessage(payload) {
  if (typeof payload === "string") return payload.trim();
  if (!payload || typeof payload !== "object") return "";
  return (
    [payload.error?.message, payload.error, payload.message, payload.detail].find(
      (value) => typeof value === "string" && value.trim(),
    ) || ""
  );
}

function metadata(response, payload) {
  return {
    code: String(payload?.error?.code || payload?.error_code || payload?.code || ""),
    requestId: String(
      response?.headers?.get("x-request-id") ||
        payload?.request_id ||
        payload?.requestId ||
        payload?.meta?.request_id ||
        "",
    ),
  };
}

async function parseResponse(response, responseType) {
  if (responseType === "response") return response;
  if (response.status === 204 || response.status === 205) return null;
  if (responseType === "blob") return response.blob();
  if (responseType === "arrayBuffer") return response.arrayBuffer();
  if (responseType === "text") return response.text();
  const contentType = response.headers.get("content-type") || "";
  if (responseType === "json" || /(?:application\/json|\+json)(?:;|$)/i.test(contentType))
    return response.json();
  if (
    responseType === "auto" &&
    /^(?:application\/octet-stream|application\/pdf|image\/|audio\/|video\/)/i.test(contentType)
  )
    return response.blob();
  return response.text();
}

let authRedirecting = false;
function sessionExpired(error) {
  // Match the existing cloud auth contract; a data-session 401 alone is not a login redirect.
  if (!error.payload?.needs_auth && !["SESSION_EXPIRED", "AUTH_REQUIRED"].includes(error.code))
    return;
  const host = globalThis.window;
  if (!host || authRedirecting) return;
  authRedirecting = true;
  host.dispatchEvent?.(
    new CustomEvent("baa:session-expired", {
      detail: { code: error.code, requestId: error.requestId },
    }),
  );
  if (host.location?.pathname !== "/login") host.location?.assign?.("/login");
}

export function createApiClient({
  baseUrl = "",
  fetchImpl = (...args) => globalThis.fetch(...args),
  timeoutMs: defaultTimeoutMs = 30000,
  onSessionExpired = sessionExpired,
} = {}) {
  async function request(path, options = {}) {
    const {
      timeoutMs = defaultTimeoutMs,
      signal: externalSignal,
      responseType = "auto",
      requireOk = false,
      method: requestedMethod = "GET",
      ...fetchOptions
    } = options;
    const method = String(requestedMethod).toUpperCase();
    const url = `${baseUrl}${path}`;
    if (!/^[A-Z]+$/.test(method) || (["GET", "HEAD"].includes(method) && options.body != null)) {
      throw new ApiError("GET/HEAD cannot have a body; specify a write method explicitly", {
        code: "INVALID_REQUEST",
        kind: "request",
        method,
        url,
      });
    }
    if (
      !["auto", "json", "text", "blob", "arrayBuffer", "response"].includes(responseType) ||
      !Number.isFinite(timeoutMs) ||
      timeoutMs < 0
    ) {
      throw new ApiError("Invalid response type or timeout", {
        code: "INVALID_REQUEST",
        kind: "request",
        method,
        url,
      });
    }
    const headers = new Headers(options.headers || {});
    let body = options.body;
    if (isJsonBody(body)) {
      if (!headers.has("Content-Type")) headers.set("Content-Type", JSON_CONTENT_TYPE);
      body = JSON.stringify(body);
    }
    const controller = new AbortController();
    let response;
    let timer;
    let timedOut = false;
    let rejectAbort;
    const aborted = new Promise((_, reject) => {
      rejectAbort = reject;
    });
    const abort = () => {
      controller.abort(externalSignal?.reason);
      rejectAbort(
        new ApiError(requestMessage(timedOut ? "timeout" : "cancelled"), {
          ...metadata(response),
          status: response?.status || 0,
          code: timedOut ? "REQUEST_TIMEOUT" : "REQUEST_ABORTED",
          kind: timedOut ? "timeout" : "abort",
          method,
          url,
        }),
      );
    };
    externalSignal?.addEventListener("abort", abort, { once: true });
    if (externalSignal?.aborted) abort();
    if (timeoutMs > 0 && !controller.signal.aborted)
      timer = setTimeout(() => {
        timedOut = true;
        abort();
      }, timeoutMs);
    try {
      const operation = async () => {
        if (controller.signal.aborted) return aborted;
        response = await fetchImpl(url, {
          credentials: "same-origin",
          ...fetchOptions,
          method,
          headers,
          body,
          signal: controller.signal,
        });
        if (controller.signal.aborted) return aborted;
        // A binary download can still return a JSON business error with HTTP 200.
        // Inspect a clone so the caller's Response/Blob contract stays intact.
        const inspectJson =
          response.ok &&
          ["blob", "arrayBuffer", "response"].includes(responseType) &&
          /(?:application\/json|\+json)(?:;|$)/i.test(response.headers.get("content-type") || "");
        let payload;
        try {
          payload = await parseResponse(
            inspectJson ? response.clone() : response,
            inspectJson ? "json" : response.ok ? responseType : "auto",
          );
        } catch (cause) {
          if (!response.ok) payload = null;
          else
            throw new ApiError(requestMessage("invalid"), {
              ...metadata(response),
              status: response.status,
              url,
              code: "INVALID_RESPONSE",
              kind: "parse",
              method,
              cause,
            });
        }
        if (controller.signal.aborted) return aborted;
        const meta = metadata(response, payload);
        const businessFailure =
          payload &&
          typeof payload === "object" &&
          (payload.ok === false ||
            payload.needs_auth ||
            ["SESSION_EXPIRED", "AUTH_REQUIRED"].includes(meta.code));
        if (!response.ok || businessFailure || (requireOk && payload?.ok !== true)) {
          const auth = Boolean(
            payload?.needs_auth || ["SESSION_EXPIRED", "AUTH_REQUIRED"].includes(meta.code),
          );
          const error = new ApiError(
            auth
              ? requestMessage("auth")
              : payloadMessage(payload) ||
                  (response.ok
                    ? requestMessage("invalid")
                    : `${response.status} ${response.statusText}`.trim()) ||
                  requestMessage("failed"),
            {
              ...meta,
              status: response.status,
              statusText: response.statusText,
              url: response.url || url,
              payload,
              code:
                meta.code ||
                (auth
                  ? "SESSION_EXPIRED"
                  : response.ok
                    ? "BUSINESS_ERROR"
                    : `HTTP_${response.status}`),
              kind: response.ok ? "business" : "http",
              method,
            },
          );
          if (auth) {
            try {
              onSessionExpired?.(error);
            } catch {
              /* Keep the API error even if the UI handler fails. */
            }
          }
          throw error;
        }
        return inspectJson ? await parseResponse(response, responseType) : payload;
      };
      return await Promise.race([operation(), aborted]);
    } catch (cause) {
      if (cause instanceof ApiError) throw cause;
      if (controller.signal.aborted) return await aborted;
      throw new ApiError(requestMessage("network"), {
        code: "NETWORK_ERROR",
        kind: "network",
        method,
        url,
        cause,
      });
    } finally {
      clearTimeout(timer);
      externalSignal?.removeEventListener("abort", abort);
    }
  }
  return Object.freeze({
    request,
    get: (path, options = {}) => request(path, { ...options, method: "GET" }),
    post: (path, body, options = {}) => request(path, { ...options, method: "POST", body }),
    put: (path, body, options = {}) => request(path, { ...options, method: "PUT", body }),
    patch: (path, body, options = {}) => request(path, { ...options, method: "PATCH", body }),
    delete: (path, options = {}) => request(path, { ...options, method: "DELETE" }),
  });
}

export const apiClient = createApiClient();
