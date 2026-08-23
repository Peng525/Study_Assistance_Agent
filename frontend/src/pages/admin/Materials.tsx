import { useEffect, useState } from "react";
import {
  Button,
  Dropdown,
  Form,
  Input,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import { UploadOutlined } from "@ant-design/icons";
import { api } from "../../api/client";

interface MaterialRow {
  course_id: string;
  status: string;
  error_message?: string | null;
  courseware_format?: string | null;
  subtitle_status?: string;
}

const FILE_TYPES = [
  { value: "video", label: "视频（mp4/webm）" },
  { value: "subtitle", label: "字幕（vtt/srt）" },
  { value: "courseware", label: "课件（md/pdf/pptx）" },
];

export default function Materials() {
  const [list, setList] = useState<MaterialRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadForm] = Form.useForm();
  const [fileList, setFileList] = useState<any[]>([]);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);

  const load = () => {
    setLoading(true);
    api
      .get("/materials")
      .then((r) => setList(r.data))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  // 上传文件（带进度条）
  const uploadFile = async (courseId: string, fileType: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    setUploading(true);
    setProgress(0);
    try {
      await api.post(`/admin/materials/upload?course_id=${courseId}&file_type=${fileType}`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
        onUploadProgress: (e) => {
          if (e.total) setProgress(Math.round((e.loaded / e.total) * 100));
        },
      });
      message.success("上传成功");
      load();
      return true;
    } catch (e: any) {
      message.error(e.response?.data?.detail || "上传失败");
      return false;
    } finally {
      setUploading(false);
      setProgress(0);
    }
  };

  const doUpload = async () => {
    const { course_id, file_type } = await uploadForm.validateFields();
    const file = fileList[0]?.originFileObj || fileList[0];
    if (!file) {
      message.warning("请选择文件");
      return;
    }
    const ok = await uploadFile(course_id, file_type, file);
    if (ok) {
      setUploadOpen(false);
      setFileList([]);
      uploadForm.resetFields();
    }
  };

  const rescan = async (course_id: string) => {
    await api.post(`/admin/materials/${course_id}/rescan`);
    message.success("重新扫描完成");
    load();
  };

  // 重新上传（弹确认后覆盖）
  const [reupload, setReupload] = useState<{ courseId: string; fileType: string } | null>(null);
  const [reuploadFile, setReuploadFile] = useState<any[]>([]);

  const confirmReupload = async () => {
    if (!reupload) return;
    const file = reuploadFile[0]?.originFileObj || reuploadFile[0];
    if (!file) {
      message.warning("请选择文件");
      return;
    }
    const ok = await uploadFile(reupload.courseId, reupload.fileType, file);
    if (ok) {
      setReupload(null);
      setReuploadFile([]);
    }
  };

  const columns = [
    { title: "课程 ID", dataIndex: "course_id" },
    {
      title: "状态",
      dataIndex: "status",
      render: (v: string) => (v === "ready" ? <Tag color="green">ready</Tag> : <Tag color="red">error</Tag>),
    },
    {
      title: "字幕状态",
      dataIndex: "subtitle_status",
      render: (v: string) => {
        const map: Record<string, string> = {
          ready: "已就绪",
          pending: "待生成",
          generating: "生成中",
          error: "失败",
        };
        return map[v] || v || "—";
      },
    },
    { title: "课件格式", dataIndex: "courseware_format", render: (v: string) => v || "—" },
    { title: "错误信息", dataIndex: "error_message", ellipsis: true, render: (v: string) => v || "—" },
    {
      title: "操作",
      render: (_: any, row: MaterialRow) => (
        <Space>
          <Button size="small" onClick={() => rescan(row.course_id)}>
            重新扫描
          </Button>
          <Dropdown
            menu={{
              items: FILE_TYPES.map((ft) => ({
                key: ft.value,
                label: `重新上传${ft.label}`,
                onClick: () => setReupload({ courseId: row.course_id, fileType: ft.value }),
              })),
            }}
          >
            <Button size="small">重新上传</Button>
          </Dropdown>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          素材管理
        </Typography.Title>
        <Button type="primary" icon={<UploadOutlined />} onClick={() => setUploadOpen(true)}>
          上传文件
        </Button>
      </div>
      <Table rowKey="course_id" loading={loading} columns={columns} dataSource={list} pagination={false} />

      <Modal title="上传文件" open={uploadOpen} onOk={doUpload} onCancel={() => setUploadOpen(false)} okText="上传" confirmLoading={uploading}>
        <Form form={uploadForm} layout="vertical">
          <Form.Item name="course_id" label="课程 ID" rules={[{ required: true }]}>
            <Input placeholder="如 course-001" />
          </Form.Item>
          <Form.Item name="file_type" label="文件类型" rules={[{ required: true }]}>
            <Select options={FILE_TYPES} />
          </Form.Item>
          <Form.Item label="文件" required>
            <Upload
              beforeUpload={() => false}
              fileList={fileList}
              onChange={({ fileList }) => setFileList(fileList.slice(-1))}
            >
              <Button icon={<UploadOutlined />}>选择文件</Button>
            </Upload>
          </Form.Item>
          {uploading && <Progress percent={progress} />}
        </Form>
      </Modal>

      {/* 重新上传（覆盖）确认弹窗 */}
      <Modal
        title="重新上传（将覆盖现有文件）"
        open={!!reupload}
        onOk={confirmReupload}
        onCancel={() => {
          setReupload(null);
          setReuploadFile([]);
        }}
        okText="上传覆盖"
        confirmLoading={uploading}
      >
        <p>
          课程 <strong>{reupload?.courseId}</strong> · 类型{" "}
          <strong>{FILE_TYPES.find((f) => f.value === reupload?.fileType)?.label}</strong>
          ，将覆盖现有文件。
        </p>
        <Upload
          beforeUpload={() => false}
          fileList={reuploadFile}
          onChange={({ fileList }) => setReuploadFile(fileList.slice(-1))}
        >
          <Button icon={<UploadOutlined />}>选择新文件</Button>
        </Upload>
        {uploading && <Progress percent={progress} />}
      </Modal>
    </div>
  );
}
