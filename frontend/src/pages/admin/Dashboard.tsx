import { useEffect, useState } from "react";
import { Card, Col, Row, Statistic, Typography } from "antd";
import { api } from "../../api/client";

export default function Dashboard() {
  const [materials, setMaterials] = useState<any[]>([]);
  const [users, setUsers] = useState<any[]>([]);

  useEffect(() => {
    api.get("/admin/model-configs").then((r) => r.data).catch(() => []);
    api.get("/materials").then((r) => setMaterials(r.data)).catch(() => []);
    api.get("/admin/users").then((r) => setUsers(r.data)).catch(() => []);
  }, []);

  const ready = materials.filter((m) => m.status === "ready").length;
  const error = materials.filter((m) => m.status === "error").length;

  return (
    <div>
      <Typography.Title level={4}>仪表盘</Typography.Title>
      <Row gutter={[16, 16]}>
        <Col span={6}>
          <Card>
            <Statistic title="用户总数" value={users.length} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="素材总数" value={materials.length} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="Ready" value={ready} valueStyle={{ color: "#3f8600" }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="Error" value={error} valueStyle={{ color: "#cf1322" }} />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
