#!/usr/bin/env python3
"""达人详情页只读抽取。

入口：列表行点头像 → /connection/creator/detail?cid=...
返回列表：history.back() 或 visit 回样品申请 URL。

纪律：页面上存在「同意/拒绝」时 **绝不点击**；本模块只解析文本/数字。
"""
from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote

from .parse_metrics import parse_count, parse_money, parse_percent
from .zclaw import visit_page, zclaw_exec

# 禁止在详情/列表误点的文案
FORBIDDEN_CLICK_RE = re.compile(r"同意|批准|Approve|Reject|拒绝|发货|同意申请")

EXTRACT_DETAIL_JS = r"""
(() => {
  // 真实页面（中文联盟详情）多为「标签\n值\n标签\n值」交错，例如：
  //   视频GPM\n$16.5\n视频数\n370\n平均视频播放量\n417\n视频平均互动率\n1.2%
  //   直播 GPM\n$0.00\n直播数\n3\n平均直播播放量\n2570\n直播平均互动率\n115.29%
  // 禁止再用「整段标签之后再取第一个 $」：会把直播区的 $0 误当成视频 GPM。
  const text = ((document.body && document.body.innerText) || '').replace(/\u00a0/g, ' ');

  const moneyToken = '\\$\\s*[0-9][0-9,]*(?:\\.[0-9]+)?[kKmM]?';
  const countToken = '[0-9][0-9,]*(?:\\.[0-9]+)?[kKmM万]?';
  const pctToken = '[0-9]+(?:\\.[0-9]+)?\\s*%';
  // 标签与值之间允许换行/空白，但中间不能再出现其它标签长文
  const gap = '[\\t \\u00a0]*[\\r\\n]+[\\t \\u00a0]*|[\\t \\u00a0]{1,8}';

  function normMoney(s) {
    return String(s || '').replace(/\\s+/g, '');
  }
  function normPlain(s) {
    return String(s || '').replace(/\\s+/g, '').trim();
  }

  function grabImmediate(full, labelAlts, valueToken) {
    const labels = Array.isArray(labelAlts) ? labelAlts : [labelAlts];
    for (const label of labels) {
      const re = new RegExp(
        '(?:^|[\\r\\n\\t ])(' + label + ')(?:' + gap + ')(' + valueToken + ')',
        'i'
      );
      const m = full.match(re);
      if (m && m[2]) return m[2];
    }
    return '';
  }

  function sectionSlice(full, startRes, endRes) {
    let start = -1;
    for (const s of startRes) {
      const i = full.search(s);
      if (i >= 0 && (start < 0 || i < start)) start = i;
    }
    if (start < 0) return '';
    let end = Math.min(full.length, start + 900);
    const rest = full.slice(start + 2);
    for (const e of endRes) {
      const j = rest.search(e);
      if (j >= 0) end = Math.min(end, start + 2 + j);
    }
    return full.slice(start, end);
  }

  // 视频区：从「视频数据/视频GPM」到「直播数据/直播 GPM」
  let videoSec = sectionSlice(
    text,
    [/视频数据/, /视频\s*GPM/, /Video\s*data/i, /Video\s*GPM/i],
    [/直播数据/, /直播\s*GPM/, /Live\s*GPM/i, /LIVE\s*data/i]
  );
  // 直播区：从「直播数据/直播 GPM」到「粉丝数/性别/趋势」等
  let liveSec = sectionSlice(
    text,
    [/直播数据/, /直播\s*GPM/, /Live\s*GPM/i, /LIVE\s*data/i],
    [/\n粉丝数\n/, /\n性别\n/, /\n趋势\n/, /\nFollowers\n/i, /\nTRENDS\n/i, /视频数据/]
  );
  if (!videoSec) videoSec = text;
  if (!liveSec) liveSec = text;

  // —— 优先：标签后紧跟数值（交错布局，与实页一致）——
  let video_gpm = normMoney(
    grabImmediate(videoSec, ['视频\\s*GPM', 'Video\\s*GPM'], moneyToken) ||
    grabImmediate(text, ['视频\\s*GPM', 'Video\\s*GPM'], moneyToken)
  );
  let live_gpm = normMoney(
    grabImmediate(liveSec, ['直播\\s*GPM', 'Live\\s*GPM'], moneyToken) ||
    grabImmediate(text, ['直播\\s*GPM', 'Live\\s*GPM'], moneyToken)
  );
  let avg_video_views = normPlain(
    grabImmediate(videoSec, ['平均视频播放量', 'Avg\\.?\\s*video\\s*views'], countToken) ||
    grabImmediate(text, ['平均视频播放量', 'Avg\\.?\\s*video\\s*views'], countToken)
  );
  let video_engagement = normPlain(
    grabImmediate(videoSec, ['视频平均互动率', 'Avg\\.?\\s*video\\s*engagement\\s*rate'], pctToken) ||
    grabImmediate(text, ['视频平均互动率', 'Avg\\.?\\s*video\\s*engagement\\s*rate'], pctToken)
  );
  let avg_live_views = normPlain(
    grabImmediate(liveSec, [
      '平均直播播放量',
      'Livestream\\s*Watch\\s*UV',
      'Watch\\s*UV',
      'Avg\\.?\\s*live\\s*views?',
    ], countToken) ||
    grabImmediate(text, ['平均直播播放量', 'Livestream\\s*Watch\\s*UV', 'Watch\\s*UV'], countToken)
  );
  let live_engagement = normPlain(
    grabImmediate(liveSec, ['直播平均互动率', 'Avg\\.?\\s*Live\\s*engagement\\s*rate'], pctToken) ||
    grabImmediate(text, ['直播平均互动率', 'Avg\\.?\\s*Live\\s*engagement\\s*rate'], pctToken)
  );

  // —— 次选：标签横排 + 数值横排（仅当视频区切片内、且未抽到 GPM 时）——
  // 注意：必须在 videoSec 内，不能扫整页，否则会吃到直播 $0
  let extract_via = 'immediate';
  if (!video_gpm) {
    const labelBlock = videoSec.match(
      /(?:视频\s*GPM|Video\s*GPM)[\s\S]{0,60}?(?:视频数|Videos?)[\s\S]{0,60}?(?:平均视频播放量|Avg\.?\s*video\s*views)[\s\S]{0,60}?(?:视频平均互动率|Avg\.?\s*video\s*engagement\s*rate)/i
    );
    if (labelBlock) {
      const after = videoSec.slice(
        labelBlock.index + labelBlock[0].length,
        labelBlock.index + labelBlock[0].length + 200
      );
      // 若 after 里在第一个 $ 前又出现「直播」，说明切片失败，勿用
      const liveHit = after.search(/直播|Live\s*GPM/i);
      const money = after.match(new RegExp(moneyToken));
      if (money && (liveHit < 0 || money.index < liveHit)) {
        video_gpm = normMoney(money[0]);
        const tail = after.slice(money.index + money[0].length);
        const nums = [...tail.matchAll(/[0-9][0-9,]*(?:\.[0-9]+)?%?/g)].map(x => x[0]);
        if (!avg_video_views) avg_video_views = normPlain(nums[1] || nums[0] || '');
        if (!video_engagement) {
          const pct = nums.find(n => String(n).includes('%'));
          video_engagement = normPlain(pct || nums[2] || '');
        }
        extract_via = 'metric-row-video-sec';
      }
    }
  }
  if (!live_gpm) {
    const money = liveSec.match(new RegExp('(?:直播\\s*GPM|Live\\s*GPM)(?:' + gap + ')(' + moneyToken + ')', 'i'));
    if (money) {
      live_gpm = normMoney(money[1]);
      extract_via = extract_via === 'immediate' ? 'immediate' : extract_via + '+live';
    }
  }

  // 整体 GPM / 客单价等（非视频/直播专用标签）
  const est_post_rate = normPlain(
    grabImmediate(text, ['Est\\.?\\s*post\\s*rate', '预计发布率'], pctToken)
  );
  let overall_gpm = '';
  {
    // 独立 GPM 行，排除视频/直播标签
    const m = text.match(/(?:^|[\r\n])[ \t]*GPM[ \t]*(?:\r?\n)[ \t]*(\$[0-9.,]+[kKmM]?)/i);
    if (m) overall_gpm = normMoney(m[1]);
    if (!overall_gpm) {
      const m2 = text.match(/千次曝光成交金额(?:[\t \u00a0]*[\r\n]+[\t \u00a0]*|[\t \u00a0]{1,8})(\$[0-9.,]+[kKmM]?)/i);
      if (m2) overall_gpm = normMoney(m2[1]);
    }
    // 无独立 GPM 时用视频 GPM 作参考（导出字段 overall 与视频分开）
  }
  const revenue_per_buyer = normMoney(
    grabImmediate(text, ['Revenue\\s*per\\s*buyer', '客单价'], moneyToken)
  );
  const revenue = normMoney(
    grabImmediate(text, ['销售额'], moneyToken)
  );
  const units = normPlain(
    grabImmediate(text, ['Units\\s*sold', '成交件数'], countToken)
  );

  // 卡片可见性诊断
  const has_cn_video_card = /视频\s*GPM/.test(text);
  const has_en_video_card = /Video\s*GPM/i.test(text);
  if (!video_gpm) extract_via = 'miss';
  else if (extract_via === 'immediate') extract_via = 'label-value';

  let creator_type = '';
  const vgNum = parseFloat(String(video_gpm || '').replace(/[^0-9.]/g, '')) || 0;
  const lgNum = parseFloat(String(live_gpm || '').replace(/[^0-9.]/g, '')) || 0;
  if (vgNum > 0 && lgNum <= 0) creator_type = '视频达人';
  else if (lgNum > 0 && vgNum <= 0) creator_type = '直播达人';
  else if (vgNum > 0 && lgNum > 0) creator_type = '视频+直播';

  // 诊断片段：视频卡附近原文，便于导出排查
  let video_block_head = '';
  {
    const i = text.search(/视频\s*GPM|Video\s*GPM/i);
    if (i >= 0) video_block_head = text.slice(i, i + 220).replace(/\s+/g, ' ').trim();
  }

  let bio = '';
  const bioEl = document.querySelector(
    '#creator-detail-profile-container span.text-body-s-regular.break-words.whitespace-pre-wrap'
  );
  if (bioEl) bio = (bioEl.innerText || '').trim();
  if (!bio) {
    const profile = document.querySelector('#creator-detail-profile-container');
    const mid = profile && profile.querySelector('.flex-c.flex-grow.relative span');
    if (mid) bio = (mid.innerText || '').trim();
  }

  return JSON.stringify({
    href: location.href,
    title: document.title || '',
    bio: bio || '',
    video_gpm: video_gpm || '',
    live_gpm: live_gpm || '',
    avg_video_views: avg_video_views || '',
    video_engagement: video_engagement || '',
    avg_live_views: avg_live_views || '',
    live_engagement: live_engagement || '',
    est_post_rate: est_post_rate || '',
    overall_gpm: overall_gpm || '',
    revenue_per_buyer: revenue_per_buyer || '',
    revenue: revenue || '',
    units_sold_detail: units || '',
    creator_type,
    has_cn_video_card,
    has_en_video_card,
    extract_via,
    video_block_head,
    text_head: text.slice(0, 400),
  });
})()
"""

