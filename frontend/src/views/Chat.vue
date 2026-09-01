<template>
  <div class="chat-page">
    <!-- 桌面端会话侧栏 -->
    <aside class="chat-sidebar desktop-only">
      <ConversationList
        :conversations="conversations"
        :current-id="currentId"
        :disabled="sending"
        @create="onCreateConversation"
        @select="selectConversation"
        @delete="removeConversation"
      />
    </aside>

    <!-- 移动端会话抽屉 -->
    <el-drawer v-model="convDrawerVisible" direction="ltr" size="80%" :with-header="false">
      <div class="mobile-conv-panel">
        <ConversationList
          :conversations="conversations"
          :current-id="currentId"
          :disabled="sending"
          @create="onCreateConversation(); convDrawerVisible = false"
          @select="onMobileSelect"
          @delete="removeConversation"
        />
      </div>
    </el-drawer>

    <!-- 聊天主区 -->
    <section class="chat-main">
      <div class="chat-toolbar">
        <el-button class="mobile-only" size="small" @click="convDrawerVisible = true">
          会话列表
        </el-button>
        <span class="chat-title">{{ currentTitle || '新对话' }}</span>
        <el-button
          size="small"
          type="primary"
          plain
          :disabled="sending"
          @click="onCreateConversation"
        >
          新会话
        </el-button>
      </div>

      <div ref="messagesRef" class="messages">
        <template v-if="messages.length">
          <div
            v-for="(msg, i) in messages"
            :key="msg.id || `local-${i}`"
            class="msg-row"
            :class="msg.role"
          >
            <div
              class="msg-bubble"
              :class="[msg.role, { streaming: msg.streaming, failed: msg.failed }]"
            >
              <template v-if="msg.role === 'assistant'">
                <!-- 等待状态：检索/生成期间交替展示，首个增量到达后消失 -->
                <div v-if="msg.streaming && msg.status && !msg.content" class="thinking-status">
                  <span class="thinking-spinner"></span>{{ msg.status }}
                </div>
                <div
                  class="md-body"
                  v-html="renderMarkdown(msg.content)"
                  @click="onCiteClick($event, msg)"
                ></div>
                <!-- 引用来源：仅在流式输出结束后展示，且只显示正文中实际引用的资料 -->
                <el-collapse
                  v-if="!msg.streaming && displaySources(msg).length"
                  class="sources-collapse"
                >
                  <el-collapse-item
                    :title="`引用来源（${displaySources(msg).length}）`"
                    name="sources"
                  >
                    <div
                      v-for="s in displaySources(msg)"
                      :key="s.index ?? s.document_name"
                      class="source-item"
                      :class="{ clickable: !!s.content }"
                      @click="openSource(s)"
                    >
                      <el-tag size="small" type="info" class="source-idx">
                        资料{{ s.index ?? '?' }}
                      </el-tag>
                      <div class="source-main">
                        <div class="source-name">{{ s.document_name || '未知文档' }}</div>
                        <div class="source-meta">
                          <span v-if="s.title_path" class="source-path">{{ s.title_path }}</span>
                          <span v-if="s.page_number != null" class="source-page">
                            第 {{ s.page_number }} 页
                          </span>
                          <span v-if="s.content" class="source-view">查看引用内容</span>
                        </div>
                      </div>
                    </div>
                  </el-collapse-item>
                </el-collapse>
                <!-- 回答反馈：点赞 / 点踩（流式结束后、且消息已落库时展示） -->
                <div v-if="!msg.streaming && msg.id" class="feedback-bar">
                  <button
                    class="feedback-btn"
                    :class="{ active: msg.feedback === 'like' }"
                    title="有用"
                    :disabled="msg.feedbackLoading"
                    @click="onFeedback(msg, 'like')"
                  >
                    👍
                  </button>
                  <button
                    class="feedback-btn"
                    :class="{ active: msg.feedback === 'dislike' }"
                    title="没用"
                    :disabled="msg.feedbackLoading"
                    @click="onFeedback(msg, 'dislike')"
                  >
                    👎
                  </button>
                </div>
              </template>
              <template v-else>{{ msg.content }}</template>
            </div>
          </div>
        </template>
        <el-empty v-else description="输入问题，开始向知识库提问" />
      </div>

      <div class="chat-input">
        <el-input
          v-model="input"
          type="textarea"
          :rows="2"
          resize="none"
          maxlength="4000"
          placeholder="输入问题，Enter 发送，Shift + Enter 换行"
          @keydown="onInputKeydown"
        />
        <el-button
          type="primary"
          class="send-btn"
          :loading="sending"
          :disabled="!input.trim()"
          @click="handleSend"
        >
          {{ sending ? '回答中' : '发送' }}
        </el-button>
      </div>
    </section>

    <!-- 引用内容查看 dialog -->
    <el-dialog
      v-model="citationVisible"
      :title="citationSource?.document_name || '引用内容'"
      width="640px"
      top="8vh"
    >
      <div class="citation-meta" v-if="citationSource">
        <el-tag size="small" type="info">资料{{ citationSource.index }}</el-tag>
        <span v-if="citationSource.title_path">{{ citationSource.title_path }}</span>
        <span v-if="citationSource.page_number != null">
          第 {{ citationSource.page_number }} 页
        </span>
      </div>
      <div v-if="citationSource?.content" class="citation-content">
        {{ citationSource.content }}
      </div>
      <el-empty
        v-else
        description="该回答产生于旧版本，未保存引用原文"
        :image-size="70"
      />
      <template #footer>
        <el-button
          v-if="citationSource?.document_id"
          type="primary"
          @click="openSourceDoc(citationSource.document_id)"
        >
          查看源文档
        </el-button>
        <el-button @click="citationVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import MarkdownIt from 'markdown-it'
