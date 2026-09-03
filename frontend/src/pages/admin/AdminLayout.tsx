import { Layout, Menu } from "antd";
import { DashboardOutlined, ApiOutlined, BookOutlined, FileSearchOutlined, FolderOutlined, TeamOutlined } from "@ant-design/icons";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import UserMenu from "../../components/UserMenu";

const { Sider, Content } = Layout;

export default function AdminLayout() {
  const navigate = useNavigate();
  const location = useLocation();

  const selectedKey = location.pathname.startsWith("/admin/columns/courseware")
    ? "columns-courseware"
    : location.pathname.startsWith("/admin/columns")
      ? "columns"
      : location.pathname === "/admin"
        ? "dashboard"
        : location.pathname.split("/")[2] || "dashboard";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider className="admin-sider" theme="dark" width={200}>
        <div style={{ color: "#fff", padding: 16, fontWeight: 700, fontSize: 16 }}>管理后台</div>
        <Menu
          theme="dark"
          mode="inline"
          defaultOpenKeys={["columns-management"]}
          selectedKeys={[selectedKey]}
          onClick={({ key }) => {
            if (key === "dashboard") navigate("/admin");
            else if (key === "columns-courseware") navigate("/admin/columns/courseware");
            else navigate(`/admin/${key}`);
          }}
          items={[
            { key: "dashboard", icon: <DashboardOutlined />, label: "仪表盘" },
            { key: "model-configs", icon: <ApiOutlined />, label: "模型配置" },
            { key: "materials", icon: <FolderOutlined />, label: "素材管理" },
            {
              key: "columns-management",
              icon: <BookOutlined />,
              label: "专栏管理",
              children: [
                { key: "columns-courseware", label: "上传课件" },
                { key: "columns", label: "专栏视频" },
              ],
            },
            { key: "users", icon: <TeamOutlined />, label: "用户管理" },
            { key: "llm-call-logs", icon: <FileSearchOutlined />, label: "AI 调用日志" },
          ]}
          style={{ flex: 1 }}
        />
        <div
          style={{
            padding: "14px 16px",
            borderTop: "1px solid rgba(255,255,255,0.12)",
          }}
        >
          <UserMenu dark />
        </div>
      </Sider>
      <Layout>
        <Content style={{ padding: 24, background: "var(--bg)" }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
