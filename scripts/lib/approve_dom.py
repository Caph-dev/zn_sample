#!/usr/bin/env python3
"""样品申请「待审核」列表：展开后点击「同意」（危险路径）。

前提：用户已停在样品申请 → 待审核。
纪律：
  - 仅在 execute 模式由 CLI 调用
  - 只点「同意」；绝不点「拒绝」
  - 二次确认弹窗仅点确认类按钮，不点取消
"""
from __future__ import annotations

import time
from typing import Any

from .zclaw import DEFAULT_EXEC_RETRIES, zclaw_exec

EXPAND_ALL_JS = r"""
(() => {
  // 若已是「全部收起」说明已展开
  const collapse = [...document.querySelectorAll('button,a,span,div')].find(el => {
    const text = (el.innerText || '').trim();
    return text === '全部收起' || text === 'Collapse all';
  });
  if (collapse && (collapse.offsetParent || collapse.getClientRects().length)) {
    return JSON.stringify({ok: true, already: true, state: 'expanded'});
  }
  const expand = [...document.querySelectorAll('button,a,span,div')].find(el => {
    const text = (el.innerText || '').trim();
    return text === '全部展开' || text === 'Expand all' || /^全部展开/.test(text);
  });
  if (!expand) {
    return JSON.stringify({ok: false, reason: 'no-expand-control'});
  }
  expand.click();
  return JSON.stringify({ok: true, clicked: true, text: (expand.innerText || '').trim().slice(0, 40)});
})()
"""

APPROVE_BY_APPLY_ID_JS = r"""
((applyId) => {
  function getRecord(tr) {
    const key = Object.keys(tr || {}).find(k =>
      k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance')
    );
    if (!key) return null;
    let fiber = tr[key];
    for (let depth = 0; depth < 60 && fiber; depth++) {
      const props = fiber.memoizedProps;
      if (props && props.record) return props.record;
      fiber = fiber.return;
    }
    return null;
  }

  const targetId = String(applyId || '');
  if (!targetId) {
    return JSON.stringify({ok: false, reason: 'empty-apply-id'});
  }

  // 同一 apply_id 常见两行：达人汇总行（无同意）+ 商品操作行（有同意/拒绝）。
  // 禁止命中第一行就 break；必须找「行内真有可点同意」的那一行。
  const rows = [...document.querySelectorAll('table tbody tr')];
  const matchedRows = [];
  for (const row of rows) {
    const current = getRecord(row) || {};
    if (String(current.apply_id || '') === targetId) {
      matchedRows.push({row, record: current});
    }
  }
  if (!matchedRows.length) {
    return JSON.stringify({ok: false, reason: 'row-not-found', apply_id: targetId});
  }

  function findApproveInRow(rowEl) {
    const candidates = [...rowEl.querySelectorAll('div,span,button,a')];
    let best = null;
    for (const element of candidates) {
      const text = (element.innerText || '').trim();
      // 必须整段就是「同意」，避免父节点 innerText 含「同意\\n拒绝」误命中
      if (text !== '同意' && text !== '批准' && text !== 'Approve') continue;
      const className = (element.className || '').toString();
      if (/cursor-not-allowed|opacity-50|disabled/i.test(className)) continue;
      if (element.disabled || element.getAttribute('aria-disabled') === 'true') continue;
      if (/text-primary|cursor-pointer|primary-normal/i.test(className) || element.tagName === 'BUTTON') {
        return element;
      }
      if (!best) best = element;
    }
    return best;
  }

  let targetRow = null;
  let record = null;
  let approveNode = null;
  for (const item of matchedRows) {
    if (item.record && item.record.can_be_approved === false) {
      continue;
    }
    const node = findApproveInRow(item.row);
    if (node) {
      targetRow = item.row;
      record = item.record;
      approveNode = node;
      break;
    }
  }

  if (!targetRow || !approveNode) {
    const anyForbidden = matchedRows.some(
      (item) => item.record && item.record.can_be_approved === false
    );
    if (anyForbidden && matchedRows.every(
      (item) => item.record && item.record.can_be_approved === false
    )) {
      return JSON.stringify({
        ok: false,
        reason: 'cannot-approve',
        apply_id: targetId,
        can_be_approved: false,
      });
    }
    const sampleTexts = matchedRows.map((item) =>
      (item.row.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80)
    );
    return JSON.stringify({
      ok: false,
      reason: 'approve-button-not-found',
      apply_id: targetId,
      matched_rows: matchedRows.length,
      can_be_approved: record ? record.can_be_approved : (matchedRows[0].record || {}).can_be_approved,
      row_text: sampleTexts.join(' | ').slice(0, 200),
    });
  }

  // 点最外层可点节点
  let clickTarget = approveNode;
  for (let hops = 0; hops < 4; hops++) {
    const parent = clickTarget.parentElement;
    if (!parent) break;
    const parentText = (parent.innerText || '').trim();
    const parentClass = (parent.className || '').toString();
    if (parentText === '同意' || parentText === '批准' || parentText === 'Approve') {
      if (/cursor-pointer|text-primary/i.test(parentClass)) {
        clickTarget = parent;
        continue;
      }
    }
    break;
  }
  clickTarget.click();
  return JSON.stringify({
    ok: true,
    clicked: true,
    apply_id: targetId,
    button_text: (approveNode.innerText || '').trim(),
    button_class: (clickTarget.className || '').toString().slice(0, 100),
  });
})
"""