import ConversationList from '../components/ConversationList.vue'
import { chatStream, setMessageFeedback } from '../api/qa'
import {
  createConversation,
  deleteConversation,
  getConversation,
  listConversations,
} from '../api/conversation'

const md = new MarkdownIt({ breaks: true, linkify: true })

const conversations = ref([])
const currentId = ref('')
const currentTitle = ref('')
const messages = ref([])
const input = ref('')
const sending = ref(false)
const messagesRef = ref(null)
const convDrawerVisible = ref(false)

const renderMarkdown = (text) =>
  md
    .render(text || '')
    .replace(
      /\[资料(\d+)\]/g,
      '<sup class="cite-ref" data-index="$1">[资料$1]</sup>'
    )

// 等待状态文案（提问后交替展示，首个增量到达后停止）
const STATUS_MESSAGES = [
  '正在查找相关资料，请稍候…',
  '马上就好，正在整理答案…',
  '快了，再等等…',
]
let statusTimer = null
const stopStatus = () => {
  if (statusTimer) {
    clearInterval(statusTimer)
    statusTimer = null
  }
}
const startStatus = (assistant) => {
  let i = 0
  assistant.status = STATUS_MESSAGES[0]
  statusTimer = setInterval(() => {
    i = (i + 1) % STATUS_MESSAGES.length
    assistant.status = STATUS_MESSAGES[i]
  }, 2000)
}

// 引用来源展示：只显示正文中实际引用的 [资料N]；旧消息（无 index）原样显示
const displaySources = (msg) => {
  const sources = msg.sources || []
  return sources.filter((s) => {
    if (s.index == null) return true
    return msg.content?.includes(`[资料${s.index}]`)
  })
}

// 点击正文中的 [资料N] 上标 → 打开对应引用内容
const onCiteClick = (event, msg) => {
  const el = event.target.closest('.cite-ref')
  if (!el) return
  const index = Number(el.dataset.index)
  const source = (msg.sources || []).find((s) => s.index === index)
  if (source?.content) {
    citationSource.value = source
    citationVisible.value = true
  }
}

