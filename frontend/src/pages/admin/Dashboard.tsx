import { useEffect, useState } from "react";
import { Button, Card, Col, Row, Space, Statistic, Typography } from "antd";
import { ApiOutlined, FolderOutlined, TeamOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { api } from "../../api/client";

interface Stats {
  default_model_name: string | null;
  material_total: number;
  material_ready: number;
  material_error: number;
  user_total: number;
  last_7_days_sessions: { date: string; count: number }[];
}

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    api
      .get("/admin/stats")
      .then((r) => setStats(r.data))
      .catch(() => {});
  }, []);

  const days = stats?.last_7_days_sessions || [];
  const maxCount = Math.max(1, ...days.map((d) => d.count));

  return (
    <div>
      <Typography.Title level={4}>仪表盘</Typography.Title>
      <Row gutter={[16, 16]}>
        <Col span={6}>
          <Card>
            <Statistic
              title="默认大模型"
              value={stats?.default_model_name || "未配置"}
              valueStyle={{ fontSize: 20 }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="素材总数" value={stats?.material_total ?? 0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="Ready" value={stats?.material_ready ?? 0} valueStyle={{ color: "#3f8600" }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="Error" value={stats?.material_error ?? 0} valueStyle={{ color: "#cf1322" }} />
          </Card>
        </Col>
      </Row>

      {/* 快速入口 */}
      <Card style={{ marginTop: 16 }} title="快速入口">
        <Space>
          <Button icon={<ApiOutlined />} onClick={() => navigate("/admin/model-configs")}>
            模型配置
          </Button>
          <Button icon={<FolderOutlined />} onClick={() => navigate("/admin/materials")}>
            素材管理
          </Button>
          <Button icon={<TeamOutlined />} onClick={() => navigate("/admin/users")}>
            用户管理
          </Button>
        </Space>
      </Card>

      {/* 7 日会话柱状图 */}
      <Card style={{ marginTop: 16 }} title="最近 7 天会话数">
        <div style={{ display: "flex", alignItems: "flex-end", gap: 12, height: 160, paddingTop: 8 }}>
          {days.map((d) => (
            <div key={d.date} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center" }}>
              <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>{d.count}</div>
              <div
                style={{
                  width: "100%",
                  maxWidth: 40,
                  height: d.count === 0 ? 2 : Math.max(8, (d.count / maxCount) * 120),
                  background: "var(--primary)",
                  borderRadius: "4px 4px 0 0",
                }}
              />
              <div style={{ fontSize: 12, marginTop: 4 }}>{d.date}</div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
