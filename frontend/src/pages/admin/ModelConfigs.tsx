import { useEffect, useState } from "react";
import {
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { api } from "../../api/client";

interface ModelConfig {
  id: number;
  name: string;
  base_url: string;
  api_key_masked: string;
  model_name: string;
  is_default: boolean;
  route_count: number;
}

interface ModelRoute {
  id: number;
  display_name: string;
  model_name: string;
  priority: number;
  is_enabled: boolean;
  health_status: string;
  connectivity_status?: "untested" | "passed" | "failed";
  failure_streak: number;
  cooldown_until?: string | null;
  last_success_at?: string | null;
  last_error_code?: string | null;
  last_error_request_id?: string | null;
  last_error_message?: string | null;
}

const DASHSCOPE_PRESET_ID = "dashscope-current";

const healthLabels: Record<string, { text: string; color: string }> = {
  healthy: { text: "正常", color: "green" },
  cooling: { text: "冷却中", color: "orange" },
  quota_exhausted: { text: "免费额度已用完", color: "red" },
  misconfigured: { text: "未开通/配置错误", color: "volcano" },
  credential_error: { text: "凭据/账户异常", color: "magenta" },
};

const connectivityLabels = {
  untested: { text: "未检测", color: "default" },
  passed: { text: "检测通过", color: "green" },
  failed: { text: "检测失败", color: "red" },
} as const;

function getConnectivityStatus(route: ModelRoute): keyof typeof connectivityLabels {
  if (route.connectivity_status) return route.connectivity_status;
  if (route.last_error_code || route.last_error_message) return "failed";
  if (route.last_success_at) return "passed";
  return "untested";
}

function ModelRoutePanel({
  config,
  onRoutesChanged,
}: {
  config: ModelConfig;
  onRoutesChanged: () => void;
}) {
  const [routes, setRoutes] = useState<ModelRoute[]>([]);
  const [loading, setLoading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [priorityDrafts, setPriorityDrafts] = useState<Record<number, number | null>>({});
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ModelRoute | null>(null);
  const [form] = Form.useForm();

  const applyRoutes = (nextRoutes: ModelRoute[]) => {
    setRoutes(nextRoutes);
    setPriorityDrafts(
      Object.fromEntries(nextRoutes.map((route) => [route.id, route.priority])),
    );
  };

  const loadRoutes = async () => {
    setLoading(true);
    try {
      const response = await api.get(`/admin/model-configs/${config.id}/routes`);
      applyRoutes(response.data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadRoutes();
  }, [config.id]);

  const importDashScopePreset = async () => {
    try {
      const response = await api.post(
        `/admin/model-configs/${config.id}/routes/presets/${DASHSCOPE_PRESET_ID}`,
      );
      applyRoutes(response.data);
      onRoutesChanged();
      message.success("阿里云十模型模板已导入");
    } catch (error: any) {
      message.error(error.response?.data?.detail || "模板导入失败");
    }
  };

  const runTests = async () => {
    setTesting(true);
    try {
      const response = await api.post(
        `/admin/model-configs/${config.id}/routes/test`,
        {},
        { timeout: 180000 },
      );
      const passed = response.data.results.filter((item: { ok: boolean }) => item.ok).length;
      if (response.data.stopped_early) {
        message.warning(
          `检测提前停止：${response.data.stop_reason || "请检查共享配置"}；` +
            `${passed}/${response.data.total_enabled} 个模型可用，${response.data.skipped_count} 个未检测`,
        );
      } else if (passed < response.data.total_enabled) {
        message.warning(`检测完成：${passed}/${response.data.total_enabled} 个模型可用，请查看失败状态`);
      } else {
        message.success(`检测完成：${passed}/${response.data.total_enabled} 个模型可用`);
      }
      await loadRoutes();
    } catch (error: any) {
      message.error(error.response?.data?.detail || "批量检测失败");
    } finally {
      setTesting(false);
    }
  };

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({
      priority: (routes[routes.length - 1]?.priority || 0) + 10,
      is_enabled: true,
    });
    setOpen(true);
  };

  const openEdit = (route: ModelRoute) => {
    setEditing(route);
    form.setFieldsValue(route);
    setOpen(true);
  };

  const save = async () => {
    const values = await form.validateFields();
    try {
      if (editing) {
        await api.put(`/admin/model-configs/${config.id}/routes/${editing.id}`, values);
      } else {
        await api.post(`/admin/model-configs/${config.id}/routes`, values);
      }
      message.success("模型路由已保存");
      setOpen(false);
      await loadRoutes();
      onRoutesChanged();
    } catch (error: any) {
      message.error(error.response?.data?.detail || "保存失败");
    }
  };

  const remove = async (routeId: number) => {
    await api.delete(`/admin/model-configs/${config.id}/routes/${routeId}`);
    message.success("模型路由已删除");
    await loadRoutes();
    onRoutesChanged();
  };

  const reset = async (routeId: number) => {
    await api.post(`/admin/model-configs/${config.id}/routes/${routeId}/reset`);
    message.success("模型状态已重置");
    await loadRoutes();
  };

  const toggle = async (route: ModelRoute, enabled: boolean) => {
    await api.put(`/admin/model-configs/${config.id}/routes/${route.id}`, {
      display_name: route.display_name,
      model_name: route.model_name,
      priority: route.priority,
      is_enabled: enabled,
    });
    await loadRoutes();
  };

  const savePriority = async (route: ModelRoute) => {
    const priority = priorityDrafts[route.id];
    if (priority == null || !Number.isInteger(priority) || priority < 1 || priority > 10000) {
      message.error("优先级必须是 1 到 10000 的整数");
      return;
    }
    try {
      await api.put(`/admin/model-configs/${config.id}/routes/${route.id}`, {
        display_name: route.display_name,
        model_name: route.model_name,
        priority,
        is_enabled: route.is_enabled,
      });
      message.success("优先级已保存");
      await loadRoutes();
    } catch (error: any) {
      setPriorityDrafts((current) => ({ ...current, [route.id]: route.priority }));
      message.error(error.response?.data?.detail || "优先级保存失败");
    }
  };

  const columns = [
    {
      title: "优先级（越小越先）",
      width: 190,
      render: (_: unknown, route: ModelRoute) => {
        const draft = priorityDrafts[route.id] ?? null;
        const valid = draft != null && Number.isInteger(draft) && draft >= 1 && draft <= 10000;
        return (
          <Space.Compact>
            <InputNumber
              aria-label={`设置 ${route.display_name} 优先级`}
              size="small"
              min={1}
              max={10000}
              precision={0}
              value={draft}
              onChange={(value) =>
                setPriorityDrafts((current) => ({ ...current, [route.id]: value }))
              }
              onPressEnter={() => void savePriority(route)}
              style={{ width: 92 }}
            />
            <Button
              aria-label={`保存 ${route.display_name} 优先级`}
              size="small"
              disabled={!valid || draft === route.priority}
              onClick={() => void savePriority(route)}
            >
              保存
            </Button>
          </Space.Compact>
        );
      },
    },
    { title: "显示名", dataIndex: "display_name" },
    { title: "模型 ID", dataIndex: "model_name" },
    {
      title: "连通性 / 状态",
      render: (_: unknown, route: ModelRoute) => {
        const connectivity = connectivityLabels[getConnectivityStatus(route)];
        const health = healthLabels[route.health_status] || {
          text: route.health_status,
          color: "default",
        };
        return (
          <Space direction="vertical" size={0}>
            <Space size={4} wrap>
              <Tag color={connectivity.color}>{connectivity.text}</Tag>
              {route.health_status !== "healthy" && (
                <Tag color={health.color}>{health.text}</Tag>
              )}
            </Space>
            {route.failure_streak > 0 && (
              <Typography.Text type="secondary">连续失败 {route.failure_streak} 次</Typography.Text>
            )}
            {route.last_error_code && (
              <Typography.Text type="secondary">{route.last_error_code}</Typography.Text>
            )}
            {route.last_error_message && (
              <Typography.Text
                type="secondary"
                ellipsis={{ tooltip: route.last_error_message }}
                style={{ maxWidth: 220 }}
              >
                {route.last_error_message}
              </Typography.Text>
            )}
          </Space>
        );
      },
    },
    {
      title: "冷却至",
      render: (_: unknown, route: ModelRoute) =>
        route.cooldown_until ? new Date(route.cooldown_until).toLocaleString() : "—",
    },
    {
      title: "最近成功",
      render: (_: unknown, route: ModelRoute) =>
        route.last_success_at ? new Date(route.last_success_at).toLocaleString() : "—",
    },
    {
      title: "启用",
      render: (_: unknown, route: ModelRoute) => (
        <Switch checked={route.is_enabled} onChange={(checked) => toggle(route, checked)} />
      ),
    },
    {
      title: "操作",
      render: (_: unknown, route: ModelRoute) => (
        <Space wrap>
          <Button size="small" onClick={() => openEdit(route)}>
            编辑
          </Button>
          <Button size="small" onClick={() => reset(route.id)}>
            重置状态
          </Button>
          <Popconfirm title="确认从模型链删除？" onConfirm={() => remove(route.id)}>
            <Button size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Card title={`模型调用链 · ${config.name}（${routes.length}）`} styles={{ body: { paddingTop: 16 } }}>
      <Space style={{ marginBottom: 12 }} wrap>
        <Popconfirm
          title="确认导入阿里云十模型模板？"
          description="该模板只适用于能够访问这些模型的阿里云兼容接口。"
          onConfirm={importDashScopePreset}
          disabled={routes.length > 0}
        >
          <Button disabled={routes.length > 0}>导入阿里云十模型模板</Button>
        </Popconfirm>
        <Button onClick={runTests} loading={testing} disabled={routes.length === 0}>
          批量连通性检测
        </Button>
        <Button icon={<PlusOutlined />} onClick={openCreate}>
          手工添加模型
        </Button>
        <Typography.Text type="secondary">
          数字越小越先调用，可直接修改保存；检测会产生少量 Token 消耗。
        </Typography.Text>
      </Space>
      <Table
        rowKey="id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={routes}
        pagination={false}
        scroll={{ x: 1100 }}
        locale={{ emptyText: "当前模型链为空，请手工添加模型或导入模板" }}
      />
      <Modal
        title={editing ? "编辑模型" : "添加模型"}
        open={open}
        onOk={save}
        onCancel={() => setOpen(false)}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="display_name" label="显示名" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="model_name" label="模型 ID" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="priority" label="优先级" rules={[{ required: true }]}>
            <InputNumber min={1} max={10000} style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="is_enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}

export default function ModelConfigs() {
  const [list, setList] = useState<ModelConfig[]>([]);
  const [selectedConfigId, setSelectedConfigId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ModelConfig | null>(null);
  const [form] = Form.useForm();

  const load = async (preferredConfigId?: number) => {
    setLoading(true);
    try {
      const response = await api.get("/admin/model-configs");
      const configs: ModelConfig[] = response.data;
      setList(configs);
      setSelectedConfigId((current) => {
        const desired = preferredConfigId ?? current;
        if (desired != null && configs.some((config) => config.id === desired)) {
          return desired;
        }
        return configs.find((config) => config.is_default)?.id ?? configs[0]?.id ?? null;
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const selectedConfig = list.find((config) => config.id === selectedConfigId) ?? null;

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    setOpen(true);
  };

  const openEdit = (row: ModelConfig) => {
    setEditing(row);
    form.setFieldsValue({
      name: row.name,
      base_url: row.base_url,
      model_name: row.model_name,
      api_key: "",
      is_default: row.is_default,
    });
    setOpen(true);
  };

  const submit = async () => {
    const values = await form.validateFields();
    try {
      const response = editing
        ? await api.put(`/admin/model-configs/${editing.id}`, {
            ...values,
            is_default: values.is_default || false,
          })
        : await api.post("/admin/model-configs", {
            ...values,
            is_default: values.is_default || false,
          });
      message.success("保存成功");
      setOpen(false);
      await load(response.data.id);
    } catch (error: any) {
      message.error(error.response?.data?.detail || "保存失败");
    }
  };

  const remove = async (id: number) => {
    await api.delete(`/admin/model-configs/${id}`);
    message.success("已删除");
    await load();
  };

  const columns = [
    { title: "接入名称", dataIndex: "name" },
    { title: "Base URL", dataIndex: "base_url", ellipsis: true },
    { title: "API Key", dataIndex: "api_key_masked" },
    { title: "无模型链时的兜底模型", dataIndex: "model_name" },
    { title: "模型数量", dataIndex: "route_count", render: (count: number) => `${count} 个` },
    {
      title: "默认 API",
      dataIndex: "is_default",
      render: (value: boolean) => (value ? <Tag color="blue">默认</Tag> : "否"),
    },
    {
      title: "操作",
      render: (_: unknown, row: ModelConfig) => (
        <Space wrap>
          <Button
            size="small"
            type={selectedConfigId === row.id ? "primary" : "default"}
            onClick={() => setSelectedConfigId(row.id)}
          >
            查看模型链
          </Button>
          <Button size="small" onClick={() => openEdit(row)}>
            编辑接入
          </Button>
          <Popconfirm title="确认删除该 API 接入及其模型链？" onConfirm={() => remove(row.id)}>
            <Button size="small" danger>
              删除
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size={20} style={{ width: "100%" }}>
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
          <div>
            <Typography.Title level={4} style={{ margin: 0 }}>
              API 接入配置
            </Typography.Title>
            <Typography.Text type="secondary">
              每条接入独立保存 Base URL 和密钥；管理员指定的默认 API 才会用于学习端对话。
            </Typography.Text>
          </div>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新增 API 接入
          </Button>
        </div>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 12 }}>
          遮罩后的 API Key 表示密钥已加密保存，不代表接口或模型已经通过连通性检测。
        </Typography.Paragraph>
        <Table
          rowKey="id"
          loading={loading}
          columns={columns}
          dataSource={list}
          pagination={false}
          scroll={{ x: 1100 }}
          locale={{ emptyText: "暂无 API 接入，请检查 .env 的 LLM_API_KEY 或点击“新增 API 接入”" }}
        />
      </Card>

      {selectedConfig ? (
        <ModelRoutePanel
          key={selectedConfig.id}
          config={selectedConfig}
          onRoutesChanged={() => void load(selectedConfig.id)}
        />
      ) : (
        <Card>
          <Typography.Text type="secondary">请先新增 API 接入，再配置它的模型调用链。</Typography.Text>
        </Card>
      )}

      <Modal
        title={editing ? "编辑 API 接入" : "新增 API 接入"}
        open={open}
        onOk={submit}
        onCancel={() => setOpen(false)}
        okText="保存"
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="接入名称" rules={[{ required: true }]}>
            <Input placeholder="例如：阿里云百炼、DeepSeek" />
          </Form.Item>
          <Form.Item name="base_url" label="Base URL" rules={[{ required: true }]}>
            <Input placeholder="https://example.com/v1" />
          </Form.Item>
          <Form.Item
            name="api_key"
            label="API Key"
            rules={editing ? [] : [{ required: true, message: "新增时必填" }]}
            extra={editing ? "留空表示不修改" : ""}
          >
            <Input.Password />
          </Form.Item>
          <Form.Item
            name="model_name"
            label="无模型链时的兜底模型"
            rules={[{ required: true }]}
            extra="仅在模型链从未初始化时使用；一旦添加或导入过模型，后续即使删空也需重新配置模型链。"
          >
            <Input placeholder="例如：qwen-plus、deepseek-chat" />
          </Form.Item>
          <Form.Item name="is_default" label="设为默认 API" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}