// ---------- 回答反馈（点赞/点踩） ----------
// 已选中再点 = 取消；切换 = 直接改。乐观更新，失败回滚。
const onFeedback = async (msg, value) => {
  if (!msg.id || msg.feedbackLoading) return
  const next = msg.feedback === value ? null : value
  const prev = msg.feedback
  msg.feedback = next
  msg.feedbackLoading = true
  try {
    await setMessageFeedback(msg.id, next)
  } catch (err) {
    msg.feedback = prev
    if (!err?.handled) ElMessage.error(err?.message || '提交评价失败')
  } finally {
    msg.feedbackLoading = false
  }
}

// ---------- 引用来源点击查看 ----------
const citationVisible = ref(false)
const citationSource = ref(null)

const openSource = (source) => {
  if (!source?.content) return // 旧消息无引用原文，不可点击
  citationSource.value = source
  citationVisible.value = true
}

const openSourceDoc = (documentId) => {
  // 源文档预览（inline）：PDF/TXT/MD 新标签页打开，docx 等由浏览器下载
  window.open(`/api/documents/${documentId}/preview`, '_blank')
}

const scrollToBottom = () => {
  nextTick(() => {
    const el = messagesRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

const refreshConversations = async () => {
  const data = await listConversations({ page: 1, size: 50 })
  conversations.value = data?.items || []
}

const selectConversation = async (id) => {
  if (sending.value || id === currentId.value) return
  try {
    const conv = await getConversation(id)
    currentId.value = conv.id
    currentTitle.value = conv.title
    messages.value = (conv.messages || []).map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content,
      sources: m.sources || null,
      streaming: false,
      failed: false,
    }))
    scrollToBottom()
  } catch (err) {
    if (!err?.handled) ElMessage.error(err?.message || '加载会话失败')
  }
}

const onMobileSelect = (id) => {
  convDrawerVisible.value = false
  selectConversation(id)
}

const onCreateConversation = () => {
  if (sending.value) return
  currentId.value = ''
  currentTitle.value = ''
  messages.value = []
  input.value = ''
}

const removeConversation = async (id) => {
  try {
    await ElMessageBox.confirm('删除后该会话及全部消息不可恢复，确定删除？', '删除会话', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户取消
  }
  try {
    await deleteConversation(id)
    ElMessage.success('会话已删除')
    if (id === currentId.value) {
      currentId.value = ''
      currentTitle.value = ''
      messages.value = []
    }
    refreshConversations().catch(() => {})
  } catch (err) {
    if (!err?.handled) ElMessage.error(err?.message || '删除会话失败')
  }
}

// Enter 发送、Shift+Enter 换行；IME 组合输入期间回车不触发发送
const onInputKeydown = (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    if (e.isComposing) return
    e.preventDefault()
    handleSend()
  }
}

const handleSend = async () => {
  const question = input.value.trim()
  if (!question || sending.value) return
  sending.value = true
  input.value = ''

  // 立即渲染 user 消息 + assistant 占位气泡
  messages.value.push({
    id: null,
    role: 'user',
    content: question,
    sources: null,
    streaming: false,
    failed: false,
  })
  const assistant = reactive({
    id: null,
    role: 'assistant',
    content: '',
    sources: null,
    feedback: null,
    streaming: true,
    failed: false,
    status: '',
  })
  messages.value.push(assistant)
  scrollToBottom()
  startStatus(assistant)

  try {
    // 未创建会话时首问自动建会话（标题取问题前 20 字）并刷新列表
    if (!currentId.value) {
      const conv = await createConversation(question.slice(0, 20))
      currentId.value = conv.id
      currentTitle.value = conv.title
      refreshConversations().catch(() => {})
    }

    await chatStream({
      question,
      conversation_id: currentId.value,
      onEvent: (event, data) => {
        if (event === 'sources') {
          // 检索完成，暂存引用来源（流式结束后再展示）
          assistant.sources = Array.isArray(data) ? data : []
        } else if (event === 'delta') {
          if (!assistant.content) stopStatus() // 首个增量到达，停止等待状态
          assistant.content += data?.content || ''
          scrollToBottom()
        } else if (event === 'done') {
          stopStatus()
          assistant.status = ''
          assistant.streaming = false
          assistant.id = data?.message_id || null
          // 兜底：后端自动创建的会话（正常情况下上方已创建）
          if (data?.conversation_id && !currentId.value) {
            currentId.value = data.conversation_id
            refreshConversations().catch(() => {})
          }
        } else if (event === 'error') {
          stopStatus()
          assistant.status = ''
          assistant.streaming = false
          assistant.failed = true
          if (!assistant.content) {
            assistant.content = data?.message || '生成回答失败，请稍后重试'
          }
          ElMessage.error(data?.message || '生成回答失败，请稍后重试')
        }
      },
    })
    stopStatus()
    assistant.status = ''
    assistant.streaming = false
    // 流结束刷新列表（updated_at / last_message 变化）
    refreshConversations().catch(() => {})
  } catch (err) {
    stopStatus()
    assistant.status = ''
    assistant.streaming = false
    assistant.failed = true
    if (!assistant.content) {
      assistant.content = err?.message || '发送失败，请稍后重试'
    }
    if (!err?.handled) ElMessage.error(err?.message || '发送失败，请稍后重试')
  } finally {
    sending.value = false
    scrollToBottom()
  }
}