CONFIRM_MODAL_JS = r"""
(() => {
  const dialogs = [...document.querySelectorAll(
    '.core-modal, .arco-modal, [role="dialog"], .core-modal-wrapper, .arco-modal-wrapper'
  )].filter(node => {
    const style = window.getComputedStyle(node);
    if (style && style.display === 'none') return false;
    if (style && style.visibility === 'hidden') return false;
    return !!(node.offsetParent || node.getClientRects().length);
  });

  // 有时 modal 在 body 直接挂载
  const bodyText = document.body.innerText || '';
  const looksLikeConfirm =
    /确认|确定|同意|Approve|Continue|Submit|提交/.test(bodyText) &&
    (dialogs.length > 0 || /是否|确定要|确认同意|Confirm/.test(bodyText));

  if (!dialogs.length && !looksLikeConfirm) {
    return JSON.stringify({ok: true, confirmed: false, reason: 'no-modal'});
  }

  const searchRoots = dialogs.length ? dialogs : [document.body];
  const confirmLabels = [
    '确定', '确认', '同意', 'Approve', 'Confirm', 'OK', 'Ok', '继续', '提交', '是'
  ];
  const cancelLabels = ['取消', 'Cancel', '关闭', 'Close', '否', '返回'];

  for (const root of searchRoots) {
    const buttons = [...root.querySelectorAll('button, a, span, div[role=button], .core-btn, .arco-btn')];
    // 优先 primary 确定
    let chosen = null;
    for (const button of buttons) {
      const text = (button.innerText || button.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ');
      if (!text || text.length > 20) continue;
      if (cancelLabels.some(label => text === label || text.includes(label))) continue;
      if (!confirmLabels.some(label => text === label || text.startsWith(label))) continue;
      const className = (button.className || '').toString();
      if (/cursor-not-allowed|disabled|opacity-50/i.test(className)) continue;
      if (button.disabled || button.getAttribute('aria-disabled') === 'true') continue;
      if (/primary|confirm|ok|blue/i.test(className)) {
        chosen = button;
        break;
      }
      if (!chosen) chosen = button;
    }
    if (chosen) {
      chosen.click();
      return JSON.stringify({
        ok: true,
        confirmed: true,
        text: (chosen.innerText || '').trim().slice(0, 40),
        class: (chosen.className || '').toString().slice(0, 80),
      });
    }
  }
  return JSON.stringify({ok: false, reason: 'confirm-button-not-found', dialogs: dialogs.length});
})()
"""

