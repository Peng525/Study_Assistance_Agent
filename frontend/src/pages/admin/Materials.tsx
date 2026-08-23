import { useEffect, useState } from "react";
import {
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
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
import { getToken } from "../../store/auth";

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

  const load = () => {
    setLoading(true);
    api
      .get("/materials")
      .then((r) => setList(r.data))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const doUpload = async () => {
    const { course_id, file_type } = await uploadForm.validateFields();
    const file = fileList[0];
    if (!file) {
      message.warning("请选择文件");
      return;
    }
    const fd = new FormData();
    fd.append("file", file.originFileObj || file);
    try {
      await api.post(`/admin/materials/upload?course_id=${course_id}&file_type=${file_type}`, fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      message.success("上传成功");
      setUploadOpen(false);
      setFileList([]);
      uploadForm.resetFields();
      load();
    } catch (e: any) {
      message.error(e.response?.data?.detail || "上传失败");
    }
  };

  const rescan = async (course_id: string) => {
    await api.post(`/admin/materials/${course_id}/rescan`);
    message.success("重新扫描完成");
    load();
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

      <Modal title="上传文件" open={uploadOpen} onOk={doUpload} onCancel={() => setUploadOpen(false)} okText="上传">
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
        </Form>
      </Modal>
    </div>
  );
}
