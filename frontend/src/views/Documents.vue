<template>
  <div class="doc-page">
    <!-- 左侧知识库列表 -->
    <aside class="kb-panel">
      <div class="panel-header">
        <span class="panel-title">知识库</span>
        <el-button
          v-if="auth.canManageKb"
          type="primary"
          size="small"
          @click="openKbDialog()"
        >
          新建
        </el-button>
      </div>
      <div class="kb-scroll">
        <div
          v-for="kb in kbList"
          :key="kb.id"
          class="kb-item"
          :class="{ active: kb.id === selectedKbId }"
          @click="selectKb(kb.id)"
        >
          <div class="kb-name">{{ kb.name }}</div>
          <div class="kb-desc">{{ kb.description || '暂无描述' }}</div>
          <div class="kb-footer">
            <el-tag size="small" :type="visibilityMeta(kb.visibility).tag">
              {{ visibilityMeta(kb.visibility).label }}
            </el-tag>
            <span v-if="auth.canManageKb" class="kb-actions">
              <el-button link type="primary" size="small" @click.stop="openKbDialog(kb)">
                编辑
              </el-button>
              <el-button link type="danger" size="small" @click.stop="removeKb(kb)">
                删除
              </el-button>
            </span>
          </div>
        </div>
        <el-empty v-if="!kbList.length" description="暂无知识库" :image-size="60" />
      </div>
    </aside>

    <!-- 右侧文档列表 -->
    <section class="doc-panel">
      <div class="panel-header doc-header">
        <span class="panel-title doc-title">
          {{ selectedKb ? selectedKb.name : '文档列表' }}
        </span>
        <el-button
          size="small"
          :disabled="!selectedKbId"
          @click="openSearchDialog"
        >
          文档检索
        </el-button>
        <el-upload
          v-if="auth.canManageKb && selectedKbId"
          ref="uploadRef"
          :show-file-list="false"
          :auto-upload="false"
          multiple
          accept=".pdf,.doc,.docx,.xls,.xlsx,.txt,.md"
          :on-change="onFileChange"
        >
          <el-button type="primary" size="small" :loading="uploading">批量上传</el-button>
        </el-upload>
      </div>

      <div class="doc-table-wrap">
        <el-table v-loading="docLoading" :data="docList" height="100%" size="default">
          <el-table-column prop="filename" label="文件名" min-width="200" show-overflow-tooltip />
          <el-table-column prop="file_type" label="类型" width="80" />
          <el-table-column label="大小" width="100">
            <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tooltip
                :disabled="row.status !== 'FAILED' || !row.error_message"
                :content="row.error_message || '处理失败'"
                placement="top"
              >
                <el-tag size="small" :type="statusMeta(row.status).tag">
                  {{ statusMeta(row.status).label }}
                </el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="上传时间" width="170">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="240" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="row.status === 'READY'"
                link
                type="primary"
                @click="openPreview(row)"
              >
                预览
              </el-button>
              <el-button
                v-if="auth.canManageKb && row.status === 'FAILED'"
                link
                type="primary"
                @click="retryDoc(row)"
              >
                重试
              </el-button>
              <el-button
                v-if="auth.canManageKb"
                link
                type="primary"
                :disabled="isProcessing(row.status)"
                @click="reindexDoc(row)"
              >
                重建索引
              </el-button>
              <el-button v-if="auth.canManageKb" link type="danger" @click="deleteDoc(row)">
                删除
              </el-button>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty
              :description="selectedKbId ? '该知识库暂无文档' : '请先选择左侧知识库'"
              :image-size="80"
            />
          </template>
        </el-table>
      </div>

      <div class="doc-pagination">
        <el-pagination
          v-model:current-page="docPage"
          v-model:page-size="docSize"
          :total="docTotal"
          :page-sizes="[10, 20, 50]"
          layout="total, prev, pager, next, sizes"
          small
          @current-change="loadDocuments()"
          @size-change="onDocSizeChange"
        />
      </div>
    </section>

    <!-- 知识库新建/编辑 dialog -->
    <el-dialog
      v-model="kbDialogVisible"
      :title="kbForm.id ? '编辑知识库' : '新建知识库'"
      width="520px"
    >
      <el-form ref="kbFormRef" :model="kbForm" :rules="kbRules" label-width="110px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="kbForm.name" maxlength="100" placeholder="知识库名称" />
        </el-form-item>
        <el-form-item label="描述" prop="description">
          <el-input
            v-model="kbForm.description"
            type="textarea"
            :rows="2"
            maxlength="500"
            placeholder="知识库描述（可选）"
          />
        </el-form-item>
        <el-form-item label="可见范围" prop="visibility">
          <el-radio-group v-model="kbForm.visibility">
            <el-radio value="ALL">全部可见</el-radio>
            <el-radio value="DEPARTMENT">按部门</el-radio>
            <el-radio value="USER">指定用户</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item
          v-if="kbForm.visibility === 'DEPARTMENT'"
          label="可见部门"
          prop="department_ids"
        >
          <el-select
            v-model="kbForm.department_ids"
            multiple
            filterable
            placeholder="选择可见部门"
            style="width: 100%"
          >
            <el-option v-for="d in departments" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item
          v-if="kbForm.visibility === 'USER'"
          label="用户 ID"
          prop="user_ids_text"
        >
          <el-input
            v-model="kbForm.user_ids_text"
            type="textarea"
            :rows="3"
            placeholder="每行一个用户 ID（UUID），也可用逗号分隔"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="kbDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="kbSaving" @click="saveKb">保存</el-button>
      </template>
    </el-dialog>

    <!-- 源文档预览 dialog -->
    <el-dialog
      v-model="previewVisible"
      :title="previewDoc?.filename || '文档预览'"
      width="950px"
      top="4vh"
      destroy-on-close
      class="preview-dialog"
      @closed="cleanupPreview"
    >
      <div v-loading="previewLoading" class="preview-body">
        <template v-if="previewDoc">
          <!-- PDF：浏览器内嵌 -->
          <iframe
            v-if="previewKind === 'pdf'"
            :src="previewUrl"
            class="preview-frame"
            :title="previewDoc.filename"
          />
          <!-- docx：docx-preview 动态加载渲染 -->
          <div v-else-if="previewKind === 'docx'" class="preview-scroll">
            <div ref="docxContainerRef"></div>
          </div>
          <!-- xlsx/xls：SheetJS 动态加载解析，按 sheet 渲染表格 -->
          <div v-else-if="previewKind === 'sheet'" class="preview-scroll">
            <div v-for="sheet in sheetHtmls" :key="sheet.name" class="sheet-block">
              <div class="sheet-name">{{ sheet.name }}</div>
              <div class="sheet-html" v-html="sheet.html"></div>
            </div>
          </div>
          <!-- txt -->
          <pre v-else-if="previewKind === 'text'" class="preview-text">{{ previewText }}</pre>
          <!-- md -->
          <div v-else-if="previewKind === 'markdown'" class="preview-md" v-html="mdHtml"></div>
          <!-- doc 老格式：不支持在线预览 -->
          <div v-else class="preview-unsupported">
            <el-empty description="该格式不支持在线预览，请下载后查看" :image-size="80">
              <el-button type="primary" @click="downloadDoc(previewDoc)">下载文件</el-button>
            </el-empty>
          </div>
        </template>
      </div>
      <template #footer>
        <el-button @click="downloadDoc(previewDoc)">下载文件</el-button>
        <el-button type="primary" @click="previewVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 文档检索 dialog -->
    <el-dialog v-model="searchVisible" title="文档检索" width="820px" top="5vh">
      <el-form inline @submit.prevent="doSearch">
        <el-form-item>
          <el-input
            v-model="searchForm.query"
            placeholder="输入检索关键词"
            clearable
            style="width: 380px"
            @keyup.enter="doSearch"
          />
        </el-form-item>
        <el-form-item>
          <el-select v-model="searchForm.top_k" style="width: 120px">
            <el-option
              v-for="n in [5, 10, 20, 50]"
              :key="n"
              :label="`返回 ${n} 条`"
              :value="n"
            />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="searching" @click="doSearch">检索</el-button>
        </el-form-item>
      </el-form>
      <div class="search-tip">
        BM25（关键词）与向量双路召回，RRF 倒数排名融合排序；同时命中两路的结果权重叠加
      </div>

      <div v-loading="searching" class="search-body">
        <el-empty
          v-if="!searching && !searchResults.length"
          description="暂无结果"
          :image-size="70"
        />
        <div v-for="(item, idx) in searchResults" :key="idx" class="search-item">
          <div class="search-item-head">
            <el-tag size="small" type="success">RRF {{ item.score.toFixed(4) }}</el-tag>
            <el-tag
              v-if="item.vector_score != null"
              size="small"
              type="primary"
              effect="plain"
            >
              向量 {{ item.vector_score.toFixed(4) }}
            </el-tag>
            <el-tag
              v-if="item.keyword_score != null"
              size="small"
              type="warning"
              effect="plain"
            >
              BM25 {{ item.keyword_score.toFixed(4) }}
            </el-tag>
            <span class="search-doc">{{ item.document_name || '未知文档' }}</span>
            <span class="search-meta">
              {{ item.metadata.title_path || '—' }}
              <template v-if="item.metadata.page_number != null">
                · 第 {{ item.metadata.page_number }} 页
              </template>
              · #{{ item.metadata.chunk_index }}
            </span>
          </div>
          <div class="search-content" :class="{ expanded: expandedIdx === idx }">
            {{ item.content }}
          </div>
          <el-button
            v-if="item.content.length > 200"
            link
            type="primary"
            size="small"
            @click="expandedIdx = expandedIdx === idx ? -1 : idx"
          >
            {{ expandedIdx === idx ? '收起' : '展开全文' }}
          </el-button>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '../stores/auth'
