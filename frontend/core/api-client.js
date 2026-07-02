const JSON_CONTENT_TYPE = "application/json";

export class ApiError extends Error {
  constructor(message, { status = 0, statusText = "", url = "", payload = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.statusText = statusText;
    this.url = url;
    this.payload = payload;
  }
}

function isJsonBody(body) {
  return (
    body !== undefined &&
    body !== null &&
    !(body instanceof FormData) &&
    !(body instanceof URLSearchParams) &&
    !(body instanceof Blob) &&
    typeof body !== "string"
  );
}

async function parseResponse(response) {
  if (response.status === 204 || response.status === 205) {
    return null;
  }

  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes(JSON_CONTENT_TYPE)) {
    return response.json();
  }
  return response.text();
}

function errorMessage(response, payload) {
  if (payload && typeof payload === "object") {
    return (
      payload.error ||
      payload.message ||
      payload.detail ||
      `${response.status} ${response.statusText}`.trim()
    );
  }
  return (
    (typeof payload === "string" && payload.trim()) ||
    `${response.status} ${response.statusText}`.trim() ||
    "Request failed"
  );
}

export function createApiClient({
  baseUrl = "",
  fetchImpl = globalThis.fetch.bind(globalThis),
} = {}) {
  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    let body = options.body;

    if (isJsonBody(body)) {
      headers.set("Content-Type", JSON_CONTENT_TYPE);
      body = JSON.stringify(body);
    }

    const response = await fetchImpl(`${baseUrl}${path}`, {
      credentials: "same-origin",
      ...options,
      headers,
      body,
    });
    const payload = await parseResponse(response);

    if (!response.ok) {
      throw new ApiError(errorMessage(response, payload), {
        status: response.status,
        statusText: response.statusText,
        url: response.url || `${baseUrl}${path}`,
        payload,
      });
    }

    return payload;
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
