import request from './request'

/** 用户列表（分页+关键字）：{ items, total } */
export const listUsers = async (params) =>
  (await request.get('/admin/users', { params })).data.data

/** 新建用户 */
export const createUser = async (data) =>
  (await request.post('/admin/users', data)).data.data

/** 更新用户（角色/启用禁用/部门等） */
export const updateUser = async (id, data) =>
  (await request.put(`/admin/users/${id}`, data)).data.data

/** 部门列表 */
export const listDepartments = async () =>
  (await request.get('/admin/departments')).data.data

/** 新增部门 */
export const createDepartment = async (data) =>
  (await request.post('/admin/departments', data)).data.data

/** 角色列表 */
export const listRoles = async () => (await request.get('/admin/roles')).data.data

/** 问答 Token / 反馈汇总 */
export const getQaSummary = async () => (await request.get('/admin/qa/summary')).data.data

/** 问答记录（分页）：{ items, total }，params: { page, size, feedback, keyword } */
export const getQaRecords = async (params) =>
  (await request.get('/admin/qa/records', { params })).data.data
