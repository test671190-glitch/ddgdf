// All network calls to the same-origin backend live here.
// Every request is sent with credentials: "same-origin" so the `us_session` cookie is attached.

import { FALLBACK_CATALOG } from "./catalog-fallback.js";

const BASE = "/api";

export class ApiError extends Error {
  /**
   * @param {number} status HTTP status (0 for network errors)
   * @param {string} code   error.code from the backend, or a local code: "network", "aborted", "bad_response"
   * @param {string} message
   */
  constructor(status, code, message, data = null) {
    super(message || code);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.data = data;
  }
}

function codeFromStatus(status) {
  if (status === 401) return "unauthorized";
  if (status === 402) return "subscription_required";
  if (status === 404) return "not_found";
  if (status === 429) return "plan_limit_reached";
  if (status >= 500) return "upstream_error";
  return "http_" + status;
}

async function errorFromResponse(res) {
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* body is not JSON */
  }
  const err = data && typeof data === "object" ? data.error : null;
  const code = (err && (err.code || err.type)) || codeFromStatus(res.status);
  const message = (err && err.message) || res.statusText || "Ошибка запроса";
  return new ApiError(res.status, String(code), String(message), data);
}

async function request(method, path, body, { signal } = {}) {
  const opts = {
    method,
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    signal,
  };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(BASE + path, opts);
  } catch (e) {
    if (e && e.name === "AbortError") throw new ApiError(0, "aborted", "Запрос отменён");
    throw new ApiError(0, "network", "Нет соединения с сервером");
  }
  if (!res.ok) throw await errorFromResponse(res);
  if (res.status === 204) return null;
  try {
    return await res.json();
  } catch {
    throw new ApiError(res.status, "bad_response", "Сервер вернул неожиданный ответ");
  }
}

/* ---------------- catalog ---------------- */

function normalizeCatalog(data) {
  const fb = FALLBACK_CATALOG;
  const d = data && typeof data === "object" ? data : {};
  const models = Array.isArray(d.models) && d.models.length ? d.models : fb.models;
  return {
    shop_name: d.shop_name || fb.shop_name,
    bot_username: d.bot_username || fb.bot_username,
    api_base_url: d.api_base_url || fb.api_base_url,
    subscription_days: Number(d.subscription_days) || fb.subscription_days,
    models: models
      .filter((x) => x && x.id)
      .map((x) => ({
        id: String(x.id),
        name: String(x.name || x.id),
        vendor: String(x.vendor || ""),
        type: x.type === "image" || x.type === "video" ? x.type : "text",
        reasoning: Array.isArray(x.reasoning) && x.reasoning.length ? x.reasoning.map(String) : null,
        default_reasoning: x.default_reasoning ? String(x.default_reasoning) : null,
      })),
    tariffs: Array.isArray(d.tariffs) && d.tariffs.length ? d.tariffs : fb.tariffs,
    faq: Array.isArray(d.faq) && d.faq.length ? d.faq : fb.faq,
    _fallback: !data,
  };
}

let catalogPromise = null;

/** GET /api/catalog — never rejects: falls back to the embedded catalog. */
export function getCatalog() {
  if (!catalogPromise) {
    catalogPromise = request("GET", "/catalog")
      .then(normalizeCatalog)
      .catch(() => normalizeCatalog(null));
  }
  return catalogPromise;
}

/* ---------------- auth ---------------- */

/** GET /api/me → profile object, or null when not logged in (401). Throws ApiError for other failures. */
export async function getMe() {
  try {
    return await request("GET", "/me");
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return null;
    throw e;
  }
}

/** POST /api/auth/login → {token, url, expires_in} */
export const startLogin = () => request("POST", "/auth/login", {});

/** GET /api/auth/login/{token} → {status: "pending"|"confirmed"|"expired", user?} */
export const getLoginStatus = (token, opts) => request("GET", "/auth/login/" + encodeURIComponent(token), undefined, opts);

/** POST /api/auth/logout → {ok:true} */
export const logout = () => request("POST", "/auth/logout", {});

/* ---------------- payments ---------------- */

/** POST /api/pay/crypto {tariff} → {order_id, pay_url, amount_usd} */
export const createCryptoInvoice = (tariff) => request("POST", "/pay/crypto", { tariff });

