const EXECUTION_LIMIT_MEMORY_KEY = 'zn-sample:auto-approval:execution-limit:v1';
const DEFAULT_EXECUTION_LIMIT = 20;

export function isValidExecutionLimit(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 1;
}

export function readExecutionLimit(storage: Pick<Storage, 'getItem'>): number {
  try {
    const storedLimit: unknown = JSON.parse(storage.getItem(EXECUTION_LIMIT_MEMORY_KEY) ?? 'null');
    return isValidExecutionLimit(storedLimit) ? storedLimit : DEFAULT_EXECUTION_LIMIT;
  } catch {
    return DEFAULT_EXECUTION_LIMIT;
  }
}

export function writeExecutionLimit(storage: Pick<Storage, 'setItem'>, limit: number): boolean {
  if (!isValidExecutionLimit(limit)) {
    return false;
  }
  try {
    storage.setItem(EXECUTION_LIMIT_MEMORY_KEY, JSON.stringify(limit));
    return true;
  } catch {
    return false;
  }
}

export function loadBrowserExecutionLimit(): number {
  try {
    return readExecutionLimit(window.localStorage);
  } catch {
    return DEFAULT_EXECUTION_LIMIT;
  }
}

export function rememberBrowserExecutionLimit(limit: number): boolean {
  try {
    return writeExecutionLimit(window.localStorage, limit);
  } catch {
    return false;
  }
}