CLICK_AVATAR_JS_TMPL = r"""
(() => {
  const want = %APPLY_ID%;
  const wantName = %CREATOR_NAME%;
  function getRecord(tr) {
    const key = Object.keys(tr || {}).find(k =>
      k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance')
    );
    if (!key) return null;
    let f = tr[key];
    for (let i = 0; i < 50 && f; i++) {
      if (f.memoizedProps && f.memoizedProps.record) return f.memoizedProps.record;
      f = f.return;
    }
    return null;
  }
  const trs = [...document.querySelectorAll('table tbody tr')];
  let target = null;
  for (const tr of trs) {
    const r = getRecord(tr) || {};
    const c = r.creator_info || {};
    const aid = r.apply_id != null ? String(r.apply_id) : '';
    const name = c.name || '';
    if (want && aid === want) { target = tr; break; }
    if (!want && wantName && name === wantName) { target = tr; break; }
  }
  if (!target && trs.length) {
    // 宽松：名称包含
    for (const tr of trs) {
      const r = getRecord(tr) || {};
      const c = r.creator_info || {};
      if (wantName && String(c.name || '').includes(wantName)) { target = tr; break; }
    }
  }
  if (!target) return JSON.stringify({ok:false, reason:'row-not-found', want, wantName});
  const av = target.querySelector('.pulse-avatar.cursor-pointer, .pulse-avatar-is-image, .pulse-avatar');
  if (!av) return JSON.stringify({ok:false, reason:'no-avatar'});
  // 二次保险：父链文案不得是同意
  let el = av;
  for (let i = 0; i < 4 && el; i++) {
    const t = (el.innerText || '') + (el.getAttribute('aria-label') || '');
    if (/同意|批准|Approve|拒绝|Reject/.test(t) && t.length < 20) {
      return JSON.stringify({ok:false, reason:'forbidden-near', t});
    }
    el = el.parentElement;
  }
  av.click();
  return JSON.stringify({ok:true, via:'avatar'});
})()
"""


