<template>
  <div class="admin-page">
    <el-tabs v-model="activeTab" class="admin-tabs" @tab-change="onTabChange">
      <!-- 用户管理 -->
      <el-tab-pane label="用户管理" name="users">
        <div class="toolbar">
          <el-input
            v-model="userKeyword"
            placeholder="搜索用户名 / 姓名 / 邮箱"
            clearable
            class="search-input"
            @keyup.enter="searchUsers"
            @clear="searchUsers"
          />
          <el-button type="primary" plain @click="searchUsers">搜索</el-button>
          <el-button type="primary" @click="openCreateUser">新建用户</el-button>
        </div>

        <el-table v-loading="userLoading" :data="userList" size="default">
          <el-table-column prop="username" label="用户名" min-width="120" show-overflow-tooltip />
          <el-table-column label="姓名" min-width="110" show-overflow-tooltip>
            <template #default="{ row }">{{ row.display_name || '-' }}</template>
          </el-table-column>
          <el-table-column label="邮箱" min-width="170" show-overflow-tooltip>
            <template #default="{ row }">{{ row.email || '-' }}</template>
          </el-table-column>
          <el-table-column label="部门" min-width="130" show-overflow-tooltip>
            <template #default="{ row }">{{ deptName(row.department_id) }}</template>
          </el-table-column>
          <el-table-column label="角色" min-width="150">
            <template #default="{ row }">
              <template v-if="row.roles && row.roles.length">
                <el-tag
                  v-for="r in row.roles"
                  :key="r"
                  size="small"
                  class="role-tag"
                  :type="r === 'ADMIN' ? 'danger' : 'info'"
                >
                  {{ roleLabel(r) }}
                </el-tag>
              </template>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.is_active === false ? 'danger' : 'success'">
                {{ row.is_active === false ? '禁用' : '启用' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" @click="openEditUser(row)">编辑</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination-wrap">
          <el-pagination
            v-model:current-page="userPage"
            v-model:page-size="userSize"
            :total="userTotal"
            :page-sizes="[10, 20, 50]"
            layout="total, prev, pager, next, sizes"
            small
            @current-change="loadUsers()"
            @size-change="onUserSizeChange"
          />
        </div>
      </el-tab-pane>

      <!-- 部门管理 -->
      <el-tab-pane label="部门管理" name="departments">
        <div class="toolbar">
          <el-button type="primary" @click="openCreateDept">新增部门</el-button>
          <el-button plain @click="loadAll">刷新</el-button>
        </div>

        <el-table v-loading="deptLoading" :data="departments" size="default">
          <el-table-column prop="name" label="部门名称" min-width="180" />
          <el-table-column label="上级部门" min-width="180">
            <template #default="{ row }">{{ deptName(row.parent_id) }}</template>
          </el-table-column>
          <el-table-column label="ID" min-width="220" show-overflow-tooltip>
            <template #default="{ row }">{{ row.id }}</template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 问答记录（Token 用量与反馈） -->
      <el-tab-pane label="问答记录" name="qa" lazy>
        <div class="qa-summary">
          <div class="summary-card">
            <div class="summary-value">{{ qaSummary.total_questions ?? '-' }}</div>
            <div class="summary-label">总提问数</div>
          </div>
          <div class="summary-card">
            <div class="summary-value">{{ formatNumber(qaSummary.total_tokens) }}</div>
            <div class="summary-label">总 Token</div>
            <div class="summary-sub">
              输入 {{ formatNumber(qaSummary.prompt_tokens) }} / 输出
              {{ formatNumber(qaSummary.completion_tokens) }}
            </div>
          </div>
          <div class="summary-card">
            <div class="summary-value">{{ formatNumber(qaSummary.today_tokens) }}</div>
            <div class="summary-label">今日 Token</div>
          </div>
          <div class="summary-card">
            <div class="summary-value like">{{ qaSummary.like_count ?? '-' }}</div>
            <div class="summary-label">👍 点赞</div>
          </div>
          <div class="summary-card">
            <div class="summary-value dislike">{{ qaSummary.dislike_count ?? '-' }}</div>
            <div class="summary-label">👎 点踩</div>
          </div>
        </div>

        <div class="toolbar">
          <el-select
            v-model="qaFeedback"
            placeholder="全部反馈"
            clearable
            style="width: 130px"
            @change="searchQaRecords"
          >
            <el-option label="👍 点赞" value="like" />
            <el-option label="👎 点踩" value="dislike" />
          </el-select>
          <el-input
            v-model="qaKeyword"
            placeholder="搜索回答内容"
            clearable
            class="search-input"
            @keyup.enter="searchQaRecords"
            @clear="searchQaRecords"
          />
          <el-button type="primary" plain @click="searchQaRecords">搜索</el-button>
          <el-button plain @click="loadQaRecords">刷新</el-button>
        </div>

        <el-table v-loading="qaLoading" :data="qaRecords" size="default">
          <el-table-column label="时间" width="170">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="用户" min-width="110" show-overflow-tooltip>
            <template #default="{ row }">{{ row.display_name || row.username || '-' }}</template>
          </el-table-column>
          <el-table-column label="会话" min-width="130" show-overflow-tooltip>
            <template #default="{ row }">{{ row.conversation_title || '-' }}</template>
          </el-table-column>
          <el-table-column label="问题" min-width="200" show-overflow-tooltip>
            <template #default="{ row }">{{ row.question || '-' }}</template>
          </el-table-column>
          <el-table-column label="回答" min-width="260" show-overflow-tooltip>
            <template #default="{ row }">{{ row.answer }}</template>
          </el-table-column>
          <el-table-column label="Token" width="110" align="right">
            <template #default="{ row }">
              <el-tooltip
                v-if="row.tokens?.total_tokens != null"
                :content="`输入 ${row.tokens.prompt_tokens ?? 0} / 输出 ${row.tokens.completion_tokens ?? 0}`"
                placement="top"
              >
                <span>{{ row.tokens.total_tokens }}</span>
              </el-tooltip>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column label="反馈" width="90">
            <template #default="{ row }">
              <el-tag v-if="row.feedback === 'like'" size="small" type="success">👍</el-tag>
              <el-tag v-else-if="row.feedback === 'dislike'" size="small" type="danger">👎</el-tag>
              <span v-else>-</span>
            </template>
          </el-table-column>
        </el-table>

        <div class="pagination-wrap">
          <el-pagination
            v-model:current-page="qaPage"
            v-model:page-size="qaSize"
            :total="qaTotal"
            :page-sizes="[10, 20, 50]"
            layout="total, prev, pager, next, sizes"
            small
            @current-change="loadQaRecords()"
            @size-change="onQaSizeChange"
          />
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 用户新建/编辑 dialog -->
    <el-dialog
      v-model="userDialogVisible"
      :title="userForm.id ? '编辑用户' : '新建用户'"
      width="480px"
    >
      <el-form ref="userFormRef" :model="userForm" :rules="userRules" label-width="90px">
        <el-form-item label="用户名" prop="username">
          <el-input
            v-model="userForm.username"
            :disabled="!!userForm.id"
            maxlength="50"
            placeholder="登录用户名"
          />
        </el-form-item>
        <el-form-item v-if="!userForm.id" label="密码" prop="password">
          <el-input
            v-model="userForm.password"
            type="password"
            show-password
            maxlength="100"
            placeholder="登录密码"
          />
        </el-form-item>
        <el-form-item label="姓名" prop="display_name">
          <el-input v-model="userForm.display_name" maxlength="50" placeholder="姓名（可选）" />
        </el-form-item>
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="userForm.email" maxlength="100" placeholder="邮箱（可选）" />
        </el-form-item>
        <el-form-item label="部门" prop="department_id">
          <el-select
            v-model="userForm.department_id"
            clearable
            filterable
            placeholder="选择部门（可选）"
            style="width: 100%"
          >
            <el-option v-for="d in departments" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="角色" prop="roles">
          <el-select
            v-model="userForm.roles"
            multiple
            placeholder="选择角色"
            style="width: 100%"
          >
            <el-option
              v-for="r in roleOptions"
              :key="roleValue(r)"
              :label="roleLabel(r)"
              :value="roleValue(r)"
            />
          </el-select>
        </el-form-item>
        <el-form-item v-if="userForm.id" label="启用">
          <el-switch v-model="userForm.is_active" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="userDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="userSaving" @click="saveUser">保存</el-button>
      </template>
    </el-dialog>

    <!-- 部门新增 dialog -->
    <el-dialog v-model="deptDialogVisible" title="新增部门" width="420px">
      <el-form ref="deptFormRef" :model="deptForm" :rules="deptRules" label-width="90px">
        <el-form-item label="部门名称" prop="name">
          <el-input v-model="deptForm.name" maxlength="50" placeholder="部门名称" />
        </el-form-item>
        <el-form-item label="上级部门">
          <el-select
            v-model="deptForm.parent_id"
            clearable
            filterable
            placeholder="无（作为顶级部门）"
            style="width: 100%"
          >
            <el-option v-for="d in departments" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="deptDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="deptSaving" @click="saveDept">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  createDepartment,
  createUser,
  getQaRecords,
  getQaSummary,
  listDepartments,
  listRoles,
  listUsers,
  updateUser,
} from '../api/admin'

const activeTab = ref('users')

// ---------- 用户列表 ----------
const userList = ref([])
const userTotal = ref(0)
const userPage = ref(1)
const userSize = ref(10)
const userKeyword = ref('')
const userLoading = ref(false)

const loadUsers = async () => {
  userLoading.value = true
  try {
    const data = await listUsers({
      page: userPage.value,
      size: userSize.value,
      keyword: userKeyword.value || undefined,
    })
    userList.value = data?.items || []
    userTotal.value = data?.total || 0
  } catch (err) {
    if (!err?.handled) ElMessage.error(err?.message || '加载用户列表失败')
  } finally {
    userLoading.value = false
  }
}

const searchUsers = () => {
  userPage.value = 1
  loadUsers()
}

const onUserSizeChange = () => {
  userPage.value = 1
  loadUsers()
}

// ---------- 部门 / 角色 ----------
const departments = ref([])
const deptLoading = ref(false)
const roles = ref([])
const deptMap = computed(() => {
  const map = {}
  departments.value.forEach((d) => {
    map[d.id] = d.name
  })
  return map
})
const deptName = (id) => (id && deptMap.value[id]) || (id ? id : '-')

// 角色列表兼容 [{id, code, name}] 或 ['ADMIN'] 两种形态
const roleLabel = (r) => (typeof r === 'string' ? r : r?.name || r?.code || '')
const roleValue = (r) => (typeof r === 'string' ? r : r?.code)
const roleOptions = computed(() => roles.value || [])

const loadDepartments = async () => {
  deptLoading.value = true
  try {
    const data = await listDepartments()
    departments.value = Array.isArray(data) ? data : data?.items || []
  } catch (err) {
    if (!err?.handled) ElMessage.error(err?.message || '加载部门列表失败')
  } finally {
    deptLoading.value = false
  }
}

const loadRoles = async () => {
  const data = await listRoles()
  roles.value = Array.isArray(data) ? data : data?.items || []
}

const loadAll = () => {
  loadUsers()
  loadDepartments()
  loadRoles().catch(() => {})
}

// ---------- 问答记录（Token 用量与反馈） ----------
const qaSummary = ref({})
const qaRecords = ref([])
const qaTotal = ref(0)
const qaPage = ref(1)
const qaSize = ref(10)
const qaKeyword = ref('')
const qaFeedback = ref(null)
const qaLoading = ref(false)
const qaLoaded = ref(false) // 懒加载标记：首次切到该 tab 时拉取

const formatNumber = (n) => (n == null ? '-' : Number(n).toLocaleString())

const formatTime = (iso) => {
  if (!iso) return '-'
  return String(iso).replace('T', ' ').slice(0, 19)
}

const loadQaSummary = async () => {
  try {
    qaSummary.value = (await getQaSummary()) || {}
  } catch (err) {
    if (!err?.handled) ElMessage.error(err?.message || '加载问答汇总失败')
  }
}

const loadQaRecords = async () => {
  qaLoading.value = true
  try {
    const data = await getQaRecords({
      page: qaPage.value,
      size: qaSize.value,
      feedback: qaFeedback.value || undefined,
      keyword: qaKeyword.value || undefined,
    })
    qaRecords.value = data?.items || []
    qaTotal.value = data?.total || 0
  } catch (err) {
    if (!err?.handled) ElMessage.error(err?.message || '加载问答记录失败')
  } finally {
    qaLoading.value = false
  }
}

const searchQaRecords = () => {
  qaPage.value = 1
  loadQaSummary()
  loadQaRecords()
}

const onQaSizeChange = () => {
  qaPage.value = 1
  loadQaRecords()
}

const onTabChange = (tab) => {
  if (tab === 'qa' && !qaLoaded.value) {
    qaLoaded.value = true
    loadQaSummary()
    loadQaRecords()
  }
}

// ---------- 用户新建/编辑 ----------
const userDialogVisible = ref(false)
const userSaving = ref(false)
const userFormRef = ref(null)
const userForm = reactive({
  id: '',
  username: '',
  password: '',
  display_name: '',
  email: '',
  department_id: '',
  roles: [],
  is_active: true,
})

const userRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
  roles: [
    {
      required: true,
      type: 'array',
      min: 1,
      message: '请至少选择一个角色',
      trigger: 'change',
    },
  ],
}

