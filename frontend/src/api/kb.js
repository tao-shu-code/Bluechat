import request from './request'

/** 知识库列表（按当前用户可见范围过滤），返回数组 */
export const listKb = async () => (await request.get('/kb')).data.data

/** 创建知识库 */
export const createKb = async (data) => (await request.post('/kb', data)).data.data

/** 更新知识库 */
export const updateKb = async (id, data) => (await request.put(`/kb/${id}`, data)).data.data

/** 删除知识库 */
export const deleteKb = async (id) => (await request.delete(`/kb/${id}`)).data.data
