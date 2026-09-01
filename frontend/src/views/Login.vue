<template>
  <div class="login-container">
    <el-card class="login-card" shadow="always">
      <h2 class="login-title">企业知识库问答系统</h2>
      <el-tabs v-model="mode" stretch>
        <!-- 登录 -->
        <el-tab-pane label="登 录" name="login">
          <el-form ref="formRef" :model="form" :rules="rules" size="large">
            <el-form-item prop="username">
              <el-input
                v-model="form.username"
                placeholder="用户名"
                @keyup.enter="handleLogin"
              />
            </el-form-item>
            <el-form-item prop="password">
              <el-input
                v-model="form.password"
                type="password"
                show-password
                placeholder="密码"
                @keyup.enter="handleLogin"
              />
            </el-form-item>
            <el-form-item>
              <el-button
                type="primary"
                class="login-btn"
                :loading="loading"
                @click="handleLogin"
              >
                登 录
              </el-button>
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <!-- 注册 -->
        <el-tab-pane label="注 册" name="register">
          <el-form ref="regFormRef" :model="regForm" :rules="regRules" size="large">
            <el-form-item prop="username">
              <el-input v-model="regForm.username" placeholder="用户名（3~32 位字母/数字/_.-）" />
            </el-form-item>
            <el-form-item prop="display_name">
              <el-input v-model="regForm.display_name" placeholder="昵称（选填）" />
            </el-form-item>
            <el-form-item prop="email">
              <el-input v-model="regForm.email" placeholder="邮箱（选填）" />
            </el-form-item>
            <el-form-item prop="password">
              <el-input
                v-model="regForm.password"
                type="password"
                show-password
                placeholder="密码（至少 6 位）"
              />
            </el-form-item>
            <el-form-item prop="confirm">
              <el-input
                v-model="regForm.confirm"
                type="password"
                show-password
                placeholder="确认密码"
                @keyup.enter="handleRegister"
              />
            </el-form-item>
            <el-form-item>
              <el-button
                type="primary"
                class="login-btn"
                :loading="registering"
                @click="handleRegister"
              >
                注 册
              </el-button>
            </el-form-item>
            <div class="reg-tip">注册后默认为普通员工角色，管理员可分配更高权限</div>
          </el-form>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()

const mode = ref('login')

/* ---------- 登录 ---------- */
const formRef = ref(null)
const loading = ref(false)
const form = reactive({
  username: '',
  password: '',
})

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

const handleLogin = () => {
  formRef.value.validate(async (valid) => {
    if (!valid) return
    loading.value = true
    try {
      await auth.login(form.username, form.password)
      ElMessage.success('登录成功')
      router.push('/chat')
    } catch (err) {
      // 拦截器已统一提示（handled），此处仅兜底未提示的错误
      if (!err?.handled) {
        ElMessage.error(err?.message || '登录失败，请检查用户名或密码')
      }
    } finally {
      loading.value = false
    }
  })
}

/* ---------- 注册 ---------- */
const regFormRef = ref(null)
const registering = ref(false)
const regForm = reactive({
  username: '',
  display_name: '',
  email: '',
  password: '',
  confirm: '',
})

const regRules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    {
      pattern: /^[A-Za-z0-9_.-]{3,32}$/,
      message: '3~32 位字母、数字或 _. -',
      trigger: 'blur',
    },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 6, message: '密码长度至少 6 位', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    {
      validator: (_rule, value, callback) => {
        if (value !== regForm.password) callback(new Error('两次输入的密码不一致'))
        else callback()
      },
      trigger: 'blur',
    },
  ],
  email: [
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
}

const handleRegister = () => {
  regFormRef.value.validate(async (valid) => {
    if (!valid) return
    registering.value = true
    try {
      await auth.register({
        username: regForm.username,
        password: regForm.password,
        display_name: regForm.display_name || undefined,
        email: regForm.email || undefined,
      })
      ElMessage.success('注册成功，已自动登录')
      router.push('/chat')
    } catch (err) {
      if (!err?.handled) {
        ElMessage.error(err?.message || '注册失败，请稍后再试')
      }
    } finally {
      registering.value = false
    }
  })
}
</script>

<style scoped>
.login-container {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  background: #f0f2f5;
}

.login-card {
  width: 380px;
  padding: 10px 10px 0;
}

.login-title {
  margin: 0 0 16px;
  text-align: center;
  color: #303133;
}

.login-btn {
  width: 100%;
}

.reg-tip {
  font-size: 12px;
  color: #909399;
  text-align: center;
  margin-bottom: 8px;
}
</style>