const openCreateUser = () => {
  userForm.id = ''
  userForm.username = ''
  userForm.password = ''
  userForm.display_name = ''
  userForm.email = ''
  userForm.department_id = ''
  userForm.roles = []
  userForm.is_active = true
  userDialogVisible.value = true
}

const openEditUser = (row) => {
  userForm.id = row.id
  userForm.username = row.username
  userForm.password = ''
  userForm.display_name = row.display_name || ''
  userForm.email = row.email || ''
  userForm.department_id = row.department_id || ''
  userForm.roles = Array.isArray(row.roles) ? [...row.roles] : []
  userForm.is_active = row.is_active !== false
  userDialogVisible.value = true
}

const saveUser = () => {
  userFormRef.value.validate(async (valid) => {
    if (!valid) return
    userSaving.value = true
    try {
      if (userForm.id) {
        // 编辑：角色 / 启用禁用 / 部门 / 基本信息
        await updateUser(userForm.id, {
          display_name: userForm.display_name || null,
          email: userForm.email || null,
          department_id: userForm.department_id || null,
          roles: userForm.roles,
          is_active: userForm.is_active,
        })
        ElMessage.success('用户已更新')
      } else {
        await createUser({
          username: userForm.username.trim(),
          password: userForm.password,
          display_name: userForm.display_name || null,
          email: userForm.email || null,
          department_id: userForm.department_id || null,
          roles: userForm.roles,
        })
        ElMessage.success('用户已创建')
      }
      userDialogVisible.value = false
      loadUsers()
    } catch (err) {
      if (!err?.handled) ElMessage.error(err?.message || '保存用户失败')
    } finally {
      userSaving.value = false
    }
  })
}