onMounted(() => {
  refreshConversations().catch(() => {})
})

onBeforeUnmount(() => {
  stopStatus()
})
</script>

<style scoped>
.chat-page {
  display: flex;
  height: 100%;
  min-width: 0;
}

.chat-sidebar {
  width: 260px;
  flex-shrink: 0;
  background-color: #001529;
}

.mobile-conv-panel {
  height: 100%;
  background-color: #001529;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background-color: #f5f7fa;
}

.chat-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  background-color: #fff;
  border-bottom: 1px solid #e4e7ed;
  flex-shrink: 0;
}

.chat-title {
  flex: 1;
  min-width: 0;
  font-size: 15px;
  font-weight: 600;
  color: #303133;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.msg-row {
  display: flex;
  margin-bottom: 14px;
}

.msg-row.user {
  justify-content: flex-end;
}

.msg-row.assistant {
  justify-content: flex-start;
}

.msg-bubble {
  max-width: 78%;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.7;
  word-break: break-word;
  overflow-wrap: anywhere;
}

.msg-bubble.user {
  background-color: #409eff;
  color: #fff;
  border-top-right-radius: 2px;
  white-space: pre-wrap;
}

.msg-bubble.assistant {
  background-color: #fff;
  color: #303133;
  border-top-left-radius: 2px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
}

.msg-bubble.failed {
  border: 1px solid #f56c6c;
  color: #c45656;
}

/* 流式打字光标 */
.msg-bubble.streaming .md-body > :last-child::after {
  content: '▍';
  color: #409eff;
  animation: cursor-blink 1s infinite;
}

@keyframes cursor-blink {
  50% {
    opacity: 0;
  }
}

/* markdown 渲染样式（v-html 内容需 :deep） */
.md-body :deep(p) {
  margin: 0 0 8px;
}

.md-body :deep(p:last-child) {
  margin-bottom: 0;
}

.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3),
.md-body :deep(h4) {
  margin: 10px 0 6px;
  font-size: 15px;
  font-weight: 600;
}

.md-body :deep(ul),
.md-body :deep(ol) {
  margin: 8px 0;
  padding-left: 20px;
}

.md-body :deep(code) {
  background-color: #f0f2f5;
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 13px;
  font-family: Consolas, Monaco, 'Courier New', monospace;
}

.md-body :deep(pre) {
  background-color: #f6f8fa;
  padding: 10px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 8px 0;
}

.md-body :deep(pre code) {
  background-color: transparent;
  padding: 0;
}

.md-body :deep(blockquote) {
  margin: 8px 0;
  padding: 2px 10px;
  border-left: 3px solid #dcdfe6;
  color: #909399;
}

.md-body :deep(table) {
  border-collapse: collapse;
  margin: 8px 0;
}

.md-body :deep(th),
.md-body :deep(td) {
  border: 1px solid #dcdfe6;
  padding: 4px 10px;
}

