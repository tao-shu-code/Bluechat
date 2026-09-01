import request from './request'
import { useAuthStore } from '../stores/auth'

/**
 * 流式问答：POST /api/qa/chat（SSE，原生 fetch，不走 axios）。
 * SSE 帧格式 "event: xxx\ndata: {json}\n\n"，手动按空行分帧（处理跨 chunk 粘包）。
 * onEvent(event, payload)：
 *  - sources: [{ document_name, title_path, page_number }]
 *  - delta:   { content } 增量文本
 *  - done:    { conversation_id, message_id }
 *  - error:   { message }
 */
export async function chatStream({ question, conversation_id, kb_ids, onEvent, signal }) {
  const auth = useAuthStore()
  const res = await fetch('/api/qa/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(auth.token ? { Authorization: `Bearer ${auth.token}` } : {}),
    },
    body: JSON.stringify({
      question,
      conversation_id: conversation_id || undefined,
      kb_ids: kb_ids?.length ? kb_ids : undefined,
      stream: true,
    }),
    signal,
  })

  if (!res.ok) {
    let message = `请求失败（HTTP ${res.status}）`
    try {
      const body = await res.json()
      message = body.message || body.detail || message
    } catch {
      /* 非 JSON 错误体，保留默认提示 */
    }
    if (res.status === 401) {
      // SSE 不经 axios 拦截器，这里手动处理登录态失效
      auth.logout()
      window.location.assign('/login')
    }
    throw new Error(message)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  const dispatchFrame = (frame) => {
    let event = 'message'
    const dataLines = []
    for (const line of frame.split('\n')) {
      if (line.startsWith('event:')) {
        event = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).replace(/^ /, ''))
      }
    }
    if (!dataLines.length) return
    const dataText = dataLines.join('\n')
    let payload = dataText
    try {
      payload = JSON.parse(dataText)
    } catch {
      /* 非 JSON data 原样透传 */
    }
    onEvent?.(event, payload)
  }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      if (frame.trim()) dispatchFrame(frame)
    }
  }
  // 流结束时残余缓冲（最后一帧可能无空行结尾）
  if (buffer.trim()) dispatchFrame(buffer)
}

/**
 * 对 AI 回答点赞/点踩：POST /api/qa/messages/{id}/feedback。
 * feedback: 'like' | 'dislike' | null（null = 取消评价）
 */
export async function setMessageFeedback(messageId, feedback) {
  const res = await request.post(`/qa/messages/${messageId}/feedback`, { feedback })
  return res.data?.data || null
}
