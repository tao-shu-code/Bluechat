import axios from 'axios'
import { ElMessage } from 'element-plus'
import router from '../router'
import { useAuthStore } from '../stores/auth'

const request = axios.create({
  baseURL: '/api',
  timeout: 15000,
})

const extractErrorMessage = (error) => {
  const data = error?.response?.data
  if (data) {
    return data.message || (typeof data.detail === 'string' ? data.detail : null) || error.message
  }
  return error?.message || '网络错误，请稍后重试'
}

// 请求拦截器：自动附加 Authorization 头
request.interceptors.request.use((config) => {
  const auth = useAuthStore()
  if (auth.token) {
    config.headers.Authorization = `Bearer ${auth.token}`
  }
  return config
})

// 响应拦截器：统一处理 { code, message } 业务结构与 HTTP 错误
request.interceptors.response.use(
  (response) => {
    const body = response.data
    // 后端统一响应 { code:0, message, data }；code!=0 为业务错误（HTTP 200）
    if (body && typeof body === 'object' && typeof body.code === 'number' && body.code !== 0) {
      const message = body.message || '请求失败'
      const err = new Error(message)
      err.handled = true // 拦截器已提示，调用方无需重复弹窗
      ElMessage.error(message)
      return Promise.reject(err)
    }
    return response
  },
  (error) => {
    if (error.response && error.response.status === 401) {
      const auth = useAuthStore()
      auth.logout()
      // 登录页输错密码也是 401，此时不重复弹"登录过期"提示
      if (router.currentRoute.value.path !== '/login') {
        ElMessage.error('登录已过期，请重新登录')
      }
      router.push('/login')
    } else {
      ElMessage.error(extractErrorMessage(error))
    }
    error.handled = true // 拦截器已提示，调用方无需重复弹窗
    return Promise.reject(error)
  }
)

export default request
