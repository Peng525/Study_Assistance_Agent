import { useEffect, useState } from "react";
import { Button, Form, Input, Modal, Popconfirm, Space, Switch, Table, Typography, message } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { api } from "../../api/client";

interface ModelConfig {
  id: number;
  name: string;
  base_url: string;
  api_key_masked: string;
  model_name: string;
  is_default: boolean;
}

export default function ModelConfigs() {
  const [list, setList] = useState<ModelConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ModelConfig | null>(null);
  const [form] = Form.useForm();

  const load = () => {
    setLoading(true);
    api
      .get("/admin/model-configs")
      .then((r) => setList(r.data))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    setOpen(true);
  };

  const openEdit = (row: ModelConfig) => {
    setEditing(row);
    form.setFieldsValue({ name: row.name, base_url: row.base_url, model_name: row.model_name, api_key: "" });
    setOpen(true);
  };

  const submit = async () => {
    const values = await form.validateFields();
    try {
      if (editing) {
        await api.put(`/admin/model-configs/${editing.id}`, { ...values, is_default: values.is_default || false });
      } else {
        await api.post("/admin/model-configs", { ...values, is_default: values.is_default || false });
      }
      message.success("保存成功");
      setOpen(false);
      load();
    } catch (e: any) {
      message.error(e.response?.data?.detail || "保存失败");
    }
  };

  const remove = async (id: number) => {
    await api.delete(`/admin/model-configs/${id}`);
    message.success("已删除");
    load();
  };

  const columns = [
    { title: "配置名", dataIndex: "name" },
    { title: "Base URL", dataIndex: "base_url", ellipsis: true },
    { title: "API Key", dataIndex: "api_key_masked" },
    { title: "模型名", dataIndex: "model_name" },
    {
      title: "默认",
      dataIndex: "is_default",
      render: (v: boolean) => (v ? "是" : "否"),
    },
    {
      title: "操作",
      render: (_: any, row: ModelConfig) => (
        <Space>
          <Button size="small" onClick={() => openEdit(row)}>
            编辑
          </Button>
          <Popconfirm title="确认删除？" onConfirm={() => remove(row.id)}>
            <Button size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          模型配置
        </Typography.Title>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
          新增配置
        </Button>
      </div>
      <Table rowKey="id" loading={loading} columns={columns} dataSource={list} pagination={false} />

      <Modal
        title={editing ? "编辑配置" : "新增配置"}
        open={open}
        onOk={submit}
        onCancel={() => setOpen(false)}
        okText="保存"
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="配置名" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="base_url" label="Base URL" rules={[{ required: true }]}>
            <Input placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1" />
          </Form.Item>
          <Form.Item
            name="api_key"
            label="API Key"
            rules={editing ? [] : [{ required: true, message: "新增时必填" }]}
            extra={editing ? "留空表示不修改" : ""}
          >
            <Input.Password />
          </Form.Item>
          <Form.Item name="model_name" label="模型名" rules={[{ required: true }]}>
            <Input placeholder="qwen-plus" />
          </Form.Item>
          <Form.Item name="is_default" label="设为默认" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
