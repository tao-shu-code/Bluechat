<template>
  <el-container class="layout">
    <!-- 桌面固定侧边栏 -->
    <el-aside width="200px" class="layout-aside desktop-only">
      <div class="logo">知识库问答</div>
      <el-menu :default-active="route.path" router class="layout-menu">
        <el-menu-item index="/chat">对话问答</el-menu-item>
        <el-menu-item index="/documents">文档管理</el-menu-item>
        <el-menu-item v-if="auth.isAdmin" index="/admin">权限管理</el-menu-item>
      </el-menu>
    </el-aside>

    <el-container class="layout-body">
      <el-header class="layout-header">
        <div class="header-left">
          <!-- 移动端汉堡按钮 -->
          <button
            class="hamburger mobile-only"
            type="button"
            aria-label="打开菜单"
            @click="drawerVisible = true"
          >
            <span></span><span></span><span></span>
          </button>
          <span class="header-title">企业知识库问答系统</span>
        </div>
        <div class="header-right">
          <span class="username">{{ auth.displayName || '未登录' }}</span>
          <el-divider direction="vertical" />
          <el-button link type="danger" @click="handleLogout">退出</el-button>
        </div>
      </el-header>
      <el-main class="layout-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>

  <!-- 移动端抽屉导航 -->
  <el-drawer v-model="drawerVisible" direction="ltr" size="72%" :with-header="false">
    <div class="drawer-inner">
      <div class="logo drawer-logo">知识库问答</div>
      <el-menu
        :default-active="route.path"
        router
        class="drawer-menu"
        @select="drawerVisible = false"
      >
        <el-menu-item index="/chat">对话问答</el-menu-item>
        <el-menu-item index="/documents">文档管理</el-menu-item>
        <el-menu-item v-if="auth.isAdmin" index="/admin">权限管理</el-menu-item>
      </el-menu>
    </div>
  </el-drawer>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const drawerVisible = ref(false)

// 页面刷新后 pinia 中 user 丢失，用 token 换取用户信息（控制菜单/权限展示）
onMounted(() => {
  if (!auth.user && auth.token) {
    auth.fetchMe().catch(() => {})
  }
})

const handleLogout = () => {
  auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.layout {
  height: 100%;
}

.layout-aside {
  background-color: #001529;
}

.logo {
  height: 60px;
  line-height: 60px;
  text-align: center;
  color: #fff;
  font-size: 16px;
  font-weight: 600;
}

.layout-menu {
  border-right: none;
  background-color: #001529;
}

.layout-menu :deep(.el-menu-item) {
  color: rgba(255, 255, 255, 0.65);
}

.layout-menu :deep(.el-menu-item.is-active) {
  color: #fff;
}

.layout-menu :deep(.el-menu-item:hover) {
  background-color: rgba(255, 255, 255, 0.08);
}

.layout-body {
  background-color: #f5f7fa;
  min-width: 0;
}

.layout-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background-color: #fff;
  border-bottom: 1px solid #e4e7ed;
  height: 56px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.header-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.header-right {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.username {
  color: #606266;
  font-size: 14px;
  max-width: 140px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.layout-main {
  background-color: #f5f7fa;
  padding: 0;
  overflow: hidden;
}

/* 汉堡按钮（移动端） */
.hamburger {
  display: inline-flex;
  flex-direction: column;
  justify-content: center;
  gap: 4px;
  width: 32px;
  height: 32px;
  padding: 6px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
}

.hamburger span {
  display: block;
  height: 2px;
  background: #303133;
  border-radius: 1px;
}

.drawer-inner {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.drawer-logo {
  background-color: #001529;
}

.drawer-menu {
  border-right: none;
}

/* 响应式：<768px 隐藏侧栏显示汉堡，>=768px 隐藏汉堡 */
@media (max-width: 767px) {
  .desktop-only {
    display: none !important;
  }
}

@media (min-width: 768px) {
  .mobile-only {
    display: none !important;
  }
}
</style>