.md-body :deep(a) {
  color: #409eff;
}

.md-body :deep(hr) {
  border: none;
  border-top: 1px solid #e4e7ed;
  margin: 8px 0;
}

/* 引用来源折叠面板 */
.sources-collapse {
  margin-top: 8px;
  border-top: 1px dashed #e4e7ed;
}

/* 回答反馈（点赞/点踩） */
.feedback-bar {
  margin-top: 6px;
  display: flex;
  gap: 6px;
}

.feedback-btn {
  border: none;
  background: transparent;
  font-size: 14px;
  line-height: 1;
  padding: 3px 6px;
  border-radius: 4px;
  cursor: pointer;
  opacity: 0.45;
  transition: opacity 0.15s, background 0.15s;
}

.feedback-btn:hover {
  opacity: 0.85;
  background: rgba(0, 0, 0, 0.05);
}

.feedback-btn.active {
  opacity: 1;
  background: rgba(64, 158, 255, 0.12);
}

.feedback-btn:disabled {
  cursor: default;
  opacity: 0.45;
}

.sources-collapse :deep(.el-collapse-item__header) {
  height: 32px;
  line-height: 32px;
  font-size: 12px;
  color: #909399;
  border-bottom: none;
}

.sources-collapse :deep(.el-collapse-item__wrap) {
  border-bottom: none;
}

.sources-collapse :deep(.el-collapse-item__content) {
  padding-bottom: 4px;
}

.source-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 4px 0;
  border-bottom: 1px dashed #f0f0f0;
}

.source-idx {
  flex-shrink: 0;
  margin-top: 1px;
}

.source-main {
  flex: 1;
  min-width: 0;
}

.source-item:last-child {
  border-bottom: none;
}

/* 可点击查看引用内容的来源项 */
.source-item.clickable {
  cursor: pointer;
  border-radius: 6px;
  transition: background-color 0.15s;
}

.source-item.clickable:hover {
  background-color: #f5f7fa;
}

.source-view {
  color: #409eff;
  font-size: 12px;
  margin-left: auto;
}

.source-item.clickable .source-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 引用内容 dialog */
.citation-meta {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: #909399;
  margin-bottom: 10px;
}

/* 等待状态：spinner + 交替文案 */
.thinking-status {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #909399;
  animation: thinking-fade 0.4s ease;
}

.thinking-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid #dcdfe6;
  border-top-color: #409eff;
  border-radius: 50%;
  animation: thinking-spin 0.8s linear infinite;
  flex-shrink: 0;
}

@keyframes thinking-spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes thinking-fade {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

/* 正文中的 [资料N] 可点击上标 */
.md-body :deep(.cite-ref) {
  color: #409eff;
  cursor: pointer;
  font-size: 11px;
  margin: 0 2px;
  user-select: none;
}

.md-body :deep(.cite-ref:hover) {
  text-decoration: underline;
}

.citation-content {
  max-height: 56vh;
  overflow: auto;
  padding: 12px;
  font-size: 13px;
  line-height: 1.8;
  color: #606266;
  white-space: pre-wrap;
  word-break: break-word;
  background: #fafafa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}

.source-name {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
}

.source-meta {
  display: flex;
  gap: 8px;
  margin-top: 2px;
  font-size: 12px;
  color: #909399;
}

.chat-input {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 12px 16px;
  background-color: #fff;
  border-top: 1px solid #e4e7ed;
  flex-shrink: 0;
}

.send-btn {
  height: 54px;
  min-width: 84px;
}

/* 移动端适配 */
@media (max-width: 767px) {
  .desktop-only {
    display: none !important;
  }

  .messages {
    padding: 12px;
  }

  .msg-bubble {
    max-width: 88%;
  }

  .chat-toolbar {
    padding: 8px 12px;
  }

  .chat-input {
    padding: 8px 12px;
  }

  .send-btn {
    height: auto;
    min-width: 72px;
    padding: 8px 14px;
  }
}
</style>
