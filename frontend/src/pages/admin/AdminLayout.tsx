import { Layout, Menu } from "antd";
import { DashboardOutlined, ApiOutlined, FolderOutlined, TeamOutlined } from "@ant-design/icons";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

const { Sider, Content } = Layout;

export default function AdminLayout() {
  const navigate = useNavigate();
  const location = useLocation();

  const selectedKey = location.pathname === "/admin" ? "dashboard" : location.pathname.split("/")[2] || "dashboard";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider theme="dark" width={200}>
        <div style={{ color: "#fff", padding: 16, fontWeight: 700, fontSize: 16 }}>管理后台</div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          onClick={({ key }) => {
            if (key === "dashboard") navigate("/admin");
            else navigate(`/admin/${key}`);
          }}
          items={[
            { key: "dashboard", icon: <DashboardOutlined />, label: "仪表盘" },
            { key: "model-configs", icon: <ApiOutlined />, label: "模型配置" },
            { key: "materials", icon: <FolderOutlined />, label: "素材管理" },
            { key: "users", icon: <TeamOutlined />, label: "用户管理" },
          ]}
        />
      </Sider>
      <Layout>
        <Content style={{ padding: 24, background: "var(--bg)" }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
