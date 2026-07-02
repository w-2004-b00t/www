import { defineStore } from 'pinia'
import { readBool, writeText } from '../utils/storage'

const REVIEW_MODE_KEY = 'eduagent_review_mode'

export const useUiStore = defineStore('ui', {
  state: () => ({
    reviewMode: readBool(REVIEW_MODE_KEY),
  }),
  actions: {
    setReviewMode(value: boolean) {
      this.reviewMode = value
      writeText(REVIEW_MODE_KEY, value)
    },
  },
})
