/** 素材域接口封装（v8 §5.5A）。

两个纪律：
1. `course_id` 可能含中文或斜杠，出现在**路径段**时一律 `encodeURIComponent`；
   出现在 **body** 里则原样传（框架会自己编码，预先编码反而会双编码）。
2. 批量端点只接收「前端已按状态过滤过」的 ID 集合；后端仍会用自己的白名单
   二次校验，所以前端过滤只是为了少发请求，不是安全边界。
*/

import { api } from "./client";

const enc = encodeURIComponent;

export interface BatchItemResult {
  course_id: string;
  ok: boolean;
  status?: string | null;
  error?: string | null;
}

export interface BatchResult {
  succeeded: number;
  failed: number;
  results: BatchItemResult[];
}

export const adminMaterials = {
  // ---- 列表 / 扫描 ----
  listMaterials: () => api.get("/materials").then((r) => r.data),
  scanAll: () => api.post("/admin/materials/scan").then((r) => r.data),
  rescan: (id: string) => api.post(`/admin/materials/${enc(id)}/rescan`).then((r) => r.data),

  // ---- 上传 / 文件 ----
  upload: (
    p: {
      courseId: string;
      fileType: string;
      file: File;
      courseType?: string;
      sourceId?: number;
      seriesId?: number;
    },
    onUploadProgress?: (e: any) => void,
  ) => {
    const fd = new FormData();
    fd.append("file", p.file);
    const q = new URLSearchParams({
      course_id: p.courseId,
      file_type: p.fileType,
      course_type: p.courseType || "theory",
      ...(p.sourceId ? { source_id: String(p.sourceId) } : {}),
      ...(p.seriesId ? { series_id: String(p.seriesId) } : {}),
    });
    return api
      .post(`/admin/materials/upload?${q}`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
        onUploadProgress,
      })
      .then((r) => r.data);
  },
  listFiles: (id: string) => api.get(`/admin/materials/${enc(id)}/files`).then((r) => r.data),
  deleteFile: (id: string, fileType: string) =>
    api.delete(`/admin/materials/${enc(id)}/files/${fileType}`).then((r) => r.data),

  // ---- 字幕：生成 / 取消 / 审核（单条）----
  generateSubtitle: (id: string) =>
    api.post(`/admin/materials/${enc(id)}/generate-subtitle`).then((r) => r.data),
  cancelSubtitle: (id: string) =>
    api.post(`/admin/materials/${enc(id)}/cancel-subtitle`).then((r) => r.data),
  reviewSubtitle: (id: string, reviewState: "reviewed" | "unreviewed") =>
    api
      .post(`/admin/materials/${enc(id)}/subtitle/review`, { review_state: reviewState })
      .then((r) => r.data),

  // ---- 字幕 cues 读写（SubtitleDrawer 查看 / 编辑）----
  getCues: (id: string) => api.get(`/admin/materials/${enc(id)}/subtitle/cues`).then((r) => r.data),
  putCues: (id: string, cues: unknown[], revision: string) =>
    api.put(`/admin/materials/${enc(id)}/subtitle/cues`, { cues, revision }).then((r) => r.data),

  // ---- 批量（v8 §5.5A.5）----
  batchGenerateSubtitle: (ids: string[]): Promise<BatchResult> =>
    api.post("/admin/materials/batch/generate-subtitle", { course_ids: ids }).then((r) => r.data),
  batchCancelSubtitle: (ids: string[]): Promise<BatchResult> =>
    api.post("/admin/materials/batch/cancel-subtitle", { course_ids: ids }).then((r) => r.data),
  batchReview: (ids: string[], reviewState: "reviewed" | "unreviewed"): Promise<BatchResult> =>
    api
      .post("/admin/materials/batch/review", { course_ids: ids, review_state: reviewState })
      .then((r) => r.data),

  // ---- 非 admin 前缀（学习端点，供状态轮询复用）----
  // 注：管理台的轮询**不走这里** —— 列表端点 GET /materials 已用 _peek_runtime()
  // 把内存 worker 的进度合并进每一行，整表刷新即可，无需逐行单查（AC-15）。
  // 保留此方法供单点精确查询使用；后端已修好 admin 过滤与 peek 语义。
  getSubtitleStatus: (id: string) =>
    api.get(`/materials/${enc(id)}/subtitle-status`).then((r) => r.data),
  whisperModelStatus: () => api.get("/admin/materials/whisper/model-status").then((r) => r.data),
};

/**
 * 批量重扫：后端没有端点，前端串行循环聚合。
 *
 * 不为它新增端点口径的理由：重扫是纯本地磁盘 IO + 入库，批量端点也省不掉这部分耗时，
 * 却要多维护一个端点；且重扫不属于字幕工作流（PRD §5.5A.5）。
 */
export async function batchRescan(ids: string[]): Promise<BatchResult> {
  const results: BatchItemResult[] = [];
  for (const id of ids) {
    try {
      await adminMaterials.rescan(id);
      results.push({ course_id: id, ok: true });
    } catch (e: any) {
      results.push({ course_id: id, ok: false, error: e.response?.data?.detail || "重扫失败" });
    }
  }
  return {
    succeeded: results.filter((r) => r.ok).length,
    failed: results.filter((r) => !r.ok).length,
    results,
  };
}