def _js_str(s: str | None) -> str:
    if s is None:
        return "null"
    return json_dumps_js(s)


def json_dumps_js(s: str) -> str:
    import json

    return json.dumps(s, ensure_ascii=False)


def open_creator_detail_by_click(
    store_id: str,
    *,
    apply_id: str = "",
    creator_name: str = "",
    wait: float = 4.0,
) -> dict:
    import json as _json

    script = CLICK_AVATAR_JS_TMPL.replace(
        "%APPLY_ID%", _json.dumps(apply_id) if apply_id else "null"
    ).replace(
        "%CREATOR_NAME%", _json.dumps(creator_name) if creator_name else "null"
    )
    ret = zclaw_exec(store_id, script)
    if not isinstance(ret, dict) or not ret.get("ok"):
        return {"ok": False, "click": ret}
    time.sleep(wait)
    return {"ok": True, "click": ret}


def open_creator_detail_by_url(
    store_id: str,
    creator_id: str,
    *,
    creator_name: str = "",
    wait: float = 4.0,
) -> dict:
    """直链打开详情（不经过同意按钮）。"""
    cid = quote(str(creator_id), safe="")
    cname = quote(creator_name or "", safe="")
    url = (
        "https://affiliate.tiktokshopglobalselling.com/connection/creator/detail"
        f"?cid={cid}&enter_from=sample_request&pair_source=sample_request_click"
        f"&cname={cname}&shop_region=US"
    )
    outer = visit_page(store_id, url)
    time.sleep(wait)
    return {"ok": True, "visit": outer, "url": url}


