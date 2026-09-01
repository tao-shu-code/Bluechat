import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/Login.vue'),
  },
  {
    path: '/',
    component: () => import('../views/Layout.vue'),
    redirect: '/chat',
    children: [
      {
        path: 'chat',
        name: 'Chat',
        component: () => import('../views/Chat.vue'),
      },
      {
        path: 'documents',
        name: 'Documents',
        component: () => import('../views/Documents.vue'),
      },
      {
        path: 'admin',
        name: 'Admin',
        component: () => import('../views/Admin.vue'),
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

// 路由守卫：无 token 访问非 /login 页面时跳转 /login
router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.path !== '/login' && !auth.token) {
    return { path: '/login' }
  }
  if (to.path === '/login' && auth.token) {
    return { path: '/chat' }
  }
  // 权限管理页仅 ADMIN 可访问（user 未加载时先放行，由 Layout 拉取后再控制）
  if (to.path === '/admin' && auth.user && !auth.isAdmin) {
    return { path: '/chat' }
  }
})

export default router