import { createKb, deleteKb, listKb, updateKb } from '../api/kb'
import {
  deleteDocument,
  fetchPreviewBlob,
  listDocuments,
  previewDownloadUrl,
  reindexDocument,
  retryDocument,
  uploadDocuments,
} from '../api/document'
import { searchChunks } from '../api/retrieval'
import { listDepartments } from '../api/admin'

const auth = useAuthStore()

// ---------- 知识库列表 ----------
const kbList = ref([])
const selectedKbId = ref('')
const selectedKb = computed(
  () => kbList.value.find((k) => k.id === selectedKbId.value) || null
)

const departments = ref([])

const VISIBILITY_META = {
  ALL: { label: '全部可见', tag: 'success' },
  DEPARTMENT: { label: '按部门', tag: 'warning' },
  USER: { label: '指定用户', tag: 'info' },
}
const visibilityMeta = (v) => VISIBILITY_META[v] || { label: v || '-', tag: 'info' }

const STATUS_META = {
  READY: { label: '就绪', tag: 'success' },
  PARSING: { label: '解析中', tag: 'warning' },
  CHUNKING: { label: '切分中', tag: 'warning' },
  EMBEDDING: { label: '向量化中', tag: 'warning' },
  FAILED: { label: '失败', tag: 'danger' },
  UPLOADED: { label: '已上传', tag: 'info' },
}
const statusMeta = (s) => STATUS_META[s] || { label: s || '-', tag: 'info' }