def _normalize_detail_numbers(ret: dict) -> dict:
    ret["video_gpm_n"] = parse_money(ret.get("video_gpm"))
    ret["live_gpm_n"] = parse_money(ret.get("live_gpm"))
    ret["overall_gpm_n"] = parse_money(ret.get("overall_gpm"))
    ret["avg_video_views_n"] = parse_count(ret.get("avg_video_views"))
    ret["avg_live_views_n"] = parse_count(ret.get("avg_live_views"))
    ret["video_engagement_n"] = parse_percent(ret.get("video_engagement"))
    ret["live_engagement_n"] = parse_percent(ret.get("live_engagement"))
    ret["est_post_rate_n"] = parse_percent(ret.get("est_post_rate"))
    ret["aov_detail_n"] = parse_money(ret.get("revenue_per_buyer"))
    return ret


def extract_creator_detail(store_id: str, *, retries: int = 2, retry_wait: float = 1.5) -> dict:
    """从当前详情页抽取指标；视频/直播 GPM 为空时短暂重试（卡片懒加载）。"""
    last: dict | None = None
    attempts = max(1, retries + 1)
    for attempt in range(attempts):
        ret = zclaw_exec(store_id, EXTRACT_DETAIL_JS)
        if not isinstance(ret, dict):
            raise RuntimeError(f"extract detail bad: {ret!r}"[:300])
        last = _normalize_detail_numbers(ret)
        has_core = (
            last.get("video_gpm_n") is not None
            or last.get("live_gpm_n") is not None
            or last.get("overall_gpm_n") is not None
        )
        # 页面已出现视频卡文案但仍无金额 → 再等一轮
        card_visible = bool(last.get("has_cn_video_card") or last.get("has_en_video_card"))
        if has_core and (last.get("video_gpm_n") is not None or last.get("live_gpm_n") is not None):
            return last
        if has_core and not card_visible:
            return last
        if attempt + 1 < attempts:
            time.sleep(retry_wait)
    assert last is not None
    return last


