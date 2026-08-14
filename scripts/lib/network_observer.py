#!/usr/bin/env python3
"""页面内被动网络观察器，用于发现批准请求契约。

观察器不主动发请求，只包装页面已有的 fetch/XMLHttpRequest。记录内容限制为
endpoint、请求体结构、必要业务 ID、HTTP/业务码和响应顶层键；不读取请求头，
也不保存完整请求体或响应。
"""
from __future__ import annotations

from typing import Any

from .zclaw import DEFAULT_EXEC_RETRIES, zclaw_exec

OBSERVER_STATE_KEY = "__znSampleNetworkObserverV1"

ARM_NETWORK_OBSERVER_JS = r"""
(() => {
  const stateKey = '__znSampleNetworkObserverV1';
  if (window[stateKey] && window[stateKey].installed) {
    return JSON.stringify({ok: true, already_installed: true});
  }

  const entries = [];
  const maximumEntries = 200;
  const identifierKeys = new Set([
    'apply_id', 'application_id', 'creator_id', 'creator_oec_id',
    'product_id', 'sku_id'
  ]);

  function describeValue(value, depth) {
    if (depth >= 5) return {type: 'max-depth'};
    if (value === null) return {type: 'null'};
    if (Array.isArray(value)) {
      const itemTypes = [...new Set(value.slice(0, 20).map(item => {
        if (item === null) return 'null';
        if (Array.isArray(item)) return 'array';
        return typeof item;
      }))].sort();
      return {type: 'array', length: value.length, item_types: itemTypes};
    }
    if (typeof value === 'object') {
      const fields = {};
      for (const key of Object.keys(value).sort().slice(0, 100)) {
        fields[key] = describeValue(value[key], depth + 1);
      }
      return {type: 'object', fields};
    }
    if (typeof value === 'string') return {type: 'string', length: value.length};
    return {type: typeof value};
  }

  function collectIdentifiers(value, output, depth) {
    if (depth >= 5 || value === null || value === undefined) return;
    if (Array.isArray(value)) {
      for (const item of value.slice(0, 50)) collectIdentifiers(item, output, depth + 1);
      return;
    }
    if (typeof value !== 'object') return;
    for (const [key, nestedValue] of Object.entries(value)) {
      const normalizedKey = String(key).toLowerCase();
      const scalarValue = ['string', 'number'].includes(typeof nestedValue);
      if (identifierKeys.has(normalizedKey) && scalarValue) {
        output[normalizedKey] = String(nestedValue).slice(0, 120);
      }
      collectIdentifiers(nestedValue, output, depth + 1);
    }
  }

  function parseRequestBody(body) {
    if (body === undefined || body === null || body === '') return null;
    if (typeof body === 'string') {
      try { return JSON.parse(body); } catch (error) {
        return {__non_json_text__: {length: body.length}};
      }
    }
    if (typeof URLSearchParams !== 'undefined' && body instanceof URLSearchParams) {
      return Object.fromEntries(body.entries());
    }
    return {__body_type__: Object.prototype.toString.call(body)};
  }

  function requestSummary(method, rawUrl, body) {
    let parsedUrl;
    try { parsedUrl = new URL(String(rawUrl || ''), location.href); } catch (error) {
      parsedUrl = new URL(location.href);
    }
    const parsedBody = parseRequestBody(body);
    const identifiers = {};
    collectIdentifiers(parsedBody, identifiers, 0);
    return {
      method: String(method || 'GET').toUpperCase(),
      endpoint: parsedUrl.origin === location.origin ? parsedUrl.pathname : 'cross-origin',
      query_keys: [...parsedUrl.searchParams.keys()].sort(),
      body_shape: describeValue(parsedBody, 0),
      identifiers,
    };
  }

  function responseSummary(responseText) {
    if (!responseText) return {business_code: null, business_message: '', response_keys: []};
    try {
      const payload = JSON.parse(responseText);
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        return {business_code: null, business_message: '', response_keys: []};
      }
      return {
        business_code: payload.code ?? null,
        business_message: String(payload.message || '').slice(0, 160),
        response_keys: Object.keys(payload).sort().slice(0, 100),
      };
    } catch (error) {
      return {business_code: null, business_message: '', response_keys: []};
    }
  }

  function appendEntry(entry) {
    entries.push(entry);
    if (entries.length > maximumEntries) entries.splice(0, entries.length - maximumEntries);
  }

  const originalFetch = window.fetch;
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;

  if (typeof originalFetch === 'function') {
    window.fetch = function(input, init) {
      const requestUrl = typeof input === 'string' ? input : (input && input.url) || '';
      const requestMethod = (init && init.method) || (input && input.method) || 'GET';
      const requestBody = init && init.body;
      const startedAt = Date.now();
      const summary = requestSummary(requestMethod, requestUrl, requestBody);
      return originalFetch.apply(this, arguments).then(response => {
        const baseEntry = {
          transport: 'fetch',
          ...summary,
          status: response.status,
          duration_ms: Date.now() - startedAt,
        };
        try {
          response.clone().text().then(text => {
            appendEntry({...baseEntry, ...responseSummary(text)});
          }).catch(() => appendEntry(baseEntry));
        } catch (error) {
          appendEntry(baseEntry);
        }
        return response;
      });
    };
  }

  XMLHttpRequest.prototype.open = function(method, url) {
    this.__znObserverRequest = {method, url, started_at: 0, body: null};
    return originalOpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function(body) {
    const requestMetadata = this.__znObserverRequest || {};
    requestMetadata.started_at = Date.now();
    requestMetadata.body = body;
    this.__znObserverRequest = requestMetadata;
    this.addEventListener('loadend', () => {
      const summary = requestSummary(
        requestMetadata.method,
        requestMetadata.url,
        requestMetadata.body
      );
      let responseText = '';
      try { responseText = this.responseText || ''; } catch (error) { responseText = ''; }
      appendEntry({
        transport: 'xhr',
        ...summary,
        status: this.status,
        duration_ms: Date.now() - requestMetadata.started_at,
        ...responseSummary(responseText),
      });
    }, {once: true});
    return originalSend.apply(this, arguments);
  };

  window[stateKey] = {
    installed: true,
    entries,
    restore: () => {
      if (typeof originalFetch === 'function') window.fetch = originalFetch;
      XMLHttpRequest.prototype.open = originalOpen;
      XMLHttpRequest.prototype.send = originalSend;
    },
  };
  return JSON.stringify({ok: true, installed: true});
})()
"""