/** GET /api/pay/crypto/{order_id} → {status: "pending"|"paid"|"expired"} */
export const getCryptoInvoice = (orderId, opts) => request("GET", "/pay/crypto/" + encodeURIComponent(orderId), undefined, opts);

/* ---------------- generation ---------------- */

/**
 * POST /api/chat/completions with stream:true. Reads the SSE stream and calls
 * onDelta({content, reasoning}) for every chunk. Resolves with {content, reasoning, finish_reason}.
 */
export async function streamChat(body, { signal, onDelta } = {}) {
  let res;
  try {
    res = await fetch(BASE + "/chat/completions", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ ...body, stream: true }),
      signal,
    });
  } catch (e) {
    if (e && e.name === "AbortError") throw new ApiError(0, "aborted", "Остановлено");
    throw new ApiError(0, "network", "Нет соединения с сервером");
  }
  if (!res.ok) throw await errorFromResponse(res);

  let content = "";
  let reasoning = "";
  let finish = null;
  const emit = (dc, dr) => {
    if (dc) content += dc;
    if (dr) reasoning += dr;
    if ((dc || dr) && onDelta) onDelta({ content, reasoning, delta: dc || "" });
  };

  const ctype = res.headers.get("content-type") || "";
  if (!res.body || ctype.includes("application/json")) {
    // The server answered without streaming: treat it as a regular completion.
    let data;
    try {
      data = await res.json();
    } catch {
      throw new ApiError(res.status, "bad_response", "Сервер вернул неожиданный ответ");
    }
    if (data && data.error) throw new ApiError(res.status, data.error.code || "upstream_error", data.error.message || "");
    const msg = data && data.choices && data.choices[0] && data.choices[0].message;
    emit(msg && typeof msg.content === "string" ? msg.content : "", msg && (msg.reasoning_content || msg.reasoning));
    return { content, reasoning, finish_reason: data?.choices?.[0]?.finish_reason || null };
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let done = false;

  const handleLine = (rawLine) => {
    const line = rawLine.replace(/\r$/, "");
    if (!line || line.startsWith(":")) return;
    if (!line.startsWith("data:")) return;
    const payload = line.slice(5).trim();
    if (!payload) return;
    if (payload === "[DONE]") {
      done = true;
      return;
    }
    let json;
    try {
      json = JSON.parse(payload);
    } catch {
      return; // ignore malformed keep-alive fragments
    }
    if (json && json.error) {
      throw new ApiError(res.status, json.error.code || "upstream_error", json.error.message || "Ошибка модели");
    }
    const choice = json && json.choices && json.choices[0];
    if (!choice) return;
    const delta = choice.delta || choice.message || {};
    const dc = typeof delta.content === "string" ? delta.content : "";
    const drRaw = delta.reasoning_content ?? delta.reasoning;
    const dr = typeof drRaw === "string" ? drRaw : "";
    emit(dc, dr);
    if (choice.finish_reason) finish = choice.finish_reason;
  };

  try {
    while (!done) {
      const { value, done: streamDone } = await reader.read();
      if (streamDone) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 1);
        handleLine(line);
        if (done) break;
      }
    }
    if (!done && buffer) handleLine(buffer);
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (e && e.name === "AbortError") throw new ApiError(0, "aborted", "Остановлено");
    throw new ApiError(0, "network", "Соединение прервалось");
  } finally {
    try {
      reader.cancel().catch(() => {});
    } catch {
      /* already closed */
    }
  }
  return { content, reasoning, finish_reason: finish };
}

/** POST /api/images/generations {model, prompt, aspect_ratio, resolution, n, images?} → {data:[{url}]} */
export const generateImages = (body, opts) => request("POST", "/images/generations", body, opts);

/** POST /api/videos {model, prompt, aspect_ratio, duration, resolution, generate_audio, images?} → {id, status} */
export const createVideo = (body, opts) => request("POST", "/videos", body, opts);

/** GET /api/videos/{id} → {id, status, url?} */
export const getVideo = (id, opts) => request("GET", "/videos/" + encodeURIComponent(id), undefined, opts);

/* ---------------- helpers ---------------- */

export const botLink = (catalog, start) =>
  "https://t.me/" + encodeURIComponent(catalog.bot_username) + (start ? "?start=" + encodeURIComponent(start) : "");