def go_back_to_list(store_id: str, list_href: str = "", *, wait: float = 2.5) -> dict:
    if list_href and "sample-request" in list_href:
        outer = visit_page(store_id, list_href)
        time.sleep(wait)
        return {"ok": True, "via": "visit", "outer": outer}
    ret = zclaw_exec(
        store_id,
        "(() => { history.back(); return JSON.stringify({ok:true, href: location.href}); })()",
    )
    time.sleep(wait)
    return {"ok": True, "via": "history.back", "ret": ret}


def fetch_detail_for_row(
    store_id: str,
    row: dict,
    *,
    list_href: str = "",
    prefer_url: bool = True,
    wait: float = 4.0,
) -> dict:
    """只读拉详情并回到列表。绝不点同意。"""
    cid = str(row.get("creator_id") or "").strip()
    name = str(row.get("creator_name") or "").strip()
    apply_id = str(row.get("apply_id") or "").strip()

    opened: dict[str, Any]
    if prefer_url and cid:
        opened = open_creator_detail_by_url(store_id, cid, creator_name=name, wait=wait)
    else:
        opened = open_creator_detail_by_click(
            store_id, apply_id=apply_id, creator_name=name, wait=wait
        )
        if not opened.get("ok") and cid:
            opened = open_creator_detail_by_url(store_id, cid, creator_name=name, wait=wait)

    if not opened.get("ok"):
        return {"ok": False, "error": "open-detail-failed", "opened": opened}

    try:
        detail = extract_creator_detail(store_id)
    except Exception as e:
        go_back_to_list(store_id, list_href)
        return {"ok": False, "error": str(e)}

    # 确认在详情页：URL 含 creator/detail，或已抽出视频/直播/整体 GPM
    href = str(detail.get("href") or "")
    on_detail = "creator/detail" in href
    has_any_metric = bool(
        detail.get("video_gpm")
        or detail.get("live_gpm")
        or detail.get("overall_gpm")
        or detail.get("revenue_per_buyer")
    )
    if not on_detail and not has_any_metric:
        go_back_to_list(store_id, list_href)
        return {"ok": False, "error": "not-on-detail", "detail": detail}

    go_back_to_list(store_id, list_href)
    # 回到列表后轻等 + 确保仍是列表
    time.sleep(1.0)
    return {"ok": True, "detail": detail, "opened": opened}
