import request from './request'

/**
 * 文档 chunk 混合检索（BM25 + 向量双路召回，RRF 融合排序）
 * @param {{query: string, kb_ids: string[], top_k: number}} params
 * @returns {{mode: string, top_k: number, items: Array}}
 *          items 每项含 score（RRF 融合分）、vector_score / keyword_score（各路原始分）
 */
export const searchChunks = async (params) =>
  (await request.post('/retrieval/search', params)).data.data
