import { defineStore } from 'pinia'
import request from '../api/request'

const TOKEN_KEY = 'token'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem(TOKEN_KEY) || '',
    user: null,
  }),
  getters: {
    /** 是否管理员（控制"权限管理"菜单可见性） */
    isAdmin: (state) => !!state.user?.roles?.includes('ADMIN'),
    /** 知识库/文档管理权限（ADMIN 或 KNOWLEDGE_MANAGER） */
    canManageKb: (state) =>
      !!state.user?.roles?.some((r) => ['ADMIN', 'KNOWLEDGE_MANAGER'].includes(r)),
    displayName: (state) => state.user?.display_name || state.user?.username || '',
  },
  actions: {
    // 登录：POST /api/auth/login，响应结构 { code, message, data: { access_token, user } }
    async login(username, password) {
      const res = await request.post('/auth/login', { username, password })
      const { access_token, user } = res.data.data
      this.token = access_token
      localStorage.setItem(TOKEN_KEY, access_token)
      this.user = user || null
      return res.data
    },
    // 注册：POST /api/auth/register，成功后直接持有 token（免二次登录）
    async register(payload) {
      const res = await request.post('/auth/register', payload)
      const { access_token, user } = res.data.data
      this.token = access_token
      localStorage.setItem(TOKEN_KEY, access_token)
      this.user = user || null
      return res.data
    },
    // 当前用户信息（页面刷新后 user 为空时调用）
    async fetchMe() {
      const res = await request.get('/auth/me')
      this.user = res.data.data
      return this.user
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem(TOKEN_KEY)
    },
  },
})
