export function readText(key: string, fallback = '') {
  return localStorage.getItem(key) ?? fallback
}

export function readBool(key: string, fallback = false) {
  const value = localStorage.getItem(key)
  return value === null ? fallback : value === 'true'
}

export function readNumber(key: string, fallback: number) {
  const value = Number(localStorage.getItem(key))
  return Number.isFinite(value) && value > 0 ? value : fallback
}

export function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch {
    return fallback
  }
}

export function writeText(key: string, value: string | number | boolean) {
  localStorage.setItem(key, String(value))
}

export function writeJson(key: string, value: unknown) {
  localStorage.setItem(key, JSON.stringify(value))
}

export function removeKeys(keys: string[]) {
  keys.forEach((key) => localStorage.removeItem(key))
}

export function removeAllScopedKeys(keys: string[]) {
  const suffixes = keys.map((key) => `${key}::`)
  Object.keys(localStorage)
    .filter((key) => suffixes.some((suffix) => key.startsWith(suffix)))
    .forEach((key) => localStorage.removeItem(key))
}

export function currentUserStorageId() {
  try {
    const raw = localStorage.getItem('eduagent_user')
    const user = raw ? JSON.parse(raw) : null
    return String(user?.id || user?.username || 'guest')
  } catch {
    return 'guest'
  }
}

export function scopedKey(key: string) {
  return `${key}::${currentUserStorageId()}`
}

export function readUserText(key: string, fallback = '') {
  return readText(scopedKey(key), fallback)
}

export function readUserBool(key: string, fallback = false) {
  return readBool(scopedKey(key), fallback)
}

export function readUserNumber(key: string, fallback: number) {
  return readNumber(scopedKey(key), fallback)
}

export function readUserJson<T>(key: string, fallback: T): T {
  return readJson(scopedKey(key), fallback)
}

export function writeUserText(key: string, value: string | number | boolean) {
  writeText(scopedKey(key), value)
}

export function writeUserJson(key: string, value: unknown) {
  writeJson(scopedKey(key), value)
}

export function removeUserKeys(keys: string[]) {
  removeKeys(keys.map((key) => scopedKey(key)))
}