const PROCESSING_STATUSES = ['UPLOADED', 'PARSING', 'CHUNKING', 'EMBEDDING']
const isProcessing = (s) => PROCESSING_STATUSES.includes(s)

const formatSize = (bytes) => {
  if (bytes == null) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

const formatTime = (iso) => {
  if (!iso) return '-'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// ---------- 文档列表 + 轮询 ----------
const docList = ref([])
const docTotal = ref(0)
const docPage = ref(1)
const docSize = ref(20)
const docLoading = ref(false)
const uploading = ref(false)

let pollTimer = null
const stopPoll = () => {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}
// 存在处理中/待处理文档时每 5 秒轮询刷新
const schedulePoll = () => {
  stopPoll()
  if (!selectedKbId.value) return
  if (docList.value.some((d) => isProcessing(d.status))) {
    pollTimer = setTimeout(async () => {
      await loadDocuments({ silent: true })
      schedulePoll()
    }, 5000)
  }
}

const loadKbList = async () => {
  kbList.value = (await listKb()) || []
  if (selectedKbId.value && !kbList.value.some((k) => k.id === selectedKbId.value)) {
    selectedKbId.value = ''
    docList.value = []
    docTotal.value = 0
  }
}

const selectKb = (id) => {
  if (id === selectedKbId.value) return
  selectedKbId.value = id
  docPage.value = 1
  loadDocuments()
}

const loadDocuments = async ({ silent } = {}) => {
  if (!selectedKbId.value) {
    docList.value = []
    docTotal.value = 0
    schedulePoll()
    return
  }
  if (!silent) docLoading.value = true
  try {
    const data = await listDocuments({
      kb_id: selectedKbId.value,
      page: docPage.value,
      size: docSize.value,
    })
    docList.value = data?.items || []
    docTotal.value = data?.total || 0
  } catch (err) {
    if (!err?.handled) ElMessage.error(err?.message || '加载文档列表失败')
  } finally {
    if (!silent) docLoading.value = false
  }
  schedulePoll()
}

const onDocSizeChange = () => {
  docPage.value = 1
  loadDocuments()
}

// ---------- 批量上传 ----------
const uploadRef = ref(null)
let uploadDebounce = null

// 多选文件时 on-change 逐个触发，去抖收集后统一一次上传
const onFileChange = (file, fileList) => {
  clearTimeout(uploadDebounce)
  uploadDebounce = setTimeout(() => {
    const files = (fileList || []).map((f) => f.raw).filter(Boolean)
    uploadRef.value?.clearFiles()
    if (files.length) doUpload(files)
  }, 120)
}

const doUpload = async (files) => {
  uploading.value = true
  const fd = new FormData()
  fd.append('kb_id', selectedKbId.value)
  files.forEach((f) => fd.append('files', f, f.name))
  try {
    const results = (await uploadDocuments(fd)) || []
    const okCount = results.filter((r) => r.success).length
    const failList = results.filter((r) => !r.success)
    if (okCount) ElMessage.success(`${okCount} 个文件上传成功，正在解析`)
    failList.forEach((r) =>
      ElMessage.error(`${r.filename || '未知文件'}：${r.reason || '上传失败'}`)
    )
    docPage.value = 1
    await loadDocuments()
  } catch (err) {
    if (!err?.handled) ElMessage.error(err?.message || '上传失败')
  } finally {
    uploading.value = false
  }
}

// ---------- 知识库新建/编辑/删除 ----------
const kbDialogVisible = ref(false)
const kbSaving = ref(false)
const kbFormRef = ref(null)
const kbForm = reactive({
  id: '',
  name: '',
  description: '',
  visibility: 'ALL',
  department_ids: [],
  user_ids_text: '',
})

const kbRules = {
  name: [{ required: true, message: '请输入知识库名称', trigger: 'blur' }],
  visibility: [{ required: true, message: '请选择可见范围', trigger: 'change' }],
}

const loadDepartments = async () => {
  const data = await listDepartments()
  departments.value = Array.isArray(data) ? data : data?.items || []
}

const openKbDialog = (kb) => {
  kbForm.id = kb?.id || ''
  kbForm.name = kb?.name || ''
  kbForm.description = kb?.description || ''
  kbForm.visibility = kb?.visibility || 'ALL'
  kbForm.department_ids = kb?.department_ids ? [...kb.department_ids] : []
  kbForm.user_ids_text = kb?.user_ids?.length ? kb.user_ids.join('\n') : ''
  kbDialogVisible.value = true
  if (!departments.value.length) {
    loadDepartments().catch(() => {}) // 部门下拉数据（接口失败不阻塞弹窗）
  }
}

const saveKb = () => {
  kbFormRef.value.validate(async (valid) => {
    if (!valid) return
    if (kbForm.visibility === 'DEPARTMENT' && !kbForm.department_ids.length) {
      ElMessage.warning('请选择至少一个可见部门')
      return
    }
    const payload = {
      name: kbForm.name.trim(),
      description: kbForm.description || null,
      visibility: kbForm.visibility,
    }
    if (kbForm.visibility === 'DEPARTMENT') {
      payload.department_ids = kbForm.department_ids
    }
    if (kbForm.visibility === 'USER') {
      const ids = kbForm.user_ids_text
        .split(/[\s,;，；]+/)
        .map((s) => s.trim())
        .filter(Boolean)
      if (!ids.length) {
        ElMessage.warning('请输入至少一个用户 ID')
        return
      }
      payload.user_ids = ids
    }

    kbSaving.value = true
    try {
      if (kbForm.id) {
        await updateKb(kbForm.id, payload)
        ElMessage.success('知识库已更新')
      } else {
        await createKb(payload)
        ElMessage.success('知识库已创建')
      }
      kbDialogVisible.value = false
      await loadKbList()
      if (!selectedKbId.value && kbList.value.length) selectKb(kbList.value[0].id)
    } catch (err) {
      if (!err?.handled) ElMessage.error(err?.message || '保存知识库失败')
    } finally {
      kbSaving.value = false
    }
  })
}

const removeKb = async (kb) => {
  try {
    await ElMessageBox.confirm(
      `删除知识库「${kb.name}」将级联删除其下全部文档与向量数据，确定删除？`,
      '删除知识库',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    await deleteKb(kb.id)
    ElMessage.success('知识库已删除')
    if (kb.id === selectedKbId.value) {
      selectedKbId.value = ''
      docList.value = []
      docTotal.value = 0
      stopPoll()
    }
    loadKbList().catch(() => {})
  } catch (err) {
    if (!err?.handled) ElMessage.error(err?.message || '删除知识库失败')
  }
}

// ---------- 文档操作 ----------
const retryDoc = async (row) => {
  try {
    await retryDocument(row.id)
    ElMessage.success('已重新提交解析')
    loadDocuments()
  } catch (err) {
    if (!err?.handled) ElMessage.error(err?.message || '重试失败')
  }
}

const reindexDoc = async (row) => {
  try {
    await reindexDocument(row.id)
    ElMessage.success('已提交重建索引')
    loadDocuments()
  } catch (err) {
    if (!err?.handled) ElMessage.error(err?.message || '重建索引失败')
  }
}

const deleteDoc = async (row) => {
  try {
    await ElMessageBox.confirm(
      `确定删除文档「${row.filename}」？删除后不可恢复。`,
      '删除文档',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    await deleteDocument(row.id)
    ElMessage.success('文档已删除')
    if (docList.value.length === 1 && docPage.value > 1) docPage.value -= 1
    loadDocuments()
  } catch (err) {
    if (!err?.handled) ElMessage.error(err?.message || '删除文档失败')
  }
}

// ---------- 源文档在线预览（docx/xlsx 预览库按需动态加载） ----------
const previewVisible = ref(false)
const previewLoading = ref(false)
const previewDoc = ref(null)
const previewKind = ref('') // pdf | docx | sheet | text | markdown | unsupported
const previewUrl = ref('')
const previewText = ref('')
const mdHtml = ref('')
const sheetHtmls = ref([])
const docxContainerRef = ref(null)

const cleanupPreview = () => {
  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value)
    previewUrl.value = ''
  }
  previewText.value = ''
  mdHtml.value = ''
  sheetHtmls.value = []
  previewDoc.value = null
}

const extToKind = (filename = '') => {
  const ext = filename.split('.').pop().toLowerCase()
  if (ext === 'pdf') return 'pdf'
  if (ext === 'docx') return 'docx'
  if (ext === 'xlsx' || ext === 'xls') return 'sheet'
  if (ext === 'txt') return 'text'
  if (ext === 'md') return 'markdown'
  return 'unsupported' // doc 等老格式
}

const openPreview = async (row) => {
  previewDoc.value = row
  previewKind.value = extToKind(row.filename)
  previewLoading.value = true
  previewVisible.value = true
  try {
    const { url, blob } = await fetchPreviewBlob(row.id)
    previewUrl.value = url

    if (previewKind.value === 'text') {
      previewText.value = await blob.text()
    } else if (previewKind.value === 'markdown') {
      const text = await blob.text()
      const { default: MarkdownIt } = await import('markdown-it')
      mdHtml.value = new MarkdownIt({ breaks: true }).render(text)
    } else if (previewKind.value === 'docx') {
      // 动态加载 docx-preview（Vite 代码分割，不进主包）
      const { renderAsync } = await import('docx-preview')
      await nextTick() // 等 dialog 内容挂载后再渲染
      if (docxContainerRef.value) {
        await renderAsync(blob, docxContainerRef.value, undefined, {
          inWrapper: false,
          ignoreLastRenderedPageBreak: false,
        })
      }
    } else if (previewKind.value === 'sheet') {
      // 动态加载 SheetJS
      const XLSX = await import('xlsx')
      const buf = await blob.arrayBuffer()
      const wb = XLSX.read(buf, { type: 'array' })
      sheetHtmls.value = wb.SheetNames.map((name) => ({
        name,
        html: XLSX.utils.sheet_to_html(wb.Sheets[name]),
      }))
    }
  } catch (err) {
    previewVisible.value = false
    if (!err?.handled) ElMessage.error(err?.message || '加载预览失败')
  } finally {
    previewLoading.value = false
  }
}

const downloadDoc = (row) => {
  if (!row?.id) return
  window.open(previewDownloadUrl(row.id), '_blank')
}

// ---------- 文档检索（BM25 + 向量双路召回，RRF 融合排序） ----------
const searchVisible = ref(false)
const searching = ref(false)
const searchForm = reactive({ query: '', top_k: 10 })
const searchResults = ref([])
const expandedIdx = ref(-1)

const openSearchDialog = () => {
  searchVisible.value = true
  if (searchForm.query.trim()) doSearch()
}

const doSearch = async () => {
  const query = searchForm.query.trim()
  if (!query) {
    ElMessage.warning('请输入检索关键词')
    return
  }
  searching.value = true
  expandedIdx.value = -1
  try {
    const data = await searchChunks({
      query,
      kb_ids: [selectedKbId.value],
      top_k: searchForm.top_k,
    })
    searchResults.value = data?.items || []
  } catch (err) {
    if (!err?.handled) ElMessage.error(err?.message || '检索失败')
  } finally {
    searching.value = false
  }
}

onMounted(async () => {
  await loadKbList().catch(() => {})
  if (!selectedKbId.value && kbList.value.length) selectKb(kbList.value[0].id)
  else loadDocuments()
})

onBeforeUnmount(() => {
  stopPoll()
  clearTimeout(uploadDebounce)
  cleanupPreview()
})
</script>

<style scoped>
.doc-page {
  display: flex;
  height: 100%;
  min-width: 0;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid #e4e7ed;
  flex-shrink: 0;
  gap: 8px;
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 左侧知识库 */
.kb-panel {
  width: 280px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background-color: #fff;
  border-right: 1px solid #e4e7ed;
  min-height: 0;
}

.kb-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 10px;
}

.kb-item {
  padding: 10px 12px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  margin-bottom: 10px;
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.kb-item:hover {
  border-color: #409eff;
}

.kb-item.active {
  border-color: #409eff;
  box-shadow: 0 0 0 1px rgba(64, 158, 255, 0.25) inset;
}

.kb-name {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kb-desc {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.kb-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}

.kb-actions {
  display: inline-flex;
  gap: 2px;
}

/* 右侧文档 */
.doc-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.doc-header {
  background-color: #fff;
}

.doc-title {
  flex: 1;
}

.doc-table-wrap {
  flex: 1;
  min-height: 0;
  padding: 0;
}

.doc-pagination {
  display: flex;
  justify-content: flex-end;
  padding: 10px 14px;
  background-color: #fff;
  border-top: 1px solid #e4e7ed;
  flex-shrink: 0;
}

/* 预览 dialog */
.preview-body {
  height: 72vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.preview-frame {
  width: 100%;
  height: 100%;
  border: none;
}

.preview-scroll {
  flex: 1;
  overflow: auto;
  background: #fafafa;
  padding: 12px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

.preview-text {
  flex: 1;
  overflow: auto;
  margin: 0;
  padding: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: Consolas, Menlo, monospace;
  font-size: 13px;
  line-height: 1.7;
  background: #fafafa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

.preview-md {
  flex: 1;
  overflow: auto;
  padding: 12px;
  background: #fafafa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

.preview-unsupported {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.sheet-block {
  margin-bottom: 18px;
}

.sheet-name {
  font-size: 13px;
  font-weight: 600;
  color: #606266;
  margin-bottom: 6px;
}

.sheet-html :deep(table) {
  border-collapse: collapse;
  font-size: 12px;
  background: #fff;
}

.sheet-html :deep(td) {
  border: 1px solid #dcdfe6;
  padding: 4px 8px;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 检索 dialog */
.search-body {
  min-height: 200px;
  max-height: 64vh;
  overflow: auto;
}

.search-tip {
  font-size: 12px;
  color: #909399;
  margin-bottom: 10px;
}

.search-item {
  padding: 10px 12px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  margin-bottom: 10px;
}

.search-item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.search-doc {
  font-size: 13px;
  font-weight: 600;
  color: #303133;
}

.search-meta {
  font-size: 12px;
  color: #909399;
}

.search-content {
  margin-top: 8px;
  font-size: 13px;
  line-height: 1.7;
  color: #606266;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  white-space: pre-wrap;
  word-break: break-word;
}

.search-content.expanded {
  display: block;
  -webkit-line-clamp: unset;
}

/* 移动端：上下堆叠 */
@media (max-width: 767px) {
  .doc-page {
    flex-direction: column;
    overflow-y: auto;
  }

  .kb-panel {
    width: 100%;
    flex-shrink: 0;
    border-right: none;
    border-bottom: 1px solid #e4e7ed;
  }

  .kb-scroll {
    max-height: 240px;
  }

  .doc-panel {
    flex: 1;
    min-height: 420px;
  }

  .doc-table-wrap :deep(.el-table) {
    height: auto !important;
  }

  .doc-pagination {
    justify-content: center;
  }
}
</style>