VERIFY_APPLY_GONE_JS = r"""
((applyId) => {
  function getRecord(tr) {
    const key = Object.keys(tr || {}).find(k =>
      k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance')
    );
    if (!key) return null;
    let fiber = tr[key];
    for (let depth = 0; depth < 60 && fiber; depth++) {
      const props = fiber.memoizedProps;
      if (props && props.record) return props.record;
      fiber = fiber.return;
    }
    return null;
  }
  const targetId = String(applyId || '');
  const rows = [...document.querySelectorAll('table tbody tr')];
  for (const row of rows) {
    const record = getRecord(row) || {};
    if (String(record.apply_id || '') === targetId) {
      return JSON.stringify({
        ok: true,
        still_present: true,
        can_be_approved: record.can_be_approved,
        review_status: record.review_status,
      });
    }
  }
  return JSON.stringify({ok: true, still_present: false});
})
"""


def ensure_all_expanded(store_id: str, *, page_wait: float = 1.5) -> dict[str, Any]:
    # 批准首包：对 network 假阳性做重试（doctor 绿也不等于 execute 通道可用）
    result = zclaw_exec(store_id, EXPAND_ALL_JS, retries=DEFAULT_EXEC_RETRIES)
    if not isinstance(result, dict):
        raise RuntimeError(f"expand-all bad result: {result!r}"[:300])
    if not result.get("ok"):
        raise RuntimeError(f"无法全部展开列表: {result}")
    if result.get("clicked"):
        time.sleep(page_wait)
    return result


def _wrap_apply_id_script(template: str, apply_id: str) -> str:
    # template is ((applyId) => { ... })  — invoke with JSON string
    return f"({template})({json_dumps_js(apply_id)})"


def json_dumps_js(value: str) -> str:
    import json as _json

    return _json.dumps(str(value), ensure_ascii=False)


def click_approve_for_apply_id(
    store_id: str,
    apply_id: str,
    *,
    page_wait: float = 1.2,
    confirm_wait: float = 0.8,
    verify_wait: float = 1.5,
) -> dict[str, Any]:
    """对指定 apply_id 点「同意」，并处理可能的二次确认。

    返回:
      ok, steps..., error?
    """
    outcome: dict[str, Any] = {
        "ok": False,
        "apply_id": str(apply_id),
        "steps": [],
    }

    expand = ensure_all_expanded(store_id, page_wait=page_wait)
    outcome["steps"].append({"expand": expand})

    click_script = _wrap_apply_id_script(APPROVE_BY_APPLY_ID_JS, apply_id)
    click_result = zclaw_exec(
        store_id, click_script, retries=DEFAULT_EXEC_RETRIES
    )
    outcome["steps"].append({"click": click_result})
    if not isinstance(click_result, dict) or not click_result.get("ok"):
        outcome["error"] = (click_result or {}).get("reason") if isinstance(click_result, dict) else "click-failed"
        outcome["detail"] = click_result
        return outcome

    time.sleep(confirm_wait)
    confirm_result = zclaw_exec(
        store_id, CONFIRM_MODAL_JS, retries=DEFAULT_EXEC_RETRIES
    )
    outcome["steps"].append({"confirm": confirm_result})
    if isinstance(confirm_result, dict) and confirm_result.get("confirmed"):
        time.sleep(verify_wait)
    else:
        # 无弹窗也可能已成功
        time.sleep(max(0.5, verify_wait * 0.5))

    verify_script = _wrap_apply_id_script(VERIFY_APPLY_GONE_JS, apply_id)
    verify_result = zclaw_exec(
        store_id, verify_script, retries=DEFAULT_EXEC_RETRIES
    )
    outcome["steps"].append({"verify": verify_result})

    still_present = isinstance(verify_result, dict) and verify_result.get("still_present")
    can_still = isinstance(verify_result, dict) and verify_result.get("can_be_approved")
    if still_present and can_still is True:
        outcome["error"] = "still-approvable-after-click"
        outcome["ok"] = False
        return outcome

    # 行消失，或仍在但不可再批 → 视为成功
    outcome["ok"] = True
    outcome["approved"] = True
    if still_present:
        outcome["note"] = "row-still-visible-but-not-approvable"
    else:
        outcome["note"] = "row-gone-or-left-pending-list"
    return outcome
