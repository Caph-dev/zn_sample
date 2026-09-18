/**
 * 执行幂等键：服务端要求非空且 ≤128 字符（docs/spec/自动审批候选筛选流程.md）。
 * 选中行不能直接拼进键——申请 ID 是 19 位数字，十几行就会超过上限并触发
 * 400 `invalid-idempotency-key`。这里对同一份选择做稳定摘要，长度固定。
 */
const IDEMPOTENCY_KEY_MAX_LENGTH = 128;

const FNV1A64_OFFSET_BASIS = 0xcbf29ce484222325n;
const FNV1A64_PRIME = 0x100000001b3n;
const UINT64_MASK = 0xffffffffffffffffn;

/** 同步、不依赖环境；相同输入永远得到相同键，重复点击/重试才能命中服务端去重。 */
function stableDigest(value: string): string {
  let hash = FNV1A64_OFFSET_BASIS;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= BigInt(value.charCodeAt(index));
    hash = (hash * FNV1A64_PRIME) & UINT64_MASK;
  }
  return hash.toString(16).padStart(16, '0');
}

export interface ExecutionIdempotencyInput {
  previewId: string;
  applyIds: string[];
  limit: number;
  writeFeishu: boolean;
}

export function buildExecutionIdempotencyKey(input: ExecutionIdempotencyInput): string {
  const {previewId, applyIds, limit, writeFeishu} = input;
  const digest = stableDigest(
    [previewId, applyIds.join(','), String(limit), writeFeishu ? '1' : '0'].join('|'),
  );
  const key = `${previewId}:${digest}`;
  if (key.length > IDEMPOTENCY_KEY_MAX_LENGTH) {
    throw new Error(`幂等键超过服务端 ${IDEMPOTENCY_KEY_MAX_LENGTH} 字符上限。`);
  }
  return key;
}