// ---------- 部门新增 ----------
const deptDialogVisible = ref(false)
const deptSaving = ref(false)
const deptFormRef = ref(null)
const deptForm = reactive({ name: '', parent_id: '' })

const deptRules = {
  name: [{ required: true, message: '请输入部门名称', trigger: 'blur' }],
}

const openCreateDept = () => {
  deptForm.name = ''
  deptForm.parent_id = ''
  deptDialogVisible.value = true
}

const saveDept = () => {
  deptFormRef.value.validate(async (valid) => {
    if (!valid) return
    deptSaving.value = true
    try {
      await createDepartment({
        name: deptForm.name.trim(),
        parent_id: deptForm.parent_id || null,
      })
      ElMessage.success('部门已创建')
      deptDialogVisible.value = false
      loadDepartments()
    } catch (err) {
      if (!err?.handled) ElMessage.error(err?.message || '创建部门失败')
    } finally {
      deptSaving.value = false
    }
  })
}

onMounted(() => {
  loadAll()
})
</script>

<style scoped>
.admin-page {
  height: 100%;
  overflow-y: auto;
  padding: 16px;
  background-color: #fff;
}

.admin-tabs {
  min-width: 0;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}

.search-input {
  width: 240px;
}

.role-tag {
  margin-right: 6px;
  margin-bottom: 2px;
}

.pagination-wrap {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}

/* 问答记录：汇总卡片 */
.qa-summary {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 14px;
}

.summary-card {
  flex: 1;
  min-width: 130px;
  padding: 12px 16px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  background: #fafbfc;
}

.summary-value {
  font-size: 22px;
  font-weight: 600;
  color: #303133;
}

.summary-value.like {
  color: #67c23a;
}

.summary-value.dislike {
  color: #f56c6c;
}

.summary-label {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}

.summary-sub {
  font-size: 11px;
  color: #c0c4cc;
  margin-top: 2px;
}

@media (max-width: 767px) {
  .admin-page {
    padding: 10px;
  }

  .search-input {
    width: 100%;
  }

  .pagination-wrap {
    justify-content: center;
  }
}
</style>
