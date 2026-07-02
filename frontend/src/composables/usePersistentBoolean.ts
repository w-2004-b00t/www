import { ref, watch } from 'vue'
import { readBool, writeText } from '../utils/storage'

export function usePersistentBoolean(key: string, fallback = false) {
  const value = ref(readBool(key, fallback))
  watch(value, (next) => writeText(key, next))
  return value
}
