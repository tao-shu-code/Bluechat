import request from './request'

/** 创建会话（title 可选） */
export const createConversation = async (title) =>
  (await request.post('/conversations', { title })).data.data

/** 会话列表（分页）：{ items, total, page, size } */
export const listConversations = async (params = {}) =>
  (await request.get('/conversations', { params })).data.data

/** 会话详情 + 消息列表 */
export const getConversation = async (id) =>
  (await request.get(`/conversations/${id}`)).data.data

/** 删除会话（级联消息） */
export const deleteConversation = async (id) =>
  (await request.delete(`/conversations/${id}`)).data.data
