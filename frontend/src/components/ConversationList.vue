<template>
  <div class="conv-list-wrap">
    <div class="conv-toolbar">
      <span class="conv-toolbar-title">会话列表</span>
      <el-button type="primary" size="small" plain :disabled="disabled" @click="$emit('create')">
        新建会话
      </el-button>
    </div>
    <div class="conv-scroll">
      <div
        v-for="conv in conversations"
        :key="conv.id"
        class="conv-item"
        :class="{ active: conv.id === currentId, disabled }"
        @click="!disabled && $emit('select', conv.id)"
      >
        <div class="conv-title">{{ conv.title || '新会话' }}</div>
        <div class="conv-meta">
          <span class="conv-time">{{ formatTime(conv.updated_at) }}</span>
          <span v-if="conv.last_message?.content" class="conv-preview">
            {{ conv.last_message.content }}
          </span>
        </div>
        <el-button
          class="conv-delete"
          link
          type="danger"
          size="small"
          @click.stop="!disabled && $emit('delete', conv.id)"
        >
          删除
        </el-button>
      </div>
      <el-empty v-if="!conversations.length" description="暂无会话" :image-size="56" />
    </div>
  </div>
</template>

<script setup>
defineProps({
  conversations: { type: Array, default: () => [] },
  currentId: { type: String, default: '' },
  /** 发送中禁用切换，避免流式消息错乱 */
  disabled: { type: Boolean, default: false },
})

defineEmits(['select', 'delete', 'create'])

const formatTime = (iso) => {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
</script>

<style scoped>
.conv-list-wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.conv-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 12px 8px;
  flex-shrink: 0;
}

.conv-toolbar-title {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

.conv-scroll {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px 12px;
}

.conv-item {
  position: relative;
  padding: 8px 64px 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 0.2s;
}

.conv-item:hover {
  background-color: rgba(255, 255, 255, 0.08);
}

.conv-item.active {
  background-color: rgba(64, 158, 255, 0.25);
}

.conv-item.disabled {
  cursor: not-allowed;
  opacity: 0.7;
}

.conv-title {
  font-size: 14px;
  color: #fff;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv-meta {
  display: flex;
  gap: 6px;
  margin-top: 2px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.45);
  align-items: center;
}

.conv-time {
  flex-shrink: 0;
}

.conv-preview {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.conv-delete {
  position: absolute;
  right: 8px;
  top: 50%;
  transform: translateY(-50%);
  opacity: 0;
  transition: opacity 0.2s;
}

.conv-item:hover .conv-delete,
.conv-item.active .conv-delete {
  opacity: 1;
}

/* 移动端抽屉里保持可点 */
@media (max-width: 767px) {
  .conv-delete {
    opacity: 1;
  }
}
</style>
