import { Avatar, Dropdown, Input, Space } from "antd";
import { DownOutlined, SearchOutlined, UserOutlined } from "@ant-design/icons";
import { Link, useNavigate } from "react-router-dom";
import { useAuthStore } from "../store/auth";
import ThemeSwitch from "./ThemeSwitch";

export default function TopNav() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();

  const menuItems = [
    ...(user?.role === "admin"
      ? [
          {
            key: "admin",
            label: "进入管理后台",
            onClick: () => navigate("/admin"),
          },
        ]
      : []),
    {
      key: "logout",
      label: "退出登录",
      onClick: () => {
        logout();
        navigate("/login");
      },
    },
  ];

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 24,
        padding: "8px 24px",
        background: "var(--bg-header)",
        borderBottom: "1px solid var(--border)",
      }}
    >
      <div style={{ fontWeight: 700, fontSize: 18 }}>AI 助学助手</div>
      <Space>
        <Link to="/">首页</Link>
        <Link to="/courses">课程列表</Link>
      </Space>
      <Input
        placeholder="搜索课程 / 主题"
        prefix={<SearchOutlined />}
        style={{ maxWidth: 260, marginLeft: "auto" }}
        onPressEnter={(e) => {
          const q = (e.target as HTMLInputElement).value;
          navigate(`/courses?q=${encodeURIComponent(q)}`);
        }}
      />
      <ThemeSwitch />
      <Dropdown menu={{ items: menuItems }}>
        <Space style={{ cursor: "pointer" }}>
          <Avatar size="small" icon={<UserOutlined />} />
          <span>{user?.username}</span>
          <DownOutlined />
        </Space>
      </Dropdown>
    </div>
  );
}