DRAIN_NETWORK_OBSERVER_JS = r"""
((uninstall) => {
  const stateKey = '__znSampleNetworkObserverV1';
  const state = window[stateKey];
  if (!state || !state.installed) {
    return JSON.stringify({ok: true, installed: false, entries: []});
  }
  const entries = Array.isArray(state.entries) ? state.entries.slice() : [];
  state.entries.splice(0, state.entries.length);
  if (uninstall) {
    try { state.restore(); } catch (error) {}
    delete window[stateKey];
  }
  return JSON.stringify({ok: true, installed: !uninstall, entries});
})
"""


def arm_network_observer(store_id: str) -> dict[str, Any]:
    """安装被动观察器；不会主动产生任何网络请求。"""
    result = zclaw_exec(
        store_id,
        ARM_NETWORK_OBSERVER_JS,
        retries=DEFAULT_EXEC_RETRIES,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"安装网络观察器失败: {result!r}"[:400])
    return result


def drain_network_observer(
    store_id: str,
    *,
    uninstall: bool = True,
) -> list[dict[str, Any]]:
    """读取安全摘要并清空观察器；默认同时恢复页面原始网络函数。"""
    invocation_script = (
        f"({DRAIN_NETWORK_OBSERVER_JS})({'true' if uninstall else 'false'})"
    )
    result = zclaw_exec(
        store_id,
        invocation_script,
        retries=DEFAULT_EXEC_RETRIES,
    )
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(f"读取网络观察器失败: {result!r}"[:400])
    entries = result.get("entries")
    if not isinstance(entries, list):
        raise RuntimeError("网络观察器返回的 entries 不是数组")
    return [entry for entry in entries if isinstance(entry, dict)]
