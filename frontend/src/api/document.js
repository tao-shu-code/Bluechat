import request from './request'

/** 文档列表（分页）：{ items, total, page, size } */
export const listDocuments = async (params) =>
  (await request.get('/documents', { params })).data.data

/** 批量上传文档（FormData：kb_id + files），返回逐文件结果数组 */
export const uploadDocuments = async (formData) =>
  (
    await request.post('/documents/upload', formData, {
      timeout: 120000,
    })
  ).data.data

/** 失败重试（仅 FAILED） */
export const retryDocument = async (id) =>
  (await request.post(`/documents/${id}/retry`)).data.data

/** 重建索引（重新切分+向量化） */
export const reindexDocument = async (id) =>
  (await request.post(`/documents/${id}/reindex`)).data.data

/** 删除文档 */
export const deleteDocument = async (id) =>
  (await request.delete(`/documents/${id}`)).data.data

/** 源文档预览：返回 { url(objectURL), filename }，调用方负责 URL.revokeObjectURL */
export const fetchPreviewBlob = async (id) => {
  const res = await request.get(`/documents/${id}/preview`, { responseType: 'blob' })
  const blob = res.data
  const url = URL.createObjectURL(blob)
  return { url, blob, filename: getFilenameFromDisposition(res.headers) }
}

/** 源文档下载地址（触发浏览器下载） */
export const previewDownloadUrl = (id) =>
  `${request.defaults.baseURL}/documents/${id}/preview?download=true`

/** 从 Content-Disposition 解析 filename*（RFC 5987）或 filename */
const getFilenameFromDisposition = (headers) => {
  const cd = headers?.['content-disposition'] || ''
  const star = cd.match(/filename\*=UTF-8''([^;]+)/i)
  if (star) {
    try {
      return decodeURIComponent(star[1].replace(/"/g, ''))
    } catch {
      /* fallthrough */
    }
  }
  const plain = cd.match(/filename="?([^";]+)"?/i)
  return plain ? plain[1] : ''
}
